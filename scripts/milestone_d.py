from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import time  # noqa: E402

from engine.api.app import create_app  # noqa: E402
from engine.config import Settings  # noqa: E402
from engine.events.models import EndpointStats, SecurityEvent, StatsSnapshot  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from tests.fakes import FakeRuntime  # noqa: E402


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


def main() -> None:
    print("=" * 60)
    print("Milestone D: Economy & Consequences")
    print("=" * 60)

    settings = Settings()

    with TestClient(create_app(settings, FakeRuntime())) as client:
        services = client.app.state.services

        # Step 1: Boot starter company session
        print("\n[1/5] Launching starter company session...")
        res_create = client.post("/sessions", json={"company": "starter"})
        if res_create.status_code != 201:
            print(f"ERROR: Session creation failed: {res_create.text}", file=sys.stderr)
            sys.exit(1)
        sid = res_create.json()["id"]
        print(f"      Session created: {sid}")

        # Initial economy state
        econ0 = client.get(f"/sessions/{sid}/economy").json()
        print(f"      Starting Balance: ${econ0['balance']:,.2f}")
        print(
            f"      Uptime: {econ0['uptime_percentage']}% | Leaks: {econ0['leaked_records']}"
        )
        assert econ0["balance"] == 1000.0

        # Step 2: Simulate 500 successful 200 OK requests
        print("\n[2/5] Simulating legitimate 200 OK traffic (+500 requests)...")
        t1 = time.time()
        snap1 = _make_snapshot(sid, ts=t1, s2xx=500)
        services.bus.publish(sid, snap1)
        time.sleep(0.05)

        econ1 = client.get(f"/sessions/{sid}/economy").json()
        print(
            f"      Revenue Earned: +${econ1['total_revenue']:.2f} | Current Balance: ${econ1['balance']:,.2f}"
        )
        if econ1["balance"] != 1005.0:
            print(f"ERROR: Expected $1005.00, got {econ1['balance']}", file=sys.stderr)
            sys.exit(1)

        # Step 3: Suffer downtime (50 5xx errors)
        print("\n[3/5] Simulating outage / server downtime (50 5xx errors)...")
        t2 = t1 + 1.0
        snap2 = _make_snapshot(sid, ts=t2, s2xx=500, s5xx=50)
        services.bus.publish(sid, snap2)
        time.sleep(0.05)

        econ2 = client.get(f"/sessions/{sid}/economy").json()
        print(
            f"      Downtime Penalties: -${econ2['total_penalties']:.2f} | Balance: ${econ2['balance']:,.2f}"
        )
        print(f"      Calculated SLA Uptime: {econ2['uptime_percentage']:.2f}%")
        if econ2["balance"] != 955.0:
            print(f"ERROR: Expected $955.00, got {econ2['balance']}", file=sys.stderr)
            sys.exit(1)

        # Step 4: Security Breach (Bot leaks 3 records)
        print("\n[4/5] Bot attack succeeds: 3 database records leaked!")
        sec_event = SecurityEvent(
            session_id=sid,
            ts=t2 + 1.0,
            flaw_id="flaw_sql_injection",
            records_leaked=3,
            records_destroyed=0,
            accounts_compromised=0,
            evidence=["token_1", "token_2", "token_3"],
        )
        services.bus.publish(sid, sec_event)
        time.sleep(0.05)

        econ3 = client.get(f"/sessions/{sid}/economy").json()
        print(
            f"      Regulatory Fine Applied: -${econ3['total_fines']:.2f} ($500/record leaked)"
        )
        print(f"      Final Balance (In The Red): ${econ3['balance']:,.2f}")
        print(f"      Total Records Leaked: {econ3['leaked_records']}")
        if econ3["balance"] != -545.0:
            print(f"ERROR: Expected -$545.00, got {econ3['balance']}", file=sys.stderr)
            sys.exit(1)

        # Step 5: Verify WebSocket live updates
        print("\n[5/5] Verifying live WebSocket economy streaming...")
        with client.websocket_connect(f"/sessions/{sid}/events/live") as ws:
            # Trigger reset
            client.post(f"/sessions/{sid}/reset")
            msg = ws.receive_json()
            # Drain until economy update
            while msg.get("type") != "economy":
                msg = ws.receive_json()
            assert msg["type"] == "economy"
            assert msg["data"]["balance"] == 1000.0
            print("      WebSocket received EconomyUpdate reset successfully!")

    print("\n" + "=" * 60)
    print("Milestone D PASSED: All economy metrics & consequences verified!")
    print("=" * 60)


if __name__ == "__main__":
    main()
