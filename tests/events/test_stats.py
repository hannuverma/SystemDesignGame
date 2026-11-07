import threading

import pytest
from engine.events.bus import EventBus
from engine.events.models import RequestEvent
from engine.events.stats import StatsCollector


def _make_event(
    session_id: str = "s1",
    ts: float = 100.0,
    endpoint: str = "hello",
    status: int = 200,
    outcome: str = "ok",
    latency_ms: float = 10.0,
) -> RequestEvent:
    return RequestEvent(
        session_id=session_id,
        ts=ts,
        method="GET",
        path=f"/{endpoint}",
        endpoint=endpoint,
        status=status,
        outcome=outcome,  # type: ignore[arg-type]
        latency_ms=latency_ms,
        upstream_ms=latency_ms - 2.0 if outcome == "ok" else None,
        client="127.0.0.1",
        bytes_in=10,
        bytes_out=100,
    )


def test_stats_basic_aggregation() -> None:
    current_time = 100.0
    collector = StatsCollector(clock=lambda: current_time)

    collector.record(
        _make_event(ts=95.0, endpoint="hello", status=200, latency_ms=10.0)
    )
    collector.record(
        _make_event(ts=96.0, endpoint="hello", status=404, latency_ms=20.0)
    )
    collector.record(
        _make_event(
            ts=97.0,
            endpoint="hello",
            status=503,
            outcome="upstream_unavailable",
            latency_ms=30.0,
        )
    )
    collector.record(
        _make_event(ts=98.0, endpoint="health", status=200, latency_ms=5.0)
    )

    snap = collector.snapshot("s1", window_s=10)
    assert snap.session_id == "s1"
    assert snap.window_s == 10

    hello = snap.endpoints["hello"]
    assert hello.count == 3
    assert hello.s2xx == 1
    assert hello.s4xx == 1
    assert hello.s5xx == 1
    assert hello.unavailable == 1
    assert hello.p50_ms == 20.0

    health = snap.endpoints["health"]
    assert health.count == 1
    assert health.s2xx == 1

    totals = snap.totals
    assert totals.count == 4
    assert totals.s2xx == 2
    assert totals.s4xx == 1
    assert totals.s5xx == 1
    assert totals.unavailable == 1
    assert totals.rps == 0.4


def test_stats_window_roll_off() -> None:
    current_time = 100.0
    collector = StatsCollector(clock=lambda: current_time)

    # Event in window (95s) and out of window (85s)
    collector.record(_make_event(ts=85.0, endpoint="hello", latency_ms=10.0))
    collector.record(_make_event(ts=95.0, endpoint="hello", latency_ms=20.0))

    snap = collector.snapshot("s1", window_s=10)
    assert snap.endpoints["hello"].count == 1
    assert snap.endpoints["hello"].p50_ms == 20.0

    # Advance time beyond retention (60s)
    current_time = 170.0
    collector.record(_make_event(ts=169.0, endpoint="hello", latency_ms=5.0))

    # Old buckets (85s and 95s) should be pruned
    snap2 = collector.snapshot("s1", window_s=10)
    assert snap2.endpoints["hello"].count == 1
    assert snap2.endpoints["hello"].p50_ms == 5.0


def test_stats_percentiles_accuracy() -> None:
    collector = StatsCollector(clock=lambda: 100.0)

    # Record 100 requests with latencies 1..100 in the same second
    for i in range(1, 101):
        collector.record(_make_event(ts=99.0, latency_ms=float(i)))

    snap = collector.snapshot("s1", window_s=10)
    hello = snap.endpoints["hello"]
    assert hello.count == 100
    assert hello.p50_ms == 50.5
    assert hello.p95_ms == 95.05
    assert hello.p99_ms == 99.01


def test_stats_reservoir_sampling_cap() -> None:
    collector = StatsCollector(clock=lambda: 100.0, seed=42)

    # Record 1000 requests in a single second bucket
    for i in range(1000):
        collector.record(_make_event(ts=99.0, latency_ms=float(i)))

    snap = collector.snapshot("s1", window_s=10)
    hello = snap.endpoints["hello"]
    assert hello.count == 1000
    # Bucket raw reservoir should have capped at 500
    session_buckets = collector._buckets["s1"]
    ep_bucket = session_buckets[99].endpoints["hello"]
    assert len(ep_bucket.latencies) == 500
    assert ep_bucket.total_latency_count == 1000


def test_stats_recent_requests_ring_buffer() -> None:
    collector = StatsCollector(clock=lambda: 100.0)

    for i in range(600):
        collector.record(_make_event(ts=100.0 + (i * 0.01), latency_ms=float(i)))

    # Only 500 kept
    recent_all = collector.recent_requests("s1", tail=1000)
    assert len(recent_all) == 500
    # First item in ring buffer is request 100 (0..99 dropped)
    assert recent_all[0].latency_ms == 100.0
    assert recent_all[-1].latency_ms == 599.0

    # Tail slicing
    recent_5 = collector.recent_requests("s1", tail=5)
    assert len(recent_5) == 5
    assert recent_5[-1].latency_ms == 599.0


def test_stats_drop_session() -> None:
    collector = StatsCollector(clock=lambda: 100.0)
    collector.record(_make_event(session_id="s1", ts=99.0))
    collector.record(_make_event(session_id="s2", ts=99.0))

    collector.drop_session("s1")
    assert collector.recent_requests("s1") == []
    assert collector.active_sessions(10) == ["s2"]
    snap = collector.snapshot("s1", window_s=10)
    assert snap.totals.count == 0


@pytest.mark.asyncio
async def test_stats_tick_publishes_to_bus() -> None:
    current_time = 100.0
    collector = StatsCollector(clock=lambda: current_time)
    bus = EventBus()
    sub = bus.subscribe("s1", maxsize=10)

    collector.record(_make_event(session_id="s1", ts=99.0))
    await collector.tick(bus, window_s=10)

    snap = await sub.get()
    assert snap.session_id == "s1"
    assert snap.totals.count == 1
    sub.close()


def test_stats_concurrent_recording() -> None:
    collector = StatsCollector(clock=lambda: 100.0)
    threads = []
    total_threads = 5
    records_per_thread = 50

    def worker(tid: int):
        for j in range(records_per_thread):
            collector.record(
                _make_event(session_id="s1", ts=99.0, latency_ms=float(tid * 100 + j))
            )

    for i in range(total_threads):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    snap = collector.snapshot("s1", window_s=10)
    assert snap.totals.count == total_threads * records_per_thread
