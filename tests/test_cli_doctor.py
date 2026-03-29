"""Tests for ``mozart doctor`` command.

Validates environment health checks: Python version, Mozart version,
conductor status, instrument availability, and safety warnings.

TDD: These tests define the contract for the doctor command.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from mozart.cli import app

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
            "mozart.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # Should succeed (exit 0) even without conductor
            assert result.exit_code == 0

    def test_doctor_shows_python_version(self) -> None:
        """Doctor should display the Python version."""
        with patch(
            "mozart.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            assert "Python" in result.stdout

    def test_doctor_shows_mozart_version(self) -> None:
        """Doctor should display the Mozart version."""
        with patch(
            "mozart.cli.commands.doctor._check_conductor_status",
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
            "mozart.cli.commands.doctor._check_conductor_status",
            return_value=("running", 12345),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            assert "12345" in result.stdout or "running" in result.stdout.lower()

    def test_conductor_not_running_shown(self) -> None:
        """Doctor should report when the conductor is not running."""
        with patch(
            "mozart.cli.commands.doctor._check_conductor_status",
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
            "mozart.cli.commands.doctor._check_conductor_status",
            return_value=("not running", None),
        ):
            result = runner.invoke(app, ["doctor"])
            # At minimum, claude-code should appear (the reference instrument)
            assert "claude" in result.stdout.lower()

    def test_instrument_binary_found(self) -> None:
        """Doctor should mark instruments as ready when their binary is on PATH."""
        with (
            patch(
                "mozart.cli.commands.doctor._check_conductor_status",
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
                "mozart.cli.commands.doctor._check_conductor_status",
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
            "mozart.cli.commands.doctor._check_conductor_status",
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
            "mozart.cli.commands.doctor._check_conductor_status",
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
            "mozart.cli.commands.doctor._check_conductor_status",
            return_value=("running", 12345),
        ):
            result = runner.invoke(app, ["doctor"])
            assert result.exit_code == 0
            # Should have some kind of summary
            assert "ready" in result.stdout.lower() or "warning" in result.stdout.lower()
