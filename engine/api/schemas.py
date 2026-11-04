from __future__ import annotations

from pydantic import BaseModel, Field

from engine.workspace import Stage


class CreateSessionRequest(BaseModel):
    company: str = "starter"


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
