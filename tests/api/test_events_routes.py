from __future__ import annotations

import pytest
from engine.api.app import create_app
from engine.events.models import DeployEvent, RequestEvent
from starlette.testclient import TestClient

from tests.fakes import FakeRuntime


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, FakeRuntime())) as c:
        yield c


@pytest.fixture
def sid(client):
    res = client.post("/sessions", json={"company": "starter"})
    assert res.status_code == 201
    return res.json()["id"]


def test_get_stats_and_recent_events(client, sid):
    # Retrieve initial stats
    res = client.get(f"/sessions/{sid}/stats?window_s=10")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == sid
    assert data["totals"]["count"] == 0

    # Retrieve initial recent
    res_recent = client.get(f"/sessions/{sid}/events/recent?tail=10")
    assert res_recent.status_code == 200
    assert res_recent.json() == []


def test_events_unknown_session(client):
    assert client.get("/sessions/nonexistent/stats").status_code == 404
    assert client.get("/sessions/nonexistent/events/recent").status_code == 404


def test_websocket_stream_events(client, sid):
    services = client.app.state.services

    with client.websocket_connect(f"/sessions/{sid}/events/live") as ws:
        # Publish RequestEvent
        req_event = RequestEvent(
            session_id=sid,
            ts=100.0,
            method="GET",
            path="/hello",
            endpoint="hello",
            status=200,
            outcome="ok",
            latency_ms=15.0,
            upstream_ms=12.0,
            client="127.0.0.1",
            bytes_in=0,
            bytes_out=50,
        )
        services.bus.publish(sid, req_event)

        msg1 = ws.receive_json()
        assert msg1["type"] == "request"
        assert msg1["data"]["endpoint"] == "hello"

        # Publish DeployEvent
        dep_event = DeployEvent(
            session_id=sid,
            ts=101.0,
            phase="finished",
            endpoint="hello",
            stage="greeting",
            step="done",
            status="deployed",
            message="all good",
        )
        services.bus.publish(sid, dep_event)

        msg2 = ws.receive_json()
        assert msg2["type"] == "deploy"
        assert msg2["data"]["phase"] == "finished"
