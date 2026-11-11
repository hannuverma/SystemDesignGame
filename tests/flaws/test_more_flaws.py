from __future__ import annotations

from engine.config import REPO_ROOT
from engine.flaws.loader import load_flaw
from engine.manifest import load_manifest

STARTER_DIR = REPO_ROOT / "content/companies/starter"
SQLI_DIR = REPO_ROOT / "content/flaws/sql_injection_search"
IDOR_DIR = REPO_ROOT / "content/flaws/missing_ownership_delete"


def test_load_sql_injection_flaw() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaw = load_flaw(SQLI_DIR, manifest, STARTER_DIR)
    assert flaw.id == "sql_injection_search"
    assert flaw.endpoint == "search"
    assert flaw.stage == "db_query"
    assert len(flaw.contract) == 1


def test_load_missing_ownership_flaw() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaw = load_flaw(IDOR_DIR, manifest, STARTER_DIR)
    assert flaw.id == "missing_ownership_delete"
    assert flaw.endpoint == "delete_item"
    assert flaw.stage == "ownership_check"
    assert len(flaw.contract) == 2
