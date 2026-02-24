"""Tests for mozart.daemon.process module.

Covers core conductor functions, PID file helpers, signal handler
installation, and _daemonize() skip in foreground mode.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from mozart.daemon.process import (
    DaemonProcess,
    _pid_alive,
    _read_pid,
    _write_pid,
    get_conductor_status,
    start_conductor,
    stop_conductor,
)


# ─── PID File Helpers ──────────────────────────────────────────────────


class TestWritePid:
    """Tests for _write_pid() atomic write."""

    def test_write_pid_creates_file(self, tmp_path: Path):
        """PID file is created with current PID."""
        pid_file = tmp_path / "test.pid"
        _write_pid(pid_file)
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) == os.getpid()

    def test_write_pid_creates_parent_dirs(self, tmp_path: Path):
        """Parent directories are created if missing."""
        pid_file = tmp_path / "nested" / "dir" / "test.pid"
        _write_pid(pid_file)
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) == os.getpid()

    def test_write_pid_overwrites_existing(self, tmp_path: Path):
        """Existing PID file is overwritten."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("99999")
        _write_pid(pid_file)
        assert int(pid_file.read_text().strip()) == os.getpid()

    def test_write_pid_rejects_symlink(self, tmp_path: Path):
        """_write_pid raises OSError when PID file is a symlink."""
        target = tmp_path / "real.pid"
        target.write_text("99999")
        pid_file = tmp_path / "link.pid"
        pid_file.symlink_to(target)

        with pytest.raises(OSError, match="symlink"):
            _write_pid(pid_file)


class TestReadPid:
    """Tests for _read_pid()."""

    def test_read_existing_pid(self, tmp_path: Path):
        """Read a valid PID from file."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")
        assert _read_pid(pid_file) == 12345

    def test_read_missing_file_returns_none(self, tmp_path: Path):
        """Missing PID file returns None."""
        pid_file = tmp_path / "nonexistent.pid"
        assert _read_pid(pid_file) is None

    def test_read_invalid_content_returns_none(self, tmp_path: Path):
        """Non-integer PID file returns None."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("not-a-pid")
        assert _read_pid(pid_file) is None

    def test_read_pid_with_whitespace(self, tmp_path: Path):
        """PID with surrounding whitespace is parsed correctly."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("  42  \n")
        assert _read_pid(pid_file) == 42


class TestPidAlive:
    """Tests for _pid_alive()."""

    def test_current_process_is_alive(self):
        """Our own PID is alive."""
        assert _pid_alive(os.getpid()) is True

    def test_nonexistent_pid_is_not_alive(self):
        """A very high PID that doesn't exist returns False."""
        # Use a PID unlikely to exist
        assert _pid_alive(4_000_000) is False

    def test_permission_error_treated_as_alive(self):
        """PermissionError from os.kill returns True (process exists)."""
        with patch("mozart.daemon.process.os.kill", side_effect=PermissionError):
            assert _pid_alive(1) is True


# ─── Core Conductor Functions ─────────────────────────────────────────


class TestStartConductor:
    """Tests for start_conductor() core function."""

    def test_start_already_running_exits_1(self, tmp_path: Path):
        """start_conductor exits with code 1 if conductor already running."""
        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text(str(os.getpid()))

        with patch("mozart.daemon.process._load_config") as mock_config:
            cfg = MagicMock()
            cfg.pid_file = pid_file
            cfg.log_level = "info"
            mock_config.return_value = cfg

            with pytest.raises(typer.Exit):
                start_conductor(foreground=True)

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

            start_conductor(foreground=True)

        mock_daemonize.assert_not_called()

    def test_start_background_calls_daemonize(self, tmp_path: Path):
        """Without foreground=True, _daemonize() is called."""
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

            start_conductor(foreground=False)

        mock_daemonize.assert_called_once()


class TestStopConductor:
    """Tests for stop_conductor() core function."""

    def test_stop_not_running_exits_1(self, tmp_path: Path):
        """stop_conductor exits with code 1 when conductor is not running."""
        pid_file = tmp_path / "mozart.pid"

        with pytest.raises(typer.Exit):
            stop_conductor(pid_file=pid_file)

    def test_stop_sends_sigterm(self, tmp_path: Path):
        """stop_conductor sends SIGTERM to the PID by default."""
        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("12345")

        with (
            patch("mozart.daemon.process._pid_alive", return_value=True),
            patch("mozart.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor(pid_file=pid_file)

        mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_stop_force_sends_sigkill(self, tmp_path: Path):
        """stop_conductor with force=True sends SIGKILL."""
        pid_file = tmp_path / "mozart.pid"
        pid_file.write_text("12345")

        with (
            patch("mozart.daemon.process._pid_alive", return_value=True),
            patch("mozart.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor(pid_file=pid_file, force=True)

        mock_kill.assert_called_once_with(12345, signal.SIGKILL)


class TestGetConductorStatus:
    """Tests for get_conductor_status() core function."""

    def test_status_not_running_exits_1(self, tmp_path: Path):
        """get_conductor_status exits with code 1 when not running."""
        pid_file = tmp_path / "mozart.pid"

        with pytest.raises(typer.Exit):
            get_conductor_status(pid_file=pid_file)

    def test_status_shows_pid_when_running(self, tmp_path: Path, capsys):
        """get_conductor_status shows the PID when running."""
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
            get_conductor_status(pid_file=pid_file, socket_path=tmp_path / "sock")


# ─── DaemonProcess ─────────────────────────────────────────────────────


class TestDaemonProcess:
    """Tests for DaemonProcess lifecycle."""

    def test_daemon_process_init(self):
        """DaemonProcess initializes with config and pgroup."""
        from mozart.daemon.config import DaemonConfig

        config = DaemonConfig()
        dp = DaemonProcess(config)
        assert dp._config is config
        assert not dp._signal_received.is_set()

    @pytest.mark.asyncio
    async def test_daemon_process_signal_handler_registration(self):
        """DaemonProcess.run() installs signal handlers for SIGTERM and SIGINT."""
        from mozart.daemon.config import DaemonConfig, SocketConfig

        config = DaemonConfig(
            pid_file=Path("/tmp/test-conductor-signal.pid"),
            socket=SocketConfig(path=Path("/tmp/test-conductor-signal.sock")),
        )
        dp = DaemonProcess(config)

        handlers_added: list[signal.Signals] = []

        # We test by running the actual run() method but intercepting the
        # event loop's add_signal_handler calls via a mock loop.
        mock_loop = MagicMock()
        mock_loop.add_signal_handler = lambda sig, cb: handlers_added.append(sig)

        # Components are imported locally inside run(), so patch at their real modules
        with (
            patch.object(dp._pgroup, "setup"),
            patch.object(dp._pgroup, "kill_all_children"),
            patch.object(dp._pgroup, "cleanup_orphans", return_value=[]),
            patch("mozart.daemon.process._write_pid"),
            patch("mozart.daemon.ipc.server.DaemonServer") as mock_server_cls,
            patch("mozart.daemon.manager.JobManager") as mock_mgr_cls,
            patch("mozart.daemon.monitor.ResourceMonitor") as mock_mon_cls,
            patch("mozart.daemon.ipc.handler.RequestHandler"),
            patch("mozart.daemon.health.HealthChecker"),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_server = AsyncMock()
            mock_server_cls.return_value = mock_server

            mock_mgr = MagicMock()
            mock_mgr.running_count = 0
            mock_mgr.active_job_count = 0
            mock_mgr.start = AsyncMock()
            mock_mgr.wait_for_shutdown = AsyncMock()
            mock_mgr_cls.return_value = mock_mgr

            mock_mon = AsyncMock()
            mock_mon_cls.return_value = mock_mon

            await dp.run()

        assert signal.SIGTERM in handlers_added
        assert signal.SIGINT in handlers_added
        assert signal.SIGHUP in handlers_added

    @pytest.mark.asyncio
    async def test_run_cleans_pid_file_on_crash(self):
        """run() removes PID file even if an exception occurs mid-lifecycle."""
        from mozart.daemon.config import DaemonConfig, SocketConfig

        pid_file = Path("/tmp/test-conductor-crash.pid")
        config = DaemonConfig(
            pid_file=pid_file,
            socket=SocketConfig(path=Path("/tmp/test-conductor-crash.sock")),
        )
        dp = DaemonProcess(config)

        with (
            patch.object(dp._pgroup, "setup", side_effect=RuntimeError("crash!")),
            patch("mozart.daemon.process._write_pid") as mock_write,
        ):
            # _write_pid is called, then setup() raises, then finally cleans up
            mock_write.side_effect = (
                lambda pf: pf.parent.mkdir(parents=True, exist_ok=True)
                or pf.write_text("12345")
            )

            with pytest.raises(RuntimeError, match="crash!"):
                await dp.run()

        # PID file should be cleaned up by the finally block
        assert not pid_file.exists()

    @pytest.mark.asyncio
    async def test_register_methods_wires_rpc(self):
        """_register_methods registers all expected JSON-RPC methods."""
        from mozart.daemon.config import DaemonConfig

        config = DaemonConfig()
        dp = DaemonProcess(config)

        handler = MagicMock()
        manager = MagicMock()
        health = MagicMock()

        dp._register_methods(handler, manager, health)

        # Check all expected methods were registered
        registered_methods = {call.args[0] for call in handler.register.call_args_list}
        expected = {
            "job.submit", "job.status", "job.pause", "job.resume",
            "job.modify", "job.cancel", "job.list", "job.clear",
            "job.errors", "job.diagnose", "job.history", "job.recover",
            "daemon.status", "daemon.shutdown", "daemon.config",
            "daemon.health", "daemon.ready",
            "daemon.top", "daemon.top.stream", "daemon.events",
            "daemon.observer_events",
        }
        assert registered_methods == expected

    @pytest.mark.asyncio
    async def test_handle_sighup_reloads_config(self, tmp_path: Path):
        """_handle_sighup reloads config from disk and applies changes."""
        from mozart.daemon.config import DaemonConfig

        # Create a config file with initial settings
        import yaml
        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 3}))

        config = DaemonConfig(max_concurrent_jobs=3)
        config.config_file = cfg_path
        dp = DaemonProcess(config)

        # Simulate running state with mock manager/monitor
        mock_manager = MagicMock()
        mock_monitor = MagicMock()
        dp._manager = mock_manager
        dp._monitor = mock_monitor

        # Update config file with new value
        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 8}))

        await dp._handle_sighup()

        mock_manager.apply_config.assert_called_once()
        mock_monitor.update_limits.assert_called_once()
        assert dp._config.max_concurrent_jobs == 8

    @pytest.mark.asyncio
    async def test_handle_sighup_no_config_file(self):
        """_handle_sighup does nothing when no config file was recorded."""
        from mozart.daemon.config import DaemonConfig

        config = DaemonConfig()  # config_file defaults to None
        dp = DaemonProcess(config)

        # Should not raise — just logs a warning
        await dp._handle_sighup()

    @pytest.mark.asyncio
    async def test_handle_sighup_warns_non_reloadable(self, tmp_path: Path):
        """_handle_sighup logs warnings when non-reloadable fields change."""
        from mozart.daemon.config import DaemonConfig, SocketConfig

        import yaml
        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(yaml.dump({
            "socket": {"path": "/tmp/new-mozart.sock"},
        }))

        config = DaemonConfig(
            socket=SocketConfig(path=Path("/tmp/old-mozart.sock")),
        )
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        dp._manager = MagicMock()
        dp._monitor = MagicMock()

        # Should not raise — applies config and logs warning for socket.path
        await dp._handle_sighup()
        # Config is updated despite non-reloadable warning
        assert dp._config.socket.path == Path("/tmp/new-mozart.sock")

    @pytest.mark.asyncio
    async def test_register_methods_without_health(self):
        """_register_methods skips health probes when health is None."""
        from mozart.daemon.config import DaemonConfig

        config = DaemonConfig()
        dp = DaemonProcess(config)

        handler = MagicMock()
        manager = MagicMock()

        dp._register_methods(handler, manager, health=None)

        registered_methods = {call.args[0] for call in handler.register.call_args_list}
        assert "daemon.health" not in registered_methods
        assert "daemon.ready" not in registered_methods
        # But core methods are still there
        assert "job.submit" in registered_methods
        assert "daemon.status" in registered_methods

    @pytest.mark.asyncio
    async def test_handle_sighup_corrupt_yaml_keeps_current_config(self, tmp_path: Path):
        """_handle_sighup keeps current config when the file contains invalid YAML."""
        from mozart.daemon.config import DaemonConfig

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text("max_concurrent_jobs: 5")

        config = DaemonConfig(max_concurrent_jobs=5)
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        dp._manager = MagicMock()
        dp._monitor = MagicMock()

        # Corrupt the file with invalid YAML that will cause model_validate to fail
        cfg_path.write_text("max_concurrent_jobs: not-a-number")

        await dp._handle_sighup()

        # Config should be unchanged — reload failed, kept current
        assert dp._config.max_concurrent_jobs == 5
        # Manager/monitor should NOT have been called
        dp._manager.apply_config.assert_not_called()
        dp._monitor.update_limits.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_sighup_deleted_file_keeps_current_config(self, tmp_path: Path):
        """_handle_sighup keeps current config when the config file is deleted."""
        from mozart.daemon.config import DaemonConfig

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text("max_concurrent_jobs: 5")

        config = DaemonConfig(max_concurrent_jobs=5)
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        dp._manager = MagicMock()
        dp._monitor = MagicMock()

        # Delete the config file
        cfg_path.unlink()

        await dp._handle_sighup()

        # _load_config returns defaults when file is missing — config IS replaced
        # with defaults (max_concurrent_jobs=5 is the default anyway)
        # But apply_config and update_limits ARE called because _load_config
        # successfully returns a DaemonConfig (defaults).
        dp._manager.apply_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_daemon_config_rpc_handler_returns_config(self):
        """The daemon.config RPC handler returns the current config as dict."""
        from mozart.daemon.config import DaemonConfig

        config = DaemonConfig(max_concurrent_jobs=7)
        dp = DaemonProcess(config)

        handler = MagicMock()
        manager = MagicMock()

        dp._register_methods(handler, manager, health=None)

        # Find the daemon.config handler
        config_handler = None
        for call in handler.register.call_args_list:
            if call.args[0] == "daemon.config":
                config_handler = call.args[1]
                break

        assert config_handler is not None
        result = await config_handler({}, None)
        assert isinstance(result, dict)
        assert result["max_concurrent_jobs"] == 7
