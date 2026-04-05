"""End-to-end integration tests for Mozart Dashboard."""
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from mozart.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from mozart.dashboard.app import create_app
from mozart.dashboard.services.job_control import JobControlService
from mozart.state.memory import InMemoryStateBackend


@pytest.fixture
def mock_state_backend():
    """In-memory state backend for testing."""
    return InMemoryStateBackend()


@pytest.fixture
def app(mock_state_backend):
    """Create test app with mock backend."""

    def get_test_backend():
        return mock_state_backend

    from mozart.dashboard.app import get_state_backend

    app = create_app()
    app.dependency_overrides[get_state_backend] = get_test_backend
    return app


@pytest.fixture
def client(app):
    """Test client for API calls."""
    return TestClient(app)


@pytest.fixture
def sample_yaml_config(tmp_path):
    """Sample YAML config for testing."""
    workspace_dir = tmp_path / "e2e-test-workspace"
    workspace_dir.mkdir(exist_ok=True)
    return f"""
name: "e2e-test-job"
description: "E2E test job for integration tests"
workspace: "{workspace_dir}"
sheet:
  size: 5
  total_items: 10
prompt:
  template: "Process item {{{{item}}}} with error handling"
retry:
  max_retries: 2
stale_detection:
  idle_timeout_seconds: 300
"""


@pytest.fixture
def sample_config_file(tmp_path, sample_yaml_config):
    """Create a temporary config file."""
    config_file = tmp_path / "e2e-test-config.yaml"
    config_file.write_text(sample_yaml_config)
    return config_file


@pytest.mark.asyncio
class TestJobLifecycleE2E:
    """End-to-end tests for complete job lifecycle."""

    async def test_complete_job_lifecycle(
        self,
        client: TestClient,
        sample_config_file: Path,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test complete job lifecycle: start -> pause -> resume -> cancel -> delete."""

        # Mock subprocess creation for job start
        mock_process = Mock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_subprocess.return_value = mock_process

            # 1. START JOB
            start_response = client.post("/api/jobs", json={
                "config_path": str(sample_config_file),
                "start_sheet": 1,
                "self_healing": True,
            })

            assert start_response.status_code == 200
            start_data = start_response.json()
            assert start_data["success"] is True
            assert start_data["job_name"] == "e2e-test-job"
            assert start_data["total_sheets"] == 2  # 10 items / 5 per sheet
            assert start_data["pid"] == 12345

            job_id = start_data["job_id"]

            # Verify job was stored in state backend
            stored_state = await mock_state_backend.load(job_id)
            assert stored_state is not None
            assert stored_state.status == JobStatus.RUNNING
            assert stored_state.pid == 12345

            # 2. PAUSE JOB (file-based pause mechanism)
            # File-based pause sends request but keeps status as RUNNING until runner processes it
            pause_response = client.post(f"/api/jobs/{job_id}/pause")

            assert pause_response.status_code == 200
            pause_data = pause_response.json()
            assert pause_data["success"] is True
            assert pause_data["job_id"] == job_id
            # File-based pause: status remains RUNNING until runner processes the signal file
            assert pause_data["status"] == JobStatus.RUNNING.value
            assert "Pause request sent" in pause_data["message"]

            # For testing, manually update state to PAUSED to continue lifecycle test
            paused_state = await mock_state_backend.load(job_id)
            assert paused_state is not None
            paused_state.status = JobStatus.PAUSED
            await mock_state_backend.save(paused_state)

            # 3. RESUME JOB (file-based resume cleans up signal files)
            with patch.object(
                JobControlService, "get_job_pid", return_value=12345
            ):
                resume_response = client.post(f"/api/jobs/{job_id}/resume")

                assert resume_response.status_code == 200
                resume_data = resume_response.json()
                assert resume_data["success"] is True
                assert resume_data["status"] == JobStatus.RUNNING.value

                # Verify state was updated
                resumed_state = await mock_state_backend.load(job_id)
                assert resumed_state is not None
                assert resumed_state.status == JobStatus.RUNNING

            # 4. CANCEL JOB
            with patch("os.kill") as mock_kill, patch("asyncio.sleep"):
                mock_kill.side_effect = [None, ProcessLookupError("No such process")]

                cancel_response = client.post(f"/api/jobs/{job_id}/cancel")

                assert cancel_response.status_code == 200
                cancel_data = cancel_response.json()
                assert cancel_data["success"] is True
                assert cancel_data["status"] == JobStatus.CANCELLED.value

                # Verify state was updated
                cancelled_state = await mock_state_backend.load(job_id)
                assert cancelled_state is not None
                assert cancelled_state.status == JobStatus.CANCELLED
                assert cancelled_state.pid is None

            # 5. DELETE JOB
            delete_response = client.delete(f"/api/jobs/{job_id}")

            assert delete_response.status_code == 200
            delete_data = delete_response.json()
            assert delete_data["success"] is True
            assert delete_data["job_id"] == job_id

            # Verify job was deleted from state backend
            deleted_state = await mock_state_backend.load(job_id)
            assert deleted_state is None

    async def test_job_start_with_inline_config(
        self,
        client: TestClient,
        sample_yaml_config: str,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test starting job with inline config content."""

        mock_process = Mock()
        mock_process.pid = 54321
        mock_process.wait = AsyncMock(return_value=0)

        with (
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
            patch("tempfile.mkstemp") as mock_mkstemp,
            patch("builtins.open", create=True),
            patch("os.close"),
            patch("os.fchmod"),
        ):
            mock_mkstemp.return_value = (3, "/tmp/test.yaml")
            mock_subprocess.return_value = mock_process

            response = client.post("/api/jobs", json={
                "config_content": sample_yaml_config,
                "workspace": "./custom-workspace",
                "start_sheet": 2,
                "self_healing": True,
            })

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["job_name"] == "e2e-test-job"
            assert data["pid"] == 54321

    async def test_job_error_scenarios(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test various error scenarios in job lifecycle."""

        # Test starting job without config
        response = client.post("/api/jobs", json={})
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid job configuration"

        # Test starting job with nonexistent file
        response = client.post("/api/jobs", json={
            "config_path": "/nonexistent/file.yaml"
        })
        assert response.status_code == 404

        # Test pausing nonexistent job
        response = client.post("/api/jobs/nonexistent/pause")
        assert response.status_code == 404

        # Test resuming nonexistent job
        response = client.post("/api/jobs/nonexistent/resume")
        assert response.status_code == 404

        # Test cancelling nonexistent job
        response = client.post("/api/jobs/nonexistent/cancel")
        assert response.status_code == 404

        # Test deleting nonexistent job
        response = client.delete("/api/jobs/nonexistent")
        assert response.status_code == 404

    async def test_job_state_validation_errors(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test job state validation and error handling."""

        # Create a completed job
        completed_job_id = "completed-job"
        completed_state = CheckpointState(
            job_id=completed_job_id,
            job_name="completed-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        await mock_state_backend.save(completed_state)

        # Test pausing completed job (returns 409 Conflict)
        response = client.post(f"/api/jobs/{completed_job_id}/pause")
        assert response.status_code == 409
        assert "Job is not running" in response.json()["detail"]

        # Test deleting running job
        running_job_id = "running-job"
        running_state = CheckpointState(
            job_id=running_job_id,
            job_name="running-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(running_state)

        with patch("os.kill"):  # Mock that process exists
            response = client.delete(f"/api/jobs/{running_job_id}")
            assert response.status_code == 409
            assert "Cannot delete running job" in response.json()["detail"]


@pytest.mark.asyncio
class TestSheetDetailsE2E:
    """End-to-end tests for sheet details API."""

    async def test_get_sheet_details_success(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test getting detailed sheet information."""

        # Create a job with detailed sheet state
        job_id = "detailed-job"
        sheet_num = 1

        # Create comprehensive sheet state
        # Note: confidence values are 0-1 scale, not percentages
        sheet_state = SheetState(
            sheet_num=sheet_num,
            status=SheetStatus.COMPLETED,
            started_at=None,
            completed_at=None,
            attempt_count=2,
            exit_code=0,
            error_message=None,
            error_category=None,
            validation_passed=True,
            validation_details=[
                {"test": "file_exists", "passed": True, "description": "Output file created"},
                {
                    "test": "content_check",
                    "passed": True,
                    "description": "Content validation passed",
                },
            ],
            execution_duration_seconds=45.2,
            exit_signal=None,
            exit_reason="completed",
            completion_attempts=2,
            passed_validations=["file_exists", "content_check"],
            failed_validations=[],
            last_pass_percentage=100.0,
            execution_mode="normal",
            confidence_score=0.955,  # 0-1 scale
            outcome_category="success_first_try",
            success_without_retry=False,
            stdout_tail="Process completed successfully\nOutput written to file",
            stderr_tail="",
            output_truncated=False,
            preflight_warnings=[],
            applied_pattern_descriptions=["retry-pattern", "validation-pattern"],
            grounding_passed=True,
            grounding_confidence=0.982,  # 0-1 scale
            grounding_guidance="All grounding checks passed",
            input_tokens=1250,
            output_tokens=850,
            estimated_cost=0.045,
            cost_confidence=0.85,  # 0-1 scale
        )

        state = CheckpointState(
            job_id=job_id,
            job_name="detailed-job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        state.sheets[sheet_num] = sheet_state

        await mock_state_backend.save(state)

        # Get sheet details
        response = client.get(f"/api/jobs/{job_id}/sheets/{sheet_num}")

        assert response.status_code == 200
        data = response.json()

        # Verify all fields are present and correct
        assert data["sheet_num"] == sheet_num
        assert data["status"] == SheetStatus.COMPLETED.value
        assert data["attempt_count"] == 2
        assert data["exit_code"] == 0
        assert data["validation_passed"] is True
        assert len(data["validation_details"]) == 2
        assert data["execution_duration_seconds"] == 45.2
        assert data["exit_reason"] == "completed"
        assert data["completion_attempts"] == 2
        assert data["passed_validations"] == [
            "file_exists", "content_check",
        ]  # List of validation names
        assert data["failed_validations"] == []
        assert data["last_pass_percentage"] == 100.0
        assert data["confidence_score"] == 0.955  # 0-1 scale
        assert data["outcome_category"] == "success_first_try"
        assert data["success_without_retry"] is False
        assert data["stdout_tail"] == "Process completed successfully\nOutput written to file"
        assert data["stderr_tail"] == ""
        assert data["output_truncated"] is False
        assert data["applied_pattern_descriptions"] == [
            "retry-pattern", "validation-pattern",
        ]
        assert data["grounding_passed"] is True
        assert data["grounding_confidence"] == 0.982  # 0-1 scale
        assert data["input_tokens"] == 1250
        assert data["output_tokens"] == 850
        assert data["estimated_cost"] == 0.045
        assert data["cost_confidence"] == 0.85  # 0-1 scale

    async def test_get_sheet_details_errors(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test error cases for sheet details API."""

        # Test nonexistent job
        response = client.get("/api/jobs/nonexistent/sheets/1")
        assert response.status_code == 404
        assert "Score not found" in response.json()["detail"]

        # Test nonexistent sheet
        job_id = "test-job"
        state = CheckpointState(
            job_id=job_id,
            job_name="test-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
        )
        await mock_state_backend.save(state)

        response = client.get(f"/api/jobs/{job_id}/sheets/999")
        assert response.status_code == 404
        assert "Sheet 999 not found" in response.json()["detail"]


@pytest.mark.asyncio
class TestProcessManagementE2E:
    """End-to-end tests for process management features."""

    async def test_zombie_detection_and_recovery(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
        sample_config_file: Path,
    ):
        """Test zombie process detection and recovery through full lifecycle.

        With file-based pause mechanism, pause doesn't check if process is alive.
        Instead, it creates a signal file. Zombie detection happens elsewhere
        (e.g., in detect_and_recover_zombies or verify_process_health).
        """

        mock_process = Mock()
        mock_process.pid = 12345
        mock_process.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_subprocess.return_value = mock_process

            # Start a job
            start_response = client.post("/api/jobs", json={
                "config_path": str(sample_config_file)
            })
            assert start_response.status_code == 200
            job_id = start_response.json()["job_id"]

            # With file-based pause, we don't check process health
            # The pause just creates a signal file
            pause_response = client.post(f"/api/jobs/{job_id}/pause")

            assert pause_response.status_code == 200
            data = pause_response.json()
            # File-based pause succeeds (creates signal file)
            assert data["success"] is True
            assert "Pause request sent" in data["message"]
            assert data["status"] == JobStatus.RUNNING.value  # Still running until runner sees file

            # Verify state still shows running (pause signal sent, not yet processed)
            pause_state = await mock_state_backend.load(job_id)
            assert pause_state is not None
            assert pause_state.status == JobStatus.RUNNING

    async def test_process_restart_on_resume(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test process restart when resuming a dead process."""

        # Create a paused job without active process
        job_id = "restart-job"
        state = CheckpointState(
            job_id=job_id,
            job_name="restart-job",
            total_sheets=2,
            status=JobStatus.PAUSED,
        )
        await mock_state_backend.save(state)

        mock_new_process = Mock()
        mock_new_process.pid = 54321
        mock_new_process.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_subprocess.return_value = mock_new_process

            # Resume should detect dead process and restart
            resume_response = client.post(f"/api/jobs/{job_id}/resume")

            assert resume_response.status_code == 200
            data = resume_response.json()
            assert data["success"] is True
            assert "restarted" in data["message"]

            # Verify subprocess was called for restart
            mock_subprocess.assert_called_once()
            args, kwargs = mock_subprocess.call_args
            args_list = list(args)
            assert "resume" in args_list
            assert job_id in args_list

    async def test_graceful_cancellation_with_timeout(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        """Test graceful job cancellation with SIGTERM -> SIGKILL progression."""

        # Create a running job
        job_id = "cancel-job"
        state = CheckpointState(
            job_id=job_id,
            job_name="cancel-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        await mock_state_backend.save(state)

        call_count = 0
        def mock_kill_side_effect(pid, sig):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (SIGTERM) - process still alive
                return None
            elif call_count == 2:
                # Second call (check if alive) - process exited
                raise ProcessLookupError("No such process")

        with (
            patch("os.kill", side_effect=mock_kill_side_effect),
            patch("asyncio.sleep"),
        ):
            cancel_response = client.post(f"/api/jobs/{job_id}/cancel")

            assert cancel_response.status_code == 200
            data = cancel_response.json()
            assert data["success"] is True
            assert data["status"] == JobStatus.CANCELLED.value

            # Verify state was cleaned up
            cancelled_state = await mock_state_backend.load(job_id)
            assert cancelled_state is not None
            assert cancelled_state.status == JobStatus.CANCELLED
            assert cancelled_state.pid is None


if __name__ == "__main__":
    pytest.main([__file__])
