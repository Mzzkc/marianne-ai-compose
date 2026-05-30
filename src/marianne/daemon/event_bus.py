"""Async pub/sub event bus for the Marianne daemon.

Routes ObserverEvents from the baton and observer to downstream consumers
(SSE dashboard, learning hub, future webhooks).

Architecture invariant 5 (load-bearing): **the EventBus never blocks
publishers.** A slow or hung subscriber must never stall the baton/observer
that publishes events. EventBus events are *observability* — unlike the baton's
execution inbox (where dropping an event strands a sheet), dropping an event
here under overload is acceptable.

To honour that invariant in practice (not just in the letter of a non-blocking
``publish``), this bus isolates subscribers from each other and from the
publisher:

* ``_pending`` is a **bounded** queue. ``publish()`` uses ``put_nowait`` and
  drops the *newest* event on overflow (atomic — no race with the drain loop's
  ``get()``), so a publisher can never block and memory can never grow without
  bound.
* The single drain loop only **filters and enqueues** events into each
  subscriber's own bounded ``deque`` (drop-oldest, drop-oldest preserves
  recency for live streams) — it never awaits a subscriber callback.
* Each subscriber owns a **worker task** that runs its callback under a
  per-callback timeout. A slow/hung callback blocks only that subscriber.
* A subscriber is **auto-evicted** after ``_MAX_CONSECUTIVE_FAILURES``
  consecutive callback exceptions, callback timeouts, or filter exceptions —
  preventing the subscriber dict from leaking dead SSE clients.
* ``shutdown()`` is **time-bounded**: it cancels the workers and drops any
  remaining observability events rather than awaiting a hung callback (which
  would force SIGKILL and risk state corruption).
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

# Type aliases for subscriber callbacks.
EventFilter = Callable[[ObserverEvent], bool] | None
EventCallback = Callable[[ObserverEvent], Any]

# Consecutive callback/filter failures before a subscriber is auto-evicted.
_MAX_CONSECUTIVE_FAILURES = 10

# Defaults (all overridable via the constructor for testability/tuning).
_DEFAULT_PENDING_MAXSIZE = 10_000
_DEFAULT_CALLBACK_TIMEOUT = 5.0
_DEFAULT_SHUTDOWN_TIMEOUT = 2.0

# Fraction of _pending capacity that triggers a (once-per-crossing) warning.
_HIGH_WATER_RATIO = 0.8

# Throttle interval for the "dropped events" warning (1 in N drops is logged).
_DROP_LOG_INTERVAL = 100


class EventBus:
    """Async pub/sub event bus with per-subscriber isolation.

    Subscribers receive events via callbacks executed in their own worker
    task. A bounded central queue plus per-subscriber bounded deques guarantee
    that a slow or hung subscriber can neither block the publisher nor stall
    other subscribers.

    Usage::

        bus = EventBus(max_queue_size=1000)
        await bus.start()

        sub_id = bus.subscribe(callback=my_handler)
        sub_id = bus.subscribe(
            callback=my_handler,
            event_filter=lambda e: e["event"].startswith("sheet."),
        )

        await bus.publish(event)

        bus.unsubscribe(sub_id)
        await bus.shutdown()
    """

    def __init__(
        self,
        *,
        max_queue_size: int = 1000,
        max_pending_size: int = _DEFAULT_PENDING_MAXSIZE,
        callback_timeout: float = _DEFAULT_CALLBACK_TIMEOUT,
        shutdown_timeout: float = _DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        if max_queue_size < 1:
            raise ValueError(f"max_queue_size must be >= 1, got {max_queue_size}")
        if max_pending_size < 1:
            raise ValueError(f"max_pending_size must be >= 1, got {max_pending_size}")
        if callback_timeout <= 0:
            raise ValueError(f"callback_timeout must be > 0, got {callback_timeout}")
        if shutdown_timeout <= 0:
            raise ValueError(f"shutdown_timeout must be > 0, got {shutdown_timeout}")

        self._max_queue_size = max_queue_size
        self._callback_timeout = callback_timeout
        self._shutdown_timeout = shutdown_timeout
        self._subscribers: dict[str, _Subscriber] = {}
        self._drain_task: asyncio.Task[None] | None = None
        self._pending: asyncio.Queue[ObserverEvent] = asyncio.Queue(
            maxsize=max_pending_size
        )
        self._pending_dropped: int = 0
        self._high_water_logged = False
        self._running = False

    async def start(self) -> None:
        """Start the background drain loop and any pending subscriber workers."""
        if self._running:
            return
        self._running = True
        self._drain_task = asyncio.create_task(
            self._drain_loop(), name="event-bus-drain"
        )
        # Start workers for subscribers registered before start().
        for sub_id, sub in self._subscribers.items():
            if sub.task is None:
                self._start_worker(sub_id, sub)

    async def publish(self, event: ObserverEvent) -> None:
        """Publish an event to all matching subscribers.

        Non-blocking for the publisher (invariant 5). If the central queue is
        full, the *newest* event is dropped — preferred over drop-oldest here
        because it is atomic (no ``get``/``put`` pair racing the drain loop).
        """
        if not self._running:
            return
        try:
            self._pending.put_nowait(event)
        except asyncio.QueueFull:
            self._pending_dropped += 1
            if self._pending_dropped % _DROP_LOG_INTERVAL == 1:
                _logger.warning(
                    "event_bus.pending_full",
                    total_dropped=self._pending_dropped,
                    pending_size=self._pending.qsize(),
                    event_type=event.get("event"),
                )

    def subscribe(
        self,
        callback: EventCallback,
        *,
        event_filter: EventFilter = None,
    ) -> str:
        """Register a subscriber.

        Args:
            callback: Async or sync callable receiving an ObserverEvent. Sync
                callbacks MUST be non-blocking (they run in the event loop);
                async callbacks are bounded by ``callback_timeout``.
            event_filter: Optional predicate. If provided, the subscriber only
                receives events where it returns True.

        Returns:
            Subscription ID for later unsubscribe.
        """
        sub_id = str(uuid.uuid4())
        sub = _Subscriber(
            callback=callback,
            event_filter=event_filter,
            queue=deque(maxlen=self._max_queue_size),
        )
        self._subscribers[sub_id] = sub
        if self._running:
            self._start_worker(sub_id, sub)
        _logger.info("event_bus.subscribed", sub_id=sub_id)
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscriber and cancel its worker.

        Returns:
            True if the subscriber existed and was removed.
        """
        sub = self._subscribers.pop(sub_id, None)
        if sub is None:
            return False
        sub.closing = True
        if sub.task is not None and sub.task is not asyncio.current_task():
            sub.task.cancel()
        _logger.info("event_bus.unsubscribed", sub_id=sub_id)
        return True

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)

    async def shutdown(self) -> None:
        """Stop the bus within a bounded time, dropping remaining events.

        Observability events are droppable; shutdown must never await a hung
        subscriber callback (which would force SIGKILL).
        """
        self._running = False

        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

        # Cancel all subscriber workers, bounded by shutdown_timeout.
        tasks: list[asyncio.Task[None]] = []
        for sub in self._subscribers.values():
            sub.closing = True
            if sub.task is not None:
                sub.task.cancel()
                tasks.append(sub.task)
        if tasks:
            await asyncio.wait(tasks, timeout=self._shutdown_timeout)

        dropped = self._pending.qsize()
        self._subscribers.clear()

        _logger.info(
            "event_bus.shutdown",
            dropped_pending=dropped,
            total_dropped=self._pending_dropped,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _start_worker(self, sub_id: str, sub: _Subscriber) -> None:
        sub.task = asyncio.create_task(
            self._subscriber_loop(sub_id, sub),
            name=f"event-bus-sub-{sub_id[:8]}",
        )

    async def _drain_loop(self) -> None:
        """Read events from the central queue and fan them out (non-blocking)."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._pending.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            self._check_high_water()
            self._distribute(event)

    def _check_high_water(self) -> None:
        qsize = self._pending.qsize()
        threshold = self._pending.maxsize * _HIGH_WATER_RATIO
        if qsize >= threshold and not self._high_water_logged:
            self._high_water_logged = True
            _logger.warning(
                "event_bus.high_water_mark",
                pending_size=qsize,
                max_size=self._pending.maxsize,
            )
        elif qsize < threshold:
            self._high_water_logged = False

    def _distribute(self, event: ObserverEvent) -> None:
        """Filter the event and enqueue it to each matching subscriber.

        Never awaits — only the per-subscriber worker awaits the callback. A
        snapshot (``list(...)``) makes it safe for a callback to mutate the
        subscriber dict (e.g. self-unsubscribe).
        """
        to_evict: list[str] = []
        for sub_id, sub in list(self._subscribers.items()):
            if sub.event_filter is not None:
                try:
                    matched = sub.event_filter(event)
                except Exception:
                    if self._record_failure(sub_id, sub, "filter_error", event):
                        to_evict.append(sub_id)
                    continue
                if not matched:
                    continue
            # Bounded deque: a full deque drops its oldest event on append.
            if len(sub.queue) == sub.queue.maxlen:
                sub.dropped += 1
            sub.queue.append(event)
            sub.wakeup.set()

        for sub_id in to_evict:
            self._evict(sub_id, reason="filter errors")

    async def _subscriber_loop(self, sub_id: str, sub: _Subscriber) -> None:
        """Drain a single subscriber's deque, running its callback in isolation."""
        while self._running and not sub.closing:
            try:
                await sub.wakeup.wait()
            except asyncio.CancelledError:
                break
            sub.wakeup.clear()
            while sub.queue:
                event = sub.queue.popleft()
                try:
                    result = sub.callback(event)
                    if asyncio.iscoroutine(result):
                        await asyncio.wait_for(
                            result, timeout=self._callback_timeout
                        )
                    sub.consecutive_failures = 0
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    if self._record_failure(sub_id, sub, "callback_timeout", event):
                        return
                except Exception:
                    if self._record_failure(sub_id, sub, "callback_error", event):
                        return

    def _record_failure(
        self,
        sub_id: str,
        sub: _Subscriber,
        kind: str,
        event: ObserverEvent,
    ) -> bool:
        """Increment a subscriber's failure count; return True if it should evict.

        Eviction itself is performed by the caller (``_distribute`` collects
        IDs to evict after iterating; ``_subscriber_loop`` returns to end its
        own task) so a subscriber never cancels its own task mid-iteration.
        """
        sub.consecutive_failures += 1
        _logger.warning(
            f"event_bus.{kind}",
            subscriber_id=sub_id,
            event_type=event.get("event"),
            consecutive_failures=sub.consecutive_failures,
        )
        if sub.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            # _distribute evicts via the returned flag; the worker self-evicts
            # by popping itself here (its task is already ending via `return`).
            if sub.task is asyncio.current_task():
                self._subscribers.pop(sub_id, None)
                _logger.error(
                    "event_bus.subscriber_evicted",
                    subscriber_id=sub_id,
                    reason=kind,
                )
            return True
        return False

    def _evict(self, sub_id: str, *, reason: str) -> None:
        sub = self._subscribers.pop(sub_id, None)
        if sub is None:
            return
        sub.closing = True
        if sub.task is not None and sub.task is not asyncio.current_task():
            sub.task.cancel()
        _logger.error(
            "event_bus.subscriber_evicted",
            subscriber_id=sub_id,
            reason=reason,
        )


class _Subscriber:
    """Internal subscriber state: a callback, its buffer, and its worker."""

    __slots__ = (
        "callback",
        "event_filter",
        "queue",
        "wakeup",
        "task",
        "consecutive_failures",
        "closing",
        "dropped",
    )

    def __init__(
        self,
        callback: EventCallback,
        event_filter: EventFilter,
        queue: deque[ObserverEvent],
    ) -> None:
        self.callback = callback
        self.event_filter = event_filter
        self.queue = queue
        self.wakeup = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.consecutive_failures: int = 0
        self.closing: bool = False
        self.dropped: int = 0


__all__ = ["EventBus"]
