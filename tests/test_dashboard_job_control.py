"""Tests for JobControlService."""
import signal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.dashboard.services.job_control import (
    JobControlService,
    JobStartResult,
    ProcessHealth,
)
from tests.conftest import MockStateBackend


@pytest.fixture
def job_control_service(mock_state_backend):
    """Fixture for JobControlService."""
    return JobControlService(mock_state_backend)


@pytest.fixture
def sample_yaml_config():
    """Sample YAML config for testing."""
    return """
name: "test-job"
description: "Test job for unit tests"
workspace: "./test-workspace"
sheet:
  size: 10
  total_items: 20
prompt:
  template: "Process item {{item}}"
"""


@pytest.fixture
def sample_config_file(tmp_path, sample_yaml_config):
    """Create a temporary config file."""
    config_file = tmp_path / "test-config.yaml"
    config_file.write_text(sample_yaml_config)
    return config_file


class TestJobControlService:
    """Test cases for JobControlService."""

    @pytest.mark.asyncio
    async def test_start_job_success(
        self,
        job_control_service: JobControlService,
        sample_config_file: Path,
    ):
        """Test successful job start with config file."""
        mock_process = Mock()
        mock_process.pid = 12345

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_subprocess.return_value = mock_process

            result = await job_control_service.start_job(config_path=sample_config_file)

            assert isinstance(result, JobStartResult)
            assert result.job_name == "test-job"
            assert result.status == JobStatus.RUNNING.value
            assert result.total_sheets == 2  # 20 items / 10 per sheet
            assert result.pid == 12345
            assert result.workspace == sample_config_file.parent / "test-workspace"

            # Verify subprocess was called correctly
            mock_subprocess.assert_called_once()
            args, _kwargs = mock_subprocess.call_args
            # args contains all the individual arguments passed to create_subprocess_exec
            # When called with *cmd_args, they become individual arguments
            assert len(args) >= 4
            assert args[1] == "-m"
            assert args[2] == "mozart.cli"
            assert args[3] == "run"
            assert args[4] == str(sample_config_file)

    @pytest.mark.asyncio
    async def test_start_job_with_config_content(
        self,
        job_control_service: JobControlService,
        sample_yaml_config: str,
    ):
        """Test starting job with inline config content."""
        mock_process = Mock()
        mock_process.pid = 12346

        with (
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
            patch("tempfile.mkstemp") as mock_mkstemp,
            patch("builtins.open", create=True) as mock_open,
            patch("os.close"),
            patch("os.fchmod") as mock_fchmod,
        ):
            mock_mkstemp.return_value = (3, "/tmp/test.yaml")
            mock_subprocess.return_value = mock_process

            result = await job_control_service.start_job(
                config_content=sample_yaml_config,
                workspace=Path("./custom-workspace"),
                start_sheet=2,
                self_healing=True,
            )

            assert isinstance(result, JobStartResult)
            assert result.job_name == "test-job"
            assert result.status == JobStatus.RUNNING.value
            assert result.pid == 12346
            assert result.workspace == Path("./custom-workspace")

            # Verify temp file was created with restrictive permissions
            mock_mkstemp.assert_called_once_with(suffix='.yaml', text=True)
            mock_fchmod.assert_called_once_with(3, 0o600)
            mock_open.assert_called_once_with(3, 'w')

            # Verify subprocess was called with correct arguments
            mock_subprocess.assert_called_once()
            args, _kwargs = mock_subprocess.call_args
            # args: individual arguments passed to create_subprocess_exec
            args_list = list(args)
            assert "--workspace" in args_list
            # The workspace Path might be converted to different string format
            workspace_idx = args_list.index("--workspace")
            assert "custom-workspace" in args_list[workspace_idx + 1]
            assert "--start-sheet" in args_list
            assert "2" in args_list
            assert "--self-healing" in args_list

    @pytest.mark.asyncio
    async def test_start_job_no_config_raises_error(
        self,
        job_control_service: JobControlService,
    ):
        """Test that starting job without config raises ValueError."""
        with pytest.raises(ValueError, match="Must provide either config_path or config_content"):
            await job_control_service.start_job()

    @pytest.mark.asyncio
    async def test_start_job_nonexistent_file_raises_error(
        self,
        job_control_service: JobControlService,
    ):
        """Test that starting job with nonexistent file raises FileNotFoundError."""
        nonexistent_file = Path("/nonexistent/config.yaml")

        with pytest.raises(FileNotFoundError, match="Config file not found"):
            await job_control_service.start_job(config_path=nonexistent_file)

    @pytest.mark.asyncio
    async def test_pause_running_job(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test pausing a running job using file-based pause signals."""
        # Create a mock running job state
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        result = await job_control_service.pause_job(job_id)

        # File-based pause: request is sent, but job remains RUNNING until runner processes it
        assert result.success is True
        assert result.job_id == job_id
        # Still running until runner picks up signal
        assert result.status == JobStatus.RUNNING.value
        assert "Pause request sent" in result.message

        # Verify pause signal file was created
        workspace_path = job_control_service._workspace_root
        pause_signal_file = workspace_path / f".mozart-pause-{job_id}"
        assert pause_signal_file.exists()

        # Cleanup pause file
        pause_signal_file.unlink()

    @pytest.mark.asyncio
    async def test_pause_job_not_found(
        self,
        job_control_service: JobControlService,
    ):
        """Test pausing a job that doesn't exist."""
        result = await job_control_service.pause_job("nonexistent-job")

        assert result.success is False
        assert result.status == JobStatus.FAILED.value
        assert "Job not found" in result.message

    @pytest.mark.asyncio
    async def test_pause_already_paused_job(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test pausing a job that's already paused."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.PAUSED,
            pid=12345,
        )
        await mock_state_backend.save(state)

        result = await job_control_service.pause_job(job_id)

        assert result.success is False
        assert result.status == JobStatus.PAUSED.value
        assert "Job is not running" in result.message

    @pytest.mark.asyncio
    async def test_pause_job_process_not_found(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test pausing a job whose process is dead - still creates pause signal file.

        With file-based pause, we still create the signal file. The job will be
        paused at next check even if process is dead (defensive design).
        """
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        # File-based pause doesn't check process health - it just creates the file
        result = await job_control_service.pause_job(job_id)

        # Pause request is still sent (file created)
        assert result.success is True
        # Still running until runner processes signal
        assert result.status == JobStatus.RUNNING.value
        assert "Pause request sent" in result.message

        # Verify pause signal file was created
        workspace_path = job_control_service._workspace_root
        pause_signal_file = workspace_path / f".mozart-pause-{job_id}"
        assert pause_signal_file.exists()

        # Cleanup pause file
        pause_signal_file.unlink()

    @pytest.mark.asyncio
    async def test_resume_paused_job(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test resuming a paused job using file-based signals.

        With file-based pause, resume cleans up the signal file and updates state.
        No SIGCONT is sent since pause was file-based, not SIGSTOP-based.
        """
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.PAUSED,
            pid=12345,
        )
        await mock_state_backend.save(state)

        # Create pause signal file that would exist from a pause operation
        workspace_path = job_control_service._workspace_root
        pause_signal_file = workspace_path / f".mozart-pause-{job_id}"
        pause_signal_file.touch()

        with patch.object(job_control_service, "get_job_pid", return_value=12345):
            result = await job_control_service.resume_job(job_id)

            assert result.success is True
            assert result.job_id == job_id
            assert result.status == JobStatus.RUNNING.value
            assert "resumed successfully" in result.message

            # Verify pause signal file was cleaned up
            assert not pause_signal_file.exists()

            # Verify state was updated
            updated_state = await mock_state_backend.load(job_id)
            assert updated_state is not None
            assert updated_state.status == JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_resume_job_not_paused(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test resuming a job that's not paused."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        result = await job_control_service.resume_job(job_id)

        assert result.success is False
        assert result.status == JobStatus.RUNNING.value
        assert "Job is not paused" in result.message

    @pytest.mark.asyncio
    async def test_resume_job_restart_dead_process(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test resuming a job when process is dead triggers restart."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.PAUSED,
        )
        await mock_state_backend.save(state)

        mock_process = Mock()
        mock_process.pid = 54321

        with (
            patch.object(job_control_service, "get_job_pid", return_value=None),
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            mock_subprocess.return_value = mock_process

            result = await job_control_service.resume_job(job_id)

            assert result.success is True
            assert result.status == JobStatus.RUNNING.value
            assert "restarted" in result.message

            # Verify restart command was called
            mock_subprocess.assert_called_once()
            args, _kwargs = mock_subprocess.call_args
            # args: individual arguments passed to create_subprocess_exec
            args_list = list(args)
            assert "resume" in args_list
            assert job_id in args_list

    @pytest.mark.asyncio
    async def test_cancel_running_job(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test cancelling a running job."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        with (
            patch("os.kill") as mock_kill,
            patch.object(job_control_service, "get_job_pid", return_value=12345),
            patch("asyncio.sleep"),
        ):
            # SIGTERM succeeds, then ProcessLookupError (process exited)
            mock_kill.side_effect = [
                None, ProcessLookupError("No such process"),
            ]

            result = await job_control_service.cancel_job(job_id)

            assert result.success is True
            assert result.job_id == job_id
            assert result.status == JobStatus.CANCELLED.value
            assert "cancelled successfully" in result.message

            # Verify SIGTERM was sent
            assert mock_kill.call_count == 2
            mock_kill.assert_any_call(12345, signal.SIGTERM)
            mock_kill.assert_any_call(12345, 0)  # Check if still alive

            # Verify state was updated
            updated_state = await mock_state_backend.load(job_id)
            assert updated_state is not None
            assert updated_state.status == JobStatus.CANCELLED
            assert updated_state.pid is None

    @pytest.mark.asyncio
    async def test_cancel_completed_job(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test cancelling a job that's already completed."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        await mock_state_backend.save(state)

        result = await job_control_service.cancel_job(job_id)

        assert result.success is False
        assert result.status == JobStatus.COMPLETED.value
        assert "Job already finished" in result.message

    @pytest.mark.asyncio
    async def test_delete_completed_job(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test deleting a completed job."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        await mock_state_backend.save(state)

        result = await job_control_service.delete_job(job_id)

        assert result is True

        # Verify job was deleted
        deleted_state = await mock_state_backend.load(job_id)
        assert deleted_state is None

    @pytest.mark.asyncio
    async def test_delete_running_job_fails(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test that deleting a running job fails."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        with (
            patch.object(job_control_service, "get_job_pid", return_value=12345),
            patch("os.kill"),  # Process exists
        ):
            result = await job_control_service.delete_job(job_id)

            assert result is False

            # Verify job was not deleted
            existing_state = await mock_state_backend.load(job_id)
            assert existing_state is not None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_job(
        self,
        job_control_service: JobControlService,
    ):
        """Test deleting a job that doesn't exist."""
        result = await job_control_service.delete_job("nonexistent-job")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_job_pid_from_state(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test getting PID from state backend."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            pid=12345,
        )
        await mock_state_backend.save(state)

        with patch("os.kill"):  # Process exists
            pid = await job_control_service.get_job_pid(job_id)
            assert pid == 12345

    @pytest.mark.asyncio
    async def test_get_job_pid_dead_process(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test getting PID when process is dead."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            pid=12345,
        )
        await mock_state_backend.save(state)

        with patch("os.kill", side_effect=ProcessLookupError("No such process")):
            pid = await job_control_service.get_job_pid(job_id)
            assert pid is None

    @pytest.mark.asyncio
    async def test_get_job_pid_from_tracked_processes(
        self,
        job_control_service: JobControlService,
    ):
        """Test getting PID from tracked processes."""
        job_id = "test-job-123"

        # Mock an active process
        mock_process = Mock()
        mock_process.pid = 54321
        mock_process.returncode = None  # Still running

        job_control_service._running_processes[job_id] = mock_process

        pid = await job_control_service.get_job_pid(job_id)
        assert pid == 54321


class TestProcessManagement:
    """Test cases for enhanced process management features."""

    @pytest.mark.asyncio
    async def test_verify_process_health_alive_process(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test process health verification for alive process."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        # Mock process start time
        job_control_service._process_start_times[job_id] = 1000.0

        with (
            patch.object(job_control_service, "get_job_pid", return_value=12345),
            patch("os.kill"),  # Process exists
            patch("time.time", return_value=1100.0),  # 100 seconds later
        ):
            health = await job_control_service.verify_process_health(job_id)

            assert isinstance(health, ProcessHealth)
            assert health.pid == 12345
            assert health.is_alive is True
            assert health.is_zombie_state is False
            assert health.process_exists is True
            assert health.uptime_seconds == 100.0

    @pytest.mark.asyncio
    async def test_verify_process_health_zombie_state(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test process health verification for zombie state."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        with (
            patch.object(job_control_service, "get_job_pid", return_value=12345),
            # Mock dead process but PID in state - this creates a zombie state
            patch("os.kill", side_effect=ProcessLookupError("No such process")),
        ):
            health = await job_control_service.verify_process_health(job_id)

            assert health.pid == 12345
            assert health.is_alive is False
            # Zombie state: process doesn't exist but state shows running with PID
            assert health.is_zombie_state is True
            assert health.process_exists is False

    @pytest.mark.asyncio
    async def test_verify_process_health_with_metrics(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test process health verification with psutil metrics."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        mock_process_metrics = Mock()
        mock_process_metrics.cpu_percent.return_value = 15.5
        mock_memory_info = Mock()
        mock_memory_info.rss = 104857600  # 100 MB in bytes
        mock_process_metrics.memory_info.return_value = mock_memory_info

        with (
            patch.object(job_control_service, "get_job_pid", return_value=12345),
            patch("os.kill"),  # Process exists
            patch("builtins.__import__") as mock_import,
        ):
            mock_psutil = Mock()
            mock_psutil.Process.return_value = mock_process_metrics
            mock_import.return_value = mock_psutil

            health = await job_control_service.verify_process_health(job_id)

            assert health.pid == 12345
            assert health.is_alive is True
            assert health.cpu_percent == 15.5
            assert health.memory_mb == 100.0  # 100 MB

    @pytest.mark.asyncio
    async def test_verify_process_health_no_psutil(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test process health verification when psutil is not available."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        with (
            patch.object(job_control_service, "get_job_pid", return_value=12345),
            patch("os.kill"),  # Process exists
        ):
            # Without patching __import__, psutil metrics won't be available
            health = await job_control_service.verify_process_health(job_id)

            assert health.pid == 12345
            assert health.is_alive is True
            assert health.cpu_percent is None
            assert health.memory_mb is None

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_processes(
        self,
        job_control_service: JobControlService,
    ):
        """Test cleanup of orphaned process references."""
        # Add some mock processes
        mock_process_1 = Mock()
        mock_process_1.pid = 100
        mock_process_1.returncode = None  # Still running

        mock_process_2 = Mock()
        mock_process_2.pid = 200
        mock_process_2.returncode = 0  # Exited

        mock_process_3 = Mock()
        mock_process_3.pid = 300
        mock_process_3.returncode = 1  # Exited with error

        job_control_service._running_processes["job1"] = mock_process_1
        job_control_service._running_processes["job2"] = mock_process_2
        job_control_service._running_processes["job3"] = mock_process_3

        job_control_service._process_start_times["job1"] = 1000.0
        job_control_service._process_start_times["job2"] = 2000.0
        job_control_service._process_start_times["job3"] = 3000.0

        orphaned = await job_control_service.cleanup_orphaned_processes()

        # Should clean up job2 and job3 (exited), but keep job1 (running)
        assert set(orphaned) == {"job2", "job3"}
        assert "job1" in job_control_service._running_processes
        assert "job2" not in job_control_service._running_processes
        assert "job3" not in job_control_service._running_processes

        assert "job1" in job_control_service._process_start_times
        assert "job2" not in job_control_service._process_start_times
        assert "job3" not in job_control_service._process_start_times

    @pytest.mark.asyncio
    async def test_detect_and_recover_zombies(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test detection and recovery of zombie jobs."""
        # Create mock jobs - one zombie, one alive
        job_id_zombie = "zombie-job"
        job_id_alive = "alive-job"

        zombie_state = CheckpointState(
            job_id=job_id_zombie,
            job_name="zombie-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )

        alive_state = CheckpointState(
            job_id=job_id_alive,
            job_name="alive-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=54321,
        )

        await mock_state_backend.save(zombie_state)
        await mock_state_backend.save(alive_state)

        # Add to tracked processes
        job_control_service._running_processes[job_id_zombie] = Mock()
        job_control_service._running_processes[job_id_alive] = Mock()
        job_control_service._process_start_times[job_id_zombie] = 1000.0
        job_control_service._process_start_times[job_id_alive] = 2000.0

        # Mock os.kill to simulate zombie (dead) process for first call, alive for second
        def mock_kill_side_effect(pid, sig):
            if pid == 12345:  # zombie job PID
                raise ProcessLookupError("No such process")
            elif pid == 54321:  # alive job PID
                return None  # Process exists
            else:
                return None

        with patch("os.kill", side_effect=mock_kill_side_effect):
            zombie_jobs = await job_control_service.detect_and_recover_zombies()

            assert zombie_jobs == [job_id_zombie]

            # Verify zombie job was cleaned up from tracking
            assert job_id_zombie not in job_control_service._running_processes
            assert job_id_zombie not in job_control_service._process_start_times

            # Verify alive job remains tracked
            assert job_id_alive in job_control_service._running_processes
            assert job_id_alive in job_control_service._process_start_times

            # Verify zombie state was marked as recovered
            recovered_state = await mock_state_backend.load(job_id_zombie)
            assert recovered_state is not None
            assert recovered_state.status == JobStatus.PAUSED
            assert recovered_state.pid is None

    @pytest.mark.asyncio
    async def test_detect_and_recover_zombies_error_handling(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test zombie detection with error handling."""
        job_id = "error-job"

        # Add to tracked processes
        job_control_service._running_processes[job_id] = Mock()

        # Mock state backend load failure
        with patch.object(mock_state_backend, "load", side_effect=Exception("State load error")):
            zombie_jobs = await job_control_service.detect_and_recover_zombies()

            # Should return empty list and handle error gracefully
            assert zombie_jobs == []
            # Process should still be tracked (not cleaned up due to error)
            assert job_id in job_control_service._running_processes

    @pytest.mark.asyncio
    async def test_process_start_time_tracking(
        self,
        job_control_service: JobControlService,
        sample_config_file: Path,
    ):
        """Test that process start times are properly tracked."""
        mock_process = Mock()
        mock_process.pid = 12345

        start_time = 1000.0

        with (
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
            patch("time.time", return_value=start_time),
        ):
            mock_subprocess.return_value = mock_process

            result = await job_control_service.start_job(config_path=sample_config_file)

            # Verify start time was recorded
            assert result.job_id in job_control_service._process_start_times
            assert job_control_service._process_start_times[result.job_id] == start_time

    @pytest.mark.asyncio
    async def test_process_start_time_tracking_on_restart(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test that process start times are tracked on job restart."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.PAUSED,
        )
        await mock_state_backend.save(state)

        mock_process = Mock()
        mock_process.pid = 54321
        restart_time = 2000.0

        with (
            patch.object(job_control_service, "get_job_pid", return_value=None),
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
            patch("time.time", return_value=restart_time),
        ):
            mock_subprocess.return_value = mock_process

            await job_control_service.resume_job(job_id)

            # Verify start time was recorded for restarted process
            assert job_id in job_control_service._process_start_times
            assert job_control_service._process_start_times[job_id] == restart_time

    @pytest.mark.asyncio
    async def test_process_cleanup_on_cancel(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test that process tracking is cleaned up on job cancel."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        # Add to tracking
        job_control_service._running_processes[job_id] = Mock()
        job_control_service._process_start_times[job_id] = 1000.0

        with (
            patch("os.kill") as mock_kill,
            patch.object(job_control_service, "get_job_pid", return_value=12345),
            patch("asyncio.sleep"),
        ):
            mock_kill.side_effect = [None, ProcessLookupError("No such process")]

            await job_control_service.cancel_job(job_id)

            # Verify cleanup
            assert job_id not in job_control_service._running_processes
            assert job_id not in job_control_service._process_start_times

    @pytest.mark.asyncio
    async def test_process_cleanup_on_delete(
        self,
        job_control_service: JobControlService,
        mock_state_backend: MockStateBackend,
    ):
        """Test that process tracking is cleaned up on job delete."""
        job_id = "test-job-123"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        await mock_state_backend.save(state)

        # Add to tracking
        job_control_service._running_processes[job_id] = Mock()
        job_control_service._process_start_times[job_id] = 1000.0

        await job_control_service.delete_job(job_id)

        # Verify cleanup
        assert job_id not in job_control_service._running_processes
        assert job_id not in job_control_service._process_start_times


if __name__ == "__main__":
    pytest.main([__file__])
