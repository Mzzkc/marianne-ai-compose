"""Tests for the baton dispatch logic — ready sheet resolution and dispatch.

The dispatch module is called after every event. It finds sheets that
are ready to execute and dispatches them, respecting concurrency limits,
instrument rate limits, circuit breakers, and cost budgets.

TDD: tests written before implementation. Red first, then green.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.events import SheetAttemptResult


# =============================================================================
# Basic dispatch
# =============================================================================


class TestBasicDispatch:
    """dispatch_ready finds and dispatches ready sheets."""

    async def test_dispatches_ready_sheet(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)

        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 1
        callback.assert_called_once()
        call_args = callback.call_args
        assert call_args[0][0] == "j1"  # job_id
        assert call_args[0][1] == 1  # sheet_num

    async def test_does_not_dispatch_completed_sheets(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        # Complete the sheet
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1,
            validation_pass_rate=100.0,
        ))

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 0
        callback.assert_not_called()

    async def test_dispatches_multiple_independent_sheets(self) -> None:
        """Sheets without dependencies can dispatch in parallel."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 3


# =============================================================================
# Concurrency limits
# =============================================================================


class TestConcurrencyLimits:
    """dispatch_ready respects global and per-instrument concurrency."""

    async def test_global_concurrency_limit(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
            3: SheetExecutionState(sheet_num=3, instrument_name="codex-cli"),
        }
        baton.register_job("j1", sheets, {})

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=2)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 2
        assert callback.call_count == 2

    async def test_per_instrument_concurrency_limit(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        callback = AsyncMock()
        config = DispatchConfig(
            max_concurrent_sheets=10,
            instrument_concurrency={"claude-code": 2},
        )
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 2

    async def test_cross_instrument_independence(self) -> None:
        """Rate limiting one instrument doesn't block another."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="gemini-cli"),
        }
        baton.register_job("j1", sheets, {})

        callback = AsyncMock()
        config = DispatchConfig(
            max_concurrent_sheets=10,
            instrument_concurrency={"claude-code": 1},
        )
        result = await dispatch_ready(baton, config, callback)
        # 1 claude-code + 1 gemini-cli = 2
        assert result.dispatched_count == 2


# =============================================================================
# Dependency handling
# =============================================================================


class TestDependencyHandling:
    """dispatch_ready respects sheet dependencies."""

    async def test_does_not_dispatch_with_unmet_deps(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 1
        # Only sheet 1 should dispatch
        call_args = callback.call_args_list[0]
        assert call_args[0][1] == 1  # sheet_num

    async def test_dispatches_after_dep_completed(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        # Complete sheet 1
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1,
            validation_pass_rate=100.0,
        ))

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 1
        call_args = callback.call_args_list[0]
        assert call_args[0][1] == 2  # sheet_num


# =============================================================================
# Paused jobs
# =============================================================================


class TestPausedJobs:
    """dispatch_ready skips paused jobs."""

    async def test_paused_job_not_dispatched(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        baton._jobs["j1"].paused = True

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 0


# =============================================================================
# Multi-job fairness
# =============================================================================


class TestMultiJobFairness:
    """dispatch_ready interleaves across jobs."""

    async def test_dispatches_from_multiple_jobs(self) -> None:
        baton = BatonCore()
        baton.register_job("j1", {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }, {})
        baton.register_job("j2", {
            1: SheetExecutionState(sheet_num=1, instrument_name="gemini-cli"),
        }, {})

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 2


# =============================================================================
# Dispatch result tracking
# =============================================================================


class TestDispatchResult:
    """DispatchResult provides feedback on what happened."""

    async def test_result_fields(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 1
        assert isinstance(result.dispatched_sheets, list)
        assert len(result.dispatched_sheets) == 1
        assert result.dispatched_sheets[0] == ("j1", 1)

    async def test_empty_dispatch_result(self) -> None:
        baton = BatonCore()
        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 0
        assert result.dispatched_sheets == []


# =============================================================================
# Shutting down
# =============================================================================


class TestShutdown:
    """dispatch_ready does nothing during shutdown."""

    async def test_no_dispatch_during_shutdown(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        baton._shutting_down = True

        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 0
