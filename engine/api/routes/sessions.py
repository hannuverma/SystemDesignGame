from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from engine.api.deps import ServicesDep, SessionDep
from engine.api.schemas import CreateSessionRequest, SessionOut
from engine.deploy import HTTP_STATUS

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _session_out(services, session) -> SessionOut:
    handle = services.deployer.handle(session.id)
    runtime = services.runtime
    return SessionOut(
        id=session.id,
        company=session.manifest.name,
        url=handle.base_url if handle else None,
        running=bool(handle and runtime.is_running(handle)),
        healthy=bool(handle and runtime.healthy(handle)),
        commit=services.workspace.head(session),
    )


@router.post("", status_code=201, response_model=SessionOut)
def create_session(body: CreateSessionRequest, services: ServicesDep):
    session = services.workspace.create_session(company=body.company)
    try:
        services.deployer.launch(session=session)
    except Exception:
        services.workspace.delete_session(session.id)
        raise
    return _session_out(services, session)


@router.get("/{session_id}", response_model=SessionOut)
def get_session_info(session: SessionDep, services: ServicesDep):
    return _session_out(services, session)


@router.get("/{session_id}/history")
def get_session_history(
    session: SessionDep, services: ServicesDep, limit: int = Query(50, ge=1, le=500)
):
    return services.workspace.history(session, limit)


@router.delete("/{session_id}/delete")
def delete_session(session: SessionDep, services: ServicesDep):
    services.deployer.shutdown(session.id)
    services.workspace.delete_session(session.id)


@router.get("/{session_id}/logs")
def logs(
    session: SessionDep,
    services: ServicesDep,
    tail: int = Query(100, ge=1, le=1000),
):
    return {"lines": services.deployer.logs(session.id, tail)}


@router.post("/{session_id}/reset")
def reset(session: SessionDep, services: ServicesDep):
    result = services.deployer.reset(session)
    return JSONResponse(result.to_dict(), status_code=HTTP_STATUS[result.status])
