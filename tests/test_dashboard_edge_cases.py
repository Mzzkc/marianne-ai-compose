"""Edge case tests for dashboard services — conductor-only JobControlService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.exceptions import DaemonNotRunningError
from marianne.daemon.types import JobResponse
from marianne.dashboard.services.job_control import (
    JobControlService,
    ProcessHealth,
)

_VALID_YAML = """
name: "edge-test"
workspace: ./ws
sheet:
  size: 10
  total_items: 20
prompt:
  template: "do {{item}}"
"""


def _make_service() -> tuple[JobControlService, MagicMock]:
    mock_client = MagicMock()
    mock_client.submit_job = AsyncMock(
        return_value=JobResponse(job_id="job-1", status="accepted"),
    )
    mock_client.pause_job = AsyncMock(return_value={"paused": True})
    mock_client.resume_job = AsyncMock(return_value={"resumed": True})
    mock_client.cancel_job = AsyncMock(return_value={"cancelled": True})
    mock_client.clear_jobs = AsyncMock(return_value={"deleted": 1})
    mock_client.get_job_status = AsyncMock(
        return_value={
            "job_id": "job-1",
            "job_name": "test-job",
            "total_sheets": 2,
            "status": "running",
            "pid": 12345,
        }
    )
    service = JobControlService(mock_client)
    return service, mock_client


class TestJobControlEdgeCases:
    """Edge cases for the conductor-only JobControlService."""

    def test_constructor_rejects_none_daemon_client(self):
        with pytest.raises(ValueError, match="DaemonClient is required"):
            JobControlService(None)  # type: ignore[arg-type]

    async def test_start_job_no_config_raises(self):
        service, _ = _make_service()
        with pytest.raises(ValueError, match="Must provide either"):
            await service.start_job()

    async def test_start_job_invalid_yaml_config(self):
        service, _ = _make_service()
        invalid_yaml = """
name: "test-job"
description: "Test job"
workspace: ./test
    sheet:
size: 10
"""
        with pytest.raises(ValueError, match="Invalid job configuration"):
            await service.start_job(config_content=invalid_yaml)

    async def test_start_job_config_missing_required_fields(self):
        service, _ = _make_service()
        incomplete_yaml = """
name: "test-job"
"""
        with pytest.raises(Exception, match="sheet"):
            await service.start_job(config_content=incomplete_yaml)

    async def test_start_job_conductor_not_running(self):
        service, mock_client = _make_service()
        mock_client.submit_job.side_effect = DaemonNotRunningError("no socket")
        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.start_job(config_content=_VALID_YAML)

    async def test_start_job_success(self):
        service, mock_client = _make_service()
        result = await service.start_job(config_content=_VALID_YAML)
        assert result.job_id == "job-1"
        assert result.status == "accepted"
        assert result.job_name == "edge-test"
        assert result.total_sheets == 2
        mock_client.submit_job.assert_awaited_once()

    async def test_pause_job_success(self):
        service, mock_client = _make_service()
        result = await service.pause_job("job-1")
        assert result.success is True
        assert result.status == "paused"
        assert result.job_id == "job-1"
        mock_client.pause_job.assert_awaited_once_with("job-1", "")

    async def test_pause_job_conductor_not_running(self):
        service, mock_client = _make_service()
        mock_client.pause_job.side_effect = DaemonNotRunningError("down")
        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.pause_job("job-1")

    async def test_resume_job_success(self):
        service, mock_client = _make_service()
        result = await service.resume_job("job-1")
        assert result.success is True
        assert result.status == "running"
        assert result.job_id == "job-1"
        mock_client.resume_job.assert_awaited_once_with("job-1", "")

    async def test_resume_job_conductor_not_running(self):
        service, mock_client = _make_service()
        mock_client.resume_job.side_effect = DaemonNotRunningError("down")
        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.resume_job("job-1")

    async def test_cancel_job_success(self):
        service, mock_client = _make_service()
        result = await service.cancel_job("job-1")
        assert result.success is True
        assert result.status == "cancelled"
        assert result.job_id == "job-1"
        mock_client.cancel_job.assert_awaited_once_with("job-1", "")

    async def test_cancel_job_conductor_not_running(self):
        service, mock_client = _make_service()
        mock_client.cancel_job.side_effect = DaemonNotRunningError("down")
        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.cancel_job("job-1")

    async def test_delete_job_success(self):
        service, mock_client = _make_service()
        result = await service.delete_job("job-1")
        assert result is True
        mock_client.clear_jobs.assert_awaited_once_with(job_ids=["job-1"])

    async def test_delete_job_not_found(self):
        service, mock_client = _make_service()
        mock_client.clear_jobs.return_value = {"deleted": 0}
        result = await service.delete_job("nonexistent")
        assert result is False

    async def test_delete_job_conductor_not_running(self):
        service, mock_client = _make_service()
        mock_client.clear_jobs.side_effect = DaemonNotRunningError("down")
        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.delete_job("job-1")

    async def test_verify_process_health_running(self):
        service, mock_client = _make_service()
        with patch("os.kill", return_value=None):
            health = await service.verify_process_health("job-1")
        assert isinstance(health, ProcessHealth)
        assert health.pid == 12345
        assert health.is_alive is True
        assert health.process_exists is True
        assert health.is_zombie_state is False

    async def test_verify_process_health_terminal(self):
        service, mock_client = _make_service()
        mock_client.get_job_status.return_value = {
            "job_id": "job-1",
            "job_name": "test-job",
            "total_sheets": 2,
            "status": "completed",
            "pid": 12345,
        }
        health = await service.verify_process_health("job-1")
        assert health.is_alive is False
        assert health.process_exists is False

    async def test_verify_process_health_conductor_not_running(self):
        service, mock_client = _make_service()
        mock_client.get_job_status.side_effect = DaemonNotRunningError("down")
        health = await service.verify_process_health("job-1")
        assert health.is_alive is False
        assert health.process_exists is False
        assert health.pid is None


@pytest.mark.adversarial
class TestJobControlAdversarial:
    """Adversarial tests attacking the conductor proxy layer."""

    async def test_start_job_conductor_rejects_submission(self):
        service, mock_client = _make_service()
        mock_client.submit_job.return_value = JobResponse(
            job_id="job-rejected",
            status="rejected",
            message="Config validation failed on server",
        )
        with pytest.raises(RuntimeError, match="Conductor rejected job"):
            await service.start_job(config_content=_VALID_YAML)

    async def test_start_job_conductor_returns_error_status(self):
        service, mock_client = _make_service()
        mock_client.submit_job.return_value = JobResponse(
            job_id="",
            status="error",
            message="Internal daemon fault",
        )
        with pytest.raises(RuntimeError, match="Conductor rejected job"):
            await service.start_job(config_content=_VALID_YAML)

    async def test_pause_job_generic_exception(self):
        service, mock_client = _make_service()
        mock_client.pause_job.side_effect = OSError("Socket broken")
        with pytest.raises(OSError, match="Socket broken"):
            await service.pause_job("job-1")

    async def test_delete_job_malformed_response(self):
        service, mock_client = _make_service()
        mock_client.clear_jobs.return_value = {}
        result = await service.delete_job("job-1")
        assert result is False

    async def test_verify_process_health_paused_job(self):
        service, mock_client = _make_service()
        mock_client.get_job_status.return_value = {
            "job_id": "job-1",
            "job_name": "test-job",
            "total_sheets": 2,
            "status": "paused",
            "pid": 12345,
        }
        with patch("os.kill", return_value=None):
            health = await service.verify_process_health("job-1")
        assert health.is_alive is True
        assert health.process_exists is True

    async def test_start_job_with_config_path_traversal(self):
        service, _ = _make_service()
        with pytest.raises(ValueError, match="traversal"):
            await service.start_job(config_path=Path("../../../etc/passwd.yaml"))

    async def test_start_job_with_nonexistent_config_path(self):
        service, _ = _make_service()
        with pytest.raises(FileNotFoundError):
            await service.start_job(config_path=Path("/nonexistent/score.yaml"))

    async def test_start_job_concurrent_submissions(self):
        import asyncio

        service, mock_client = _make_service()
        mock_client.submit_job = AsyncMock(
            side_effect=[
                JobResponse(job_id="j1", status="accepted"),
                JobResponse(job_id="j2", status="accepted"),
                JobResponse(job_id="j3", status="accepted"),
            ]
        )
        results = await asyncio.gather(
            service.start_job(config_content=_VALID_YAML),
            service.start_job(config_content=_VALID_YAML),
            service.start_job(config_content=_VALID_YAML),
        )
        assert {r.job_id for r in results} == {"j1", "j2", "j3"}
