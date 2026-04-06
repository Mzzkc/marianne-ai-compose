"""Tests for runner pause/resume integration (Sheet 12).

HIGH complexity tests covering:
- JobRunner pause signal detection
- File-based pause/resume communication
- Integration with job control service
- State management during pause/resume cycles
"""
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import BackendConfig, JobConfig, PromptConfig, SheetConfig
from marianne.dashboard.services.job_control import JobControlService
from marianne.execution.runner import GracefulShutdownError, JobRunner
from marianne.state.json_backend import JsonStateBackend


@pytest.fixture
def temp_workspace():
    """Create temporary workspace for test isolation."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def mock_config(temp_workspace):
    """Create mock job config for testing."""
    return JobConfig(
        name="test-pause-job",
        workspace=temp_workspace,
        sheet=SheetConfig(size=10, total_items=50),
        prompt=PromptConfig(template="Test prompt template: {{ item }}"),
        backend=BackendConfig(type="claude_cli", timeout_seconds=30),
    )


@pytest.fixture
def mock_backend():
    """Create mock backend for testing."""
    backend = AsyncMock()
    backend.execute.return_value = MagicMock(
        exit_code=0,
        stdout="Success",
        stderr="",
        duration_seconds=1.0,
    )
    return backend


@pytest.fixture
async def pause_test_state(mock_config, temp_workspace):
    """Create test checkpoint state."""
    state_backend = JsonStateBackend(state_dir=temp_workspace)
    state = CheckpointState(
        job_id="test-job-123",
        job_name="test-pause-job",
        total_sheets=5,
        last_completed_sheet=0,
        status=JobStatus.RUNNING,
    )
    await state_backend.save(state)
    return state, state_backend


@pytest.fixture
def runner_with_mocks(mock_config, mock_backend, pause_test_state):
    """Create runner instance with mocked dependencies."""
    state, state_backend = pause_test_state
    runner = JobRunner(
        config=mock_config,
        backend=mock_backend,
        state_backend=state_backend,
    )
    return runner, state, state_backend


class TestJobRunnerPauseDetection:
    """Test pause signal detection in runner."""

    async def test_check_pause_signal_no_file(self, runner_with_mocks):
        """Test pause signal check when no signal file exists."""
        runner, state, _ = runner_with_mocks

        # Should return False when no pause signal file exists
        assert not runner._check_pause_signal(state)

    async def test_check_pause_signal_file_exists(self, runner_with_mocks, temp_workspace):
        """Test pause signal detection when file exists."""
        runner, state, _ = runner_with_mocks

        # Create pause signal file
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        pause_signal_file.touch()

        # Should return True when pause signal file exists
        assert runner._check_pause_signal(state)

    async def test_clear_pause_signal(self, runner_with_mocks, temp_workspace):
        """Test pause signal cleanup."""
        runner, state, _ = runner_with_mocks

        # Create pause signal file
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        pause_signal_file.touch()
        assert pause_signal_file.exists()

        # Clear the signal
        runner._clear_pause_signal(state)

        # File should be removed
        assert not pause_signal_file.exists()

    async def test_clear_pause_signal_no_file(self, runner_with_mocks):
        """Test pause signal cleanup when no file exists."""
        runner, state, _ = runner_with_mocks

        # Should not raise error when no file exists
        runner._clear_pause_signal(state)
        # Verify the state is still intact (nothing was corrupted)
        assert state.job_id is not None

    async def test_check_pause_signal_no_job_id(self, runner_with_mocks):
        """Test pause signal check with invalid job_id."""
        runner, state, _ = runner_with_mocks
        state.job_id = None

        # Should return False for invalid job_id
        assert not runner._check_pause_signal(state)


class TestJobRunnerPauseHandling:
    """Test pause request handling in runner."""

    async def test_handle_pause_request(self, runner_with_mocks, temp_workspace):
        """Test graceful pause handling."""
        runner, state, state_backend = runner_with_mocks

        # Create pause signal file
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        pause_signal_file.touch()

        # Mock console for output verification
        runner.console = MagicMock()

        # Should raise GracefulShutdownError and update state
        with pytest.raises(GracefulShutdownError) as exc_info:
            await runner._handle_pause_request(state, current_sheet=3)

        # Verify error message
        assert "paused at sheet 3" in str(exc_info.value)

        # Verify state was updated
        updated_state = await state_backend.load(state.job_id)
        assert updated_state.status == JobStatus.PAUSED

        # Verify signal file was cleared
        assert not pause_signal_file.exists()

        # Verify console output
        runner.console.print.assert_called()

    async def test_handle_pause_request_signal_cleanup_failure(self, runner_with_mocks):
        """Test pause handling when signal cleanup fails."""
        runner, state, _ = runner_with_mocks
        runner.console = MagicMock()

        # Mock _clear_pause_signal to simulate failure (non-critical)
        with patch.object(runner, '_clear_pause_signal') as mock_clear:
            mock_clear.side_effect = OSError("Permission denied")

            # Should still handle pause gracefully
            with pytest.raises(GracefulShutdownError):
                await runner._handle_pause_request(state, current_sheet=2)


class TestSequentialExecutionPauseIntegration:
    """Test pause integration in sequential execution mode."""

    @patch('marianne.execution.runner.JobRunner._get_next_sheet_dag_aware')
    @patch('marianne.execution.runner.JobRunner._execute_sheet_with_recovery')
    async def test_sequential_execution_pause_check(
        self,
        mock_execute_sheet,
        mock_next_sheet,
        runner_with_mocks,
        temp_workspace
    ):
        """Test that sequential execution checks for pause signals."""
        runner, state, _ = runner_with_mocks

        # Configure mocks
        mock_next_sheet.side_effect = [1, 2, None]  # Execute 2 sheets then stop
        mock_execute_sheet.return_value = None

        # Create pause signal after first sheet
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"

        def create_pause_signal_after_first_sheet(*args):
            """Create pause signal after first sheet execution."""
            pause_signal_file.touch()

        mock_execute_sheet.side_effect = create_pause_signal_after_first_sheet
        runner.console = MagicMock()

        # Should detect pause signal and raise GracefulShutdownError
        with pytest.raises(GracefulShutdownError):
            await runner._execute_sequential_mode(state)

        # Verify first sheet was executed
        mock_execute_sheet.assert_called_once_with(state, 1)

        # Verify state is paused
        assert state.status == JobStatus.PAUSED

    @patch('marianne.execution.runner.JobRunner._get_next_sheet_dag_aware')
    async def test_sequential_execution_no_pause_signal(
        self,
        mock_next_sheet,
        runner_with_mocks
    ):
        """Test sequential execution continues when no pause signal."""
        runner, state, _ = runner_with_mocks

        # Configure to execute no sheets (immediate completion)
        mock_next_sheet.return_value = None

        # Should complete normally without pause
        await runner._execute_sequential_mode(state)

        # No pause should have occurred
        assert state.status == JobStatus.RUNNING


class TestParallelExecutionPauseIntegration:
    """Test pause integration in parallel execution mode."""

    async def test_parallel_execution_pause_check(self, runner_with_mocks, temp_workspace):
        """Test that parallel execution checks for pause signals."""
        runner, state, _ = runner_with_mocks

        # Mock parallel executor
        mock_parallel_executor = MagicMock()
        mock_parallel_executor.get_next_parallel_batch.side_effect = [
            [1, 2],  # First batch
            []       # No more batches
        ]
        runner._parallel_executor = mock_parallel_executor
        runner.console = MagicMock()

        # Create pause signal
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        pause_signal_file.touch()

        # Should detect pause signal and raise GracefulShutdownError
        with pytest.raises(GracefulShutdownError):
            await runner._execute_parallel_mode(state)

        # Verify state is paused
        assert state.status == JobStatus.PAUSED


class TestJobControlServicePauseSignaling:
    """Test job control service pause signal creation."""

    @pytest.fixture
    def job_control_service(self, temp_workspace):
        """Create job control service for testing."""
        state_backend = JsonStateBackend(state_dir=temp_workspace)
        return JobControlService(state_backend, temp_workspace)

    async def test_pause_job_creates_signal_file(
        self,
        job_control_service,
        temp_workspace,
        pause_test_state
    ):
        """Test that pause_job creates the correct signal file."""
        state, state_backend = pause_test_state
        job_control_service._state_backend = state_backend

        # Pause the job
        result = await job_control_service.pause_job(state.job_id)

        # Verify result
        assert result.success
        assert "pause request sent" in result.message.lower()
        assert result.status == JobStatus.RUNNING.value  # Still running until signal processed

        # Verify signal file was created
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        assert pause_signal_file.exists()

    async def test_pause_job_nonexistent_job(self, job_control_service):
        """Test pausing non-existent job."""
        result = await job_control_service.pause_job("nonexistent-job")

        # Should fail gracefully
        assert not result.success
        assert "not found" in result.message.lower()

    async def test_pause_job_non_running_job(
        self,
        job_control_service,
        pause_test_state
    ):
        """Test pausing job that's not running."""
        state, state_backend = pause_test_state
        job_control_service._state_backend = state_backend

        # Mark job as completed
        state.status = JobStatus.COMPLETED
        await state_backend.save(state)

        # Attempt to pause
        result = await job_control_service.pause_job(state.job_id)

        # Should fail
        assert not result.success
        assert "not running" in result.message.lower()

    async def test_resume_job_cleans_signal_file(
        self,
        job_control_service,
        temp_workspace,
        pause_test_state
    ):
        """Test that resume_job cleans up pause signal files."""
        state, state_backend = pause_test_state
        job_control_service._state_backend = state_backend

        # Mark job as paused and create signal file
        state.mark_job_paused()
        state.pid = 12345  # Mock PID
        await state_backend.save(state)

        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        pause_signal_file.touch()

        # Mock get_job_pid to return valid PID
        job_control_service.get_job_pid = AsyncMock(return_value=12345)

        # Resume the job
        result = await job_control_service.resume_job(state.job_id)

        # Verify result
        assert result.success
        assert result.status == JobStatus.RUNNING.value

        # Verify signal file was cleaned up
        assert not pause_signal_file.exists()

        # Verify state was updated (patch is_zombie since PID 12345 is not a real process)
        with patch.object(CheckpointState, 'is_zombie', return_value=False):
            updated_state = await state_backend.load(state.job_id)
        assert updated_state.status == JobStatus.RUNNING


class TestPauseResumeIntegrationFlow:
    """Integration tests for complete pause/resume flow."""

    async def test_full_pause_resume_cycle(
        self,
        temp_workspace,
        mock_config,
        mock_backend
    ):
        """Test complete pause/resume cycle integration."""
        # Setup
        state_backend = JsonStateBackend(state_dir=temp_workspace)
        job_control = JobControlService(state_backend, temp_workspace)

        # Patch is_zombie throughout since PID 12345 is not a real process
        with patch.object(CheckpointState, 'is_zombie', return_value=False):
            # Create initial state
            state = CheckpointState(
                job_id="integration-test-123",
                job_name="integration-test",
                total_sheets=3,
                last_completed_sheet=0,
                status=JobStatus.RUNNING,
                pid=12345,
            )
            await state_backend.save(state)

            # Phase 1: Request pause
            pause_result = await job_control.pause_job(state.job_id)
            assert pause_result.success

            # Verify pause signal file exists
            pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
            assert pause_signal_file.exists()

            # Phase 2: Simulate runner detecting pause and handling it
            runner = JobRunner(
                config=mock_config, backend=mock_backend, state_backend=state_backend,
            )
            runner.console = MagicMock()

            # JobRunner should detect pause signal
            assert runner._check_pause_signal(state)

            # Simulate pause handling (would happen in execution loop)
            with pytest.raises(GracefulShutdownError):
                await runner._handle_pause_request(state, current_sheet=2)

            # State should be paused and signal cleaned
            updated_state = await state_backend.load(state.job_id)
            assert updated_state.status == JobStatus.PAUSED
            assert not pause_signal_file.exists()

            # Phase 3: Resume the job
            updated_state.pid = 12345
            await state_backend.save(updated_state)
            job_control.get_job_pid = AsyncMock(return_value=12345)
            resume_result = await job_control.resume_job(state.job_id)
            assert resume_result.success

            # State should be running
            final_state = await state_backend.load(state.job_id)
            assert final_state.status == JobStatus.RUNNING

    async def test_pause_with_dead_process_restart(
        self,
        temp_workspace,
        mock_config
    ):
        """Test pause/resume when process is dead (requires restart)."""
        state_backend = JsonStateBackend(state_dir=temp_workspace)
        job_control = JobControlService(state_backend, temp_workspace)

        # Create paused state with dead process
        state = CheckpointState(
            job_id="restart-test-456",
            job_name="restart-test",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.PAUSED,
            pid=99999,  # Non-existent PID
        )
        await state_backend.save(state)

        # Create leftover pause signal
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        pause_signal_file.touch()

        # Mock process creation for restart
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_process = MagicMock()
            mock_process.pid = 54321
            mock_subprocess.return_value = mock_process

            # Resume should trigger restart
            result = await job_control.resume_job(state.job_id)

            # Should succeed and clean up signal
            assert result.success
            assert not pause_signal_file.exists()

            # Should have attempted to restart
            mock_subprocess.assert_called_once()
