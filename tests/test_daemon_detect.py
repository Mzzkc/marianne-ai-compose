"""Tests for daemon detection and CLI routing.

Availability probes return False on error; routing preserves failures after
a healthy probe so an uncertain operation cannot fall back and run again.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marianne.daemon.detect import (
    _resolve_socket_path,
    is_daemon_available,
    try_daemon_route,
)
from marianne.daemon.exceptions import DaemonError, DaemonProtocolError

# =============================================================================
# _resolve_socket_path
# =============================================================================


class TestResolveSocketPath:
    """Tests for socket path resolution."""

    def test_returns_explicit_path(self):
        """Explicit path is used when provided."""
        p = Path("/custom/socket.sock")
        assert _resolve_socket_path(p) == p

    def test_falls_back_to_socket_config_default(self, tmp_path, monkeypatch):
        """None triggers fallback to SocketConfig().path."""
        from marianne.daemon import detect

        # Isolate from the host: a real legacy /tmp socket must not leak in.
        monkeypatch.setattr(detect, "LEGACY_SOCKET_PATH", tmp_path / "none.sock")
        result = _resolve_socket_path(None)
        assert result == Path.home() / ".config" / "mzt" / "mzt.sock"


# =============================================================================
# is_daemon_available
# =============================================================================


# The DaemonClient is imported INSIDE the function body via
# `from marianne.daemon.ipc.client import DaemonClient`, so we patch it
# at the source module.
_CLIENT_PATH = "marianne.daemon.ipc.client.DaemonClient"


@pytest.mark.asyncio
class TestIsDaemonAvailable:
    """Tests for daemon availability detection."""

    async def test_returns_true_when_daemon_running(self):
        """Happy path: daemon responds, returns True."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is True

    async def test_returns_false_when_daemon_not_running(self):
        """Daemon client responds with not running."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=False)

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is False

    async def test_oserror_returns_false(self):
        """OSError (e.g. socket not found) returns False."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(side_effect=OSError("No such file"))

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is False

    async def test_connection_error_returns_false(self):
        """ConnectionError (socket exists but daemon dead) returns False."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(side_effect=ConnectionRefusedError("refused"))

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
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(side_effect=RuntimeError("unexpected crash"))

            result = await is_daemon_available(Path("/tmp/test.sock"))

        assert result is False

    async def test_none_socket_uses_default(self, tmp_path, monkeypatch):
        """None socket_path triggers SocketConfig fallback."""
        from marianne.daemon import detect

        # Isolate from the host: a real legacy /tmp socket must not leak in.
        monkeypatch.setattr(detect, "LEGACY_SOCKET_PATH", tmp_path / "none.sock")
        with (
            patch(
                "marianne.daemon.clone.get_clone_name",
                return_value=None,
            ),
            patch(_CLIENT_PATH) as MockClient,
        ):
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)

            result = await is_daemon_available(None)

        assert result is True
        # Client was created with the default path (no clone active)
        MockClient.assert_called_once_with(Path.home() / ".config" / "mzt" / "mzt.sock")


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
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(return_value={"status": "ok"})

            routed, result = await try_daemon_route(
                "job.submit",
                {"config": "test.yaml"},
                socket_path=Path("/tmp/test.sock"),
            )

        assert routed is True
        assert result == {"status": "ok"}
        client.call.assert_called_once_with("job.submit", {"config": "test.yaml"})

    async def test_returns_false_when_daemon_not_running(self):
        """When daemon not running, returns (False, None)."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=False)

            routed, result = await try_daemon_route("job.submit", {})

        assert routed is False
        assert result is None
        client.call.assert_not_called()

    async def test_oserror_after_healthy_probe_raises(self):
        """A lost response means unavailable data, not conductor absence."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(side_effect=OSError("broken pipe"))

            with pytest.raises(DaemonError, match="connection failed"):
                await try_daemon_route("job.status", {})
        client.close.assert_awaited_once()

    async def test_timeout_after_confirmed_running_raises_daemon_error(self):
        """TimeoutError after daemon confirmed running raises DaemonError.

        When is_daemon_running() returns True but call() times out, the
        daemon IS running but slow.  This must raise DaemonError so
        callers show "conductor busy" instead of "conductor not running".
        """
        from marianne.daemon.exceptions import DaemonError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(side_effect=TimeoutError("timed out"))
            client._timeout = 30.0

            with pytest.raises(DaemonError, match="did not respond"):
                await try_daemon_route("job.status", {})

    async def test_value_error_preserves_protocol_failure(self):
        """A malformed response cannot claim the healthy conductor is absent."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(side_effect=ValueError("invalid params"))

            with pytest.raises(DaemonProtocolError, match="invalid params"):
                await try_daemon_route("job.status", {})
        client.close.assert_awaited_once()

    async def test_buffer_limit_error_raises_daemon_error(self):
        """StreamReader buffer overflow re-raises as DaemonError.

        When readline() raises ValueError with 'chunk exceed the limit',
        the daemon IS running but the response was too large.  This must
        propagate as DaemonError so callers show "conductor error" instead
        of the misleading "conductor not running".
        """
        from marianne.daemon.exceptions import DaemonError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=ValueError("Separator is not found, and chunk exceed the limit"),
            )

            with pytest.raises(DaemonError, match="Response too large"):
                await try_daemon_route("job.status", {})

    async def test_unexpected_confirmed_failure_raises(self):
        """A failure after the probe remains an error and forbids fallback."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(side_effect=RuntimeError("totally unexpected"))

            with pytest.raises(DaemonError, match="totally unexpected"):
                await try_daemon_route("job.submit", {})
        client.close.assert_awaited_once()

    async def test_connection_refused_returns_false_none(self):
        """ConnectionRefusedError (stale socket) returns (False, None)."""
        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.close = AsyncMock()
            client.is_daemon_running = AsyncMock(side_effect=ConnectionRefusedError("refused"))

            routed, result = await try_daemon_route("job.list", {})

        assert routed is False
        assert result is None
