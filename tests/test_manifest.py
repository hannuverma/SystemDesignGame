"""Tests for engine.manifest — CompanyManifest model and loader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from engine.manifest import CompanyManifest, ManifestLoadError, load_manifest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_YAML = dedent("""\
    name: starter
    language: python
    run: "uvicorn app.main:app --host 127.0.0.1 --port {port}"
    health_path: /health
    health_timeout_s: 10
    source_dirs: [app]
""")


@pytest.fixture()
def manifest_file(tmp_path: Path) -> Path:
    """Write a valid company.yaml and return its path."""
    p = tmp_path / "company.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_valid_manifest(manifest_file: Path) -> None:
    m = load_manifest(manifest_file)
    assert m.name == "starter"
    assert m.language == "python"
    assert "{port}" in m.run
    assert m.health_path == "/health"
    assert m.health_timeout_s == 10
    assert m.source_dirs == ["app"]


def test_defaults_applied() -> None:
    """health_path and health_timeout_s have defaults."""
    m = CompanyManifest(
        name="minimal",
        language="go",
        run="./server --port {port}",
        source_dirs=["src"],
    )
    assert m.health_path == "/health"
    assert m.health_timeout_s == 10


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_field",
    ["name", "language", "run", "source_dirs"],
)
def test_missing_required_field(tmp_path: Path, missing_field: str) -> None:
    """Each required field must be present; omitting one raises."""
    import yaml

    data = yaml.safe_load(VALID_YAML)
    del data[missing_field]
    p = tmp_path / "company.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ManifestLoadError, match="validation failed"):
        load_manifest(p)


# ---------------------------------------------------------------------------
# Validation: run must contain {port}
# ---------------------------------------------------------------------------


def test_run_missing_port_placeholder(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("{port}", "8000")
    p = tmp_path / "company.yaml"
    p.write_text(bad, encoding="utf-8")

    with pytest.raises(ManifestLoadError, match="port"):
        load_manifest(p)


def test_model_rejects_run_without_port() -> None:
    with pytest.raises(ValidationError, match="port"):
        CompanyManifest(
            name="x",
            language="python",
            run="uvicorn app:app --port 8000",
            source_dirs=["app"],
        )


# ---------------------------------------------------------------------------
# Validation: health_path must start with /
# ---------------------------------------------------------------------------


def test_health_path_without_leading_slash(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("/health", "health")
    p = tmp_path / "company.yaml"
    p.write_text(bad, encoding="utf-8")

    with pytest.raises(ManifestLoadError, match="health_path"):
        load_manifest(p)


def test_model_rejects_bad_health_path() -> None:
    with pytest.raises(ValidationError, match="health_path"):
        CompanyManifest(
            name="x",
            language="python",
            run="./run --port {port}",
            health_path="no-slash",
            source_dirs=["src"],
        )


# ---------------------------------------------------------------------------
# Validation: source_dirs must be non-empty
# ---------------------------------------------------------------------------


def test_empty_source_dirs(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("[app]", "[]")
    p = tmp_path / "company.yaml"
    p.write_text(bad, encoding="utf-8")

    with pytest.raises(ManifestLoadError, match="validation failed"):
        load_manifest(p)


# ---------------------------------------------------------------------------
# File-level errors
# ---------------------------------------------------------------------------


def test_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        load_manifest(tmp_path / "nope.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "company.yaml"
    p.write_text("{{not valid yaml: [", encoding="utf-8")

    with pytest.raises(ManifestLoadError, match="Invalid YAML"):
        load_manifest(p)


def test_yaml_non_mapping(tmp_path: Path) -> None:
    """A YAML file that parses to a list instead of a dict."""
    p = tmp_path / "company.yaml"
    p.write_text("- item1\n- item2\n", encoding="utf-8")

    with pytest.raises(ManifestLoadError, match="mapping"):
        load_manifest(p)
