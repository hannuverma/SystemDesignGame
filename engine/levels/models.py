from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DifficultyTier = Literal["guided", "easy", "normal", "hard"]
FailMode = Literal["warn", "none"]


class ObjectiveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    flaw: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    text: dict[str, str] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)
    reward: int = Field(default=100, ge=0)


class LevelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    company: str = Field(pattern=r"^[a-z0-9_-]+$")
    difficulty: DifficultyTier = "normal"
    fail_mode: FailMode = "warn"
    attack_delay_s: float = Field(default=2.0, ge=0.0)
    wave_interval_s: float = Field(default=10.0, gt=0.0)
    objectives: list[ObjectiveSpec] = Field(min_length=1)

