from __future__ import annotations

import pytest
from engine.config import REPO_ROOT
from engine.errors import (
    CompanyNotFound,
    InvalidStageCode,
    SessionNotFound,
    StageNotFound,
)

NEW = 'def build_greeting(name: str) -> dict:\n    return {"message": f"Yo {name}"}'


def test_create_session_copies_template_and_tracks_it_in_git(workspace):
    session = workspace.create_session()
    assert (session.company_dir / "company.yaml").exists()
    assert (session.company_dir / ".git").is_dir()
    assert [h["message"] for h in workspace.history(session)] == ["initial"]
    assert (
        session.log_path.parent == session.root / "logs"
    )  # logs live outside the repo


def test_sessions_are_isolated_from_each_other_and_from_the_template(workspace):
    a, b = workspace.create_session(), workspace.create_session()
    assert a.id != b.id
    workspace.write_stage(a, "hello", "greeting", NEW)
    assert "Yo" in (a.company_dir / "app/greeting.py").read_text()
    assert "Yo" not in (b.company_dir / "app/greeting.py").read_text()
    template = (REPO_ROOT / "content/companies/starter/app/greeting.py").read_text()
    assert "Yo" not in template


def test_list_and_get_stage(workspace):
    session = workspace.create_session()
    stages = workspace.list_stages(session)
    assert [(s.endpoint, s.name, s.file) for s in stages] == [
        ("hello", "greeting", "app/greeting.py")
    ]
    assert workspace.get_stage(session, "hello", "greeting").symbol == "build_greeting"
    with pytest.raises(StageNotFound):
        workspace.get_stage(session, "hello", "missing")


def test_write_commit_history_and_reset_to_original(workspace):
    session = workspace.create_session()
    original = (session.company_dir / "app/greeting.py").read_text()
    workspace.write_stage(session, "hello", "greeting", NEW)
    sha = workspace.commit(session, "deploy: greeting (hello)")
    assert workspace.head(session) == sha
    assert [h["message"] for h in workspace.history(session)] == [
        "deploy: greeting (hello)",
        "initial",
    ]
    workspace.reset_to_original(session)
    assert (session.company_dir / "app/greeting.py").read_text() == original


def test_commit_with_no_changes_returns_head(workspace):
    session = workspace.create_session()
    head = workspace.head(session)
    assert workspace.commit(session, "noop") == head
    assert len(workspace.history(session)) == 1


def test_pycache_is_not_committed(workspace):
    session = workspace.create_session()
    cache = session.company_dir / "app/__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"junk")
    head = workspace.head(session)
    assert workspace.commit(session, "x") == head


def test_failed_write_leaves_file_untouched(workspace):
    session = workspace.create_session()
    path = session.company_dir / "app/greeting.py"
    before = path.read_bytes()
    with pytest.raises(InvalidStageCode):
        workspace.write_stage(session, "hello", "greeting", "def nope():\n    pass")
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", ["", "../x", "ABC", "a" * 40, "zzzzzzzzzzzz", "x/y"])
def test_bad_session_ids(workspace, bad):
    with pytest.raises(SessionNotFound):
        workspace.get_session(bad)


@pytest.mark.parametrize("bad", ["nope", "../etc", "/abs", "Starter", ""])
def test_bad_company_names(workspace, bad):
    with pytest.raises(CompanyNotFound):
        workspace.create_session(bad)


def test_delete_session(workspace):
    session = workspace.create_session()
    workspace.delete_session(session.id)
    assert not session.root.exists()
    with pytest.raises(SessionNotFound):
        workspace.get_session(session.id)


def test_failed_creation_leaves_nothing_behind(workspace, settings, monkeypatch):
    def boom(self):
        raise RuntimeError("git exploded")

    monkeypatch.setattr("engine.workspace.git.Git.init", boom)
    with pytest.raises(RuntimeError):
        workspace.create_session()
    assert list(settings.runs_dir.iterdir()) == []
