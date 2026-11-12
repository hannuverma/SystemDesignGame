from __future__ import annotations

from pydantic import BaseModel, Field

from engine.bots.models import BotResult
from engine.flaws.models import BudgetConfig
from engine.levels.models import DifficultyTier
from engine.verify.attacks import AttackRecord, AttackStatus
from engine.verify.contract import ContractResult
from engine.verify.verdict import Verdict
from engine.workspace import Stage


class CreateSessionRequest(BaseModel):
    company: str = "starter"
    level: str | None = None
    difficulty: DifficultyTier | None = None


class SessionOut(BaseModel):
    id: str
    company: str
    url: str | None
    running: bool
    healthy: bool
    commit: str


class StageSummary(BaseModel):
    name: str
    endpoint: str
    order: int
    file: str
    symbol: str
    start_line: int
    end_line: int

    @classmethod
    def of(cls, stage: Stage) -> StageSummary:
        return cls(**{k: getattr(stage, k) for k in cls.model_fields})


class StageDetail(StageSummary):
    code: str

    @classmethod
    def of(cls, stage: Stage) -> StageDetail:
        return cls(**{k: getattr(stage, k) for k in cls.model_fields})


class StageUpdate(BaseModel):
    code: str = Field(max_length=200_000)


class StartAttackRequest(BaseModel):
    flaw_id: str
    seed: int = 42
    budget: BudgetConfig | None = None
    wait: bool = False


class ReplayAttackRequest(BaseModel):
    wait: bool = False


class AttackOut(BaseModel):
    id: str
    session_id: str
    flaw_id: str
    seed: int
    status: AttackStatus
    budget: BudgetConfig
    verdict: Verdict | None = None
    bot_result: BotResult | None = None
    contract_result: ContractResult | None = None
    created_at: float
    completed_at: float | None = None

    @classmethod
    def of(cls, rec: AttackRecord) -> AttackOut:
        return cls(
            id=rec.id,
            session_id=rec.session_id,
            flaw_id=rec.flaw_id,
            seed=rec.seed,
            status=rec.status,
            budget=rec.budget,
            verdict=rec.verdict,
            bot_result=rec.bot_result,
            contract_result=rec.contract_result,
            created_at=rec.created_at,
            completed_at=rec.completed_at,
        )


class EconomyOut(BaseModel):
    session_id: str
    balance: float
    total_revenue: float
    total_penalties: float
    total_fines: float
    leaked_records: int
    uptime_percentage: float
    updated_at: float
