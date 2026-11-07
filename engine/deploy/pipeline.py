"""Save = deploy.

validate -> write -> commit -> restart -> health check, and on any failure after the write,
roll back to the last good commit. This is the only module that knows about both the
workspace and the runtime; neither of them knows about the other.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from engine.config import Settings
from engine.deploy.checks import check_syntax
from engine.deploy.models import DeployResult
from engine.errors import GitError, InvalidStageCode, RuntimeStartError
from engine.events.models import DeployEvent
from engine.runtime.base import RunHandle, Runtime
from engine.workspace.manager import WorkspaceManager
from engine.workspace.models import Session

if TYPE_CHECKING:
    from engine.events.bus import EventBus
    from engine.events.stats import StatsCollector

_Failure = tuple[str, str, list[str]]  # (step, message, log tail)


class Deployer:
    def __init__(
        self,
        workspace: WorkspaceManager,
        runtime: Runtime,
        settings: Settings,
        bus: EventBus | None = None,
        stats: StatsCollector | None = None,
    ):
        self._ws = workspace
        self._runtime = runtime
        self._settings = settings
        self._bus = bus
        self._stats = stats
        self._handles: dict[str, RunHandle] = {}
        self._latest: dict[str, DeployResult] = {}

    # ------------------------------------------------------------ lifecycle

    def launch(self, session: Session) -> RunHandle:
        with self._ws.lock(session.id):
            failure = self._boot_and_wait(session)
            if failure:
                _, message, tail = failure
                self.shutdown(session.id)
                raise RuntimeStartError(f"company failed to start: {message}", tail)
            return self._handles[session.id]

    def shutdown(self, session_id: str) -> None:
        handle = self._handles.pop(session_id, None)
        if handle is not None:
            self._runtime.stop(handle)
        self._latest.pop(session_id, None)

    def shutdown_all(self) -> None:
        for session_id in list(self._handles):
            self.shutdown(session_id)

    def handle(self, session_id: str) -> RunHandle | None:
        return self._handles.get(session_id)

    def latest(self, session_id: str) -> DeployResult | None:
        return self._latest.get(session_id)

    def logs(self, session_id: str, tail: int) -> list[str]:
        handle = self._handles.get(session_id)
        return self._runtime.logs(handle, tail) if handle else []

    # --------------------------------------------------------------- deploy

    def deploy_stage(
        self, session: Session, endpoint: str, name: str, new_code: str
    ) -> DeployResult:
        started = time.monotonic()
        if self._bus:
            self._bus.publish(
                session.id,
                DeployEvent(
                    session_id=session.id,
                    ts=time.time(),
                    phase="started",
                    endpoint=endpoint,
                    stage=name,
                    step="validate",
                    status="running",
                    message=f"deploying {name}",
                ),
            )
        if self._stats:
            self._stats.mark_active(session.id)

        with self._ws.lock(session.id):
            self._ws.get_stage(session, endpoint, name)
            previous = self._ws.head(session)

            def emit_step(step_name: str, status_name: str, msg: str):
                if self._bus:
                    self._bus.publish(
                        session.id,
                        DeployEvent(
                            session_id=session.id,
                            ts=time.time(),
                            phase="step",
                            endpoint=endpoint,
                            stage=name,
                            step=step_name,
                            status=status_name,
                            message=msg,
                        ),
                    )

            def finish(status, step, message, tail=None, commit=None) -> DeployResult:
                handle = self._handles.get(session.id)
                result = DeployResult(
                    status=status,
                    step=step,
                    message=message,
                    endpoint=endpoint,
                    stage=name,
                    commit=commit or previous,
                    previous_commit=previous,
                    url=handle.base_url if handle else None,
                    duration_s=round(time.monotonic() - started, 3),
                    log_tail=tail or [],
                )
                self._latest[session.id] = result
                if self._bus:
                    self._bus.publish(
                        session.id,
                        DeployEvent(
                            session_id=session.id,
                            ts=time.time(),
                            phase="finished",
                            endpoint=endpoint,
                            stage=name,
                            step=step or "done",
                            status=status,
                            message=message,
                        ),
                    )
                if self._stats:
                    self._stats.mark_active(session.id)
                return result

            error = check_syntax(session.manifest.language, new_code)
            if error:
                return finish("rejected", "validate", error)
            try:
                emit_step("write", "running", "writing stage file")
                self._ws.write_stage(session, endpoint, name, new_code)
            except InvalidStageCode as exc:
                return finish("rejected", "write", str(exc))  # nothing was written

            try:
                emit_step("commit", "running", "committing workspace")
                commit = self._ws.commit(session, f"deploy: {name} ({endpoint})")
            except GitError as exc:
                return self._roll_back(
                    session, previous, ("commit", str(exc), []), finish
                )
            emit_step("restart", "running", "restarting runtime")
            failure = self._boot_and_wait(session)

            if failure:
                return self._roll_back(session, previous, failure, finish)
            return finish("deployed", None, "deployed", commit=commit)

    def reset(self, session: Session) -> DeployResult:
        """Throw away all edits: back to the original template, restarted."""
        started = time.monotonic()
        with self._ws.lock(session.id):
            self._ws.reset_to_original(session)
            failure = self._boot_and_wait(session)
            handle = self._handles.get(session.id)
            if failure:
                step, message, tail = failure
                status = "broken"
            else:
                step, message, tail, status = None, "reset to original", [], "deployed"
            result = DeployResult(
                status=status,
                step=step,
                message=message,
                commit=self._ws.head(session),
                url=handle.base_url if handle else None,
                duration_s=round(time.monotonic() - started, 3),
                log_tail=tail or [],
            )
            self._latest[session.id] = result
            return result

    # ------------------------------------------------------------- internals

    def _roll_back(
        self, session: Session, previous: str, failure: _Failure, finish
    ) -> DeployResult:
        step, message, tail = failure
        try:
            self._ws.reset_to(session, previous)
        except GitError as exc:
            return finish(
                "broken", "rollback", f"{message}; rollback failed: {exc}", tail
            )
        second = self._boot_and_wait(session)
        if second:
            _, why, _ = second
            return finish(
                "broken",
                "rollback",
                f"{message}; previous version also failed: {why}",
                tail,
            )
        return finish("rolled_back", step, message, tail)

    def reset_company_state(self, session: Session) -> bool:
        """Call the company's direct reset endpoint to wipe & reseed its database."""
        if not session.manifest.reset_path:
            return True
        handle = self._handles.get(session.id)
        if not handle:
            return False
        import httpx

        url = f"{handle.base_url}{session.manifest.reset_path}"
        try:
            resp = httpx.post(
                url,
                headers={"X-Game-Secret": session.secret},
                timeout=5.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _boot_and_wait(self, session: Session) -> _Failure | None:
        try:
            old = self._handles.get(session.id)
            env = {"GAME_RESET_SECRET": session.secret} if session.secret else {}
            if old is not None:
                handle = self._runtime.restart(old, env=env)
            else:
                handle = self._runtime.start(
                    session.company_dir, session.manifest, session.log_path, env=env
                )
        except RuntimeStartError as exc:
            return ("restart", str(exc), exc.log_tail)
        self._handles[session.id] = handle
        ok, reason = self._await_healthy(session, handle)
        if not ok:
            return (
                "health",
                reason,
                self._runtime.logs(handle, self._settings.log_tail_lines),
            )
        return None

    def _await_healthy(self, session: Session, handle: RunHandle) -> tuple[bool, str]:
        timeout = min(
            session.manifest.health_timeout_s, self._settings.max_health_timeout_s
        )
        deadline = time.monotonic() + timeout
        while True:
            if not self._runtime.is_running(handle):
                return False, "the process exited during startup"
            if self._runtime.healthy(handle):
                return True, ""
            if time.monotonic() >= deadline:
                return False, f"health check did not pass within {timeout:g}s"
            time.sleep(self._settings.health_poll_interval_s)
