"""End-to-end integration tests for Marianne Dashboard.

Tests the conductor-only JobControlService proxy and the sheet details endpoint.
The dashboard no longer spawns subprocesses — every operation routes through
the conductor via DaemonClient IPC.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    OutcomeCategory,
    SheetState,
    SheetStatus,
)
from marianne.daemon.types import JobResponse
from marianne.dashboard.app import create_app
from marianne.dashboard.services.job_control import JobControlService
from marianne.state.memory import InMemoryStateBackend


@pytest.fixture
def mock_state_backend():
    return InMemoryStateBackend()


@pytest.fixture
def mock_daemon_client():
    client = MagicMock()
    client.submit_job = AsyncMock(
        return_value=JobResponse(job_id="test-job-id", status="accepted"),
    )
    client.pause_job = AsyncMock(return_value={"paused": True})
    client.resume_job = AsyncMock(return_value={"resumed": True})
    client.cancel_job = AsyncMock(return_value={"cancelled": True})
    client.clear_jobs = AsyncMock(return_value={"deleted": 1})
    client.get_job_status = AsyncMock(return_value={})
    return client


@pytest.fixture
def mock_job_control_service(mock_daemon_client):
    return JobControlService(mock_daemon_client)


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app, mock_state_backend, mock_job_control_service):
    with (
        patch(
            "marianne.dashboard.routes.jobs._get_job_control_service",
            return_value=mock_job_control_service,
        ),
        patch(
            "marianne.dashboard.routes.jobs.get_state_backend",
            return_value=mock_state_backend,
        ),
    ):
        yield TestClient(app)


@pytest.fixture
def sample_yaml_config(tmp_path):
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
    config_file = tmp_path / "e2e-test-config.yaml"
    config_file.write_text(sample_yaml_config)
    return config_file


class TestJobLifecycleE2E:
    async def test_complete_job_lifecycle(
        self,
        client: TestClient,
        sample_config_file: Path,
        mock_daemon_client: MagicMock,
    ):
        mock_daemon_client.submit_job.return_value = JobResponse(
            job_id="lifecycle-job-id",
            status="accepted",
        )
        mock_daemon_client.pause_job.return_value = {"paused": True}
        mock_daemon_client.resume_job.return_value = {"resumed": True}
        mock_daemon_client.cancel_job.return_value = {"cancelled": True}
        mock_daemon_client.clear_jobs.return_value = {"deleted": 1}

        start_response = client.post(
            "/api/jobs",
            json={
                "config_path": str(sample_config_file),
                "start_sheet": 1,
                "self_healing": True,
            },
        )

        assert start_response.status_code == 200
        start_data = start_response.json()
        assert start_data["success"] is True
        assert start_data["job_name"] == "e2e-test-job"
        assert start_data["total_sheets"] == 2
        assert start_data["status"] == "accepted"

        job_id = start_data["job_id"]
        mock_daemon_client.submit_job.assert_called_once()

        pause_response = client.post(f"/api/jobs/{job_id}/pause")
        assert pause_response.status_code == 200
        pause_data = pause_response.json()
        assert pause_data["success"] is True
        assert pause_data["job_id"] == job_id
        assert pause_data["status"] == "paused"
        mock_daemon_client.pause_job.assert_called_once_with(job_id, "")

        resume_response = client.post(f"/api/jobs/{job_id}/resume")
        assert resume_response.status_code == 200
        resume_data = resume_response.json()
        assert resume_data["success"] is True
        assert resume_data["status"] == "running"
        mock_daemon_client.resume_job.assert_called_once_with(job_id, "")

        cancel_response = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancel_response.status_code == 200
        cancel_data = cancel_response.json()
        assert cancel_data["success"] is True
        assert cancel_data["status"] == "cancelled"
        mock_daemon_client.cancel_job.assert_called_once_with(job_id, "")

        delete_response = client.delete(f"/api/jobs/{job_id}")
        assert delete_response.status_code == 200
        delete_data = delete_response.json()
        assert delete_data["success"] is True
        assert delete_data["job_id"] == job_id
        mock_daemon_client.clear_jobs.assert_called_once_with(job_ids=[job_id])

    async def test_job_start_with_inline_config(
        self,
        client: TestClient,
        sample_yaml_config: str,
        mock_daemon_client: MagicMock,
    ):
        mock_daemon_client.submit_job.return_value = JobResponse(
            job_id="inline-job-id",
            status="accepted",
        )

        response = client.post(
            "/api/jobs",
            json={
                "config_content": sample_yaml_config,
                "workspace": "./custom-workspace",
                "start_sheet": 2,
                "self_healing": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["job_name"] == "e2e-test-job"
        assert data["job_id"] == "inline-job-id"

    async def test_job_error_scenarios(self, client: TestClient):
        response = client.post("/api/jobs", json={})
        assert response.status_code == 400

        response = client.post(
            "/api/jobs",
            json={
                "config_path": "/nonexistent/file.yaml",
            },
        )
        assert response.status_code == 404

    async def test_job_state_validation_errors(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        completed_job_id = "completed-job"
        completed_state = CheckpointState(
            job_id=completed_job_id,
            job_name="completed-job",
            total_sheets=2,
            status=JobStatus.COMPLETED,
        )
        await mock_state_backend.save(completed_state)

        response = client.post(f"/api/jobs/{completed_job_id}/pause")
        assert response.status_code == 200

        response = client.delete(f"/api/jobs/{completed_job_id}")
        assert response.status_code in [200, 404]

    async def test_conductor_not_running(
        self,
        client: TestClient,
        mock_job_control_service: JobControlService,
    ):
        mock_job_control_service.start_job = AsyncMock(
            side_effect=RuntimeError("Conductor not running."),
        )

        response = client.post(
            "/api/jobs",
            json={
                "config_path": "/some/file.yaml",
            },
        )
        assert response.status_code == 503


class TestSheetDetailsE2E:
    async def test_get_sheet_details_success(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        job_id = "detailed-job"
        sheet_num = 1

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
                {"rule_type": "file_exists", "passed": True, "description": "Output file created"},
                {
                    "rule_type": "content_check",
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
            confidence_score=0.955,
            outcome_category=OutcomeCategory.SUCCESS_FIRST_TRY,
            success_without_retry=False,
            stdout_tail="Process completed successfully\nOutput written to file",
            stderr_tail="",
            output_truncated=False,
            preflight_warnings=[],
            applied_patterns=[
                {"id": "retry-pattern", "description": "retry-pattern"},
                {"id": "validation-pattern", "description": "validation-pattern"},
            ],
            grounding_passed=True,
            grounding_confidence=0.982,
            grounding_guidance="All grounding checks passed",
            input_tokens=1250,
            output_tokens=850,
            estimated_cost=0.045,
            cost_confidence=0.85,
            cost_uncertain=True,
        )

        state = CheckpointState(
            job_id=job_id,
            job_name="detailed-job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        state.sheets[sheet_num] = sheet_state

        await mock_state_backend.save(state)

        response = client.get(f"/api/jobs/{job_id}/sheets/{sheet_num}")

        assert response.status_code == 200
        data = response.json()

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
            "file_exists",
            "content_check",
        ]
        assert data["failed_validations"] == []
        assert data["last_pass_percentage"] == 100.0
        assert data["confidence_score"] == 0.955
        assert data["outcome_category"] == "success_first_try"
        assert data["success_without_retry"] is False
        assert data["stdout_tail"] == "Process completed successfully\nOutput written to file"
        assert data["stderr_tail"] == ""
        assert data["output_truncated"] is False
        assert data["applied_pattern_descriptions"] == [
            "retry-pattern",
            "validation-pattern",
        ]
        assert data["grounding_passed"] is True
        assert data["grounding_confidence"] == 0.982
        assert data["input_tokens"] == 1250
        assert data["output_tokens"] == 850
        assert data["estimated_cost"] == 0.045
        assert data["cost_confidence"] == 0.85
        # #373: $0-from-missing-pricing must be distinguishable from free
        assert data["cost_uncertain"] is True

    async def test_get_sheet_details_errors(
        self,
        client: TestClient,
        mock_state_backend: InMemoryStateBackend,
    ):
        response = client.get("/api/jobs/nonexistent/sheets/1")
        assert response.status_code == 404
        assert "Score not found" in response.json()["detail"]

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


if __name__ == "__main__":
    pytest.main([__file__])
