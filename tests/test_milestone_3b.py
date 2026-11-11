from __future__ import annotations

import time
from dataclasses import replace

from engine.api.app import create_app
from engine.config import REPO_ROOT
from fastapi.testclient import TestClient

from tests.conftest import pid_alive

FLAW_RATE_LIMIT = REPO_ROOT / "content/flaws/brute_force_login"
FLAW_SQLI = REPO_ROOT / "content/flaws/sql_injection_search"
FLAW_IDOR = REPO_ROOT / "content/flaws/missing_ownership_delete"


def test_milestone_3b_full_level_progression(settings) -> None:
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services

        # 1. Start tutorial level session
        created = client.post(
            "/sessions",
            json={"company": "starter", "level": "tutorial", "difficulty": "guided"},
        ).json()
        sid = created["id"]
        assert created["healthy"]

        # 2. Objective 1 is active
        view1 = client.get(f"/sessions/{sid}/level").json()
        assert view1["active_objective_id"] == "obj_rate_limit"

        # 3. Deploy reference fix for objective 1
        ref1 = (FLAW_RATE_LIMIT / "reference_fix.py").read_text(encoding="utf-8")
        dep1 = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": ref1},
        ).json()
        assert dep1["status"] == "deployed"

        # Wait for objective 1 to complete and objective 2 to unlock
        deadline = time.time() + 25.0
        active_id = None
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            active_id = view["active_objective_id"]
            if active_id == "obj_sql_injection":
                break
            time.sleep(0.5)
        assert active_id == "obj_sql_injection"

        # 4. Deploy broken fix to verify fail_mode=warn and undo
        broken1 = (FLAW_RATE_LIMIT / "broken_fix.py").read_text(encoding="utf-8")
        dep_broken = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": broken1},
        ).json()
        assert dep_broken["status"] == "deployed"
        assert len(dep_broken["warnings"]) > 0

        # One-click undo restores working code
        undo_res = client.post(f"/sessions/{sid}/undo").json()
        assert undo_res["status"] == "deployed"
        assert undo_res["message"] == "undone"

        # 5. Deploy reference fix for objective 2 (search)
        ref2 = (FLAW_SQLI / "reference_fix.py").read_text(encoding="utf-8")
        dep2 = client.put(
            f"/sessions/{sid}/stages/search/db_query",
            json={"code": ref2},
        ).json()
        assert dep2["status"] == "deployed"

        # Wait for objective 2 to complete and objective 3 to unlock
        deadline = time.time() + 25.0
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            active_id = view["active_objective_id"]
            if active_id == "obj_missing_ownership":
                break
            time.sleep(0.5)
        assert active_id == "obj_missing_ownership"

        # 6. Deploy reference fix for objective 3 (delete)
        ref3 = (FLAW_IDOR / "reference_fix.py").read_text(encoding="utf-8")
        dep3 = client.put(
            f"/sessions/{sid}/stages/delete_item/ownership_check",
            json={"code": ref3},
        ).json()
        assert dep3["status"] == "deployed"

        # Wait for level to complete
        deadline = time.time() + 25.0
        is_completed = False
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            is_completed = view["is_completed"]
            if is_completed:
                break
            time.sleep(0.5)
        assert is_completed is True

        # 7. Regression check: re-introduce vulnerability in login
        vuln_login = (
            "def check_login_rate(ip: str, username: str) -> bool:\n    return True\n"
        )
        dep_vuln = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": vuln_login},
        ).json()
        assert dep_vuln["status"] == "deployed"

        # Wait for regression guard to detect regression on objective 1
        deadline = time.time() + 25.0
        regressed = False
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            obj1 = next(
                (o for o in view["objectives"] if o["id"] == "obj_rate_limit"), None
            )
            if obj1 and obj1["status"] == "regressed":
                regressed = True
                break
            time.sleep(0.5)
        assert regressed is True

        # 8. Check marks per difficulty
        # Guided: active endpoint is highlighted
        eps_guided = client.get(f"/sessions/{sid}/endpoints").json()
        login_ep = next((e for e in eps_guided if e["endpoint"] == "login"), None)
        assert login_ep is not None and login_ep["highlighted"] is True

        # Normal: no static flaw highlights (only observable symptoms)
        eps_normal = client.get(f"/sessions/{sid}/endpoints?difficulty=normal").json()
        assert not any(e["highlighted"] for e in eps_normal)

        # Hard: no marks
        eps_hard = client.get(f"/sessions/{sid}/endpoints?difficulty=hard").json()
        assert len(eps_hard) == 0

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid)
