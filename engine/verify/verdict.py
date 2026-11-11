from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.bots.models import BotReport, BotResult
from engine.verify.contract import ContractResult

VerdictStatus = Literal["vulnerable", "fixed", "broke_business", "inconclusive"]


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: VerdictStatus
    attack_report: BotReport | None = None
    contract_result: ContractResult | None = None
    reasons: list[str] = Field(default_factory=list)


def compute_verdict(
    bot_result: BotResult,
    contract_result: ContractResult | None = None,
    min_requests: int = 5,
) -> Verdict:
    """Compute verdict based on bot execution result and contract result.

    Rules:
    - If bot crashed, timed out, had errors, sent too few requests, or failed to produce a valid completed report:
      -> 'inconclusive'
    - If attack succeeded:
      -> 'vulnerable'
    - If attack failed and contract passed:
      -> 'fixed'
    - If attack failed and contract failed:
      -> 'broke_business'
    """
    reasons: list[str] = []

    # 1. Check for inconclusive conditions
    if bot_result.status != "completed":
        reasons.append(
            f"Bot run did not complete cleanly (status: {bot_result.status}, exit code: {bot_result.exit_code})"
        )
        if bot_result.stderr_lines:
            reasons.append(f"Stderr: {bot_result.stderr_lines[-1]}")
        return Verdict(
            status="inconclusive",
            attack_report=bot_result.report,
            contract_result=contract_result,
            reasons=reasons,
        )

    report = bot_result.report
    if report is None:
        reasons.append("Bot did not produce a report on stdout")
        return Verdict(
            status="inconclusive",
            attack_report=None,
            contract_result=contract_result,
            reasons=reasons,
        )

    if not report.completed:
        reasons.append("Bot reported that it could not complete its attack run")
        return Verdict(
            status="inconclusive",
            attack_report=report,
            contract_result=contract_result,
            reasons=reasons,
        )

    if report.usage.requests_sent < min_requests:
        reasons.append(
            f"Bot sent too few requests ({report.usage.requests_sent} < minimum {min_requests})"
        )
        return Verdict(
            status="inconclusive",
            attack_report=report,
            contract_result=contract_result,
            reasons=reasons,
        )

    # Check evidence for infrastructure failures
    infra_errors = [
        ev
        for ev in report.evidence
        if any(
            code in ev
            for code in ("502", "503", "504", "Bad Gateway", "Gateway Timeout")
        )
    ]
    if infra_errors:
        reasons.append(
            f"Bot encountered gateway/infrastructure errors: {'; '.join(infra_errors)}"
        )
        return Verdict(
            status="inconclusive",
            attack_report=report,
            contract_result=contract_result,
            reasons=reasons,
        )

    # 2. Check if attack succeeded -> vulnerable
    if report.succeeded:
        impact_parts = []
        if report.impact.accounts_compromised > 0:
            impact_parts.append(
                f"{report.impact.accounts_compromised} account(s) compromised"
            )
        if report.impact.records_leaked > 0:
            impact_parts.append(f"{report.impact.records_leaked} record(s) leaked")
        if report.impact.records_destroyed > 0:
            impact_parts.append(
                f"{report.impact.records_destroyed} record(s) destroyed"
            )

        impact_desc = ", ".join(impact_parts) if impact_parts else "attack succeeded"
        reasons.append(f"Flaw is vulnerable: {impact_desc}")
        return Verdict(
            status="vulnerable",
            attack_report=report,
            contract_result=contract_result,
            reasons=reasons,
        )

    # 3. Attack failed: check contract result
    if contract_result is None or not contract_result.passed:
        failure_msg = (
            contract_result.failure_message
            if contract_result and contract_result.failure_message
            else "Contract checks failed or were not run"
        )
        reasons.append(
            f"Attack was repelled, but legitimate business contract failed: {failure_msg}"
        )
        return Verdict(
            status="broke_business",
            attack_report=report,
            contract_result=contract_result,
            reasons=reasons,
        )

    # 4. Attack failed AND contract passed -> fixed!
    reasons.append("Attack was repelled and all business contract checks passed")
    return Verdict(
        status="fixed",
        attack_report=report,
        contract_result=contract_result,
        reasons=reasons,
    )
