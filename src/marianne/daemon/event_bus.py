"""Async pub/sub event bus for the Marianne daemon.

Routes ObserverEvents from the runner and observer to downstream consumers
(SSE dashboard, learning hub, future webhooks). Each subscriber gets a
bounded deque — slow subscribers lose oldest events rather than blocking
the publisher.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from marianne.core.logging import get_logger
from marianne.daemon.types import ObserverEvent

_logger = get_logger("daemon.event_bus")

# Type alias for subscriber callbacks
EventFilter = Callable[[ObserverEvent], bool] | None
EventCallback = Callable[[ObserverEvent], Any]

_MAX_CONSECUTIVE_FAILURES = 10


class EventBus:
    """Async pub/sub event bus with bounded queues per subscriber.

    Subscribers receive events asynchronously via callbacks. Each subscriber
    has a bounded deque (max_queue_size). When the queue is full, the oldest
    event is dropped (backpressure via drop-oldest policy).

    Usage::

        bus = EventBus(max_queue_size=1000)
        await bus.start()

        # Subscribe to all events
        sub_id = bus.subscribe(callback=my_handler)

        # Subscribe with filter
        sub_id = bus.subscribe(
            callback=my_handler,
            event_filter=lambda e: e["event"].startswith("sheet."),
        )

        # Publish events
        await bus.publish(event)

        # Cleanup
        bus.unsubscribe(sub_id)
        await bus.shutdown()
    """

    def __init__(self, *, max_queue_size: int = 1000) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: dict[str, _Subscriber] = {}
        self._drain_task: asyncio.Task[None] | None = None
        self._pending: asyncio.Queue[ObserverEvent] = asyncio.Queue()
        self._running = False

    async def start(self) -> None:
        """Start the background drain loop."""
        if self._running:
            return
        self._running = True
        self._drain_task = asyncio.create_task(
            self._drain_loop(), name="event-bus-drain"
        )

    async def publish(self, event: ObserverEvent) -> None:
        """Publish an event to all matching subscribers.

        Non-blocking for the publisher. Events are queued for the drain loop
        to distribute to subscribers.
        """
        if not self._running:
            return
        await self._pending.put(event)

    def subscribe(
        self,
        callback: EventCallback,
        *,
        event_filter: EventFilter = None,
    ) -> str:
        """Register a subscriber.

        Args:
            callback: Async or sync callable receiving ObserverEvent.
            event_filter: Optional filter function. If provided, the subscriber
                only receives events where filter returns True.

        Returns:
            Subscription ID for later unsubscribe.
        """
        sub_id = str(uuid.uuid4())
        self._subscribers[sub_id] = _Subscriber(
            callback=callback,
            event_filter=event_filter,
            queue=deque(maxlen=self._max_queue_size),
        )
        _logger.info("event_bus.subscribed", sub_id=sub_id)
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscriber.

        Returns:
            True if the subscriber existed and was removed.
        """
        removed = self._subscribers.pop(sub_id, None) is not None
        if removed:
            _logger.info("event_bus.unsubscribed", sub_id=sub_id)
        return removed

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)

    async def shutdown(self) -> None:
        """Stop the drain loop and drain remaining events."""
        self._running = False
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

        # Drain remaining events
        while not self._pending.empty():
            try:
                event = self._pending.get_nowait()
                await self._distribute(event)
            except asyncio.QueueEmpty:
                break

        _logger.info(
            "event_bus.shutdown",
            remaining_subscribers=len(self._subscribers),
        )

    async def _drain_loop(self) -> None:
        """Background loop that distributes queued events to subscribers."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._pending.get(), timeout=1.0
                )
                await self._distribute(event)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _distribute(self, event: ObserverEvent) -> None:
        """Distribute a single event to all matching subscribers."""
        for sub_id, sub in self._subscribers.items():
            if sub.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                continue
            try:
                if sub.event_filter is not None and not sub.event_filter(event):
                    continue
            except Exception:
                _logger.warning(
                    "event_bus.filter_error",
                    subscriber_id=sub_id,
                    event_type=event.get("event"),
                    exc_info=True,
                )
                continue
            # Bounded deque handles drop-oldest automatically
            sub.queue.append(event)
            try:
                result = sub.callback(event)
                if asyncio.iscoroutine(result):
                    await result
                sub.consecutive_failures = 0
            except Exception:
                sub.consecutive_failures += 1
                _logger.warning(
                    "event_bus.subscriber_error",
                    subscriber_id=sub_id,
                    event_type=event.get("event"),
                    consecutive_failures=sub.consecutive_failures,
                    exc_info=True,
                )
                if sub.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    _logger.error(
                        "event_bus.subscriber_disabled",
                        subscriber_id=sub_id,
                        reason=f"{_MAX_CONSECUTIVE_FAILURES} consecutive failures",
                    )


class _Subscriber:
    """Internal subscriber state."""

    __slots__ = ("callback", "event_filter", "queue", "consecutive_failures")

    def __init__(
        self,
        callback: EventCallback,
        event_filter: EventFilter,
        queue: deque[ObserverEvent],
    ) -> None:
        self.callback = callback
        self.event_filter = event_filter
        self.queue = queue
        self.consecutive_failures: int = 0


__all__ = ["EventBus"]
