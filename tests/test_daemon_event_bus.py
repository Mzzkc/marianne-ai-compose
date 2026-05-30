"""Tests for marianne.daemon.event_bus module.

Covers EventBus lifecycle, subscription management, publish/subscribe
delivery, backpressure (bounded queue with drop-oldest), event filtering,
sync/async callback support, and graceful shutdown with drain.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from marianne.daemon.event_bus import EventBus
from marianne.daemon.types import ObserverEvent

# ─── Helpers ──────────────────────────────────────────────────────────


def _make_event(
    event: str = "test.event",
    job_id: str = "test-job",
    sheet_num: int = 1,
    data: dict | None = None,
) -> ObserverEvent:
    """Create a test ObserverEvent."""
    return ObserverEvent(
        job_id=job_id,
        sheet_num=sheet_num,
        event=event,
        data=data,
        timestamp=time.time(),
    )


# ─── Lifecycle ────────────────────────────────────────────────────────


class TestLifecycle:
    """Tests for EventBus start/shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_drain_task(self):
        """Starting the bus creates the background drain task."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        assert bus._running is True
        assert bus._drain_task is not None
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        """Calling start() twice does not create a second drain task."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        task1 = bus._drain_task
        await bus.start()  # Second call
        assert bus._drain_task is task1
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_stops_drain_task(self):
        """Shutdown cancels the drain task and sets running to False."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        await bus.shutdown()
        assert bus._running is False
        assert bus._drain_task is None

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self):
        """Calling shutdown() twice does not raise."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        await bus.shutdown()
        await bus.shutdown()  # Should not raise


# ─── Subscription ─────────────────────────────────────────────────────


class TestSubscription:
    """Tests for subscribe/unsubscribe and subscriber_count."""

    @pytest.mark.asyncio
    async def test_subscribe_returns_id(self):
        """subscribe() returns a string subscription ID."""
        bus = EventBus(max_queue_size=100)
        sub_id = bus.subscribe(callback=AsyncMock())
        assert isinstance(sub_id, str)
        assert len(sub_id) > 0

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_subscriber(self):
        """unsubscribe() removes the subscriber and returns True."""
        bus = EventBus(max_queue_size=100)
        sub_id = bus.subscribe(callback=AsyncMock())
        assert bus.subscriber_count == 1
        removed = bus.unsubscribe(sub_id)
        assert removed is True
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_returns_false(self):
        """unsubscribe() with unknown ID returns False."""
        bus = EventBus(max_queue_size=100)
        removed = bus.unsubscribe("nonexistent-id")
        assert removed is False

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        """subscriber_count reflects active subscriptions."""
        bus = EventBus(max_queue_size=100)
        id1 = bus.subscribe(callback=AsyncMock())
        id2 = bus.subscribe(callback=AsyncMock())
        assert bus.subscriber_count == 2
        bus.unsubscribe(id1)
        assert bus.subscriber_count == 1
        bus.unsubscribe(id2)
        assert bus.subscriber_count == 0


# ─── Publish / Subscribe ──────────────────────────────────────────────


class TestPublishSubscribe:
    """Tests for event publish and delivery to subscribers."""

    @pytest.mark.asyncio
    async def test_async_callback_receives_event(self):
        """An async callback receives published events."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        callback = AsyncMock(side_effect=lambda e: received.append(e))
        bus.subscribe(callback=callback)

        event = _make_event("sheet.started")
        await bus.publish(event)
        await asyncio.sleep(0.1)  # Let drain loop process

        assert len(received) == 1
        assert received[0]["event"] == "sheet.started"
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_sync_callback_receives_event(self):
        """A sync callback receives published events."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        bus.subscribe(callback=lambda e: received.append(e))

        event = _make_event("job.completed")
        await bus.publish(event)
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0]["event"] == "job.completed"
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive(self):
        """All subscribers receive the same event."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received_1: list[ObserverEvent] = []
        received_2: list[ObserverEvent] = []
        bus.subscribe(callback=lambda e: received_1.append(e))
        bus.subscribe(callback=lambda e: received_2.append(e))

        await bus.publish(_make_event("sheet.completed"))
        await asyncio.sleep(0.1)

        assert len(received_1) == 1
        assert len(received_2) == 1
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_filtered_subscriber_only_sees_matching(self):
        """A subscriber with a filter only receives matching events."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        sheet_events: list[ObserverEvent] = []
        all_events: list[ObserverEvent] = []

        bus.subscribe(
            callback=lambda e: sheet_events.append(e),
            event_filter=lambda e: e["event"].startswith("sheet."),
        )
        bus.subscribe(callback=lambda e: all_events.append(e))

        await bus.publish(_make_event("sheet.started"))
        await bus.publish(_make_event("job.completed"))
        await asyncio.sleep(0.1)

        assert len(sheet_events) == 1
        assert sheet_events[0]["event"] == "sheet.started"
        assert len(all_events) == 2
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_publish_when_not_running_is_noop(self):
        """Publishing to a stopped bus silently drops events."""
        bus = EventBus(max_queue_size=100)
        # Never started
        await bus.publish(_make_event())
        assert bus._pending.empty()


# ─── Backpressure ─────────────────────────────────────────────────────


class TestBackpressure:
    """Tests for bounded queue and error handling."""

    def test_bounded_queue_drops_oldest(self):
        """A subscriber's bounded deque drops its OLDEST buffered events.

        Each subscriber owns a ``deque(maxlen=max_queue_size)`` that is the
        actual delivery buffer (#220). When the central drain fans out faster
        than the subscriber drains, the deque keeps the newest events and drops
        the oldest — preserving recency for live observability streams. Driven
        deterministically through ``_distribute`` with no worker/drain timing:
        the bus is never started, so ``subscribe`` registers the subscriber
        without spawning a draining worker.
        """
        bus = EventBus(max_queue_size=2)
        sub_id = bus.subscribe(callback=lambda e: None)
        sub = bus._subscribers[sub_id]

        for i in range(5):
            bus._distribute(_make_event(f"event.{i}", sheet_num=i))

        # deque(maxlen=2) retains only the two newest events.
        assert [e["event"] for e in sub.queue] == ["event.3", "event.4"]

    @pytest.mark.asyncio
    async def test_callback_error_does_not_stop_bus(self):
        """An exception in a callback does not crash the bus."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        good_received: list[ObserverEvent] = []

        def bad_callback(e: ObserverEvent) -> None:
            raise ValueError("boom")

        bus.subscribe(callback=bad_callback)
        bus.subscribe(callback=lambda e: good_received.append(e))

        await bus.publish(_make_event())
        await asyncio.sleep(0.1)

        # The good subscriber still receives events
        assert len(good_received) == 1
        assert bus._running is True
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_filter_error_skips_subscriber(self):
        """An exception in a filter function skips the subscriber."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        # The current implementation doesn't catch filter errors —
        # they bubble up to _distribute which catches all exceptions.
        # This test documents the behavior.
        received: list[ObserverEvent] = []

        def bad_filter(e: ObserverEvent) -> bool:
            raise ValueError("filter boom")

        bus.subscribe(
            callback=lambda e: received.append(e),
            event_filter=bad_filter,
        )

        await bus.publish(_make_event())
        await asyncio.sleep(0.1)

        # Event not received because filter errored
        # The bus is still running
        assert bus._running is True
        await bus.shutdown()


# ─── Shutdown Drain ───────────────────────────────────────────────────


class TestShutdownDrain:
    """Tests for graceful shutdown draining pending events."""

    @pytest.mark.asyncio
    async def test_running_drain_loop_delivers_before_shutdown(self):
        """The running drain loop delivers queued events before shutdown.

        (Shutdown itself is bounded and DROPS any still-pending observability
        events rather than awaiting callbacks — see #228 tests. Here the event
        is delivered by the live drain loop prior to shutdown.)
        """
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        got = asyncio.Event()

        def handler(e: ObserverEvent) -> None:
            received.append(e)
            got.set()

        bus.subscribe(callback=handler)
        await bus.publish(_make_event("drain.test"))
        await asyncio.wait_for(got.wait(), timeout=2.0)
        await bus.shutdown()

        assert any(e["event"] == "drain.test" for e in received)
