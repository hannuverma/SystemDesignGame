from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)


class SubscriptionClosed(Exception):
    """Raised when awaiting an event on a closed subscription."""


class Subscription:
    """A bounded, drop-oldest event queue for a single subscriber."""

    def __init__(
        self,
        session_id: str,
        bus: EventBus,
        loop: asyncio.AbstractEventLoop | None = None,
        maxsize: int = 256,
    ) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize must be > 0, got {maxsize}")
        self.session_id = session_id
        self.maxsize = maxsize
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = loop
        if self._loop is None:
            with contextlib.suppress(RuntimeError):
                self._loop = asyncio.get_running_loop()
        self._queue: deque[Any] = deque(maxlen=maxsize)
        self._dropped: int = 0
        self._lock = threading.Lock()
        self._waiter: asyncio.Future[None] | None = None
        self._closed: bool = False

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def is_closed(self) -> int:
        with self._lock:
            return self._closed

    def _put(self, event: Any) -> None:
        waiter: asyncio.Future[None] | None = None
        with self._lock:
            if self._closed:
                return
            if len(self._queue) == self.maxsize:
                self._dropped += 1
            self._queue.append(event)
            if self._waiter is not None and not self._waiter.done():
                waiter = self._waiter
                self._waiter = None

        if waiter is not None and self._loop is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._resolve_waiter, waiter)

    @staticmethod
    def _resolve_waiter(waiter: asyncio.Future[None]) -> None:
        if not waiter.done():
            waiter.set_result(None)

    async def get(self) -> Any:
        """Fetch the next event. Blocks until an event is available or closed."""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        while True:
            waiter: asyncio.Future[None] | None = None
            with self._lock:
                if self._queue:
                    return self._queue.popleft()
                if self._closed:
                    raise SubscriptionClosed("Subscription is closed")

                waiter = self._loop.create_future()
                self._waiter = waiter

            try:
                await waiter
            except asyncio.CancelledError:
                with self._lock:
                    if self._waiter is waiter:
                        self._waiter = None
                raise

    def close(self) -> None:
        """Close the subscription and unblock any waiting consumer."""
        waiter: asyncio.Future[None] | None = None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            if self._waiter is not None and not self._waiter.done():
                waiter = self._waiter
                self._waiter = None

        if waiter is not None and self._loop is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._resolve_waiter, waiter)

        self._bus._unsubscribe(self.session_id, self)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.get()
        except SubscriptionClosed:
            raise StopAsyncIteration from None


class EventBus:
    """Thread-safe, non-blocking event distributor routing events by session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, set[Subscription]] = defaultdict(set)

    def publish(self, session_id: str, event: Any) -> None:
        """Publish an event for `session_id`.

        Thread-safe, non-blocking, and NEVER raises into the caller.
        """
        try:
            with self._lock:
                subs = list(self._subs.get(session_id, ()))
                if "*" in self._subs and session_id != "*":
                    subs.extend(self._subs["*"])

            for sub in subs:
                try:
                    sub._put(event)
                except Exception:
                    logger.exception("Error delivering event to subscription")
        except Exception:
            logger.exception("Error in EventBus.publish for session %s", session_id)

    def subscribe(
        self,
        session_id: str,
        maxsize: int = 256,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> Subscription:
        """Create a bounded subscription for `session_id`.

        Captures the running event loop if not explicitly provided.
        """
        if maxsize <= 0:
            raise ValueError(f"maxsize must be > 0, got {maxsize}")
        sub = Subscription(session_id, self, loop=loop, maxsize=maxsize)
        with self._lock:
            self._subs[session_id].add(sub)
        return sub

    def _unsubscribe(self, session_id: str, sub: Subscription) -> None:
        with self._lock:
            subs = self._subs.get(session_id)
            if subs and sub in subs:
                subs.remove(sub)
                if not subs:
                    del self._subs[session_id]

    def subscriber_count(self, session_id: str) -> int:
        """Return active subscriber count for `session_id`."""
        with self._lock:
            return len(self._subs.get(session_id, ()))
