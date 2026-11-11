from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.levels.models import LevelSpec
from engine.verify.verdict import Verdict

logger = logging.getLogger(__name__)

ObjectiveStatus = Literal["locked", "active", "fixed", "regressed"]


class ObjectiveState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    flaw: str
    status: ObjectiveStatus
    hints_used: int = 0
    completed_at: float | None = None


class LevelProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level_id: str
    objectives: dict[str, ObjectiveState]
    active_objective_id: str | None = None
    is_completed: bool = False


class ProgressTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str
    previous_status: ObjectiveStatus
    new_status: ObjectiveStatus
    event: Literal["activated", "completed", "regressed", "level_completed"]


def init_level_progress(level: LevelSpec) -> LevelProgress:
    """Initialize level progress with the first unlocked objective set to active."""
    objs: dict[str, ObjectiveState] = {}
    active_id: str | None = None

    for obj in level.objectives:
        if not obj.requires and active_id is None:
            status: ObjectiveStatus = "active"
            active_id = obj.id
        else:
            status = "locked"
        objs[obj.id] = ObjectiveState(
            id=obj.id,
            flaw=obj.flaw,
            status=status,
            hints_used=0,
        )

    return LevelProgress(
        level_id=level.id,
        objectives=objs,
        active_objective_id=active_id,
        is_completed=False,
    )


def save_progress(progress: LevelProgress, path: Path) -> None:
    """Atomically save progress to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = path.parent
    data = progress.model_dump_json(indent=2)

    with tempfile.NamedTemporaryFile(
        "w", dir=temp_dir, delete=False, encoding="utf-8"
    ) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
        temp_name = f.name

    os.replace(temp_name, path)


def load_progress(path: Path) -> LevelProgress:
    """Load progress from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return LevelProgress.model_validate(data)


def update_objective_verdict(
    progress: LevelProgress,
    level: LevelSpec,
    objective_id: str,
    verdict: Verdict,
) -> list[ProgressTransition]:
    """Process a verdict for an objective and return any state transitions.

    Rules:
    - If fixed:
      - 'active' -> 'fixed'
      - 'regressed' -> 'fixed'
      - Check if subsequent locked objectives can now be unlocked.
    - If vulnerable:
      - 'fixed' -> 'regressed' (and re-activates it as active objective)
    """
    transitions: list[ProgressTransition] = []
    obj_state = progress.objectives.get(objective_id)
    if not obj_state:
        return transitions

    prev_status = obj_state.status

    if verdict.status == "fixed":
        if prev_status in ("active", "regressed"):
            obj_state.status = "fixed"
            obj_state.completed_at = time.time()
            transitions.append(
                ProgressTransition(
                    objective_id=objective_id,
                    previous_status=prev_status,
                    new_status="fixed",
                    event="completed",
                )
            )

            # Check if all objectives are now fixed
            if all(o.status == "fixed" for o in progress.objectives.values()):
                progress.is_completed = True
                progress.active_objective_id = None
                transitions.append(
                    ProgressTransition(
                        objective_id=objective_id,
                        previous_status="fixed",
                        new_status="fixed",
                        event="level_completed",
                    )
                )
                return transitions

            # Find next objective to unlock
            next_active: str | None = None
            for cand in level.objectives:
                cand_state = progress.objectives[cand.id]
                if cand_state.status == "locked":
                    # Check if all requirements are fixed
                    reqs_met = all(
                        progress.objectives[r].status == "fixed" for r in cand.requires
                    )
                    if reqs_met:
                        cand_state.status = "active"
                        next_active = cand.id
                        transitions.append(
                            ProgressTransition(
                                objective_id=cand.id,
                                previous_status="locked",
                                new_status="active",
                                event="activated",
                            )
                        )
                        break

            progress.active_objective_id = next_active

    elif verdict.status == "vulnerable" and prev_status == "fixed":
        obj_state.status = "regressed"
        progress.is_completed = False
        progress.active_objective_id = objective_id
        transitions.append(
            ProgressTransition(
                objective_id=objective_id,
                previous_status="fixed",
                new_status="regressed",
                event="regressed",
            )
        )

    return transitions
