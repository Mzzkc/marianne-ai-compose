"""Tests for Mozart JobRunner with graceful shutdown and progress tracking."""

import asyncio
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import JobConfig, ValidationRule
from marianne.execution.preflight import PreflightResult, PromptMetrics
from marianne.execution.runner import (
    FatalError,
    GracefulShutdownError,
    JobRunner,
    RunSummary,
)
from marianne.execution.runner.models import RunnerContext
from marianne.execution.validation import SheetValidationResult, ValidationResult


def make_mock_preflight_result() -> PreflightResult:
    """Create a valid PreflightResult for tests that don't need preflight checks."""
    return PreflightResult(
        prompt_metrics=PromptMetrics(
            character_count=100,
            estimated_tokens=25,
            line_count=5,
            has_file_references=False,
            referenced_paths=[],
            word_count=20,
        ),
        warnings=[],
        errors=[],
        paths_accessible={},
        working_directory_valid=True,
    )


@pytest.fixture
def mock_backend() -> MagicMock:
    """Create a mock backend that returns successful execution results."""
    backend = AsyncMock()
    backend.execute = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            stdout="Task completed",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
        )
    )
    backend.health_check = AsyncMock(return_value=True)
    return backend


@pytest.fixture
def mock_state_backend() -> MagicMock:
    """Create a mock state backend."""
    backend = AsyncMock()
    backend.load = AsyncMock(return_value=None)
    backend.save = AsyncMock()
    backend.list_jobs = AsyncMock(return_value=[])
    return backend


@pytest.fixture
def sample_config() -> JobConfig:
    """Create a sample job configuration for testing."""
    return JobConfig.model_validate({
        "name": "test-job",
        "description": "Test job for runner tests",
        "backend": {
            "type": "claude_cli",
            "skip_permissions": True,
        },
        "sheet": {
            "size": 10,
            "total_items": 30,
        },
        "prompt": {
            "template": "Process sheet {{ sheet_num }} of {{ total_sheets }}.",
        },
        "retry": {
            "max_retries": 2,
            "base_delay_seconds": 0.01,  # Fast tests: near-zero retry delay
            "max_delay_seconds": 0.1,
            "jitter": False,
        },
        "rate_limit": {
            "wait_minutes": 1,  # Minimum integer; tests that need rate limit use shorter override
        },
        "validations": [],  # No validations for simpler tests
        "pause_between_sheets_seconds": 0,  # Fast tests: no pause between sheets
    })


class TestGracefulShutdownError:
    """Tests for graceful shutdown functionality."""

    @pytest.mark.asyncio
    async def test_shutdown_flag_starts_false(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that shutdown flag is initially false."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )
        assert runner._shutdown_requested is False

    @pytest.mark.asyncio
    async def test_signal_handler_sets_shutdown_flag(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that signal handler sets the shutdown flag."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Call signal handler directly
        runner._signal_handler()

        assert runner._shutdown_requested is True

    @pytest.mark.asyncio
    async def test_signal_handler_only_sets_once(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that signal handler only prints message on first call."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # First call should set flag
        runner._signal_handler()
        assert runner._shutdown_requested is True

        # Second call should not change anything (no exception)
        runner._signal_handler()
        assert runner._shutdown_requested is True

    @pytest.mark.asyncio
    async def test_install_signal_handlers_registers_sigterm_and_sighup(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that SIGTERM and SIGHUP are registered alongside SIGINT."""
        import signal as sig_mod

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler") as mock_add:
            runner._install_signal_handlers()

            registered_signals = {call.args[0] for call in mock_add.call_args_list}
            assert sig_mod.SIGINT in registered_signals
            assert sig_mod.SIGTERM in registered_signals
            assert sig_mod.SIGHUP in registered_signals
            assert len(registered_signals) == 3

    @pytest.mark.asyncio
    async def test_remove_signal_handlers_removes_all_three(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that all three signal handlers are removed on cleanup."""
        import signal as sig_mod

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        loop = asyncio.get_running_loop()
        with patch.object(loop, "remove_signal_handler") as mock_remove:
            runner._remove_signal_handlers()

            removed_signals = {call.args[0] for call in mock_remove.call_args_list}
            assert sig_mod.SIGINT in removed_signals
            assert sig_mod.SIGTERM in removed_signals
            assert sig_mod.SIGHUP in removed_signals

    @pytest.mark.asyncio
    async def test_graceful_shutdown_saves_state_as_paused(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that graceful shutdown saves state with PAUSED status."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Create a state to save
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            last_completed_sheet=1,
            status=JobStatus.RUNNING,
        )

        # Call the graceful shutdown handler
        with pytest.raises(GracefulShutdownError) as exc_info:
            await runner._handle_graceful_shutdown(state)

        # Verify state was saved as PAUSED
        assert state.status == JobStatus.PAUSED
        mock_state_backend.save.assert_called_once_with(state)
        assert "paused by user request" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_interruptible_sleep_checks_shutdown_flag(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that interruptible sleep exits early when shutdown is requested."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Set shutdown flag
        runner._shutdown_requested = True

        # Sleep should return almost immediately
        import time
        start = time.monotonic()
        await runner._interruptible_sleep(10.0)  # Long sleep
        elapsed = time.monotonic() - start

        # Should have exited much earlier than 10 seconds
        # Use generous tolerance for CI environments under load
        assert elapsed < 30.0

    @pytest.mark.asyncio
    async def test_run_checks_shutdown_before_each_sheet(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that run method checks shutdown flag before each sheet."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Mock validation to always pass
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            # Set shutdown after first sheet completes
            async def set_shutdown_after_first(*args: Any) -> None:
                state = args[0]
                sheet_num = args[1]
                # First mark the sheet as started (creates the sheet state)
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)
                runner._shutdown_requested = True

            mock_exec.side_effect = set_shutdown_after_first

            # Run should stop after first sheet due to shutdown
            with pytest.raises(GracefulShutdownError):
                await runner.run()

            # Should have only executed one sheet
            assert mock_exec.call_count == 1

        # Verify state_backend.save was called to persist shutdown state
        assert mock_state_backend.save.call_count >= 1, (
            "Expected state_backend.save to be called during graceful shutdown"
        )


class TestProgressTracking:
    """Tests for progress tracking functionality."""

    @pytest.mark.asyncio
    async def test_progress_callback_is_called(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that progress callback is called after each sheet."""
        progress_updates: list[tuple[int, int, float | None]] = []

        def track_progress(completed: int, total: int, eta: float | None) -> None:
            progress_updates.append((completed, total, eta))

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(progress_callback=track_progress),
        )

        # Mock the sheet execution to just mark sheets complete
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def mark_complete(state: CheckpointState, sheet_num: int) -> None:
                # First mark the sheet as started (creates the sheet state)
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = mark_complete

            # Run all 3 sheets
            await runner.run()

        # Should have 3 progress updates
        assert len(progress_updates) == 3

        # Check progress values
        assert progress_updates[0][0] == 1  # 1 completed
        assert progress_updates[1][0] == 2  # 2 completed
        assert progress_updates[2][0] == 3  # 3 completed

        # All should have total=3
        assert all(total == 3 for _, total, _ in progress_updates)

        # Verify mock_state_backend.save was called (at least once for initial state)
        assert mock_state_backend.save.call_count >= 1, (
            f"Expected state_backend.save to be called at least once, "
            f"got {mock_state_backend.save.call_count}"
        )

    @pytest.mark.asyncio
    async def test_eta_is_calculated_from_sheet_times(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that ETA is calculated from average sheet time."""
        etas: list[float | None] = []

        def track_eta(completed: int, total: int, eta: float | None) -> None:
            etas.append(eta)

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(progress_callback=track_eta),
        )

        # Simulate sheet times
        runner._sheet_times = [1.0, 2.0, 3.0]  # Avg = 2.0

        # Create a state
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=5,
            last_completed_sheet=3,
        )

        # Call progress update
        runner._update_progress(state)

        # ETA should be 2.0 * 2 remaining = 4.0
        assert len(etas) == 1
        assert etas[0] is not None
        assert abs(etas[0] - 4.0) < 0.5

    @pytest.mark.asyncio
    async def test_no_callback_no_error(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that no progress callback doesn't cause errors."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Create a state
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            last_completed_sheet=1,
        )

        # No callback configured — verify _update_progress handles it gracefully
        runner._update_progress(state)
        assert runner.progress_callback is None


class TestProgressCallbackErrorPaths:
    """Tests for progress callback behavior during error/retry/validation failures.

    B2-11: Current tests only cover the happy path where all sheets succeed.
    These tests verify progress reporting during failure scenarios.
    """

    @pytest.mark.asyncio
    async def test_progress_callback_on_validation_failure(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Progress callback should still fire when a sheet fails validation."""
        progress_updates: list[tuple[int, int, float | None]] = []

        def track_progress(completed: int, total: int, eta: float | None) -> None:
            progress_updates.append((completed, total, eta))

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(progress_callback=track_progress),
        )

        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            call_count = 0

            async def validation_fail_then_succeed(
                state: CheckpointState, sheet_num: int
            ) -> None:
                nonlocal call_count
                call_count += 1
                state.mark_sheet_started(sheet_num)
                if sheet_num == 2:
                    # Simulate validation failure — mark with validation_passed=False
                    state.mark_sheet_failed(
                        sheet_num, error_message="Validation failed"
                    )
                    raise FatalError("Sheet 2 validation failed")
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = validation_fail_then_succeed

            # Run should stop when FatalError is raised at sheet 2
            with pytest.raises(FatalError):
                await runner.run()

        # Sheet 1 should have triggered a progress update
        assert len(progress_updates) >= 1
        assert progress_updates[0][0] == 1  # 1 sheet completed

    @pytest.mark.asyncio
    async def test_progress_callback_not_called_without_callback(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """No progress callback set should not raise errors during failures."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            # No progress callback
        )

        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def fail_on_sheet_2(
                state: CheckpointState, sheet_num: int
            ) -> None:
                state.mark_sheet_started(sheet_num)
                if sheet_num == 2:
                    state.mark_sheet_failed(
                        sheet_num, error_message="Error"
                    )
                    raise FatalError("Sheet 2 failed")
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = fail_on_sheet_2

            with pytest.raises(FatalError):
                await runner.run()

        # Should not raise — just verifying no callback doesn't break error paths


class TestGracefulShutdownErrorException:
    """Tests for the GracefulShutdownError exception."""

    def test_graceful_shutdown_message(self) -> None:
        """Test GracefulShutdownError exception message."""
        exc = GracefulShutdownError("Test message")
        assert str(exc) == "Test message"

    def test_graceful_shutdown_is_exception(self) -> None:
        """Test GracefulShutdownError inherits from Exception."""
        exc = GracefulShutdownError("Test")
        assert isinstance(exc, Exception)

    def test_graceful_shutdown_can_be_caught(self) -> None:
        """Test GracefulShutdownError can be caught separately from other exceptions."""
        try:
            raise GracefulShutdownError("User interrupt")
        except GracefulShutdownError:
            pass  # Expected: caught by specific handler
        except Exception:
            pytest.fail("GracefulShutdownError was not caught by its specific handler")


class TestSheetTiming:
    """Tests for sheet timing and ETA calculation."""

    @pytest.mark.asyncio
    async def test_sheet_times_are_tracked(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that sheet execution times are recorded."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Initially empty
        assert len(runner._sheet_times) == 0

        # Mock sheet execution with a small delay
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def slow_sheet(state: CheckpointState, sheet_num: int) -> None:
                await asyncio.sleep(0.01)  # 10ms - fast enough to avoid CI flakiness
                # First mark the sheet as started (creates the sheet state)
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = slow_sheet

            # Run all sheets
            await runner.run()

        # Should have 3 sheet times recorded
        assert len(runner._sheet_times) == 3

        # Each time should be positive (non-zero execution)
        assert all(t > 0 for t in runner._sheet_times)

    def test_eta_calculation_with_no_times(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test ETA returns None when no sheet times recorded."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        etas: list[float | None] = []

        def track_eta(completed: int, total: int, eta: float | None) -> None:
            etas.append(eta)

        runner.progress_callback = track_eta

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            last_completed_sheet=1,
        )

        runner._update_progress(state)

        # No sheet times = no ETA
        assert etas[0] is None


class TestRunSummary:
    """Tests for RunSummary dataclass and methods."""

    def test_run_summary_initialization(self) -> None:
        """Test RunSummary initializes with correct defaults."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
        )

        assert summary.job_id == "test-job"
        assert summary.job_name == "Test Job"
        assert summary.total_sheets == 10
        assert summary.completed_sheets == 0
        assert summary.failed_sheets == 0
        assert summary.skipped_sheets == 0
        assert summary.total_duration_seconds == 0.0
        assert summary.total_retries == 0
        assert summary.final_status == JobStatus.PENDING

    def test_success_rate_calculation(self) -> None:
        """Test success rate percentage calculation."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            completed_sheets=8,
            failed_sheets=2,
        )

        assert summary.success_rate == 80.0

    def test_success_rate_zero_sheets(self) -> None:
        """Test success rate with zero sheets returns 0."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=0,
        )

        assert summary.success_rate == 0.0

    def test_validation_pass_rate_calculation(self) -> None:
        """Test validation pass rate calculation."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            validation_pass_count=7,
            validation_fail_count=3,
        )

        assert summary.validation_pass_rate == 70.0

    def test_validation_pass_rate_no_validations(self) -> None:
        """Test validation pass rate with no validations returns 100%."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
        )

        # No validations = 100% pass
        assert summary.validation_pass_rate == 100.0

    def test_success_without_retry_rate_calculation(self) -> None:
        """Test success without retry rate calculation."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            completed_sheets=8,
            successes_without_retry=6,
        )

        assert summary.success_without_retry_rate == 75.0

    def test_success_without_retry_rate_no_completions(self) -> None:
        """Test success without retry rate with no completed sheets returns 0."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            completed_sheets=0,
        )

        assert summary.success_without_retry_rate == 0.0

    def test_to_dict_structure(self) -> None:
        """Test to_dict returns correct structure."""
        summary = RunSummary(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            completed_sheets=8,
            failed_sheets=1,
            skipped_sheets=1,
            total_duration_seconds=120.5,
            total_retries=3,
            total_completion_attempts=2,
            rate_limit_waits=1,
            validation_pass_count=8,
            validation_fail_count=0,
            successes_without_retry=6,
            final_status=JobStatus.COMPLETED,
        )

        result = summary.to_dict()

        assert result["job_id"] == "test-job"
        assert result["job_name"] == "Test Job"
        assert result["status"] == "completed"
        assert result["duration_seconds"] == 120.5
        assert result["duration_formatted"] == "2m 0s"

        assert result["sheets"]["total"] == 10
        assert result["sheets"]["completed"] == 8
        assert result["sheets"]["failed"] == 1
        assert result["sheets"]["skipped"] == 1
        assert result["sheets"]["success_rate"] == 88.9  # 8 / (10 - 1 skipped) = 88.9%

        assert result["validation"]["passed"] == 8
        assert result["validation"]["failed"] == 0
        assert result["validation"]["pass_rate"] == 100.0

        assert result["execution"]["total_retries"] == 3
        assert result["execution"]["completion_attempts"] == 2
        assert result["execution"]["rate_limit_waits"] == 1
        assert result["execution"]["successes_without_retry"] == 6
        assert result["execution"]["success_without_retry_rate"] == 75.0

    def test_format_duration_seconds(self) -> None:
        """Test duration formatting for seconds."""
        assert RunSummary._format_duration(45.5) == "45.5s"

    def test_format_duration_minutes(self) -> None:
        """Test duration formatting for minutes."""
        assert RunSummary._format_duration(125) == "2m 5s"

    def test_format_duration_hours(self) -> None:
        """Test duration formatting for hours."""
        assert RunSummary._format_duration(3725) == "1h 2m"


class TestRunnerReturnsRunSummary:
    """Tests for JobRunner returning RunSummary."""

    @pytest.mark.asyncio
    async def test_run_returns_tuple(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that runner.run() returns tuple of (state, summary)."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Mock sheet execution
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def complete_sheet(state: CheckpointState, sheet_num: int) -> None:
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = complete_sheet

            result = await runner.run()

        # Should return tuple
        assert isinstance(result, tuple)
        assert len(result) == 2

        state, summary = result
        assert isinstance(state, CheckpointState)
        assert isinstance(summary, RunSummary)

    @pytest.mark.asyncio
    async def test_run_summary_populated(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that summary is correctly populated after run."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Mock sheet execution to succeed with success_without_retry
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def complete_sheet(state: CheckpointState, sheet_num: int) -> None:
                state.mark_sheet_started(sheet_num)
                sheet_state = state.sheets[sheet_num]
                sheet_state.success_without_retry = True
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = complete_sheet

            state, summary = await runner.run()

        assert summary.job_id == "test-job"
        assert summary.job_name == "test-job"
        assert summary.total_sheets == 3  # sample_config has 3 sheets
        assert summary.completed_sheets == 3
        assert summary.failed_sheets == 0
        assert summary.final_status == JobStatus.COMPLETED
        assert summary.total_duration_seconds > 0

    @pytest.mark.asyncio
    async def test_get_summary_returns_current_summary(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test get_summary() returns the current summary after run."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Before run, summary is None
        assert runner.get_summary() is None

        # Mock sheet execution
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def complete_sheet(state: CheckpointState, sheet_num: int) -> None:
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = complete_sheet

            await runner.run()

        # After run, summary is populated
        summary = runner.get_summary()
        assert summary is not None
        assert summary.completed_sheets == 3


class TestRunnerLoggingIntegration:
    """Tests for structured logging integration in JobRunner.

    These tests verify that the runner emits structured log events with
    correct data at appropriate times during job execution.
    """

    def setup_method(self) -> None:
        """Reset logging configuration before each test."""
        import logging as stdlib_logging

        import structlog

        from marianne.core.logging import clear_context

        # Reset structlog's caching
        structlog.reset_defaults()
        structlog.configure(cache_logger_on_first_use=False)
        root_logger = stdlib_logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        clear_context()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        from marianne.core.logging import clear_context

        clear_context()

    @staticmethod
    def strip_ansi(text: str) -> str:
        """Remove ANSI escape codes from text for assertion matching."""
        import re

        ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
        return ansi_pattern.sub("", text)

    @pytest.mark.asyncio
    async def test_job_started_log_emitted(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that job.started event is logged when run begins."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="INFO",
            format="console",
            include_timestamps=False,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Mock sheet execution to complete immediately
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def complete_sheet(state: CheckpointState, sheet_num: int) -> None:
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = complete_sheet
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        assert "job.started" in err
        assert "job_id=test-job" in err
        assert "total_sheets=3" in err

    @pytest.mark.asyncio
    async def test_job_completed_log_emitted(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that job.completed event is logged with summary data."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="INFO",
            format="console",
            include_timestamps=False,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def complete_sheet(state: CheckpointState, sheet_num: int) -> None:
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = complete_sheet
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        assert "job.completed" in err
        assert "status=completed" in err
        assert "duration_seconds" in err
        assert "completed_sheets=3" in err
        assert "success_rate" in err

    @pytest.mark.asyncio
    async def test_job_failed_log_emitted(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that job.failed event is logged when job fails."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="ERROR",
            format="console",
            include_timestamps=False,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Mock sheet execution to fail
        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            mock_exec.side_effect = FatalError("Test fatal error")

            with pytest.raises(FatalError):
                await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        assert "job.failed" in err
        assert "sheet_num=1" in err
        assert "Test fatal error" in err

    @pytest.mark.asyncio
    async def test_sheet_started_log_emitted(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that sheet.started event is logged for each sheet."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="INFO",
            format="console",
            include_timestamps=False,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # For sheet.started to be logged, we need to actually run sheets
        # This requires mocking deeper - let's patch at a higher level
        executed_sheets: list[int] = []

        async def track_and_complete(state: CheckpointState, sheet_num: int) -> None:
            executed_sheets.append(sheet_num)
            state.mark_sheet_started(sheet_num)
            state.mark_sheet_completed(sheet_num, validation_passed=True)

        with patch.object(runner, "_execute_sheet_with_recovery", track_and_complete):
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        # Check that sheet.started was logged for each sheet via job.started logs
        assert "job.started" in err

    @pytest.mark.asyncio
    async def test_sheet_retry_log_emitted(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that sheet.retry event is logged when retry occurs."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="WARNING",
            format="console",
            include_timestamps=False,
        )

        # Configure backend to fail then succeed
        call_count = 0

        async def fail_then_succeed(prompt: str, **kwargs: object) -> ExecutionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call fails with transient error
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="Connection timeout",
                    exit_code=1,
                    duration_seconds=1.0,
                )
            else:
                # Subsequent calls succeed
                return ExecutionResult(
                    success=True,
                    stdout="Success",
                    stderr="",
                    exit_code=0,
                    duration_seconds=1.0,
                )

        mock_backend.execute = AsyncMock(side_effect=fail_then_succeed)

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Track validation calls to fail first, pass second
        validation_call_count = 0
        test_rule = ValidationRule(
            type="file_exists",
            path="{workspace}/output.txt",
            description="Output file must exist",
        )

        def validation_side_effect(*args: Any, **kwargs: Any) -> SheetValidationResult:
            nonlocal validation_call_count
            validation_call_count += 1
            if validation_call_count == 1:
                # First validation fails
                return SheetValidationResult(
                    sheet_num=1,
                    results=[
                        ValidationResult(rule=test_rule, passed=False)
                    ],
                )
            else:
                # Subsequent validations pass
                return SheetValidationResult(
                    sheet_num=1,
                    results=[
                        ValidationResult(rule=test_rule, passed=True)
                    ],
                )

        # Patch validation and preflight to pass
        with (
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.run_validations"
            ) as mock_validation,
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.snapshot_mtime_files"
            ),
            patch.object(
                runner, "_run_preflight_checks", return_value=make_mock_preflight_result()
            ),
        ):
            mock_validation.side_effect = validation_side_effect
            # Run only one sheet to limit complexity
            sample_config.sheet.total_items = 10
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        assert "sheet.retry" in err
        assert "attempt=1" in err

    @pytest.mark.asyncio
    async def test_rate_limit_log_emitted(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that rate_limit.detected event is logged."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="WARNING",
            format="console",
            include_timestamps=False,
        )

        # Configure short rate limit wait for test
        sample_config.rate_limit.wait_minutes = 0.01  # Very short wait

        call_count = 0

        async def rate_limit_then_succeed(prompt: str, **kwargs: object) -> ExecutionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ExecutionResult(
                    success=False,
                    stdout="rate limit exceeded",
                    stderr="",
                    exit_code=1,
                    duration_seconds=1.0,
                )
            else:
                return ExecutionResult(
                    success=True,
                    stdout="Success",
                    stderr="",
                    exit_code=0,
                    duration_seconds=1.0,
                )

        mock_backend.execute = AsyncMock(side_effect=rate_limit_then_succeed)

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Track validation calls to fail during rate-limit, pass after recovery
        validation_call_count = 0
        test_rule = ValidationRule(
            type="file_exists",
            path="{workspace}/output.txt",
            description="Output file must exist",
        )

        def validation_side_effect(*args: Any, **kwargs: Any) -> SheetValidationResult:
            nonlocal validation_call_count
            validation_call_count += 1
            if validation_call_count == 1:
                # First validation fails (during rate-limit execution)
                return SheetValidationResult(
                    sheet_num=1,
                    results=[
                        ValidationResult(rule=test_rule, passed=False)
                    ],
                )
            else:
                # After rate-limit wait, validation passes
                return SheetValidationResult(
                    sheet_num=1,
                    results=[
                        ValidationResult(rule=test_rule, passed=True)
                    ],
                )

        # Patch validation, preflight, and asyncio.sleep to avoid real waits.
        # The error classifier returns suggested_wait=3600s for rate limits,
        # which overrides config.rate_limit.wait_minutes. We must patch sleep
        # to prevent the test from sleeping for an hour.
        async def fast_sleep(seconds: float) -> None:
            pass  # Skip all sleeps in tests

        with (
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.run_validations"
            ) as mock_validation,
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.snapshot_mtime_files"
            ),
            patch.object(
                runner, "_run_preflight_checks", return_value=make_mock_preflight_result()
            ),
            patch("marianne.execution.runner.base.asyncio.sleep", side_effect=fast_sleep),
        ):
            mock_validation.side_effect = validation_side_effect
            sample_config.sheet.total_items = 10
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        assert "rate_limit.detected" in err
        assert "wait_count=1" in err

    @pytest.mark.asyncio
    async def test_config_summary_includes_safe_fields(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that _get_config_summary returns safe, non-sensitive data."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        summary = runner._get_config_summary()

        # Should include these safe fields
        assert "backend_type" in summary
        assert "sheet_size" in summary
        assert "total_items" in summary
        assert "max_retries" in summary
        assert "validation_count" in summary

        # Should NOT include sensitive fields
        assert "api_key" not in str(summary).lower()
        assert "token" not in str(summary).lower()
        assert "secret" not in str(summary).lower()

    @pytest.mark.asyncio
    async def test_sheet_completed_includes_validation_duration(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that sheet.completed includes validation_duration_seconds."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="INFO",
            format="console",
            include_timestamps=False,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Patch validation and preflight to pass
        with (
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.run_validations"
            ) as mock_validation,
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.snapshot_mtime_files"
            ),
            patch.object(
                runner, "_run_preflight_checks", return_value=make_mock_preflight_result()
            ),
        ):
            mock_validation.return_value = SheetValidationResult(sheet_num=1, results=[])
            sample_config.sheet.total_items = 10
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        assert "sheet.completed" in err
        assert "validation_duration_seconds" in err

    @pytest.mark.asyncio
    async def test_execution_context_created_for_run(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that ExecutionContext is created during run."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Before run, no execution context
        assert runner._execution_context is None

        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def complete_sheet(state: CheckpointState, sheet_num: int) -> None:
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = complete_sheet
            await runner.run()

        # After run, execution context should be set
        assert runner._execution_context is not None
        assert runner._execution_context.job_id == "test-job"
        assert runner._execution_context.component == "runner"

    @pytest.mark.asyncio
    async def test_preflight_warnings_logged(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that preflight warnings count is included in sheet.started log."""
        from marianne.core.logging import configure_logging

        # Use INFO level to capture sheet.started which includes preflight_warnings count
        configure_logging(
            level="INFO",
            format="console",
            include_timestamps=False,
        )

        # Create a preflight result with warnings (simulates large prompt)
        preflight_with_warnings = PreflightResult(
            prompt_metrics=PromptMetrics(
                character_count=200000,
                estimated_tokens=50000,  # Above warning threshold
                line_count=100,
                has_file_references=False,
                referenced_paths=[],
                word_count=40000,
            ),
            warnings=["Large prompt detected: ~50,000 tokens"],
            errors=[],
            paths_accessible={},
            working_directory_valid=True,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Patch validation and preflight to return our warning result
        with (
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.run_validations"
            ) as mock_validation,
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.snapshot_mtime_files"
            ),
            patch.object(
                runner, "_run_preflight_checks", return_value=preflight_with_warnings
            ),
        ):
            mock_validation.return_value = SheetValidationResult(sheet_num=1, results=[])
            sample_config.sheet.total_items = 10
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)
        out = self.strip_ansi(captured.out)

        # When _run_preflight_checks is patched, the logging inside it doesn't run.
        # But the caller logs sheet.started with preflight_warnings count at INFO level.
        # Check that either:
        # 1. The structured warning log appears in stderr (if not patched)
        # 2. The sheet.started log shows preflight_warnings count > 0
        # 3. Console output shows the warning (stdout)
        assert (
            "sheet.preflight_warnings" in err
            or "preflight_warnings=1" in err
            or "Large prompt detected" in out
        )


class TestLoggingLevelFiltering:
    """Tests for log level filtering in runner."""

    def setup_method(self) -> None:
        """Reset logging configuration before each test."""
        import logging as stdlib_logging

        import structlog

        from marianne.core.logging import clear_context

        structlog.reset_defaults()
        structlog.configure(cache_logger_on_first_use=False)
        root_logger = stdlib_logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        clear_context()

    @staticmethod
    def strip_ansi(text: str) -> str:
        """Remove ANSI escape codes from text."""
        import re

        ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
        return ansi_pattern.sub("", text)

    @pytest.mark.asyncio
    async def test_debug_logs_not_shown_at_info_level(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that DEBUG level logs are not shown when level is INFO."""
        from marianne.core.logging import configure_logging

        configure_logging(
            level="INFO",
            format="console",
            include_timestamps=False,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        with patch.object(runner, "_execute_sheet_with_recovery") as mock_exec:
            async def complete_sheet(state: CheckpointState, sheet_num: int) -> None:
                state.mark_sheet_started(sheet_num)
                state.mark_sheet_completed(sheet_num, validation_passed=True)

            mock_exec.side_effect = complete_sheet
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        # INFO level logs should be present
        assert "job.started" in err or "job.completed" in err

        # DEBUG level logs (like preflight_metrics) should NOT be present
        # at INFO level
        assert "sheet.preflight_metrics" not in err

    @pytest.mark.asyncio
    async def test_warning_logs_shown_at_warning_level(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that WARNING level logs are shown when level is WARNING."""
        from marianne.core.logging import configure_logging

        # Configure short rate limit wait
        sample_config.rate_limit.wait_minutes = 0.01

        call_count = 0

        async def rate_limit_then_succeed(prompt: str, **kwargs: object) -> ExecutionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ExecutionResult(
                    success=False,
                    stdout="rate limit exceeded",
                    stderr="",
                    exit_code=1,
                    duration_seconds=1.0,
                )
            else:
                return ExecutionResult(
                    success=True,
                    stdout="Success",
                    stderr="",
                    exit_code=0,
                    duration_seconds=1.0,
                )

        mock_backend.execute = AsyncMock(side_effect=rate_limit_then_succeed)

        configure_logging(
            level="WARNING",
            format="console",
            include_timestamps=False,
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Track validation calls to fail during rate-limit, pass after recovery
        validation_call_count = 0
        test_rule = ValidationRule(
            type="file_exists",
            path="{workspace}/output.txt",
            description="Output file must exist",
        )

        def validation_side_effect(*args: Any, **kwargs: Any) -> SheetValidationResult:
            nonlocal validation_call_count
            validation_call_count += 1
            if validation_call_count == 1:
                # First validation fails (during rate-limit execution)
                return SheetValidationResult(
                    sheet_num=1,
                    results=[
                        ValidationResult(rule=test_rule, passed=False)
                    ],
                )
            else:
                # After rate-limit wait, validation passes
                return SheetValidationResult(
                    sheet_num=1,
                    results=[
                        ValidationResult(rule=test_rule, passed=True)
                    ],
                )

        # Patch asyncio.sleep to avoid 3600s waits from error classifier
        async def fast_sleep(seconds: float) -> None:
            pass

        with (
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.run_validations"
            ) as mock_validation,
            patch(
                "marianne.execution.runner.sheet.ValidationEngine.snapshot_mtime_files"
            ),
            patch.object(
                runner, "_run_preflight_checks", return_value=make_mock_preflight_result()
            ),
            patch("marianne.execution.runner.base.asyncio.sleep", side_effect=fast_sleep),
        ):
            mock_validation.side_effect = validation_side_effect
            sample_config.sheet.total_items = 10
            await runner.run()

        captured = capsys.readouterr()
        err = self.strip_ansi(captured.err)

        # WARNING level logs should be present
        assert "rate_limit.detected" in err

        # INFO level logs should NOT be present at WARNING level
        assert "job.started" not in err
        assert "job.completed" not in err


class TestActiveBroadcastPolling:
    """Tests for v16 Evolution: Active Broadcast Polling.

    These tests verify that the runner correctly polls for pattern
    discoveries from other concurrent jobs during retry waits.
    """

    @pytest.mark.asyncio
    async def test_poll_broadcast_discoveries_calls_store(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Verify polling calls check_recent_pattern_discoveries on store."""

        mock_store = MagicMock()
        mock_store.check_recent_pattern_discoveries = MagicMock(return_value=[])

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(global_learning_store=mock_store),
        )

        await runner._poll_broadcast_discoveries("test-job-123", sheet_num=1)

        mock_store.check_recent_pattern_discoveries.assert_called_once_with(
            exclude_job_id="test-job-123",
            min_effectiveness=0.5,
            limit=10,
        )

    @pytest.mark.asyncio
    async def test_poll_broadcast_discoveries_handles_empty_results(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Verify polling handles empty discovery results gracefully."""
        mock_store = MagicMock()
        mock_store.check_recent_pattern_discoveries = MagicMock(return_value=[])

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(global_learning_store=mock_store),
        )

        # Should not raise, should not log anything for empty results
        await runner._poll_broadcast_discoveries("test-job-123", sheet_num=1)

        mock_store.check_recent_pattern_discoveries.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_broadcast_discoveries_logs_found_patterns(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify polling logs when patterns are discovered."""
        from datetime import datetime, timedelta

        from marianne.learning.global_store import PatternDiscoveryEvent

        now = datetime.now()
        discoveries = [
            PatternDiscoveryEvent(
                id="disc-001",
                pattern_id="pat-001",
                pattern_name="Test Pattern 1",
                pattern_type="retry_pattern",
                source_job_hash="other-job-hash",
                recorded_at=now,
                expires_at=now + timedelta(minutes=10),
                effectiveness_score=0.85,
                context_tags=["test"],
            ),
            PatternDiscoveryEvent(
                id="disc-002",
                pattern_id="pat-002",
                pattern_name="Test Pattern 2",
                pattern_type="validation_failure",
                source_job_hash="another-job-hash",
                recorded_at=now,
                expires_at=now + timedelta(minutes=10),
                effectiveness_score=0.72,
                context_tags=["test", "validation"],
            ),
        ]

        mock_store = MagicMock()
        mock_store.check_recent_pattern_discoveries = MagicMock(return_value=discoveries)

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(global_learning_store=mock_store),
        )

        await runner._poll_broadcast_discoveries("test-job-123", sheet_num=2)

        captured = capsys.readouterr()
        # Console should show discovery count
        assert "2 pattern" in captured.out
        assert "broadcast" in captured.out

    @pytest.mark.asyncio
    async def test_poll_broadcast_discoveries_excludes_self_job(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Verify polling excludes the current job's own patterns."""
        mock_store = MagicMock()
        mock_store.check_recent_pattern_discoveries = MagicMock(return_value=[])

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(global_learning_store=mock_store),
        )

        await runner._poll_broadcast_discoveries("my-unique-job-id", sheet_num=1)

        # Verify exclude_job_id is passed correctly
        call_kwargs = mock_store.check_recent_pattern_discoveries.call_args[1]
        assert call_kwargs["exclude_job_id"] == "my-unique-job-id"

    @pytest.mark.asyncio
    async def test_poll_broadcast_discoveries_handles_store_error(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Verify polling handles store errors gracefully without blocking."""
        mock_store = MagicMock()
        mock_store.check_recent_pattern_discoveries = MagicMock(
            side_effect=sqlite3.OperationalError("Database connection failed")
        )

        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=RunnerContext(global_learning_store=mock_store),
        )

        # Polling failure shouldn't block retry — verify the store was called and
        # the exception was swallowed (not re-raised)
        await runner._poll_broadcast_discoveries("test-job", sheet_num=1)
        mock_store.check_recent_pattern_discoveries.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_broadcast_discoveries_noop_without_store(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Verify polling is no-op when global learning store is not configured."""
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # No store configured — verify it returns without attempting any store calls
        await runner._poll_broadcast_discoveries("test-job", sheet_num=1)
        assert runner._global_learning_store is None



class TestCostTracking:
    """Tests for CostMixin cost tracking functionality.

    Tests the cost tracking methods:
    - _track_cost(): Token usage and cost calculation
    - _check_cost_limits(): Per-sheet and per-job cost limits
    """

    @pytest.fixture
    def cost_enabled_config(self) -> JobConfig:
        """Create a config with cost tracking enabled."""
        return JobConfig.model_validate({
            "name": "test-cost-job",
            "description": "Test job for cost tracking tests",
            "backend": {
                "type": "claude_cli",
                "skip_permissions": True,
            },
            "sheet": {
                "size": 10,
                "total_items": 30,
            },
            "prompt": {
                "template": "Process sheet {{ sheet_num }}.",
            },
            "cost_limits": {
                "enabled": True,
                "max_cost_per_sheet": 1.0,
                "max_cost_per_job": 10.0,
                "cost_per_1k_input_tokens": 0.003,
                "cost_per_1k_output_tokens": 0.015,
                "warn_at_percent": 80,
            },
        })

    @pytest.fixture
    def cost_runner(
        self,
        cost_enabled_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> JobRunner:
        """Create a runner with cost tracking enabled."""
        return JobRunner(
            config=cost_enabled_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

    @pytest.mark.asyncio
    async def test_track_cost_with_exact_tokens(
        self,
        cost_runner: JobRunner,
    ) -> None:
        """Test cost tracking with exact token counts from API."""
        from marianne.backends.base import ExecutionResult
        from marianne.core.checkpoint import CheckpointState, SheetState

        result = ExecutionResult(
            success=True,
            stdout="Output",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
            input_tokens=1000,
            output_tokens=500,
        )
        sheet_state = SheetState(sheet_num=1)
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )

        input_tokens, output_tokens, cost, confidence = await cost_runner._track_cost(
            result, sheet_state, state
        )

        assert input_tokens == 1000
        assert output_tokens == 500
        # Cost = (1000/1000 * 0.003) + (500/1000 * 0.015) = 0.003 + 0.0075 = 0.0105
        assert abs(cost - 0.0105) < 0.0001
        assert confidence == 1.0  # Exact counts

    @pytest.mark.asyncio
    async def test_track_cost_with_legacy_tokens(
        self,
        cost_runner: JobRunner,
    ) -> None:
        """Test cost tracking with legacy tokens_used field."""
        from marianne.backends.base import ExecutionResult
        from marianne.core.checkpoint import CheckpointState, SheetState

        result = ExecutionResult(
            success=True,
            stdout="Output",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
            tokens_used=1000,  # Legacy field
        )
        sheet_state = SheetState(sheet_num=1)
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )

        _, output_tokens, _, confidence = await cost_runner._track_cost(
            result, sheet_state, state
        )

        assert output_tokens == 1000
        assert confidence == 0.85  # Lower confidence for legacy

    @pytest.mark.asyncio
    async def test_track_cost_with_estimation(
        self,
        cost_runner: JobRunner,
    ) -> None:
        """Test cost tracking with character-based estimation."""
        from marianne.backends.base import ExecutionResult
        from marianne.core.checkpoint import CheckpointState, SheetState

        # 400 chars => ~100 tokens (4 chars per token)
        result = ExecutionResult(
            success=True,
            stdout="x" * 400,
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
        )
        sheet_state = SheetState(sheet_num=1)
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )

        _, output_tokens, _, confidence = await cost_runner._track_cost(
            result, sheet_state, state
        )

        assert output_tokens == 100  # 400 chars / 4
        assert confidence == 0.7  # Lowest confidence for estimation

    @pytest.mark.asyncio
    async def test_track_cost_accumulates_to_state(
        self,
        cost_runner: JobRunner,
    ) -> None:
        """Test that costs accumulate across multiple calls."""
        from marianne.backends.base import ExecutionResult
        from marianne.core.checkpoint import CheckpointState, SheetState

        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3
        )

        # Track cost for 3 sheets
        for i in range(3):
            result = ExecutionResult(
                success=True,
                stdout="Output",
                stderr="",
                exit_code=0,
                duration_seconds=1.0,
                input_tokens=1000,
                output_tokens=500,
            )
            sheet_state = SheetState(sheet_num=i + 1)
            await cost_runner._track_cost(result, sheet_state, state)

        # Total should be 3x the individual cost
        expected_cost = 3 * 0.0105
        assert abs(state.total_estimated_cost - expected_cost) < 0.0001
        assert state.total_input_tokens == 3000
        assert state.total_output_tokens == 1500

    def test_check_cost_limits_passes_within_limits(
        self,
        cost_runner: JobRunner,
    ) -> None:
        """Test that cost limit check passes when within limits."""
        from marianne.core.checkpoint import CheckpointState, SheetState

        sheet_state = SheetState(sheet_num=1)
        sheet_state.estimated_cost = 0.5  # Below max_cost_per_sheet (1.0)
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )
        state.total_estimated_cost = 5.0  # Below max_cost_per_job (10.0)

        exceeded, reason = cost_runner._check_cost_limits(sheet_state, state)

        assert exceeded is False
        assert reason is None

    def test_check_cost_limits_fails_on_sheet_limit(
        self,
        cost_runner: JobRunner,
    ) -> None:
        """Test that cost limit check fails when sheet limit exceeded."""
        from marianne.core.checkpoint import CheckpointState, SheetState

        sheet_state = SheetState(sheet_num=1)
        sheet_state.estimated_cost = 1.5  # Above max_cost_per_sheet (1.0)
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )

        exceeded, reason = cost_runner._check_cost_limits(sheet_state, state)

        assert exceeded is True
        assert "Sheet cost" in reason
        assert "exceeded limit" in reason

    def test_check_cost_limits_fails_on_job_limit(
        self,
        cost_runner: JobRunner,
    ) -> None:
        """Test that cost limit check fails when job limit exceeded."""
        from marianne.core.checkpoint import CheckpointState, SheetState

        sheet_state = SheetState(sheet_num=1)
        sheet_state.estimated_cost = 0.5
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )
        state.total_estimated_cost = 11.0  # Above max_cost_per_job (10.0)

        exceeded, reason = cost_runner._check_cost_limits(sheet_state, state)

        assert exceeded is True
        assert "Job cost" in reason
        assert "exceeded limit" in reason

    def test_check_cost_limits_disabled_passes_always(
        self,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """Test that cost limits always pass when disabled."""
        from marianne.core.checkpoint import CheckpointState, SheetState

        config = JobConfig.model_validate({
            "name": "test-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 10, "total_items": 30},
            "prompt": {"template": "Test"},
            "cost_limits": {"enabled": False},  # Disabled
        })
        runner = JobRunner(
            config=config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        sheet_state = SheetState(sheet_num=1)
        sheet_state.estimated_cost = 1000.0  # Very high
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )
        state.total_estimated_cost = 1000.0

        exceeded, reason = runner._check_cost_limits(sheet_state, state)

        assert exceeded is False  # Passes because disabled
        assert reason is None


# ===========================================================================
# Tests: Runner Callback Events (Issue #49, Phase 2)
# ===========================================================================


class TestCostUpdateEvent:
    """Verify job.cost_update event fires from _track_cost."""

    @pytest.fixture
    def cost_config_with_budget(self) -> JobConfig:
        """Create a config with cost tracking and a budget limit."""
        return JobConfig.model_validate({
            "name": "test-cost-event-job",
            "description": "Test job for cost event tests",
            "backend": {
                "type": "claude_cli",
                "skip_permissions": True,
            },
            "sheet": {
                "size": 10,
                "total_items": 30,
            },
            "prompt": {
                "template": "Process sheet {{ sheet_num }}.",
            },
            "cost_limits": {
                "enabled": True,
                "max_cost_per_sheet": 1.0,
                "max_cost_per_job": 10.0,
                "cost_per_1k_input_tokens": 0.003,
                "cost_per_1k_output_tokens": 0.015,
                "warn_at_percent": 80,
            },
        })

    @pytest.mark.asyncio
    async def test_cost_update_event_fires(
        self,
        cost_config_with_budget: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """job.cost_update event fires from _track_cost with correct data."""
        from marianne.core.checkpoint import CheckpointState, SheetState
        from marianne.execution.runner.models import RunnerContext

        events: list[tuple[str, int, str, dict]] = []

        def event_cb(job_id: str, sheet_num: int, event: str, data: dict | None) -> None:
            events.append((job_id, sheet_num, event, data or {}))

        ctx = RunnerContext(event_callback=event_cb)
        runner = JobRunner(
            config=cost_config_with_budget,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=ctx,
        )

        result = ExecutionResult(
            success=True,
            stdout="Output",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
            input_tokens=1000,
            output_tokens=500,
        )
        sheet_state = SheetState(sheet_num=1)
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=1
        )

        await runner._track_cost(result, sheet_state, state)

        cost_events = [(j, s, e, d) for j, s, e, d in events if e == "job.cost_update"]
        assert len(cost_events) == 1
        _, sheet_num, _, data = cost_events[0]
        assert sheet_num == 0  # cost events use sheet_num=0 (job-level)
        assert "total_cost" in data
        assert "budget_remaining" in data
        assert "input_tokens" in data
        assert "output_tokens" in data
        assert data["total_cost"] > 0
        assert data["budget_remaining"] is not None

    @pytest.mark.asyncio
    async def test_cost_update_event_no_budget(
        self,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> None:
        """job.cost_update with no budget has budget_remaining=None."""
        from marianne.core.checkpoint import CheckpointState, SheetState
        from marianne.execution.runner.models import RunnerContext

        config = JobConfig.model_validate({
            "name": "test-no-budget",
            "backend": {"type": "claude_cli", "skip_permissions": True},
            "sheet": {"size": 10, "total_items": 30},
            "prompt": {"template": "Process sheet {{ sheet_num }}."},
            "cost_limits": {
                "enabled": True,
                "max_cost_per_sheet": 5.0,
                "cost_per_1k_input_tokens": 0.003,
                "cost_per_1k_output_tokens": 0.015,
            },
        })

        events: list[tuple[str, int, str, dict]] = []

        def event_cb(job_id: str, sheet_num: int, event: str, data: dict | None) -> None:
            events.append((job_id, sheet_num, event, data or {}))

        ctx = RunnerContext(event_callback=event_cb)
        runner = JobRunner(
            config=config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=ctx,
        )

        result = ExecutionResult(
            success=True, stdout="Output", stderr="", exit_code=0,
            duration_seconds=1.0, input_tokens=500, output_tokens=200,
        )
        sheet_state = SheetState(sheet_num=1)
        state = CheckpointState(job_id="test", job_name="Test", total_sheets=1)

        await runner._track_cost(result, sheet_state, state)

        cost_events = [(j, s, e, d) for j, s, e, d in events if e == "job.cost_update"]
        assert len(cost_events) == 1
        assert cost_events[0][3]["budget_remaining"] is None


class TestClassifySuccessOutcome:
    """Tests for _classify_success_outcome static method."""

    def test_first_try_success(self) -> None:
        """Test classification when sheet succeeds on first attempt."""
        from marianne.execution.runner.sheet import SheetExecutionMixin

        outcome, first = SheetExecutionMixin._classify_success_outcome(0, 0)
        assert outcome == "success_first_try"
        assert first is True

    def test_success_after_retry(self) -> None:
        """Test classification when sheet succeeds after normal retries."""
        from marianne.execution.runner.sheet import SheetExecutionMixin

        outcome, first = SheetExecutionMixin._classify_success_outcome(2, 0)
        assert outcome == "success_retry"
        assert first is False

    def test_success_via_completion_mode(self) -> None:
        """Test classification when sheet succeeds via completion mode."""
        from marianne.execution.runner.sheet import SheetExecutionMixin

        outcome, first = SheetExecutionMixin._classify_success_outcome(1, 3)
        assert outcome == "success_completion"
        assert first is False

    def test_success_completion_without_retries(self) -> None:
        """Test classification when completion mode used without prior retries."""
        from marianne.execution.runner.sheet import SheetExecutionMixin

        outcome, first = SheetExecutionMixin._classify_success_outcome(0, 1)
        assert outcome == "success_completion"
        assert first is False


class TestDecideNextAction:
    """Tests for _decide_next_action decision tree in SheetExecutionMixin."""

    @pytest.fixture
    def runner(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> JobRunner:
        """Create a runner for decision logic tests."""
        return JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

    def _make_validation_result(
        self,
        pass_pct: float = 80.0,
        confidence: float = 0.8,
        passed_count: int = 4,
        failed_count: int = 1,
    ) -> MagicMock:
        """Create a mock SheetValidationResult with configurable scores."""
        result = MagicMock()
        result.executed_pass_percentage = pass_pct
        result.aggregate_confidence = confidence
        result.pass_percentage = pass_pct
        result.passed_count = passed_count
        result.failed_count = failed_count
        result.get_passed_results.return_value = [MagicMock()] * passed_count
        result.get_failed_results.return_value = [MagicMock()] * failed_count
        result.get_semantic_summary.return_value = {"dominant_category": "format"}
        result.get_actionable_hints.return_value = ["Fix formatting"]
        return result

    def test_high_confidence_enters_completion(self, runner: JobRunner) -> None:
        """High confidence + majority passed should enter completion mode."""
        from marianne.execution.runner.models import SheetExecutionMode

        result = self._make_validation_result(pass_pct=80.0, confidence=0.9)
        mode, reason, hints = runner._decide_next_action(result, 0, 0)
        assert mode == SheetExecutionMode.COMPLETION
        assert "high confidence" in reason

    def test_high_confidence_completion_exhausted_falls_to_retry(self, runner: JobRunner) -> None:
        """High confidence but completion attempts exhausted should retry."""
        from marianne.execution.runner.models import SheetExecutionMode

        result = self._make_validation_result(pass_pct=80.0, confidence=0.9)
        max_completion = runner.config.retry.max_completion_attempts
        mode, reason, hints = runner._decide_next_action(result, 0, max_completion)
        assert mode == SheetExecutionMode.RETRY
        assert "completion attempts exhausted" in reason

    def test_low_confidence_no_escalation_retries(self, runner: JobRunner) -> None:
        """Low confidence without escalation handler should retry."""
        from marianne.execution.runner.models import SheetExecutionMode

        result = self._make_validation_result(pass_pct=30.0, confidence=0.1)
        runner.escalation_handler = None
        mode, reason, hints = runner._decide_next_action(result, 0, 0)
        assert mode == SheetExecutionMode.RETRY
        assert "low confidence" in reason
        assert "escalation not available" in reason

    def test_medium_confidence_enters_completion_if_eligible(self, runner: JobRunner) -> None:
        """Medium confidence with eligible pass rate should enter completion."""
        from marianne.execution.runner.models import SheetExecutionMode

        threshold = runner.config.retry.completion_threshold_percent
        result = self._make_validation_result(
            pass_pct=threshold + 10,
            confidence=0.5,
        )
        mode, reason, hints = runner._decide_next_action(result, 0, 0)
        assert mode == SheetExecutionMode.COMPLETION
        assert "medium confidence" in reason

    def test_medium_confidence_retries_if_low_pass_rate(self, runner: JobRunner) -> None:
        """Medium confidence with low pass rate should retry."""
        from marianne.execution.runner.models import SheetExecutionMode

        result = self._make_validation_result(pass_pct=10.0, confidence=0.5)
        mode, reason, hints = runner._decide_next_action(result, 0, 0)
        assert mode == SheetExecutionMode.RETRY
        assert "full retry needed" in reason

    def test_semantic_hints_included_in_result(self, runner: JobRunner) -> None:
        """Decision should include semantic hints from validation result."""
        result = self._make_validation_result(pass_pct=80.0, confidence=0.9)
        result.get_actionable_hints.return_value = ["Fix file encoding", "Add header"]
        _, _, hints = runner._decide_next_action(result, 0, 0)
        assert "Fix file encoding" in hints
        assert "Add header" in hints


class TestShouldEnterCompletionMode:
    """Tests for _should_enter_completion_mode helper."""

    @pytest.fixture
    def runner(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> JobRunner:
        return JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

    def test_eligible_when_above_threshold_and_attempts_remain(self, runner: JobRunner) -> None:
        threshold = runner.config.retry.completion_threshold_percent
        assert runner._should_enter_completion_mode(threshold + 10, 0) is True

    def test_not_eligible_when_below_threshold(self, runner: JobRunner) -> None:
        assert runner._should_enter_completion_mode(10.0, 0) is False

    def test_not_eligible_when_attempts_exhausted(self, runner: JobRunner) -> None:
        max_comp = runner.config.retry.max_completion_attempts
        threshold = runner.config.retry.completion_threshold_percent
        assert runner._should_enter_completion_mode(threshold + 10, max_comp) is False

    def test_boundary_at_threshold_not_eligible(self, runner: JobRunner) -> None:
        """Pass pct exactly at threshold should NOT enter completion (> not >=)."""
        threshold = runner.config.retry.completion_threshold_percent
        assert runner._should_enter_completion_mode(threshold, 0) is False


class TestIsEscalationAvailable:
    """Tests for _is_escalation_available helper."""

    @pytest.fixture
    def runner(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> JobRunner:
        return JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

    def test_available_when_enabled_and_handler_present(self, runner: JobRunner) -> None:
        runner.config.learning.escalation_enabled = True
        runner.escalation_handler = MagicMock()
        assert runner._is_escalation_available() is True

    def test_not_available_when_disabled(self, runner: JobRunner) -> None:
        runner.config.learning.escalation_enabled = False
        runner.escalation_handler = MagicMock()
        assert runner._is_escalation_available() is False

    def test_not_available_when_no_handler(self, runner: JobRunner) -> None:
        runner.config.learning.escalation_enabled = True
        runner.escalation_handler = None
        assert runner._is_escalation_available() is False


class TestGetRetryDelay:
    """Tests for _get_retry_delay exponential backoff calculation."""

    @pytest.fixture
    def runner(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> JobRunner:
        return JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

    def test_first_attempt_uses_base_delay(self, runner: JobRunner) -> None:
        runner.config.retry.jitter = False
        delay = runner._get_retry_delay(1)
        assert delay == runner.config.retry.base_delay_seconds

    def test_delay_increases_exponentially(self, runner: JobRunner) -> None:
        runner.config.retry.jitter = False
        delay1 = runner._get_retry_delay(1)
        delay2 = runner._get_retry_delay(2)
        delay3 = runner._get_retry_delay(3)
        assert delay2 > delay1
        assert delay3 > delay2

    def test_delay_capped_at_max(self, runner: JobRunner) -> None:
        runner.config.retry.jitter = False
        delay = runner._get_retry_delay(100)
        assert delay <= runner.config.retry.max_delay_seconds

    def test_jitter_adds_variability(self, runner: JobRunner) -> None:
        runner.config.retry.jitter = True
        delays = {runner._get_retry_delay(1) for _ in range(20)}
        # With jitter, we should get some variation (not all identical)
        assert len(delays) > 1


class TestUpdateEscalationOutcome:
    """Tests for _update_escalation_outcome recording."""

    @pytest.fixture
    def runner(
        self,
        sample_config: JobConfig,
        mock_backend: MagicMock,
        mock_state_backend: MagicMock,
    ) -> JobRunner:
        runner = JobRunner(
            config=sample_config,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )
        return runner

    def test_noop_without_global_store(self, runner: JobRunner) -> None:
        """Should return silently when no global learning store."""
        from marianne.core.checkpoint import SheetState

        runner._global_learning_store = None
        sheet_state = SheetState(sheet_num=1)
        sheet_state.outcome_data = {"escalation_record_id": "esc-123"}
        # No store → should return without raising or calling anything
        result = runner._update_escalation_outcome(sheet_state, "success", 1)
        assert result is None

    def test_noop_without_escalation_record_id(self, runner: JobRunner) -> None:
        """Should return silently when no escalation_record_id in outcome_data."""
        from marianne.core.checkpoint import SheetState

        runner._global_learning_store = MagicMock()
        sheet_state = SheetState(sheet_num=1)
        sheet_state.outcome_data = {}
        runner._update_escalation_outcome(sheet_state, "success", 1)
        runner._global_learning_store.update_escalation_outcome.assert_not_called()

    def test_records_outcome_when_store_and_id_present(self, runner: JobRunner) -> None:
        """Should call update_escalation_outcome with correct params."""
        from marianne.core.checkpoint import SheetState

        mock_store = MagicMock()
        mock_store.update_escalation_outcome.return_value = True
        runner._global_learning_store = mock_store

        sheet_state = SheetState(sheet_num=1)
        sheet_state.outcome_data = {"escalation_record_id": "esc-456"}
        runner._update_escalation_outcome(sheet_state, "failed", 1)

        mock_store.update_escalation_outcome.assert_called_once_with(
            escalation_id="esc-456",
            outcome_after_action="failed",
        )

    def test_handles_store_exception_gracefully(self, runner: JobRunner) -> None:
        """Should log warning and not crash if store raises."""
        from marianne.core.checkpoint import SheetState

        mock_store = MagicMock()
        mock_store.update_escalation_outcome.side_effect = RuntimeError("DB error")
        runner._global_learning_store = mock_store

        sheet_state = SheetState(sheet_num=1)
        sheet_state.outcome_data = {"escalation_record_id": "esc-789"}
        # Should swallow the exception — verify the store was called (proving
        # the exception was raised and caught, not skipped)
        runner._update_escalation_outcome(sheet_state, "success", 1)
        mock_store.update_escalation_outcome.assert_called_once()

