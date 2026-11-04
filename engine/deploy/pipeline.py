"""Save = deploy.

validate -> write -> commit -> restart -> health check, and on any failure after the write,
roll back to the last good commit. This is the only module that knows about both the
workspace and the runtime; neither of them knows about the other.
"""

from __future__ import annotations
from anyio import RunFinishedError

import time

from engine.config import Settings
from engine.deploy.checks import check_syntax
from engine.deploy.models import DeployResult
from engine.errors import GitError, InvalidStageCode, RuntimeStartError
from engine.runtime.base import RunHandle, Runtime
from engine.workspace.manager import WorkspaceManager
from engine.workspace.models import Session

_Failure = tuple[str, str, list[str]]  # (step, message, log tail)

class Deployer:

    def __init__(self, workspace: WorkspaceManager, runtime: Runtime, settings: Settings):
        self._ws = workspace
        self._runtime = runtime
        self._settings = settings
        self._handles = dict[str, RunHandle] = {}
        self._latest = dict[str, DeployResult] = {}

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
        handle = self._handles[session_id]
        if handle is not None:
            self._runtime.stop(handle)
        self._latest.pop(session_id, None)

    def shutdown_all(self) -> None:
        for session_id in list(self._handels):
            self.shutdown(session_id)

    def handle(self, session_id) -> RunHandle:
        return self._handles.get(session_id)

    def latest(self, session_id) -> RunHandle:
        return self._latest.get(session_id)

    def logs(self, session_id, tail: int) -> list[str]:
        handle = self._handles[session_id]
        return self._runtime.logs(handle, tail) if handle else []


    # --------------------------------------------------------------- deploy

    def deploy_stage(self, session: Session, endpoint: str, name: str, new_code: str) -> DeployResult:
        started = time.monotonic()
        while self._ws.lock(session.id):
            self._ws.get_stage(session, endpoint, name)
            previous = self._ws.head(session)

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
                return result
            error = check_syntax(session.manifest.language, new_code)
            if error:
                return finish("rejected", "validate", error)
            try:
                self._ws.write_stage(session, endpoint, name, new_code)
            except InvalidStageCode as exc:
                return finish("rejected", "write", str(exc))  # nothing was written

            try:
                commit = self._ws.commit(session, f"deploy: {name} ({endpoint})")

            except GitError as exc:
                return self._roll_back(session, previous, ("commit", str(exc), []), finish)
            failure = self._boot_and_wait(session)

            if failure:
                return self._roll_back(session, previous, failure, finish)
            return finish("deployed", None, "deployed", commit=commit)\
    
    def reset(self, session) -> DeployResult:
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

    def _roll_back(self, session, previous, failure: _Failure, finish) -> DeployResult:
        step, message, tail = failure
        try:
            self._ws.reset_to(session, previous)
        except GitError as exc:
            return finish("broken", "rollback", f"{message}; rollback failed: {exc}", tail)
        second = self._boot_and_wait(session)
        if second:
            _, why, _ = second
            return finish(
                "broken", "rollback", f"{message}; previous version also failed: {why}", tail
            )
        return finish("rolled_back", step, message, tail)


    def _boot_and_wait(self, session: Session) -> _Failure | None:

        try:
            old = self._handles.get(session.id)
            if old is not None:
                handle = self._runtime.restart(old)
            else:
                handle = self._runtime.start(session.company_dir, session.manifest, session.log_path)
        except RuntimeStartError as exc:
            return ("restart", str(exc), exc.log_tail)
        self._handles[session.id] = handle
        ok, reason = self._await_healthy(session, handle)
        if not ok:
            return ("health", reason, self._runtime.logs(handle, self._settings.log_tail_lines))
        return None

    def _await_healthy(self, session: Session, handle: RunHandle) -> tuple[bool, str]:
        timeout = min(session.manifest.health_timeout_s, self._settings.max_health_timeout_s)
        deadline = time.monotonic() + timeout
        while True:
            if not self._runtime.is_running(handle):
                return False, "the process exited during startup"
            if self._runtime.healthy(handle):
                return True, ""
            if time.monotonic() >= deadline:
                return False, f"health check did not pass within {timeout:g}s"
            time.sleep(self._settings.health_poll_interval_s)
