"""The one place that decides whether a path is allowed. Every file operation goes through it."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from engine.errors import PathEscape

def resolve_inside(root: Path, relative: str|Path) -> Path:
    """Resolve `relative` under `root`, rejecting absolute paths, '..' and escaping symlinks."""
    rel = Path(relative)
    if rel.is_absolute():
        raise PathEscape(f"absolute paths are not allowed: {relative}")
    root_resolved = root.resolve()
    candidate = (root_resolved / rel).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise PathEscape(f"path escapes its root: {relative}")
    return candidate

def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via temp file + os.replace so a crash never leaves a half-written file."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
