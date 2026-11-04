"""FakeRuntime: lets us test every deploy failure path in milliseconds, no processes.

Behaviour is driven by markers in the company's source, so a test "breaks" a deploy simply by
submitting code that contains one of them:
    NO_START     -> start/restart raises RuntimeStartError
    CRASH_ME     -> process dies right after starting
    HEALTH_FAIL  -> process stays up but never becomes healthy
`fail_starts` additionally forces specific start numbers (1-based) to fail, which is how we
simulate "the rollback itself failed".
"""

from __future__ import annotations

import itertools
from pathlib import Path

from engine.errors import RuntimeStartError
from engine.manifest import CompanyManifest
from engine.runtime.base import RunHandle


class FakeRuntime:
    def __init__(self, fail_starts: set[int] | None = None) -> None:
        self.fail_starts = fail_starts or set()
        self.starts = 0
        self.stops = 0
        self._ids = itertools.count(1)
        self._state: dict[str, str] = {}  # run_id -> "up" | "dead" | "unhealthy"

    @staticmethod
    def _source(workdir: Path) -> str:
        return "\n".join(p.read_text() for p in sorted(workdir.rglob("*.py")))

    def start(self, workdir: Path, manifest: CompanyManifest, log_path: Path) -> RunHandle:
        self.starts += 1
        source = self._source(workdir)
        if self.starts in self.fail_starts or "NO_START" in source:
            raise RuntimeStartError("fake: cannot start", ["fake log: boom"])
        run_id = f"fake-{next(self._ids)}"
        self._state[run_id] = (
            "dead" if "CRASH_ME" in source else "unhealthy" if "HEALTH_FAIL" in source else "up"
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as log:
            log.write(f"fake start #{self.starts}\n")
        return RunHandle(run_id, 0, 9000, workdir, log_path, manifest)

    def stop(self, handle: RunHandle) -> None:
        if self._state.pop(handle.run_id, None) is not None:
            self.stops += 1

    def restart(self, handle: RunHandle) -> RunHandle:
        self.stop(handle)
        return self.start(handle.workdir, handle.manifest, handle.log_path)

    def is_running(self, handle: RunHandle) -> bool:
        return self._state.get(handle.run_id) in ("up", "unhealthy")

    def healthy(self, handle: RunHandle) -> bool:
        return self._state.get(handle.run_id) == "up"

    def logs(self, handle: RunHandle, tail: int = 200) -> list[str]:
        try:
            return handle.log_path.read_text().splitlines()[-tail:]
        except OSError:
            return []
