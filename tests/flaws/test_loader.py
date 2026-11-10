from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from engine.config import REPO_ROOT
from engine.errors import FlawError
from engine.flaws.loader import load_all_flaws, load_flaw
from engine.manifest import load_manifest

STARTER_DIR = REPO_ROOT / "content/companies/starter"
STARTER_MANIFEST = load_manifest(STARTER_DIR)


@pytest.fixture
def valid_flaw_dir(tmp_path: Path) -> Path:
    flaw_dir = tmp_path / "test_flaw"
    flaw_dir.mkdir()

    # attack script
    attack_script = flaw_dir / "attack.py"
    attack_script.write_text("print('attack')")

    # reference fix
    ref_fix = flaw_dir / "reference_fix.py"
    ref_fix.write_text(
        "def check_login_rate(ip: str, username: str) -> bool:\n    return True\n"
    )

    flaw_data = {
        "id": "test_flaw",
        "title": "Test Flaw",
        "category": "authentication",
        "endpoint": "login",
        "stage": "rate_limit",
        "attack": {
            "command": ["attack.py"],
            "budget": {
                "max_requests": 100,
                "max_duration_s": 10.0,
                "max_concurrency": 2,
            },
        },
        "contract": [
            {
                "method": "POST",
                "path": "/login",
                "json": {"username": "alice", "password": "{alice_password}"},
                "status": 200,
                "keys": ["token"],
                "save": {"token": "token"},
            }
        ],
        "reference_fix": "reference_fix.py",
        "symptoms": "High login failure rate",
        "hints": ["Check IP tracking", "Limit failed attempts"],
        "lesson": "Always rate limit authentication endpoints.",
    }

    flaw_yaml = flaw_dir / "flaw.yaml"
    flaw_yaml.write_text(yaml.dump(flaw_data))
    return flaw_dir


def test_load_valid_flaw(valid_flaw_dir: Path) -> None:
    flaw = load_flaw(valid_flaw_dir, STARTER_MANIFEST, STARTER_DIR)
    assert flaw.id == "test_flaw"
    assert flaw.endpoint == "login"
    assert flaw.stage == "rate_limit"
    assert len(flaw.contract) == 1
    assert flaw.contract[0].expect_status == 200


def test_missing_flaw_yaml(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(FlawError, match=r"flaw\.yaml not found"):
        load_flaw(empty_dir, STARTER_MANIFEST, STARTER_DIR)


def test_invalid_extra_field_in_flaw(valid_flaw_dir: Path) -> None:
    yaml_path = valid_flaw_dir / "flaw.yaml"
    data = yaml.safe_load(yaml_path.read_text())
    data["extra_unwanted"] = "boom"
    yaml_path.write_text(yaml.dump(data))

    with pytest.raises(FlawError, match=r"invalid flaw\.yaml"):
        load_flaw(valid_flaw_dir, STARTER_MANIFEST, STARTER_DIR)


def test_missing_reference_fix_file(valid_flaw_dir: Path) -> None:
    (valid_flaw_dir / "reference_fix.py").unlink()
    with pytest.raises(FlawError, match="reference fix file not found"):
        load_flaw(valid_flaw_dir, STARTER_MANIFEST, STARTER_DIR)


def test_missing_attack_script(valid_flaw_dir: Path) -> None:
    (valid_flaw_dir / "attack.py").unlink()
    with pytest.raises(FlawError, match="attack script not found"):
        load_flaw(valid_flaw_dir, STARTER_MANIFEST, STARTER_DIR)


def test_endpoint_not_in_routes(valid_flaw_dir: Path) -> None:
    yaml_path = valid_flaw_dir / "flaw.yaml"
    data = yaml.safe_load(yaml_path.read_text())
    data["endpoint"] = "unknown_endpoint"
    yaml_path.write_text(yaml.dump(data))

    with pytest.raises(FlawError, match="not found in company routes"):
        load_flaw(valid_flaw_dir, STARTER_MANIFEST, STARTER_DIR)


def test_stage_not_in_company(valid_flaw_dir: Path) -> None:
    yaml_path = valid_flaw_dir / "flaw.yaml"
    data = yaml.safe_load(yaml_path.read_text())
    data["stage"] = "unknown_stage"
    yaml_path.write_text(yaml.dump(data))

    with pytest.raises(FlawError, match="not found in company source"):
        load_flaw(valid_flaw_dir, STARTER_MANIFEST, STARTER_DIR)


def test_reference_fix_wrong_symbol(valid_flaw_dir: Path) -> None:
    ref_fix = valid_flaw_dir / "reference_fix.py"
    # Stage rate_limit requires symbol check_login_rate, not wrong_function
    ref_fix.write_text("def wrong_function(): pass\n")

    with pytest.raises(FlawError, match="does not define expected function"):
        load_flaw(valid_flaw_dir, STARTER_MANIFEST, STARTER_DIR)


def test_load_all_flaws(tmp_path: Path, valid_flaw_dir: Path) -> None:
    flaws_parent = tmp_path
    flaws = load_all_flaws(flaws_parent, STARTER_MANIFEST, STARTER_DIR)
    assert len(flaws) == 1
    assert "test_flaw" in flaws
