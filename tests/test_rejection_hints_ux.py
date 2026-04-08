"""Tests for context-aware conductor rejection hints.

When the conductor rejects a job submission, the CLI should provide
specific, actionable hints based on the rejection reason — not generic
"try again later" for every rejection type.

TDD: Written by Lens, Movement 3.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


def _make_config(tmp_path: Path) -> Path:
    """Create a minimal valid config file for testing."""
    config = {
        "name": "test-job",
        "instrument": "claude-code",
        "sheet": {"size": 10, "total_items": 10},
        "prompt": {"template": "test"},
    }
    config_path = tmp_path / "test.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return config_path


def _mock_rejection(message: str, job_id: str = "") -> tuple[bool, dict[str, str]]:
    """Build a mock rejection response from the conductor."""
    return (True, {"job_id": job_id, "status": "rejected", "message": message})


# =============================================================================
# Context-aware hints for specific rejection types
# =============================================================================


class TestRejectionHintsShutdown:
    """When rejected because daemon is shutting down, hints should guide
    the user to wait or restart — not say 'try again later'."""

    def test_shutdown_hints_mention_restart(self, tmp_path: Path) -> None:
        """Shutdown rejection should suggest waiting or restarting."""
        config_path = _make_config(tmp_path)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=_mock_rejection("Daemon is shutting down"),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output.lower()
        # Must suggest restarting
        assert "mzt start" in output or "restart" in output


class TestRejectionHintsPressure:
    """Backpressure rejections should suggest checking system load."""

    def test_pressure_hints_mention_load_check(
        self, tmp_path: Path
    ) -> None:
        """High pressure rejection should suggest checking load."""
        config_path = _make_config(tmp_path)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=_mock_rejection(
                    "System under high pressure — try again later"
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output
        # Should suggest checking rate limits or conductor status
        assert "clear-rate-limits" in output or "conductor-status" in output


class TestRejectionHintsDuplicate:
    """When a job with the same name is already running, hints should
    suggest pause/cancel — not generic 'try again later'."""

    def test_duplicate_job_hints_mention_pause_or_cancel(
        self, tmp_path: Path
    ) -> None:
        """Duplicate job rejection should suggest pause or cancel."""
        config_path = _make_config(tmp_path)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=_mock_rejection(
                    "Job 'test-job' is already running. "
                    "Use 'mzt pause' or 'mzt cancel' first, "
                    "or wait for it to finish.",
                    job_id="test-job",
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output
        # Should suggest pause/cancel, not generic "try again"
        assert "pause" in output.lower() or "cancel" in output.lower()
        # Should NOT include generic "try again later"
        # (the conductor message already has specific guidance)


class TestRejectionHintsWorkspace:
    """Workspace-related rejections should suggest checking paths."""

    def test_workspace_parent_missing_hints_mention_path(
        self, tmp_path: Path
    ) -> None:
        """Workspace parent missing should suggest creating the directory."""
        config_path = _make_config(tmp_path)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=_mock_rejection(
                    "Workspace parent directory does not exist: /tmp/no-such-dir. "
                    "Create the parent directory or change the workspace path.",
                    job_id="test-job",
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output.lower()
        # Should suggest workspace-related action
        assert "workspace" in output or "--workspace" in output


class TestRejectionHintsConfigParse:
    """Config parse failures should suggest validation — not 'try again'."""

    def test_config_parse_failure_hints_mention_validate(
        self, tmp_path: Path
    ) -> None:
        """Config parse rejection should suggest mzt validate."""
        config_path = _make_config(tmp_path)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=_mock_rejection(
                    "Failed to parse config file: test.yaml "
                    "(invalid key 'foo'). "
                    "Cannot determine workspace. "
                    "Fix the config or pass --workspace explicitly.",
                    job_id="test-job",
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output.lower()
        # Should suggest validating the config
        assert "validate" in output


# =============================================================================
# Early failure error detail formatting
# =============================================================================


class TestEarlyFailureDisplay:
    """When a job fails immediately after submission, the error detail
    should be included in the structured output — not as a raw print."""

    def test_early_failure_shows_error_detail_in_hints(
        self, tmp_path: Path
    ) -> None:
        """Error detail from early failure should appear as a hint."""
        config_path = _make_config(tmp_path)

        # Mock: daemon accepts, then early poll shows failure
        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(
                    True,
                    {"job_id": "test-job", "status": "accepted", "message": ""},
                ),
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value={
                    "status": "failed",
                    "error_message": "Template variable 'missing_var' is undefined",
                },
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output
        # The error detail must appear in the output
        assert "missing_var" in output or "undefined" in output
        # Should suggest diagnose
        assert "diagnose" in output.lower()

    def test_early_failure_no_error_detail_still_shows_diagnose(
        self, tmp_path: Path
    ) -> None:
        """Even without error detail, early failure suggests diagnose."""
        config_path = _make_config(tmp_path)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(
                    True,
                    {"job_id": "test-job", "status": "accepted", "message": ""},
                ),
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value={"status": "failed", "error_message": ""},
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        assert "diagnose" in result.output.lower()
