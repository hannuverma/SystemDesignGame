from __future__ import annotations

from engine.bots.models import BotImpact, BotReport, BotResult, BotUsage
from engine.verify.contract import ContractResult, ContractStepResult
from engine.verify.verdict import compute_verdict


def _make_report(
    succeeded: bool = True,
    completed: bool = True,
    requests_sent: int = 10,
    accounts: int = 1,
    evidence: list[str] | None = None,
) -> BotReport:
    return BotReport(
        attack="brute_force_login",
        seed=42,
        succeeded=succeeded,
        completed=completed,
        impact=BotImpact(accounts_compromised=accounts),
        usage=BotUsage(requests_sent=requests_sent, duration_s=1.0),
        evidence=evidence or [],
    )


def test_verdict_inconclusive_on_bot_crash() -> None:
    res = BotResult(status="crashed", exit_code=1, stderr_lines=["ModuleNotFoundError"])
    v = compute_verdict(res)
    assert v.status == "inconclusive"
    assert any("did not complete" in r for r in v.reasons)
    assert any("ModuleNotFoundError" in r for r in v.reasons)


def test_verdict_inconclusive_on_missing_report() -> None:
    res = BotResult(status="completed", report=None)
    v = compute_verdict(res)
    assert v.status == "inconclusive"
    assert any("did not produce a report" in r for r in v.reasons)


def test_verdict_inconclusive_on_uncompleted_report() -> None:
    report = _make_report(completed=False)
    res = BotResult(status="completed", report=report)
    v = compute_verdict(res)
    assert v.status == "inconclusive"
    assert any("could not complete" in r for r in v.reasons)


def test_verdict_inconclusive_on_insufficient_requests() -> None:
    report = _make_report(requests_sent=2)
    res = BotResult(status="completed", report=report)
    v = compute_verdict(res, min_requests=5)
    assert v.status == "inconclusive"
    assert any("too few requests" in r for r in v.reasons)


def test_verdict_inconclusive_on_infra_error() -> None:
    report = _make_report(evidence=["503 Service Unavailable from gateway"])
    res = BotResult(status="completed", report=report)
    v = compute_verdict(res)
    assert v.status == "inconclusive"
    assert any("gateway/infrastructure" in r for r in v.reasons)


def test_verdict_vulnerable() -> None:
    report = _make_report(succeeded=True, requests_sent=15, accounts=1)
    res = BotResult(status="completed", report=report)
    v = compute_verdict(res)
    assert v.status == "vulnerable"
    assert any("1 account(s) compromised" in r for r in v.reasons)


def test_verdict_fixed() -> None:
    report = _make_report(succeeded=False, requests_sent=15, accounts=0)
    res = BotResult(status="completed", report=report)
    contract = ContractResult(
        passed=True,
        step_results=[
            ContractStepResult(
                step_index=1,
                passed=True,
                method="POST",
                path="/login",
                expected_status=200,
                actual_status=200,
            )
        ],
    )
    v = compute_verdict(res, contract_result=contract)
    assert v.status == "fixed"
    assert any(
        "repelled and all business contract checks passed" in r for r in v.reasons
    )


def test_verdict_broke_business_when_contract_fails() -> None:
    report = _make_report(succeeded=False, requests_sent=15, accounts=0)
    res = BotResult(status="completed", report=report)
    contract = ContractResult(
        passed=False,
        failure_message="Step 1 (POST /login): expected status 200, got 500",
    )
    v = compute_verdict(res, contract_result=contract)
    assert v.status == "broke_business"
    assert any("expected status 200, got 500" in r for r in v.reasons)


def test_verdict_broke_business_when_contract_missing() -> None:
    report = _make_report(succeeded=False, requests_sent=15, accounts=0)
    res = BotResult(status="completed", report=report)
    v = compute_verdict(res, contract_result=None)
    assert v.status == "broke_business"
    assert any("contract checks failed or were not run" in r.lower() for r in v.reasons)
