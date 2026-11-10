from __future__ import annotations

import time
from dataclasses import replace

from engine.api.app import create_app
from engine.bots.models import BotSpec
from engine.bots.runner import run_bot
from engine.config import REPO_ROOT
from engine.flaws.loader import load_flaw
from engine.flaws.models import BudgetConfig
from engine.manifest import load_manifest
from fastapi.testclient import TestClient

from tests.conftest import pid_alive

FLAW_DIR = REPO_ROOT / "content/flaws/brute_force_login"
STARTER_DIR = REPO_ROOT / "content/companies/starter"


def test_load_brute_force_login_flaw() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaw = load_flaw(FLAW_DIR, manifest, STARTER_DIR)
    assert flaw.id == "brute_force_login"
    assert flaw.endpoint == "login"
    assert flaw.stage == "rate_limit"
    assert len(flaw.contract) == 1


def test_brute_force_attack_against_live_starter(settings) -> None:
    """Test running brute_force_login attack against a real starter session."""
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services
        gw_url = services.gateway.url

        # 1. Create a session
        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        assert created["healthy"]

        target_url = f"{gw_url}/{sid}"

        # 2. Run bot attack
        spec = BotSpec(
            id="brute_force_login",
            command=[str(FLAW_DIR / "attack.py")],
            target_url=target_url,
            budget=BudgetConfig(max_requests=50, max_duration_s=10.0),
            seed=42,
            cwd=FLAW_DIR,
        )

        result = run_bot(spec, bus=services.bus, session_id=sid)

        assert result.status == "completed"
        assert result.report is not None
        assert result.report.succeeded is True
        assert result.report.impact.accounts_compromised == 1
        assert result.report.usage.requests_sent >= 1

        # 3. Verify stats show the flood on endpoint 'login'
        stats = services.stats.snapshot(sid, window_s=10)
        login_stats = stats.endpoints.get("login")
        assert login_stats is not None
        assert login_stats.count >= 1
        assert login_stats.s4xx >= 1  # multiple failed attempts (401)
        assert login_stats.s2xx >= 1  # 1 successful login (200)

        # 4. Verify recent requests show bot client header
        recent = services.stats.recent_requests(sid, tail=10)
        assert any(r.client == "bot:brute_force_login" for r in recent)

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid)
