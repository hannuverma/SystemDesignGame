from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from engine.api.deps import ServicesDep, SessionDep
from engine.api.schemas import StageDetail, StageSummary, StageUpdate
from engine.deploy import HTTP_STATUS

router = APIRouter(prefix="/sessions/{session_id}/stages", tags=["stages"])


@router.get("", response_model=list[StageSummary])
def list_stages(session: SessionDep, services: ServicesDep):
    return [StageSummary.of(s) for s in services.workspace.list_stages(session)]


@router.get("/{endpoint}/{name}", response_model=StageDetail)
def get_stage(endpoint: str, name: str, session: SessionDep, services: ServicesDep):
    return StageDetail.of(services.workspace.get_stage(session, endpoint, name))


@router.put("/{endpoint}/{name}")
def update_stage(
    endpoint: str,
    name: str,
    body: StageUpdate,
    session: SessionDep,
    services: ServicesDep,
):
    result = services.deployer.deploy_stage(session, endpoint, name, body.code)
    return JSONResponse(result.to_dict(), status_code=HTTP_STATUS[result.status])
