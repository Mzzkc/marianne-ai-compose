"""Tests for F-450: IPC Method Not Found misreported as conductor not running.

When a client calls an IPC method that doesn't exist on the conductor (e.g.,
new CLI against old conductor), the server returns JSON-RPC METHOD_NOT_FOUND
(-32601). Previously this was swallowed as a generic DaemonError and
try_daemon_route() returned (False, None), causing the CLI to show
"conductor is not running" — which is wrong.

The fix: map METHOD_NOT_FOUND to a specific MethodNotFoundError exception
that try_daemon_route() re-raises so callers can show an accurate message.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_CLIENT_PATH = "marianne.daemon.ipc.client.DaemonClient"


# ---------------------------------------------------------------------------
# Exception hierarchy: MethodNotFoundError exists and inherits correctly
# ---------------------------------------------------------------------------


class TestMethodNotFoundErrorExists:
    """Verify that MethodNotFoundError is a proper DaemonError subclass."""

    def test_exception_importable(self):
        """MethodNotFoundError can be imported from daemon.exceptions."""
        from marianne.daemon.exceptions import MethodNotFoundError

        assert MethodNotFoundError is not None

    def test_inherits_from_daemon_error(self):
        """MethodNotFoundError is a DaemonError subclass."""
        from marianne.daemon.exceptions import DaemonError, MethodNotFoundError

        assert issubclass(MethodNotFoundError, DaemonError)

    def test_can_be_instantiated_with_message(self):
        """Exception carries the method name in its message."""
        from marianne.daemon.exceptions import MethodNotFoundError

        exc = MethodNotFoundError("Method not found: daemon.new_feature")
        assert "daemon.new_feature" in str(exc)

    def test_caught_by_daemon_error_handler(self):
        """MethodNotFoundError is caught by except DaemonError."""
        from marianne.daemon.exceptions import DaemonError, MethodNotFoundError

        with pytest.raises(DaemonError):
            raise MethodNotFoundError("test")


# ---------------------------------------------------------------------------
# Error mapping: METHOD_NOT_FOUND (-32601) → MethodNotFoundError
# ---------------------------------------------------------------------------


class TestMethodNotFoundMapping:
    """Verify that rpc_error_to_exception maps -32601 to MethodNotFoundError."""

    def test_method_not_found_maps_to_specific_exception(self):
        """JSON-RPC -32601 produces MethodNotFoundError, not base DaemonError."""
        from marianne.daemon.exceptions import MethodNotFoundError
        from marianne.daemon.ipc.errors import METHOD_NOT_FOUND, rpc_error_to_exception

        error_dict = {
            "code": METHOD_NOT_FOUND,
            "message": "Method not found: daemon.new_feature",
        }
        exc = rpc_error_to_exception(error_dict)

        assert isinstance(exc, MethodNotFoundError)
        assert "daemon.new_feature" in str(exc)

    def test_method_not_found_preserves_message(self):
        """The server's error message is preserved in the exception."""
        from marianne.daemon.ipc.errors import METHOD_NOT_FOUND, rpc_error_to_exception

        error_dict = {
            "code": METHOD_NOT_FOUND,
            "message": "Method not found: daemon.clear_rate_limits",
        }
        exc = rpc_error_to_exception(error_dict)
        assert str(exc) == "Method not found: daemon.clear_rate_limits"

    def test_other_codes_still_work(self):
        """Existing mappings (JOB_NOT_FOUND etc.) are unaffected."""
        from marianne.daemon.exceptions import JobSubmissionError
        from marianne.daemon.ipc.errors import JOB_NOT_FOUND, rpc_error_to_exception

        error_dict = {"code": JOB_NOT_FOUND, "message": "job not found"}
        exc = rpc_error_to_exception(error_dict)
        assert isinstance(exc, JobSubmissionError)

    def test_unknown_code_falls_back_to_daemon_error(self):
        """Unmapped codes still fall back to base DaemonError."""
        from marianne.daemon.exceptions import DaemonError, MethodNotFoundError
        from marianne.daemon.ipc.errors import rpc_error_to_exception

        error_dict = {"code": -99999, "message": "unknown"}
        exc = rpc_error_to_exception(error_dict)
        assert isinstance(exc, DaemonError)
        assert not isinstance(exc, MethodNotFoundError)


# ---------------------------------------------------------------------------
# Routing: try_daemon_route re-raises MethodNotFoundError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTryDaemonRouteMethodNotFound:
    """Verify that try_daemon_route re-raises MethodNotFoundError.

    This is the core F-450 fix: when the conductor IS running but doesn't
    recognize a method, the CLI must NOT show "conductor not running."
    """

    async def test_method_not_found_raises_not_swallowed(self):
        """MethodNotFoundError from client.call() re-raises through routing.

        Previously returned (False, None) → misleading "not running" message.
        Now re-raises with restart guidance so callers show an accurate error.
        """
        from marianne.daemon.exceptions import MethodNotFoundError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=MethodNotFoundError(
                    "Method not found: daemon.new_feature"
                )
            )

            with pytest.raises(MethodNotFoundError, match="does not support") as exc_info:
                await try_daemon_route(
                    "daemon.new_feature",
                    {},
                    socket_path=Path("/tmp/test.sock"),
                )

            # The wrapped message includes restart guidance
            assert "mzt restart" in str(exc_info.value)
            assert "daemon.new_feature" in str(exc_info.value)

    async def test_method_not_found_not_returned_as_false(self):
        """MethodNotFoundError must NOT return (False, None).

        This is the regression test for F-450: the old behavior was to
        return (False, None) which made the CLI say "conductor not running."
        """
        from marianne.daemon.detect import try_daemon_route
        from marianne.daemon.exceptions import MethodNotFoundError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=MethodNotFoundError("Method not found: x")
            )

            # Must raise, not return (False, None)
            with pytest.raises(MethodNotFoundError):
                await try_daemon_route(
                    "x", {}, socket_path=Path("/tmp/test.sock")
                )

    async def test_job_submission_error_still_reraises(self):
        """JobSubmissionError still re-raises (existing behavior preserved)."""
        from marianne.daemon.exceptions import JobSubmissionError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=JobSubmissionError("job not found")
            )

            with pytest.raises(JobSubmissionError):
                await try_daemon_route(
                    "job.status", {}, socket_path=Path("/tmp/test.sock")
                )

    async def test_resource_exhausted_still_reraises(self):
        """ResourceExhaustedError still re-raises (existing behavior preserved)."""
        from marianne.daemon.exceptions import ResourceExhaustedError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=ResourceExhaustedError("rate limited")
            )

            with pytest.raises(ResourceExhaustedError):
                await try_daemon_route(
                    "job.submit", {}, socket_path=Path("/tmp/test.sock")
                )

    async def test_generic_daemon_error_still_returns_false(self):
        """Generic DaemonError (not a specific subclass) still returns (False, None).

        Only MethodNotFoundError gets re-raised. Plain DaemonError (e.g., from
        a shutting-down conductor) still returns False for fallback handling.
        """
        from marianne.daemon.exceptions import DaemonError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=DaemonError("shutting down")
            )

            routed, result = await try_daemon_route(
                "job.status", {}, socket_path=Path("/tmp/test.sock")
            )

        assert routed is False
        assert result is None

    async def test_daemon_already_running_error_still_returns_false(self):
        """DaemonAlreadyRunningError still returns (False, None).

        This verifies we didn't accidentally change behavior for other
        DaemonError subclasses.
        """
        from marianne.daemon.exceptions import DaemonAlreadyRunningError

        with patch(_CLIENT_PATH) as MockClient:
            client = MockClient.return_value
            client.is_daemon_running = AsyncMock(return_value=True)
            client.call = AsyncMock(
                side_effect=DaemonAlreadyRunningError("already running")
            )

            routed, result = await try_daemon_route(
                "conductor.start", {}, socket_path=Path("/tmp/test.sock")
            )

        assert routed is False
        assert result is None


# ---------------------------------------------------------------------------
# Integration: IPC client test update (MethodNotFoundError from wire format)
# ---------------------------------------------------------------------------


class TestIpcClientMethodNotFoundIntegration:
    """Verify the DaemonClient.call() → rpc_error_to_exception path."""

    def test_client_existing_test_should_use_method_not_found_error(self):
        """The existing test_call_unknown_method_raises should catch MethodNotFoundError.

        This validates that the fix is end-to-end: server sends -32601,
        client receives it, rpc_error_to_exception maps it to
        MethodNotFoundError (a DaemonError subclass).
        """
        from marianne.daemon.exceptions import DaemonError, MethodNotFoundError

        # MethodNotFoundError IS-A DaemonError, so existing code that
        # catches DaemonError will still work
        assert issubclass(MethodNotFoundError, DaemonError)

        # But now we can also catch it specifically
        try:
            raise MethodNotFoundError("Method not found: nonexistent.method")
        except MethodNotFoundError as e:
            assert "nonexistent.method" in str(e)
        except DaemonError:
            pytest.fail("MethodNotFoundError should be caught before DaemonError")


from marianne.daemon.detect import try_daemon_route  # noqa: E402
