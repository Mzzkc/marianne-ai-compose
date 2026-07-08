"""Tests for JobControlService — conductor-only proxy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.daemon.exceptions import DaemonNotRunningError
from marianne.daemon.types import JobResponse
from marianne.dashboard.services.job_control import (
    JobActionResult,
    JobControlService,
    JobStartResult,
    ProcessHealth,
)


@pytest.fixture
def mock_daemon_client() -> MagicMock:
    """Create a mock DaemonClient."""
    client = MagicMock()
    client.submit_job = AsyncMock()
    client.pause_job = AsyncMock()
    client.resume_job = AsyncMock()
    client.cancel_job = AsyncMock()
    client.clear_jobs = AsyncMock()
    client.get_job_status = AsyncMock()
    return client


@pytest.fixture
def job_control_service(mock_daemon_client: MagicMock) -> JobControlService:
    """Fixture for JobControlService backed by mock DaemonClient."""
    return JobControlService(mock_daemon_client)


@pytest.fixture
def sample_yaml_config() -> str:
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
def sample_config_file(tmp_path: Path, sample_yaml_config: str) -> Path:
    config_file = tmp_path / "test-config.yaml"
    config_file.write_text(sample_yaml_config)
    return config_file


class TestJobControlServiceInit:
    def test_requires_daemon_client(self) -> None:
        with pytest.raises(ValueError, match="DaemonClient is required"):
            JobControlService(None)  # type: ignore[arg-type]

    def test_accepts_daemon_client(self, mock_daemon_client: MagicMock) -> None:
        service = JobControlService(mock_daemon_client)
        assert service._client is mock_daemon_client


class TestStartJob:
    @pytest.mark.asyncio
    async def test_start_job_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
        sample_config_file: Path,
    ) -> None:
        mock_daemon_client.submit_job.return_value = JobResponse(
            job_id="job-abc",
            status="accepted",
        )

        result = await job_control_service.start_job(config_path=sample_config_file)

        assert isinstance(result, JobStartResult)
        assert result.job_id == "job-abc"
        assert result.job_name == "test-job"
        assert result.status == "accepted"
        assert result.via_daemon is True

        mock_daemon_client.submit_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_job_with_config_content(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
        sample_yaml_config: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MARIANNE_DASHBOARD_SUBMISSIONS_DIR", str(tmp_path))
        mock_daemon_client.submit_job.return_value = JobResponse(
            job_id="job-xyz",
            status="accepted",
        )

        result = await job_control_service.start_job(
            config_content=sample_yaml_config,
            workspace=Path("./custom-workspace"),
        )

        assert isinstance(result, JobStartResult)
        assert result.job_id == "job-xyz"
        assert result.job_name == "test-job"
        request = mock_daemon_client.submit_job.call_args.args[0]
        assert request.config_path.parent == tmp_path
        assert request.config_path.exists()
        assert request.config_path.read_text(encoding="utf-8") == sample_yaml_config

    @pytest.mark.asyncio
    async def test_start_job_forwards_daemon_request_options(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
        sample_config_file: Path,
        tmp_path: Path,
    ) -> None:
        mock_daemon_client.submit_job.return_value = JobResponse(
            job_id="job-options",
            status="pending",
        )
        workspace = tmp_path / "workspace"
        client_cwd = tmp_path / "caller"
        client_cwd.mkdir()

        result = await job_control_service.start_job(
            config_path=sample_config_file,
            workspace=workspace,
            start_sheet=2,
            fresh=True,
            self_healing=True,
            self_healing_auto_confirm=True,
            escalation=True,
            dry_run=True,
            chain_depth=3,
            client_cwd=client_cwd,
            runtime_variables={"clip_id": "abc123"},
        )

        assert result.status == "pending"
        request = mock_daemon_client.submit_job.call_args.args[0]
        assert request.workspace == workspace.resolve()
        assert request.start_sheet == 2
        assert request.fresh is True
        assert request.self_healing is True
        assert request.self_healing_auto_confirm is True
        assert request.escalation is True
        assert request.dry_run is True
        assert request.chain_depth == 3
        assert request.client_cwd == client_cwd.resolve()
        assert request.runtime_variables == {"clip_id": "abc123"}

    @pytest.mark.asyncio
    async def test_start_job_no_config_raises_error(
        self,
        job_control_service: JobControlService,
    ) -> None:
        with pytest.raises(ValueError, match="Must provide either"):
            await job_control_service.start_job()

    @pytest.mark.asyncio
    async def test_start_job_nonexistent_file_raises_error(
        self,
        job_control_service: JobControlService,
    ) -> None:
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            await job_control_service.start_job(config_path=Path("/nonexistent/config.yaml"))

    @pytest.mark.asyncio
    async def test_start_job_traversal_raises_error(
        self,
        job_control_service: JobControlService,
    ) -> None:
        with pytest.raises(ValueError, match="traversal"):
            await job_control_service.start_job(config_path=Path("../../../etc/config.yaml"))

    @pytest.mark.asyncio
    async def test_start_job_conductor_not_running(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
        sample_config_file: Path,
    ) -> None:
        mock_daemon_client.submit_job.side_effect = DaemonNotRunningError("not running")

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await job_control_service.start_job(config_path=sample_config_file)

    @pytest.mark.asyncio
    async def test_start_job_timeout(
        self,
        job_control_service: JobControlService,
        sample_config_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _timed_out(coro, *_args, **_kwargs):
            coro.close()
            raise TimeoutError

        monkeypatch.setattr(
            "marianne.dashboard.services.job_control.asyncio.wait_for",
            _timed_out,
        )

        with pytest.raises(RuntimeError, match="timed out"):
            await job_control_service.start_job(config_path=sample_config_file)


class TestPauseJob:
    @pytest.mark.asyncio
    async def test_pause_job_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.pause_job.return_value = {"paused": True}

        result = await job_control_service.pause_job("job-123")

        assert isinstance(result, JobActionResult)
        assert result.success is True
        assert result.job_id == "job-123"
        assert result.status == "pause_requested"
        assert result.via_daemon is True
        mock_daemon_client.pause_job.assert_called_once_with("job-123", "")

    @pytest.mark.asyncio
    async def test_pause_job_rejected_response_is_not_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.pause_job.return_value = {
            "paused": False,
            "error": "Score not found: job-123",
        }

        result = await job_control_service.pause_job("job-123")

        assert result.success is False
        assert result.status == "pause_rejected"
        assert result.message == "Score not found: job-123"

    @pytest.mark.asyncio
    async def test_pause_job_conductor_not_running(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.pause_job.side_effect = DaemonNotRunningError("down")

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await job_control_service.pause_job("job-123")


class TestResumeJob:
    @pytest.mark.asyncio
    async def test_resume_job_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.resume_job.return_value = {
            "job_id": "job-123",
            "status": "accepted",
            "message": None,
        }

        result = await job_control_service.resume_job("job-123")

        assert result.success is True
        assert result.job_id == "job-123"
        assert result.status == "resume_requested"
        assert result.via_daemon is True
        mock_daemon_client.resume_job.assert_called_once_with("job-123", "")

    @pytest.mark.asyncio
    async def test_resume_job_rejected_response_is_not_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.resume_job.return_value = {
            "job_id": "job-123",
            "status": "rejected",
            "message": "Job is not paused",
        }

        result = await job_control_service.resume_job("job-123")

        assert result.success is False
        assert result.status == "resume_rejected"
        assert result.message == "Job is not paused"

    @pytest.mark.asyncio
    async def test_resume_job_conductor_not_running(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.resume_job.side_effect = DaemonNotRunningError("down")

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await job_control_service.resume_job("job-123")


class TestCancelJob:
    @pytest.mark.asyncio
    async def test_cancel_job_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.cancel_job.return_value = {"cancelled": True}

        result = await job_control_service.cancel_job("job-123")

        assert result.success is True
        assert result.job_id == "job-123"
        assert result.status == "cancel_requested"
        assert result.via_daemon is True
        mock_daemon_client.cancel_job.assert_called_once_with("job-123", "")

    @pytest.mark.asyncio
    async def test_cancel_job_rejected_response_is_not_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.cancel_job.return_value = {"cancelled": False}

        result = await job_control_service.cancel_job("job-123")

        assert result.success is False
        assert result.status == "cancel_rejected"
        assert result.message == "Cancel request was rejected"

    @pytest.mark.asyncio
    async def test_cancel_job_conductor_not_running(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.cancel_job.side_effect = DaemonNotRunningError("down")

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await job_control_service.cancel_job("job-123")

    @pytest.mark.asyncio
    async def test_cancel_job_timeout(
        self,
        job_control_service: JobControlService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _timed_out(coro, *_args, **_kwargs):
            coro.close()
            raise TimeoutError

        monkeypatch.setattr(
            "marianne.dashboard.services.job_control.asyncio.wait_for",
            _timed_out,
        )

        with pytest.raises(RuntimeError, match="cancel request timed out"):
            await job_control_service.cancel_job("job-123")


class TestDeleteJob:
    @pytest.mark.asyncio
    async def test_delete_job_success(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.clear_jobs.return_value = {"deleted": 1}

        result = await job_control_service.delete_job("job-123")

        assert result is True
        mock_daemon_client.clear_jobs.assert_called_once_with(job_ids=["job-123"])

    @pytest.mark.asyncio
    async def test_delete_job_not_found(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.clear_jobs.return_value = {"deleted": 0}

        result = await job_control_service.delete_job("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_job_conductor_not_running(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.clear_jobs.side_effect = DaemonNotRunningError("down")

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await job_control_service.delete_job("job-123")


class TestVerifyProcessHealth:
    @pytest.mark.asyncio
    async def test_running_job_health(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        from datetime import UTC, datetime

        mock_daemon_client.get_job_status.return_value = {
            "job_id": "job-123",
            "job_name": "test",
            "total_sheets": 2,
            "status": "running",
            "pid": 12345,
            "started_at": datetime.now(UTC).isoformat(),
            "sheets": {},
        }

        health = await job_control_service.verify_process_health("job-123")

        assert isinstance(health, ProcessHealth)
        assert health.pid == 12345
        assert health.is_alive is True
        assert health.process_exists is True

    @pytest.mark.asyncio
    async def test_completed_job_health(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.get_job_status.return_value = {
            "job_id": "job-123",
            "job_name": "test",
            "total_sheets": 2,
            "status": "completed",
            "sheets": {},
        }

        health = await job_control_service.verify_process_health("job-123")

        assert health.is_alive is False
        assert health.process_exists is False

    @pytest.mark.asyncio
    async def test_conductor_not_running(
        self,
        job_control_service: JobControlService,
        mock_daemon_client: MagicMock,
    ) -> None:
        mock_daemon_client.get_job_status.side_effect = DaemonNotRunningError("down")

        health = await job_control_service.verify_process_health("job-123")

        assert health.pid is None
        assert health.is_alive is False
        assert health.process_exists is False

    @pytest.mark.asyncio
    async def test_health_timeout(
        self,
        job_control_service: JobControlService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _timed_out(coro, *_args, **_kwargs):
            coro.close()
            raise TimeoutError

        monkeypatch.setattr(
            "marianne.dashboard.services.job_control.asyncio.wait_for",
            _timed_out,
        )

        health = await job_control_service.verify_process_health("job-123")

        assert health.pid is None
        assert health.is_alive is False
        assert health.process_exists is False


if __name__ == "__main__":
    pytest.main([__file__])
