from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.events.stats import StatsSnapshot

EndpointHealthStatus = Literal["ok", "warning", "critical", "breached"]


class EndpointStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: str
    status: EndpointHealthStatus
    reasons: list[str] = Field(default_factory=list)
    highlighted: bool = False
    stage_highlight: str | None = None


def detect_endpoint_status(
    endpoint_name: str,
    snapshot: StatsSnapshot,
    is_breached: bool = False,
    error_rate_crit_threshold: float = 0.5,
    error_rate_warn_threshold: float = 0.2,
    rate_spike_crit_threshold: int = 60,
    rate_spike_warn_threshold: int = 25,
    latency_crit_threshold_ms: float = 1500.0,
    latency_warn_threshold_ms: float = 500.0,
) -> tuple[EndpointHealthStatus, list[str]]:
    """Determine endpoint health status strictly from observable metrics or confirmed breach."""
    reasons: list[str] = []

    if is_breached:
        reasons.append("Confirmed security breach detected on endpoint")
        return "breached", reasons

    ep = snapshot.endpoints.get(endpoint_name)
    if not ep or ep.count == 0:
        return "ok", reasons

    # 1. 5xx Server Errors
    if ep.s5xx > 0:
        reasons.append(f"Server errors detected: {ep.s5xx} 5xx response(s)")
        return "critical", reasons

    # 2. Error rate (4xx + 5xx)
    error_count = ep.s4xx + ep.s5xx
    error_rate = error_count / ep.count
    if error_rate >= error_rate_crit_threshold and ep.count >= 5:
        reasons.append(
            f"Severe error rate: {error_rate:.0%} ({error_count}/{ep.count})"
        )
        return "critical", reasons
    if error_rate >= error_rate_warn_threshold and ep.count >= 3:
        reasons.append(
            f"Elevated error rate: {error_rate:.0%} ({error_count}/{ep.count})"
        )
        return "warning", reasons

    # 3. Request volume spikes
    if ep.count >= rate_spike_crit_threshold:
        reasons.append(f"Massive traffic spike: {ep.count} requests in window")
        return "critical", reasons
    if ep.count >= rate_spike_warn_threshold:
        reasons.append(f"Traffic spike: {ep.count} requests in window")
        return "warning", reasons

    # 4. Latency spikes
    p95 = ep.p95_ms
    if p95 >= latency_crit_threshold_ms:
        reasons.append(f"Extreme p95 latency: {p95:.1f}ms")
        return "critical", reasons
    if p95 >= latency_warn_threshold_ms:
        reasons.append(f"High p95 latency: {p95:.1f}ms")
        return "warning", reasons

    return "ok", reasons
