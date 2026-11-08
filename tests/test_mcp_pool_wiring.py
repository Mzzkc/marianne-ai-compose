"""Tests for MCP pool conductor lifecycle wiring."""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

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
                    command="echo test", transport="http", socket=str(tmp_path / "test.sock")
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
                    command="echo test", transport="http", socket=str(tmp_path / "test.sock")
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


@pytest.fixture()
def restart_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[McpPoolManager, Path]:
    """Real child process, optionally speaking enough MCP to initialize a bridge."""
    import marianne.daemon.mcp_pool as pool_module

    monkeypatch.setattr(pool_module, "_MONITOR_INTERVAL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(pool_module, "_RESTART_BACKOFF_SECONDS", 0.01, raising=False)
    script = tmp_path / "server.py"
    exit_file = tmp_path / "exit"
    script.write_text(
        "import json, sys, threading, time\n"
        "from pathlib import Path\n"
        "def serve():\n"
        "    for line in sys.stdin:\n"
        "        req = json.loads(line)\n"
        "        if 'id' in req:\n"
        "            response = {'jsonrpc':'2.0', 'id':req['id'], 'result':{}}\n"
        "            print(json.dumps(response), flush=True)\n"
        "threading.Thread(target=serve, daemon=True).start()\n"
        f"exit_file = Path({str(exit_file)!r})\n"
        "while not exit_file.exists(): time.sleep(0.005)\n"
        "code = int(exit_file.read_text())\n"
        "exit_file.unlink()\n"
        "sys.exit(code)\n"
    )
    manager = McpPoolManager(McpPoolConfig(servers={
        "t": McpServerEntry(
            command=shlex.join([sys.executable, str(script)]),
            socket=str(tmp_path / "s"), transport="http",
        ),
    }))
    return manager, exit_file


async def _wait_until(predicate: object) -> None:
    from collections.abc import Callable
    from typing import cast

    async with asyncio.timeout(5):
        while not cast(Callable[[], bool], predicate)():
            await asyncio.sleep(0.01)


async def _assert_stays(predicate: object, *, duration: float = 0.06) -> None:
    """Observe an invariant over several monitor polls, rather than sleeping blindly."""
    from collections.abc import Callable
    from typing import cast

    deadline = asyncio.get_running_loop().time() + duration
    while asyncio.get_running_loop().time() < deadline:
        assert cast(Callable[[], bool], predicate)()
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize(("policy", "exit_code", "restarts"), [
    ("on-failure", 1, True), ("always", 0, True), ("always", 1, True),
    ("never", 1, False), ("never", 0, False), ("on-failure", 0, False),
])
async def test_restart_policy(
    restart_pool: tuple[McpPoolManager, Path], transport: str,
    policy: str, exit_code: int, restarts: bool,
) -> None:
    manager, exit_file = restart_pool
    manager._config.servers["t"].restart_policy = policy
    manager._config.servers["t"].transport = transport
    try:
        await manager.start_all()
        process = manager._handles["t"].process
        assert process is not None
        if exit_code:
            process.kill()
        else:
            exit_file.write_text("0")
        await asyncio.wait_for(process.wait(), 5)
        # A dispatch liveness check must not swallow the supervisor's exit event.
        assert not manager.is_running("t")
        if restarts:
            await _wait_until(lambda: manager._handles["t"].process is not process
                              and manager.is_running("t"))
            config_path = manager.generate_mcp_config_file(exit_file.parent)
            assert config_path is not None
            assert manager.get_status()["t"] == McpServerState.RUNNING
            if transport == "stdio":
                reader, writer = await asyncio.open_unix_connection(str(exit_file.parent / "s"))
                try:
                    writer.write(b'{"jsonrpc":"2.0","id":7,"method":"initialize"}\n')
                    await writer.drain()
                    assert json.loads(await asyncio.wait_for(reader.readline(), 5))["id"] == 7
                finally:
                    writer.close()
                    await writer.wait_closed()
        else:
            await _assert_stays(lambda: manager._handles["t"].process is process)
            assert manager._handles["t"].process is process
            assert manager.get_status()["t"] == McpServerState.FAILED
            assert manager.generate_mcp_config_file(exit_file.parent) is None
    finally:
        await manager.stop_all()
    assert manager.get_status()["t"] == McpServerState.STOPPED


async def test_restart_failures_are_bounded(restart_pool: tuple[McpPoolManager, Path]) -> None:
    manager, _ = restart_pool
    try:
        await manager.start_all()
        process = manager._handles["t"].process
        assert process is not None
        with patch("marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
                   side_effect=OSError("restart unavailable")) as spawn:
            process.kill()
            await process.wait()
            await _wait_until(lambda: spawn.await_count >= 3)
            await _assert_stays(lambda: spawn.await_count == 3, duration=0.12)
            assert spawn.await_count == 3
            assert manager.get_status()["t"] == McpServerState.FAILED
    finally:
        await manager.stop_all()


async def test_restart_monitor_stops_before_shutdown(
    restart_pool: tuple[McpPoolManager, Path],
) -> None:
    manager, _ = restart_pool
    manager._config.servers["t"].restart_policy = "always"
    await manager.start_all()
    process = manager._handles["t"].process
    assert process is not None
    await manager.stop_all()
    assert process.returncode is not None
    await _assert_stays(lambda: manager.get_status()["t"] == McpServerState.STOPPED)
    assert manager.get_status()["t"] == McpServerState.STOPPED
    assert manager._monitor_task is None


def test_restart_policy_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError, match="restart_policy"):
        McpServerEntry(command="server", socket="/tmp/not-used", restart_policy="sometimes")


@pytest.mark.parametrize("operation", ["start", "stop"])
async def test_restart_lifecycle_serializes_startup(
    restart_pool: tuple[McpPoolManager, Path], operation: str,
) -> None:
    """Starting twice or stopping mid-spawn must never leave an unowned child."""
    manager, _ = restart_pool
    entered, release = asyncio.Event(), asyncio.Event()
    real_spawn = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def delayed_spawn(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        entered.set()
        await release.wait()
        process = await real_spawn(*args, **kwargs)
        children.append(process)
        return process

    with patch("marianne.daemon.mcp_pool.asyncio.create_subprocess_exec", delayed_spawn):
        first = asyncio.create_task(manager.start_all())
        await entered.wait()
        second = asyncio.create_task(
            manager.start_all() if operation == "start" else manager.stop_all(),
        )
        try:
            await asyncio.sleep(0)
            if operation == "stop":
                assert not second.done(), "shutdown must join the in-flight startup"
            release.set()
            await asyncio.gather(first, second)
            assert len(children) == 1, "concurrent start calls must share one server"
            if operation == "stop":
                assert children[0].returncode is not None
                assert not manager.is_running("t")
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
            await manager.stop_all()
            for process in children:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
