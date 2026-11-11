from __future__ import annotations

import time
from dataclasses import replace

from engine.api.app import create_app
from engine.config import REPO_ROOT
from starlette.testclient import TestClient

from tests.conftest import pid_alive

FLAW_DIR = REPO_ROOT / "content/flaws/brute_force_login"


def test_milestone_c_flaw_attack_patch_replay_and_contract(settings) -> None:
    """Milestone C: end-to-end flow of flaw detection, patching, replay and contract verification.

    1. Attack starter company -> flaw is vulnerable.
    2. Patch stage with reference fix -> replay repels attack and contract passes (fixed).
    3. Deploy stage that blocks legitimate use -> contract fails (broke_business).
    """
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services

        # 1. Create starter company session
        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        assert created["healthy"]

        # 2. Launch initial attack -> expect 'vulnerable'
        attack1 = client.post(
            f"/sessions/{sid}/attacks",
            json={"flaw_id": "brute_force_login", "wait": True},
        ).json()
        assert attack1["status"] == "completed"
        assert attack1["verdict"]["status"] == "vulnerable"
        assert attack1["bot_result"]["report"]["succeeded"] is True
        assert attack1["bot_result"]["report"]["impact"]["accounts_compromised"] >= 1

        # 3. Patch the stage with reference fix
        ref_fix_code = (FLAW_DIR / "reference_fix.py").read_text(encoding="utf-8")
        patch_res = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": ref_fix_code},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["status"] == "deployed"

        # 4. Replay attack against patched code -> expect 'fixed'
        attack2 = client.post(
            f"/sessions/{sid}/attacks/replay",
            json={"wait": True},
        ).json()
        assert attack2["status"] == "completed"
        assert attack2["verdict"]["status"] == "fixed"
        assert attack2["bot_result"]["report"]["succeeded"] is False
        assert attack2["contract_result"]["passed"] is True

        # 5. Deploy deliberately broken fix that blocks legitimate use
        broken_code = (FLAW_DIR / "broken_fix.py").read_text(encoding="utf-8")
        broken_deploy = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": broken_code},
        )
        assert broken_deploy.status_code == 200
        assert broken_deploy.json()["status"] == "deployed"

        # 6. Replay attack against broken fix -> expect 'broke_business'
        attack3 = client.post(
            f"/sessions/{sid}/attacks/replay",
            json={"wait": True},
        ).json()
        assert attack3["status"] == "completed"
        assert attack3["verdict"]["status"] == "broke_business"
        assert attack3["bot_result"]["report"]["succeeded"] is False
        assert attack3["contract_result"]["passed"] is False

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid)
