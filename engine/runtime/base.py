from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..manifest import CompanyManifest


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    pid: int
    port: int
    workdir: Path
    log_path: Path
    manifest: CompanyManifest

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def health_url(self):
        return f"{self.base_url}{self.manifest.health_path}"


class Runtime(Protocol):
    def start(
        self,
        workdir: Path,
        manifest: CompanyManifest,
        log_path: Path,
        env: dict[str, str] | None = None,
    ) -> RunHandle:
        """Launch the company. Raises RuntimeStartError if it cannot be launched at all."""

    def stop(self, handle: RunHandle) -> None:
        """Stop it and everything it spawned. Safe to call more than once."""

    def restart(self, handle: RunHandle) -> RunHandle:
        """Stop then start again, preferably on the same port."""

    def is_running(self, handle: RunHandle) -> bool:
        """Is the process still alive (says nothing about health)?"""

    def healthy(self, handle: RunHandle) -> bool:
        """Does the health endpoint answer 2xx right now?"""

    def logs(self, handle: RunHandle, tail: int = 200) -> list[str]:
        """Last `tail` lines of the company's combined stdout/stderr."""
