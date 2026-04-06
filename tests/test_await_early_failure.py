"""Tests for the await_early_failure() CLI helper.

Validates that the CLI detects early job failures after daemon submission
by polling job.status, and fails open on connection errors.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client_mock(
    responses: list[dict[str, Any]],
) -> MagicMock:
    """Build a DaemonClient mock whose .call() returns responses in order."""
    client = MagicMock()
    call_mock = AsyncMock(side_effect=responses)
    client.call = call_mock
    return client


# ---------------------------------------------------------------------------
# Unit tests for await_early_failure()
# ---------------------------------------------------------------------------

class TestAwaitEarlyFailure:
    """Unit tests for await_early_failure() polling logic."""

    @pytest.mark.asyncio
    async def test_detects_immediate_failure(self) -> None:
        """Status returns 'failed' on first poll -> returns failure dict."""
        failure = {"status": "failed", "error_message": "UndefinedError: 'foo'"}
        client = _make_client_mock([failure])

        with (
            patch("marianne.daemon.detect._resolve_socket_path", return_value="/tmp/test.sock"),
            patch("marianne.daemon.ipc.client.DaemonClient", return_value=client),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=1.0, poll_interval=0.05)

        assert result is not None
        assert result["status"] == "failed"
        assert "UndefinedError" in result["error_message"]

    @pytest.mark.asyncio
    async def test_returns_none_when_running(self) -> None:
        """Status stays 'running' -> returns None after timeout."""
        running = {"status": "running"}
        # Enough responses to outlast the timeout
        client = _make_client_mock([running] * 50)

        with (
            patch("marianne.daemon.detect._resolve_socket_path", return_value="/tmp/test.sock"),
            patch("marianne.daemon.ipc.client.DaemonClient", return_value=client),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=0.2, poll_interval=0.05)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_queued(self) -> None:
        """Status stays 'queued' -> returns None after timeout."""
        queued = {"status": "queued"}
        client = _make_client_mock([queued] * 50)

        with (
            patch("marianne.daemon.detect._resolve_socket_path", return_value="/tmp/test.sock"),
            patch("marianne.daemon.ipc.client.DaemonClient", return_value=client),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=0.2, poll_interval=0.05)

        assert result is None

    @pytest.mark.asyncio
    async def test_fails_open_on_connection_error(self) -> None:
        """DaemonClient.call raises -> returns None (fail open)."""
        client = MagicMock()
        client.call = AsyncMock(side_effect=ConnectionError("refused"))

        with (
            patch("marianne.daemon.detect._resolve_socket_path", return_value="/tmp/test.sock"),
            patch("marianne.daemon.ipc.client.DaemonClient", return_value=client),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=0.5, poll_interval=0.05)

        assert result is None

    @pytest.mark.asyncio
    async def test_fails_open_on_import_error(self) -> None:
        """ImportError during lazy import -> returns None (fail open)."""
        with patch(
            "marianne.daemon.detect._resolve_socket_path",
            side_effect=ImportError("no module"),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=0.2, poll_interval=0.05)

        assert result is None

    @pytest.mark.asyncio
    async def test_detects_failure_on_second_poll(self) -> None:
        """First 'running', second 'failed' -> returns failure."""
        responses = [
            {"status": "running"},
            {"status": "failed", "error_message": "template error"},
        ]
        client = _make_client_mock(responses)

        with (
            patch("marianne.daemon.detect._resolve_socket_path", return_value="/tmp/test.sock"),
            patch("marianne.daemon.ipc.client.DaemonClient", return_value=client),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=2.0, poll_interval=0.05)

        assert result is not None
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_detects_cancelled(self) -> None:
        """Status 'cancelled' is also a terminal state."""
        cancelled = {"status": "cancelled"}
        client = _make_client_mock([cancelled])

        with (
            patch("marianne.daemon.detect._resolve_socket_path", return_value="/tmp/test.sock"),
            patch("marianne.daemon.ipc.client.DaemonClient", return_value=client),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=1.0, poll_interval=0.05)

        assert result is not None
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_detects_early_completion(self) -> None:
        """Status 'completed' is returned (rare but valid)."""
        completed = {"status": "completed"}
        client = _make_client_mock([completed])

        with (
            patch("marianne.daemon.detect._resolve_socket_path", return_value="/tmp/test.sock"),
            patch("marianne.daemon.ipc.client.DaemonClient", return_value=client),
        ):
            from marianne.cli.helpers import await_early_failure
            result = await await_early_failure("test-job", timeout=1.0, poll_interval=0.05)

        assert result is not None
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# Integration-style tests for CLI run command
# ---------------------------------------------------------------------------

class TestRunCommandEarlyFailure:
    """Test that the run command surfaces early failures to the user."""

    @pytest.mark.asyncio
    async def test_cli_run_shows_error_on_early_failure(self) -> None:
        """submit=accepted + status=failed -> _try_daemon_submit raises Exit(1)."""
        import typer

        from marianne.cli.commands.run import _try_daemon_submit

        submit_result = {
            "status": "accepted",
            "job_id": "broken-job",
            "message": "Job queued",
        }
        failure_result = {
            "status": "failed",
            "error_message": "UndefinedError: 'missing_var'",
        }

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, submit_result),
            ),
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value=failure_result,
            ),
        ):
            from pathlib import Path

            with pytest.raises(typer.Exit) as exc_info:
                await _try_daemon_submit(
                    config_file=Path("fake.yaml"),
                    workspace=None,
                    fresh=False,
                    self_healing=False,
                    auto_confirm=False,
                    json_output=False,
                )
            assert exc_info.value.exit_code == 1

    @pytest.mark.asyncio
    async def test_cli_run_succeeds_when_no_early_failure(self) -> None:
        """submit=accepted + polling returns None -> normal success."""
        from marianne.cli.commands.run import _try_daemon_submit

        submit_result = {
            "status": "accepted",
            "job_id": "good-job",
            "message": "Job queued",
        }

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, submit_result),
            ),
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            from pathlib import Path

            result = await _try_daemon_submit(
                config_file=Path("fake.yaml"),
                workspace=None,
                fresh=False,
                self_healing=False,
                auto_confirm=False,
                json_output=False,
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_cli_run_json_mode_early_failure(self) -> None:
        """JSON mode: early failure prints failure dict and exits 1."""
        import typer

        from marianne.cli.commands.run import _try_daemon_submit

        submit_result = {
            "status": "accepted",
            "job_id": "broken-job",
            "message": "Job queued",
        }
        failure_result = {
            "status": "failed",
            "error_message": "Template syntax error",
        }

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, submit_result),
            ),
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value=failure_result,
            ),
        ):
            from pathlib import Path

            with pytest.raises(typer.Exit) as exc_info:
                await _try_daemon_submit(
                    config_file=Path("fake.yaml"),
                    workspace=None,
                    fresh=False,
                    self_healing=False,
                    auto_confirm=False,
                    json_output=True,
                )
            assert exc_info.value.exit_code == 1
