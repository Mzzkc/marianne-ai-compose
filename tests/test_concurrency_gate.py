"""#231: tests for ConcurrencyGate — a live-resizable concurrency limiter.

The conductor gated concurrent jobs with an ``asyncio.Semaphore`` that
``apply_config`` *replaced* on SIGHUP reload — orphaning in-flight acquisitions
(they released into the dead object) while the new semaphore started with all
permits free, so lowering the limit while jobs ran immediately over-admitted.

``ConcurrencyGate`` tracks ``_limit`` and ``_acquired`` explicitly and resizes
*in place* via ``set_limit`` — in-flight holders are never affected; the limit
tightens as they drain. The wakeup model is an ``asyncio.Event`` re-check loop:
a woken waiter re-checks and increments the counter itself, synchronously, with
no ``await`` between check and increment — so a cancelled waiter never held a
permit (cancellation-safe by construction, no reservation cleanup to get wrong).
Tradeoff: not strictly FIFO (eventual admission), which job admission does not
require (``asyncio.Semaphore`` isn't FIFO either).

Lab-reviewed by four models — unanimous on a custom in-place limiter; this is
GLM's Event-based variant, chosen for eliminating the cancellation-deadlock hole
the reservation-based designs must handle perfectly.
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.daemon.concurrency import ConcurrencyGate


async def _blocks(coro_fn, timeout: float = 0.15) -> bool:
    """True if awaiting coro_fn() does not complete within timeout."""
    task = asyncio.ensure_future(coro_fn())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return False
    except TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return True


class TestBasics:
    async def test_acquire_release_counting(self) -> None:
        gate = ConcurrencyGate(2)
        await gate.acquire()
        await gate.acquire()
        assert gate.acquired == 2
        assert gate.available == 0
        gate.release()
        assert gate.acquired == 1
        await gate.acquire()
        assert gate.acquired == 2

    async def test_acquire_blocks_at_limit(self) -> None:
        gate = ConcurrencyGate(1)
        await gate.acquire()
        assert await _blocks(gate.acquire)
        gate.release()
        await asyncio.wait_for(gate.acquire(), timeout=0.5)  # now succeeds
        assert gate.acquired == 1

    async def test_release_without_acquire_raises(self) -> None:
        gate = ConcurrencyGate(3)
        with pytest.raises(RuntimeError):
            gate.release()

    async def test_context_manager(self) -> None:
        gate = ConcurrencyGate(1)
        async with gate:
            assert gate.acquired == 1
        assert gate.acquired == 0

    async def test_limit_below_one_rejected(self) -> None:
        with pytest.raises(ValueError):
            ConcurrencyGate(0)


class TestRaise:
    async def test_raise_admits_waiters(self) -> None:
        gate = ConcurrencyGate(1)
        await gate.acquire()  # full
        t1 = asyncio.ensure_future(gate.acquire())
        t2 = asyncio.ensure_future(gate.acquire())
        await asyncio.sleep(0.02)  # let them queue
        assert not t1.done() and not t2.done()
        gate.set_limit(3)
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=0.5)
        assert gate.acquired == 3

    async def test_raise_no_waiters_updates_limit(self) -> None:
        gate = ConcurrencyGate(2)
        await gate.acquire()
        gate.set_limit(5)
        assert gate.limit == 5
        await asyncio.wait_for(gate.acquire(), timeout=0.5)  # immediate
        assert gate.acquired == 2


class TestLower:
    async def test_lower_blocks_new_until_drain(self) -> None:
        gate = ConcurrencyGate(5)
        for _ in range(3):
            await gate.acquire()  # 3 in-flight
        gate.set_limit(2)
        assert gate.limit == 2
        assert gate.acquired == 3  # over-limit, not killed
        # A new admission must block until acquired < 2.
        assert await _blocks(gate.acquire)
        gate.release()  # 3 -> 2 (still not < 2)
        assert await _blocks(gate.acquire)
        gate.release()  # 2 -> 1 (now < 2)
        await asyncio.wait_for(gate.acquire(), timeout=0.5)
        assert gate.acquired == 2

    async def test_lower_does_not_kill_in_flight(self) -> None:
        gate = ConcurrencyGate(5)
        for _ in range(4):
            await gate.acquire()
        gate.set_limit(1)
        # The 4 in-flight holders are unaffected; releasing each succeeds.
        for _ in range(4):
            gate.release()  # must not raise
        assert gate.acquired == 0


class TestNoOrphanedPermits:
    async def test_no_orphan_after_resize(self) -> None:
        gate = ConcurrencyGate(5)
        for _ in range(3):
            await gate.acquire()
        gate.set_limit(10)
        for _ in range(3):
            gate.release()
        assert gate.acquired == 0
        assert gate.available == 10  # NOT 12 or 7
        for _ in range(10):
            await gate.acquire()
        assert await _blocks(gate.acquire)  # 11th blocks

    async def test_repeated_resize_no_leak(self) -> None:
        gate = ConcurrencyGate(5)
        await gate.acquire()
        await gate.acquire()
        gate.set_limit(10)
        gate.set_limit(3)
        gate.set_limit(7)
        gate.set_limit(4)
        gate.release()
        gate.release()
        assert gate.acquired == 0
        assert gate.limit == 4
        for _ in range(4):
            await gate.acquire()
        assert await _blocks(gate.acquire)


class TestCancellation:
    async def test_cancelled_waiter_holds_no_permit(self) -> None:
        gate = ConcurrencyGate(1)
        await gate.acquire()  # full
        waiter = asyncio.ensure_future(gate.acquire())
        await asyncio.sleep(0.02)  # let it block
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert gate.acquired == 1  # cancelled waiter never incremented
        # Gate is not deadlocked: release frees the slot for a new acquirer.
        gate.release()
        await asyncio.wait_for(gate.acquire(), timeout=0.5)
        assert gate.acquired == 1

    async def test_multiple_cancelled_waiters(self) -> None:
        gate = ConcurrencyGate(1)
        await gate.acquire()
        waiters = [asyncio.ensure_future(gate.acquire()) for _ in range(5)]
        await asyncio.sleep(0.02)
        for w in waiters:
            w.cancel()
        for w in waiters:
            with pytest.raises(asyncio.CancelledError):
                await w
        assert gate.acquired == 1
        gate.release()
        await asyncio.wait_for(gate.acquire(), timeout=0.5)


class TestBoundary:
    async def test_set_limit_to_current_acquired(self) -> None:
        gate = ConcurrencyGate(5)
        for _ in range(3):
            await gate.acquire()
        gate.set_limit(3)
        assert await _blocks(gate.acquire)  # 3 >= 3
        gate.release()  # 2 < 3
        await asyncio.wait_for(gate.acquire(), timeout=0.5)

    async def test_set_limit_same_value_noop(self) -> None:
        gate = ConcurrencyGate(5)
        for _ in range(3):
            await gate.acquire()
        gate.set_limit(5)
        assert gate.acquired == 3
        await asyncio.wait_for(gate.acquire(), timeout=0.5)
