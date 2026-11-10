from __future__ import annotations

import time
from pathlib import Path

from engine.bots.models import BotSpec
from engine.bots.runner import run_bot
from engine.events.bus import EventBus
from engine.flaws.models import BudgetConfig

from tests.conftest import pid_alive


def test_runner_well_behaved(tmp_path: Path) -> None:
    script = tmp_path / "bot.py"
    script.write_text(
        """
import sys, json
from engine.bots.sdk import send_progress, send_report

send_progress(10, "starting attack")
send_report(
    attack="test_attack",
    seed=42,
    succeeded=True,
    completed=True,
    impact={"accounts_compromised": 1, "records_leaked": 0, "records_destroyed": 0},
    usage={"requests_sent": 10, "duration_s": 0.5},
    evidence=["logged in as admin"]
)
"""
    )
    bus = EventBus()
    sub = bus.subscribe("s1")

    spec = BotSpec(
        id="test_attack",
        command=[str(script)],
        target_url="http://127.0.0.1:8000",
        budget=BudgetConfig(max_requests=50, max_duration_s=5.0),
    )

    progress_events = []
    result = run_bot(
        spec, bus=bus, session_id="s1", on_progress=lambda p: progress_events.append(p)
    )

    assert result.status == "completed"
    assert result.report is not None
    assert result.report.succeeded is True
    assert result.report.impact.accounts_compromised == 1
    assert len(progress_events) == 1
    assert progress_events[0].requests_sent == 10
    sub.close()


def test_runner_hangs_and_killed_on_timeout(tmp_path: Path) -> None:
    script = tmp_path / "hanging.py"
    script.write_text(
        """
import time
time.sleep(60)
"""
    )

    spec = BotSpec(
        id="hanging_attack",
        command=[str(script)],
        target_url="http://127.0.0.1:8000",
        budget=BudgetConfig(max_duration_s=0.5),
    )

    start = time.monotonic()
    result = run_bot(spec)
    duration = time.monotonic() - start

    assert result.status == "timeout"
    assert result.report is None
    assert duration < 3.0


def test_runner_spawns_child_and_tree_is_killed(tmp_path: Path) -> None:
    child = tmp_path / "child.py"
    child.write_text(
        """
import time
time.sleep(60)
"""
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        f"""
import subprocess, sys, time
proc = subprocess.Popen([sys.executable, r'{child}'])
with open(r'{tmp_path / "child.pid"}', 'w') as f:
    f.write(str(proc.pid))
time.sleep(60)
"""
    )

    spec = BotSpec(
        id="spawner_attack",
        command=[str(parent)],
        target_url="http://127.0.0.1:8000",
        budget=BudgetConfig(max_duration_s=0.8),
    )

    result = run_bot(spec)
    assert result.status == "timeout"

    time.sleep(0.3)
    pid_file = tmp_path / "child.pid"
    if pid_file.is_file():
        child_pid = int(pid_file.read_text().strip())
        assert not pid_alive(child_pid), (
            "Child process survived parent process group termination"
        )


def test_runner_garbage_and_huge_output(tmp_path: Path) -> None:
    script = tmp_path / "garbage.py"
    script.write_text(
        """
import sys
from engine.bots.sdk import send_report

for i in range(2000):
    print("some random noise text line " + str(i))
print("invalid json {not valid json}")

send_report(
    attack="noisy_attack",
    seed=1,
    succeeded=False,
    completed=True,
    evidence=["failed"]
)
"""
    )

    spec = BotSpec(
        id="noisy_attack",
        command=[str(script)],
        target_url="http://127.0.0.1:8000",
        budget=BudgetConfig(max_duration_s=5.0),
    )

    result = run_bot(spec)
    assert result.status == "completed"
    assert result.report is not None
    assert result.report.succeeded is False
    # Verify stdout buffer was capped to MAX_STDOUT_LINES (1000)
    assert len(result.stdout_lines) <= 1000


def test_runner_crash_no_report(tmp_path: Path) -> None:
    script = tmp_path / "crash.py"
    script.write_text(
        """
import sys
sys.stderr.write("Fatal exception boom\\n")
sys.exit(1)
"""
    )

    spec = BotSpec(
        id="crasher",
        command=[str(script)],
        target_url="http://127.0.0.1:8000",
        budget=BudgetConfig(max_duration_s=5.0),
    )

    result = run_bot(spec)
    assert result.status == "crashed"
    assert result.report is None
    assert result.exit_code == 1
    assert any("Fatal exception" in line for line in result.stderr_lines)
