"""Tests for F-190: CLI commands must catch DaemonError to avoid raw tracebacks.

Commands that route through the conductor via try_daemon_route() must catch
DaemonError (including MethodNotFoundError) and display a user-friendly error
with restart guidance, rather than showing raw Python tracebacks.

Previously only run.py handled this correctly. diagnose.py (errors, diagnose,
history) and recover.py were missing the catch.

TDD: Tests define the contract. Implementation fulfills it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.daemon.exceptions import DaemonError, MethodNotFoundError

runner = CliRunner()


class TestDiagnoseErrorsDaemonErrorCatch:
    """errors command catches DaemonError and shows user-friendly message."""

    def test_method_not_found_on_errors(self) -> None:
        """MethodNotFoundError from try_daemon_route produces clean error,
        not a raw traceback."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=MethodNotFoundError(
                "Method 'job.errors' not found on conductor. "
                "Restart with: mzt restart"
            ),
        ):
            result = runner.invoke(app, ["errors", "test-job"])
        assert result.exit_code != 0
        # Should show the error message, not a traceback
        assert "Traceback" not in result.output
        assert "restart" in result.output.lower() or "Restart" in result.output

    def test_generic_daemon_error_on_errors(self) -> None:
        """Generic DaemonError produces clean error output."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=DaemonError("Connection lost to conductor"),
        ):
            result = runner.invoke(app, ["errors", "test-job"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output


class TestDiagnoseDaemonErrorCatch:
    """diagnose command catches DaemonError and shows user-friendly message."""

    def test_method_not_found_on_diagnose(self) -> None:
        """MethodNotFoundError from try_daemon_route produces clean error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=MethodNotFoundError(
                "Method 'job.diagnose' not found"
            ),
        ):
            result = runner.invoke(app, ["diagnose", "test-job"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        assert "restart" in result.output.lower() or "Restart" in result.output

    def test_generic_daemon_error_on_diagnose(self) -> None:
        """Generic DaemonError produces clean error output."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=DaemonError("Conductor internal error"),
        ):
            result = runner.invoke(app, ["diagnose", "test-job"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output


class TestHistoryDaemonErrorCatch:
    """history command catches DaemonError and shows user-friendly message."""

    def test_method_not_found_on_history(self) -> None:
        """MethodNotFoundError from try_daemon_route produces clean error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=MethodNotFoundError(
                "Method 'job.history' not found"
            ),
        ):
            result = runner.invoke(app, ["history", "test-job"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        assert "restart" in result.output.lower() or "Restart" in result.output


class TestRecoverDaemonErrorCatch:
    """recover command catches DaemonError and shows user-friendly message."""

    def test_method_not_found_on_recover(self) -> None:
        """MethodNotFoundError from try_daemon_route produces clean error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=MethodNotFoundError(
                "Method 'job.recover' not found"
            ),
        ):
            result = runner.invoke(app, ["recover", "test-job"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        assert "restart" in result.output.lower() or "Restart" in result.output

    def test_generic_daemon_error_on_recover(self) -> None:
        """Generic DaemonError produces clean error output."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=DaemonError("Conductor busy"),
        ):
            result = runner.invoke(app, ["recover", "test-job"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output
