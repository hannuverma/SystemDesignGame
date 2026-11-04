from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.errors import PathEscape
from engine.workspace.paths import atomic_write_bytes, resolve_inside


def test_inside_ok(tmp_path):
    (tmp_path / "a").mkdir()
    assert resolve_inside(tmp_path, "a/b.py") == tmp_path.resolve() / "a" / "b.py"


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/etc/passwd", "a/../.."])
def test_escapes_rejected(tmp_path, bad):
    with pytest.raises(PathEscape):
        resolve_inside(tmp_path, bad)


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires administrator or Developer Mode on Windows")
    with pytest.raises(PathEscape):
        resolve_inside(root, "link/secret.txt")


def test_atomic_write_replaces_and_keeps_mode(tmp_path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    target.chmod(0o600)
    mode_before = target.stat().st_mode
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    else:
        assert target.stat().st_mode == mode_before
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]  # no temp files left behind
