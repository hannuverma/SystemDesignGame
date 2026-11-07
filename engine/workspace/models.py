from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.manifest import CompanyManifest


@dataclass(frozen=True)
class Stage:
    name: str
    endpoint: str
    order: int
    file: str
    symbol: str
    start_line: str
    end_line: str
    code: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.endpoint, self.name)


@dataclass(frozen=True)
class Session:
    id: str
    root: Path  # runs/<id>/
    company_dir: Path  # runs/<id>/company/  (the git repo the player edits)
    log_path: Path  # runs/<id>/logs/company.log  (outside the repo on purpose)
    manifest: CompanyManifest
    secret: str = ""
