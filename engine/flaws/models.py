from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

FLAW_ID_RE = re.compile(r"^[a-z0-9_]+$")


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_requests: int = Field(default=500, gt=0, le=50000)
    max_duration_s: float = Field(default=30.0, gt=0.0, le=300.0)
    max_concurrency: int = Field(default=5, gt=0, le=100)


class AttackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: list[str] = Field(min_length=1)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


class ContractStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = "GET"
    path: str
    json_body: Any = Field(default=None, alias="json")
    headers: dict[str, str] = Field(default_factory=dict)
    expect_status: int = Field(default=200, alias="status")
    expect_keys: list[str] = Field(default_factory=list, alias="keys")
    save: dict[str, str] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        return v.upper()


class FlawSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    category: str
    endpoint: str
    stage: str
    attack: AttackConfig
    contract: list[ContractStepSpec] = Field(min_length=1)
    reference_fix: str
    symptoms: str
    hints: list[str] = Field(default_factory=list)
    lesson: str
    dir: Path | None = None
