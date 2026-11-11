from __future__ import annotations

import sys
from dataclasses import replace

from engine.api.app import create_app
from engine.config import REPO_ROOT, Settings
from starlette.testclient import TestClient

FLAW_DIR = REPO_ROOT / "content/flaws/brute_force_login"


def main() -> None:
    print("=" * 60)
    print("Milestone C: Flaws, Bots, Verdicts, and Replay")
    print("=" * 60)

    settings = replace(
        Settings(),
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(settings)) as client:
        # Step 1: Boot starter company
        print("\n[1/6] Launching starter company session...")
        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        print(f"      Session created: {sid} (healthy: {created['healthy']})")

        # Step 2: Run brute-force attack against baseline
        print("\n[2/6] Running initial attack: brute_force_login...")
        atk1 = client.post(
            f"/sessions/{sid}/attacks",
            json={"flaw_id": "brute_force_login", "wait": True},
        ).json()
        status1 = atk1["verdict"]["status"]
        print(f"      Verdict: {status1}")
        print(f"      Reasons: {atk1['verdict']['reasons']}")
        if status1 != "vulnerable":
            print(f"ERROR: Expected vulnerable, got {status1}", file=sys.stderr)
            sys.exit(1)

        # Step 3: Patch stage with reference fix
        print("\n[3/6] Deploying reference fix to stage login/rate_limit...")
        ref_code = (FLAW_DIR / "reference_fix.py").read_text(encoding="utf-8")
        dep_res = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": ref_code},
        ).json()
        print(f"      Deploy status: {dep_res['status']}")

        # Step 4: Replay attack against patched code
        print("\n[4/6] Replaying attack against patched stage...")
        atk2 = client.post(
            f"/sessions/{sid}/attacks/replay",
            json={"wait": True},
        ).json()
        status2 = atk2["verdict"]["status"]
        print(f"      Verdict: {status2}")
        print(f"      Reasons: {atk2['verdict']['reasons']}")
        if status2 != "fixed":
            print(f"ERROR: Expected fixed, got {status2}", file=sys.stderr)
            sys.exit(1)

        # Step 5: Deploy broken fix that blocks all login requests
        print("\n[5/6] Deploying broken fix that blocks legitimate use...")
        broken_code = (FLAW_DIR / "broken_fix.py").read_text(encoding="utf-8")
        dep_broken = client.put(
            f"/sessions/{sid}/stages/login/rate_limit",
            json={"code": broken_code},
        ).json()
        print(f"      Deploy status: {dep_broken['status']}")

        # Step 6: Replay attack against broken stage
        print("\n[6/6] Replaying attack against broken stage...")
        atk3 = client.post(
            f"/sessions/{sid}/attacks/replay",
            json={"wait": True},
        ).json()
        status3 = atk3["verdict"]["status"]
        print(f"      Verdict: {status3}")
        print(f"      Reasons: {atk3['verdict']['reasons']}")
        if status3 != "broke_business":
            print(f"ERROR: Expected broke_business, got {status3}", file=sys.stderr)
            sys.exit(1)

    print("\n" + "=" * 60)
    print("SUCCESS: Milestone C verification completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
