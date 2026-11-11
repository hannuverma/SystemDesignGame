from __future__ import annotations

import time
from dataclasses import replace

from engine.api.app import create_app
from fastapi.testclient import TestClient

from tests.conftest import pid_alive


def test_attacks_api_flow(settings) -> None:
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services

        # 1. Create a session
        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        assert created["healthy"]

        # 2. Replay without prior attack returns 404
        bad_replay = client.post(f"/sessions/{sid}/attacks/replay")
        assert bad_replay.status_code == 404

        # 3. Non-existent flaw returns 404
        bad_flaw = client.post(
            f"/sessions/{sid}/attacks",
            json={"flaw_id": "non_existent_flaw_xyz"},
        )
        assert bad_flaw.status_code == 404

        # 4. Start attack with wait=True
        res = client.post(
            f"/sessions/{sid}/attacks",
            json={"flaw_id": "brute_force_login", "wait": True},
        )
        assert res.status_code == 200
        data = res.json()
        atk_id = data["id"]
        assert data["status"] == "completed"
        assert data["verdict"] is not None
        assert data["verdict"]["status"] == "vulnerable"
        assert data["bot_result"]["report"]["succeeded"] is True

        # 5. GET attack by id
        get_res = client.get(f"/sessions/{sid}/attacks/{atk_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == atk_id

        # 6. GET list of attacks
        list_res = client.get(f"/sessions/{sid}/attacks")
        assert list_res.status_code == 200
        attacks = list_res.json()
        assert len(attacks) == 1
        assert attacks[0]["id"] == atk_id

        # 7. Replay the attack
        replay_res = client.post(
            f"/sessions/{sid}/attacks/replay",
            json={"wait": True},
        )
        assert replay_res.status_code == 200
        replay_data = replay_res.json()
        assert replay_data["id"] != atk_id
        assert replay_data["flaw_id"] == "brute_force_login"
        assert replay_data["verdict"]["status"] == "vulnerable"

        # 8. Attacks list now has 2 items
        attacks_after = client.get(f"/sessions/{sid}/attacks").json()
        assert len(attacks_after) == 2

        # 9. Concurrency test: simulate an active attack lock
        services.attacks._active_attacks[sid] = "fake_active"
        try:
            conflict_res = client.post(
                f"/sessions/{sid}/attacks",
                json={"flaw_id": "brute_force_login"},
            )
            assert conflict_res.status_code == 409
            assert "already running" in conflict_res.json()["detail"]
        finally:
            services.attacks._active_attacks.pop(sid, None)

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid)
