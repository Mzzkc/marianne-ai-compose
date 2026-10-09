"""Tests for marianne.daemon.process module.

Covers core conductor functions, PID file helpers, signal handler
installation, and _daemonize() skip in foreground mode.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from marianne.daemon.config import DaemonConfig
from marianne.daemon.process import (
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
        with patch("marianne.daemon.process.os.kill", side_effect=PermissionError):
            assert _pid_alive(1) is True


# ─── Core Conductor Functions ─────────────────────────────────────────


class TestStartConductor:
    """Tests for start_conductor() core function."""

    def test_start_already_running_exits_1(self, tmp_path: Path):
        """start_conductor exits with code 1 if conductor already running."""
        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text(str(os.getpid()))

        with patch("marianne.daemon.process._load_config") as mock_config:
            cfg = MagicMock()
            cfg.pid_file = pid_file
            cfg.log_level = "info"
            mock_config.return_value = cfg

            with pytest.raises(typer.Exit):
                start_conductor(foreground=True)

    def test_start_foreground_skips_daemonize(self, tmp_path: Path):
        """In foreground mode, _daemonize() is NOT called."""
        pid_file = tmp_path / "marianne.pid"

        with (
            patch("marianne.daemon.process._load_config") as mock_config,
            patch("marianne.daemon.process._daemonize") as mock_daemonize,
            patch("marianne.daemon.process._read_pid", return_value=None),
            patch("marianne.core.logging.configure_logging"),
            patch("marianne.daemon.process.DaemonProcess"),
            patch("marianne.daemon.process.asyncio.run"),
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
        pid_file = tmp_path / "marianne.pid"

        with (
            patch("marianne.daemon.process._load_config") as mock_config,
            patch("marianne.daemon.process._daemonize") as mock_daemonize,
            patch("marianne.daemon.process._read_pid", return_value=None),
            patch("marianne.core.logging.configure_logging"),
            patch("marianne.daemon.process.DaemonProcess"),
            patch("marianne.daemon.process.asyncio.run"),
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
        pid_file = tmp_path / "marianne.pid"

        with pytest.raises(typer.Exit):
            stop_conductor(pid_file=pid_file)

    def test_stop_sends_sigterm(self, tmp_path: Path):
        """stop_conductor sends SIGTERM to the PID by default."""
        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        with (
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
        ):
            stop_conductor(pid_file=pid_file)

        mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_stop_force_sends_sigkill(self, tmp_path: Path):
        """stop_conductor with force=True sends SIGKILL."""
        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        with (
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor(pid_file=pid_file, force=True)

        mock_kill.assert_called_once_with(12345, signal.SIGKILL)


class TestGetConductorStatus:
    """Tests for get_conductor_status() core function."""

    def test_status_not_running_exits_1(self, tmp_path: Path):
        """get_conductor_status exits with code 1 when not running."""
        pid_file = tmp_path / "marianne.pid"

        with pytest.raises(typer.Exit):
            get_conductor_status(pid_file=pid_file)

    def test_status_shows_pid_when_running(self, tmp_path: Path, capsys):
        """get_conductor_status shows the PID when running (pid-file only;
        socket details unavailable is non-fatal in human mode)."""
        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        def _mock_asyncio_run(coro):
            """Close the coroutine to avoid 'unawaited coroutine' warning."""
            coro.close()
            raise OSError("no socket")

        with (
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.asyncio.run", side_effect=_mock_asyncio_run),
        ):
            get_conductor_status(pid_file=pid_file)

        assert "PID 12345" in capsys.readouterr().out

    def test_status_explicit_socket_unreachable_exits_1(self, tmp_path: Path):
        """Explicit --socket that does not answer exits 1 — socket-only
        probing has no pid-file fallback (#408 landing P2)."""
        pid_file = tmp_path / "marianne.pid"
        pid_file.write_text("12345")

        def _mock_asyncio_run(coro):
            coro.close()
            return (None, None, None)  # probes got no answer

        with (
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.asyncio.run", side_effect=_mock_asyncio_run),
            pytest.raises(typer.Exit),
        ):
            get_conductor_status(pid_file=pid_file, socket_path=tmp_path / "sock")


# ─── DaemonProcess ─────────────────────────────────────────────────────


class TestDaemonProcess:
    """Tests for DaemonProcess lifecycle."""

    def test_daemon_process_init(self):
        """DaemonProcess initializes with config and pgroup."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig()
        dp = DaemonProcess(config)
        assert dp._config is config
        assert not dp._signal_received.is_set()

    @pytest.mark.asyncio
    async def test_daemon_process_signal_handler_registration(self):
        """DaemonProcess.run() installs signal handlers for SIGTERM and SIGINT."""
        from marianne.daemon.config import DaemonConfig, ProfilerConfig, SocketConfig

        config = DaemonConfig(
            pid_file=Path("/tmp/test-conductor-signal.pid"),
            socket=SocketConfig(path=Path("/tmp/test-conductor-signal.sock")),
            profiler=ProfilerConfig(enabled=False),
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
            patch("marianne.daemon.process._write_pid"),
            patch("marianne.daemon.ipc.server.DaemonServer") as mock_server_cls,
            patch("marianne.daemon.manager.JobManager") as mock_mgr_cls,
            patch("marianne.daemon.monitor.ResourceMonitor") as mock_mon_cls,
            patch("marianne.daemon.ipc.handler.RequestHandler"),
            patch("marianne.daemon.health.HealthChecker") as mock_health_cls,
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

            mock_health = MagicMock()
            mock_health.start_periodic_checks = AsyncMock()
            mock_health.stop_periodic_checks = AsyncMock()
            mock_health_cls.return_value = mock_health

            await dp.run()

            # The listen backlog must never become the lifetime client budget.
            assert mock_server_cls.call_args.kwargs["max_connections"] == 500
            assert mock_server_cls.call_args.kwargs["backlog"] == 5

        assert signal.SIGTERM in handlers_added
        assert signal.SIGINT in handlers_added
        assert signal.SIGHUP in handlers_added

    @pytest.mark.asyncio
    async def test_run_does_not_warn_that_active_state_or_sheet_limits_are_ignored(
        self,
        tmp_path: Path,
    ):
        """Configured SQLite persistence and Baton sheet limits are real runtime inputs."""
        from marianne.daemon.config import DaemonConfig, ProfilerConfig, SocketConfig

        config = DaemonConfig(
            pid_file=tmp_path / "conductor.pid",
            socket=SocketConfig(path=tmp_path / "conductor.sock"),
            profiler=ProfilerConfig(enabled=False),
            state_db_path=tmp_path / "state.db",
            max_concurrent_sheets=7,
        )
        dp = DaemonProcess(config)
        mock_loop = MagicMock()
        mock_loop.add_signal_handler = MagicMock()

        with (
            patch.object(dp._pgroup, "setup"),
            patch.object(dp._pgroup, "kill_all_children"),
            patch.object(dp._pgroup, "cleanup_orphans", return_value=[]),
            patch("marianne.daemon.process._write_pid"),
            patch("marianne.daemon.ipc.server.DaemonServer") as mock_server_cls,
            patch("marianne.daemon.manager.JobManager") as mock_mgr_cls,
            patch("marianne.daemon.monitor.ResourceMonitor") as mock_mon_cls,
            patch("marianne.daemon.ipc.handler.RequestHandler"),
            patch("marianne.daemon.health.HealthChecker") as mock_health_cls,
            patch("marianne.daemon.process._logger.warning") as warning,
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_server_cls.return_value = AsyncMock()
            mock_mgr = MagicMock(running_count=0, active_job_count=0)
            mock_mgr.start = AsyncMock()
            mock_mgr.wait_for_shutdown = AsyncMock()
            mock_mgr_cls.return_value = mock_mgr
            mock_mon_cls.return_value = AsyncMock()
            mock_health = MagicMock()
            mock_health.start_periodic_checks = AsyncMock()
            mock_health.stop_periodic_checks = AsyncMock()
            mock_health_cls.return_value = mock_health

            await dp.run()

        assert not any(
            call.args and call.args[0] == "config.reserved_field_ignored"
            for call in warning.call_args_list
        )

    @pytest.mark.asyncio
    async def test_run_cleans_pid_file_on_crash(self):
        """run() removes PID file even if an exception occurs mid-lifecycle."""
        from marianne.daemon.config import DaemonConfig, SocketConfig

        pid_file = Path("/tmp/test-conductor-crash.pid")
        config = DaemonConfig(
            pid_file=pid_file,
            socket=SocketConfig(path=Path("/tmp/test-conductor-crash.sock")),
        )
        dp = DaemonProcess(config)

        with (
            patch.object(dp._pgroup, "setup", side_effect=RuntimeError("crash!")),
            patch("marianne.daemon.process._write_pid") as mock_write,
        ):
            # _write_pid is called, then setup() raises, then finally cleans up
            mock_write.side_effect = lambda pf: pf.parent.mkdir(
                parents=True, exist_ok=True
            ) or pf.write_text("12345")

            with pytest.raises(RuntimeError, match="crash!"):
                await dp.run()

        # PID file should be cleaned up by the finally block
        assert not pid_file.exists()

    @pytest.mark.asyncio
    async def test_register_methods_wires_rpc(self):
        """_register_methods registers all expected JSON-RPC methods."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig()
        dp = DaemonProcess(config)

        handler = MagicMock()
        manager = MagicMock()
        health = MagicMock()

        dp._register_methods(handler, manager, health)

        # Check all expected methods were registered
        registered_handlers = {
            call.args[0]: call.args[1]
            for call in handler.register.call_args_list
        }
        registered_methods = set(registered_handlers)
        expected = {
            "job.submit",
            "job.status",
            "job.pause",
            "job.resume",
            "job.modify",
            "job.cancel",
            "job.list",
            "job.clear",
            "job.errors",
            "job.diagnose",
            "job.history",
            "job.recover",
            "job.resolve_escalation",
            "job.output.stream",
            "daemon.status",
            "daemon.shutdown",
            "daemon.config",
            "daemon.reload",
            "daemon.health",
            "daemon.ready",
            "daemon.top",
            "daemon.top.stream",
            "daemon.events",
            "daemon.observer_events",
            "daemon.monitor.stream",
            "daemon.rate_limits",
            "daemon.clear_rate_limits",
            "daemon.learning.patterns",
        }
        assert registered_methods == expected

        manager.cancel_job = AsyncMock(return_value=True)
        cancel_result = await registered_handlers["job.cancel"](
            {"job_id": "job-123"},
            object(),
        )

        assert cancel_result == {"cancelled": True}
        manager.cancel_job.assert_awaited_once_with("job-123", source="ipc")

    @staticmethod
    def _reload_manager(config: DaemonConfig, result: Any = None) -> MagicMock:
        """Mock manager whose reload_configuration returns a real result.

        #408: SIGHUP delegates to JobManager.reload_configuration; the mock
        must behave like the real single reload path for the delegate.
        """
        from marianne.daemon.types import ConfigReloadResult

        manager = MagicMock()
        manager.config = config
        manager.reload_configuration = AsyncMock(
            return_value=result
            or ConfigReloadResult(
                success=True, reason="sighup", config_generation=2
            )
        )
        return manager

    @pytest.mark.asyncio
    async def test_handle_sighup_delegates_to_single_reload_path(
        self, tmp_path: Path
    ):
        """_handle_sighup is a trigger: it calls manager.reload_configuration
        and syncs the process config view from the manager (#408)."""
        import yaml

        from marianne.daemon.config import DaemonConfig

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 8}))

        config = DaemonConfig(max_concurrent_jobs=8)
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        mock_manager = self._reload_manager(config)
        dp._manager = mock_manager

        await dp._handle_sighup()

        mock_manager.reload_configuration.assert_awaited_once_with(
            "sighup", profile=None
        )
        assert dp._config is config

    @pytest.mark.asyncio
    async def test_handle_sighup_no_config_file(self):
        """A missing manager surface is tolerated — delegate fails soft."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig()  # config_file defaults to None
        dp = DaemonProcess(config)

        # No manager wired at all: should not raise, returns a failed result.
        result = await dp.reload_configuration("sighup")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_handle_sighup_failed_reload_keeps_process_config(
        self, tmp_path: Path
    ):
        """When the single path declines, the process config view is kept."""
        import yaml

        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.types import ConfigReloadResult

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 5}))

        config = DaemonConfig(max_concurrent_jobs=5)
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        mock_manager = self._reload_manager(
            config,
            ConfigReloadResult(
                success=False,
                reason="sighup",
                config_generation=1,
                error="validation failed",
            ),
        )
        dp._manager = mock_manager

        await dp._handle_sighup()

        mock_manager.reload_configuration.assert_awaited_once()
        assert dp._config.max_concurrent_jobs == 5  # unchanged view

    @pytest.mark.asyncio
    async def test_handle_sighup_warns_non_reloadable(self, tmp_path: Path):
        """Restart-only decisions live in the manager; the delegate applies
        whatever effective config the manager hands back (#408)."""
        import yaml

        from marianne.daemon.config import DaemonConfig, SocketConfig

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "socket": {"path": "/tmp/new-marianne.sock"},
                }
            )
        )

        config = DaemonConfig(
            socket=SocketConfig(path=Path("/tmp/old-marianne.sock")),
        )
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        # The manager's effective config keeps the RUNNING socket value —
        # exactly what reload_configuration returns for restart-only fields.
        dp._manager = self._reload_manager(config)

        await dp._handle_sighup()
        assert dp._config.socket.path == Path("/tmp/old-marianne.sock")

    @pytest.mark.asyncio
    async def test_handle_sighup_reconfigures_log_level_only_when_applied(
        self,
        tmp_path: Path,
    ):
        """SIGHUP reconfigures logging only when the reload applied log_level
        (log DESTINATION is restart-only; the level is hot)."""
        import yaml

        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.types import ConfigReloadResult

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text(yaml.dump({"log_level": "debug"}))

        config = DaemonConfig(log_level="info")
        config.config_file = cfg_path
        effective = config.model_copy(update={"log_level": "debug"})
        dp = DaemonProcess(config)
        dp._manager = self._reload_manager(
            effective,
            ConfigReloadResult(
                success=True,
                reason="sighup",
                config_generation=2,
                applied=["log_level"],
            ),
        )

        with patch("marianne.core.logging.configure_logging") as mock_configure:
            await dp._handle_sighup()

        mock_configure.assert_called_once()
        assert mock_configure.call_args.kwargs["level"] == "DEBUG"
        # Destination is restart-only: reconfigure keeps the RUNNING file.
        assert mock_configure.call_args.kwargs["file_path"] == effective.log_file

    @pytest.mark.asyncio
    async def test_register_methods_without_health(self):
        """_register_methods skips health probes when health is None."""
        from marianne.daemon.config import DaemonConfig

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
        """A failed reload keeps the process config view — the delegate only
        syncs on success. Real fail-closed semantics are covered in
        test_config_hot_reload_408.py against the real manager."""
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.types import ConfigReloadResult

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text("max_concurrent_jobs: 5")

        config = DaemonConfig(max_concurrent_jobs=5)
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        dp._manager = self._reload_manager(
            config,
            ConfigReloadResult(
                success=False,
                reason="sighup",
                config_generation=1,
                error="validation failed",
            ),
        )

        await dp._handle_sighup()

        # Config view unchanged — reload failed, kept current
        assert dp._config.max_concurrent_jobs == 5
        dp._manager.reload_configuration.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_sighup_deleted_file_keeps_current_config(self, tmp_path: Path):
        """A deleted config file is a failed reload (fail-closed #408) —
        the running config must NOT silently revert to defaults."""
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.types import ConfigReloadResult

        cfg_path = tmp_path / "daemon.yaml"
        cfg_path.write_text("max_concurrent_jobs: 7")

        config = DaemonConfig(max_concurrent_jobs=7)
        config.config_file = cfg_path
        dp = DaemonProcess(config)
        cfg_path.unlink()
        dp._manager = self._reload_manager(
            config,
            ConfigReloadResult(
                success=False,
                reason="sighup",
                config_generation=1,
                error=f"config file missing: {cfg_path}",
            ),
        )

        await dp._handle_sighup()

        assert dp._config.max_concurrent_jobs == 7
        dp._manager.reload_configuration.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_daemon_config_rpc_handler_returns_config(self):
        """The daemon.config RPC handler returns the current config as dict."""
        from marianne.daemon.config import DaemonConfig

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
