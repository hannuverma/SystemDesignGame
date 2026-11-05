import asyncio
import threading

import pytest
from engine.events.bus import EventBus, SubscriptionClosed


@pytest.mark.asyncio
async def test_bus_ordering() -> None:
    bus = EventBus()
    sub = bus.subscribe("s1", maxsize=10)

    for i in range(5):
        bus.publish("s1", i)

    results = []
    for _ in range(5):
        results.append(await sub.get())

    assert results == [0, 1, 2, 3, 4]
    sub.close()


@pytest.mark.asyncio
async def test_bus_overflow_drop_oldest() -> None:
    bus = EventBus()
    sub = bus.subscribe("s1", maxsize=3)

    for i in range(5):  # publishes 0, 1, 2, 3, 4
        bus.publish("s1", i)

    assert sub.dropped == 2
    # The 3 remaining events should be the newest: 2, 3, 4
    results = [await sub.get(), await sub.get(), await sub.get()]
    assert results == [2, 3, 4]
    sub.close()


@pytest.mark.asyncio
async def test_bus_session_isolation() -> None:
    bus = EventBus()
    sub1 = bus.subscribe("s1", maxsize=10)
    sub2 = bus.subscribe("s2", maxsize=10)

    bus.publish("s1", "for_s1")
    bus.publish("s2", "for_s2")

    assert await sub1.get() == "for_s1"
    assert await sub2.get() == "for_s2"

    sub1.close()
    sub2.close()


@pytest.mark.asyncio
async def test_bus_subscriber_closing_mid_stream() -> None:
    bus = EventBus()
    sub = bus.subscribe("s1")

    async def getter():
        with pytest.raises(SubscriptionClosed):
            await sub.get()

    task = asyncio.create_task(getter())
    await asyncio.sleep(0.01)

    assert bus.subscriber_count("s1") == 1
    sub.close()
    await task
    assert bus.subscriber_count("s1") == 0

    # Getting from closed sub raises immediately
    with pytest.raises(SubscriptionClosed):
        await sub.get()


@pytest.mark.asyncio
async def test_bus_many_publisher_threads() -> None:
    bus = EventBus()
    total_threads = 10
    events_per_thread = 50
    expected_total = total_threads * events_per_thread
    sub = bus.subscribe("s1", maxsize=expected_total + 10)

    def publish_worker(thread_id: int):
        for j in range(events_per_thread):
            bus.publish("s1", (thread_id, j))

    threads = [
        threading.Thread(target=publish_worker, args=(i,)) for i in range(total_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    received = set()
    for _ in range(expected_total):
        item = await asyncio.wait_for(sub.get(), timeout=2.0)
        received.add(item)

    assert len(received) == expected_total
    assert sub.dropped == 0
    sub.close()


@pytest.mark.asyncio
async def test_bus_async_iteration() -> None:
    bus = EventBus()
    sub = bus.subscribe("s1", maxsize=5)

    bus.publish("s1", 1)
    bus.publish("s1", 2)

    received = []

    async def consume():
        async for item in sub:
            received.append(item)
            if len(received) == 2:
                sub.close()

    await consume()
    assert received == [1, 2]
    assert sub.is_closed


def test_bus_publish_never_raises_on_unknown_or_broken() -> None:
    bus = EventBus()
    # Publish to unknown session
    bus.publish("nonexistent", {"test": 1})

    # Sub with broken maxsize
    with pytest.raises(ValueError, match="maxsize must be > 0"):
        bus.subscribe("s1", maxsize=0)
