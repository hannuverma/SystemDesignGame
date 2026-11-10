from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx


class BudgetExceededError(Exception):
    """Raised when an attack hits its cooperative budget limit."""


class BotConfig:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.attack_id: str = raw.get("id", "unknown_attack")
        self.target_url: str = raw.get("target_url", "").rstrip("/")
        budget = raw.get("budget", {})
        self.max_requests: int = budget.get("max_requests", 500)
        self.max_duration_s: float = float(budget.get("max_duration_s", 30.0))
        self.max_concurrency: int = budget.get("max_concurrency", 5)
        self.seed: int = raw.get("seed", 42)


def load_config() -> BotConfig:
    """Load bot configuration from the JSON file path passed in sys.argv[1]."""
    if len(sys.argv) < 2:
        raise ValueError("Missing path to bot config JSON in sys.argv[1]")
    cfg_path = Path(sys.argv[1])
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return BotConfig(data)


def send_progress(requests_sent: int, note: str = "") -> None:
    """Emit a JSON progress line to stdout."""
    payload = {"type": "progress", "requests_sent": requests_sent, "note": note}
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def send_report(
    attack: str,
    seed: int,
    succeeded: bool,
    completed: bool,
    impact: dict[str, int] | None = None,
    usage: dict[str, Any] | None = None,
    evidence: list[str] | None = None,
) -> None:
    """Emit the final JSON report line to stdout."""
    payload = {
        "type": "report",
        "attack": attack,
        "seed": seed,
        "succeeded": succeeded,
        "completed": completed,
        "impact": impact or {},
        "usage": usage or {},
        "evidence": (evidence or [])[:5],
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


class BotClient:
    """Cooperative HTTP client enforcing request budget and timeout."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.start_time = time.monotonic()
        self.requests_sent = 0
        self.client = httpx.Client(
            headers={"X-Game-Client": f"bot:{config.attack_id}"},
            timeout=5.0,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        now = time.monotonic()
        if now - self.start_time > self.config.max_duration_s:
            raise BudgetExceededError("Attack exceeded duration budget")
        if self.requests_sent >= self.config.max_requests:
            raise BudgetExceededError("Attack exceeded request count budget")

        url = (
            f"{self.config.target_url}{path}"
            if path.startswith("/")
            else f"{self.config.target_url}/{path}"
        )
        self.requests_sent += 1
        return self.client.request(method, url, **kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> BotClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
