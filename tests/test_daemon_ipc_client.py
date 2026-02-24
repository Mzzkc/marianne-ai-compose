"""Tests for mozart.daemon.ipc.client — async Unix socket client.

Tests both the client in isolation (socket doesn't exist) and integrated
with a real DaemonServer (using tmp_path for the socket).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mozart.daemon.exceptions import DaemonNotRunningError, JobSubmissionError
from mozart.daemon.ipc.client import DaemonClient
from mozart.daemon.ipc.handler import RequestHandler
from mozart.daemon.ipc.server import DaemonServer
from mozart.daemon.types import DaemonStatus, JobRequest, JobResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_handler() -> RequestHandler:
    """Build a handler that mimics real daemon methods."""
    handler = RequestHandler()

    async def _daemon_health(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {"status": "alive"}

    async def _daemon_status(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {
            "pid": 12345,
            "uptime_seconds": 100.5,
            "running_jobs": 2,
            "total_jobs_active": 5,
            "memory_usage_mb": 256.0,
            "version": "0.1.0-test",
        }

    async def _job_submit(params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {
            "job_id": "test-job-1",
            "status": "accepted",
            "message": f"Job accepted: {params.get('config_path', 'unknown')}",
        }

    async def _job_status(params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {
            "job_id": params["job_id"],
            "status": "running",
            "sheets_completed": 3,
            "total_sheets": 10,
        }

    async def _job_pause(params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {"paused": True}

    async def _job_list(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return [
            {"job_id": "job-1", "status": "running"},
            {"job_id": "job-2", "status": "completed"},
        ]

    async def _job_fail(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        raise JobSubmissionError("job not found: nonexistent")

    handler.register("daemon.health", _daemon_health)
    handler.register("daemon.status", _daemon_status)
    handler.register("job.submit", _job_submit)
    handler.register("job.status", _job_status)
    handler.register("job.pause", _job_pause)
    handler.register("job.list", _job_list)
    handler.register("test.fail", _job_fail)
    return handler


# ---------------------------------------------------------------------------
# Tests: client without server (negative cases)
# ---------------------------------------------------------------------------


class TestDaemonClientNoServer:
    """Tests for client behavior when no daemon is running."""

    @pytest.mark.asyncio
    async def test_is_daemon_running_returns_false_no_socket(self, tmp_path: Path):
        """is_daemon_running() returns False when socket doesn't exist."""
        sock = tmp_path / "nonexistent.sock"
        client = DaemonClient(sock)
        assert await client.is_daemon_running() is False

    @pytest.mark.asyncio
    async def test_is_daemon_running_returns_false_stale_socket(self, tmp_path: Path):
        """is_daemon_running() returns False when socket file exists but no server."""
        sock = tmp_path / "stale.sock"
        sock.touch()  # File exists but nothing is listening
        client = DaemonClient(sock)
        assert await client.is_daemon_running() is False

    @pytest.mark.asyncio
    async def test_call_raises_not_running_no_socket(self, tmp_path: Path):
        """call() raises DaemonNotRunningError when socket doesn't exist."""
        sock = tmp_path / "nonexistent.sock"
        client = DaemonClient(sock)
        with pytest.raises(DaemonNotRunningError):
            await client.call("daemon.status")

    @pytest.mark.asyncio
    async def test_status_raises_not_running(self, tmp_path: Path):
        """status() raises DaemonNotRunningError when no daemon."""
        sock = tmp_path / "nonexistent.sock"
        client = DaemonClient(sock)
        with pytest.raises(DaemonNotRunningError):
            await client.status()

    @pytest.mark.asyncio
    async def test_submit_job_raises_not_running(self, tmp_path: Path):
        """submit_job() raises DaemonNotRunningError when no daemon."""
        sock = tmp_path / "nonexistent.sock"
        client = DaemonClient(sock)
        req = JobRequest(config_path=Path("/tmp/test.yaml"))
        with pytest.raises(DaemonNotRunningError):
            await client.submit_job(req)


# ---------------------------------------------------------------------------
# Tests: client with real server (integration)
# ---------------------------------------------------------------------------


class TestDaemonClientWithServer:
    """Integration tests: client communicates with a real DaemonServer."""

    @pytest.mark.asyncio
    async def test_is_daemon_running_returns_true(self, tmp_path: Path):
        """is_daemon_running() returns True when server is listening."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            assert await client.is_daemon_running() is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_call_returns_result(self, tmp_path: Path):
        """Raw call() returns the result dict."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            result = await client.call("daemon.status")
            assert result["pid"] == 12345
            assert result["running_jobs"] == 2
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_status_returns_typed_model(self, tmp_path: Path):
        """status() returns a DaemonStatus Pydantic model."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            status = await client.status()
            assert isinstance(status, DaemonStatus)
            assert status.pid == 12345
            assert status.uptime_seconds == 100.5
            assert status.version == "0.1.0-test"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_submit_job_returns_typed_model(self, tmp_path: Path):
        """submit_job() returns a JobResponse Pydantic model."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            req = JobRequest(config_path=Path("/tmp/test.yaml"))
            resp = await client.submit_job(req)
            assert isinstance(resp, JobResponse)
            assert resp.job_id == "test-job-1"
            assert resp.status == "accepted"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_get_job_status(self, tmp_path: Path):
        """get_job_status() returns status dict for a job."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            result = await client.get_job_status("my-job", "/tmp/ws")
            assert result["job_id"] == "my-job"
            assert result["status"] == "running"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_pause_job(self, tmp_path: Path):
        """pause_job() returns True on success."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            result = await client.pause_job("my-job", "/tmp/ws")
            assert result == {"paused": True}
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_list_jobs(self, tmp_path: Path):
        """list_jobs() returns a list of job dicts."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            jobs = await client.list_jobs()
            assert len(jobs) == 2
            assert jobs[0]["job_id"] == "job-1"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_error_raises_exception(self, tmp_path: Path):
        """Server-side DaemonError is raised as an exception on the client."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            with pytest.raises(JobSubmissionError, match="job not found"):
                await client.call("test.fail")
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_call_unknown_method_raises(self, tmp_path: Path):
        """Calling an unknown method raises DaemonError from the error code."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_test_handler())
        await server.start()
        try:
            client = DaemonClient(sock)
            # method_not_found maps to -32601 which is not in _CODE_EXCEPTION_MAP,
            # so it falls back to DaemonError
            from mozart.daemon.exceptions import DaemonError

            with pytest.raises(DaemonError, match="Method not found"):
                await client.call("nonexistent.rpc.method")
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Tests: timeout handling
# ---------------------------------------------------------------------------


class TestDaemonClientTimeout:
    """Tests for client timeout behavior."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_timeout_on_slow_response(self, tmp_path: Path):
        """Client timeout fires when server doesn't respond in time.

        Uses a raw socket server that accepts but never responds, avoiding
        the bidirectional cleanup deadlock between DaemonClient and
        DaemonServer when both try to close their writers simultaneously.
        """
        import socket as sock_mod

        sock_path = tmp_path / "test.sock"
        # Create a raw Unix socket server that accepts but never responds
        srv_sock = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        srv_sock.bind(str(sock_path))
        srv_sock.listen(1)
        srv_sock.setblocking(False)

        try:
            client = DaemonClient(sock_path, timeout=0.5)
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                await client.call("test.slow")
        finally:
            srv_sock.close()
            sock_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_connection_timeout_no_server(self, tmp_path: Path):
        """is_daemon_running returns False quickly even with timeout."""
        sock = tmp_path / "nonexistent.sock"
        client = DaemonClient(sock, timeout=0.5)
        # Should return False, not hang
        result = await asyncio.wait_for(client.is_daemon_running(), timeout=3.0)
        assert result is False
