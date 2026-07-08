"""Comprehensive tests for pause/resume via conductor-only JobControlService.

Tests cover:
- pause/resume/cancel proxy through DaemonClient
- RuntimeError when conductor is down (DaemonNotRunningError)
- Edge cases: concurrent requests, double operations, None daemon client
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.daemon.exceptions import DaemonNotRunningError
from marianne.dashboard.services.job_control import JobActionResult, JobControlService


def _make_daemon_client() -> MagicMock:
    mock = MagicMock()
    mock.pause_job = AsyncMock(return_value={"paused": True})
    mock.resume_job = AsyncMock(return_value={"status": "accepted"})
    mock.cancel_job = AsyncMock(return_value={"cancelled": True})
    return mock


class TestJobControlServiceConstruction:
    """Test construction and validation."""

    def test_none_daemon_client_raises(self):
        with pytest.raises(ValueError, match="DaemonClient is required"):
            JobControlService(None)  # type: ignore[arg-type]

    def test_valid_daemon_client(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)
        assert service._client is mock


class TestPauseJob:
    """Test pause_job conductor proxy."""

    async def test_pause_calls_daemon_client(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)

        result = await service.pause_job("job-123")

        mock.pause_job.assert_awaited_once_with("job-123", "")
        assert result.success
        assert result.job_id == "job-123"
        assert result.status == "pause_requested"
        assert "pause" in result.message.lower()

    async def test_pause_conductor_not_running(self):
        mock = _make_daemon_client()
        mock.pause_job.side_effect = DaemonNotRunningError()
        service = JobControlService(mock)

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.pause_job("job-123")

    async def test_pause_conductor_rejects_terminal_job(self):
        mock = _make_daemon_client()
        mock.pause_job.side_effect = ValueError("Job job-done is already completed")
        service = JobControlService(mock)

        with pytest.raises(ValueError, match="already completed"):
            await service.pause_job("job-done")


class TestResumeJob:
    """Test resume_job conductor proxy."""

    async def test_resume_calls_daemon_client(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)

        result = await service.resume_job("job-456")

        mock.resume_job.assert_awaited_once_with("job-456", "")
        assert result.success
        assert result.job_id == "job-456"
        assert result.status == "resume_requested"
        assert "resume" in result.message.lower()

    async def test_resume_conductor_not_running(self):
        mock = _make_daemon_client()
        mock.resume_job.side_effect = DaemonNotRunningError()
        service = JobControlService(mock)

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.resume_job("job-456")

    async def test_resume_non_paused_job_forwards_error(self):
        mock = _make_daemon_client()
        mock.resume_job.side_effect = ValueError("Job job-running is not paused")
        service = JobControlService(mock)

        with pytest.raises(ValueError, match="not paused"):
            await service.resume_job("job-running")


class TestCancelJob:
    """Test cancel_job conductor proxy."""

    async def test_cancel_calls_daemon_client(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)

        result = await service.cancel_job("job-789")

        mock.cancel_job.assert_awaited_once_with("job-789", "")
        assert result.success
        assert result.job_id == "job-789"
        assert result.status == "cancel_requested"
        assert "cancel" in result.message.lower()

    async def test_cancel_conductor_not_running(self):
        mock = _make_daemon_client()
        mock.cancel_job.side_effect = DaemonNotRunningError()
        service = JobControlService(mock)

        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.cancel_job("job-789")


class TestPauseResumeCancelIntegration:
    """Integration-style tests for multi-step flows."""

    async def test_full_pause_resume_cycle(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)

        pause_result = await service.pause_job("job-cycle")
        assert pause_result.success
        assert pause_result.status == "pause_requested"

        resume_result = await service.resume_job("job-cycle")
        assert resume_result.success
        assert resume_result.status == "resume_requested"

        mock.pause_job.assert_awaited_once_with("job-cycle", "")
        mock.resume_job.assert_awaited_once_with("job-cycle", "")

    async def test_pause_then_cancel(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)

        pause_result = await service.pause_job("job-pc")
        assert pause_result.success

        cancel_result = await service.cancel_job("job-pc")
        assert cancel_result.success
        assert cancel_result.status == "cancel_requested"

    async def test_concurrent_pause_requests(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)

        results = await asyncio.gather(
            service.pause_job("job-concurrent"),
            service.pause_job("job-concurrent"),
            service.pause_job("job-concurrent"),
        )

        assert all(r.success for r in results)
        assert mock.pause_job.await_count == 3

    async def test_conductor_down_mid_flow(self):
        mock = _make_daemon_client()
        service = JobControlService(mock)

        pause_result = await service.pause_job("job-flaky")
        assert pause_result.success

        mock.resume_job.side_effect = DaemonNotRunningError()
        with pytest.raises(RuntimeError, match="Conductor not running"):
            await service.resume_job("job-flaky")


class TestJobActionResultDataclass:
    """Test the JobActionResult dataclass."""

    def test_defaults(self):
        result = JobActionResult(
            success=True,
            job_id="x",
            status="paused",
            message="ok",
        )
        assert result.via_daemon is True

    def test_via_daemon_explicit(self):
        result = JobActionResult(
            success=False,
            job_id="y",
            status="failed",
            message="nope",
            via_daemon=False,
        )
        assert result.via_daemon is False
