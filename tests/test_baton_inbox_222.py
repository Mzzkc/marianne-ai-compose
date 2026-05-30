"""Tests for the baton inbox depth strategy (#222).

The inbox is intentionally UNBOUNDED — dropping a baton event (completion,
rate-limit hit, retry-due, control command) strands sheets or corrupts state,
and the self-enqueue producers run on the consumer task so a blocking bounded
put would deadlock. The vetted design (4-model thinking-lab review) is:

  1. Keep the queue unbounded.
  2. Coalesce DispatchRetry (the only purely-redundant self-enqueued event) so a
     wide fan-in cascade can't queue N redundant wakes.
  3. Track inbox depth (high-water) and emit a rate-limited WARNING when the
     consumer is falling behind, so a stall is diagnosable before OOM.
"""

from __future__ import annotations

from marianne.daemon.baton.adapter import (
    _INBOX_WARN_SIZE,
    BatonAdapter,
)
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import DispatchRetry
from marianne.daemon.baton.state import SheetExecutionState


class TestDispatchRetryCoalescing:
    def test_enqueue_coalesces_to_one(self) -> None:
        baton = BatonCore()
        for _ in range(10):
            baton.enqueue_dispatch_retry()
        # Ten wake requests collapse to a single queued DispatchRetry.
        assert baton.inbox.qsize() == 1
        assert baton._dispatch_retry_pending is True

    async def test_flag_clears_on_handle_allowing_fresh_wake(self) -> None:
        baton = BatonCore()
        baton.enqueue_dispatch_retry()
        assert baton.inbox.qsize() == 1

        # The consumer drains and handles the DispatchRetry → flag clears.
        event = baton.inbox.get_nowait()
        assert isinstance(event, DispatchRetry)
        await baton.handle_event(event)
        assert baton._dispatch_retry_pending is False

        # A subsequent cascade can enqueue a fresh wake (not suppressed).
        baton.enqueue_dispatch_retry()
        assert baton.inbox.qsize() == 1

    async def test_wide_fanin_cascade_enqueues_at_most_one_retry(self) -> None:
        # sheet 1 is the upstream; sheets 2..20 all depend on it (fan-in).
        baton = BatonCore()
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, 21)
        }
        deps = {i: [1] for i in range(2, 21)}
        baton.register_job("job", sheets, deps)

        # Drain the registration's own dispatch kick, if any.
        while not baton.inbox.empty():
            baton.inbox.get_nowait()
        baton._dispatch_retry_pending = False

        # Propagating sheet 1's failure cascades to all 19 dependents but must
        # not flood the inbox with 19 redundant DispatchRetry wakes.
        baton._propagate_failure_to_dependents("job", 1)
        retries = [
            e for e in list(baton.inbox._queue)  # type: ignore[attr-defined]
            if isinstance(e, DispatchRetry)
        ]
        assert len(retries) <= 1

    def test_inbox_itself_is_not_bounded_or_auto_coalescing(self) -> None:
        # The coalescing lives in enqueue_dispatch_retry(), NOT the queue: the
        # queue must accept unbounded distinct events (we never drop them).
        baton = BatonCore()
        for _ in range(50):
            baton.inbox.put_nowait(DispatchRetry())
        assert baton.inbox.qsize() == 50


class TestInboxHighWater:
    def test_observe_tracks_running_maximum(self) -> None:
        baton = BatonCore()
        baton.observe_inbox_depth(5)
        baton.observe_inbox_depth(3)
        baton.observe_inbox_depth(12)
        baton.observe_inbox_depth(7)
        assert baton._inbox_high_water == 12

    def test_diagnostics_expose_inbox_depth_and_high_water(self) -> None:
        baton = BatonCore()
        baton.register_job(
            "job", {1: SheetExecutionState(sheet_num=1)}, {}
        )
        baton.observe_inbox_depth(9)
        diag = baton.get_diagnostics("job")
        assert diag is not None
        assert "inbox_depth" in diag
        assert diag["inbox_high_water"] == 9


class TestAdapterInboxWarning:
    """The consumer loop warns (rate-limited) when the inbox is too deep."""

    def test_below_threshold_does_not_warn(self) -> None:
        adapter = BatonAdapter()
        assert adapter._maybe_warn_inbox_depth(_INBOX_WARN_SIZE - 1, "X") is False

    def test_at_threshold_warns_once_then_rate_limited(self) -> None:
        adapter = BatonAdapter()
        # First crossing warns.
        assert adapter._maybe_warn_inbox_depth(_INBOX_WARN_SIZE, "X") is True
        # Immediate re-cross is rate-limited (no spam every event).
        assert adapter._maybe_warn_inbox_depth(_INBOX_WARN_SIZE + 100, "X") is False
        # After the interval elapses, it warns again.
        adapter._inbox_warn_logged_at -= 3600.0
        assert adapter._maybe_warn_inbox_depth(_INBOX_WARN_SIZE, "X") is True
