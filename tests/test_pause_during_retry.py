"""Test that pause signals are checked during the sheet retry loop (#93).

Bug: When a sheet is stuck in a validation-failure retry loop,
pause signals are never detected because _check_pause_signal is only
called at sheet boundaries (between sheets), not within the retry loop
of _execute_sheet_with_recovery.

Fix: Add _check_pause_signal + _handle_pause_request at the top of the
while True retry loop in _execute_sheet_with_recovery, so that pause
signals are consumed between retry attempts — not just between sheets.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import JobConfig
from marianne.execution.runner import JobRunner
from marianne.execution.runner.models import GracefulShutdownError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Provide a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def config(workspace: Path) -> JobConfig:
    """Create a minimal JobConfig for pause testing."""
    return JobConfig.model_validate({
        "name": "pause-retry-test",
        "description": "Test pause during retry loop",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": 10},
        "prompt": {"template": "Process sheet {{ sheet_num }}."},
        "workspace": str(workspace),
        "retry": {"max_retries": 5},
    })


@pytest.fixture
def state() -> CheckpointState:
    """Create a CheckpointState for testing."""
    return CheckpointState(
        job_id="pause-retry-test",
        job_name="pause-retry-test",
        total_sheets=1,
        last_completed_sheet=0,
        status=JobStatus.RUNNING,
    )


@pytest.fixture
def runner(config: JobConfig) -> JobRunner:
    """Create a JobRunner with mocked backend and state backend."""
    mock_backend = AsyncMock()
    mock_state_backend = AsyncMock()
    return JobRunner(
        config=config,
        backend=mock_backend,
        state_backend=mock_state_backend,
    )


class TestPauseDuringRetryLoop:
    """Tests verifying pause signals are checked during retry loops."""

    @pytest.mark.asyncio
    async def test_pause_signal_detected_during_retry(
        self, runner: JobRunner, state: CheckpointState, workspace: Path
    ) -> None:
        """Pause signal created during retry loop should be detected.

        Simulates a sheet that fails validation on first attempt, then
        a pause signal is created. The retry loop should detect the pause
        before the second execution attempt.
        """
        call_count = 0

        def check_pause_side_effect(s: CheckpointState) -> bool:
            nonlocal call_count
            call_count += 1
            # First call: no pause. Second call (during retry): pause present.
            return call_count >= 2

        runner._check_pause_signal = MagicMock(  # type: ignore[method-assign]
            side_effect=check_pause_side_effect,
        )

        # Mock execution to always return failed validation
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "output"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_result.rate_limited = False
        mock_result.cost_usd = None
        mock_result.output_tokens = 100
        mock_result.input_tokens = 50
        mock_result.duration_seconds = 1.0

        runner._configure_and_execute_sheet = AsyncMock(return_value=mock_result)  # type: ignore[method-assign]

        # Mock validation to always fail (triggering retry)
        mock_val_result = MagicMock()
        mock_val_result.all_passed = False
        mock_val_result.passed_count = 0
        mock_val_result.failed_count = 1
        mock_val_result.pass_percentage = 0.0
        mock_val_result.get_failed_results.return_value = []

        mock_val_engine = AsyncMock()
        mock_val_engine.run_validations = AsyncMock(return_value=mock_val_result)
        mock_val_engine.snapshot_mtime_files = MagicMock()

        runner._prepare_sheet_execution = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                MagicMock(
                    original_prompt="test",
                    current_prompt="test",
                    current_mode=MagicMock(value="normal"),
                    max_retries=5,
                    max_completion=3,
                ),
                MagicMock(),
                mock_val_engine,
            )
        )

        # _handle_pause_request raises GracefulShutdownError
        runner._handle_pause_request = AsyncMock(  # type: ignore[method-assign]
            side_effect=GracefulShutdownError("Paused during retry"),
        )

        # Mock other methods needed during execution
        runner._fire_event = AsyncMock()  # type: ignore[method-assign]
        runner._record_execution_bookkeeping = AsyncMock()  # type: ignore[method-assign]
        runner._log_incomplete_validations = MagicMock(return_value=(0, 1, 0.0))  # type: ignore[method-assign]
        runner._check_execution_guards = AsyncMock(return_value=False)  # type: ignore[method-assign]

        # Mode decision should try to retry
        runner._apply_mode_decision = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                action="continue",
                current_prompt="test",
                current_mode=MagicMock(value="normal"),
                normal_attempts=1,
                completion_attempts=0,
                fatal_message="",
            ),
        )

        with pytest.raises(GracefulShutdownError, match="Paused during retry"):
            await runner._execute_sheet_with_recovery(state, 1)

        # Verify _handle_pause_request was called (pause detected during retry)
        runner._handle_pause_request.assert_called_once()
        # Verify _check_pause_signal was called at least twice
        # (once before first execution, once before retry)
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_no_pause_signal_allows_normal_retry(
        self, runner: JobRunner, state: CheckpointState
    ) -> None:
        """When no pause signal exists, retry loop continues normally.

        Verifies the pause check doesn't interfere with normal retry behavior.
        """
        # _check_pause_signal always returns False
        runner._check_pause_signal = MagicMock(return_value=False)  # type: ignore[method-assign]

        # Mock execution: first attempt fails validation, second succeeds
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "output"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_result.rate_limited = False
        mock_result.cost_usd = None
        mock_result.output_tokens = 100
        mock_result.input_tokens = 50
        mock_result.duration_seconds = 1.0

        runner._configure_and_execute_sheet = AsyncMock(return_value=mock_result)  # type: ignore[method-assign]

        # Validation: fail first, pass second
        call_counter = {"val": 0}
        mock_val_fail = MagicMock()
        mock_val_fail.all_passed = False
        mock_val_fail.passed_count = 0
        mock_val_fail.failed_count = 1
        mock_val_fail.pass_percentage = 0.0
        mock_val_fail.get_failed_results.return_value = []

        mock_val_pass = MagicMock()
        mock_val_pass.all_passed = True
        mock_val_pass.passed_count = 1
        mock_val_pass.failed_count = 0
        mock_val_pass.pass_percentage = 100.0

        async def val_side_effect(*args, **kwargs):
            call_counter["val"] += 1
            return mock_val_fail if call_counter["val"] == 1 else mock_val_pass

        mock_val_engine = AsyncMock()
        mock_val_engine.run_validations = AsyncMock(side_effect=val_side_effect)
        mock_val_engine.snapshot_mtime_files = MagicMock()

        runner._prepare_sheet_execution = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                MagicMock(
                    original_prompt="test",
                    current_prompt="test",
                    current_mode=MagicMock(value="normal"),
                    max_retries=5,
                    max_completion=3,
                ),
                MagicMock(),
                mock_val_engine,
            )
        )

        runner._fire_event = AsyncMock()  # type: ignore[method-assign]
        runner._record_execution_bookkeeping = AsyncMock()  # type: ignore[method-assign]
        runner._log_incomplete_validations = MagicMock(return_value=(0, 1, 0.0))  # type: ignore[method-assign]
        runner._check_execution_guards = AsyncMock(return_value=False)  # type: ignore[method-assign]

        # Mode decision: retry once
        runner._apply_mode_decision = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                action="continue",
                current_prompt="test",
                current_mode=MagicMock(value="normal"),
                normal_attempts=1,
                completion_attempts=0,
                fatal_message="",
            ),
        )

        # Success handler should complete the sheet
        runner._handle_validation_success = AsyncMock(return_value=None)  # type: ignore[method-assign]

        # Should complete without error — pause check doesn't interfere
        await runner._execute_sheet_with_recovery(state, 1)

        # Verify _check_pause_signal was called multiple times (once per loop iteration)
        assert runner._check_pause_signal.call_count >= 2

    @pytest.mark.asyncio
    async def test_pause_during_retry_preserves_sheet_state(
        self, runner: JobRunner, state: CheckpointState, workspace: Path
    ) -> None:
        """When pausing during retry, sheet state should indicate in-progress.

        The sheet was being retried, so it should be marked as started
        (in-progress) when the pause happens, ensuring resume knows where
        to pick up.
        """
        # Pause on second call (during retry)
        call_count = 0

        def check_pause(s):
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        runner._check_pause_signal = MagicMock(side_effect=check_pause)  # type: ignore[method-assign]

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "output"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_result.rate_limited = False
        mock_result.cost_usd = None
        mock_result.output_tokens = 100
        mock_result.input_tokens = 50
        mock_result.duration_seconds = 1.0

        runner._configure_and_execute_sheet = AsyncMock(return_value=mock_result)  # type: ignore[method-assign]

        mock_val_result = MagicMock()
        mock_val_result.all_passed = False
        mock_val_result.passed_count = 0
        mock_val_result.failed_count = 1
        mock_val_result.pass_percentage = 0.0
        mock_val_result.get_failed_results.return_value = []

        mock_val_engine = AsyncMock()
        mock_val_engine.run_validations = AsyncMock(return_value=mock_val_result)
        mock_val_engine.snapshot_mtime_files = MagicMock()

        runner._prepare_sheet_execution = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                MagicMock(
                    original_prompt="test",
                    current_prompt="test",
                    current_mode=MagicMock(value="normal"),
                    max_retries=5,
                    max_completion=3,
                ),
                MagicMock(),
                mock_val_engine,
            )
        )

        # Capture the state and sheet_num passed to _handle_pause_request
        pause_args: dict = {}

        async def capture_pause(s, sheet_num):
            pause_args["state"] = s
            pause_args["sheet_num"] = sheet_num
            raise GracefulShutdownError("Paused")

        runner._handle_pause_request = AsyncMock(side_effect=capture_pause)  # type: ignore[method-assign]
        runner._fire_event = AsyncMock()  # type: ignore[method-assign]
        runner._record_execution_bookkeeping = AsyncMock()  # type: ignore[method-assign]
        runner._log_incomplete_validations = MagicMock(return_value=(0, 1, 0.0))  # type: ignore[method-assign]
        runner._check_execution_guards = AsyncMock(return_value=False)  # type: ignore[method-assign]

        runner._apply_mode_decision = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                action="continue",
                current_prompt="test",
                current_mode=MagicMock(value="normal"),
                normal_attempts=1,
                completion_attempts=0,
                fatal_message="",
            ),
        )

        with pytest.raises(GracefulShutdownError):
            await runner._execute_sheet_with_recovery(state, 1)

        # Verify pause was called with correct sheet number
        assert pause_args["sheet_num"] == 1
        assert pause_args["state"] is state

    @pytest.mark.asyncio
    async def test_pause_check_before_guard_check(
        self, runner: JobRunner, state: CheckpointState
    ) -> None:
        """Pause signal should be checked before execution guards.

        If both a pause signal and a guard wait are pending, the pause
        should take priority — the user's explicit intent to pause is
        more important than a circuit breaker/rate limit wait.
        """
        # Pause on first call
        runner._check_pause_signal = MagicMock(return_value=True)  # type: ignore[method-assign]
        runner._handle_pause_request = AsyncMock(  # type: ignore[method-assign]
            side_effect=GracefulShutdownError("Paused before guard"),
        )

        mock_val_engine = AsyncMock()
        mock_val_engine.snapshot_mtime_files = MagicMock()

        runner._prepare_sheet_execution = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                MagicMock(
                    original_prompt="test",
                    current_prompt="test",
                    current_mode=MagicMock(value="normal"),
                    max_retries=5,
                    max_completion=3,
                ),
                MagicMock(),
                mock_val_engine,
            )
        )
        runner._check_execution_guards = AsyncMock(return_value=True)  # type: ignore[method-assign]

        with pytest.raises(GracefulShutdownError, match="Paused before guard"):
            await runner._execute_sheet_with_recovery(state, 1)

        # Pause handler was called, meaning pause check happened first
        runner._handle_pause_request.assert_called_once()
        # Guards should NOT have been checked — pause exits immediately
        runner._check_execution_guards.assert_not_called()

    @pytest.mark.asyncio
    async def test_filesystem_pause_signal_in_retry_loop(
        self, runner: JobRunner, state: CheckpointState, workspace: Path
    ) -> None:
        """End-to-end: pause signal file created mid-retry is detected.

        Uses the real _check_pause_signal (filesystem-based) to verify
        that a signal file created during the retry delay window is
        detected on the next iteration.
        """
        state.job_id = "pause-retry-test"
        iteration = 0

        async def mock_execute(*args, **kwargs):
            nonlocal iteration
            iteration += 1
            # Create pause signal file after first execution
            if iteration == 1:
                pause_file = workspace / ".marianne-pause-pause-retry-test"
                pause_file.touch()
            result = MagicMock()
            result.success = True
            result.stdout = "output"
            result.stderr = ""
            result.exit_code = 0
            result.rate_limited = False
            result.cost_usd = None
            result.output_tokens = 100
            result.input_tokens = 50
            result.duration_seconds = 1.0
            return result

        runner._configure_and_execute_sheet = AsyncMock(side_effect=mock_execute)  # type: ignore[method-assign]

        mock_val_result = MagicMock()
        mock_val_result.all_passed = False
        mock_val_result.passed_count = 0
        mock_val_result.failed_count = 1
        mock_val_result.pass_percentage = 0.0
        mock_val_result.get_failed_results.return_value = []

        mock_val_engine = AsyncMock()
        mock_val_engine.run_validations = AsyncMock(return_value=mock_val_result)
        mock_val_engine.snapshot_mtime_files = MagicMock()

        runner._prepare_sheet_execution = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                MagicMock(
                    original_prompt="test",
                    current_prompt="test",
                    current_mode=MagicMock(value="normal"),
                    max_retries=5,
                    max_completion=3,
                ),
                MagicMock(),
                mock_val_engine,
            )
        )

        runner._fire_event = AsyncMock()  # type: ignore[method-assign]
        runner._record_execution_bookkeeping = AsyncMock()  # type: ignore[method-assign]
        runner._log_incomplete_validations = MagicMock(return_value=(0, 1, 0.0))  # type: ignore[method-assign]
        runner._check_execution_guards = AsyncMock(return_value=False)  # type: ignore[method-assign]

        runner._apply_mode_decision = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                action="continue",
                current_prompt="test",
                current_mode=MagicMock(value="normal"),
                normal_attempts=1,
                completion_attempts=0,
                fatal_message="",
            ),
        )

        with pytest.raises(GracefulShutdownError):
            await runner._execute_sheet_with_recovery(state, 1)

        # Only one execution should have happened — paused before second
        assert iteration == 1
