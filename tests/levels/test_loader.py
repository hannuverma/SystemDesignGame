from __future__ import annotations

import pytest
import yaml
from engine.config import REPO_ROOT
from engine.errors import LevelError
from engine.flaws.loader import load_all_flaws
from engine.levels.loader import load_level
from engine.manifest import load_manifest

COMPANIES_DIR = REPO_ROOT / "content/companies"
STARTER_DIR = COMPANIES_DIR / "starter"
FLAWS_DIR = REPO_ROOT / "content/flaws"
TUTORIAL_PATH = REPO_ROOT / "content/levels/tutorial.yaml"


def test_load_tutorial_level() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaws = load_all_flaws(FLAWS_DIR, manifest, STARTER_DIR)
    level = load_level(TUTORIAL_PATH, flaws=flaws, companies_dir=COMPANIES_DIR)

    assert level.id == "tutorial"
    assert level.company == "starter"
    assert level.difficulty == "guided"
    assert level.fail_mode == "warn"
    assert len(level.objectives) == 3
    assert level.objectives[0].id == "obj_rate_limit"
    assert level.objectives[1].requires == ["obj_rate_limit"]
    assert level.objectives[2].requires == ["obj_sql_injection"]


def test_reject_missing_company(tmp_path) -> None:
    data = {
        "id": "lvl_test",
        "company": "non_existent_company",
        "objectives": [{"id": "o1", "flaw": "brute_force_login", "title": "Obj 1"}],
    }
    p = tmp_path / "lvl.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(LevelError, match="does not exist"):
        load_level(p, companies_dir=COMPANIES_DIR)


def test_reject_missing_flaw(tmp_path) -> None:
    data = {
        "id": "lvl_test",
        "company": "starter",
        "objectives": [{"id": "o1", "flaw": "unknown_flaw_xyz", "title": "Obj 1"}],
    }
    p = tmp_path / "lvl.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(LevelError, match="unknown flaw"):
        load_level(p, flaws={}, companies_dir=COMPANIES_DIR)


def test_reject_duplicate_objective_id(tmp_path) -> None:
    data = {
        "id": "lvl_test",
        "company": "starter",
        "objectives": [
            {"id": "o1", "flaw": "brute_force_login", "title": "Obj 1"},
            {"id": "o1", "flaw": "brute_force_login", "title": "Obj 1 Duplicate"},
        ],
    }
    p = tmp_path / "lvl.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(LevelError, match="duplicate objective id"):
        load_level(p)


def test_reject_unknown_requires(tmp_path) -> None:
    data = {
        "id": "lvl_test",
        "company": "starter",
        "objectives": [
            {
                "id": "o1",
                "flaw": "brute_force_login",
                "title": "Obj 1",
                "requires": ["non_existent_obj"],
            }
        ],
    }
    p = tmp_path / "lvl.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(LevelError, match="unknown objective"):
        load_level(p)


def test_reject_self_requires(tmp_path) -> None:
    data = {
        "id": "lvl_test",
        "company": "starter",
        "objectives": [
            {
                "id": "o1",
                "flaw": "brute_force_login",
                "title": "Obj 1",
                "requires": ["o1"],
            }
        ],
    }
    p = tmp_path / "lvl.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(LevelError, match="cannot require itself"):
        load_level(p)


def test_reject_cycle_in_requires(tmp_path) -> None:
    data = {
        "id": "lvl_test",
        "company": "starter",
        "objectives": [
            {
                "id": "o1",
                "flaw": "brute_force_login",
                "title": "Obj 1",
                "requires": ["o2"],
            },
            {
                "id": "o2",
                "flaw": "brute_force_login",
                "title": "Obj 2",
                "requires": ["o3"],
            },
            {
                "id": "o3",
                "flaw": "brute_force_login",
                "title": "Obj 3",
                "requires": ["o1"],
            },
        ],
    }
    p = tmp_path / "lvl.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(LevelError, match="cycle detected"):
        load_level(p)
