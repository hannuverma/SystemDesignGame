from __future__ import annotations

import os
import subprocess
from pathlib import Path

from engine.errors import GitError

_IDENTITY = {
    "GIT_AUTHOR_NAME": "sysdesign-game",
    "GIT_AUTHOR_EMAIL": "game@localhost",
    "GIT_COMMITTER_NAME": "sysdesign-game",
    "GIT_COMMITTER_EMAIL": "game@localhost",
}


class Git:
    def __init__(self, repo: Path, timeout_s: float = 10.0) -> None:
        self._repo = repo
        self._timeout = timeout_s
        self._env = {
            **os.environ,
            **_IDENTITY,
            "GIT_CONFIG_GLOBAL": os.devnull,  # ignore the user's own git config and hooks
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def run(self, *args: str) -> str:
        cmd = ["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args]
        try:
            proc = subprocess.run(
                cmd,
                cwd=self._repo,
                env=self._env,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )

        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out after {self._timeout}s") from exc
        except OSError as exc:
            raise GitError(f"cannot run git: {exc}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise GitError(f"git {' '.join(args)} failed: {detail}")
        return proc.stdout.strip()

    def init(self) -> None:
        self.run("init", "-q")

    def head(self) -> str:
        return self.run("rev-parse", "head")

    def commit_all(self, message: str) -> str:
        self.run("add", "-A")

        if self.run("status", "--porcelain"):
            self.run("commit", "-q", "-m", message)
        return self.head()

    def tag(self, name: str) -> None:
        return self.run("tag", "-f", name)

    def reset_hard(self, ref: str) -> None:
        self.run("reset", "-q", "--hard", ref)

    def log(self, limit: int = 50) -> list[dict[str, str]]:
        out = self.run("log", f"-{int(limit)}", "--format=%H%x1f%s%x1f%cI")
        entries = []
        for line in out.splitlines():
            sha, message, date = line.split("\x1f")
            entries.append({"commit": sha, "message": message, "date": date})
        return entries
