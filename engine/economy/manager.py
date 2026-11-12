from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from engine.economy.models import EconomyConfig, EconomyState
from engine.events.bus import SubscriptionClosed
from engine.events.models import (
    EconomyUpdate,
    EndpointStats,
    SecurityEvent,
    StatsSnapshot,
)

if TYPE_CHECKING:
    from engine.events.bus import EventBus, Subscription

logger = logging.getLogger(__name__)


class EconomyManager:
    """Tracks financial state and translates telemetry/breaches into revenue, penalties, and fines."""

    def __init__(
        self,
        bus: EventBus | None = None,
        config: EconomyConfig | None = None,
    ) -> None:
        self.bus = bus
        self.config = config or EconomyConfig()
        self._lock = threading.RLock()
        self._states: dict[str, EconomyState] = {}
        self._prev_totals: dict[str, EndpointStats] = {}
        self._cum_requests: dict[str, int] = {}
        self._cum_errors: dict[str, int] = {}
        self._listener_task: asyncio.Task[None] | None = None
        self._sub: Subscription | None = None

    def get_or_create(self, session_id: str) -> EconomyState:
        """Get or initialize the economy state for a session."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = EconomyState(
                    session_id=session_id,
                    balance=self.config.initial_balance,
                    total_revenue=0.0,
                    total_penalties=0.0,
                    total_fines=0.0,
                    leaked_records=0,
                    uptime_percentage=100.0,
                    updated_at=time.time(),
                )
                self._states[session_id] = state
                self._cum_requests[session_id] = 0
                self._cum_errors[session_id] = 0
            return state

    def get_state(self, session_id: str) -> EconomyState | None:
        """Return the current economy state for `session_id`, or None if not initialized."""
        with self._lock:
            return self._states.get(session_id)

    def record_snapshot(self, session_id: str, snapshot: StatsSnapshot) -> EconomyState:
        """Update financial balance based on deltas from a StatsSnapshot."""
        with self._lock:
            state = self.get_or_create(session_id)
            prev = self._prev_totals.get(session_id)
            totals = snapshot.totals

            if prev is None:
                d_2xx = totals.s2xx
                d_5xx = totals.s5xx
                d_unavail = totals.unavailable
                d_count = totals.count
            else:
                d_2xx = max(0, totals.s2xx - prev.s2xx)
                d_5xx = max(0, totals.s5xx - prev.s5xx)
                d_unavail = max(0, totals.unavailable - prev.unavailable)
                d_count = max(0, totals.count - prev.count)

            self._prev_totals[session_id] = totals

            # Accumulate requests for uptime calculation
            self._cum_requests[session_id] = (
                self._cum_requests.get(session_id, 0) + d_count
            )
            err_count = d_5xx + d_unavail
            self._cum_errors[session_id] = (
                self._cum_errors.get(session_id, 0) + err_count
            )

            cum_req = self._cum_requests[session_id]
            cum_err = self._cum_errors[session_id]
            if cum_req > 0:
                uptime = round(
                    max(0.0, min(100.0, (1.0 - (cum_err / cum_req)) * 100.0)), 2
                )
            else:
                uptime = 100.0

            rev = round(d_2xx * self.config.revenue_per_2xx, 4)
            penalties = round(
                (d_5xx * self.config.penalty_per_5xx)
                + (d_unavail * self.config.penalty_per_unavailable),
                4,
            )

            state.balance = round(state.balance + rev - penalties, 2)
            state.total_revenue = round(state.total_revenue + rev, 2)
            state.total_penalties = round(state.total_penalties + penalties, 2)
            state.uptime_percentage = uptime
            state.updated_at = snapshot.ts

            update_event = self._make_update(state)

        self._broadcast(session_id, update_event)
        return state

    def record_breach(self, session_id: str, event: SecurityEvent) -> EconomyState:
        """Apply financial penalties for a security breach."""
        with self._lock:
            state = self.get_or_create(session_id)
            records = max(0, event.records_leaked)
            fine = round(records * self.config.fine_per_leak, 2)

            state.balance = round(state.balance - fine, 2)
            state.total_fines = round(state.total_fines + fine, 2)
            state.leaked_records += records
            state.updated_at = event.ts

            update_event = self._make_update(state)

        self._broadcast(session_id, update_event)
        return state

    def reset_session(self, session_id: str) -> EconomyState:
        """Reset financial metrics for a session back to initial balance."""
        with self._lock:
            now = time.time()
            state = EconomyState(
                session_id=session_id,
                balance=self.config.initial_balance,
                total_revenue=0.0,
                total_penalties=0.0,
                total_fines=0.0,
                leaked_records=0,
                uptime_percentage=100.0,
                updated_at=now,
            )
            self._states[session_id] = state
            self._prev_totals.pop(session_id, None)
            self._cum_requests[session_id] = 0
            self._cum_errors[session_id] = 0
            update_event = self._make_update(state)

        self._broadcast(session_id, update_event)
        return state

    def drop_session(self, session_id: str) -> None:
        """Clean up all memory state associated with a session."""
        with self._lock:
            self._states.pop(session_id, None)
            self._prev_totals.pop(session_id, None)
            self._cum_requests.pop(session_id, None)
            self._cum_errors.pop(session_id, None)

    def handle_event(self, event: Any) -> None:
        """Process incoming bus events."""
        session_id = getattr(event, "session_id", None)
        if not session_id:
            return

        try:
            if isinstance(event, StatsSnapshot):
                self.record_snapshot(session_id, event)
            elif isinstance(event, SecurityEvent):
                self.record_breach(session_id, event)
        except Exception:
            logger.exception(
                "Failed to process event in EconomyManager for session %s", session_id
            )

    def _make_update(self, state: EconomyState) -> EconomyUpdate:
        return EconomyUpdate(
            session_id=state.session_id,
            ts=state.updated_at,
            balance=state.balance,
            total_revenue=state.total_revenue,
            total_penalties=state.total_penalties,
            total_fines=state.total_fines,
            leaked_records=state.leaked_records,
            uptime_percentage=state.uptime_percentage,
        )

    def _broadcast(self, session_id: str, event: EconomyUpdate) -> None:
        if self.bus is not None:
            self.bus.publish(session_id, event)

    def start(self) -> asyncio.Task[None] | None:
        """Start background event consumption from EventBus if bus is provided."""
        if self.bus is None:
            return None
        if self._listener_task is not None and not self._listener_task.done():
            return self._listener_task

        self._sub = self.bus.subscribe("*")
        self._listener_task = asyncio.create_task(self._run_listener())
        return self._listener_task

    def stop(self) -> None:
        """Stop background event consumption."""
        if self._sub is not None:
            self._sub.close()
            self._sub = None
        if self._listener_task is not None:
            self._listener_task.cancel()
            self._listener_task = None

    async def astop(self) -> None:
        """Asynchronously stop the listener and await task completion."""
        if self._sub is not None:
            self._sub.close()
            self._sub = None
        if self._listener_task is not None:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None

    async def _run_listener(self) -> None:
        if self._sub is None:
            return
        try:
            async for event in self._sub:
                self.handle_event(event)
        except (asyncio.CancelledError, SubscriptionClosed):
            pass
        except Exception:
            logger.exception("Unexpected error in EconomyManager listener")
        finally:
            if self._sub is not None:
                self._sub.close()
                self._sub = None
