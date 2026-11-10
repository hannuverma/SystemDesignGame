from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from engine.bots.models import BotProgress, BotReport, BotResult, BotSpec
from engine.events.models import DeployEvent

if TYPE_CHECKING:
    from engine.events.bus import EventBus

logger = logging.getLogger(__name__)

MAX_STDOUT_LINES = 1000
MAX_LINE_LENGTH = 4096


def _kill_process_group(pid: int) -> None:
    """Kill process group across Windows and POSIX."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(pid, signal.SIGKILL)


def run_bot(
    spec: BotSpec,
    bus: EventBus | None = None,
    session_id: str | None = None,
    on_progress: Callable[[BotProgress], None] | None = None,
) -> BotResult:
    """Run an attack bot as a separate OS process with hard timeout and output parsing."""
    start_time = time.monotonic()

    # Publish started event
    if bus and session_id:
        bus.publish(
            session_id,
            DeployEvent(
                session_id=session_id,
                ts=time.time(),
                phase="started",
                endpoint=spec.id,
                stage="attack",
                step="launch",
                status="running",
                message=f"Attack {spec.id} started",
            ),
        )

    # Write config JSON to temp file
    temp_dir = tempfile.mkdtemp(prefix="bot_")
    config_path = Path(temp_dir) / "config.json"
    config_dict = {
        "id": spec.id,
        "target_url": spec.target_url,
        "env": spec.env,
        "budget": spec.budget.model_dump(),
        "seed": spec.seed,
    }
    config_path.write_text(json.dumps(config_dict), encoding="utf-8")

    cmd = list(spec.command)
    if cmd[0].endswith(".py") or cmd[0] == "python":
        if cmd[0] == "python":
            cmd[0] = sys.executable
        else:
            cmd = [sys.executable, *cmd]
    cmd.append(str(config_path))

    merged_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        **spec.env,
    }

    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(spec.cwd) if spec.cwd else None,
            env=merged_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            **popen_kwargs,
        )
    except Exception as exc:
        duration = round(time.monotonic() - start_time, 3)
        return BotResult(
            status="crashed",
            duration_s=duration,
            stderr_lines=[f"Failed to launch process: {exc}"],
        )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    report: BotReport | None = None

    def read_stdout():
        nonlocal report
        if not proc.stdout:
            return
        for line in proc.stdout:
            if len(stdout_lines) < MAX_STDOUT_LINES:
                stdout_lines.append(line.rstrip("\r\n")[:MAX_LINE_LENGTH])
            stripped = line.strip()
            if not (stripped.startswith("{") and stripped.endswith("}")):
                continue
            try:
                data = json.loads(stripped)
            except Exception:
                continue

            msg_type = data.get("type")
            if msg_type == "progress":
                with contextlib.suppress(Exception):
                    prog = BotProgress.model_validate(data)
                    if on_progress:
                        on_progress(prog)
                    if bus and session_id:
                        bus.publish(
                            session_id,
                            DeployEvent(
                                session_id=session_id,
                                ts=time.time(),
                                phase="step",
                                endpoint=spec.id,
                                stage="attack",
                                step="progress",
                                status="running",
                                message=f"Requests: {prog.requests_sent} ({prog.note})",
                            ),
                        )
            elif msg_type == "report":
                with contextlib.suppress(Exception):
                    report = BotReport.model_validate(data)

    def read_stderr():
        if not proc.stderr:
            return
        for line in proc.stderr:
            if len(stderr_lines) < MAX_STDOUT_LINES:
                stderr_lines.append(line.rstrip("\r\n")[:MAX_LINE_LENGTH])

    out_thread = threading.Thread(target=read_stdout, daemon=True)
    err_thread = threading.Thread(target=read_stderr, daemon=True)
    out_thread.start()
    err_thread.start()

    timeout_s = spec.budget.max_duration_s
    timed_out = False

    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2.0)

    out_thread.join(timeout=1.0)
    err_thread.join(timeout=1.0)
    duration = round(time.monotonic() - start_time, 3)

    # Determine status
    if timed_out:
        status = "timeout"
    elif report is not None:
        status = "completed" if proc.returncode == 0 else "error"
    elif proc.returncode != 0:
        status = "crashed"
    else:
        # Exited with 0 but emitted no report
        status = "crashed"

    # Publish finished event
    if bus and session_id:
        bus.publish(
            session_id,
            DeployEvent(
                session_id=session_id,
                ts=time.time(),
                phase="finished",
                endpoint=spec.id,
                stage="attack",
                step="done",
                status=status,
                message=(
                    f"Attack finished ({status}): succeeded={report.succeeded}"
                    if report
                    else f"Attack finished ({status}): no report"
                ),
            ),
        )

    # Clean up temp config
    with contextlib.suppress(Exception):
        config_path.unlink(missing_ok=True)
        Path(temp_dir).rmdir()

    return BotResult(
        status=status,  # type: ignore[arg-type]
        report=report,
        exit_code=proc.returncode,
        duration_s=duration,
        stdout_lines=stdout_lines,
        stderr_lines=stderr_lines,
    )
