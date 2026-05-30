"""Adversarial concurrency tests for EventBus resource bounds + shutdown (#220, #228).

A 4-model thinking-lab review (Opus 4.7 / Gemini 3.1 Pro / GLM 5.1 / GPT-5.5)
converged on a single root cause: the original ``_distribute`` awaited each
subscriber callback *serially in the single drain loop*, so one hung subscriber
stalled the whole pipeline and let the unbounded ``_pending`` queue grow until
OOM — violating architecture invariant 5 ("the EventBus never blocks
publishers") in practice even though ``publish()`` itself returned.

The vetted design these tests pin down:

* ``_pending`` is a *bounded* ``asyncio.Queue``; ``publish()`` uses
  ``put_nowait`` and drops the **newest** event on overflow (atomic, no race
  with the drain loop's ``get()``), bumping ``_pending_dropped``.
* Each subscriber owns a bounded ``deque`` + a worker ``asyncio.Task``. The
  central drain loop only filters and enqueues (non-blocking); the worker runs
  the callback under a per-callback ``asyncio.wait_for`` timeout. A slow/hung
  subscriber blocks only *itself*.
* A subscriber is **auto-evicted** (removed from the dict, worker cancelled)
  after ``_MAX_CONSECUTIVE_FAILURES`` — counting callback exceptions, callback
  timeouts, *and* filter exceptions. Transient failures reset on success.
* ``shutdown()`` is time-bounded: it cancels the workers and drops any
  remaining observability events rather than awaiting a hung callback.

All tests are deterministic: synchronisation is via ``asyncio.Event``; every
wait is wrapped in ``asyncio.wait_for`` purely as a *test guard* (the assertion
succeeds the instant the condition holds, never on a fixed delay).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from marianne.daemon.event_bus import _MAX_CONSECUTIVE_FAILURES, EventBus
from marianne.daemon.types import ObserverEvent

_GUARD = 2.0  # seconds — generous test guard, not a timing assertion


def _evt(event: str = "test.event", sheet_num: int = 0) -> ObserverEvent:
    return ObserverEvent(
        job_id="j1",
        sheet_num=sheet_num,
        event=event,
        data=None,
        timestamp=0.0,
    )


async def _until(cond: Callable[[], bool], *, guard: float = _GUARD) -> None:
    """Wait until ``cond()`` is true, bounded by a test guard timeout."""

    async def _spin() -> None:
        while not cond():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_spin(), timeout=guard)


class TestNonBlockingInvariant:
    async def test_publish_returns_with_hung_subscriber(self) -> None:
        """Invariant 5: publish() must not block behind a hung subscriber."""
        bus = EventBus()
        await bus.start()
        blocker = asyncio.Event()

        async def hung(_e: ObserverEvent) -> None:
            await blocker.wait()

        bus.subscribe(hung)
        try:
            # Each publish must return promptly even though the subscriber's
            # callback never completes.
            for i in range(50):
                await asyncio.wait_for(bus.publish(_evt(f"e.{i}")), timeout=_GUARD)
        finally:
            blocker.set()
            await bus.shutdown()

    async def test_hung_subscriber_does_not_block_others(self) -> None:
        """A hung subscriber must not prevent delivery to a healthy one."""
        bus = EventBus()
        await bus.start()
        blocker = asyncio.Event()
        got = asyncio.Event()

        async def hung(_e: ObserverEvent) -> None:
            await blocker.wait()

        def fast(_e: ObserverEvent) -> None:
            got.set()

        bus.subscribe(hung)
        bus.subscribe(fast)
        try:
            await bus.publish(_evt("ping"))
            # The healthy subscriber receives the event while the other hangs.
            await asyncio.wait_for(got.wait(), timeout=_GUARD)
            assert got.is_set()
        finally:
            blocker.set()
            await bus.shutdown()


class TestBoundedPending:
    async def test_pending_drops_oldest_when_full(self) -> None:
        """publish() must drop the OLDEST event (not block) when _pending is full.

        Per architecture.yaml's "drop-oldest backpressure: slow consumers lose
        old events rather than blocking the system." With the drain loop never
        started, _pending fills to max_pending_size and subsequent publishes
        evict the oldest in-flight event. The retained events are therefore the
        NEWEST ones, and the dropped count is exact.
        """
        bus = EventBus(max_pending_size=3)
        # Deliberately do NOT start() — nothing drains _pending.
        bus._running = True  # allow publish() to accept events
        try:
            for i in range(10):
                await bus.publish(_evt(f"e.{i}"))
            assert bus._pending.qsize() == 3
            assert bus._pending_dropped == 7
            # Newest retained (drop-oldest): the last three events survive.
            retained = [bus._pending.get_nowait()["event"] for _ in range(3)]
            assert retained == ["e.7", "e.8", "e.9"]
        finally:
            bus._running = False

    async def test_publish_noop_when_not_running(self) -> None:
        bus = EventBus()
        # Never started; publish is a silent no-op.
        await bus.publish(_evt("ignored"))
        assert bus._pending.qsize() == 0


class TestAutoEviction:
    async def test_evicts_after_consecutive_exceptions(self) -> None:
        bus = EventBus()
        await bus.start()

        def boom(_e: ObserverEvent) -> None:
            raise RuntimeError("broken")

        sub_id = bus.subscribe(boom)
        assert bus.subscriber_count == 1
        try:
            for i in range(_MAX_CONSECUTIVE_FAILURES + 2):
                await bus.publish(_evt(f"e.{i}"))
            await _until(lambda: bus.subscriber_count == 0)
            assert bus.unsubscribe(sub_id) is False  # already gone
        finally:
            await bus.shutdown()

    async def test_transient_failures_do_not_evict(self) -> None:
        bus = EventBus()
        await bus.start()
        seen = 0
        last = asyncio.Event()

        def flaky(_e: ObserverEvent) -> None:
            nonlocal seen
            seen += 1
            if seen >= 9:
                last.set()  # signal completion even on the failing 9th call
            if seen % 3 == 0:
                raise RuntimeError("transient")

        bus.subscribe(flaky)
        try:
            for i in range(9):
                await bus.publish(_evt(f"e.{i}"))
            await asyncio.wait_for(last.wait(), timeout=_GUARD)
            # 3 failures among 9, never 10 consecutive → still subscribed.
            assert bus.subscriber_count == 1
        finally:
            await bus.shutdown()

    async def test_callback_timeout_counts_as_failure_and_evicts(self) -> None:
        bus = EventBus(callback_timeout=0.02)
        await bus.start()
        blocker = asyncio.Event()

        async def hung(_e: ObserverEvent) -> None:
            await blocker.wait()

        sub_id = bus.subscribe(hung)
        try:
            for i in range(_MAX_CONSECUTIVE_FAILURES + 2):
                await bus.publish(_evt(f"e.{i}"))
            await _until(lambda: bus.subscriber_count == 0)
            assert bus.unsubscribe(sub_id) is False
        finally:
            blocker.set()
            await bus.shutdown()

    async def test_filter_exception_counts_toward_eviction(self) -> None:
        """A persistently-raising filter is a broken subscriber → evicted.

        Otherwise the callback never runs, consecutive_failures never moves,
        and the dead subscriber leaks forever (GPT-5.5 finding).
        """
        bus = EventBus()
        await bus.start()

        def bad_filter(_e: ObserverEvent) -> bool:
            raise ValueError("filter bug")

        sub_id = bus.subscribe(lambda _e: None, event_filter=bad_filter)
        try:
            for i in range(_MAX_CONSECUTIVE_FAILURES + 2):
                await bus.publish(_evt(f"e.{i}"))
            await _until(lambda: bus.subscriber_count == 0)
            assert bus.unsubscribe(sub_id) is False
        finally:
            await bus.shutdown()


class TestNormalDelivery:
    async def test_delivers_to_matching_subscriber(self) -> None:
        bus = EventBus()
        await bus.start()
        received: list[str] = []
        done = asyncio.Event()

        def handler(e: ObserverEvent) -> None:
            received.append(e["event"])
            done.set()

        bus.subscribe(handler)
        try:
            await bus.publish(_evt("hello"))
            await asyncio.wait_for(done.wait(), timeout=_GUARD)
            assert received == ["hello"]
        finally:
            await bus.shutdown()

    async def test_filter_restricts_delivery(self) -> None:
        bus = EventBus()
        await bus.start()
        received: list[str] = []
        got_match = asyncio.Event()

        def handler(e: ObserverEvent) -> None:
            received.append(e["event"])
            if e["event"] == "sheet.done":
                got_match.set()

        bus.subscribe(
            handler,
            event_filter=lambda e: e["event"].startswith("sheet."),
        )
        try:
            await bus.publish(_evt("observer.tick"))  # filtered out
            await bus.publish(_evt("sheet.done"))  # matches
            await asyncio.wait_for(got_match.wait(), timeout=_GUARD)
            assert "observer.tick" not in received
            assert "sheet.done" in received
        finally:
            await bus.shutdown()

    async def test_async_callback_is_awaited(self) -> None:
        bus = EventBus()
        await bus.start()
        done = asyncio.Event()

        async def handler(_e: ObserverEvent) -> None:
            done.set()

        bus.subscribe(handler)
        try:
            await bus.publish(_evt("async.event"))
            await asyncio.wait_for(done.wait(), timeout=_GUARD)
        finally:
            await bus.shutdown()


class TestShutdown:
    async def test_shutdown_bounded_with_hung_subscriber(self) -> None:
        """#228: a hung callback must not make shutdown() hang."""
        bus = EventBus(shutdown_timeout=0.2)
        await bus.start()
        entered = asyncio.Event()

        async def hung(_e: ObserverEvent) -> None:
            entered.set()
            await asyncio.Event().wait()  # never completes

        bus.subscribe(hung)
        await bus.publish(_evt("e.0"))
        await asyncio.wait_for(entered.wait(), timeout=_GUARD)
        # Shutdown must return well within the guard despite the hung callback.
        await asyncio.wait_for(bus.shutdown(), timeout=_GUARD)
        assert bus.subscriber_count == 0

    async def test_shutdown_drops_remaining_pending(self) -> None:
        bus = EventBus()
        await bus.start()
        # No subscribers: events sit in _pending / are dropped on shutdown.
        for i in range(50):
            await bus.publish(_evt(f"e.{i}"))
        await asyncio.wait_for(bus.shutdown(), timeout=_GUARD)

    async def test_unsubscribe_is_idempotent(self) -> None:
        bus = EventBus()
        await bus.start()
        sub_id = bus.subscribe(lambda _e: None)
        try:
            assert bus.unsubscribe(sub_id) is True
            assert bus.unsubscribe(sub_id) is False
        finally:
            await bus.shutdown()
