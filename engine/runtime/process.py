
from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from engine.errors import RuntimeStartError
from engine.manifest import CompanyManifest
from engine.runtime.base import RunHandle
from engine.runtime.ports import find_free_port, port_is_free

_TAIL_READ_BYTES = 256 * 1024


def read_log_trail(path: Path, tail: int) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_READ_BYTES))
            data = handle.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-tail:] if tail > 0 else []

class ProcessRuntime:

    def __init__(self, stop_grace_s: float = 3.0) -> None:
        self._grace = stop_grace_s
        self._procs: dict[str, subprocess.Popen[bytes]] = {}

    def start(
        self,
        manifest: CompanyManifest,
        workdir: Path,
        log_path: Path,
        *,
        port: int |None = None,
    ) -> RunHandle:
        port = port or find_free_port()
        argv = shlex.split(manifest.format_run(port))
        if not argv:
            raise RuntimeStartError("run command is empty")
        if argv[0] in ("python", "python3"):
            argv[0] = sys.executable  # use the same interpreter/venv the engine runs in
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",  # keep __pycache__ out of the player's repo
            "PORT": str(port),
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open('ab') as log:
            stamp = datetime.now(UTC).isoformat(timespec="seconds")
            log.write(f"=== start {stamp} port={port} ===\n".encode())
            log.flush()
            try:
                proc = subprocess.Popen(  # noqa: S603 - list argv, no shell
                    argv,
                    cwd=workdir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,  # a file, not a pipe: a full pipe would block the child
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # own process group so we can kill the whole tree
                )
            except OSError as exc:
                raise RuntimeStartError(f"cannot launch {argv[0]!r}: {exc}") from exc
        run_id = uuid.uuid4().hex
        self._procs[run_id] = proc
        return RunHandle(run_id, proc.pid, port, workdir, log_path, manifest)

    def stop(self, handle: RunHandle) -> None:
        proc = self._procs.pop(handle.run_id, None)
        if proc is None:
            return
        pgid = proc.pid  # start_new_session=True made the child its own group leader
        self._signal_group(pgid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=self._grace)
        # Always follow up: children can outlive a leader that exited on SIGTERM.
        self._signal_group(pgid, signal.SIGKILL)
        proc.wait()

    def restart(self, handle: RunHandle) -> RunHandle:
        self.stop(handle)

        port = handle.port
        for _ in range(20):
            if port_is_free(port):
                break
            time.sleep(0.05)

        else:
            port = find_free_port()
        return self.start(handle.manifest, handle.workdir, handle.log_path, port=port)

    def is_running(self, handle: RunHandle) -> bool:
        proc = self._procs.get(handle.run_id)
        return proc is not None and proc.poll() is None

    def healthy(self, handle: RunHandle) -> bool:
        if not self.is_running(handle):
            return False
        
        try:
            response = httpx.get(handle.health_url, timeout = 1.0)
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 300
    
    def logs(self, handle: RunHandle, tail: int = 200) -> list[str]:
        return read_log_trail(handle.log_path, tail)

    @staticmethod
    def _signal_group(pgid: int, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, sig)
        
        