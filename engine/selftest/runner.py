from __future__ import annotations

import contextlib
import logging
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from engine.api.deps import Services, build_services
from engine.bots.models import BotSpec
from engine.bots.runner import run_bot
from engine.config import Settings
from engine.flaws.loader import load_flaw
from engine.manifest import load_manifest
from engine.verify.contract import run_contract
from engine.verify.verdict import Verdict, compute_verdict

logger = logging.getLogger(__name__)


class SelftestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    flaw_id: str
    passed: bool
    baseline_verdict: Verdict | None = None
    ref_fix_verdict: Verdict | None = None
    broken_fix_verdict: Verdict | None = None
    error_message: str | None = None


def run_flaw_selftest(
    flaw_dir: Path,
    company_name: str = "starter",
    settings: Settings | None = None,
    services: Services | None = None,
    broken_fix_code: str | None = None,
) -> SelftestResult:
    """Run an end-to-end selftest on a flaw.

    Verifies:
    1. Baseline company yields 'vulnerable'.
    2. Deploying reference fix yields 'fixed'.
    3. Deploying a broken fix yields 'broke_business'.
    """
    settings = settings or Settings()
    company_dir = settings.content_dir / "companies" / company_name
    manifest = load_manifest(company_dir)
    flaw = load_flaw(flaw_dir, manifest, company_dir)

    owns_services = False
    if services is None:
        owns_services = True
        custom_settings = replace(
            settings,
            gateway_port=0,
            max_health_timeout_s=max(settings.max_health_timeout_s, 20.0),
            health_poll_interval_s=0.1,
        )
        services = build_services(custom_settings)
        services.gateway.start()

    session = services.workspace.create_session(company_name)
    try:
        try:
            services.deployer.launch(session)
        except Exception as exc:
            return SelftestResult(
                flaw_id=flaw.id,
                passed=False,
                error_message=f"Initial company launch failed: {exc}",
            )

        target_url = f"{services.gateway.url}/{session.id}"

        # -------------------------------------------------------------
        # 1. Baseline check: expect 'vulnerable'
        # -------------------------------------------------------------
        services.deployer.reset_company_state(session)

        bot_spec = BotSpec(
            id=flaw.id,
            command=flaw.attack.command,
            target_url=target_url,
            budget=flaw.attack.budget,
            cwd=flaw_dir,
        )
        base_bot_res = run_bot(bot_spec, bus=services.bus, session_id=session.id)
        base_contract_res = run_contract(
            steps=flaw.contract,
            target_url=target_url,
            probe_token=services.probe_token,
            fixtures=manifest.fixtures,
        )
        baseline_verdict = compute_verdict(base_bot_res, base_contract_res)

        if baseline_verdict.status != "vulnerable":
            return SelftestResult(
                flaw_id=flaw.id,
                passed=False,
                baseline_verdict=baseline_verdict,
                error_message=(
                    f"Baseline check expected 'vulnerable', got '{baseline_verdict.status}': "
                    f"{'; '.join(baseline_verdict.reasons)}"
                ),
            )

        # -------------------------------------------------------------
        # 2. Reference fix check: expect 'fixed'
        # -------------------------------------------------------------
        ref_fix_path = flaw_dir / flaw.reference_fix
        ref_code = ref_fix_path.read_text(encoding="utf-8")
        dep_ref = services.deployer.deploy_stage(
            session=session,
            endpoint=flaw.endpoint,
            name=flaw.stage,
            new_code=ref_code,
        )
        if dep_ref.status != "deployed":
            return SelftestResult(
                flaw_id=flaw.id,
                passed=False,
                baseline_verdict=baseline_verdict,
                error_message=f"Reference fix deploy failed: {dep_ref.message}",
            )

        services.deployer.reset_company_state(session)
        ref_bot_res = run_bot(bot_spec, bus=services.bus, session_id=session.id)
        ref_contract_res = run_contract(
            steps=flaw.contract,
            target_url=target_url,
            probe_token=services.probe_token,
            fixtures=manifest.fixtures,
        )
        ref_verdict = compute_verdict(ref_bot_res, ref_contract_res)

        if ref_verdict.status != "fixed":
            return SelftestResult(
                flaw_id=flaw.id,
                passed=False,
                baseline_verdict=baseline_verdict,
                ref_fix_verdict=ref_verdict,
                error_message=(
                    f"Reference fix check expected 'fixed', got '{ref_verdict.status}': "
                    f"{'; '.join(ref_verdict.reasons)}"
                ),
            )

        # -------------------------------------------------------------
        # 3. Deliberately broken fix check: expect 'broke_business'
        # -------------------------------------------------------------
        if broken_fix_code is None:
            broken_file = flaw_dir / "broken_fix.py"
            if broken_file.is_file():
                broken_fix_code = broken_file.read_text(encoding="utf-8")
            else:
                stages = services.workspace.list_stages(session)
                matching = [
                    s
                    for s in stages
                    if s.name == flaw.stage and s.endpoint == flaw.endpoint
                ]
                symbol = matching[0].symbol if matching else "handler"
                broken_fix_code = f"def {symbol}(*args, **kwargs):\n    return False\n"

        dep_broken = services.deployer.deploy_stage(
            session=session,
            endpoint=flaw.endpoint,
            name=flaw.stage,
            new_code=broken_fix_code,
        )
        if dep_broken.status != "deployed":
            return SelftestResult(
                flaw_id=flaw.id,
                passed=False,
                baseline_verdict=baseline_verdict,
                ref_fix_verdict=ref_verdict,
                error_message=f"Broken fix deploy failed: {dep_broken.message}",
            )

        services.deployer.reset_company_state(session)
        broken_bot_res = run_bot(bot_spec, bus=services.bus, session_id=session.id)
        broken_contract_res = run_contract(
            steps=flaw.contract,
            target_url=target_url,
            probe_token=services.probe_token,
            fixtures=manifest.fixtures,
        )
        broken_verdict = compute_verdict(broken_bot_res, broken_contract_res)

        if broken_verdict.status != "broke_business":
            return SelftestResult(
                flaw_id=flaw.id,
                passed=False,
                baseline_verdict=baseline_verdict,
                ref_fix_verdict=ref_verdict,
                broken_fix_verdict=broken_verdict,
                error_message=(
                    f"Broken fix check expected 'broke_business', got '{broken_verdict.status}': "
                    f"{'; '.join(broken_verdict.reasons)}"
                ),
            )

        return SelftestResult(
            flaw_id=flaw.id,
            passed=True,
            baseline_verdict=baseline_verdict,
            ref_fix_verdict=ref_verdict,
            broken_fix_verdict=broken_verdict,
        )
    finally:
        with contextlib.suppress(Exception):
            services.deployer.shutdown(session.id)
        if owns_services:
            with contextlib.suppress(Exception):
                services.gateway.stop()
