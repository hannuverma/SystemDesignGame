from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

RequestOutcome = Literal["ok", "upstream_unavailable", "timeout", "error"]
DeployPhase = Literal["started", "step", "finished"]

VALID_OUTCOMES: frozenset[str] = frozenset(
    {"ok", "upstream_unavailable", "timeout", "error"}
)
VALID_DEPLOY_PHASES: frozenset[str] = frozenset({"started", "step", "finished"})


@dataclass(frozen=True)
class RequestEvent:
    session_id: str
    ts: float
    method: str
    path: str
    endpoint: str
    status: int
    outcome: RequestOutcome
    latency_ms: float
    upstream_ms: float | None
    client: str
    bytes_in: int
    bytes_out: int

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"Invalid outcome: {self.outcome!r}. Must be one of {sorted(VALID_OUTCOMES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EndpointStats:
    endpoint: str
    count: int
    rps: float
    s2xx: int
    s4xx: int
    s5xx: int
    unavailable: int
    p50_ms: float
    p95_ms: float
    p99_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StatsSnapshot:
    session_id: str
    ts: float
    window_s: int
    endpoints: dict[str, EndpointStats]
    totals: EndpointStats

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["endpoints"] = {
            k: v.to_dict() if isinstance(v, EndpointStats) else v
            for k, v in self.endpoints.items()
        }
        data["totals"] = (
            self.totals.to_dict()
            if isinstance(self.totals, EndpointStats)
            else self.totals
        )
        return data


@dataclass(frozen=True)
class DeployEvent:
    session_id: str
    ts: float
    phase: DeployPhase
    endpoint: str
    stage: str
    step: str
    status: str
    message: str

    def __post_init__(self) -> None:
        if self.phase not in VALID_DEPLOY_PHASES:
            raise ValueError(
                f"Invalid phase: {self.phase!r}. Must be one of {sorted(VALID_DEPLOY_PHASES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
