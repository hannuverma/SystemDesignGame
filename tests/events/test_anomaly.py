from __future__ import annotations

from engine.events.anomaly import detect_endpoint_status
from engine.events.models import EndpointStats, StatsSnapshot


def _make_snapshot(
    count: int = 10,
    s2xx: int = 10,
    s4xx: int = 0,
    s5xx: int = 0,
    p95: float = 50.0,
) -> StatsSnapshot:
    ep = EndpointStats(
        endpoint="login",
        count=count,
        rps=float(count) / 10.0,
        s2xx=s2xx,
        s4xx=s4xx,
        s5xx=s5xx,
        unavailable=0,
        p50_ms=20.0,
        p95_ms=p95,
        p99_ms=p95,
    )
    return StatsSnapshot(
        session_id="s1",
        ts=100.0,
        window_s=30,
        totals=ep,
        endpoints={"login": ep},
    )


def test_detect_ok_status() -> None:
    snap = _make_snapshot(count=10, s2xx=10)
    status, reasons = detect_endpoint_status("login", snap)
    assert status == "ok"
    assert reasons == []


def test_detect_breached_status() -> None:
    snap = _make_snapshot(count=10, s2xx=10)
    status, reasons = detect_endpoint_status("login", snap, is_breached=True)
    assert status == "breached"
    assert any("breach detected" in r for r in reasons)


def test_detect_5xx_critical() -> None:
    snap = _make_snapshot(count=10, s2xx=9, s5xx=1)
    status, reasons = detect_endpoint_status("login", snap)
    assert status == "critical"
    assert any("Server errors" in r for r in reasons)


def test_detect_error_rate_warning_and_critical() -> None:
    # 3 errors out of 10 = 30% -> warning
    snap_warn = _make_snapshot(count=10, s2xx=7, s4xx=3)
    status_w, _ = detect_endpoint_status("login", snap_warn)
    assert status_w == "warning"

    # 6 errors out of 10 = 60% -> critical
    snap_crit = _make_snapshot(count=10, s2xx=4, s4xx=6)
    status_c, _ = detect_endpoint_status("login", snap_crit)
    assert status_c == "critical"


def test_detect_traffic_spikes() -> None:
    snap_spike = _make_snapshot(count=35, s2xx=35)
    status, reasons = detect_endpoint_status("login", snap_spike)
    assert status == "warning"
    assert any("Traffic spike" in r for r in reasons)


def test_detect_latency_spikes() -> None:
    snap_lat = _make_snapshot(count=10, s2xx=10, p95=600.0)
    status, reasons = detect_endpoint_status("login", snap_lat)
    assert status == "warning"
    assert any("High p95 latency" in r for r in reasons)
