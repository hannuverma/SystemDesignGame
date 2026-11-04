from __future__ import annotations

from fastapi import APIRouter, HTTPException

from engine.api.deps import ServicesDep, SessionDep

router = APIRouter(prefix="/sessions/{session_id}/deploys", tags=["deploys"])


@router.get("/latest")
def get_latest(session: SessionDep, services: ServicesDep):
    result = services.deployer.latest(session.id)
    if result is None:
        raise HTTPException(404, "no deploy has happened in this session yet")
    return result.to_dict()