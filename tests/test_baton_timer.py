"""Tests for the baton's timer wheel — all timing flows through here.

The timer wheel is a priority queue of (fire_at, event) pairs with a
background drain task that sleeps until the next timer fires, then puts
the event into the baton's inbox.

Tests cover: scheduling, cancellation, ordering, concurrent timers,
edge cases (zero delay, negative delay, many timers), and the background
drain task lifecycle.

TDD: tests written before implementation. Red first, then green.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from marianne.daemon.baton.events import (
    CronTick,
    DispatchRetry,
    JobTimeout,
    PacingComplete,
    RateLimitExpired,
    RetryDue,
    StaleCheck,
)
from marianne.daemon.baton.timer import TimerHandle, TimerWheel


# =============================================================================
# Construction & basic interface
# =============================================================================


class TestTimerWheelConstruction:
    """TimerWheel can be created with an asyncio.Queue inbox."""

    async def test_creates_with_queue(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        assert wheel is not None

    async def test_has_no_pending_timers_initially(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        assert wheel.pending_count == 0


# =============================================================================
# Scheduling
# =============================================================================


class TestSchedule:
    """schedule() adds timers to the wheel and returns handles."""

    async def test_schedule_returns_handle(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        event = RetryDue(job_id="j1", sheet_num=1)
        handle = wheel.schedule(5.0, event)
        assert isinstance(handle, TimerHandle)
        assert wheel.pending_count == 1

    async def test_schedule_zero_delay(self) -> None:
        """Zero delay should be accepted — fires immediately on next tick."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        event = DispatchRetry()
        handle = wheel.schedule(0.0, event)
        assert isinstance(handle, TimerHandle)
        assert wheel.pending_count == 1

    async def test_schedule_negative_delay_treated_as_immediate(self) -> None:
        """Negative delay is clamped to 0 — fires immediately."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        event = DispatchRetry()
        handle = wheel.schedule(-1.0, event)
        assert isinstance(handle, TimerHandle)
        assert handle.fire_at <= time.monotonic()

    async def test_schedule_multiple_timers(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        wheel.schedule(1.0, RetryDue(job_id="j1", sheet_num=1))
        wheel.schedule(2.0, RetryDue(job_id="j1", sheet_num=2))
        wheel.schedule(3.0, RetryDue(job_id="j1", sheet_num=3))
        assert wheel.pending_count == 3

    async def test_handle_contains_fire_time_and_event(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        event = JobTimeout(job_id="j1")
        before = time.monotonic()
        handle = wheel.schedule(10.0, event)
        after = time.monotonic()
        assert handle.fire_at >= before + 10.0
        assert handle.fire_at <= after + 10.0
        assert handle.event is event


# =============================================================================
# Cancellation
# =============================================================================


class TestCancel:
    """cancel() removes a scheduled timer before it fires."""

    async def test_cancel_returns_true_for_pending(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        handle = wheel.schedule(10.0, RetryDue(job_id="j1", sheet_num=1))
        assert wheel.cancel(handle) is True

    async def test_cancel_returns_false_for_already_cancelled(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        handle = wheel.schedule(10.0, RetryDue(job_id="j1", sheet_num=1))
        wheel.cancel(handle)
        assert wheel.cancel(handle) is False

    async def test_cancelled_timer_does_not_fire(self) -> None:
        """Cancelled timers are skipped when their fire_at arrives."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        handle = wheel.schedule(0.0, RetryDue(job_id="j1", sheet_num=1))
        wheel.cancel(handle)

        # Run the wheel briefly — nothing should appear in inbox
        task = asyncio.create_task(wheel.run())
        try:
            await asyncio.sleep(0.1)
            assert inbox.empty()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_cancel_does_not_affect_other_timers(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        h1 = wheel.schedule(0.01, RetryDue(job_id="j1", sheet_num=1))
        wheel.schedule(0.01, RetryDue(job_id="j1", sheet_num=2))
        wheel.cancel(h1)

        task = asyncio.create_task(wheel.run())
        try:
            event = await asyncio.wait_for(inbox.get(), timeout=2.0)
            assert isinstance(event, RetryDue)
            assert event.sheet_num == 2
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# =============================================================================
# Firing behavior
# =============================================================================


class TestFiring:
    """The background run() task fires events into the inbox at the right time."""

    async def test_fires_event_after_delay(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        event = RetryDue(job_id="j1", sheet_num=1)
        wheel.schedule(0.05, event)

        task = asyncio.create_task(wheel.run())
        try:
            received = await asyncio.wait_for(inbox.get(), timeout=2.0)
            assert received is event
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_fires_zero_delay_immediately(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        event = DispatchRetry()
        wheel.schedule(0.0, event)

        task = asyncio.create_task(wheel.run())
        try:
            received = await asyncio.wait_for(inbox.get(), timeout=2.0)
            assert received is event
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_fires_multiple_in_order(self) -> None:
        """Events fire in fire_at order, not insertion order."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        # Schedule in reverse order — 3rd, 2nd, 1st
        e3 = StaleCheck(job_id="j1", sheet_num=3)
        e1 = RetryDue(job_id="j1", sheet_num=1)
        e2 = PacingComplete(job_id="j1")

        wheel.schedule(0.15, e3)
        wheel.schedule(0.05, e1)
        wheel.schedule(0.10, e2)

        task = asyncio.create_task(wheel.run())
        try:
            r1 = await asyncio.wait_for(inbox.get(), timeout=2.0)
            r2 = await asyncio.wait_for(inbox.get(), timeout=2.0)
            r3 = await asyncio.wait_for(inbox.get(), timeout=2.0)

            assert r1 is e1  # earliest fire_at
            assert r2 is e2
            assert r3 is e3  # latest fire_at
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_fires_simultaneous_timers(self) -> None:
        """Multiple timers with the same delay all fire."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        events = [
            RetryDue(job_id="j1", sheet_num=i)
            for i in range(5)
        ]
        for e in events:
            wheel.schedule(0.01, e)

        task = asyncio.create_task(wheel.run())
        try:
            received = []
            for _ in range(5):
                r = await asyncio.wait_for(inbox.get(), timeout=2.0)
                received.append(r)
            assert len(received) == 5
            # All are RetryDue events
            assert all(isinstance(r, RetryDue) for r in received)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_pending_count_decreases_after_fire(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        wheel.schedule(0.01, RetryDue(job_id="j1", sheet_num=1))
        assert wheel.pending_count == 1

        task = asyncio.create_task(wheel.run())
        try:
            await asyncio.wait_for(inbox.get(), timeout=2.0)
            # Give the wheel a tick to update its internal state
            await asyncio.sleep(0.05)
            assert wheel.pending_count == 0
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# =============================================================================
# Dynamic scheduling (timers added while running)
# =============================================================================


class TestDynamicScheduling:
    """Timers can be added while the wheel is already running."""

    async def test_schedule_while_running(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        task = asyncio.create_task(wheel.run())
        try:
            # Schedule after the wheel is running
            await asyncio.sleep(0.01)
            event = RetryDue(job_id="j1", sheet_num=1)
            wheel.schedule(0.05, event)

            received = await asyncio.wait_for(inbox.get(), timeout=2.0)
            assert received is event
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_schedule_earlier_timer_while_sleeping(self) -> None:
        """Adding an earlier timer wakes the drain task to recalculate."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        # Schedule a long timer first
        wheel.schedule(10.0, JobTimeout(job_id="j1"))

        task = asyncio.create_task(wheel.run())
        try:
            await asyncio.sleep(0.01)
            # Now schedule a much shorter timer — should wake up and fire it
            early = RetryDue(job_id="j1", sheet_num=1)
            wheel.schedule(0.05, early)

            received = await asyncio.wait_for(inbox.get(), timeout=2.0)
            assert received is early
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# =============================================================================
# Run lifecycle
# =============================================================================


class TestRunLifecycle:
    """The run() coroutine is well-behaved under cancellation."""

    async def test_cancellation_stops_cleanly(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        wheel.schedule(100.0, RetryDue(job_id="j1", sheet_num=1))

        task = asyncio.create_task(wheel.run())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_runs_indefinitely_when_empty(self) -> None:
        """An empty wheel sleeps until a timer is added or cancelled."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        task = asyncio.create_task(wheel.run())
        await asyncio.sleep(0.1)
        assert not task.done()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_shutdown_drains_pending(self) -> None:
        """shutdown() fires all pending timers immediately and stops."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        wheel.schedule(100.0, RetryDue(job_id="j1", sheet_num=1))
        wheel.schedule(100.0, RetryDue(job_id="j1", sheet_num=2))

        # Shutdown should put all pending events into inbox
        await wheel.shutdown()
        assert inbox.qsize() == 2


# =============================================================================
# Snapshot for persistence (restart recovery)
# =============================================================================


class TestSnapshot:
    """The wheel can export/import its state for restart recovery."""

    async def test_snapshot_returns_pending_timers(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        wheel.schedule(10.0, RetryDue(job_id="j1", sheet_num=1))
        wheel.schedule(20.0, JobTimeout(job_id="j2"))

        snapshot = wheel.snapshot()
        assert len(snapshot) == 2

    async def test_snapshot_excludes_cancelled(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        h1 = wheel.schedule(10.0, RetryDue(job_id="j1", sheet_num=1))
        wheel.schedule(20.0, JobTimeout(job_id="j2"))
        wheel.cancel(h1)

        snapshot = wheel.snapshot()
        assert len(snapshot) == 1

    async def test_snapshot_contains_fire_at_and_event(self) -> None:
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        event = RetryDue(job_id="j1", sheet_num=1)
        handle = wheel.schedule(10.0, event)

        snapshot = wheel.snapshot()
        assert len(snapshot) == 1
        fire_at, snap_event = snapshot[0]
        assert fire_at == handle.fire_at
        assert snap_event is event


# =============================================================================
# Adversarial / edge cases
# =============================================================================


class TestAdversarial:
    """Edge cases and adversarial input."""

    async def test_many_timers_performance(self) -> None:
        """Scheduling 1000 timers doesn't blow up."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        for i in range(1000):
            wheel.schedule(float(i), RetryDue(job_id="j1", sheet_num=i))
        assert wheel.pending_count == 1000

    async def test_cancel_after_fire_returns_false(self) -> None:
        """Cancelling a timer that already fired returns False."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        handle = wheel.schedule(0.01, DispatchRetry())

        task = asyncio.create_task(wheel.run())
        try:
            await asyncio.wait_for(inbox.get(), timeout=2.0)
            await asyncio.sleep(0.05)
            assert wheel.cancel(handle) is False
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_various_event_types(self) -> None:
        """Timer wheel handles all timer-related event types."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        events = [
            RetryDue(job_id="j1", sheet_num=1),
            StaleCheck(job_id="j1", sheet_num=2),
            CronTick(entry_name="nightly", score_path="scores/nightly.yaml"),
            JobTimeout(job_id="j2"),
            PacingComplete(job_id="j3"),
            RateLimitExpired(instrument="claude-code"),
            DispatchRetry(),
        ]

        for i, e in enumerate(events):
            wheel.schedule(0.01 * (i + 1), e)

        task = asyncio.create_task(wheel.run())
        try:
            received = []
            for _ in range(len(events)):
                r = await asyncio.wait_for(inbox.get(), timeout=5.0)
                received.append(r)
            assert len(received) == len(events)
            # Verify types — they should arrive in order
            assert isinstance(received[0], RetryDue)
            assert isinstance(received[1], StaleCheck)
            assert isinstance(received[2], CronTick)
            assert isinstance(received[3], JobTimeout)
            assert isinstance(received[4], PacingComplete)
            assert isinstance(received[5], RateLimitExpired)
            assert isinstance(received[6], DispatchRetry)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_large_delay_does_not_block_earlier_additions(self) -> None:
        """A timer with delay=3600 doesn't prevent a timer with delay=0.01."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)
        wheel.schedule(3600.0, JobTimeout(job_id="j1"))

        task = asyncio.create_task(wheel.run())
        try:
            await asyncio.sleep(0.01)
            fast = DispatchRetry()
            wheel.schedule(0.01, fast)

            received = await asyncio.wait_for(inbox.get(), timeout=2.0)
            assert received is fast
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
