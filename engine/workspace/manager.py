"""Sessions: a private, git-tracked copy of a company per game, plus stage read/write."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path

from engine.config import Settings
from engine.errors import CompanyNotFound, SessionNotFound, StageNotFound
from engine.manifest import load_manifest
from engine.workspace.git import Git
from engine.workspace.models import Session, Stage
from engine.workspace.paths import atomic_write_bytes, resolve_inside
from engine.workspace.stages import LANGUAGE_GLOBS, apply_stage_edit, scan_text

SESSION_ID_RE = re.compile(r"^[0-9a-f]{12}$")
COMPANY_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ORIGINAL_TAG = "original"
DEFAULT_GITIGNORE = "__pycache__/\n*.pyc\n"


def _robust_rmtree(path: Path) -> None:
    """Remove a directory tree, handling Windows file-locking issues."""

    def _onerror(func, fpath, _exc_info):
        # On Windows, git index files can be read-only; chmod and retry
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except OSError:
            pass

    for attempt in range(3):
        shutil.rmtree(path, onerror=_onerror)
        if not path.exists():
            return
        time.sleep(0.1 * (attempt + 1))
    # Final attempt: ignore any remaining errors
    shutil.rmtree(path, ignore_errors=True)


class WorkspaceManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------- sessions

    def lock(self, session_id: str) -> threading.RLock:
        """One re-entrant lock per session: two requests never mutate a workspace at once."""
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.RLock())

    def create_session(self, company: str = "starter") -> Session:
        if not COMPANY_NAME_RE.match(company):
            raise CompanyNotFound(f"invalid company name: {company!r}")
        template = resolve_inside(self._settings.content_dir / "companies", company)
        if not template.is_dir():
            raise CompanyNotFound(f"no such company: {company!r}")
        load_manifest(template)  # fail early if the template itself is broken

        session_id = uuid.uuid4().hex[:12]
        root = self._settings.runs_dir / session_id
        company_dir = root / "company"
        try:
            root.mkdir(parents=True)
            shutil.copytree(
                template,
                company_dir,
                ignore=shutil.ignore_patterns("__pycache__", ".git"),
            )
            gitignore = company_dir / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
            git = self._git(company_dir)
            git.init()
            git.commit_all("initial")
            git.tag(ORIGINAL_TAG)
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Session:
        if not SESSION_ID_RE.match(session_id):
            raise SessionNotFound(f"no such session: {session_id!r}")
        root = (self._settings.runs_dir / session_id).resolve()
        company_dir = root / "company"
        if not company_dir.is_dir():
            raise SessionNotFound(f"no such session: {session_id!r}")
        secret_path = root / ".secret"
        if secret_path.is_file():
            secret = secret_path.read_text(encoding="utf-8").strip()
        else:
            secret = secrets.token_hex(16)
            secret_path.write_text(secret, encoding="utf-8")
        return Session(
            id=session_id,
            root=root,
            company_dir=company_dir,
            log_path=root / "logs" / "company.log",
            manifest=load_manifest(company_dir),
            secret=secret,
        )

    def delete_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        with self.lock(session_id):
            _robust_rmtree(session.root)
        with self._locks_guard:
            self._locks.pop(session_id, None)

    # --------------------------------------------------------------- stages

    def list_stages(self, session: Session) -> list[Stage]:
        pattern = LANGUAGE_GLOBS[session.manifest.language]
        company_root = session.company_dir.resolve()
        stages: list[Stage] = []
        seen: dict[tuple[str, str], str] = {}
        for source_dir in session.manifest.source_dirs:
            base = resolve_inside(session.company_dir, source_dir)
            if not base.is_dir():
                continue
            for path in sorted(base.rglob(pattern)):
                if path.is_symlink() or "__pycache__" in path.parts:
                    continue
                relative = path.relative_to(company_root).as_posix()
                for stage in scan_text(path.read_bytes().decode("utf-8"), relative):
                    if stage.key in seen:
                        raise StageNotFound(
                            f"stage {stage.name!r} for endpoint {stage.endpoint!r} is defined in "
                            f"both {seen[stage.key]} and {relative}"
                        )
                    seen[stage.key] = relative
                    stages.append(stage)
        return sorted(stages, key=lambda s: (s.endpoint, s.order, s.name))

    def get_stage(self, session: Session, endpoint: str, name: str) -> Stage:
        for stage in self.list_stages(session):
            if stage.key == (endpoint, name):
                return stage
        raise StageNotFound(f"no stage {name!r} for endpoint {endpoint!r}")

    def write_stage(
        self, session: Session, endpoint: str, name: str, new_code: str
    ) -> Stage:
        """Validate and apply the edit in memory, then write the file atomically."""
        stage = self.get_stage(session, endpoint, name)
        path = resolve_inside(session.company_dir, stage.file)
        text = path.read_bytes().decode("utf-8")
        new_text = apply_stage_edit(
            text,
            stage.file,
            endpoint,
            name,
            new_code,
            max_bytes=self._settings.max_stage_bytes,
        )
        atomic_write_bytes(path, new_text.encode("utf-8"))
        return self.get_stage(session, endpoint, name)

    # ------------------------------------------------------------------ git

    def commit(self, session: Session, message: str) -> str:
        return self._git(session.company_dir).commit_all(message)

    def head(self, session: Session) -> str:
        return self._git(session.company_dir).head()

    def history(self, session: Session, limit: int = 50) -> list[dict[str, str]]:
        return self._git(session.company_dir).log(limit)

    def reset_to(self, session: Session, ref: str) -> None:
        self._git(session.company_dir).reset_hard(ref)

    def reset_to_original(self, session: Session) -> None:
        self.reset_to(session, ORIGINAL_TAG)

    def _git(self, company_dir: Path) -> Git:
        return Git(company_dir, self._settings.git_timeout_s)
