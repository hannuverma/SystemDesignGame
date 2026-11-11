from __future__ import annotations

import time
from dataclasses import replace

from engine.api.app import create_app
from engine.config import REPO_ROOT, Settings
from starlette.testclient import TestClient

FLAW_RATE_LIMIT = REPO_ROOT / "content/flaws/brute_force_login"
FLAW_SQLI = REPO_ROOT / "content/flaws/sql_injection_search"
FLAW_IDOR = REPO_ROOT / "content/flaws/missing_ownership_delete"


def main() -> None:
    print("=" * 65, flush=True)
    print("Milestone 3b: Levels, Objectives, Progression, Waves, and Regressions", flush=True)
    print("=" * 65, flush=True)

    settings = replace(
        Settings(),
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    print("Entering TestClient...", flush=True)
    with TestClient(create_app(settings)) as client:
        # Step 1: Start tutorial level session
        print("\n[1/7] Creating session with tutorial level (guided difficulty)...", flush=True)
        created = client.post(
            "/sessions",
            json={"company": "starter", "level": "tutorial", "difficulty": "guided"},
        ).json()
        sid = created["id"]
        print(f"      Session ID: {sid}", flush=True)

        view1 = client.get(f"/sessions/{sid}/level").json()
        print(f"      Active Objective: {view1['active_objective_id']}")
        assert view1["active_objective_id"] == "obj_rate_limit"

        # Step 2: Deploy reference fix for objective 1
        print("\n[2/7] Deploying reference fix for login rate limiting...", flush=True)
        ref1 = (FLAW_RATE_LIMIT / "reference_fix.py").read_text(encoding="utf-8")
        dep1 = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": ref1},
        ).json()
        print(f"      Deploy status: {dep1['status']}", flush=True)

        print("      Waiting for background verification to unlock objective 2...", flush=True)
        deadline = time.time() + 25.0
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            if view["active_objective_id"] == "obj_sql_injection":
                break
            time.sleep(0.5)
        print(f"      Objective 2 unlocked: {view['active_objective_id']}", flush=True)
        assert view["active_objective_id"] == "obj_sql_injection"

        # Step 3: Test contract warning and undo
        print("\n[3/7] Testing contract warning and undo...", flush=True)
        broken1 = (FLAW_RATE_LIMIT / "broken_fix.py").read_text(encoding="utf-8")
        dep_broken = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": broken1},
        ).json()
        print(f"      Deploy warnings: {dep_broken['warnings']}", flush=True)
        assert len(dep_broken["warnings"]) > 0

        undo_res = client.post(f"/sessions/{sid}/undo").json()
        print(f"      Undo result: {undo_res['status']} ({undo_res['message']})", flush=True)
        assert undo_res["message"] == "undone"

        # Step 4: Deploy reference fix for objective 2 (search)
        print("\n[4/7] Deploying reference fix for SQL injection in search...", flush=True)
        ref2 = (FLAW_SQLI / "reference_fix.py").read_text(encoding="utf-8")
        dep2 = client.put(
            f"/sessions/{sid}/stages/search/db_query",
            json={"code": ref2},
        ).json()
        print(f"      Deploy status: {dep2['status']}", flush=True)

        print("      Waiting for background verification to unlock objective 3...", flush=True)
        deadline = time.time() + 25.0
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            if view["active_objective_id"] == "obj_missing_ownership":
                break
            time.sleep(0.5)
        print(f"      Objective 3 unlocked: {view['active_objective_id']}", flush=True)
        assert view["active_objective_id"] == "obj_missing_ownership"

        # Step 5: Deploy reference fix for objective 3 (delete)
        print("\n[5/7] Deploying reference fix for missing ownership on delete...", flush=True)
        ref3 = (FLAW_IDOR / "reference_fix.py").read_text(encoding="utf-8")
        dep3 = client.put(
            f"/sessions/{sid}/stages/delete_item/ownership_check",
            json={"code": ref3},
        ).json()
        print(f"      Deploy status: {dep3['status']}", flush=True)

        print("      Waiting for level completion...", flush=True)
        deadline = time.time() + 25.0
        is_completed = False
        while time.time() < deadline:
            view = client.get(f"/sessions/{sid}/level").json()
            if view["is_completed"]:
                is_completed = True
                break
            time.sleep(0.5)
        print(f"      Level completed: {is_completed}", flush=True)
        assert is_completed is True

        # Step 6: Test regression guard
        print("\n[6/7] Testing regression guard (reintroducing vulnerability)...", flush=True)
        vuln_login = (
            "def check_login_rate(ip: str, username: str) -> bool:\n    return True\n"
        )
        client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": vuln_login},
        )

        print("      Waiting for regression guard to detect regression...", flush=True)
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
        print(f"      Objective 1 regressed: {regressed}", flush=True)
        assert regressed is True

        # Step 7: Difficulty marks
        print("\n[7/7] Verifying endpoint marks and difficulty visibility...")
        eps = client.get(f"/sessions/{sid}/endpoints").json()
        print(f"      Reported endpoints count: {len(eps)}")

    print("\n" + "=" * 65)
    print("SUCCESS: Milestone 3b completed successfully!")
    print("=" * 65)


if __name__ == "__main__":
    main()
