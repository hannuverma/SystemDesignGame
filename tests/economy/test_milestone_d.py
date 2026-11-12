from __future__ import annotations

import time

from engine.api.app import create_app
from engine.config import Settings
from engine.events.models import EndpointStats, SecurityEvent, StatsSnapshot
from starlette.testclient import TestClient
from tests.fakes import FakeRuntime


def _make_snapshot(
    session_id: str,
    ts: float,
    s2xx: int = 0,
    s4xx: int = 0,
    s5xx: int = 0,
    unavailable: int = 0,
) -> StatsSnapshot:
    count = s2xx + s4xx + s5xx + unavailable
    totals = EndpointStats(
        endpoint="*",
        count=count,
        rps=float(count),
        s2xx=s2xx,
        s4xx=s4xx,
        s5xx=s5xx,
        unavailable=unavailable,
        p50_ms=10.0,
        p95_ms=25.0,
        p99_ms=50.0,
    )
    return StatsSnapshot(
        session_id=session_id,
        ts=ts,
        window_s=60,
        endpoints={"/api/v1": totals},
        totals=totals,
    )


def test_milestone_d_economy_and_consequences_e2e(settings: Settings) -> None:
    """Milestone D end-to-end verification.

    1. Create session -> starting balance is $1,000.00, uptime 100%, 0 leaks.
    2. Connect WebSocket -> listens for live telemetry and economy updates.
    3. Generate 200 OK traffic -> revenue earned (+$0.01 per 2xx), balance rises.
    4. Suffer downtime (500s / 5xx) -> penalties applied (-$1.00 per 5xx), uptime drops.
    5. Bot data breach (SecurityEvent) -> breach fine applied (-$500 per leak), balance drops into negative.
    6. Verify REST endpoint /sessions/{id}/economy accurately reflects all events.
    """
    with TestClient(create_app(settings, FakeRuntime())) as client:
        services = client.app.state.services

        # 1. Create a session
        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        assert created["running"]
        assert created["healthy"]

        # Check initial economy state
        res0 = client.get(f"/sessions/{sid}/economy").json()
        assert res0["session_id"] == sid
        assert res0["balance"] == 1000.0
        assert res0["total_revenue"] == 0.0
        assert res0["total_penalties"] == 0.0
        assert res0["total_fines"] == 0.0
        assert res0["leaked_records"] == 0
        assert res0["uptime_percentage"] == 100.0

        # 2. Open WebSocket stream
        with client.websocket_connect(f"/sessions/{sid}/events/live") as ws:
            # 3. Legitimate 200 OK traffic: 200 requests -> +$2.00
            t1 = time.time()
            snap1 = _make_snapshot(sid, ts=t1, s2xx=200)
            services.bus.publish(sid, snap1)

            # Receive stats and economy update over WebSocket
            received_types = set()
            deadline = time.time() + 2.0
            while time.time() < deadline and "economy" not in received_types:
                msg = ws.receive_json()
                received_types.add(msg["type"])

            assert "economy" in received_types

            # Verify balance earned money
            econ1 = client.get(f"/sessions/{sid}/economy").json()
            assert econ1["balance"] == 1002.0
            assert econ1["total_revenue"] == 2.0
            assert econ1["total_penalties"] == 0.0
            assert econ1["uptime_percentage"] == 100.0

            # 4. Outage / Downtime: 20 5xx errors -> -$20.00 penalty
            # Total requests: 200 + 20 = 220, Uptime: (200 / 220) * 100 = 90.91%
            t2 = t1 + 1.0
            snap2 = _make_snapshot(sid, ts=t2, s2xx=200, s5xx=20)
            services.bus.publish(sid, snap2)

            time.sleep(0.05)
            econ2 = client.get(f"/sessions/{sid}/economy").json()
            # 1002.0 - 20.0 = 982.0
            assert econ2["balance"] == 982.0
            assert econ2["total_penalties"] == 20.0
            assert round(econ2["uptime_percentage"], 1) == 90.9

            # 5. Security Breach: Bot steals 3 database records
            # Fine: 3 * $500 = $1,500 fine
            # Balance: 982.0 - 1500.0 = -518.00 (negative!)
            sec_event = SecurityEvent(
                session_id=sid,
                ts=t2 + 1.0,
                flaw_id="flaw_sql_injection",
                records_leaked=3,
                records_destroyed=0,
                accounts_compromised=0,
                evidence=["user_hash_1", "user_hash_2", "user_hash_3"],
            )
            services.bus.publish(sid, sec_event)

            time.sleep(0.05)
            econ3 = client.get(f"/sessions/{sid}/economy").json()
            assert econ3["balance"] == -518.0
            assert econ3["total_fines"] == 1500.0
            assert econ3["leaked_records"] == 3
            assert econ3["total_revenue"] == 2.0
            assert econ3["total_penalties"] == 20.0
