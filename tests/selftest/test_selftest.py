from __future__ import annotations

from dataclasses import replace

from engine.config import REPO_ROOT
from engine.selftest.runner import run_flaw_selftest

FLAW_DIR = REPO_ROOT / "content/flaws/brute_force_login"


def test_brute_force_login_selftest(settings) -> None:
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )
    result = run_flaw_selftest(FLAW_DIR, settings=real)
    assert result.passed is True
    assert result.error_message is None
    assert result.baseline_verdict is not None
    assert result.baseline_verdict.status == "vulnerable"
    assert result.ref_fix_verdict is not None
    assert result.ref_fix_verdict.status == "fixed"
    assert result.broken_fix_verdict is not None
    assert result.broken_fix_verdict.status == "broke_business"
