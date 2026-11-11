from __future__ import annotations

import time
from dataclasses import replace

from engine.api.app import create_app
from engine.config import REPO_ROOT
from fastapi.testclient import TestClient

from tests.conftest import pid_alive

FLAW_DIR = REPO_ROOT / "content/flaws/brute_force_login"


def test_level_runner_progression_undo_and_hints(settings) -> None:
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services

        # 1. Start session with tutorial level on guided difficulty
        created = client.post(
            "/sessions",
            json={"company": "starter", "level": "tutorial", "difficulty": "guided"},
        ).json()
        sid = created["id"]
        assert created["healthy"]

        # 2. Verify level view
        level_view = client.get(f"/sessions/{sid}/level").json()
        assert level_view["id"] == "tutorial"
        assert level_view["difficulty"] == "guided"
        assert level_view["active_objective_id"] == "obj_rate_limit"

        # 3. Test progressive hints
        hint1 = client.post(f"/sessions/{sid}/hints").json()
        assert hint1["hint_number"] == 1
        assert "401" in hint1["hint"]

        hint2 = client.post(f"/sessions/{sid}/hints").json()
        assert hint2["hint_number"] == 2
        assert "sliding time window" in hint2["hint"]

        # 4. Deploy broken code that triggers post-deploy warning in warn mode
        broken_code = (FLAW_DIR / "broken_fix.py").read_text(encoding="utf-8")
        dep_broken = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": broken_code},
        ).json()
        assert dep_broken["status"] == "deployed"
        # Since fail_mode is 'warn', contract failure is returned as a warning
        assert any("Contract failed" in w for w in dep_broken["warnings"])

        # 5. Test undo
        undo_res = client.post(f"/sessions/{sid}/undo")
        assert undo_res.status_code == 200
        assert undo_res.json()["status"] == "deployed"
        assert undo_res.json()["message"] == "undone"

        # 6. Deploy reference fix for objective 1
        ref_code = (FLAW_DIR / "reference_fix.py").read_text(encoding="utf-8")
        dep_ref = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": ref_code},
        ).json()
        assert dep_ref["status"] == "deployed"
        assert dep_ref["warnings"] == []

        # Wait for background evaluation to complete objective 1
        deadline = time.time() + 15.0
        active_id = None
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            active_id = view["active_objective_id"]
            if active_id == "obj_sql_injection":
                break
            time.sleep(0.5)

        assert active_id == "obj_sql_injection"

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid)

