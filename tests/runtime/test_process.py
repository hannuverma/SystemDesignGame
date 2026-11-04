"""Real subprocess tests. These start actual processes, so they are the slowest tests."""

from __future__ import annotations

import time

import httpx
import pytest

from engine.errors import RuntimeStartError
from engine.manifest import CompanyManifest
from engine.runtime import ProcessRuntime
from engine.runtime.ports import find_free_port, port_is_free
from tests.conftest import pid_alive


@pytest.fixture
def runtime():
    rt = ProcessRuntime(stop_grace_s=1.0)
    yield rt


def wait_until(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def manifest(run: str, health_path: str = "/health") -> CompanyManifest:
    return CompanyManifest(
        name="t", language="python", run=run, health_path=health_path, source_dirs=["app"]
    )


def test_start_serve_restart_stop(runtime, workspace):
    session = workspace.create_session()
    handle = runtime.start(session.company_dir, session.manifest, session.log_path)
    try:
        assert wait_until(lambda: runtime.healthy(handle))
        assert httpx.get(f"{handle.base_url}/hello").json() == {"message": "Hello, world!"}

        again = runtime.restart(handle)
        assert again.port == handle.port  # URL stays stable across restarts
        assert not pid_alive(handle.pid)
        assert wait_until(lambda: runtime.healthy(again))
        handle = again
    finally:
        runtime.stop(handle)
    assert not runtime.is_running(handle) and not runtime.healthy(handle)
    assert not pid_alive(handle.pid)
    assert wait_until(lambda: port_is_free(handle.port), timeout=3)
    assert any("=== start" in line for line in runtime.logs(handle, 500))


def test_stop_is_idempotent(runtime, workspace):
    session = workspace.create_session()
    handle = runtime.start(session.company_dir, session.manifest, session.log_path)
    runtime.stop(handle)
    runtime.stop(handle)


def test_stop_kills_the_whole_process_tree(runtime, tmp_path):
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    handle = runtime.start(tmp_path, manifest("python parent.py --port {port}"), tmp_path / "l.log")
    assert wait_until(lambda: (tmp_path / "child.pid").exists())
    child_pid = int((tmp_path / "child.pid").read_text())
    assert pid_alive(child_pid)
    runtime.stop(handle)
    assert wait_until(lambda: not pid_alive(child_pid), timeout=3), "child process was orphaned"


def test_stubborn_process_is_killed_after_grace(runtime, tmp_path):
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    handle = runtime.start(
        tmp_path, manifest("python stubborn.py --port {port}"), tmp_path / "l.log"
    )
    assert wait_until(lambda: "ready" in "".join(runtime.logs(handle, 50)))
    started = time.monotonic()
    runtime.stop(handle)
    assert time.monotonic() - started < 5
    assert not pid_alive(handle.pid)


def test_process_that_exits_immediately_is_detected(runtime, tmp_path):
    (tmp_path / "die.py").write_text("raise SystemExit('nope')\n")
    handle = runtime.start(tmp_path, manifest("python die.py --port {port}"), tmp_path / "l.log")
    assert wait_until(lambda: not runtime.is_running(handle), timeout=5)
    assert not runtime.healthy(handle)
    assert "nope" in "\n".join(runtime.logs(handle, 20))


def test_missing_executable_raises(runtime, tmp_path):
    with pytest.raises(RuntimeStartError, match="cannot launch"):
        runtime.start(tmp_path, manifest("definitely-not-a-program {port}"), tmp_path / "l.log")


def test_log_tail_is_bounded(runtime, tmp_path):
    (tmp_path / "chatty.py").write_text("for i in range(500):\n    print('line', i, flush=True)\n")
    handle = runtime.start(tmp_path, manifest("python chatty.py {port}"), tmp_path / "l.log")
    assert wait_until(lambda: not runtime.is_running(handle), timeout=5)
    lines = runtime.logs(handle, 10)
    assert len(lines) == 10 and lines[-1] == "line 499"


def test_ports():
    port = find_free_port()
    assert 1024 <= port <= 65535 and port_is_free(port)
