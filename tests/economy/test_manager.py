from __future__ import annotations

import pytest
from engine.economy.manager import EconomyManager
from engine.economy.models import EconomyConfig
from engine.events.bus import EventBus
from engine.events.models import (
    EconomyUpdate,
    EndpointStats,
    SecurityEvent,
    StatsSnapshot,
)


def _make_snapshot(
    session_id: str,
    ts: float,
    s2xx: int = 0,
    s4xx: int = 0,
    s5xx: int = 0,
    unavailable: int = 0,
) -> StatsSnapshot:
    count = s2xx + s4xx + s5xx + unavailable
    totals = EndpointStats(
        endpoint="*",
        count=count,
        rps=float(count),
        s2xx=s2xx,
        s4xx=s4xx,
        s5xx=s5xx,
        unavailable=unavailable,
        p50_ms=10.0,
        p95_ms=20.0,
        p99_ms=30.0,
    )
    return StatsSnapshot(
        session_id=session_id,
        ts=ts,
        window_s=60,
        endpoints={"/test": totals},
        totals=totals,
    )


def test_initial_state() -> None:
    mgr = EconomyManager()
    state = mgr.get_or_create("s1")
    assert state.session_id == "s1"
    assert state.balance == 1000.0
    assert state.total_revenue == 0.0
    assert state.total_penalties == 0.0
    assert state.total_fines == 0.0
    assert state.leaked_records == 0
    assert state.uptime_percentage == 100.0


def test_record_snapshot_revenue() -> None:
    mgr = EconomyManager(
        config=EconomyConfig(initial_balance=1000.0, revenue_per_2xx=0.01)
    )
    snap1 = _make_snapshot("s1", ts=100.0, s2xx=100)
    state = mgr.record_snapshot("s1", snap1)

    # 100 * $0.01 = +$1.00
    assert state.balance == 1001.0
    assert state.total_revenue == 1.0
    assert state.total_penalties == 0.0
    assert state.uptime_percentage == 100.0

    # Next tick: 50 more 2xx requests (total 150)
    snap2 = _make_snapshot("s1", ts=101.0, s2xx=150)
    state2 = mgr.record_snapshot("s1", snap2)
    # 50 * $0.01 = +$0.50
    assert state2.balance == 1001.5
    assert state2.total_revenue == 1.5


def test_record_snapshot_penalties_and_uptime() -> None:
    mgr = EconomyManager(
        config=EconomyConfig(
            initial_balance=1000.0,
            revenue_per_2xx=0.01,
            penalty_per_5xx=1.0,
            penalty_per_unavailable=1.0,
        )
    )
    # 90 2xx and 10 5xx -> total 100 requests. Uptime should be 90.0%
    # Revenue: +$0.90, Penalty: -$10.00 -> balance: $990.90
    snap = _make_snapshot("s1", ts=100.0, s2xx=90, s5xx=10)
    state = mgr.record_snapshot("s1", snap)

    assert state.balance == 990.9
    assert state.total_revenue == 0.9
    assert state.total_penalties == 10.0
    assert state.uptime_percentage == 90.0


def test_sliding_window_deltas_non_negative() -> None:
    mgr = EconomyManager()
    snap1 = _make_snapshot("s1", ts=100.0, s2xx=100)
    mgr.record_snapshot("s1", snap1)
    assert mgr.get_state("s1").balance == 1001.0

    # Old requests drop off window -> count drops from 100 to 80
    # Delta should be max(0, 80 - 100) = 0, balance should NOT decrease
    snap2 = _make_snapshot("s1", ts=160.0, s2xx=80)
    state2 = mgr.record_snapshot("s1", snap2)
    assert state2.balance == 1001.0

    # New requests arrive -> count goes from 80 to 90 (+10)
    snap3 = _make_snapshot("s1", ts=161.0, s2xx=90)
    state3 = mgr.record_snapshot("s1", snap3)
    # +10 * 0.01 = +0.10
    assert state3.balance == 1001.1


def test_record_breach_fine() -> None:
    mgr = EconomyManager(
        config=EconomyConfig(initial_balance=1000.0, fine_per_leak=500.0)
    )
    breach = SecurityEvent(
        session_id="s1",
        ts=100.0,
        flaw_id="flaw_sqli",
        records_leaked=3,
        evidence=["admin", "root", "guest"],
    )
    state = mgr.record_breach("s1", breach)

    # 3 * 500 = $1500 fine -> balance drops from 1000 to -500
    assert state.balance == -500.0
    assert state.total_fines == 1500.0
    assert state.leaked_records == 3


def test_reset_and_drop_session() -> None:
    mgr = EconomyManager()
    mgr.record_snapshot("s1", _make_snapshot("s1", ts=1.0, s2xx=500))
    mgr.record_breach(
        "s1", SecurityEvent(session_id="s1", ts=2.0, flaw_id="f", records_leaked=1)
    )

    # Reset
    reset_state = mgr.reset_session("s1")
    assert reset_state.balance == 1000.0
    assert reset_state.total_revenue == 0.0
    assert reset_state.total_penalties == 0.0
    assert reset_state.total_fines == 0.0
    assert reset_state.leaked_records == 0

    # Drop
    mgr.drop_session("s1")
    assert mgr.get_state("s1") is None


def test_bus_broadcast_on_update() -> None:
    bus = EventBus()
    sub = bus.subscribe("s1")
    mgr = EconomyManager(bus=bus)

    mgr.record_snapshot("s1", _make_snapshot("s1", ts=1.0, s2xx=100))

    # Queue should contain EconomyUpdate
    assert len(sub._queue) == 1
    event = sub._queue.popleft()
    assert isinstance(event, EconomyUpdate)
    assert event.session_id == "s1"
    assert event.balance == 1001.0
    assert event.total_revenue == 1.0


@pytest.mark.asyncio
async def test_bus_listener_handles_snapshot_and_security_events() -> None:
    bus = EventBus()
    mgr = EconomyManager(bus=bus)
    task = mgr.start()
    assert task is not None

    try:
        # Publish snapshot
        snap = _make_snapshot("s1", ts=10.0, s2xx=200)
        bus.publish("s1", snap)

        # Publish security event
        sec = SecurityEvent(session_id="s1", ts=11.0, flaw_id="sqli", records_leaked=1)
        bus.publish("s1", sec)

        # Yield control to event loop so listener processes events
        import asyncio

        await asyncio.sleep(0.05)

        state = mgr.get_state("s1")
        assert state is not None
        # 200 * 0.01 = +2.00 rev, 1 * 500 = -500 fine -> balance = 1000 + 2 - 500 = 502.0
        assert state.balance == 502.0
        assert state.total_revenue == 2.0
        assert state.total_fines == 500.0
        assert state.leaked_records == 1
    finally:
        await mgr.astop()
