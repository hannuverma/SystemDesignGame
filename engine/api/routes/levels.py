from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from engine.api.deps import ServicesDep, SessionDep
from engine.deploy import HTTP_STATUS
from engine.errors import LevelError
from engine.events.anomaly import detect_endpoint_status
from engine.levels.visibility import (
    EndpointStatus,
    LevelView,
    filter_endpoints_view,
    filter_level_view,
)

router = APIRouter(prefix="/sessions/{session_id}", tags=["levels"])


@router.get("/level", response_model=LevelView)
def get_session_level(
    session: SessionDep,
    services: ServicesDep,
    difficulty: DifficultyTier | None = None,
) -> LevelView:
    state = services.levels_runner.get_state(session.id)
    if not state:
        raise LevelError(f"No level attached to session {session.id}")

    diff = difficulty or state.difficulty
    return filter_level_view(
        level=state.level,
        progress=state.progress,
        flaws=services.levels_runner.flaws,
        difficulty=diff,
    )


@router.get("/endpoints", response_model=list[EndpointStatus])
def get_session_endpoints(
    session: SessionDep,
    services: ServicesDep,
    difficulty: DifficultyTier | None = None,
) -> list[EndpointStatus]:
    state = services.levels_runner.get_state(session.id)
    diff = difficulty or (state.difficulty if state else "normal")
    breached = state.breached_endpoints if state else set()

    active_flaw = None
    if state and state.progress.active_objective_id:
        active_obj = next(
            (
                o
                for o in state.level.objectives
                if o.id == state.progress.active_objective_id
            ),
            None,
        )
        if active_obj:
            active_flaw = services.levels_runner.flaws.get(active_obj.flaw)

    snapshot = services.stats.snapshot(session.id, window_s=30)
    routes = {r.name for r in session.manifest.routes}

    statuses: dict = {}
    for ep_name in sorted(routes):
        is_breached = ep_name in breached
        status, reasons = detect_endpoint_status(
            endpoint_name=ep_name,
            snapshot=snapshot,
            is_breached=is_breached,
        )
        statuses[ep_name] = (status, reasons)

    return filter_endpoints_view(
        endpoint_statuses=statuses,
        active_flaw=active_flaw,
        difficulty=diff,
    )


@router.post("/hints")
def get_hint(
    session: SessionDep,
    services: ServicesDep,
):
    return services.levels_runner.next_hint(session)


@router.post("/undo")
def undo_deploy(
    session: SessionDep,
    services: ServicesDep,
):
    result = services.deployer.undo(session)
    services.levels_runner.on_undo(session)
    status_code = HTTP_STATUS.get(result.status, 200)
    return JSONResponse(result.to_dict(), status_code=status_code)
