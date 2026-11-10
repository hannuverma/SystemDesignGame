from __future__ import annotations

import time
from dataclasses import replace

from engine.api.app import create_app
from engine.config import REPO_ROOT
from engine.flaws.loader import load_flaw
from engine.flaws.models import ContractStepSpec
from engine.manifest import load_manifest
from engine.verify.contract import (
    _substitute_variables,
    run_contract,
)
from fastapi.testclient import TestClient

from tests.conftest import pid_alive

FLAW_DIR = REPO_ROOT / "content/flaws/brute_force_login"
STARTER_DIR = REPO_ROOT / "content/companies/starter"


def test_variable_substitution() -> None:
    context = {
        "user": "alice",
        "pwd": "password123",
        "count": 42,
    }

    # String interpolation
    sub, missing = _substitute_variables("/users/{user}/login", context)
    assert sub == "/users/alice/login"
    assert missing == []

    # Whole-string type preservation
    sub_count, missing = _substitute_variables("{count}", context)
    assert sub_count == 42
    assert missing == []

    # Dict & list interpolation
    data = {
        "name": "{user}",
        "pass": "{pwd}",
        "tags": ["{user}", "fixed"],
    }
    sub_data, missing = _substitute_variables(data, context)
    assert sub_data == {
        "name": "alice",
        "pass": "password123",
        "tags": ["alice", "fixed"],
    }
    assert missing == []

    # Missing variables
    _, missing = _substitute_variables("/items/{item_id}", context)
    assert missing == ["item_id"]


def test_contract_against_live_starter(settings) -> None:
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services
        gw_url = services.gateway.url
        probe_token = services.gateway.probe_token

        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        assert created["healthy"]

        target_url = f"{gw_url}/{sid}"

        manifest = load_manifest(STARTER_DIR)
        flaw = load_flaw(FLAW_DIR, manifest, STARTER_DIR)

        # 1. Run legitimate contract
        result = run_contract(
            steps=flaw.contract,
            target_url=target_url,
            probe_token=probe_token,
            fixtures=manifest.fixtures,
        )

        assert result.passed is True
        assert result.failure_message is None
        assert len(result.step_results) == 1
        assert result.step_results[0].passed is True
        assert "alice_token" in result.step_results[0].saved_values

        # 2. Verify probe requests are excluded from stats
        stats = services.stats.snapshot(sid, window_s=10)
        login_stats = stats.endpoints.get("login")
        # No bot attack ran, and probe was excluded: login count should be 0!
        assert login_stats is None or login_stats.count == 0

        # 3. Test failure: wrong status code
        failing_step = ContractStepSpec(
            method="POST",
            path="/login",
            json={"username": "alice", "password": "wrong_password"},
            status=200,
        )
        fail_result = run_contract(
            steps=[failing_step],
            target_url=target_url,
            probe_token=probe_token,
        )
        assert fail_result.passed is False
        assert fail_result.step_results[0].actual_status == 401
        assert "expected status 200, got 401" in (fail_result.failure_message or "")

        # 4. Test failure: missing key in response
        missing_key_step = ContractStepSpec(
            method="POST",
            path="/login",
            json={"username": "alice", "password": "{alice_password}"},
            status=200,
            keys=["non_existent_key"],
        )
        fail_key_res = run_contract(
            steps=[missing_key_step],
            target_url=target_url,
            probe_token=probe_token,
            fixtures=manifest.fixtures,
        )
        assert fail_key_res.passed is False
        assert "missing expected key(s) ['non_existent_key']" in (
            fail_key_res.failure_message or ""
        )

        # 5. Test failure: unreachable host / connection error
        conn_fail_res = run_contract(
            steps=flaw.contract,
            target_url="http://127.0.0.1:1",  # closed port
            fixtures=manifest.fixtures,
            timeout_s=0.5,
        )
        assert conn_fail_res.passed is False
        assert "connection error" in (conn_fail_res.failure_message or "")

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid)

