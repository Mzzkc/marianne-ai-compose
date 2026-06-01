"""Live-resizable concurrency limiter for the conductor (#231).

``ConcurrencyGate`` is a counting gate with a **mutable limit**. It exists
because the conductor must adjust ``max_concurrent_jobs`` on a SIGHUP config
reload *without* orphaning in-flight job acquisitions.

The previous approach replaced the ``asyncio.Semaphore`` object on reload. That
is a broken accounting model: in-flight jobs hold the *old* object and release
into it, while the *new* object starts with all permits free and no knowledge of
the running jobs — so lowering the limit immediately over-admits, and the error
compounds across reloads. ("Single-threaded asyncio" prevents data races on the
attribute, not this semantic orphaning.)

This gate tracks ``_limit`` and ``_acquired`` explicitly and resizes **in place**
via :meth:`set_limit`. In-flight holders are never affected — the limit tightens
as they drain (a lower never kills running work; it only blocks new admissions
until ``_acquired`` falls below the new limit).

Wakeup model: an :class:`asyncio.Event` re-check loop. A woken waiter re-checks
``_acquired < _limit`` and increments the counter **itself, synchronously**, with
no ``await`` between the check and the increment. Two consequences:

- **Cancellation-safe by construction.** A waiter cancelled at ``await
  wait()`` never incremented ``_acquired`` (the increment lives in the branch
  that returns), so there is no reserved permit to reclaim — no cleanup logic to
  get wrong, and no granted-then-cancelled deadlock hole.
- **Not strictly FIFO** (eventual admission). On each release/resize all waiters
  wake and re-check; whichever the loop schedules first wins. Job admission does
  not require FIFO (``asyncio.Semaphore`` itself is not FIFO), and the herd is
  bounded by ``max_concurrent_jobs``, so the re-check cost is negligible.

.. warning::
   Relies on single-threaded asyncio: the no-``await``-between-check-and-clear
   invariant is what prevents lost wakeups. Do **not** call from threads or
   executors.
"""

from __future__ import annotations

import asyncio


class ConcurrencyGate:
    """An async concurrency limiter whose limit can be changed at runtime."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        self._limit = limit
        self._acquired = 0
        self._wake = asyncio.Event()

    @property
    def limit(self) -> int:
        """The current configured limit."""
        return self._limit

    @property
    def acquired(self) -> int:
        """How many permits are currently held (may exceed limit after a lower)."""
        return self._acquired

    @property
    def available(self) -> int:
        """Free admission slots under the current limit (never negative)."""
        return max(0, self._limit - self._acquired)

    async def acquire(self) -> None:
        """Acquire a slot, blocking until ``acquired < limit``."""
        while True:
            # No `await` between this check and the increment/`clear()` below,
            # so in single-threaded asyncio nothing interleaves: the increment
            # is atomic w.r.t. the limit check, and a `set()` racing our
            # `clear()` cannot be lost (it lands before the check or after the
            # `wait()` yield point).
            if self._acquired < self._limit:
                self._acquired += 1
                return
            self._wake.clear()
            await self._wake.wait()

    def release(self) -> None:
        """Release a held slot and wake any waiters."""
        if self._acquired <= 0:
            raise RuntimeError(
                "ConcurrencyGate.release() called without a matching acquire()"
            )
        self._acquired -= 1
        self._wake.set()

    def set_limit(self, new_limit: int) -> None:
        """Change the limit in place. Raises wake waiters; lowers tighten as
        running work drains. Never affects in-flight holders."""
        if new_limit < 1:
            raise ValueError(f"limit must be >= 1, got {new_limit}")
        self._limit = new_limit
        # Wake waiters to re-check against the new limit. Harmless on a lower
        # (woken waiters re-check, find no room, and wait again) and necessary
        # on a raise (newly-available slots admit queued waiters).
        self._wake.set()

    async def __aenter__(self) -> ConcurrencyGate:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()
