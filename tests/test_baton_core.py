"""Tests for the baton core — event inbox, main loop, and sheet registry.

The baton core is the event-driven execution heart of the conductor.
It processes events from musicians, timers, external commands, and
the observer, then dispatches ready sheets.

Tests cover: event processing, sheet registration, state transitions,
shutdown behavior, and error resilience.

TDD: tests written before implementation. Red first, then green.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    CancelJob,
    PauseJob,
    ResumeJob,
    SheetAttemptResult,
    SheetSkipped,
    ShutdownRequested,
)
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

# =============================================================================
# Construction
# =============================================================================


class TestBatonCoreConstruction:
    """BatonCore can be created and provides basic properties."""

    async def test_creates_with_defaults(self) -> None:
        baton = BatonCore()
        assert baton is not None
        assert baton.is_running is False

    async def test_no_jobs_registered_initially(self) -> None:
        baton = BatonCore()
        assert baton.job_count == 0
        assert baton.running_sheet_count == 0


# =============================================================================
# Sheet Registry — register/deregister jobs
# =============================================================================


class TestSheetRegistry:
    """The baton tracks sheets across all jobs."""

    async def test_register_job(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="gemini-cli"),
        }
        dependencies: dict[int, list[int]] = {2: [1], 3: [2]}
        baton.register_job("test-job", sheets, dependencies)
        assert baton.job_count == 1

    async def test_register_multiple_jobs(self) -> None:
        baton = BatonCore()
        sheets1 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        sheets2 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="gemini-cli"),
        }
        baton.register_job("job1", sheets1, {})
        baton.register_job("job2", sheets2, {})
        assert baton.job_count == 2

    async def test_deregister_job(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        baton.deregister_job("test-job")
        assert baton.job_count == 0

    async def test_deregister_nonexistent_job_is_noop(self) -> None:
        baton = BatonCore()
        baton.deregister_job("nonexistent")
        assert baton.job_count == 0

    async def test_has_job_presence_predicate(self) -> None:
        # #162: presence predicate for cancel/pause of auto-recovered jobs.
        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        assert baton.has_job("test-job") is False
        baton.register_job("test-job", sheets, {})
        assert baton.has_job("test-job") is True
        baton.deregister_job("test-job")
        assert baton.has_job("test-job") is False

    async def test_get_sheet_state(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        state = baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.sheet_num == 1
        assert state.instrument_name == "claude-code"
        assert state.status == BatonSheetStatus.PENDING

    async def test_get_sheet_state_nonexistent(self) -> None:
        baton = BatonCore()
        state = baton.get_sheet_state("nonexistent", 1)
        assert state is None


# =============================================================================
# Sheet Execution State
# =============================================================================


class TestSheetExecutionState:
    """SheetExecutionState tracks per-sheet conductor state."""

    def test_defaults(self) -> None:
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        assert state.status == BatonSheetStatus.PENDING
        assert state.normal_attempts == 0
        assert state.completion_attempts == 0
        assert state.healing_attempts == 0
        assert state.max_retries == 3
        assert state.max_completion == 5

    def test_custom_retry_limits(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=5,
            max_completion=3,
        )
        assert state.max_retries == 5
        assert state.max_completion == 3


# =============================================================================
# Event Processing — main loop
# =============================================================================


class TestEventProcessing:
    """The baton processes events from the inbox."""

    async def test_process_sheet_completed(self) -> None:
        """A successful sheet attempt marks the sheet as completed."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=3,
            validations_total=3,
            validation_pass_rate=100.0,
        )
        await baton.handle_event(result)

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.COMPLETED

    async def test_process_sheet_failed_increments_attempts(self) -> None:
        """A failed sheet attempt increments normal_attempts."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="EXECUTION_ERROR",
        )
        await baton.handle_event(result)

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.normal_attempts == 1
        # Should be in retry_scheduled, not failed (still has retries)
        assert state.status in (BatonSheetStatus.RETRY_SCHEDULED, BatonSheetStatus.PENDING)

    async def test_process_sheet_skipped(self) -> None:
        """A skipped sheet is marked accordingly."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        skip = SheetSkipped(job_id="j1", sheet_num=1, reason="skip_when condition")
        await baton.handle_event(skip)

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.SKIPPED

    async def test_process_pause_job(self) -> None:
        """PauseJob pauses dispatching for the job."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(PauseJob(job_id="j1"))
        assert baton.is_job_paused("j1") is True

    async def test_process_resume_job(self) -> None:
        """ResumeJob resumes dispatching for a paused job."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(PauseJob(job_id="j1"))
        await baton.handle_event(ResumeJob(job_id="j1"))
        assert baton.is_job_paused("j1") is False

    async def test_process_cancel_job(self) -> None:
        """CancelJob marks all sheets as cancelled and deregisters."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        await baton.handle_event(CancelJob(job_id="j1"))
        # Job should be cleaned up
        assert baton.job_count == 0

    async def test_event_handler_exception_does_not_crash(self) -> None:
        """Per baton spec: handler exceptions are logged, not re-raised."""
        baton = BatonCore()
        # Process an event for a non-existent job — should not raise
        result = SheetAttemptResult(
            job_id="nonexistent",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
        )
        # This should not raise — the baton logs and continues
        await baton.handle_event(result)
        assert baton.job_count == 0, "no jobs should be registered"


# =============================================================================
# Ready sheet resolution
# =============================================================================


class TestReadySheets:
    """The baton identifies which sheets are ready to dispatch."""

    async def test_sheet_with_no_deps_is_ready(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 1
        assert ready[0].sheet_num == 1

    async def test_sheet_with_unmet_deps_not_ready(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})
        ready = baton.get_ready_sheets("j1")
        # Only sheet 1 should be ready (sheet 2 depends on sheet 1)
        assert len(ready) == 1
        assert ready[0].sheet_num == 1

    async def test_sheet_with_met_deps_is_ready(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        # Complete sheet 1
        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=1,
            validations_total=1,
            validation_pass_rate=100.0,
        )
        await baton.handle_event(result)

        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 1
        assert ready[0].sheet_num == 2

    async def test_completed_sheets_not_ready(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=1,
            validations_total=1,
            validation_pass_rate=100.0,
        )
        await baton.handle_event(result)

        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 0

    async def test_paused_job_has_no_ready_sheets(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(PauseJob(job_id="j1"))
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 0

    async def test_skipped_dep_satisfies_downstream(self) -> None:
        """A skipped sheet counts as satisfied for dependency resolution."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        await baton.handle_event(SheetSkipped(job_id="j1", sheet_num=1, reason="test"))
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 1
        assert ready[0].sheet_num == 2


# =============================================================================
# Job completion detection
# =============================================================================


class TestJobCompletion:
    """The baton detects when all sheets in a job are done."""

    async def test_all_completed_means_job_done(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        # Complete both sheets
        for sn in [1, 2]:
            await baton.handle_event(
                SheetAttemptResult(
                    job_id="j1",
                    sheet_num=sn,
                    instrument_name="claude-code",
                    attempt=1,
                    execution_success=True,
                    validations_passed=1,
                    validations_total=1,
                    validation_pass_rate=100.0,
                )
            )

        assert baton.is_job_complete("j1") is True

    async def test_partial_completion_not_done(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=1,
                validations_total=1,
                validation_pass_rate=100.0,
            )
        )

        assert baton.is_job_complete("j1") is False

    async def test_skipped_counts_as_terminal(self) -> None:
        """Skipped sheets count toward job completion."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetSkipped(job_id="j1", sheet_num=1, reason="test"))
        assert baton.is_job_complete("j1") is True


# =============================================================================
# Run loop
# =============================================================================


class TestRunLoop:
    """The main run loop processes events and dispatches sheets."""

    async def test_run_processes_event_from_inbox(self) -> None:
        """Events placed in the inbox get processed."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        # Put a skip event directly into the inbox
        await baton.inbox.put(SheetSkipped(job_id="j1", sheet_num=1, reason="test"))

        task = asyncio.create_task(baton.run())
        try:
            # Wait for processing
            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                state = baton.get_sheet_state("j1", 1)
                if state and state.status == BatonSheetStatus.SKIPPED:
                    break
                await asyncio.sleep(0.05)
            state = baton.get_sheet_state("j1", 1)
            assert state is not None
            assert state.status == BatonSheetStatus.SKIPPED
        finally:
            await baton.inbox.put(ShutdownRequested(graceful=False))
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def test_shutdown_stops_run_loop(self) -> None:
        baton = BatonCore()
        task = asyncio.create_task(baton.run())

        await asyncio.sleep(0.05)
        assert not task.done()

        await baton.inbox.put(ShutdownRequested(graceful=False))
        await asyncio.wait_for(task, timeout=2.0)
        assert task.done()


# =============================================================================
# Diagnostics
# =============================================================================


class TestDiagnostics:
    """The baton provides diagnostic information."""

    async def test_get_diagnostics(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        diag = baton.get_diagnostics("j1")
        assert diag is not None
        assert "sheets" in diag
        assert diag["sheets"]["total"] == 2
        assert diag["sheets"]["pending"] >= 1

    async def test_get_diagnostics_nonexistent_job(self) -> None:
        baton = BatonCore()
        diag = baton.get_diagnostics("nonexistent")
        assert diag is None


class TestEventHandlerErrorContext:
    """#317: a failing event handler must log job_id + sheet_num, not just the
    event type — otherwise correlating a traceback to a specific job in a
    multi-job conductor requires timestamp-based log grepping."""

    async def test_handler_error_logs_job_and_sheet(self) -> None:
        baton = BatonCore()
        baton.register_job(
            "j1",
            {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")},
            {},
        )
        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=7,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
        )

        with (
            patch.object(
                baton, "_handle_attempt_result", side_effect=RuntimeError("boom")
            ),
            patch("marianne.daemon.baton.core._logger") as mock_logger,
        ):
            # handle_event must catch the handler error (not propagate) ...
            await baton.handle_event(result)

        # ... and the error log must carry the job/sheet context.
        mock_logger.error.assert_called_once()
        extra = mock_logger.error.call_args.kwargs["extra"]
        assert extra["event_type"] == "SheetAttemptResult"
        assert extra["job_id"] == "j1"
        assert extra["sheet_num"] == 7


class TestExhaustionPathOrdering:
    """#248: `_handle_exhaustion` tries targeted recovery (fallback → healing →
    escalation) BEFORE consuming the normal-retry budget. Reordering would burn
    retries on the same failing instrument before recovery kicks in. These
    tests lock the precedence so a future reorder fails at CI."""

    @staticmethod
    def _register(sheet: SheetExecutionState, *, escalation: bool = False,
                  healing: bool = False) -> BatonCore:
        baton = BatonCore()
        baton.register_job(
            "j1", {1: sheet}, {},
            escalation_enabled=escalation, self_healing_enabled=healing,
        )
        return baton

    async def test_fallback_precedes_retry(self) -> None:
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=3, fallback_chain=["gemini-cli"],
        )
        baton = self._register(sheet)  # can_retry is True AND fallback available
        await baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.status == BatonSheetStatus.PENDING        # fallback path
        assert sheet.current_instrument_index == 1             # advanced
        assert sheet.normal_attempts == 0                      # retry NOT consumed
        assert baton._fallback_events                          # fallback emitted

    async def test_healing_precedes_retry(self) -> None:
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=3,
        )  # no fallback
        baton = self._register(sheet, healing=True)
        await baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.healing_attempts == 1                     # healing path
        assert sheet.normal_attempts == 0                      # retry NOT consumed
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

    async def test_escalation_precedes_retry(self) -> None:
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=3,
        )
        baton = self._register(sheet, escalation=True)
        await baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.status == BatonSheetStatus.FERMATA        # escalation path
        assert sheet.normal_attempts == 0

    async def test_retry_only_when_no_targeted_recovery(self) -> None:
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=3,
        )
        baton = self._register(sheet)  # no fallback/healing/escalation
        await baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.normal_attempts == 1                      # retry path
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

    async def test_fail_when_nothing_applies(self) -> None:
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=1,
        )
        sheet.normal_attempts = 1  # can_retry is now False
        baton = self._register(sheet)
        await baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.status == BatonSheetStatus.FAILED
