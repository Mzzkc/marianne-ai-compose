"""Tests for the shared MCP pool manager process lifecycle.

The MCP pool manages long-running MCP server processes for the conductor.
For stdio MCP servers, it exposes a multiplexed Unix socket bridge.

TDD: tests written before implementation.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.config import McpPoolConfig, McpServerEntry
from marianne.daemon.mcp_pool import McpPoolManager, McpServerState


def _write_framed(
    writer: asyncio.StreamWriter,
    payload: dict[str, object],
) -> None:
    body = json.dumps(payload).encode()
    writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)


async def _read_framed(reader: asyncio.StreamReader) -> dict[str, object]:
    header = await reader.readline()
    assert header.lower().startswith(b"content-length:")
    length = int(header.split(b":", 1)[1].strip())
    blank = await reader.readline()
    assert blank in (b"\r\n", b"\n")
    return json.loads((await reader.readexactly(length)).decode())

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def pool_config(tmp_path: Path) -> McpPoolConfig:
    """Create a McpPoolConfig with two test servers."""
    return McpPoolConfig(
        servers={
            "github": McpServerEntry(
                command="github-mcp-server",
                transport="http",
                socket=str(tmp_path / "github.sock"),
                restart_policy="on-failure",
            ),
            "filesystem": McpServerEntry(
                command="fs-mcp-server",
                transport="http",
                socket=str(tmp_path / "filesystem.sock"),
                restart_policy="never",
            ),
        },
    )


@pytest.fixture()
def empty_config() -> McpPoolConfig:
    """Empty pool with no servers."""
    return McpPoolConfig()


# =============================================================================
# Construction
# =============================================================================


class TestConstruction:
    """McpPoolManager can be created from config."""

    def test_creates_from_config(self, pool_config: McpPoolConfig) -> None:
        manager = McpPoolManager(pool_config)
        assert manager is not None

    def test_creates_with_empty_config(self, empty_config: McpPoolConfig) -> None:
        manager = McpPoolManager(empty_config)
        assert manager is not None

    def test_server_names(self, pool_config: McpPoolConfig) -> None:
        manager = McpPoolManager(pool_config)
        assert set(manager.server_names()) == {"github", "filesystem"}


# =============================================================================
# Start / Stop lifecycle
# =============================================================================


class TestLifecycle:
    """Server processes are started and stopped by the manager."""

    async def test_start_all_starts_servers(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        with patch(
            "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await manager.start_all()

        # Both servers should be tracked
        assert manager.is_running("github")
        assert manager.is_running("filesystem")

    async def test_stop_all_terminates_servers(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        with patch(
            "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await manager.start_all()

        await manager.stop_all()

        assert not manager.is_running("github")
        assert not manager.is_running("filesystem")

    async def test_start_empty_pool_is_noop(
        self,
        empty_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(empty_config)
        await manager.start_all()  # should not raise
        await manager.stop_all()

    async def test_stdio_pool_creates_socket_bridge_and_multiplexes_clients(
        self,
        tmp_path: Path,
    ) -> None:
        """Two clients can share one stdio MCP server with colliding IDs."""
        server_script = tmp_path / "mcp_server.py"
        request_log = tmp_path / "request_ids.jsonl"
        server_script.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    f"LOG = {str(request_log)!r}",
                    "def respond(payload):",
                    "    sys.stdout.write(json.dumps(payload) + '\\n')",
                    "    sys.stdout.flush()",
                    "for line in sys.stdin:",
                    "    if not line.strip():",
                    "        continue",
                    "    req = json.loads(line)",
                    "    if 'id' not in req:",
                    "        continue",
                    "    with open(LOG, 'a', encoding='utf-8') as fh:",
                    "        entry = {'method': req.get('method'), 'id': req.get('id')}",
                    "        fh.write(json.dumps(entry) + '\\n')",
                    "    method = req.get('method')",
                    "    if method == 'initialize':",
                    "        result = {'protocolVersion': '2025-11-25'}",
                    "        result['capabilities'] = {'tools': {}}",
                    "        result['serverInfo'] = {'name': 'test', 'version': '1'}",
                    "        respond({'jsonrpc': '2.0', 'id': req['id'], 'result': result})",
                    "    elif method == 'tools/call':",
                    "        args = req.get('params', {}).get('arguments', {})",
                    "        content = [{'type': 'text', 'text': args.get('message', '')}]",
                    "        result = {'content': content}",
                    "        respond({'jsonrpc': '2.0', 'id': req['id'], 'result': result})",
                    "    elif method == 'tools/list':",
                    "        respond({'jsonrpc': '2.0', 'id': req['id'], 'result': {'tools': []}})",
                    "    else:",
                    "        respond({'jsonrpc': '2.0', 'id': req['id'], 'result': {}})",
                ]
            )
        )
        socket_path = tmp_path / "server.sock"
        manager = McpPoolManager(
            McpPoolConfig(
                servers={
                    "test": McpServerEntry(
                        command=f"{sys.executable} {server_script}",
                        transport="stdio",
                        socket=str(socket_path),
                    )
                }
            )
        )

        try:
            await manager.start_all()
            assert manager.is_running("test")
            assert socket_path.exists()

            async def call_client(message: str) -> dict[str, object]:
                reader, writer = await asyncio.open_unix_connection(str(socket_path))
                try:
                    _write_framed(
                        writer,
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {},
                        },
                    )
                    init_response = await _read_framed(reader)
                    assert init_response["id"] == 1

                    _write_framed(
                        writer,
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "echo",
                                "arguments": {"message": message},
                            },
                        },
                    )
                    return await _read_framed(reader)
                finally:
                    writer.close()
                    await writer.wait_closed()

            first, second = await asyncio.gather(
                call_client("first"),
                call_client("second"),
            )
            assert first["id"] == 1
            assert second["id"] == 1
            assert first["result"]["content"][0]["text"] == "first"  # type: ignore[index]
            assert second["result"]["content"][0]["text"] == "second"  # type: ignore[index]

            logged = [
                json.loads(line)
                for line in request_log.read_text().splitlines()
            ]
            tool_ids = [
                entry["id"]
                for entry in logged
                if entry["method"] == "tools/call"
            ]
            assert len(tool_ids) == 2
            assert len(set(tool_ids)) == 2
            assert 1 not in tool_ids
        finally:
            await manager.stop_all()

    async def test_stop_all_is_idempotent(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        with patch(
            "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await manager.start_all()

        await manager.stop_all()
        await manager.stop_all()  # should not raise


# =============================================================================
# Server state tracking
# =============================================================================


class TestServerState:
    """Server state is tracked correctly."""

    def test_initial_state_is_stopped(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)
        assert not manager.is_running("github")
        assert not manager.is_running("filesystem")

    def test_unknown_server_not_running(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)
        assert not manager.is_running("nonexistent")

    def test_server_state_enum(self) -> None:
        assert McpServerState.STOPPED.value == "stopped"
        assert McpServerState.RUNNING.value == "running"
        assert McpServerState.FAILED.value == "failed"

    async def test_get_status_returns_all_servers(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)
        status = manager.get_status()
        assert "github" in status
        assert "filesystem" in status
        assert status["github"] == McpServerState.STOPPED
        assert status["filesystem"] == McpServerState.STOPPED


# =============================================================================
# Process failure handling
# =============================================================================


class TestFailureHandling:
    """Process failures are detected and handled per restart policy."""

    async def test_start_failure_marks_server_failed(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)

        with patch(
            "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("command not found"),
        ):
            await manager.start_all()

        # Servers should be marked as failed, not running
        status = manager.get_status()
        assert status["github"] == McpServerState.FAILED
        assert status["filesystem"] == McpServerState.FAILED

    async def test_stdio_bridge_start_failure_does_not_stop_other_servers(
        self,
        tmp_path: Path,
    ) -> None:
        manager = McpPoolManager(
            McpPoolConfig(
                servers={
                    "broken": McpServerEntry(
                        command=f"{sys.executable} missing.py",
                        transport="stdio",
                        socket=str(tmp_path / "broken.sock"),
                    ),
                    "healthy": McpServerEntry(
                        command="healthy-server",
                        transport="http",
                        socket=str(tmp_path / "healthy.sock"),
                    ),
                }
            )
        )
        mock_proc = AsyncMock()
        mock_proc.pid = 45678
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        with (
            patch(
                "marianne.daemon.mcp_pool.McpSocketBridge.start",
                side_effect=RuntimeError("initialize failed"),
            ),
            patch(
                "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ) as create_subprocess,
        ):
            await manager.start_all()

        status = manager.get_status()
        assert status["broken"] == McpServerState.FAILED
        assert status["healthy"] == McpServerState.RUNNING
        create_subprocess.assert_awaited_once_with(
            "healthy-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_stop_handles_already_dead_process(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 1  # Already exited
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock(return_value=1)

        with patch(
            "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await manager.start_all()

        # Should not raise even though process already exited
        await manager.stop_all()


# =============================================================================
# Socket path management
# =============================================================================


class TestSocketPaths:
    """Socket paths are managed correctly."""

    def test_get_socket_path(
        self,
        pool_config: McpPoolConfig,
        tmp_path: Path,
    ) -> None:
        manager = McpPoolManager(pool_config)
        assert manager.get_socket_path("github") == Path(str(tmp_path / "github.sock"))

    def test_get_socket_path_unknown_server(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)
        assert manager.get_socket_path("nonexistent") is None

    def test_get_all_socket_paths(
        self,
        pool_config: McpPoolConfig,
        tmp_path: Path,
    ) -> None:
        manager = McpPoolManager(pool_config)
        paths = manager.get_all_socket_paths()
        assert len(paths) == 2
        assert "github" in paths
        assert "filesystem" in paths


# =============================================================================
# Health check
# =============================================================================


class TestHealthCheck:
    """Health checks verify server processes are alive."""

    async def test_health_check_running_server(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None  # Still running
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        with patch(
            "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await manager.start_all()

        assert await manager.health_check("github")

    async def test_health_check_dead_server(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        with patch(
            "marianne.daemon.mcp_pool.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await manager.start_all()

        # Simulate process death
        mock_proc.returncode = 1

        assert not await manager.health_check("github")

    async def test_health_check_unknown_server(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)
        assert not await manager.health_check("nonexistent")

    async def test_health_check_not_started(
        self,
        pool_config: McpPoolConfig,
    ) -> None:
        manager = McpPoolManager(pool_config)
        assert not await manager.health_check("github")
