"""Tests for daemon detection and CLI routing.

Verifies the safety-critical property: detect.py functions NEVER raise.
All exception paths must return safe fallback values.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mozart.daemon.detect import (
    _resolve_socket_path,
    is_daemon_available,
    try_daemon_route,
)

# =============================================================================
# _resolve_socket_path
# =============================================================================


class TestResolveSocketPath:
    """Tests for socket path resolution."""

    def test_returns_explicit_path(self):
        """Explicit path is used when provided."""
        p = Path("/custom/socket.sock")
        assert _resolve_socket_path(p) == p

    def test_falls_back_to_socket_config_default(self):
        """None triggers fallback to SocketConfig().path."""
        result = _resolve_socket_path(None)
        assert result == Path("/tmp/mozart.sock")


# =============================================================================
# is_daemon_available
# =============================================================================


# The DaemonClient is imported INSIDE the function body via
# `from mozart.daemon.ipc.client import DaemonClient`, so we patch it
# at the source module.
_CLIENT_PATH = "mozart.daemon.ipc.client.DaemonClient"


@pytest.mark.asyncio
class TestIsDaemonAvailable:
    """Tests for daemon availability detection."""

    async def test_returns_true_when_daemon_running(self):
        """Happy path: daemon responds, returns True."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is True

    async def test_returns_false_when_daemon_not_running(self):
        """Daemon client responds with not running."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=False)

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is False

    async def test_oserror_returns_false(self):
        """OSError (e.g. socket not found) returns False."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(
                side_effect=OSError("No such file")
            )

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is False

    async def test_connection_error_returns_false(self):
        """ConnectionError (socket exists but daemon dead) returns False."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(
                side_effect=ConnectionRefusedError("refused")
            )

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is False

    async def test_import_error_returns_false(self):
        """ImportError (daemon modules missing) returns False."""
        with patch(
            _CLIENT_PATH,
            side_effect=ImportError("no module"),
        ):
            result = await is_daemon_available()

        assert result is False

    async def test_unexpected_exception_returns_false(self):
        """Arbitrary exceptions return False (safety guarantee)."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(
                side_effect=RuntimeError("unexpected crash")
            )

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is False

    async def test_none_socket_uses_default(self):
        """None socket_path triggers SocketConfig fallback."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)

            result = await is_daemon_available(None)

        assert result is True
        # Client was created with the default path
        MockClient.assert_called_once_with(Path("/tmp/mozart.sock"))


# =============================================================================
# try_daemon_route
# =============================================================================


@pytest.mark.asyncio
class TestTryDaemonRoute:
    """Tests for daemon routing."""

    async def test_routes_successfully(self):
        """When daemon is running, routes and returns result."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(return_value={"status": "ok"})

            routed, result = await try_daemon_route(
                "job.submit",
                {"config": "test.yaml"},
                socket_path=Path("/tmp/test.sock"),
            )

        assert routed is True
        assert result == {"status": "ok"}
        client.call.assert_called_once_with(
            "job.submit", {"config": "test.yaml"}
        )

    async def test_returns_false_when_daemon_not_running(self):
        """When daemon not running, returns (False, None)."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=False)

            routed, result = await try_daemon_route("job.submit", {})

        assert routed is False
        assert result is None
        client.call.assert_not_called()

    async def test_oserror_returns_false_none(self):
        """OSError during routing returns (False, None)."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(side_effect=OSError("broken pipe"))

            routed, result = await try_daemon_route("job.status", {})

        assert routed is False
        assert result is None

    async def test_timeout_error_returns_false_none(self):
        """TimeoutError during routing returns (False, None)."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(side_effect=TimeoutError("timed out"))

            routed, result = await try_daemon_route("job.status", {})

        assert routed is False
        assert result is None

    async def test_value_error_returns_false_with_error_details(self):
        """ValueError during routing returns (False, error_details).

        Non-JSONDecodeError ValueErrors indicate daemon-side bugs, so the
        error details are returned to help callers distinguish "daemon not
        running" from "daemon has a protocol bug".
        """
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(side_effect=ValueError("invalid params"))

            routed, result = await try_daemon_route("job.status", {})

        assert routed is False
        assert result == {"error": "invalid params", "error_type": "ValueError"}

    async def test_buffer_limit_error_raises_daemon_error(self):
        """StreamReader buffer overflow re-raises as DaemonError.

        When readline() raises ValueError with 'chunk exceed the limit',
        the daemon IS running but the response was too large.  This must
        propagate as DaemonError so callers show "conductor error" instead
        of the misleading "conductor not running".
        """
        from mozart.daemon.exceptions import DaemonError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=ValueError(
                    "Separator is not found, and chunk exceed the limit"
                ),
            )

            with pytest.raises(DaemonError, match="Response too large"):
                await try_daemon_route("job.status", {})

    async def test_unexpected_exception_returns_false_none(self):
        """Arbitrary exceptions return (False, None) — safety guarantee."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=RuntimeError("totally unexpected")
            )

            routed, result = await try_daemon_route("job.submit", {})

        assert routed is False
        assert result is None

    async def test_connection_refused_returns_false_none(self):
        """ConnectionRefusedError (stale socket) returns (False, None)."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(
                side_effect=ConnectionRefusedError("refused")
            )

            routed, result = await try_daemon_route("job.list", {})

        assert routed is False
        assert result is None
