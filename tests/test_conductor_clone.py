"""Tests for --conductor-clone feature.

TDD tests for the conductor-clone functionality that allows running
isolated conductor instances alongside the production conductor.

This enables safe testing of Marianne CLI commands and daemon features
without risking the production conductor (#145, composer P0 directive).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marianne.daemon.config import DaemonConfig, SocketConfig


# =============================================================================
# Clone path resolution
# =============================================================================


class TestClonePathResolution:
    """Test that clone names produce correct isolated paths."""

    def test_default_clone_paths(self) -> None:
        """Default clone uses /tmp/marianne-clone.* paths."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths(None)
        assert paths.socket == Path("/tmp/marianne-clone.sock")
        assert paths.pid_file == Path("/tmp/marianne-clone.pid")
        assert paths.log_file == Path("/tmp/marianne-clone.log")
        assert "clone" in str(paths.state_db)

    def test_named_clone_paths(self) -> None:
        """Named clones get unique paths based on clone name."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("test-1")
        assert paths.socket == Path("/tmp/marianne-clone-test-1.sock")
        assert paths.pid_file == Path("/tmp/marianne-clone-test-1.pid")
        assert paths.log_file == Path("/tmp/marianne-clone-test-1.log")
        assert "clone-test-1" in str(paths.state_db)

    def test_clone_paths_isolated_from_production(self) -> None:
        """Clone paths must never overlap with production paths."""
        from marianne.daemon.clone import resolve_clone_paths

        production = DaemonConfig()
        clone = resolve_clone_paths(None)

        assert clone.socket != production.socket.path
        assert clone.pid_file != production.pid_file

    def test_named_clones_isolated_from_each_other(self) -> None:
        """Different clone names produce different paths."""
        from marianne.daemon.clone import resolve_clone_paths

        clone_a = resolve_clone_paths("alpha")
        clone_b = resolve_clone_paths("beta")

        assert clone_a.socket != clone_b.socket
        assert clone_a.pid_file != clone_b.pid_file
        assert clone_a.state_db != clone_b.state_db

    def test_clone_name_sanitization(self) -> None:
        """Clone names with special characters are sanitized."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("my test/clone")
        # Should not contain spaces or slashes in file paths
        assert " " not in paths.socket.name
        assert "/" not in paths.socket.name


# =============================================================================
# Clone name global state
# =============================================================================


class TestCloneNameState:
    """Test that clone name is stored and retrieved correctly."""

    def test_default_is_none(self) -> None:
        """No clone by default."""
        from marianne.daemon.clone import get_clone_name, set_clone_name

        # Reset state
        set_clone_name(None)
        assert get_clone_name() is None

    def test_set_and_get(self) -> None:
        """Clone name can be set and retrieved."""
        from marianne.daemon.clone import get_clone_name, set_clone_name

        set_clone_name("test-clone")
        try:
            assert get_clone_name() == "test-clone"
        finally:
            set_clone_name(None)

    def test_empty_string_treated_as_default(self) -> None:
        """Empty string clone name means default (unnamed) clone."""
        from marianne.daemon.clone import get_clone_name, set_clone_name

        set_clone_name("")
        try:
            # Empty string is truthy check — treated as "use default clone"
            assert get_clone_name() == ""
        finally:
            set_clone_name(None)


# =============================================================================
# Socket path override via clone
# =============================================================================


class TestSocketPathOverride:
    """Test that _resolve_socket_path respects clone name."""

    def test_no_clone_returns_default(self) -> None:
        """Without clone, socket path is the standard production path."""
        from marianne.daemon.clone import set_clone_name
        from marianne.daemon.detect import _resolve_socket_path

        set_clone_name(None)
        path = _resolve_socket_path(None)
        assert path == SocketConfig().path

    def test_clone_overrides_socket_path(self) -> None:
        """With clone set, socket path is the clone's path."""
        from marianne.daemon.clone import set_clone_name
        from marianne.daemon.detect import _resolve_socket_path

        set_clone_name("test-abc")
        try:
            path = _resolve_socket_path(None)
            assert "clone-test-abc" in str(path)
        finally:
            set_clone_name(None)

    def test_explicit_path_overrides_clone(self) -> None:
        """An explicit socket_path parameter takes precedence over clone."""
        from marianne.daemon.clone import set_clone_name
        from marianne.daemon.detect import _resolve_socket_path

        set_clone_name("test-abc")
        try:
            explicit = Path("/custom/path.sock")
            path = _resolve_socket_path(explicit)
            assert path == explicit
        finally:
            set_clone_name(None)


# =============================================================================
# DaemonConfig from clone
# =============================================================================


class TestDaemonConfigFromClone:
    """Test that clone produces a valid DaemonConfig with isolated paths."""

    def test_clone_config_inherits_defaults(self) -> None:
        """Clone config inherits production defaults for non-path fields."""
        from marianne.daemon.clone import build_clone_config

        config = build_clone_config(None)
        production = DaemonConfig()

        # Non-path fields inherited
        assert config.max_concurrent_jobs == production.max_concurrent_jobs
        assert config.resource_limits == production.resource_limits

        # Path fields are isolated
        assert config.socket.path != production.socket.path
        assert config.pid_file != production.pid_file

    def test_clone_config_from_existing(self) -> None:
        """Clone config can be built from an existing production config."""
        from marianne.daemon.clone import build_clone_config

        prod_config = DaemonConfig(max_concurrent_jobs=5)
        clone_config = build_clone_config(None, base_config=prod_config)

        assert clone_config.max_concurrent_jobs == 5
        assert clone_config.socket.path != prod_config.socket.path

    def test_clone_config_isolates_state_db_no_base(self) -> None:
        """F-132 complement: build_clone_config(None) isolates state_db_path."""
        from marianne.daemon.clone import build_clone_config

        config = build_clone_config(None)
        production = DaemonConfig()

        # state_db_path must differ from production
        assert config.state_db_path != production.state_db_path
        assert "clone" in str(config.state_db_path)

    def test_clone_config_isolates_state_db_with_base(self) -> None:
        """F-132: build_clone_config with base_config isolates state_db_path."""
        from marianne.daemon.clone import build_clone_config

        prod_config = DaemonConfig(max_concurrent_jobs=5)
        clone_config = build_clone_config(None, base_config=prod_config)

        assert clone_config.state_db_path != prod_config.state_db_path
        assert "clone" in str(clone_config.state_db_path)

    def test_clone_config_isolates_log_file(self) -> None:
        """Clone log_file must be isolated from production."""
        from marianne.daemon.clone import build_clone_config

        config = build_clone_config("test-log")
        # log_file should contain the clone name
        assert config.log_file is not None
        assert "clone" in str(config.log_file)

    def test_named_clone_state_db_unique(self) -> None:
        """Named clones get unique state_db_path per name."""
        from marianne.daemon.clone import build_clone_config

        clone_a = build_clone_config("alpha")
        clone_b = build_clone_config("beta")
        assert clone_a.state_db_path != clone_b.state_db_path


# =============================================================================
# try_daemon_route with clone
# =============================================================================


class TestTryDaemonRouteWithClone:
    """Test that try_daemon_route uses clone socket when clone is active."""

    @pytest.mark.asyncio
    async def test_route_uses_clone_socket(self) -> None:
        """When clone is active, daemon route uses clone socket."""
        from marianne.daemon.clone import set_clone_name

        set_clone_name("route-test")
        try:
            # DaemonClient is imported inside try_daemon_route, so patch
            # at the module where it's actually imported
            with patch(
                "marianne.daemon.ipc.client.DaemonClient",
            ) as mock_client_cls:
                mock_instance = AsyncMock()
                mock_instance.is_daemon_running = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_instance

                from marianne.daemon.detect import try_daemon_route

                routed, _ = await try_daemon_route("job.status", {"job_id": "test"})

                # Should have used the clone socket path
                call_args = mock_client_cls.call_args
                socket_used = call_args[0][0]
                assert "clone-route-test" in str(socket_used)
        finally:
            set_clone_name(None)


# =============================================================================
# Conductor start with clone
# =============================================================================


class TestConductorStartWithClone:
    """Test that start_conductor properly handles clone_name parameter."""

    def test_start_conductor_accepts_clone_name(self) -> None:
        """start_conductor should accept a clone_name parameter."""
        import inspect

        from marianne.daemon.process import start_conductor

        sig = inspect.signature(start_conductor)
        assert "clone_name" in sig.parameters

    def test_start_with_clone_uses_clone_socket(self) -> None:
        """When clone_name is set, the daemon uses the clone socket path."""
        from marianne.daemon.clone import resolve_clone_paths

        clone_paths = resolve_clone_paths("start-test")

        with patch("marianne.daemon.process._load_config") as mock_load, \
             patch("marianne.daemon.process._read_pid", return_value=None), \
             patch("marianne.daemon.process.DaemonProcess") as mock_daemon, \
             patch("marianne.core.logging.configure_logging"), \
             patch("asyncio.run"):
            mock_load.return_value = DaemonConfig()

            from marianne.daemon.process import start_conductor

            start_conductor(clone_name="start-test", foreground=True)

            # The config passed to DaemonProcess should have clone paths
            daemon_config = mock_daemon.call_args[0][0]
            assert daemon_config.socket.path == clone_paths.socket
            assert daemon_config.pid_file == clone_paths.pid_file

    def test_start_with_clone_inherits_non_path_config(self) -> None:
        """Clone config should inherit non-path settings from base config."""
        with patch("marianne.daemon.process._load_config") as mock_load, \
             patch("marianne.daemon.process._read_pid", return_value=None), \
             patch("marianne.daemon.process.DaemonProcess") as mock_daemon, \
             patch("marianne.core.logging.configure_logging"), \
             patch("asyncio.run"):
            base_config = DaemonConfig(max_concurrent_jobs=7)
            mock_load.return_value = base_config

            from marianne.daemon.process import start_conductor

            start_conductor(clone_name="inherit-test", foreground=True)

            daemon_config = mock_daemon.call_args[0][0]
            assert daemon_config.max_concurrent_jobs == 7

    def test_start_without_clone_uses_production_paths(self) -> None:
        """Without clone_name, production paths are used (no regression)."""
        # Use a temp PID path so the real conductor's lock doesn't interfere
        prod_config = DaemonConfig(
            pid_file=Path("/tmp/test-prod-no-clone.pid"),
        )

        with patch("marianne.daemon.process._load_config") as mock_load, \
             patch("marianne.daemon.process._read_pid", return_value=None), \
             patch("marianne.daemon.process.DaemonProcess") as mock_daemon, \
             patch("marianne.core.logging.configure_logging"), \
             patch("asyncio.run"):
            mock_load.return_value = prod_config

            from marianne.daemon.process import start_conductor

            start_conductor(foreground=True)

            daemon_config = mock_daemon.call_args[0][0]
            assert daemon_config.socket.path == prod_config.socket.path
            assert daemon_config.pid_file == prod_config.pid_file

    def test_start_clone_checks_clone_pid_not_production(self) -> None:
        """Clone start should check clone PID file, not production."""
        from marianne.daemon.clone import resolve_clone_paths

        clone_paths = resolve_clone_paths("pid-test")

        with patch("marianne.daemon.process._load_config") as mock_load, \
             patch("marianne.daemon.process._read_pid") as mock_read_pid, \
             patch("marianne.daemon.process.DaemonProcess"), \
             patch("marianne.core.logging.configure_logging"), \
             patch("asyncio.run"):
            mock_load.return_value = DaemonConfig()
            mock_read_pid.return_value = None

            from marianne.daemon.process import start_conductor

            start_conductor(clone_name="pid-test", foreground=True)

            # _read_pid should be called with clone PID path
            mock_read_pid.assert_called_once_with(clone_paths.pid_file)

    def test_start_clone_uses_clone_log_file(self) -> None:
        """Clone conductor should log to clone-specific log file."""
        from marianne.daemon.clone import resolve_clone_paths

        clone_paths = resolve_clone_paths("log-test")

        with patch("marianne.daemon.process._load_config") as mock_load, \
             patch("marianne.daemon.process._read_pid", return_value=None), \
             patch("marianne.daemon.process.DaemonProcess"), \
             patch("marianne.core.logging.configure_logging") as mock_log, \
             patch("asyncio.run"):
            mock_load.return_value = DaemonConfig()

            from marianne.daemon.process import start_conductor

            start_conductor(clone_name="log-test", foreground=True)

            # configure_logging should receive clone log file
            assert mock_log.called
            log_call = mock_log.call_args
            # file_path can be positional or keyword
            file_path = log_call.kwargs.get("file_path")
            assert file_path == clone_paths.log_file


# =============================================================================
# Conductor stop/restart/status with clone
# =============================================================================


class TestConductorStopWithClone:
    """Test that stop command uses clone PID when clone is active."""

    def test_stop_with_clone_redirects_pid_file(self) -> None:
        """Stop with clone active should use clone PID file."""
        from marianne.daemon.clone import resolve_clone_paths, set_clone_name

        set_clone_name("stop-test")
        try:
            clone_paths = resolve_clone_paths("stop-test")
            with patch("marianne.daemon.process.stop_conductor") as mock_stop:
                # Simulate what conductor.py does
                from marianne.daemon.clone import get_clone_name, is_clone_active

                pid_file = None
                if is_clone_active() and pid_file is None:
                    pid_file = resolve_clone_paths(get_clone_name()).pid_file

                mock_stop(pid_file=pid_file, force=False)
                mock_stop.assert_called_once_with(
                    pid_file=clone_paths.pid_file, force=False
                )
        finally:
            set_clone_name(None)


class TestConductorRestartWithClone:
    """Test that restart command uses clone paths."""

    def test_restart_with_clone_stops_clone_pid(self) -> None:
        """Restart with clone should stop the clone, not production."""
        from marianne.daemon.clone import resolve_clone_paths, set_clone_name

        set_clone_name("restart-test")
        try:
            clone_paths = resolve_clone_paths("restart-test")
            with patch("marianne.daemon.process.stop_conductor") as mock_stop, \
                 patch("marianne.daemon.process.wait_for_conductor_exit", return_value=True), \
                 patch("marianne.daemon.process.start_conductor") as mock_start:

                from marianne.daemon.clone import get_clone_name, is_clone_active

                # Simulate restart with clone
                pid_file = None
                if is_clone_active():
                    pid_file = resolve_clone_paths(get_clone_name()).pid_file

                try:
                    mock_stop(pid_file=pid_file)
                except SystemExit:
                    pass

                mock_stop.assert_called_once_with(pid_file=clone_paths.pid_file)
        finally:
            set_clone_name(None)


class TestConductorStatusWithClone:
    """Test that conductor-status uses clone PID and socket."""

    def test_status_with_clone_uses_clone_paths(self) -> None:
        """conductor-status with clone should check clone PID and socket."""
        from marianne.daemon.clone import resolve_clone_paths, set_clone_name

        set_clone_name("status-test")
        try:
            clone_paths = resolve_clone_paths("status-test")

            # Verify the clone paths are correctly computed
            assert "clone-status-test" in str(clone_paths.socket)
            assert "clone-status-test" in str(clone_paths.pid_file)
        finally:
            set_clone_name(None)


# =============================================================================
# Adversarial: clone name edge cases
# =============================================================================


class TestCloneNameAdversarial:
    """Adversarial tests for clone name handling."""

    def test_path_traversal_in_clone_name(self) -> None:
        """Clone name with path traversal characters should be sanitized."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("../../etc/passwd")
        # No path traversal in the resulting paths
        assert ".." not in paths.socket.name
        assert "etc" not in str(paths.socket.parent)
        assert paths.socket.parent == Path("/tmp")

    def test_very_long_clone_name(self) -> None:
        """Very long clone names should not break file paths."""
        from marianne.daemon.clone import resolve_clone_paths

        long_name = "a" * 200
        paths = resolve_clone_paths(long_name)
        # Should still resolve without error
        assert paths.socket.parent == Path("/tmp")
        assert "clone" in str(paths.socket)

    def test_null_bytes_in_clone_name(self) -> None:
        """Null bytes in clone name should be stripped."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("test\x00evil")
        assert "\x00" not in str(paths.socket)

    def test_unicode_clone_name(self) -> None:
        """Unicode clone names should be sanitized to ASCII-safe."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("tëst-clöne")
        # Non-ASCII chars replaced with hyphens by sanitizer
        assert paths.socket.parent == Path("/tmp")

    def test_clone_isolation_invariant(self) -> None:
        """No clone should ever share a path with production."""
        from marianne.daemon.clone import resolve_clone_paths

        prod = DaemonConfig()
        for name in [None, "", "test", "../../etc", "a" * 200]:
            clone = resolve_clone_paths(name)
            assert clone.socket != prod.socket.path, f"Socket collision for name={name!r}"
            assert clone.pid_file != prod.pid_file, f"PID collision for name={name!r}"


# =============================================================================
# config_cmd clone awareness (Ghost, movement 1)
# =============================================================================


class TestConfigCmdCloneAwareness:
    """config show must query the clone conductor when clone is active."""

    def test_try_live_config_uses_clone_socket(self) -> None:
        """_try_live_config uses clone socket when clone is active."""
        from marianne.daemon.clone import set_clone_name

        set_clone_name("cfg-test")
        try:
            with patch(
                "marianne.daemon.ipc.client.DaemonClient",
            ) as mock_client_cls:
                mock_instance = AsyncMock()
                mock_instance.config = AsyncMock(
                    side_effect=ConnectionRefusedError("test"),
                )
                mock_client_cls.return_value = mock_instance

                from marianne.cli.commands.config_cmd import _try_live_config

                _try_live_config()

                call_args = mock_client_cls.call_args
                socket_used = call_args[0][0]
                assert "clone-cfg-test" in str(socket_used)
        finally:
            set_clone_name(None)

    def test_try_live_config_uses_production_without_clone(self) -> None:
        """_try_live_config uses production socket when no clone is active."""
        from marianne.daemon.clone import set_clone_name
        from marianne.daemon.config import SocketConfig

        set_clone_name(None)
        with patch(
            "marianne.daemon.ipc.client.DaemonClient",
        ) as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.config = AsyncMock(
                side_effect=ConnectionRefusedError("test"),
            )
            mock_client_cls.return_value = mock_instance

            from marianne.cli.commands.config_cmd import _try_live_config

            _try_live_config()

            call_args = mock_client_cls.call_args
            socket_used = call_args[0][0]
            assert socket_used == SocketConfig().path
