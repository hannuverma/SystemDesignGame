from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    content_dir: Path = field(default_factory=lambda: REPO_ROOT / "content")
    runs_dir: Path = field(default_factory=lambda: REPO_ROOT / "runs")
    git_timeout_s: float = 10.0
    stop_grace_s: float = 3.0
    health_poll_interval_s: float = 0.15
    max_health_timeout_s: float = 30.0  # upper bound on any manifest's health_timeout_s
    max_stage_bytes: int = 20_000
    log_tail_lines: int = 40
    gateway_port: int = 8001
    gateway_timeout_s: float = 5.0
