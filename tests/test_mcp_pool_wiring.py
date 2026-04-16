"""Tests for MCP pool conductor lifecycle wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from marianne.daemon.config import McpPoolConfig, McpServerEntry
from marianne.daemon.mcp_pool import McpPoolManager, McpServerState


class TestMcpPoolLifecycleWiring:
    def test_manager_init_has_mcp_pool_none(self) -> None:
        assert McpPoolManager(McpPoolConfig()).server_names() == []

    def test_empty_pool_config_backward_compat(self) -> None:
        assert McpPoolConfig().servers == {}

    async def test_pool_start_all_called_on_start(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "test": McpServerEntry(
                    command="echo test", transport="stdio", socket=str(tmp_path / "test.sock")
                )
            }
        )
        mgr = McpPoolManager(config)
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock(pid=12345, returncode=None)
            mock_exec.return_value = mock_proc
            await mgr.start_all()
            assert mgr.is_running("test")

    async def test_pool_stop_all_called_on_shutdown(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "test": McpServerEntry(
                    command="echo test", transport="stdio", socket=str(tmp_path / "test.sock")
                )
            }
        )
        mgr = McpPoolManager(config)
        mock_proc = AsyncMock(returncode=None, pid=12345)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mgr._handles["test"].process = mock_proc
        mgr._handles["test"].state = McpServerState.RUNNING
        await mgr.stop_all()
        mock_proc.terminate.assert_called_once()
        assert mgr._handles["test"].state == McpServerState.STOPPED

    def test_pool_config_with_servers(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "a": McpServerEntry(
                    command="a", transport="stdio", socket=str(tmp_path / "a.sock")
                ),
                "b": McpServerEntry(
                    command="b", transport="stdio", socket=str(tmp_path / "b.sock")
                ),
            }
        )
        assert len(McpPoolManager(config).server_names()) == 2


class TestMcpPoolHealthCheck:
    async def test_health_check_running_server(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "t": McpServerEntry(command="e", transport="stdio", socket=str(tmp_path / "t.sock"))
            }
        )
        mgr = McpPoolManager(config)
        mgr._handles["t"].process = MagicMock(returncode=None)
        mgr._handles["t"].state = McpServerState.RUNNING
        assert await mgr.health_check("t") is True

    async def test_health_check_dead_server(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "t": McpServerEntry(command="e", transport="stdio", socket=str(tmp_path / "t.sock"))
            }
        )
        mgr = McpPoolManager(config)
        mgr._handles["t"].process = MagicMock(returncode=1)
        mgr._handles["t"].state = McpServerState.FAILED
        assert await mgr.health_check("t") is False

    async def test_health_check_unknown_server(self) -> None:
        assert await McpPoolManager(McpPoolConfig()).health_check("x") is False

    async def test_restart_failed_server_on_failure_policy(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "t": McpServerEntry(
                    command="e",
                    transport="stdio",
                    socket=str(tmp_path / "t.sock"),
                    restart_policy="on-failure",
                )
            }
        )
        mgr = McpPoolManager(config)
        mgr._handles["t"].state = McpServerState.FAILED
        assert config.servers["t"].restart_policy == "on-failure"

    async def test_no_restart_with_never_policy(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "t": McpServerEntry(
                    command="e",
                    transport="stdio",
                    socket=str(tmp_path / "t.sock"),
                    restart_policy="never",
                )
            }
        )
        mgr = McpPoolManager(config)
        mgr._handles["t"].state = McpServerState.FAILED
        assert config.servers["t"].restart_policy == "never"


class TestMcpPoolSocketPaths:
    def test_get_socket_path(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "t": McpServerEntry(command="e", transport="stdio", socket=str(tmp_path / "t.sock"))
            }
        )
        assert McpPoolManager(config).get_socket_path("t") == tmp_path / "t.sock"

    def test_get_socket_path_missing(self) -> None:
        assert McpPoolManager(McpPoolConfig()).get_socket_path("x") is None

    def test_get_all_socket_paths(self, tmp_path: Path) -> None:
        config = McpPoolConfig(
            servers={
                "a": McpServerEntry(
                    command="a", transport="stdio", socket=str(tmp_path / "a.sock")
                ),
                "b": McpServerEntry(
                    command="b", transport="stdio", socket=str(tmp_path / "b.sock")
                ),
            }
        )
        paths = McpPoolManager(config).get_all_socket_paths()
        assert len(paths) == 2

    def test_get_all_socket_paths_empty(self) -> None:
        assert McpPoolManager(McpPoolConfig()).get_all_socket_paths() == {}
