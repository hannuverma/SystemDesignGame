from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from engine.bots.models import BotResult, BotSpec
from engine.bots.runner import run_bot
from engine.errors import AttackConflict, AttackNotFound, FlawNotFound
from engine.events.bus import EventBus
from engine.flaws.loader import load_flaw
from engine.flaws.models import BudgetConfig
from engine.manifest import CompanyManifest
from engine.verify.contract import ContractResult, run_contract
from engine.verify.verdict import Verdict, compute_verdict

logger = logging.getLogger(__name__)

AttackStatus = Literal["running", "completed", "timeout", "crashed", "error"]


class AttackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    flaw_id: str
    seed: int
    budget: BudgetConfig
    status: AttackStatus = "running"
    bot_result: BotResult | None = None
    contract_result: ContractResult | None = None
    verdict: Verdict | None = None
    created_at: float
    completed_at: float | None = None


class AttackManager:
    """Manages attack runs, concurrency, replays, and verdicts across sessions."""

    def __init__(self) -> None:
        self._attacks: dict[str, AttackRecord] = {}
        self._session_attacks: dict[str, list[str]] = {}
        self._active_attacks: dict[str, str] = {}
        self._lock = threading.Lock()

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._active_attacks

    def get_attack(self, attack_id: str) -> AttackRecord:
        with self._lock:
            record = self._attacks.get(attack_id)
            if not record:
                raise AttackNotFound(f"attack not found: {attack_id}")
            return record.model_copy()

    def list_attacks(self, session_id: str) -> list[AttackRecord]:
        with self._lock:
            attack_ids = self._session_attacks.get(session_id, [])
            return [
                self._attacks[aid].model_copy()
                for aid in attack_ids
                if aid in self._attacks
            ]

    def start_attack(
        self,
        session_id: str,
        flaw_id: str,
        target_url: str,
        probe_token: str,
        manifest: CompanyManifest,
        flaws_dir: Path,
        company_dir: Path,
        seed: int = 42,
        budget: BudgetConfig | None = None,
        bus: EventBus | None = None,
        reset_fn: Callable[[], None] | None = None,
        wait: bool = False,
    ) -> AttackRecord:
        """Start a new attack against a session.

        Raises AttackConflict if another attack is currently running.
        """
        flaw_dir = flaws_dir / flaw_id
        if not flaw_dir.is_dir():
            raise FlawNotFound(f"flaw directory not found: {flaw_dir}")

        flaw = load_flaw(flaw_dir, manifest, company_dir)
        effective_budget = budget or flaw.attack.budget
        attack_id = f"atk_{uuid4().hex[:12]}"
        now = time.time()

        with self._lock:
            if session_id in self._active_attacks:
                raise AttackConflict(
                    f"An attack is already running for session {session_id}"
                )
            record = AttackRecord(
                id=attack_id,
                session_id=session_id,
                flaw_id=flaw_id,
                seed=seed,
                budget=effective_budget,
                status="running",
                created_at=now,
            )
            self._attacks[attack_id] = record
            self._session_attacks.setdefault(session_id, []).append(attack_id)
            self._active_attacks[session_id] = attack_id

        def _execute() -> None:
            try:
                # 1. Reset company state if reset callback provided
                if reset_fn:
                    try:
                        reset_fn()
                    except Exception as exc:
                        logger.warning(
                            "Failed to reset state for session %s: %s", session_id, exc
                        )

                # 2. Run bot attack
                spec = BotSpec(
                    id=flaw.id,
                    command=flaw.attack.command,
                    target_url=target_url,
                    budget=effective_budget,
                    seed=seed,
                    cwd=flaw_dir,
                )
                bot_res = run_bot(spec, bus=bus, session_id=session_id)

                # 3. Run contract probe checks
                contract_res = run_contract(
                    steps=flaw.contract,
                    target_url=target_url,
                    probe_token=probe_token,
                    fixtures=manifest.fixtures,
                )

                # 4. Compute verdict
                verdict = compute_verdict(bot_res, contract_res)

                # 5. Update record
                with self._lock:
                    rec = self._attacks[attack_id]
                    rec.status = bot_res.status
                    rec.bot_result = bot_res
                    rec.contract_result = contract_res
                    rec.verdict = verdict
                    rec.completed_at = time.time()
            except Exception as exc:
                logger.exception(
                    "Attack %s encountered unhandled error: %s", attack_id, exc
                )
                with self._lock:
                    rec = self._attacks[attack_id]
                    rec.status = "error"
                    rec.completed_at = time.time()
            finally:
                with self._lock:
                    self._active_attacks.pop(session_id, None)

        if wait:
            _execute()
            return self.get_attack(attack_id)

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()
        return record.model_copy()

    def replay_attack(
        self,
        session_id: str,
        target_url: str,
        probe_token: str,
        manifest: CompanyManifest,
        flaws_dir: Path,
        company_dir: Path,
        bus: EventBus | None = None,
        reset_fn: Callable[[], None] | None = None,
        wait: bool = False,
    ) -> AttackRecord:
        """Replay the most recent attack run for a session with identical seed and budget."""
        with self._lock:
            attack_ids = self._session_attacks.get(session_id, [])
            if not attack_ids:
                raise AttackNotFound(
                    f"No previous attack recorded to replay for session {session_id}"
                )
            last_id = attack_ids[-1]
            last_record = self._attacks[last_id]
            flaw_id = last_record.flaw_id
            seed = last_record.seed
            budget = last_record.budget

        return self.start_attack(
            session_id=session_id,
            flaw_id=flaw_id,
            target_url=target_url,
            probe_token=probe_token,
            manifest=manifest,
            flaws_dir=flaws_dir,
            company_dir=company_dir,
            seed=seed,
            budget=budget,
            bus=bus,
            reset_fn=reset_fn,
            wait=wait,
        )
