from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from engine.events.anomaly import EndpointHealthStatus, EndpointStatus
from engine.flaws.models import FlawSpec
from engine.levels.models import DifficultyTier, LevelSpec
from engine.levels.progress import LevelProgress, ObjectiveState


class ObjectiveView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str | None = None
    status: str
    text: str | None = None
    flaw_category: str | None = None
    endpoint_highlight: str | None = None
    stage_highlight: str | None = None
    lesson: str | None = None
    hints_used: int = 0
    reward: int = 0


class LevelView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    difficulty: DifficultyTier
    fail_mode: str
    active_objective_id: str | None = None
    is_completed: bool = False
    objectives: list[ObjectiveView] = Field(default_factory=list)


def filter_level_view(
    level: LevelSpec,
    progress: LevelProgress,
    flaws: dict[str, FlawSpec],
    difficulty: DifficultyTier,
) -> LevelView:
    """Filter level objectives and guidance strictly according to difficulty tier."""
    obj_views: list[ObjectiveView] = []

    for obj in level.objectives:
        state: ObjectiveState = progress.objectives[obj.id]
        flaw = flaws.get(obj.flaw)

        text_val: str | None = None
        flaw_cat: str | None = None
        ep_highlight: str | None = None
        st_highlight: str | None = None
        lesson_val: str | None = None
        title_val: str | None = obj.title

        if difficulty == "guided":
            text_val = (
                obj.text.get("guided")
                or obj.text.get("easy")
                or (flaw.symptoms if flaw else None)
            )
            flaw_cat = flaw.category if flaw else None
            ep_highlight = flaw.endpoint if flaw else None
            st_highlight = flaw.stage if flaw else None
            lesson_val = flaw.lesson if (flaw and state.status == "fixed") else None

        elif difficulty == "easy":
            text_val = obj.text.get("easy") or (flaw.symptoms if flaw else None)
            ep_highlight = flaw.endpoint if flaw else None
            # Stage highlight is NOT visible on easy
            st_highlight = None
            lesson_val = None

        elif difficulty == "normal":
            # No detailed task text; generic stress text only if available
            text_val = obj.text.get("normal")
            ep_highlight = None
            st_highlight = None
            lesson_val = None

        elif difficulty == "hard":
            # Raw mode: no task text or titles
            text_val = None
            title_val = None
            ep_highlight = None
            st_highlight = None
            lesson_val = None

        obj_views.append(
            ObjectiveView(
                id=obj.id,
                title=title_val,
                status=state.status,
                text=text_val,
                flaw_category=flaw_cat,
                endpoint_highlight=ep_highlight,
                stage_highlight=st_highlight,
                lesson=lesson_val,
                hints_used=state.hints_used,
                reward=obj.reward,
            )
        )

    return LevelView(
        id=level.id,
        difficulty=difficulty,
        fail_mode=level.fail_mode,
        active_objective_id=progress.active_objective_id,
        is_completed=progress.is_completed,
        objectives=obj_views,
    )


def filter_endpoints_view(
    endpoint_statuses: dict[str, tuple[EndpointHealthStatus, list[str]]],
    active_flaw: FlawSpec | None,
    difficulty: DifficultyTier,
) -> list[EndpointStatus]:
    """Filter endpoint observable marks according to difficulty tier."""
    results: list[EndpointStatus] = []

    if difficulty == "hard":
        # Hard tier: no marks whatsoever
        return []

    for ep_name, (status, reasons) in endpoint_statuses.items():
        if difficulty == "normal":
            # Normal: show red marks only when observable symptoms exist (warning, critical, breached)
            if status != "ok":
                results.append(
                    EndpointStatus(
                        endpoint=ep_name,
                        status=status,
                        reasons=reasons,
                        highlighted=False,
                    )
                )
        elif difficulty in ("guided", "easy"):
            # Guided and Easy: show all marks, and highlight active endpoint/stage
            is_active_ep = bool(active_flaw and active_flaw.endpoint == ep_name)
            stage_hl = (
                active_flaw.stage
                if (difficulty == "guided" and is_active_ep and active_flaw)
                else None
            )
            results.append(
                EndpointStatus(
                    endpoint=ep_name,
                    status=status,
                    reasons=reasons,
                    highlighted=is_active_ep,
                    stage_highlight=stage_hl,
                )
            )

    return results
