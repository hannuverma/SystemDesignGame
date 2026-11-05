import pytest
from engine.events.models import DeployEvent, EndpointStats, RequestEvent, StatsSnapshot


def test_request_event_valid() -> None:
    event = RequestEvent(
        session_id="s1",
        ts=100.0,
        method="GET",
        path="/hello",
        endpoint="hello",
        status=200,
        outcome="ok",
        latency_ms=12.5,
        upstream_ms=10.0,
        client="127.0.0.1",
        bytes_in=10,
        bytes_out=50,
    )
    d = event.to_dict()
    assert d["session_id"] == "s1"
    assert d["outcome"] == "ok"
    assert d["latency_ms"] == 12.5


def test_request_event_invalid_outcome() -> None:
    with pytest.raises(ValueError, match="Invalid outcome"):
        RequestEvent(
            session_id="s1",
            ts=100.0,
            method="GET",
            path="/hello",
            endpoint="hello",
            status=500,
            outcome="invalid_outcome",  # type: ignore[arg-type]
            latency_ms=1.0,
            upstream_ms=None,
            client="127.0.0.1",
            bytes_in=0,
            bytes_out=0,
        )


def test_deploy_event_valid_and_invalid() -> None:
    event = DeployEvent(
        session_id="s1",
        ts=100.0,
        phase="started",
        endpoint="hello",
        stage="hello",
        step="build",
        status="running",
        message="building stage",
    )
    assert event.to_dict()["phase"] == "started"

    with pytest.raises(ValueError, match="Invalid phase"):
        DeployEvent(
            session_id="s1",
            ts=100.0,
            phase="unknown",  # type: ignore[arg-type]
            endpoint="hello",
            stage="hello",
            step="build",
            status="running",
            message="building stage",
        )


def test_stats_snapshot_to_dict() -> None:
    ep = EndpointStats(
        endpoint="hello",
        count=10,
        rps=2.0,
        s2xx=9,
        s4xx=1,
        s5xx=0,
        unavailable=0,
        p50_ms=5.0,
        p95_ms=10.0,
        p99_ms=15.0,
    )
    snapshot = StatsSnapshot(
        session_id="s1",
        ts=100.0,
        window_s=10,
        endpoints={"hello": ep},
        totals=ep,
    )
    d = snapshot.to_dict()
    assert d["endpoints"]["hello"]["count"] == 10
    assert d["totals"]["count"] == 10
