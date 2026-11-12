from __future__ import annotations

import time

from engine.api.app import create_app
from engine.config import Settings
from engine.events.models import EndpointStats, SecurityEvent, StatsSnapshot
from starlette.testclient import TestClient

from tests.fakes import FakeRuntime


def test_get_economy_unknown_session_404(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeRuntime())) as client:
        res = client.get("/sessions/unknown-session-id/economy")
        assert res.status_code == 404
        assert res.json()["error"] == "SessionNotFound"


def test_get_economy_initial_state(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeRuntime())) as client:
        res_create = client.post("/sessions", json={"company": "starter"})
        assert res_create.status_code == 201
        sid = res_create.json()["id"]

        res = client.get(f"/sessions/{sid}/economy")
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == sid
        assert data["balance"] == 1000.0
        assert data["total_revenue"] == 0.0
        assert data["total_penalties"] == 0.0
        assert data["total_fines"] == 0.0
        assert data["leaked_records"] == 0
        assert data["uptime_percentage"] == 100.0


def test_get_economy_after_stats_and_breach(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeRuntime())) as client:
        services = client.app.state.services
        res_create = client.post("/sessions", json={"company": "starter"})
        assert res_create.status_code == 201
        sid = res_create.json()["id"]

        # Publish snapshot with 500 successful 200 requests (+$5.00)
        totals = EndpointStats(
            endpoint="*",
            count=500,
            rps=50.0,
            s2xx=500,
            s4xx=0,
            s5xx=0,
            unavailable=0,
            p50_ms=10.0,
            p95_ms=20.0,
            p99_ms=30.0,
        )
        snap = StatsSnapshot(
            session_id=sid,
            ts=time.time(),
            window_s=60,
            endpoints={"/test": totals},
            totals=totals,
        )
        services.bus.publish(sid, snap)

        # Allow background listener to process event
        time.sleep(0.05)

        res = client.get(f"/sessions/{sid}/economy")
        assert res.status_code == 200
        data = res.json()
        assert data["balance"] == 1005.0
        assert data["total_revenue"] == 5.0

        # Publish security breach: 2 records leaked (-$1000.00 fine)
        sec = SecurityEvent(
            session_id=sid,
            ts=time.time(),
            flaw_id="flaw_sqli",
            records_leaked=2,
            records_destroyed=0,
            accounts_compromised=0,
            evidence=["pwd1", "pwd2"],
        )
        services.bus.publish(sid, sec)

        time.sleep(0.05)

        res2 = client.get(f"/sessions/{sid}/economy")
        assert res2.status_code == 200
        data2 = res2.json()
        # 1005 - 1000 = 5.0
        assert data2["balance"] == 5.0
        assert data2["total_fines"] == 1000.0
        assert data2["leaked_records"] == 2


def test_economy_lifecycle_reset_and_delete(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeRuntime())) as client:
        services = client.app.state.services
        res_create = client.post("/sessions", json={"company": "starter"})
        assert res_create.status_code == 201
        sid = res_create.json()["id"]

        # Incur breach fine
        services.bus.publish(
            sid,
            SecurityEvent(
                session_id=sid, ts=time.time(), flaw_id="f", records_leaked=1
            ),
        )
        time.sleep(0.05)

        assert client.get(f"/sessions/{sid}/economy").json()["balance"] == 500.0

        # Reset session restores balance
        reset_res = client.post(f"/sessions/{sid}/reset")
        assert reset_res.status_code == 200
        assert client.get(f"/sessions/{sid}/economy").json()["balance"] == 1000.0

        # Delete session
        del_res = client.delete(f"/sessions/{sid}")
        assert del_res.status_code == 204
        assert client.get(f"/sessions/{sid}/economy").status_code == 404
        assert services.economy.get_state(sid) is None
