from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from engine.config import REPO_ROOT, Settings
from engine.workspace import WorkspaceManager


def pid_alive(pid: int) -> bool:
    """True if the process exists and is not a zombie."""
    if os.name == "nt":
        # Windows: use tasklist to check if the PID exists
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # tasklist prints "INFO: No tasks are running..." when PID not found
            return str(pid) in result.stdout and "No tasks" not in result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False
    else:
        # POSIX: signal 0 checks existence without killing
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return True
        return state != "Z"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        content_dir=REPO_ROOT / "content",
        runs_dir=tmp_path / "runs",
        health_poll_interval_s=0.01,
        max_health_timeout_s=0.5,  # keep "never healthy" tests fast
        stop_grace_s=1.0,
    )


@pytest.fixture
def workspace(settings: Settings) -> WorkspaceManager:
    return WorkspaceManager(settings)
