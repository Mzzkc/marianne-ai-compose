"""Test comprehensive error handling for dashboard APIs."""
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.dashboard.app import create_app
from tests.conftest import MockStateBackend


@pytest.fixture
def app(mock_state_backend):
    """Create test app with mock backend."""

    def get_test_backend():
        return mock_state_backend

    from marianne.dashboard.app import get_state_backend

    app = create_app()
    app.dependency_overrides[get_state_backend] = get_test_backend
    return app


@pytest.fixture
def client(app):
    """Test client for API calls."""
    return TestClient(app)


@pytest.fixture
def sample_yaml_config():
    """Sample YAML config for testing."""
    return """
name: "error-test-job"
description: "Job for error handling tests"
workspace: "./error-test-workspace"
sheet:
  size: 3
  total_items: 6
prompt:
  template: "Test error handling for {{item}}"
"""


@pytest.fixture
def sample_config_file(tmp_path, sample_yaml_config):
    """Create a temporary config file."""
    config_file = tmp_path / "error-test-config.yaml"
    config_file.write_text(sample_yaml_config)
    return config_file


class TestHTTPErrorCodes:
    """Test proper HTTP status codes for various error scenarios."""

    def test_400_bad_request_errors(self, client: TestClient):
        """Test 400 Bad Request errors for invalid input."""

        # Missing required config
        response = client.post("/api/jobs", json={})
        assert response.status_code == 400
        error = response.json()
        assert "detail" in error
        assert error["detail"] == "Invalid job configuration"

        # Both config sources provided
        response = client.post("/api/jobs", json={
            "config_content": "test: config",
            "config_path": "/some/path.yaml"
        })
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid job configuration"

        # Invalid start_sheet (negative)
        response = client.post("/api/jobs", json={
            "config_content": "name: test",
            "start_sheet": -1
        })
        assert response.status_code == 422  # Pydantic validation error
        error = response.json()
        assert "detail" in error

        # Note: Invalid JSON testing is handled at the HTTP client level

    def test_404_not_found_errors(self, client: TestClient):
        """Test 404 Not Found errors for missing resources."""

        # Nonexistent config file
        response = client.post("/api/jobs", json={
            "config_path": "/nonexistent/file.yaml"
        })
        assert response.status_code == 404
        error = response.json()
        assert "detail" in error
        assert "not found" in error["detail"].lower()

        # Nonexistent job operations
        nonexistent_job_id = "nonexistent-job-12345"

        response = client.post(f"/api/jobs/{nonexistent_job_id}/pause")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

        response = client.post(f"/api/jobs/{nonexistent_job_id}/resume")
        assert response.status_code == 404

        response = client.post(f"/api/jobs/{nonexistent_job_id}/cancel")
        assert response.status_code == 404

        response = client.delete(f"/api/jobs/{nonexistent_job_id}")
        assert response.status_code == 404

        # Nonexistent sheet
        response = client.get(f"/api/jobs/{nonexistent_job_id}/sheets/1")
        assert response.status_code == 404

    def test_409_conflict_errors(self, client: TestClient, mock_state_backend: MockStateBackend):
        """Test 409 Conflict errors for state conflicts."""

        # Create a running job
        job_id = "running-job"
        state = CheckpointState(
            job_id=job_id,
            job_name="running-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        mock_state_backend.states[job_id] = state

        # Try to delete running job - should get 409 Conflict
        with patch("os.kill"):  # Mock that process exists
            response = client.delete(f"/api/jobs/{job_id}")
            assert response.status_code == 409
            error = response.json()
            assert "detail" in error
            assert "Cannot delete running job" in error["detail"]
            assert "Cancel the job first" in error["detail"]

    def test_500_internal_server_errors(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
        sample_config_file: Path,
    ):
        """Test 500 Internal Server Error for system failures."""

        # Simulate subprocess creation failure
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=RuntimeError("Process creation failed"),
        ):
            response = client.post("/api/jobs", json={
                "config_path": str(sample_config_file)
            })
            assert response.status_code == 500
            error = response.json()
            assert "detail" in error
            assert error["detail"] == "Failed to start job"

        # Simulate state backend failure during operations
        mock_state_backend.should_fail_load = True

        # Any operation that needs to load state should raise an error
        # The TestClient re-raises exceptions by default, so we expect RuntimeError
        with pytest.raises(RuntimeError, match="Database connection failed"):
            client.post("/api/jobs/test/pause")


class TestErrorResponseFormat:
    """Test that error responses follow consistent format."""

    def test_error_response_structure(self, client: TestClient):
        """Test that all error responses have consistent structure."""

        # 400 error
        response = client.post("/api/jobs", json={})
        assert response.status_code == 400
        error = response.json()
        assert "detail" in error
        assert isinstance(error["detail"], str)

        # 404 error
        response = client.get("/api/jobs/nonexistent/sheets/1")
        assert response.status_code == 404
        error = response.json()
        assert "detail" in error
        assert isinstance(error["detail"], str)

    def test_validation_error_format(self, client: TestClient):
        """Test Pydantic validation error format."""

        # Invalid field type
        response = client.post("/api/jobs", json={
            "config_content": "test",
            "start_sheet": "invalid"  # Should be int
        })
        assert response.status_code == 422
        error = response.json()
        assert "detail" in error
        # Pydantic returns list of validation errors
        assert isinstance(error["detail"], list)


class TestJobStateValidation:
    """Test validation of job state transitions."""

    def test_pause_job_state_validation(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
    ):
        """Test pause operation state validation."""

        job_id = "state-test-job"

        # Test pausing completed job - should be handled gracefully
        completed_state = CheckpointState(
            job_id=job_id,
            job_name="completed-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        mock_state_backend.states[job_id] = completed_state

        response = client.post(f"/api/jobs/{job_id}/pause")
        assert response.status_code == 409  # State conflict
        assert "Job is not running" in response.json()["detail"]

        # Test pausing already paused job
        paused_state = CheckpointState(
            job_id=job_id,
            job_name="paused-job",
            total_sheets=2,
            status=JobStatus.PAUSED,
            pid=12345,
        )
        mock_state_backend.states[job_id] = paused_state

        response = client.post(f"/api/jobs/{job_id}/pause")
        assert response.status_code == 409  # State conflict
        assert "Job is not running" in response.json()["detail"]

    def test_resume_job_state_validation(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
    ):
        """Test resume operation state validation."""

        job_id = "resume-test-job"

        # Test resuming running job
        running_state = CheckpointState(
            job_id=job_id,
            job_name="running-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        mock_state_backend.states[job_id] = running_state

        response = client.post(f"/api/jobs/{job_id}/resume")
        assert response.status_code == 409  # State conflict
        assert "Job is not paused" in response.json()["detail"]

        # Test resuming completed job
        completed_state = CheckpointState(
            job_id=job_id,
            job_name="completed-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        mock_state_backend.states[job_id] = completed_state

        response = client.post(f"/api/jobs/{job_id}/resume")
        assert response.status_code == 409  # State conflict
        assert "Job is not paused" in response.json()["detail"]

    def test_cancel_job_state_validation(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
    ):
        """Test cancel operation state validation."""

        job_id = "cancel-test-job"

        # Test cancelling completed job
        completed_state = CheckpointState(
            job_id=job_id,
            job_name="completed-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        mock_state_backend.states[job_id] = completed_state

        response = client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 409  # State conflict
        assert "Job already finished" in response.json()["detail"]


class TestProcessErrorHandling:
    """Test error handling for process management operations."""

    def test_process_not_found_handling(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
    ):
        """Test handling when signal file creation fails due to missing workspace."""

        job_id = "zombie-job"

        # Create job state with PID
        zombie_state = CheckpointState(
            job_id=job_id,
            job_name="zombie-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=99999,
        )
        mock_state_backend.states[job_id] = zombie_state

        # pause_job now uses file-based signals, not os.kill
        # Simulate file creation failure (e.g., workspace not found)
        with patch("pathlib.Path.touch", side_effect=FileNotFoundError("No such directory")):
            response = client.post(f"/api/jobs/{job_id}/pause")
            # Service returns success=False, route returns 409
            assert response.status_code == 409

    def test_permission_denied_handling(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
    ):
        """Test handling when signal file operations are denied."""

        job_id = "permission-job"

        # Create job state with PID
        state = CheckpointState(
            job_id=job_id,
            job_name="permission-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        mock_state_backend.states[job_id] = state

        # pause_job now uses file-based signals, not os.kill
        # Mock Path.touch to raise PermissionError
        with patch("pathlib.Path.touch", side_effect=PermissionError("Operation not permitted")):
            response = client.post(f"/api/jobs/{job_id}/pause")
            # Service returns success=False, route returns 409
            assert response.status_code == 409
            assert "Failed to create pause signal" in response.json()["detail"]

    def test_subprocess_creation_errors(
        self,
        client: TestClient,
        sample_config_file: Path,
    ):
        """Test handling of subprocess creation failures."""

        # Test various subprocess creation failures
        error_scenarios = [
            FileNotFoundError("python executable not found"),
            PermissionError("Permission denied"),
            OSError("Resource temporarily unavailable"),
        ]

        for error in error_scenarios:
            with patch("asyncio.create_subprocess_exec", side_effect=error):
                response = client.post("/api/jobs", json={
                    "config_path": str(sample_config_file)
                })
                assert response.status_code == 500
                response_data = response.json()
                assert "detail" in response_data


class TestConcurrencyErrorHandling:
    """Test error handling for concurrent operations."""

    def test_concurrent_job_operations(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
    ):
        """Test handling concurrent operations on the same job."""

        job_id = "concurrent-job"

        # Create job state
        state = CheckpointState(
            job_id=job_id,
            job_name="concurrent-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        mock_state_backend.states[job_id] = state

        # Simulate concurrent operations by mocking delayed responses
        with patch("os.kill"):
            # Both operations should complete, but one may override the other
            response1 = client.post(f"/api/jobs/{job_id}/pause")
            response2 = client.post(f"/api/jobs/{job_id}/cancel")

            # Both should succeed or fail gracefully
            assert response1.status_code in [200, 409, 500]
            assert response2.status_code in [200, 409, 500]

    def test_state_consistency_during_errors(
        self,
        client: TestClient,
        mock_state_backend: MockStateBackend,
    ):
        """Test that state remains consistent during error conditions."""

        job_id = "consistency-job"

        original_state = CheckpointState(
            job_id=job_id,
            job_name="consistency-job",
            total_sheets=2,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        mock_state_backend.states[job_id] = original_state

        # Simulate state save failure
        mock_state_backend.should_fail_save = True

        with patch("os.kill"):
            client.post(f"/api/jobs/{job_id}/pause")

            # Operation might fail, but state should not be corrupted
            current_state = mock_state_backend.states[job_id]
            # State should either be unchanged or properly updated
            assert current_state.status in [JobStatus.RUNNING, JobStatus.PAUSED]
            assert current_state.job_id == job_id
            assert current_state.job_name == "consistency-job"


if __name__ == "__main__":
    pytest.main([__file__])
