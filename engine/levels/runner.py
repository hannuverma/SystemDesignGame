from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from engine.bots.models import BotSpec
from engine.bots.runner import run_bot
from engine.deploy.models import DeployResult
from engine.events.bus import EventBus
from engine.events.models import DeployEvent
from engine.flaws.models import FlawSpec
from engine.levels.models import DifficultyTier, LevelSpec
from engine.levels.progress import (
    LevelProgress,
    init_level_progress,
    load_progress,
    save_progress,
    update_objective_verdict,
)
from engine.verify.contract import run_contract
from engine.verify.verdict import Verdict, compute_verdict
from engine.workspace.models import Session

logger = logging.getLogger(__name__)


class SessionLevelState:
    def __init__(
        self,
        session: Session,
        level: LevelSpec,
        progress: LevelProgress,
        flaws: dict[str, FlawSpec],
        difficulty: DifficultyTier,
        progress_path: Path,
    ) -> None:
        self.session = session
        self.level = level
        self.progress = progress
        self.flaws = flaws
        self.difficulty = difficulty
        self.progress_path = progress_path
        self.breached_endpoints: set[str] = set()
        self.wave_timer: threading.Timer | None = None
        self.cancelled = False
        self.eval_generation = 0
        self.lock = threading.RLock()


class LevelRunner:
    """Coordinates levels, objective progression, wave attacks, regressions, and hints."""

    def __init__(
        self,
        flaws: dict[str, FlawSpec],
        levels: dict[str, LevelSpec],
        gateway_url_fn: Callable[[str], str],
        probe_token: str,
        bus: EventBus | None = None,
        reset_fn: Callable[[Session], None] | None = None,
    ) -> None:
        self.flaws = flaws
        self.levels = levels
        self.gateway_url_fn = gateway_url_fn
        self.probe_token = probe_token
        self.bus = bus
        self.reset_fn = reset_fn
        self._states: dict[str, SessionLevelState] = {}
        self._lock = threading.RLock()

    def get_state(self, session_id: str) -> SessionLevelState | None:
        with self._lock:
            return self._states.get(session_id)

    def attach_level(
        self,
        session: Session,
        level: LevelSpec,
        difficulty: DifficultyTier | None = None,
    ) -> LevelProgress:
        """Attach a level to a session and start the first objective's attack wave."""
        diff = difficulty or level.difficulty
        progress_path = session.root / "progress.json"

        if progress_path.is_file():
            try:
                progress = load_progress(progress_path)
            except Exception:
                progress = init_level_progress(level)
        else:
            progress = init_level_progress(level)

        save_progress(progress, progress_path)
        state = SessionLevelState(
            session=session,
            level=level,
            progress=progress,
            flaws=self.flaws,
            difficulty=diff,
            progress_path=progress_path,
        )

        with self._lock:
            self._states[session.id] = state

        # Check for pre-emptive fix
        active_id = progress.active_objective_id
        if active_id:
            verdict = self._evaluate_objective(session, active_id)
            if verdict.status == "fixed":
                transitions = update_objective_verdict(
                    progress, level, active_id, verdict
                )
                save_progress(progress, progress_path)
                self._emit_transitions(session.id, transitions)

        # Schedule initial wave if not completed
        if not progress.is_completed and progress.active_objective_id:
            self._schedule_next_wave(state, delay_s=level.attack_delay_s)

        return progress

    def stop_session(self, session_id: str) -> None:
        with self._lock:
            state = self._states.pop(session_id, None)
        if state:
            with state.lock:
                state.cancelled = True
                if state.wave_timer:
                    state.wave_timer.cancel()

    def _schedule_next_wave(self, state: SessionLevelState, delay_s: float) -> None:
        with state.lock:
            if (
                state.cancelled
                or state.progress.is_completed
                or not state.progress.active_objective_id
            ):
                return

            def _wave() -> None:
                if state.cancelled:
                    return
                self._run_active_wave(state)
                # Reschedule next wave
                self._schedule_next_wave(state, delay_s=state.level.wave_interval_s)

            timer = threading.Timer(delay_s, _wave)
            timer.daemon = True
            state.wave_timer = timer
            timer.start()

    def _run_active_wave(self, state: SessionLevelState) -> None:
        """Run a single attack wave for the currently active objective."""
        active_id = state.progress.active_objective_id
        if not active_id:
            return

        obj = next((o for o in state.level.objectives if o.id == active_id), None)
        if not obj:
            return
        flaw = state.flaws.get(obj.flaw)
        if not flaw:
            return

        target_url = self.gateway_url_fn(state.session.id)
        if self.reset_fn:
            with contextlib.suppress(Exception):
                self.reset_fn(state.session)

        spec = BotSpec(
            id=flaw.id,
            command=flaw.attack.command,
            target_url=target_url,
            budget=flaw.attack.budget,
            cwd=flaw.dir,
        )
        bot_res = run_bot(spec, bus=self.bus, session_id=state.session.id)

        # Record breach symptom if attack succeeded with impact
        if bot_res.report and bot_res.report.succeeded:
            impact = bot_res.report.impact
            if (
                impact.accounts_compromised > 0
                or impact.records_leaked > 0
                or impact.records_destroyed > 0
            ):
                state.breached_endpoints.add(flaw.endpoint)

    def _evaluate_objective(self, session: Session, objective_id: str) -> Verdict:
        """Evaluate an objective by running its attack and contract."""
        state = self.get_state(session.id)
        if not state:
            return Verdict(status="inconclusive", reasons=["Level state not found"])

        obj = next((o for o in state.level.objectives if o.id == objective_id), None)
        if not obj:
            return Verdict(status="inconclusive", reasons=["Objective not found"])

        flaw = state.flaws.get(obj.flaw)
        if not flaw:
            return Verdict(status="inconclusive", reasons=["Flaw not found"])

        target_url = self.gateway_url_fn(session.id)
        if self.reset_fn:
            with contextlib.suppress(Exception):
                self.reset_fn(session)

        spec = BotSpec(
            id=flaw.id,
            command=flaw.attack.command,
            target_url=target_url,
            budget=flaw.attack.budget,
            cwd=flaw.dir,
        )
        bot_res = run_bot(spec, bus=self.bus, session_id=session.id)

        contract_res = run_contract(
            steps=flaw.contract,
            target_url=target_url,
            probe_token=self.probe_token,
            fixtures=session.manifest.fixtures,
        )
        verdict = compute_verdict(bot_res, contract_res)

        if verdict.status == "fixed":
            # Clear breach status on successful fix
            state.breached_endpoints.discard(flaw.endpoint)

        return verdict

    def on_post_deploy_hook(
        self, session: Session, deploy_result: DeployResult
    ) -> list[str]:
        """Post-deploy verification: run contract checks and trigger background objective verification."""
        state = self.get_state(session.id)
        if not state:
            return []

        warnings: list[str] = []
        target_url = self.gateway_url_fn(session.id)

        # 1. Run contracts of active and fixed objectives
        active_and_fixed = [
            obj
            for obj in state.level.objectives
            if state.progress.objectives[obj.id].status in ("active", "fixed")
        ]
        for obj in active_and_fixed:
            flaw = state.flaws.get(obj.flaw)
            if not flaw:
                continue
            contract_res = run_contract(
                steps=flaw.contract,
                target_url=target_url,
                probe_token=self.probe_token,
                fixtures=session.manifest.fixtures,
            )
            if not contract_res.passed:
                msg = f"Contract failed for {obj.title}: {contract_res.failure_message}"
                if state.level.fail_mode == "warn":
                    warnings.append(msg)
                if self.bus:
                    self.bus.publish(
                        session.id,
                        DeployEvent(
                            session_id=session.id,
                            ts=time.time(),
                            phase="finished",
                            endpoint=flaw.endpoint,
                            stage=flaw.stage,
                            step="contract",
                            status="warning",
                            message=msg,
                        ),
                    )

        # 2. Asynchronously evaluate active objective and check for regressions
        with state.lock:
            state.eval_generation += 1
            gen = state.eval_generation

        def _bg_eval() -> None:
            self._evaluate_and_progress(state, gen)

        threading.Thread(target=_bg_eval, daemon=True).start()
        return warnings

    def on_undo(self, session: Session) -> None:
        """Cancel pending evaluation of previous deploy on undo."""
        state = self.get_state(session.id)
        if state:
            with state.lock:
                state.eval_generation += 1

    def _evaluate_and_progress(self, state: SessionLevelState, gen: int) -> None:
        """Background progression check after deploy."""
        with state.lock:
            if state.cancelled or gen != state.eval_generation:
                return
            active_id = state.progress.active_objective_id

        # A. Evaluate active objective (outside lock so deploys/undos are not blocked)
        if active_id:
            verdict = self._evaluate_objective(state.session, active_id)
            with state.lock:
                if state.cancelled or gen != state.eval_generation:
                    return
                logger.info(
                    "Evaluated active objective %s: %s (reasons: %s)",
                    active_id,
                    verdict.status,
                    verdict.reasons,
                )
                if verdict.status == "fixed":
                    transitions = update_objective_verdict(
                        state.progress, state.level, active_id, verdict
                    )
                    logger.info("State transitions for %s: %s", active_id, transitions)
                    save_progress(state.progress, state.progress_path)
                    self._emit_transitions(state.session.id, transitions)

                    # Reschedule wave for next objective
                    if not state.progress.is_completed:
                        self._schedule_next_wave(
                            state, delay_s=state.level.attack_delay_s
                        )
                    return

        # B. Regression guard: check already fixed objectives (outside lock)
        with state.lock:
            if state.cancelled or gen != state.eval_generation:
                return
            fixed_objs = [
                obj.id
                for obj in state.level.objectives
                if state.progress.objectives[obj.id].status == "fixed"
            ]

        for obj_id in fixed_objs:
            with state.lock:
                if state.cancelled or gen != state.eval_generation:
                    return
            verdict = self._evaluate_objective(state.session, obj_id)
            with state.lock:
                if state.cancelled or gen != state.eval_generation:
                    return
                if verdict.status == "vulnerable":
                    transitions = update_objective_verdict(
                        state.progress, state.level, obj_id, verdict
                    )
                    save_progress(state.progress, state.progress_path)
                    self._emit_transitions(state.session.id, transitions)
                    self._schedule_next_wave(state, delay_s=state.level.attack_delay_s)
                    break

    def _emit_transitions(self, session_id: str, transitions: list) -> None:
        if not self.bus:
            return
        for t in transitions:
            self.bus.publish(
                session_id,
                DeployEvent(
                    session_id=session_id,
                    ts=time.time(),
                    phase="finished",
                    endpoint="level",
                    stage="progress",
                    step=t.event,
                    status="running" if t.event != "level_completed" else "ok",
                    message=f"Objective {t.objective_id} {t.event} ({t.previous_status} -> {t.new_status})",
                ),
            )

    def next_hint(self, session: Session) -> dict:
        """Return the next progressive hint for the active objective."""
        state = self.get_state(session.id)
        if not state:
            return {
                "hint": "No active level for this session.",
                "hint_number": 0,
                "total_hints": 0,
            }

        active_id = state.progress.active_objective_id
        if not active_id:
            return {"hint": "No active objective.", "hint_number": 0, "total_hints": 0}

        obj = next((o for o in state.level.objectives if o.id == active_id), None)
        flaw = state.flaws.get(obj.flaw) if obj else None
        if not flaw or not flaw.hints:
            return {
                "hint": "No hints available for this objective.",
                "hint_number": 0,
                "total_hints": 0,
            }

        obj_state = state.progress.objectives[active_id]
        if obj_state.hints_used < len(flaw.hints):
            hint_idx = obj_state.hints_used
            obj_state.hints_used += 1
            save_progress(state.progress, state.progress_path)
            return {
                "hint": flaw.hints[hint_idx],
                "hint_number": obj_state.hints_used,
                "total_hints": len(flaw.hints),
            }

        return {
            "hint": "All hints have already been revealed.",
            "hint_number": obj_state.hints_used,
            "total_hints": len(flaw.hints),
        }
