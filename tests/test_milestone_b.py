from __future__ import annotations

import time
from dataclasses import replace

import httpx
from engine.api.app import create_app
from starlette.testclient import TestClient

from tests.conftest import pid_alive

GOOD_GREETING = (
    'def build_greeting(name: str) -> dict:\n    return {"message": f"Yo {name}"}'
)


def test_milestone_b_gateway_traffic_websocket_stats_and_redeploy(settings):
    """Milestone B: send traffic through gateway, verify live telemetry over WebSocket,

    redeploy a stage, and confirm public gateway URL does not change.
    """
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services
        gateway_url = services.gateway.url

        # 1. Create a session running the real starter company
        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        assert created["healthy"]

        # Public gateway URL for this session
        public_url = f"{gateway_url}/{sid}/hello"

        # 2. Open WebSocket stream
        with client.websocket_connect(f"/sessions/{sid}/events/live") as ws:
            # 3. Send traffic through the Gateway
            res = httpx.get(f"{public_url}?name=Ada")
            assert res.status_code == 200
            assert res.json() == {"message": "Hello, Ada!"}

            # 4. Watch for events on WebSocket
            received_types = set()
            deadline = time.time() + 5.0
            while time.time() < deadline and "request" not in received_types:
                msg = ws.receive_json()
                received_types.add(msg["type"])
                if msg["type"] == "request":
                    assert msg["data"]["endpoint"] == "hello"
                    assert msg["data"]["status"] == 200
                    assert msg["data"]["outcome"] == "ok"

            assert "request" in received_types

            # 5. Redeploy stage 'hello' with updated greeting
            deploy_res = client.put(
                f"/sessions/{sid}/stages/hello/greeting",
                json={"code": GOOD_GREETING},
            )
            assert deploy_res.status_code == 200
            assert deploy_res.json()["status"] == "deployed"

            # 6. Verify public URL did NOT change and serves the new greeting
            res2 = httpx.get(f"{public_url}?name=Ada")
            assert res2.status_code == 200
            assert res2.json() == {"message": "Yo Ada"}

            # 7. Check deploy events over WebSocket
            deadline = time.time() + 5.0
            while time.time() < deadline and "deploy" not in received_types:
                msg = ws.receive_json()
                received_types.add(msg["type"])

            assert "deploy" in received_types

        # 8. Check REST stats & recent requests endpoints
        stats_resp = client.get(f"/sessions/{sid}/stats?window_s=10")
        assert stats_resp.status_code == 200
        stats_data = stats_resp.json()
        assert stats_data["session_id"] == sid
        assert stats_data["endpoints"]["hello"]["count"] >= 2
        assert stats_data["endpoints"]["hello"]["s2xx"] >= 2

        recent_resp = client.get(f"/sessions/{sid}/events/recent?tail=10")
        assert recent_resp.status_code == 200
        recent_data = recent_resp.json()
        assert len(recent_data) >= 2
        assert recent_data[-1]["endpoint"] == "hello"

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid), "Company process terminated with engine shutdown"
