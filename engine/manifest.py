"""company.yaml: the contract between the engine and any company.

The engine runs whatever the manifest says and never assumes what is inside a company.
"""

from __future__ import annotations

import string
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from engine.errors import ManifestError

ManifestLoadError = ManifestError

MANIFEST_FILENAME = "company.yaml"
SUPPORTED_LANGUAGES = frozenset({"python", "go"})


class CompanyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    language: str
    run: str
    health_path: str = "/health"
    health_timeout_s: float = Field(default=10.0, gt=0, le=120)
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
        formatter = string.Formatter()
        try:
            fields = {name for _, name, _, _ in formatter.parse(value) if name is not None}
        except ValueError as exc:
            raise ValueError(f"malformed format string in 'run': {exc}") from exc
        if "port" not in fields:
            raise ValueError("run must contain '{port}' so the engine can choose the port")
        unknown = fields - {"port"}
        if unknown:
            raise ValueError(f"unknown placeholder(s) in 'run': {sorted(unknown)}")
        return value

    @field_validator("health_path")
    @classmethod
    def _check_health_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"health_path must start with '/', got {value!r}")
        return value

    @field_validator("source_dirs")
    @classmethod
    def _check_source_dirs(cls, dirs: list[str]) -> list[str]:
        cleaned: list[str] = []
        for d in dirs:
            p = PurePosixPath(d)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"source_dirs entries must be relative, got {d!r}")
            if p.as_posix() in (".", ""):
                raise ValueError("cannot use '.' as a source_dir; specify named subdirectories")
            cleaned.append(p.as_posix())
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("source_dirs contains duplicates")
        return cleaned

    def format_run(self, port: int) -> str:
        return self.run.format(port=port)


def load_manifest(company_dir: Path) -> CompanyManifest:
    if company_dir.is_file() or company_dir.suffix in (".yaml", ".yml"):
        path = company_dir
        if not path.exists():
            raise FileNotFoundError(f"{path.name} not found")
    else:
        path = company_dir / MANIFEST_FILENAME

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestLoadError(f"{MANIFEST_FILENAME} not found in {company_dir.name}") from exc
    except yaml.YAMLError as exc:
        raise ManifestLoadError(f"Invalid YAML in {path.name}: {exc}") from exc
    except OSError as exc:
        raise ManifestLoadError(f"cannot read {path.name}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestLoadError(f"{path.name} must contain a mapping at the top level")

    try:
        return CompanyManifest.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'manifest'}: {e['msg']}"
            for e in exc.errors()
        )
        raise ManifestLoadError(f"validation failed: {problems}") from exc
