from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.flaws.models import BudgetConfig

BotStatus = Literal["completed", "timeout", "error", "crashed"]


class BotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    command: list[str] = Field(min_length=1)
    target_url: str
    env: dict[str, str] = Field(default_factory=dict)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    seed: int = 42
    cwd: Path | None = None


class BotImpact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accounts_compromised: int = 0
    records_leaked: int = 0
    records_destroyed: int = 0


class BotUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requests_sent: int = 0
    duration_s: float = 0.0


class BotProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["progress"] = "progress"
    requests_sent: int
    note: str = ""


class BotReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["report"] = "report"
    attack: str
    seed: int
    succeeded: bool
    completed: bool
    impact: BotImpact = Field(default_factory=BotImpact)
    usage: BotUsage = Field(default_factory=BotUsage)
    evidence: list[str] = Field(default_factory=list)


class BotResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: BotStatus
    report: BotReport | None = None
    exit_code: int | None = None
    duration_s: float = 0.0
    stdout_lines: list[str] = Field(default_factory=list)
    stderr_lines: list[str] = Field(default_factory=list)
