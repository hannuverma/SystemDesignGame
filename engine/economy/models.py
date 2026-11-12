from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EconomyConfig:
    initial_balance: float = 1000.0
    revenue_per_2xx: float = 0.01
    penalty_per_5xx: float = 1.0
    penalty_per_unavailable: float = 1.0
    fine_per_leak: float = 500.0


@dataclass
class EconomyState:
    session_id: str
    balance: float
    total_revenue: float = 0.0
    total_penalties: float = 0.0
    total_fines: float = 0.0
    leaked_records: int = 0
    uptime_percentage: float = 100.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
