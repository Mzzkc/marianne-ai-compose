"""Tests for marianne.daemon.detect — CLI daemon detection and routing.

The detect module provides daemon availability checks and RPC routing.
``mzt run`` requires a running daemon — if unavailable, the CLI
prints an error and exits.  ``is_daemon_available()`` and
``try_daemon_route()`` never raise; callers inspect return values.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.detect import is_daemon_available, try_daemon_route
from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.server import DaemonServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_detect_handler() -> RequestHandler:
    """Build a handler with methods needed for detection tests."""
    handler = RequestHandler()

    async def _health(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {"status": "alive"}

    async def _status(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {
            "pid": 9999,
            "uptime_seconds": 42.0,
            "running_jobs": 1,
            "total_jobs_active": 3,
            "memory_usage_mb": 128.0,
            "version": "0.1.0",
        }

    async def _echo(params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {"echo": params}

    handler.register("daemon.health", _health)
    handler.register("daemon.status", _status)
    handler.register("test.echo", _echo)
    return handler


# ---------------------------------------------------------------------------
# Tests: is_daemon_available
# ---------------------------------------------------------------------------


class TestIsDaemonAvailable:
    """Tests for is_daemon_available()."""

    @pytest.mark.asyncio
    async def test_returns_false_no_socket(self, tmp_path: Path):
        """Returns False when no socket file exists."""
        sock = tmp_path / "nonexistent.sock"
        result = await is_daemon_available(sock)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_stale_socket(self, tmp_path: Path):
        """Returns False when socket file exists but no server listens."""
        sock = tmp_path / "stale.sock"
        sock.touch()
        result = await is_daemon_available(sock)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_running(self, tmp_path: Path):
        """Returns True when a server is actually listening."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_detect_handler())
        await server.start()
        try:
            result = await is_daemon_available(sock)
            assert result is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_never_raises(self, tmp_path: Path):
        """Function never raises, even with invalid input."""
        # Pass a directory instead of a file — should return False, not raise
        result = await is_daemon_available(tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# Tests: try_daemon_route
# ---------------------------------------------------------------------------


class TestTryDaemonRoute:
    """Tests for try_daemon_route()."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_daemon(self, tmp_path: Path):
        """Returns (False, None) when daemon is not running."""
        sock = tmp_path / "nonexistent.sock"
        routed, result = await try_daemon_route(
            "daemon.status", {}, socket_path=sock
        )
        assert routed is False
        assert result is None

    @pytest.mark.asyncio
    async def test_routes_through_daemon(self, tmp_path: Path):
        """Returns (True, result) when daemon handles the request."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_detect_handler())
        await server.start()
        try:
            routed, result = await try_daemon_route(
                "test.echo",
                {"message": "routed"},
                socket_path=sock,
            )
            assert routed is True
            assert result["echo"]["message"] == "routed"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_raises_on_unknown_method(self, tmp_path: Path):
        """Raises MethodNotFoundError when daemon doesn't know the method (F-450).

        Previously returned (False, None) which caused misleading "conductor
        not running" messages. Now re-raises with restart guidance.
        """
        from marianne.daemon.exceptions import MethodNotFoundError

        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_detect_handler())
        await server.start()
        try:
            with pytest.raises(MethodNotFoundError, match="does not support"):
                await try_daemon_route(
                    "nonexistent.method",
                    {},
                    socket_path=sock,
                )
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_never_raises(self, tmp_path: Path):
        """Function never raises, even on unexpected errors."""
        # Invalid path that will cause errors
        routed, result = await try_daemon_route(
            "daemon.status", {}, socket_path=tmp_path / "nope.sock"
        )
        assert routed is False
        assert result is None

    @pytest.mark.asyncio
    async def test_routes_status_request(self, tmp_path: Path):
        """Status request returns full daemon status through routing."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_detect_handler())
        await server.start()
        try:
            routed, result = await try_daemon_route(
                "daemon.status", {}, socket_path=sock
            )
            assert routed is True
            assert result["pid"] == 9999
            assert result["version"] == "0.1.0"
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Tests: CLI non-daemon regression (subprocess-based)
# ---------------------------------------------------------------------------


class TestCliNonDaemonRegression:
    """Verify that Marianne CLI's non-execution paths work without a daemon.

    Uses subprocess execution to test the real CLI entry point.
    ``--dry-run`` and ``validate`` don't need a daemon.  Actual
    ``run`` (without ``--dry-run``) will error if no daemon is running.
    """

    def test_dry_run_without_daemon(self):
        """mzt run --dry-run succeeds without a daemon socket.

        Dry-run mode validates and displays the job plan without
        executing, so it doesn't require a running daemon.
        """
        result = subprocess.run(
            [
                sys.executable, "-m", "marianne", "run",
                "examples/simple-sheet.yaml", "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **__import__("os").environ,
                # Ensure no daemon socket is found
                "MZT_SOCKET_PATH": "/tmp/nonexistent-marianne-test.sock",
            },
        )
        assert result.returncode == 0, (
            f"CLI dry-run failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
        # Verify expected output markers
        assert "simple-sheet" in result.stdout.lower() or "dry run" in result.stdout.lower(), (
            f"Expected output markers not found:\n{result.stdout[:500]}"
        )

    def test_validate_without_daemon(self):
        """mzt validate succeeds without a daemon socket."""
        result = subprocess.run(
            [
                sys.executable, "-m", "marianne", "validate",
                "examples/simple-sheet.yaml",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"CLI validate failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
