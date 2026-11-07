from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from engine.api.deps import ServicesDep, SessionDep
from engine.events.bus import SubscriptionClosed
from engine.events.models import DeployEvent, RequestEvent, StatsSnapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions/{session_id}", tags=["events"])


def _serialize_event(event: Any) -> dict[str, Any]:
    if isinstance(event, RequestEvent):
        return {"type": "request", "data": event.to_dict()}
    if isinstance(event, DeployEvent):
        return {"type": "deploy", "data": event.to_dict()}
    if isinstance(event, StatsSnapshot):
        return {"type": "stats", "data": event.to_dict()}
    if hasattr(event, "to_dict"):
        return {"type": "custom", "data": event.to_dict()}
    return {"type": "custom", "data": event}


@router.get("/stats")
def get_stats(
    session: SessionDep,
    services: ServicesDep,
    window_s: int = Query(default=10, ge=1, le=60),
) -> dict[str, Any]:
    """Return aggregated endpoint metrics snapshot over the requested window."""
    snapshot = services.stats.snapshot(session.id, window_s=window_s)
    return snapshot.to_dict()


@router.get("/events/recent")
def get_recent_events(
    session: SessionDep,
    services: ServicesDep,
    tail: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Return the raw recent requests ring buffer for this session."""
    events = services.stats.recent_requests(session.id, tail=tail)
    return [e.to_dict() for e in events]


@router.websocket("/events/live")
async def stream_live_events(
    websocket: WebSocket, session_id: str, services: ServicesDep
) -> None:
    """Stream live telemetry events (requests, deployments, stats snapshots) over WebSocket."""
    # Verify session exists
    try:
        services.workspace.get_session(session_id)
    except Exception:
        await websocket.close(code=4404, reason="Session not found")
        return

    await websocket.accept()
    sub = services.bus.subscribe(session_id)

    try:
        while True:
            try:
                event = await sub.get()
            except SubscriptionClosed:
                break
            payload = _serialize_event(event)
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected for session %s", session_id)
    except Exception:
        logger.exception(
            "Error in WebSocket live events stream for session %s", session_id
        )
    finally:
        sub.close()
