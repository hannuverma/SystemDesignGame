from __future__ import annotations

import logging

from fastapi import APIRouter

from engine.api.deps import ServicesDep, SessionDep
from engine.api.schemas import EconomyOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions/{session_id}/economy", tags=["economy"])


@router.get("", response_model=EconomyOut)
def get_economy_state(session: SessionDep, services: ServicesDep) -> EconomyOut:
    """Return the current financial and consequence metrics for the session."""
    state = services.economy.get_or_create(session.id)
    return EconomyOut(**state.to_dict())
