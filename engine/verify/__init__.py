"""Verification package: contracts, verdicts, and regression checks."""

from engine.verify.contract import (
    ContractResult,
    ContractStepResult,
    run_contract,
)
from engine.verify.verdict import (
    Verdict,
    VerdictStatus,
    compute_verdict,
)

__all__ = [
    "ContractResult",
    "ContractStepResult",
    "Verdict",
    "VerdictStatus",
    "compute_verdict",
    "run_contract",
]
