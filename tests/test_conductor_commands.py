"""Tests for Mozart conductor commands (mozart start/stop/restart/conductor-status).

Tests verify that the conductor commands correctly delegate to the
core functions in ``mozart.daemon.process`` and handle edge cases
like already-running conductors and missing PID files.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mozart.cli import app

runner = CliRunner()


# ─── Start Command ────────────────────────────────────────────────────


class TestStartCommand:
    """Tests for ``mozart start``."""

    def test_start_help(self):
        """``mozart start --help`` shows conductor start help."""
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0
        assert "Start the Mozart conductor" in result.output

    def test_start_already_running_exits_1(self, tmp_path: Path):
        """start exits with code 1 if conductor already running."""
        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text(str(os.getpid()))

        with patch("mozart.daemon.process._load_config") as mock_config:
            cfg = MagicMock()
            cfg.pid_file = pid_file
            cfg.log_level = "info"
            mock_config.return_value = cfg

            result = runner.invoke(app, ["start", "--foreground"])

        assert result.exit_code == 1
        assert "already running" in result.output

    def test_start_foreground_skips_daemonize(self, tmp_path: Path):
        """In foreground mode, _daemonize() is NOT called."""
        pid_file = tmp_path / "mozart.pid"

        with (
            patch("mozart.daemon.process._load_config") as mock_config,
            patch("mozart.daemon.process._daemonize") as mock_daemonize,
            patch("mozart.daemon.process._read_pid", return_value=None),
            patch("mozart.core.logging.configure_logging"),
            patch("mozart.daemon.process.DaemonProcess"),
            patch("mozart.daemon.process.asyncio.run"),
        ):
            cfg = MagicMock()
            cfg.pid_file = pid_file
            cfg.log_level = "info"
            cfg.log_file = None
            mock_config.return_value = cfg

            result = runner.invoke(app, ["start", "--foreground"])

        assert result.exit_code == 0
        mock_daemonize.assert_not_called()

    def test_start_background_calls_daemonize(self, tmp_path: Path):
        """Without --foreground, _daemonize() is called."""
        pid_file = tmp_path / "mozart.pid"

        with (
            patch("mozart.daemon.process._load_config") as mock_config,
            patch("mozart.daemon.process._daemonize") as mock_daemonize,
            patch("mozart.daemon.process._read_pid", return_value=None),
            patch("mozart.core.logging.configure_logging"),
            patch("mozart.daemon.process.DaemonProcess"),
            patch("mozart.daemon.process.asyncio.run"),
        ):
            cfg = MagicMock()
            cfg.pid_file = pid_file
            cfg.log_level = "info"
            cfg.log_file = None
            mock_config.return_value = cfg

            result = runner.invoke(app, ["start"])

        assert result.exit_code == 0
        mock_daemonize.assert_called_once()


# ─── Stop Command ─────────────────────────────────────────────────────


class TestStopCommand:
    """Tests for ``mozart stop``."""

    def test_stop_help(self):
        """``mozart stop --help`` shows conductor stop help."""
        result = runner.invoke(app, ["stop", "--help"])
        assert result.exit_code == 0
        assert "Stop the Mozart conductor" in result.output

    def test_stop_not_running_exits_1(self, tmp_path: Path):
        """stop exits with code 1 when conductor is not running."""
        pid_file = tmp_path / "mozart.pid"

        result = runner.invoke(app, ["stop", "--pid-file", str(pid_file)])

        assert result.exit_code == 1
        assert "not running" in result.output

    def test_stop_sends_sigterm(self, tmp_path: Path):
        """stop sends SIGTERM to the PID by default."""
        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("12345")

        import signal

        with (
            patch("mozart.daemon.process._pid_alive", return_value=True),
            patch("mozart.daemon.process.os.kill") as mock_kill,
        ):
            result = runner.invoke(app, ["stop", "--pid-file", str(pid_file)])

        assert result.exit_code == 0
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
        assert "SIGTERM" in result.output

    def test_stop_force_sends_sigkill(self, tmp_path: Path):
        """stop --force sends SIGKILL."""
        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("12345")

        import signal

        with (
            patch("mozart.daemon.process._pid_alive", return_value=True),
            patch("mozart.daemon.process.os.kill") as mock_kill,
        ):
            result = runner.invoke(app, ["stop", "--pid-file", str(pid_file), "--force"])

        assert result.exit_code == 0
        mock_kill.assert_called_once_with(12345, signal.SIGKILL)
        assert "SIGKILL" in result.output


# ─── Restart Command ──────────────────────────────────────────────────


class TestRestartCommand:
    """Tests for ``mozart restart``."""

    def test_restart_help(self):
        """``mozart restart --help`` shows conductor restart help."""
        result = runner.invoke(app, ["restart", "--help"])
        assert result.exit_code == 0
        assert "Restart the Mozart conductor" in result.output

    def test_restart_calls_stop_then_start(self, tmp_path: Path):
        """restart calls stop_conductor, waits, then start_conductor."""
        with (
            patch("mozart.daemon.process.stop_conductor") as mock_stop,
            patch("mozart.daemon.process.wait_for_conductor_exit", return_value=True),
            patch("mozart.daemon.process.start_conductor") as mock_start,
        ):
            result = runner.invoke(app, ["restart", "--foreground"])

        assert result.exit_code == 0
        mock_stop.assert_called_once()
        mock_start.assert_called_once()

    def test_restart_continues_if_stop_fails(self, tmp_path: Path):
        """restart continues with start even if stop raises SystemExit."""
        with (
            patch(
                "mozart.daemon.process.stop_conductor",
                side_effect=SystemExit(1),
            ),
            patch("mozart.daemon.process.wait_for_conductor_exit", return_value=True),
            patch("mozart.daemon.process.start_conductor") as mock_start,
        ):
            result = runner.invoke(app, ["restart", "--foreground"])

        assert result.exit_code == 0
        mock_start.assert_called_once()

    def test_restart_aborts_if_old_conductor_wont_exit(self, tmp_path: Path):
        """restart exits 1 if old conductor doesn't exit within timeout."""
        with (
            patch("mozart.daemon.process.stop_conductor"),
            patch("mozart.daemon.process.wait_for_conductor_exit", return_value=False),
            patch("mozart.daemon.process.start_conductor") as mock_start,
        ):
            result = runner.invoke(app, ["restart", "--foreground"])

        assert result.exit_code == 1
        assert "did not exit" in result.output
        mock_start.assert_not_called()


# ─── Conductor Status Command ────────────────────────────────────────


class TestConductorStatusCommand:
    """Tests for ``mozart conductor-status``."""

    def test_conductor_status_help(self):
        """``mozart conductor-status --help`` shows status help."""
        result = runner.invoke(app, ["conductor-status", "--help"])
        assert result.exit_code == 0
        assert "Check Mozart conductor status" in result.output

    def test_conductor_status_not_running(self, tmp_path: Path):
        """conductor-status exits with 1 when not running."""
        pid_file = tmp_path / "mozart.pid"

        result = runner.invoke(
            app, ["conductor-status", "--pid-file", str(pid_file)],
        )

        assert result.exit_code == 1
        assert "not running" in result.output

    def test_conductor_status_shows_pid(self, tmp_path: Path):
        """conductor-status shows PID when conductor is running."""
        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("12345")

        def _mock_asyncio_run(coro):
            """Close the coroutine to avoid 'unawaited coroutine' warning."""
            coro.close()
            raise OSError("no socket")

        with (
            patch("mozart.daemon.process._pid_alive", return_value=True),
            patch("mozart.daemon.process.asyncio.run", side_effect=_mock_asyncio_run),
        ):
            result = runner.invoke(
                app,
                ["conductor-status", "--pid-file", str(pid_file), "--socket", str(tmp_path / "sock")],
            )

        assert result.exit_code == 0
        assert "PID 12345" in result.output


# ─── wait_for_conductor_exit ─────────────────────────────────────────


class TestWaitForConductorExit:
    """Tests for ``wait_for_conductor_exit``."""

    def test_returns_true_when_no_pid_file(self, tmp_path: Path):
        """Returns True immediately when PID file doesn't exist."""
        from mozart.daemon.process import wait_for_conductor_exit

        assert wait_for_conductor_exit(tmp_path / "missing.pid") is True

    def test_returns_true_when_process_already_dead(self, tmp_path: Path):
        """Returns True immediately when PID is not alive."""
        from mozart.daemon.process import wait_for_conductor_exit

        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("99999999")  # Non-existent PID

        with patch("mozart.daemon.process._pid_alive", return_value=False):
            result = wait_for_conductor_exit(pid_file, timeout=1.0)

        assert result is True
        assert not pid_file.exists()  # Stale file cleaned up

    def test_returns_true_when_process_exits_during_wait(self, tmp_path: Path):
        """Returns True when process dies while we're polling."""
        from mozart.daemon.process import wait_for_conductor_exit

        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("12345")

        # Alive for first 2 checks, then dead
        call_count = 0

        def _alive_then_dead(pid: int) -> bool:
            nonlocal call_count
            call_count += 1
            return call_count <= 2

        with (
            patch("mozart.daemon.process._pid_alive", side_effect=_alive_then_dead),
            patch("mozart.daemon.process.time.sleep"),  # Don't actually sleep
        ):
            result = wait_for_conductor_exit(pid_file, timeout=5.0)

        assert result is True
        assert not pid_file.exists()

    def test_returns_false_on_timeout(self, tmp_path: Path):
        """Returns False when process stays alive past timeout."""
        from mozart.daemon.process import wait_for_conductor_exit

        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("12345")

        with (
            patch("mozart.daemon.process._pid_alive", return_value=True),
            patch("mozart.daemon.process.time.monotonic", side_effect=[
                0.0,   # Initial deadline calc: deadline = 0.0 + 0.5 = 0.5
                0.0,   # First loop check: 0.0 < 0.5 → continue
                0.3,   # Second loop check: 0.3 < 0.5 → continue
                0.6,   # Third loop check: 0.6 >= 0.5 → exit
            ]),
            patch("mozart.daemon.process.time.sleep"),
        ):
            result = wait_for_conductor_exit(pid_file, timeout=0.5)

        assert result is False
        assert pid_file.exists()  # Not cleaned up — process still alive
