"""Tests for the MCP proxy shim and MCP config file generation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from marianne.daemon.config import McpPoolConfig, McpServerEntry
from marianne.daemon.mcp_pool import McpPoolManager, McpServerState


class TestMcpConfigFileGeneration:
    def _make_pool(self, tmp_path: Path) -> McpPoolManager:
        config = McpPoolConfig(
            servers={
                "github": McpServerEntry(
                    command="github-mcp-server",
                    transport="stdio",
                    socket=str(tmp_path / "github.sock"),
                    restart_policy="on-failure",
                ),
                "filesystem": McpServerEntry(
                    command="fs-mcp-server",
                    transport="stdio",
                    socket=str(tmp_path / "filesystem.sock"),
                    restart_policy="never",
                ),
            }
        )
        return McpPoolManager(config)

    def test_generate_config_empty_when_no_servers(self, tmp_path: Path) -> None:
        pool = McpPoolManager(McpPoolConfig())
        assert pool.generate_mcp_config_file(tmp_path) is None

    def test_generate_config_empty_when_no_running(self, tmp_path: Path) -> None:
        pool = self._make_pool(tmp_path)
        assert pool.generate_mcp_config_file(tmp_path) is None

    def test_generate_config_for_running_servers(self, tmp_path: Path) -> None:
        pool = self._make_pool(tmp_path)
        pool._handles["github"].state = McpServerState.RUNNING
        pool._handles["github"].process = MagicMock(returncode=None)
        result = pool.generate_mcp_config_file(tmp_path)
        assert result is not None and result.exists()
        data = json.loads(result.read_text())
        assert "github" in data["mcpServers"]
        assert "filesystem" not in data["mcpServers"]

    def test_config_file_structure(self, tmp_path: Path) -> None:
        pool = self._make_pool(tmp_path)
        pool._handles["github"].state = McpServerState.RUNNING
        pool._handles["github"].process = MagicMock(returncode=None)
        result = pool.generate_mcp_config_file(tmp_path)
        assert result is not None
        server = json.loads(result.read_text())["mcpServers"]["github"]
        assert "command" in server and "args" in server
        assert any("mcp_proxy_shim" in str(a) for a in server["args"])

    def test_multiple_running_servers(self, tmp_path: Path) -> None:
        pool = self._make_pool(tmp_path)
        for name in ["github", "filesystem"]:
            pool._handles[name].state = McpServerState.RUNNING
            pool._handles[name].process = MagicMock(returncode=None)
        result = pool.generate_mcp_config_file(tmp_path)
        assert result is not None
        data = json.loads(result.read_text())
        assert "github" in data["mcpServers"] and "filesystem" in data["mcpServers"]

    def test_config_file_written_atomically(self, tmp_path: Path) -> None:
        pool = self._make_pool(tmp_path)
        pool._handles["github"].state = McpServerState.RUNNING
        pool._handles["github"].process = MagicMock(returncode=None)
        result = pool.generate_mcp_config_file(tmp_path)
        assert result is not None
        assert not result.with_suffix(".tmp").exists()
        json.loads(result.read_text())


class TestProxyShimBehavior:
    async def test_proxy_connects_to_mock_socket_server(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "test.sock"
        received: list[bytes] = []

        async def handle_client(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            data = await r.read(4096)
            received.append(data)
            w.write(b'{"jsonrpc":"2.0","result":"ok","id":1}\n')
            await w.drain()
            w.close()
            await w.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(socket_path))
        try:
            r, w = await asyncio.open_unix_connection(str(socket_path))
            w.write(b'{"jsonrpc":"2.0","method":"test","id":1}\n')
            await w.drain()
            w.write_eof()
            response = await asyncio.wait_for(r.read(4096), timeout=5.0)
            w.close()
            await w.wait_closed()
            assert len(received) == 1 and b"result" in response
        finally:
            server.close()
            await server.wait_closed()

    async def test_stdin_to_socket_forwarding(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "forward.sock"
        received: list[bytes] = []

        async def handle_client(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            received.append(await r.read(4096))
            w.close()
            await w.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(socket_path))
        try:
            r, w = await asyncio.open_unix_connection(str(socket_path))
            w.write(b'{"jsonrpc":"2.0","method":"ping","id":42}\n')
            await w.drain()
            w.close()
            await w.wait_closed()
            await asyncio.sleep(0.1)
        finally:
            server.close()
            await server.wait_closed()
        assert len(received) == 1 and b"ping" in received[0]

    async def test_socket_to_stdout_forwarding(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "response.sock"
        response_data = b'{"jsonrpc":"2.0","result":{"tools":[]},"id":1}\n'

        async def handle_client(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            await r.read(4096)
            w.write(response_data)
            await w.drain()
            w.close()
            await w.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(socket_path))
        try:
            r, w = await asyncio.open_unix_connection(str(socket_path))
            w.write(b'{"jsonrpc":"2.0","method":"list","id":1}\n')
            await w.drain()
            w.write_eof()
            response = await asyncio.wait_for(r.read(4096), timeout=5.0)
            w.close()
            await w.wait_closed()
            assert response == response_data
        finally:
            server.close()
            await server.wait_closed()

    async def test_socket_not_available_exits_with_error(self, tmp_path: Path) -> None:
        from marianne.daemon.mcp_proxy_shim import run_proxy

        assert await run_proxy(str(tmp_path / "nonexistent.sock")) == 1

    async def test_socket_connection_refused_returns_error(self, tmp_path: Path) -> None:
        from marianne.daemon.mcp_proxy_shim import run_proxy

        fake_sock = tmp_path / "fake.sock"
        fake_sock.write_text("not a socket")
        assert await run_proxy(str(fake_sock)) == 1

    async def test_full_roundtrip_jsonrpc(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "roundtrip.sock"

        async def echo_server(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            data = await r.read(4096)
            try:
                req = json.loads(data.decode())
                resp = {
                    "jsonrpc": "2.0",
                    "result": {"method_was": req.get("method")},
                    "id": req.get("id"),
                }
                w.write(json.dumps(resp).encode() + b"\n")
                await w.drain()
            except Exception:
                pass
            w.close()
            await w.wait_closed()

        server = await asyncio.start_unix_server(echo_server, path=str(socket_path))
        try:
            r, w = await asyncio.open_unix_connection(str(socket_path))
            request = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
            w.write(json.dumps(request).encode() + b"\n")
            await w.drain()
            w.write_eof()
            raw = await asyncio.wait_for(r.read(4096), timeout=5.0)
            w.close()
            await w.wait_closed()
            response = json.loads(raw.decode())
            assert response["id"] == 1 and response["result"]["method_was"] == "tools/list"
        finally:
            server.close()
            await server.wait_closed()
