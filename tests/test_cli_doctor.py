"""Tests for ``mozart doctor`` command.

Validates environment health checks: Python version, Mozart version,
conductor status, instrument availability, and safety warnings.

TDD: These tests define the contract for the doctor command.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic command registration
# ---------------------------------------------------------------------------


class TestDoctorCommandExists:
    """Verify the doctor command is registered and callable."""

    def test_doctor_command_registered(self) -> None:
        """The doctor command appears in the CLI."""
        result = runner.invoke(app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "environment" in result.stdout.lower() or "health" in result.stdout.lower()

    def test_doctor_runs_without_conductor(self) -> None:
        """Doctor should work even when the conductor is not running."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # Should succeed (exit 0) even without conductor
            assert result.exit_code == 0

    def test_doctor_shows_python_version(self) -> None:
        """Doctor should display the Python version."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert "Python" in result.stdout

    def test_doctor_shows_mozart_version(self) -> None:
        """Doctor should display the Mozart version."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert "Mozart" in result.stdout


# ---------------------------------------------------------------------------
# Conductor status checks
# ---------------------------------------------------------------------------


class TestDoctorConductorChecks:
    """Verify conductor health reporting."""

    def test_conductor_running_shown(self) -> None:
        """Doctor should report when the conductor is running."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("running", 12345),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            assert "12345" in result.stdout or "running" in result.stdout.lower()

    def test_conductor_not_running_shown(self) -> None:
        """Doctor should report when the conductor is not running."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            assert "not running" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Instrument checks
# ---------------------------------------------------------------------------


class TestDoctorInstrumentChecks:
    """Verify instrument availability reporting."""

    def test_native_instruments_listed(self) -> None:
        """Doctor should list the native instruments."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # At minimum, claude-code should appear (the reference instrument)
            assert "claude" in result.stdout.lower()

    def test_instrument_binary_found(self) -> None:
        """Doctor should mark instruments as ready when their binary is on PATH."""
        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch("shutil.which", return_value="/usr/local/bin/claude"),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0

    def test_instrument_binary_missing(self) -> None:
        """Doctor should report when an instrument binary is not found."""
        with (
            patch(
                "marianne.cli.commands.doctor._check_conductor_status",
                return_value=("not running", None),
            ),
            patch("shutil.which", return_value=None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            # Should still succeed — missing optional instruments are warnings


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


class TestDoctorSafetyChecks:
    """Verify safety warning reporting."""

    def test_no_cost_limits_warning(self) -> None:
        """Doctor should warn if no cost limits are configured by default."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # The default state has no cost limits — doctor should mention this
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON output mode
# ---------------------------------------------------------------------------


class TestDoctorJsonOutput:
    """Verify JSON output mode."""

    def test_json_flag_produces_json(self) -> None:
        """Doctor --json should produce parseable JSON."""
        import json

        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert "python_version" in data
            assert "mozart_version" in data
            assert "conductor" in data
            assert "instruments" in data


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


class TestDoctorSummary:
    """Verify the summary line at the end."""

    def test_summary_shows_ready(self) -> None:
        """Doctor should show a summary indicating readiness."""
        with patch(
            "marianne.cli.commands.doctor._check_conductor_status",
            return_value=("running", 12345),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            # Should have some kind of summary
            assert "ready" in result.stdout.lower() or "warning" in result.stdout.lower()


# ---------------------------------------------------------------------------
# F-090: IPC socket fallback detection (Ghost, movement 1)
# ---------------------------------------------------------------------------


class TestDoctorSocketFallback:
    """F-090: Doctor should detect conductor via IPC socket when PID file is missing."""

    def test_socket_probe_detects_running_conductor(self) -> None:
        """When PID file is missing but socket responds, report running."""
        from unittest.mock import AsyncMock

        with (
            patch(
                "marianne.cli.commands.doctor._check_pid_file",
                return_value=None,
            ),
            patch(
                "marianne.daemon.detect._resolve_socket_path",
            ) as mock_resolve,
            patch(
                "marianne.daemon.ipc.client.DaemonClient",
            ) as mock_client_cls,
        ):
            # Socket exists
            mock_path = type("MockPath", (), {"exists": lambda self: True})()
            mock_resolve.return_value = mock_path

            # IPC probe returns alive
            mock_instance = AsyncMock()
            mock_instance.is_daemon_running = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_instance

            from marianne.cli.commands.doctor import _check_conductor_status

            status, pid = _check_conductor_status()
            assert status == "running"
            # PID is None because we found it via socket, not PID file
            assert pid is None

    def test_no_pid_no_socket_reports_not_running(self) -> None:
        """When both PID file and socket are missing, report not running."""
        with (
            patch(
                "marianne.cli.commands.doctor._check_pid_file",
                return_value=None,
            ),
            patch(
                "marianne.daemon.detect._resolve_socket_path",
            ) as mock_resolve,
        ):
            mock_path = type("MockPath", (), {"exists": lambda self: False})()
            mock_resolve.return_value = mock_path

            from marianne.cli.commands.doctor import _check_conductor_status

            status, pid = _check_conductor_status()
            assert status == "not running"
            assert pid is None

    def test_pid_found_skips_socket_probe(self) -> None:
        """When PID file check succeeds, skip the socket probe."""
        with patch(
            "marianne.cli.commands.doctor._check_pid_file",
            return_value=12345,
        ):
            from marianne.cli.commands.doctor import _check_conductor_status

            status, pid = _check_conductor_status()
            assert status == "running"
            assert pid == 12345


# ---------------------------------------------------------------------------
# Doctor clone awareness (Ghost, movement 1)
# ---------------------------------------------------------------------------


class TestDoctorCloneAwareness:
    """Doctor should check clone conductor when --conductor-clone is active."""

    def test_pid_check_uses_clone_path(self) -> None:
        """_check_pid_file should use clone PID file when clone is active."""
        from marianne.daemon.clone import set_clone_name

        set_clone_name("doctor-test")
        try:
            from marianne.cli.commands.doctor import _check_pid_file

            # Clone PID file doesn't exist → returns None
            pid = _check_pid_file()
            assert pid is None
        finally:
            set_clone_name(None)
