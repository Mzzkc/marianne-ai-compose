"""TDD tests for stale state feedback (#139).

Three remaining issues from the stale state feedback work:
1. Stale PID detection in ``mzt start`` — clean up + notify user
2. ``--fresh`` early failure suppression — skip await_early_failure when --fresh
3. Contradictory error regression — rejection must NOT fall through to "not running"

Red first, then green.  Written by Dash, Movement 3.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click.exceptions
import pytest
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


# =============================================================================
# 1. Stale PID detection in ``mzt start``
# =============================================================================


class TestStalePidDetection:
    """When ``mzt start`` finds a PID file with a dead process,
    it should clean up the stale file and notify the user."""

    def test_stale_pid_shows_cleanup_message(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """User should see a message about stale PID cleanup."""
        from marianne.daemon.process import start_conductor

        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("88888")

        mock_config = MagicMock()
        mock_config.pid_file = pid_file
        mock_config.log_file = tmp_path / "marianne.log"

        with (
            patch(
                "marianne.daemon.process._load_config",
                return_value=mock_config,
            ),
            patch("marianne.daemon.process._read_pid", return_value=88888),
            patch("marianne.daemon.process._pid_alive", return_value=False),
            # After stale cleanup, PID file is deleted so lock check skipped.
            # Stop at configure_logging to prevent full startup.
            patch(
                "marianne.core.logging.configure_logging",
                side_effect=RuntimeError("test stop"),
            ),
        ):
            with pytest.raises(RuntimeError, match="test stop"):
                start_conductor(foreground=True)

        captured = capsys.readouterr()
        assert "stale" in captured.out.lower(), (
            f"Expected 'stale' in output, got: {captured.out!r}"
        )
        assert "88888" in captured.out, (
            f"Expected PID '88888' in output, got: {captured.out!r}"
        )

    def test_alive_pid_still_blocks_start(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When PID is alive, start should still exit with 'already running'."""
        from marianne.daemon.process import start_conductor

        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        mock_config = MagicMock()
        mock_config.pid_file = pid_file

        with (
            patch(
                "marianne.daemon.process._load_config",
                return_value=mock_config,
            ),
            patch("marianne.daemon.process._read_pid", return_value=12345),
            patch("marianne.daemon.process._pid_alive", return_value=True),
            pytest.raises(click.exceptions.Exit),
        ):
            start_conductor(foreground=True)

        captured = capsys.readouterr()
        assert "already running" in captured.out.lower(), (
            f"Expected 'already running' in output, got: {captured.out!r}"
        )

    def test_stale_pid_file_deleted(self, tmp_path: Path) -> None:
        """Stale PID file should be unlinked before starting."""
        from marianne.daemon.process import start_conductor

        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("77777")

        mock_config = MagicMock()
        mock_config.pid_file = pid_file
        mock_config.log_file = tmp_path / "marianne.log"

        with (
            patch(
                "marianne.daemon.process._load_config",
                return_value=mock_config,
            ),
            patch("marianne.daemon.process._read_pid", return_value=77777),
            patch("marianne.daemon.process._pid_alive", return_value=False),
            patch(
                "marianne.core.logging.configure_logging",
                side_effect=RuntimeError("test stop"),
            ),
        ):
            with pytest.raises(RuntimeError, match="test stop"):
                start_conductor(foreground=True)

        assert not pid_file.exists(), "Stale PID file should have been deleted"

    def test_no_pid_proceeds_normally(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When no PID file exists, start should proceed without messages."""
        from marianne.daemon.process import start_conductor

        mock_config = MagicMock()
        mock_config.pid_file = tmp_path / "marianne.pid"
        mock_config.log_file = tmp_path / "marianne.log"

        with (
            patch(
                "marianne.daemon.process._load_config",
                return_value=mock_config,
            ),
            patch("marianne.daemon.process._read_pid", return_value=None),
            patch(
                "marianne.core.logging.configure_logging",
                side_effect=RuntimeError("test stop"),
            ),
        ):
            with pytest.raises(RuntimeError, match="test stop"):
                start_conductor(foreground=True)

        captured = capsys.readouterr()
        assert "stale" not in captured.out.lower()
        assert "already running" not in captured.out.lower()


# =============================================================================
# 2. Fresh flag early failure suppression
# =============================================================================


class TestFreshEarlyFailureSuppression:
    """When --fresh is used, early failure polling should be skipped
    because transient state during cleanup can produce false reports."""

    def test_fresh_skips_early_failure_check(self, tmp_path: Path) -> None:
        """With --fresh, await_early_failure should not be called."""
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
                    {
                        "job_id": "test-job",
                        "status": "accepted",
                        "message": "",
                    },
                ),
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_early,
        ):
            result = runner.invoke(app, ["run", str(config_path), "--fresh"])

        mock_early.assert_not_called()
        assert result.exit_code == 0

    def test_fresh_shows_success_even_with_old_failure(
        self,
        tmp_path: Path,
    ) -> None:
        """With --fresh, old run's failure state should not cause CLI error."""
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
                    {
                        "job_id": "test-job",
                        "status": "accepted",
                        "message": "",
                    },
                ),
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value={
                    "status": "failed",
                    "error_message": (
                        "Parallel batch failed: Sheet 68 - Task cancelled"
                    ),
                },
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path), "--fresh"])

        # With --fresh, this should succeed
        assert result.exit_code == 0

    def test_without_fresh_early_failure_still_reported(
        self,
        tmp_path: Path,
    ) -> None:
        """Without --fresh, early failures should still be reported."""
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
                    {
                        "job_id": "test-job",
                        "status": "accepted",
                        "message": "",
                    },
                ),
            ),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value={
                    "status": "failed",
                    "error_message": "Template variable 'x' is undefined",
                },
            ) as mock_early,
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        mock_early.assert_called_once()
        assert result.exit_code != 0


# =============================================================================
# 3. Contradictory error regression test
# =============================================================================


class TestContradictoryErrorRegression:
    """When the conductor rejects a job (e.g. 'already running'),
    the CLI must NOT also report 'conductor is not running'."""

    def test_rejection_does_not_show_not_running(
        self,
        tmp_path: Path,
    ) -> None:
        """Conductor rejection should exit cleanly, not fall through."""
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
                    {
                        "job_id": "test-job",
                        "status": "rejected",
                        "message": "Job 'test-job' is already running.",
                    },
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output.lower()
        assert "already running" in output
        assert "conductor is not running" not in output

    def test_pressure_rejection_does_not_show_not_running(
        self,
        tmp_path: Path,
    ) -> None:
        """Backpressure rejection should not show 'conductor not running'."""
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
                    {
                        "job_id": "",
                        "status": "rejected",
                        "message": (
                            "System under high pressure — try again later"
                        ),
                    },
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output.lower()
        assert "conductor is not running" not in output
