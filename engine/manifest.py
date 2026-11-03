"""company.yaml: the contract between the engine and any company.

The engine runs whatever the manifest says and never assumes what is inside a company.
"""

from __future__ import annotations

import string
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from engine.errors import ManifestError

MANIFEST_FILENAME = "company.yaml"
SUPPORTED_LANGUAGES = frozenset({"python"})


class CompanyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    language: str
    run: str
    health_path: str = "/health"
    health_timeout_s: float = Field(default=15.0, gt=0, le=120)
    source_dirs: list[str] = Field(min_length=1)

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        if value not in SUPPORTED_LANGUAGES:
            supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
            raise ValueError(f"unsupported language {value!r} (supported: {supported})")
        return value

    @field_validator("run")
    @classmethod
    def _check_run(cls, value: str) -> str:
        try:
            names = [n for _, n, _, _ in string.Formatter().parse(value) if n is not None]
        except ValueError as exc:
            raise ValueError(f"malformed braces in run command: {exc}") from exc
        if not names:
            raise ValueError("run command must contain a {port} placeholder")
        if any(n != "port" for n in names):
            raise ValueError("run command may only use the {port} placeholder")
        return value

    @field_validator("health_path")
    @classmethod
    def _check_health_path(cls, value: str) -> str:
        if not value.startswith("/") or any(c in value for c in " \t\r\n?#"):
            raise ValueError("health_path must start with '/' and contain no spaces, '?' or '#'")
        return value

    @field_validator("source_dirs")
    @classmethod
    def _check_source_dirs(cls, value: list[str]) -> list[str]:
        for entry in value:
            path = PurePosixPath(entry)
            if not entry.strip() or path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    f"source_dirs entry {entry!r} must be a relative path inside the company"
                )
        return value

    def format_run(self, port: int) -> str:
        return self.run.format(port=port)


def load_manifest(company_dir: Path) -> CompanyManifest:
    path = company_dir / MANIFEST_FILENAME
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"{MANIFEST_FILENAME} not found in {company_dir.name}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"cannot read {MANIFEST_FILENAME}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{MANIFEST_FILENAME} must contain a mapping at the top level")
    try:
        return CompanyManifest.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'manifest'}: {e['msg']}" for e in exc.errors()
        )
        raise ManifestError(f"invalid {MANIFEST_FILENAME}: {problems}") from exc
