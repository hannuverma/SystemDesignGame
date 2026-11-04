from __future__ import annotations

from pathlib import Path

import pytest

from engine.errors import ManifestError
from engine.manifest import MANIFEST_FILENAME, load_manifest

VALID = """\
name: demo
language: python
run: "python -m uvicorn app.main:app --port {port}"
health_path: /health
health_timeout_s: 5
source_dirs: [app]
"""


def write(tmp_path: Path, text: str) -> Path:
    (tmp_path / MANIFEST_FILENAME).write_text(text)
    return tmp_path


def test_valid_manifest_loads(tmp_path):
    manifest = load_manifest(write(tmp_path, VALID))
    assert manifest.name == "demo"
    assert manifest.format_run(8123).endswith("--port 8123")


def test_starter_manifest_is_valid():
    from engine.config import REPO_ROOT

    assert load_manifest(REPO_ROOT / "content/companies/starter").language == "python"


def test_missing_file(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(tmp_path)


def test_not_a_mapping(tmp_path):
    with pytest.raises(ManifestError, match="mapping"):
        load_manifest(write(tmp_path, "- a\n- b\n"))


def test_invalid_yaml(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, "name: [unclosed"))


@pytest.mark.parametrize("field", ["name", "language", "run", "source_dirs"])
def test_missing_required_field(tmp_path, field):
    text = "\n".join(line for line in VALID.splitlines() if not line.startswith(field))
    with pytest.raises(ManifestError, match=field):
        load_manifest(write(tmp_path, text))


@pytest.mark.parametrize(
    "run",
    ["python app.py", "python app.py --port {host}", "python app.py {port", "python {}"],
)
def test_bad_run_template(tmp_path, run):
    text = VALID.replace('"python -m uvicorn app.main:app --port {port}"', f'"{run}"')
    with pytest.raises(ManifestError, match="run"):
        load_manifest(write(tmp_path, text))


@pytest.mark.parametrize("path", ["health", "/he alth", "/health?x=1", ""])
def test_bad_health_path(tmp_path, path):
    with pytest.raises(ManifestError, match="health_path"):
        load_manifest(write(tmp_path, VALID.replace("/health", f'"{path}"', 1)))


@pytest.mark.parametrize("entry", ["/abs", "../up", "a/../../b"])
def test_bad_source_dirs(tmp_path, entry):
    with pytest.raises(ManifestError, match="source_dirs"):
        load_manifest(write(tmp_path, VALID.replace("[app]", f'["{entry}"]')))


def test_unsupported_language(tmp_path):
    with pytest.raises(ManifestError, match="unsupported language"):
        load_manifest(write(tmp_path, VALID.replace("python\n", "cobol\n", 1)))


def test_unknown_field_rejected(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, VALID + "surprise: 1\n"))


@pytest.mark.parametrize("timeout", ["0", "-1", "500"])
def test_timeout_bounds(tmp_path, timeout):
    with pytest.raises(ManifestError, match="health_timeout_s"):
        load_manifest(
            write(tmp_path, VALID.replace("health_timeout_s: 5", f"health_timeout_s: {timeout}"))
        )
