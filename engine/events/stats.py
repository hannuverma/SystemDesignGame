from __future__ import annotations

import asyncio
import math
import random
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from engine.events.models import EndpointStats, RequestEvent, StatsSnapshot

if TYPE_CHECKING:
    from engine.events.bus import EventBus

MAX_LATENCY_SAMPLES = 500
RETENTION_S = 60
DEFAULT_WINDOW_S = 10


def _calculate_percentile(sorted_values: list[float], p: float) -> float:
    """Calculate percentile p (0-100) using linear interpolation."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return round(float(sorted_values[0]), 2)
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(float(sorted_values[int(k)]), 2)
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return round(float(d0 + d1), 2)


@dataclass
class _EndpointBucket:
    count: int = 0
    s2xx: int = 0
    s4xx: int = 0
    s5xx: int = 0
    unavailable: int = 0
    latencies: list[float] = field(default_factory=list)
    total_latency_count: int = 0

    def record(self, event: RequestEvent, rng: random.Random) -> None:
        self.count += 1
        status = event.status
        if 200 <= status < 300:
            self.s2xx += 1
        elif 400 <= status < 500:
            self.s4xx += 1
        elif 500 <= status < 600:
            self.s5xx += 1

        if event.outcome == "upstream_unavailable" or status in (503, 504):
            self.unavailable += 1

        latency = event.latency_ms
        if len(self.latencies) < MAX_LATENCY_SAMPLES:
            self.latencies.append(latency)
        else:
            j = rng.randint(0, self.total_latency_count)
            if j < MAX_LATENCY_SAMPLES:
                self.latencies[j] = latency
        self.total_latency_count += 1


@dataclass
class _TimeBucket:
    ts: int
    endpoints: dict[str, _EndpointBucket] = field(
        default_factory=lambda: defaultdict(_EndpointBucket)
    )


class StatsCollector:
    """Aggregates request events into 1-second time buckets over a 60-second window."""

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        seed: int | None = None,
    ) -> None:
        self.clock: Callable[[], float] = clock or time.time
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        # session_id -> {bucket_sec -> _TimeBucket}
        self._buckets: dict[str, dict[int, _TimeBucket]] = defaultdict(dict)
        # session_id -> deque of RequestEvent (maxlen=500)
        self._recent: dict[str, deque[RequestEvent]] = defaultdict(
            lambda: deque(maxlen=500)
        )
        # session_id -> last activity timestamp
        self._last_active: dict[str, float] = {}

    def record(self, event: RequestEvent) -> None:
        """Record a single request event."""
        now = self.clock()
        sec = int(event.ts)
        with self._lock:
            self._prune_session_locked(event.session_id, now)
            session_buckets = self._buckets[event.session_id]
            if sec not in session_buckets:
                session_buckets[sec] = _TimeBucket(ts=sec)
            bucket = session_buckets[sec]
            ep_bucket = bucket.endpoints[event.endpoint]
            ep_bucket.record(event, self._rng)

            self._recent[event.session_id].append(event)
            self._last_active[event.session_id] = now

    def mark_active(self, session_id: str) -> None:
        """Mark a session as having activity (e.g. from a deploy event)."""
        now = self.clock()
        with self._lock:
            self._last_active[session_id] = now

    def _prune_session_locked(self, session_id: str, now: float) -> None:
        cutoff = int(now) - RETENTION_S
        session_buckets = self._buckets.get(session_id)
        if not session_buckets:
            return
        expired = [ts for ts in session_buckets if ts < cutoff]
        for ts in expired:
            del session_buckets[ts]

    def snapshot(
        self, session_id: str, window_s: int = DEFAULT_WINDOW_S
    ) -> StatsSnapshot:
        """Compute an aggregated snapshot of stats over the last `window_s` seconds."""
        if window_s <= 0:
            raise ValueError(f"window_s must be > 0, got {window_s}")

        now = self.clock()
        cutoff_sec = int(now) - window_s

        with self._lock:
            self._prune_session_locked(session_id, now)
            session_buckets = self._buckets.get(session_id, {})
            matching_buckets = [
                b
                for ts, b in session_buckets.items()
                if ts > cutoff_sec and ts <= int(now)
            ]

            # Aggregate per endpoint
            ep_totals: dict[str, dict[str, Any]] = defaultdict(
                lambda: {
                    "count": 0,
                    "s2xx": 0,
                    "s4xx": 0,
                    "s5xx": 0,
                    "unavailable": 0,
                    "latencies": [],
                }
            )

            for b in matching_buckets:
                for ep_name, ep_b in b.endpoints.items():
                    data = ep_totals[ep_name]
                    data["count"] += ep_b.count
                    data["s2xx"] += ep_b.s2xx
                    data["s4xx"] += ep_b.s4xx
                    data["s5xx"] += ep_b.s5xx
                    data["unavailable"] += ep_b.unavailable
                    data["latencies"].extend(ep_b.latencies)

        # Compute per-endpoint stats
        endpoints_result: dict[str, EndpointStats] = {}
        total_count = 0
        total_s2xx = 0
        total_s4xx = 0
        total_s5xx = 0
        total_unavailable = 0
        all_latencies: list[float] = []

        for ep_name, data in ep_totals.items():
            count = data["count"]
            s2xx = data["s2xx"]
            s4xx = data["s4xx"]
            s5xx = data["s5xx"]
            unavailable = data["unavailable"]
            latencies = sorted(data["latencies"])

            total_count += count
            total_s2xx += s2xx
            total_s4xx += s4xx
            total_s5xx += s5xx
            total_unavailable += unavailable
            all_latencies.extend(latencies)

            endpoints_result[ep_name] = EndpointStats(
                endpoint=ep_name,
                count=count,
                rps=round(count / window_s, 2),
                s2xx=s2xx,
                s4xx=s4xx,
                s5xx=s5xx,
                unavailable=unavailable,
                p50_ms=_calculate_percentile(latencies, 50),
                p95_ms=_calculate_percentile(latencies, 95),
                p99_ms=_calculate_percentile(latencies, 99),
            )

        all_latencies.sort()
        totals = EndpointStats(
            endpoint="totals",
            count=total_count,
            rps=round(total_count / window_s, 2),
            s2xx=total_s2xx,
            s4xx=total_s4xx,
            s5xx=total_s5xx,
            unavailable=total_unavailable,
            p50_ms=_calculate_percentile(all_latencies, 50),
            p95_ms=_calculate_percentile(all_latencies, 95),
            p99_ms=_calculate_percentile(all_latencies, 99),
        )

        return StatsSnapshot(
            session_id=session_id,
            ts=round(now, 3),
            window_s=window_s,
            endpoints=endpoints_result,
            totals=totals,
        )

    def recent_requests(self, session_id: str, tail: int = 50) -> list[RequestEvent]:
        """Return the most recent `tail` requests for `session_id`."""
        if tail <= 0:
            return []
        with self._lock:
            q = self._recent.get(session_id)
            if not q:
                return []
            items = list(q)
            return items[-tail:]

    def active_sessions(self, window_s: int = DEFAULT_WINDOW_S) -> list[str]:
        """Return session IDs that had activity within the last `window_s` seconds."""
        now = self.clock()
        cutoff = now - window_s
        with self._lock:
            return [
                sid for sid, last_ts in self._last_active.items() if last_ts >= cutoff
            ]

    def drop_session(self, session_id: str) -> None:
        """Free all accumulated state for `session_id`."""
        with self._lock:
            self._buckets.pop(session_id, None)
            self._recent.pop(session_id, None)
            self._last_active.pop(session_id, None)

    async def tick(self, bus: EventBus, window_s: int = DEFAULT_WINDOW_S) -> None:
        """Generate and publish a snapshot for all sessions active in the window."""
        sessions = self.active_sessions(window_s)
        for sid in sessions:
            snap = self.snapshot(sid, window_s)
            bus.publish(sid, snap)

    async def run_ticker(
        self,
        bus: EventBus,
        interval_s: float = 1.0,
        window_s: int = DEFAULT_WINDOW_S,
    ) -> None:
        """Run periodic 1-second snapshots for active sessions until cancelled."""
        while True:
            await asyncio.sleep(interval_s)
            await self.tick(bus, window_s)
