"""Timer wheel — all timing in the baton flows through here.

The timer wheel is a priority queue of future events. A background drain
task sleeps until the next timer fires, puts the event into the baton's
inbox, and repeats. Components never call ``asyncio.sleep()`` for scheduling
purposes — they schedule a timer instead.

Eight timing responsibilities converge here:
- Retry backoff delays
- Rate limit recovery waits
- Circuit breaker recovery timers
- Stale detection idle timeouts
- Inter-sheet pacing delays
- Concert cooldown between chained jobs
- Job wall-clock timeouts
- Cron ticks

Design:
- Priority queue via ``heapq`` — O(log n) schedule/fire.
- Cancelled timers are lazily skipped on fire (tombstone pattern),
  not eagerly removed — O(1) cancel.
- The drain task is woken by ``asyncio.Event`` when timers change,
  so adding an earlier timer doesn't wait for the previous sleep.
- All public methods are synchronous (no await needed to schedule).
  Only ``run()`` and ``shutdown()`` are async.

See: ``docs/plans/2026-03-26-baton-design.md`` — Timer Wheel section.
"""

from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Any

from marianne.core.logging import get_logger
from marianne.daemon.baton.events import BatonEvent

_logger = get_logger("daemon.baton.timer")

# Monotonically increasing counter to break ties in heapq when fire_at
# is identical. Without this, heapq would try to compare BatonEvent
# dataclasses, which may not be orderable.
_counter: int = 0


def _next_seq() -> int:
    """Return a monotonically increasing sequence number for heap ordering."""
    global _counter  # noqa: PLW0603
    _counter += 1
    return _counter


@dataclass(frozen=True)
class TimerHandle:
    """Opaque handle returned by ``schedule()``. Used for cancellation.

    Attributes:
        fire_at: Monotonic time when the event should fire.
        event: The BatonEvent to deliver to the inbox.
    """

    fire_at: float
    event: BatonEvent
    _seq: int = field(default_factory=_next_seq, repr=False, compare=False)


@dataclass(order=True)
class _TimerEntry:
    """Internal heap entry. Ordered by (fire_at, seq) for deterministic pop."""

    fire_at: float
    seq: int = field(compare=True)
    handle: TimerHandle = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class TimerWheel:
    """Priority queue of future events with a background drain task.

    Usage::

        inbox = asyncio.Queue()
        wheel = TimerWheel(inbox)

        # Schedule a retry in 30 seconds
        handle = wheel.schedule(
            30.0,
            RetryDue(job_id="j1", sheet_num=5, registration_generation=1),
        )

        # Cancel if no longer needed
        wheel.cancel(handle)

        # Run the drain task (usually as asyncio.create_task(wheel.run()))
        await wheel.run()  # blocks forever, fires events into inbox

    Args:
        inbox: The asyncio.Queue that receives fired events. This is
            the baton's event inbox — the same queue that receives
            musician results, external commands, etc.
    """

    def __init__(self, inbox: asyncio.Queue[Any]) -> None:
        self._inbox = inbox
        self._heap: list[_TimerEntry] = []
        self._cancelled: set[int] = set()  # set of cancelled _seq values
        self._wake = asyncio.Event()
        self._shutting_down = False

    @property
    def pending_count(self) -> int:
        """Number of non-cancelled timers in the wheel."""
        return sum(
            1 for entry in self._heap if entry.handle._seq not in self._cancelled
        )

    def schedule(self, delay_seconds: float, event: BatonEvent) -> TimerHandle:
        """Schedule an event to fire after ``delay_seconds``.

        Args:
            delay_seconds: Seconds from now. Clamped to >= 0.
            event: The BatonEvent to deliver when the timer fires.

        Returns:
            A TimerHandle that can be passed to ``cancel()``.
        """
        delay = max(0.0, delay_seconds)
        fire_at = time.monotonic() + delay
        handle = TimerHandle(fire_at=fire_at, event=event)
        entry = _TimerEntry(fire_at=fire_at, seq=handle._seq, handle=handle)
        heapq.heappush(self._heap, entry)

        _logger.debug(
            "timer.scheduled",
            extra={
                "event_type": type(event).__name__,
                "delay_seconds": delay,
                "fire_at": fire_at,
                "pending": self.pending_count,
            },
        )

        # Wake the drain task — it may need to recalculate its sleep
        self._wake.set()
        return handle

    def cancel(self, handle: TimerHandle) -> bool:
        """Cancel a scheduled timer.

        Args:
            handle: The handle returned by ``schedule()``.

        Returns:
            True if the timer was pending and is now cancelled.
            False if it was already cancelled or already fired.
        """
        seq = handle._seq
        if seq in self._cancelled:
            return False

        # Check if it's still in the heap (not yet fired)
        for entry in self._heap:
            if entry.handle._seq == seq and not entry.cancelled:
                entry.cancelled = True
                self._cancelled.add(seq)
                _logger.debug(
                    "timer.cancelled",
                    extra={
                        "event_type": type(handle.event).__name__,
                        "fire_at": handle.fire_at,
                    },
                )
                return True

        return False

    def snapshot(self) -> list[tuple[float, BatonEvent]]:
        """Export pending (non-cancelled) timers for persistence.

        Returns a list of (fire_at, event) tuples — the data needed to
        reconstruct the timer wheel after a restart.
        """
        return [
            (entry.fire_at, entry.handle.event)
            for entry in self._heap
            if entry.handle._seq not in self._cancelled
        ]

    async def run(self) -> None:
        """Background drain task — fire timers into the inbox.

        This coroutine runs indefinitely. Cancel the task to stop it.
        It sleeps until the next timer is due (or a wake signal arrives),
        fires all due timers, and repeats.
        """
        while True:
            # Skip cancelled entries at the front of the heap
            while self._heap and self._heap[0].handle._seq in self._cancelled:
                heapq.heappop(self._heap)

            if not self._heap:
                # No timers — sleep until a timer is added
                self._wake.clear()
                await self._wake.wait()
                continue

            next_fire = self._heap[0].fire_at
            now = time.monotonic()

            if next_fire <= now:
                # Timer is due — pop and fire
                entry = heapq.heappop(self._heap)
                if entry.handle._seq in self._cancelled:
                    # Was cancelled between check and pop — skip
                    self._cancelled.discard(entry.handle._seq)
                    continue

                self._cancelled.discard(entry.handle._seq)
                await self._inbox.put(entry.handle.event)

                _logger.debug(
                    "timer.fired",
                    extra={
                        "event_type": type(entry.handle.event).__name__,
                        "fire_at": entry.fire_at,
                        "latency_ms": (time.monotonic() - entry.fire_at) * 1000,
                    },
                )
            else:
                # Sleep until next timer or wake signal
                sleep_for = next_fire - now
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=sleep_for)
                except TimeoutError:
                    pass  # Timer is due, loop will fire it

    async def shutdown(self) -> None:
        """Fire all pending timers immediately and stop.

        Used during graceful shutdown — ensures no events are lost.
        Events are placed into the inbox in fire_at order.
        """
        self._shutting_down = True

        # Drain heap in order, skipping cancelled
        while self._heap:
            entry = heapq.heappop(self._heap)
            if entry.handle._seq not in self._cancelled:
                await self._inbox.put(entry.handle.event)

        _logger.debug("timer.shutdown_complete")
