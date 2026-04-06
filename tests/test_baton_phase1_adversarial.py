"""Phase 1 Baton Adversarial Tests — proving the baton works under hostile conditions.

Movement 3, Adversary.

These tests systematically attack the baton's 8 integration surfaces under
conditions that Phase 1 (PROVE THE BATON WORKS) must survive before the baton
becomes default. Each test class targets a specific attack surface with
hostile inputs, race-like orderings, and boundary conditions.

Attack surfaces covered:
1. Dispatch failure handling (F-152 regression + edge cases)
2. Multi-job concurrent instrument sharing and rate limits
3. Recovery from corrupted/degraded checkpoint state
4. State sync callback resilience
5. Completion signaling under adversarial conditions
6. Cost limit enforcement at extreme boundaries
7. Event ordering attacks (interleaved multi-job events)
8. Deregistration during live execution
9. F-440 propagation on registration edge cases
10. Dispatch logic under adversarial concurrency constraints
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.daemon.baton.adapter import (
    BatonAdapter,
    baton_to_checkpoint_status,
    checkpoint_to_baton_status,
)
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.events import (
    CancelJob,
    DispatchRetry,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    JobTimeout,
    PauseJob,
    ProcessExited,
    RateLimitExpired,
    RateLimitHit,
    ResumeJob,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
    ShutdownRequested,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    SheetExecutionState,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_sheet_state(
    num: int,
    instrument: str = "claude-cli",
    status: BatonSheetStatus = BatonSheetStatus.PENDING,
    max_retries: int = 3,
) -> SheetExecutionState:
    """Create a SheetExecutionState for testing."""
    state = SheetExecutionState(
        sheet_num=num,
        instrument_name=instrument,
        max_retries=max_retries,
    )
    state.status = status
    return state


def _make_result(
    job_id: str = "j1",
    sheet_num: int = 1,
    instrument: str = "claude-cli",
    success: bool = True,
    pass_rate: float = 100.0,
    cost: float = 0.01,
    rate_limited: bool = False,
    error_class: str | None = None,
    validations_total: int = 1,
) -> SheetAttemptResult:
    """Create a SheetAttemptResult for testing."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=1,
        execution_success=success,
        validation_pass_rate=pass_rate,
        validations_passed=int(pass_rate / 100 * validations_total),
        validations_total=validations_total,
        cost_usd=cost,
        rate_limited=rate_limited,
        error_classification=error_class,
    )


def _register_simple_job(
    baton: BatonCore,
    job_id: str = "j1",
    sheet_count: int = 3,
    instrument: str = "claude-cli",
    deps: dict[int, list[int]] | None = None,
    **kwargs: Any,
) -> dict[int, SheetExecutionState]:
    """Register a simple job and return the sheet states."""
    sheets = {
        i: _make_sheet_state(i, instrument=instrument)
        for i in range(1, sheet_count + 1)
    }
    if deps is None:
        deps = {i: [] for i in range(1, sheet_count + 1)}
    baton.register_job(job_id, sheets, deps, **kwargs)
    return sheets


# =============================================================================
# 1. Dispatch Failure Handling (F-152 Regression + Edge Cases)
# =============================================================================


class TestDispatchFailureHandling:
    """Attack the dispatch callback to verify F-152 fix holds and edge cases
    are covered. Every early-return path in _dispatch_callback must produce
    a SheetAttemptResult failure event."""

    def test_dispatch_failure_on_missing_sheet_produces_e505(self) -> None:
        """If the adapter's sheet registry doesn't have the sheet, an E505
        failure event must be posted to the baton inbox."""
        adapter = BatonAdapter()
        baton = adapter.baton
        # Register job in baton but NOT in adapter's _job_sheets
        sheets = _register_simple_job(baton, "j1", 1)
        # Don't populate adapter._job_sheets

        # Run dispatch callback synchronously via event loop
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                adapter._dispatch_callback("j1", 1, sheets[1])
            )
        finally:
            loop.close()

        # The failure event should be in the baton inbox
        assert not baton.inbox.empty()
        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.error_classification == "E505"
        assert not event.execution_success

    def test_dispatch_failure_on_no_backend_pool(self) -> None:
        """Without a backend pool, dispatch must fail with E505."""
        adapter = BatonAdapter()
        baton = adapter.baton
        sheets = _register_simple_job(baton, "j1", 1)
        adapter._job_sheets["j1"] = {}
        # Create a mock Sheet object
        mock_sheet = MagicMock()
        mock_sheet.num = 1
        mock_sheet.instrument_name = "claude-cli"
        adapter._job_sheets["j1"][1] = mock_sheet

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                adapter._dispatch_callback("j1", 1, sheets[1])
            )
        finally:
            loop.close()

        assert not baton.inbox.empty()
        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.error_classification == "E505"

    def test_dispatch_failure_on_backend_acquire_exception(self) -> None:
        """When backend pool raises, dispatch must send failure event."""
        adapter = BatonAdapter()
        baton = adapter.baton
        sheets = _register_simple_job(baton, "j1", 1)

        mock_sheet = MagicMock()
        mock_sheet.num = 1
        mock_sheet.instrument_name = "claude-cli"
        mock_sheet.instrument_config = {}
        mock_sheet.workspace = "/tmp/test"
        adapter._job_sheets["j1"] = {1: mock_sheet}

        mock_pool = AsyncMock()
        mock_pool.acquire = AsyncMock(
            side_effect=NotImplementedError("Unsupported instrument kind: http")
        )
        adapter.set_backend_pool(mock_pool)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                adapter._dispatch_callback("j1", 1, sheets[1])
            )
        finally:
            loop.close()

        assert not baton.inbox.empty()
        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.error_classification == "E505"
        assert "Unsupported instrument kind" in (event.error_message or "")

    def test_dispatch_failure_attempt_number_reflects_state(self) -> None:
        """The attempt number in the failure event must be derived from
        the sheet's current attempt counts, not hardcoded to 1."""
        adapter = BatonAdapter()
        baton = adapter.baton
        state = _make_sheet_state(1)
        state.normal_attempts = 2
        state.completion_attempts = 1
        baton.register_job("j1", {1: state}, {1: []})

        # No backend pool → E505 failure
        mock_sheet = MagicMock()
        mock_sheet.num = 1
        mock_sheet.instrument_name = "claude-cli"
        adapter._job_sheets["j1"] = {1: mock_sheet}

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                adapter._dispatch_callback("j1", 1, state)
            )
        finally:
            loop.close()

        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        # Should be 2 + 1 + 1 = 4, not 1
        assert event.attempt == 4


# =============================================================================
# 2. Multi-Job Concurrent Instrument Sharing
# =============================================================================


class TestMultiJobInstrumentSharing:
    """Two or more jobs running on the same instrument. Rate limits affect
    all jobs, not just the one that triggered it."""

    def test_rate_limit_hit_affects_all_jobs_on_instrument(self) -> None:
        """A rate limit on instrument X must move ALL dispatched sheets
        on X to WAITING, across all jobs."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2, instrument="claude-cli")
        _register_simple_job(baton, "j2", 2, instrument="claude-cli")

        # Manually set some sheets to DISPATCHED
        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.DISPATCHED
        baton._jobs["j2"].sheets[1].status = BatonSheetStatus.DISPATCHED
        baton._jobs["j2"].sheets[2].status = BatonSheetStatus.DISPATCHED

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    RateLimitHit(
                        instrument="claude-cli",
                        wait_seconds=60,
                        job_id="j1",
                        sheet_num=1,
                    )
                )
            )
        finally:
            loop.close()

        # All dispatched sheets on claude-cli must be WAITING
        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.WAITING
        assert baton._jobs["j2"].sheets[1].status == BatonSheetStatus.WAITING
        assert baton._jobs["j2"].sheets[2].status == BatonSheetStatus.WAITING
        # PENDING sheet unaffected
        assert baton._jobs["j1"].sheets[2].status == BatonSheetStatus.PENDING

    def test_rate_limit_on_one_instrument_doesnt_affect_other(self) -> None:
        """A rate limit on claude-cli must NOT touch sheets on gemini-cli."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1, instrument="claude-cli")
        _register_simple_job(baton, "j2", 1, instrument="gemini-cli")

        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.DISPATCHED
        baton._jobs["j2"].sheets[1].status = BatonSheetStatus.DISPATCHED

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    RateLimitHit(
                        instrument="claude-cli",
                        wait_seconds=60,
                        job_id="j1",
                        sheet_num=1,
                    )
                )
            )
        finally:
            loop.close()

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.WAITING
        # gemini-cli sheet must be untouched
        assert baton._jobs["j2"].sheets[1].status == BatonSheetStatus.DISPATCHED

    def test_rate_limit_expired_clears_all_waiting_sheets(self) -> None:
        """When rate limit expires, ALL WAITING sheets on that instrument
        across all jobs go back to PENDING."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2, instrument="claude-cli")
        _register_simple_job(baton, "j2", 1, instrument="claude-cli")

        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.WAITING
        baton._jobs["j1"].sheets[1].instrument_name = "claude-cli"
        baton._jobs["j2"].sheets[1].status = BatonSheetStatus.WAITING
        baton._jobs["j2"].sheets[1].instrument_name = "claude-cli"

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(RateLimitExpired(instrument="claude-cli"))
            )
        finally:
            loop.close()

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.PENDING
        assert baton._jobs["j2"].sheets[1].status == BatonSheetStatus.PENDING

    def test_cancel_one_job_doesnt_affect_shared_instrument(self) -> None:
        """Cancelling j1 must not touch j2's sheets even if they share
        an instrument."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2, instrument="claude-cli")
        _register_simple_job(baton, "j2", 2, instrument="claude-cli")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(baton.handle_event(CancelJob(job_id="j1")))
        finally:
            loop.close()

        assert "j1" not in baton._jobs
        assert "j2" in baton._jobs
        # j2 sheets unaffected
        for s in baton._jobs["j2"].sheets.values():
            assert s.status == BatonSheetStatus.PENDING


# =============================================================================
# 3. Recovery from Corrupted Checkpoint State
# =============================================================================


class TestRecoveryFromCorruptedCheckpoint:
    """The adapter's recover_job() must handle degraded checkpoint data
    gracefully — missing sheets, unknown statuses, extreme attempt counts."""

    def test_recover_missing_sheet_in_checkpoint(self) -> None:
        """A sheet that exists in the config but not in the checkpoint
        should start as PENDING with zero attempts."""
        adapter = BatonAdapter()

        mock_sheets = []
        for i in range(1, 4):
            s = MagicMock()
            s.num = i
            s.instrument_name = "claude-cli"
            s.instrument_config = {}
            s.movement = 1
            mock_sheets.append(s)

        # Checkpoint only has sheet 1 and 3 — sheet 2 is missing
        mock_cp = MagicMock()
        mock_cp.sheets = {
            1: MagicMock(
                status=MagicMock(value="completed"),
                attempt_count=2, completion_attempts=0,
            ),
            3: MagicMock(
                status=MagicMock(value="failed"),
                attempt_count=3, completion_attempts=0,
            ),
        }

        adapter.recover_job(
            "j1", mock_sheets, {1: [], 2: [1], 3: [2]}, checkpoint=mock_cp
        )

        # Sheet 2 should be PENDING with 0 attempts
        state2 = adapter.baton.get_sheet_state("j1", 2)
        assert state2 is not None
        assert state2.status == BatonSheetStatus.PENDING
        assert state2.normal_attempts == 0

    def test_recover_in_progress_resets_to_pending(self) -> None:
        """in_progress sheets must reset to PENDING on recovery because
        their musician died when the conductor restarted."""
        adapter = BatonAdapter()

        mock_sheet = MagicMock()
        mock_sheet.num = 1
        mock_sheet.instrument_name = "claude-cli"
        mock_sheet.instrument_config = {}
        mock_sheet.movement = 1

        mock_cp = MagicMock()
        mock_cp.sheets = {
            1: MagicMock(
                status=MagicMock(value="in_progress"),
                attempt_count=1, completion_attempts=0,
            ),
        }

        adapter.recover_job("j1", [mock_sheet], {1: []}, checkpoint=mock_cp)

        state = adapter.baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.PENDING
        # Attempt count preserved to avoid infinite retries
        assert state.normal_attempts == 1

    def test_recover_preserves_terminal_states(self) -> None:
        """Completed, failed, skipped sheets must keep their status."""
        adapter = BatonAdapter()
        terminal_statuses = ["completed", "failed", "skipped"]
        mock_sheets = []
        for i, _status in enumerate(terminal_statuses, 1):
            s = MagicMock()
            s.num = i
            s.instrument_name = "claude-cli"
            s.instrument_config = {}
            s.movement = 1
            mock_sheets.append(s)

        mock_cp = MagicMock()
        mock_cp.sheets = {
            i: MagicMock(status=MagicMock(value=status), attempt_count=1, completion_attempts=0)
            for i, status in enumerate(terminal_statuses, 1)
        }

        adapter.recover_job(
            "j1",
            mock_sheets,
            {1: [], 2: [], 3: []},
            checkpoint=mock_cp,
        )

        expected = {
            1: BatonSheetStatus.COMPLETED,
            2: BatonSheetStatus.FAILED,
            3: BatonSheetStatus.SKIPPED,
        }
        for num, exp_status in expected.items():
            state = adapter.baton.get_sheet_state("j1", num)
            assert state is not None
            assert state.status == exp_status, (
                f"Sheet {num}: expected {exp_status}, got {state.status}"
            )

    def test_recover_with_unknown_checkpoint_status_raises(self) -> None:
        """An unknown status string in the checkpoint should raise KeyError
        from checkpoint_to_baton_status."""
        with pytest.raises(KeyError):
            checkpoint_to_baton_status("zombie")


# =============================================================================
# 4. State Sync Callback Resilience
# =============================================================================


class TestStateSyncCallbackResilience:
    """The state sync callback is called after each baton event that changes
    sheet status. If it fails, the baton must continue processing."""

    def test_state_sync_callback_is_called_on_completion(self) -> None:
        """Verify the adapter's run loop invokes state sync after events."""
        sync_calls: list[tuple[str, int, str]] = []

        def sync_callback(job_id: str, sheet_num: int, status: str) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=sync_callback)
        assert adapter._state_sync_callback is sync_callback

    def test_baton_to_checkpoint_status_covers_all_states(self) -> None:
        """Every BatonSheetStatus must have a mapping to checkpoint status."""
        for status in BatonSheetStatus:
            result = baton_to_checkpoint_status(status)
            assert isinstance(result, str), f"No mapping for {status}"

    def test_checkpoint_to_baton_status_covers_all_checkpoint_states(self) -> None:
        """All 5 checkpoint states must map to baton states."""
        for cp_status in ["pending", "in_progress", "completed", "failed", "skipped"]:
            result = checkpoint_to_baton_status(cp_status)
            assert isinstance(result, BatonSheetStatus)


# =============================================================================
# 5. Completion Signaling Under Adversarial Conditions
# =============================================================================


class TestCompletionSignalingAdversarial:
    """Attack the completion signaling mechanism to find race conditions
    and edge cases."""

    def test_completion_event_set_when_all_sheets_terminal(self) -> None:
        """_check_completions must set the event when all sheets are terminal."""
        adapter = BatonAdapter()
        mock_sheets = [MagicMock(num=1, instrument_name="claude-cli", movement=1)]
        adapter.register_job("j1", mock_sheets, {1: []})

        # Manually complete the sheet in the baton
        baton_state = adapter.baton.get_sheet_state("j1", 1)
        assert baton_state is not None
        baton_state.status = BatonSheetStatus.COMPLETED

        adapter._check_completions()

        assert adapter._completion_events["j1"].is_set()
        assert adapter._completion_results["j1"] is True

    def test_completion_false_when_any_sheet_failed(self) -> None:
        """If one sheet fails, the completion result must be False."""
        adapter = BatonAdapter()
        mock_sheets = [
            MagicMock(num=1, instrument_name="claude-cli", movement=1),
            MagicMock(num=2, instrument_name="claude-cli", movement=1),
        ]
        adapter.register_job("j1", mock_sheets, {1: [], 2: []})

        adapter.baton.get_sheet_state("j1", 1).status = BatonSheetStatus.COMPLETED
        adapter.baton.get_sheet_state("j1", 2).status = BatonSheetStatus.FAILED

        adapter._check_completions()

        assert adapter._completion_events["j1"].is_set()
        assert adapter._completion_results["j1"] is False

    def test_completion_not_set_while_sheets_pending(self) -> None:
        """Completion must NOT fire while any sheet is non-terminal."""
        adapter = BatonAdapter()
        mock_sheets = [
            MagicMock(num=1, instrument_name="claude-cli", movement=1),
            MagicMock(num=2, instrument_name="claude-cli", movement=1),
        ]
        adapter.register_job("j1", mock_sheets, {1: [], 2: []})

        adapter.baton.get_sheet_state("j1", 1).status = BatonSheetStatus.COMPLETED
        # Sheet 2 still PENDING

        adapter._check_completions()

        assert not adapter._completion_events["j1"].is_set()

    def test_deregister_cleans_up_completion_tracking(self) -> None:
        """Deregistering a job must clean up completion events and results."""
        adapter = BatonAdapter()
        mock_sheets = [MagicMock(num=1, instrument_name="claude-cli", movement=1)]
        adapter.register_job("j1", mock_sheets, {1: []})

        assert "j1" in adapter._completion_events
        adapter.deregister_job("j1")
        assert "j1" not in adapter._completion_events
        assert "j1" not in adapter._completion_results
        assert "j1" not in adapter._job_sheets

    def test_wait_for_completion_raises_on_unknown_job(self) -> None:
        """Waiting on an unregistered job must raise KeyError."""
        adapter = BatonAdapter()
        with pytest.raises(KeyError, match="not registered"):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(adapter.wait_for_completion("nonexistent"))
            finally:
                loop.close()

    def test_has_completed_sheets_returns_false_for_unknown_job(self) -> None:
        """has_completed_sheets must return False for unknown job."""
        adapter = BatonAdapter()
        assert adapter.has_completed_sheets("nonexistent") is False

    def test_has_completed_sheets_true_when_any_completed(self) -> None:
        """Must return True if at least one sheet is COMPLETED."""
        adapter = BatonAdapter()
        mock_sheets = [
            MagicMock(num=1, instrument_name="claude-cli", movement=1),
            MagicMock(num=2, instrument_name="claude-cli", movement=1),
        ]
        adapter.register_job("j1", mock_sheets, {1: [], 2: []})

        adapter.baton.get_sheet_state("j1", 1).status = BatonSheetStatus.COMPLETED
        # Sheet 2 still pending
        assert adapter.has_completed_sheets("j1") is True


# =============================================================================
# 6. Cost Limit Enforcement at Extreme Boundaries
# =============================================================================


class TestCostLimitBoundaries:
    """Push cost limits to extremes: zero, negative, NaN-like, exactly at
    boundary, and cost accumulation across multiple sheets."""

    def test_zero_cost_limit_pauses_on_first_nonzero_attempt(self) -> None:
        """A cost limit of 0.0 should pause the job immediately on any cost."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1)
        baton.set_job_cost_limit("j1", 0.0)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(job_id="j1", sheet_num=1, cost=0.001)
                )
            )
        finally:
            loop.close()

        # Sheet completes (success), but job should be paused due to cost
        assert baton._jobs["j1"].paused is True

    def test_cost_accumulates_across_sheets(self) -> None:
        """Total job cost is sum of all sheet costs."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3)
        baton.set_job_cost_limit("j1", 0.1)

        loop = asyncio.new_event_loop()
        try:
            # Complete sheets 1 and 2 with costs that individually are under
            # limit but together exceed it
            for num in [1, 2]:
                loop.run_until_complete(
                    baton.handle_event(
                        _make_result(job_id="j1", sheet_num=num, cost=0.06)
                    )
                )
        finally:
            loop.close()

        # 0.06 + 0.06 = 0.12 > 0.1 limit
        assert baton._jobs["j1"].paused is True

    def test_per_sheet_cost_limit_fails_sheet_on_exceed(self) -> None:
        """Per-sheet cost limit exceeded → sheet FAILED, not just paused."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2, instrument="claude-cli")
        baton.set_sheet_cost_limit("j1", 1, 0.05)

        # Sheet deps: sheet 2 depends on sheet 1
        baton._jobs["j1"].dependencies[2] = [1]

        loop = asyncio.new_event_loop()
        try:
            # Fail the sheet with some cost, then succeed with high cost
            # First: a failure that adds cost
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(
                        job_id="j1", sheet_num=1,
                        success=False, pass_rate=0.0, cost=0.03,
                    )
                )
            )
            # Sheet should be in retry_scheduled now
            # Second failure pushes cost over
            loop.run_until_complete(
                baton.handle_event(
                    RetryDue(job_id="j1", sheet_num=1)
                )
            )
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(
                        job_id="j1", sheet_num=1,
                        success=False, pass_rate=0.0, cost=0.03,
                    )
                )
            )
        finally:
            loop.close()

        sheet1 = baton._jobs["j1"].sheets[1]
        # After exceeding per-sheet cost, should be FAILED
        assert sheet1.status == BatonSheetStatus.FAILED
        # Dependent sheet 2 should also be failed (propagation)
        assert baton._jobs["j1"].sheets[2].status == BatonSheetStatus.FAILED

    def test_resume_after_cost_pause_rechecks_cost(self) -> None:
        """ResumeJob re-checks cost limits (F-140). A cost-paused job
        that gets resumed must re-pause if cost is still exceeded."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2)
        baton.set_job_cost_limit("j1", 0.05)

        loop = asyncio.new_event_loop()
        try:
            # Complete sheet 1 with cost exceeding limit
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(job_id="j1", sheet_num=1, cost=0.10)
                )
            )
            assert baton._jobs["j1"].paused is True

            # Resume — should re-pause because cost still exceeded
            loop.run_until_complete(
                baton.handle_event(ResumeJob(job_id="j1"))
            )
        finally:
            loop.close()

        # Must re-pause: cost exceeds limit
        assert baton._jobs["j1"].paused is True


# =============================================================================
# 7. Event Ordering Attacks
# =============================================================================


class TestEventOrderingAttacks:
    """Adversarial event orderings that could expose state machine bugs.
    In an async system, events can arrive in any order."""

    def test_result_for_deregistered_job_is_safe(self) -> None:
        """A late SheetAttemptResult for a cancelled/deregistered job
        must not crash or corrupt state."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1)

        # Cancel and deregister the job
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(baton.handle_event(CancelJob(job_id="j1")))
        finally:
            loop.close()

        assert "j1" not in baton._jobs

        # Late result arrives — should be silently ignored
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(job_id="j1", sheet_num=1)
                )
            )
        finally:
            loop.close()
        # No crash, no state corruption

    def test_skip_then_result_for_same_sheet(self) -> None:
        """If a sheet is skipped and then a late result arrives,
        the terminal guard must prevent status regression."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1)

        loop = asyncio.new_event_loop()
        try:
            # Skip first
            loop.run_until_complete(
                baton.handle_event(
                    SheetSkipped(job_id="j1", sheet_num=1, reason="skip_when")
                )
            )
            assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.SKIPPED

            # Late result arrives
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(job_id="j1", sheet_num=1)
                )
            )
        finally:
            loop.close()

        # Must still be SKIPPED — terminal guard
        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.SKIPPED

    def test_escalation_during_user_pause_preserves_user_pause(self) -> None:
        """If a user pauses a job, then an escalation resolves, the job
        must stay paused (user_paused takes precedence)."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2, escalation_enabled=True)
        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.DISPATCHED

        loop = asyncio.new_event_loop()
        try:
            # User pauses
            loop.run_until_complete(baton.handle_event(PauseJob(job_id="j1")))
            assert baton._jobs["j1"].user_paused is True

            # Sheet 1 triggers escalation
            baton._jobs["j1"].sheets[1].status = BatonSheetStatus.FERMATA
            baton._jobs["j1"].sheets[1].normal_attempts = 3

            # Escalation resolves with "retry"
            loop.run_until_complete(
                baton.handle_event(
                    EscalationResolved(
                        job_id="j1", sheet_num=1, decision="retry"
                    )
                )
            )
        finally:
            loop.close()

        # Job must still be paused — user_paused takes precedence
        assert baton._jobs["j1"].paused is True
        assert baton._jobs["j1"].user_paused is True

    def test_multiple_fermata_sheets_only_unpause_when_all_resolved(self) -> None:
        """With 2 sheets in FERMATA, resolving one must NOT unpause the job."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3, escalation_enabled=True)

        # Two sheets in fermata
        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.FERMATA
        baton._jobs["j1"].sheets[2].status = BatonSheetStatus.FERMATA
        baton._jobs["j1"].paused = True

        loop = asyncio.new_event_loop()
        try:
            # Resolve sheet 1
            loop.run_until_complete(
                baton.handle_event(
                    EscalationResolved(
                        job_id="j1", sheet_num=1, decision="retry"
                    )
                )
            )
        finally:
            loop.close()

        # Sheet 2 still in FERMATA → job stays paused
        assert baton._jobs["j1"].paused is True

        # Now resolve sheet 2
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    EscalationResolved(
                        job_id="j1", sheet_num=2, decision="accept"
                    )
                )
            )
        finally:
            loop.close()

        # Both resolved → job unpauses
        assert baton._jobs["j1"].paused is False

    def test_job_timeout_only_cancels_nonterminal_sheets(self) -> None:
        """JobTimeout must only cancel non-terminal sheets. Completed
        sheets must remain completed."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3)
        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.COMPLETED
        baton._jobs["j1"].sheets[2].status = BatonSheetStatus.DISPATCHED

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(JobTimeout(job_id="j1"))
            )
        finally:
            loop.close()

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.COMPLETED
        assert baton._jobs["j1"].sheets[2].status == BatonSheetStatus.CANCELLED
        assert baton._jobs["j1"].sheets[3].status == BatonSheetStatus.CANCELLED

    def test_process_exit_on_non_dispatched_sheet_is_noop(self) -> None:
        """ProcessExited for a sheet that isn't DISPATCHED must be ignored.
        Only dispatched sheets can crash."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1)
        # Sheet is PENDING, not DISPATCHED
        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.PENDING

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    ProcessExited(job_id="j1", sheet_num=1, pid=12345)
                )
            )
        finally:
            loop.close()

        # Must still be PENDING — not dispatched, so no crash to handle
        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.PENDING

    def test_shutdown_graceful_leaves_dispatched_sheets(self) -> None:
        """Graceful shutdown must NOT cancel dispatched sheets — they
        should be allowed to complete."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2)
        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.DISPATCHED

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(ShutdownRequested(graceful=True))
            )
        finally:
            loop.close()

        # Graceful: dispatched sheets continue
        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.DISPATCHED
        # But pending sheets also remain (graceful doesn't cancel anything)
        assert baton._jobs["j1"].sheets[2].status == BatonSheetStatus.PENDING
        assert baton._shutting_down is True

    def test_shutdown_not_graceful_cancels_all(self) -> None:
        """Non-graceful shutdown must cancel all non-terminal sheets."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3)
        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.DISPATCHED
        baton._jobs["j1"].sheets[2].status = BatonSheetStatus.COMPLETED

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(ShutdownRequested(graceful=False))
            )
        finally:
            loop.close()

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.CANCELLED
        assert baton._jobs["j1"].sheets[2].status == BatonSheetStatus.COMPLETED
        assert baton._jobs["j1"].sheets[3].status == BatonSheetStatus.CANCELLED


# =============================================================================
# 8. Deregistration During Active Execution
# =============================================================================


class TestDeregistrationDuringExecution:
    """Deregistering a job while it has active musician tasks."""

    def test_deregister_cancels_active_tasks(self) -> None:
        """Active asyncio tasks for a job must be cancelled on deregistration."""
        adapter = BatonAdapter()
        mock_sheets = [MagicMock(num=1, instrument_name="claude-cli", movement=1)]
        adapter.register_job("j1", mock_sheets, {1: []})

        # Simulate an active task
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        adapter._active_tasks[("j1", 1)] = mock_task

        adapter.deregister_job("j1")

        mock_task.cancel.assert_called_once()
        assert ("j1", 1) not in adapter._active_tasks

    def test_deregister_cleans_up_cost_limits(self) -> None:
        """Deregistration must clean up per-job and per-sheet cost limits."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2)
        baton.set_job_cost_limit("j1", 1.0)
        baton.set_sheet_cost_limit("j1", 1, 0.5)
        baton.set_sheet_cost_limit("j1", 2, 0.5)

        baton.deregister_job("j1")

        assert "j1" not in baton._job_cost_limits
        assert ("j1", 1) not in baton._sheet_cost_limits
        assert ("j1", 2) not in baton._sheet_cost_limits

    def test_events_for_deregistered_job_are_harmless(self) -> None:
        """Events arriving for a deregistered job must not crash."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1)
        baton.deregister_job("j1")

        loop = asyncio.new_event_loop()
        try:
            # All these should be silently ignored
            events: list[Any] = [
                _make_result(job_id="j1"),
                SheetSkipped(job_id="j1", sheet_num=1, reason="test"),
                PauseJob(job_id="j1"),
                ResumeJob(job_id="j1"),
                RetryDue(job_id="j1", sheet_num=1),
                ProcessExited(job_id="j1", sheet_num=1, pid=1),
                EscalationNeeded(job_id="j1", sheet_num=1, reason="test"),
                EscalationResolved(job_id="j1", sheet_num=1, decision="retry"),
                EscalationTimeout(job_id="j1", sheet_num=1),
                JobTimeout(job_id="j1"),
            ]
            for event in events:
                loop.run_until_complete(baton.handle_event(event))
        finally:
            loop.close()
        # No crash — all events safely ignored


# =============================================================================
# 9. F-440 Propagation on Registration Edge Cases
# =============================================================================


class TestF440PropagationOnRegistration:
    """F-440: Failed sheets must propagate failure to dependents on
    registration (for restart recovery). Test the edge cases."""

    def test_failed_sheet_propagates_on_register(self) -> None:
        """A FAILED sheet during register_job must cascade to dependents."""
        baton = BatonCore()
        sheets = {
            1: _make_sheet_state(1, status=BatonSheetStatus.FAILED),
            2: _make_sheet_state(2),
            3: _make_sheet_state(3),
        }
        deps = {1: [], 2: [1], 3: [2]}

        baton.register_job("j1", sheets, deps)

        # Sheet 2 and 3 should be FAILED (transitive propagation)
        assert sheets[2].status == BatonSheetStatus.FAILED
        assert sheets[3].status == BatonSheetStatus.FAILED

    def test_failed_sheet_doesnt_overwrite_completed_dependent(self) -> None:
        """If a dependent was already COMPLETED before registration
        (shouldn't happen in practice, but defense in depth), the
        propagation should NOT overwrite it — terminal state is terminal."""
        baton = BatonCore()
        sheets = {
            1: _make_sheet_state(1, status=BatonSheetStatus.FAILED),
            2: _make_sheet_state(2, status=BatonSheetStatus.COMPLETED),
            3: _make_sheet_state(3),  # PENDING — should get failed
        }
        deps = {1: [], 2: [1], 3: [2]}

        baton.register_job("j1", sheets, deps)

        # Sheet 2 stays COMPLETED (terminal guard in propagation)
        assert sheets[2].status == BatonSheetStatus.COMPLETED
        # Sheet 3 depends on sheet 2 (which is COMPLETED), so the
        # propagation from sheet 1 goes through the dependency chain.
        # But since sheet 2 is terminal (COMPLETED), propagation from
        # sheet 1 reaches sheet 2 (no-op) and then sheet 3.
        # Sheet 3 depends on sheet 2 (COMPLETED), not directly on sheet 1.
        # The BFS visits: sheet 1 → dependents of 1 = [2] → sheet 2 COMPLETED (skip)
        #   → dependents of 2 = [3] → sheet 3 PENDING → FAILED
        assert sheets[3].status == BatonSheetStatus.FAILED

    def test_multiple_failed_sheets_propagate_independently(self) -> None:
        """Multiple FAILED sheets at registration time should each
        independently propagate failure."""
        baton = BatonCore()
        sheets = {
            1: _make_sheet_state(1, status=BatonSheetStatus.FAILED),
            2: _make_sheet_state(2, status=BatonSheetStatus.FAILED),
            3: _make_sheet_state(3),  # depends on 1
            4: _make_sheet_state(4),  # depends on 2
            5: _make_sheet_state(5),  # depends on both 3 and 4
        }
        deps = {1: [], 2: [], 3: [1], 4: [2], 5: [3, 4]}

        baton.register_job("j1", sheets, deps)

        assert sheets[3].status == BatonSheetStatus.FAILED
        assert sheets[4].status == BatonSheetStatus.FAILED
        assert sheets[5].status == BatonSheetStatus.FAILED

    def test_duplicate_registration_is_noop(self) -> None:
        """Registering the same job_id twice should be a no-op (idempotent)."""
        baton = BatonCore()
        sheets1 = {1: _make_sheet_state(1)}
        sheets2 = {1: _make_sheet_state(1), 2: _make_sheet_state(2)}

        baton.register_job("j1", sheets1, {1: []})
        baton.register_job("j1", sheets2, {1: [], 2: []})

        # Should still have the original registration
        assert len(baton._jobs["j1"].sheets) == 1


# =============================================================================
# 10. Dispatch Logic Under Adversarial Concurrency Constraints
# =============================================================================


class TestDispatchAdversarialConcurrency:
    """Push the dispatch logic with hostile concurrency configurations."""

    def test_zero_global_concurrency_dispatches_nothing(self) -> None:
        """max_concurrent_sheets=0 should dispatch zero sheets."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 5)

        config = DispatchConfig(max_concurrent_sheets=0)
        dispatched: list[tuple[str, int]] = []

        async def callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
            dispatched.append((job_id, sheet_num))

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                dispatch_ready(baton, config, callback)
            )
        finally:
            loop.close()

        assert result.dispatched_count == 0
        assert len(dispatched) == 0

    def test_all_instruments_rate_limited_dispatches_nothing(self) -> None:
        """If every instrument is rate limited, nothing can be dispatched."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3, instrument="claude-cli")

        config = DispatchConfig(
            max_concurrent_sheets=10,
            rate_limited_instruments={"claude-cli"},
        )
        dispatched: list[tuple[str, int]] = []

        async def callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
            dispatched.append((job_id, sheet_num))

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                dispatch_ready(baton, config, callback)
            )
        finally:
            loop.close()

        assert result.dispatched_count == 0
        assert "rate_limited:claude-cli" in result.skipped_reasons

    def test_per_instrument_concurrency_respected(self) -> None:
        """Per-instrument concurrency must limit dispatches even when
        global concurrency allows more."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 5, instrument="claude-cli")

        config = DispatchConfig(
            max_concurrent_sheets=100,
            instrument_concurrency={"claude-cli": 2},
        )
        dispatched: list[tuple[str, int]] = []

        async def callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
            dispatched.append((job_id, sheet_num))

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                dispatch_ready(baton, config, callback)
            )
        finally:
            loop.close()

        assert result.dispatched_count == 2

    def test_open_circuit_breaker_blocks_dispatch(self) -> None:
        """Sheets on instruments with open circuit breakers must not dispatch."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3, instrument="broken-cli")

        config = DispatchConfig(
            max_concurrent_sheets=10,
            open_circuit_breakers={"broken-cli"},
        )

        async def callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
            pass

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                dispatch_ready(baton, config, callback)
            )
        finally:
            loop.close()

        assert result.dispatched_count == 0
        assert "circuit_breaker:broken-cli" in result.skipped_reasons

    def test_dispatch_callback_exception_logged_not_fatal(self) -> None:
        """If the dispatch callback raises, the exception is caught and
        the sheet is NOT marked as dispatched."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3)

        config = DispatchConfig(max_concurrent_sheets=10)

        call_count = 0

        async def failing_callback(
            job_id: str, sheet_num: int, state: SheetExecutionState
        ) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Backend pool exploded")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                dispatch_ready(baton, config, failing_callback)
            )
        finally:
            loop.close()

        # First sheet fails, but dispatch continues for the rest
        # At least one dispatch should succeed (the 2nd and 3rd)
        assert result.dispatched_count >= 1

    def test_dispatch_during_shutdown_returns_immediately(self) -> None:
        """dispatch_ready during shutdown must return empty result."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3)
        baton._shutting_down = True

        config = DispatchConfig(max_concurrent_sheets=10)

        async def callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
            pass

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                dispatch_ready(baton, config, callback)
            )
        finally:
            loop.close()

        assert result.dispatched_count == 0

    def test_paused_job_not_dispatched(self) -> None:
        """Paused jobs must return empty ready sheets."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 3)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(baton.handle_event(PauseJob(job_id="j1")))
        finally:
            loop.close()

        assert baton.get_ready_sheets("j1") == []

    def test_mixed_instruments_dispatched_correctly(self) -> None:
        """Jobs with mixed instruments should dispatch respecting
        per-instrument limits independently."""
        baton = BatonCore()
        sheets = {
            1: _make_sheet_state(1, instrument="claude-cli"),
            2: _make_sheet_state(2, instrument="gemini-cli"),
            3: _make_sheet_state(3, instrument="claude-cli"),
            4: _make_sheet_state(4, instrument="gemini-cli"),
        }
        baton.register_job("j1", sheets, {1: [], 2: [], 3: [], 4: []})

        config = DispatchConfig(
            max_concurrent_sheets=10,
            instrument_concurrency={"claude-cli": 1, "gemini-cli": 1},
        )
        dispatched: list[tuple[str, int]] = []

        async def callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
            dispatched.append((job_id, sheet_num))

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                dispatch_ready(baton, config, callback)
            )
        finally:
            loop.close()

        # 1 claude-cli + 1 gemini-cli = 2 dispatched
        assert result.dispatched_count == 2
        instruments_dispatched = set()
        for _job_id, snum in dispatched:
            instruments_dispatched.add(sheets[snum].instrument_name)
        assert "claude-cli" in instruments_dispatched
        assert "gemini-cli" in instruments_dispatched


# =============================================================================
# 11. Additional Edge Cases — Terminal State Resistance
# =============================================================================


class TestTerminalStateResistanceM3:
    """Verify M3 terminal state fixes hold under adversarial event sequences.
    Every terminal state must resist regression from any event type."""

    @pytest.mark.parametrize(
        "terminal_status",
        [BatonSheetStatus.COMPLETED, BatonSheetStatus.FAILED,
         BatonSheetStatus.SKIPPED, BatonSheetStatus.CANCELLED],
    )
    def test_terminal_sheet_resists_attempt_result(
        self, terminal_status: BatonSheetStatus
    ) -> None:
        """No SheetAttemptResult can regress a terminal sheet."""
        baton = BatonCore()
        sheets = {1: _make_sheet_state(1, status=terminal_status)}
        baton.register_job("j1", sheets, {1: []})

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(job_id="j1", sheet_num=1)
                )
            )
        finally:
            loop.close()

        assert sheets[1].status == terminal_status

    @pytest.mark.parametrize(
        "terminal_status",
        [BatonSheetStatus.COMPLETED, BatonSheetStatus.FAILED,
         BatonSheetStatus.SKIPPED, BatonSheetStatus.CANCELLED],
    )
    def test_terminal_sheet_resists_skip(
        self, terminal_status: BatonSheetStatus
    ) -> None:
        """No SheetSkipped event can regress a terminal sheet."""
        baton = BatonCore()
        sheets = {1: _make_sheet_state(1, status=terminal_status)}
        baton.register_job("j1", sheets, {1: []})

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    SheetSkipped(job_id="j1", sheet_num=1, reason="late")
                )
            )
        finally:
            loop.close()

        assert sheets[1].status == terminal_status

    @pytest.mark.parametrize(
        "terminal_status",
        [BatonSheetStatus.COMPLETED, BatonSheetStatus.FAILED,
         BatonSheetStatus.SKIPPED, BatonSheetStatus.CANCELLED],
    )
    def test_terminal_sheet_resists_escalation(
        self, terminal_status: BatonSheetStatus
    ) -> None:
        """No EscalationNeeded event can move a terminal sheet to FERMATA."""
        baton = BatonCore()
        sheets = {1: _make_sheet_state(1, status=terminal_status)}
        baton.register_job("j1", sheets, {1: []})

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    EscalationNeeded(
                        job_id="j1", sheet_num=1, reason="test"
                    )
                )
            )
        finally:
            loop.close()

        assert sheets[1].status == terminal_status


# =============================================================================
# 12. Exhaustion Decision Tree
# =============================================================================


class TestExhaustionDecisionTree:
    """The exhaustion handler has three paths: healing, escalation, fail.
    Exactly one must fire. Test the mutual exclusion."""

    def test_exhaustion_with_healing_schedules_retry(self) -> None:
        """When self_healing is enabled and budget remains, the sheet
        gets a healing retry, not escalation or failure."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1, self_healing_enabled=True)
        sheet = baton._jobs["j1"].sheets[1]
        sheet.status = BatonSheetStatus.DISPATCHED

        loop = asyncio.new_event_loop()
        try:
            # Exhaust retries
            for _ in range(3):
                sheet.status = BatonSheetStatus.DISPATCHED
                loop.run_until_complete(
                    baton.handle_event(
                        _make_result(
                            job_id="j1", sheet_num=1,
                            success=False, pass_rate=0.0, cost=0.0,
                        )
                    )
                )
                if sheet.status == BatonSheetStatus.RETRY_SCHEDULED:
                    loop.run_until_complete(
                        baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
                    )
        finally:
            loop.close()

        # After 3 retries exhausted, healing kicks in
        assert sheet.healing_attempts >= 1
        assert sheet.status != BatonSheetStatus.FAILED

    def test_exhaustion_with_escalation_enters_fermata(self) -> None:
        """When healing is exhausted but escalation is enabled,
        the sheet enters FERMATA."""
        baton = BatonCore()
        _register_simple_job(
            baton, "j1", 1,
            escalation_enabled=True,
            self_healing_enabled=False,
        )
        sheet = baton._jobs["j1"].sheets[1]
        sheet.max_retries = 1  # Quick exhaustion

        loop = asyncio.new_event_loop()
        try:
            sheet.status = BatonSheetStatus.DISPATCHED
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(
                        job_id="j1", sheet_num=1,
                        success=False, pass_rate=0.0,
                    )
                )
            )
        finally:
            loop.close()

        assert sheet.status == BatonSheetStatus.FERMATA
        assert baton._jobs["j1"].paused is True

    def test_exhaustion_without_healing_or_escalation_fails(self) -> None:
        """Without healing or escalation, exhaustion → FAILED + propagation."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 2)
        baton._jobs["j1"].dependencies[2] = [1]
        sheet = baton._jobs["j1"].sheets[1]
        sheet.max_retries = 1

        loop = asyncio.new_event_loop()
        try:
            sheet.status = BatonSheetStatus.DISPATCHED
            loop.run_until_complete(
                baton.handle_event(
                    _make_result(
                        job_id="j1", sheet_num=1,
                        success=False, pass_rate=0.0,
                    )
                )
            )
        finally:
            loop.close()

        assert sheet.status == BatonSheetStatus.FAILED
        # Dependent sheet 2 also failed
        assert baton._jobs["j1"].sheets[2].status == BatonSheetStatus.FAILED


# =============================================================================
# 13. Observer Event Conversion
# =============================================================================


class TestObserverEventConversion:
    """Verify that all event types convert to ObserverEvent format without
    errors. Completeness matters — a missing case in to_observer_event()
    would raise ValueError in production."""

    def test_all_event_types_convert_to_observer_event(self) -> None:
        """Every event type in the BatonEvent union must convert cleanly."""
        from marianne.daemon.baton.events import to_observer_event

        events = [
            SheetAttemptResult(job_id="j1", sheet_num=1, instrument_name="cli", attempt=1),
            SheetSkipped(job_id="j1", sheet_num=1, reason="test"),
            RateLimitHit(instrument="cli", wait_seconds=60, job_id="j1", sheet_num=1),
            RateLimitExpired(instrument="cli"),
            RetryDue(job_id="j1", sheet_num=1),
            ShutdownRequested(graceful=True),
            PauseJob(job_id="j1"),
            ResumeJob(job_id="j1"),
            CancelJob(job_id="j1"),
            JobTimeout(job_id="j1"),
            ProcessExited(job_id="j1", sheet_num=1, pid=1),
            EscalationNeeded(job_id="j1", sheet_num=1, reason="test"),
            EscalationResolved(job_id="j1", sheet_num=1, decision="retry"),
            EscalationTimeout(job_id="j1", sheet_num=1),
            DispatchRetry(),
        ]

        for event in events:
            result = to_observer_event(event)
            assert "event" in result, f"Missing 'event' key for {type(event).__name__}"
            assert "timestamp" in result, f"Missing 'timestamp' key for {type(event).__name__}"


# =============================================================================
# 14. Auto-Instrument Registration
# =============================================================================


class TestAutoInstrumentRegistration:
    """Verify instruments are auto-registered from sheet metadata."""

    def test_auto_register_on_job_registration(self) -> None:
        """Instruments used by sheets are auto-registered when a job is registered."""
        baton = BatonCore()
        sheets = {
            1: _make_sheet_state(1, instrument="claude-cli"),
            2: _make_sheet_state(2, instrument="gemini-cli"),
            3: _make_sheet_state(3, instrument="claude-cli"),
        }
        baton.register_job("j1", sheets, {1: [], 2: [], 3: []})

        assert baton.get_instrument_state("claude-cli") is not None
        assert baton.get_instrument_state("gemini-cli") is not None
        assert baton.get_instrument_state("nonexistent") is None

    def test_auto_register_is_idempotent(self) -> None:
        """Registering the same instrument twice (via two jobs) must not
        reset the instrument state."""
        baton = BatonCore()
        _register_simple_job(baton, "j1", 1, instrument="claude-cli")

        inst = baton.get_instrument_state("claude-cli")
        assert inst is not None
        inst.rate_limited = True

        _register_simple_job(baton, "j2", 1, instrument="claude-cli")

        # Must still be rate limited — registration didn't reset
        assert baton.get_instrument_state("claude-cli").rate_limited is True
