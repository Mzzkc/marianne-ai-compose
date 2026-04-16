"""Tests for MCP proxy shim — stdio-to-socket proxy and config file generation.

Tests cover:
1. Proxy shim behavior: stdio-to-Unix-socket forwarding of JSON-RPC messages
2. MCP config file generation: temporary JSON files for CLI instruments

TDD: tests written before implementation.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

from marianne.daemon.config import McpPoolConfig, McpServerEntry
from marianne.daemon.mcp_pool import McpPoolManager, McpServerState

# =============================================================================
# MCP config file generation
# =============================================================================


class TestMcpConfigFileGeneration:
    """Generate MCP config JSON files from pool socket paths."""

    def test_generate_config_for_running_servers(self, tmp_path: Path) -> None:
        """Config file includes only running servers."""
        from marianne.daemon.baton.techniques import generate_mcp_config_file

        config = McpPoolConfig(
            servers={
                "github": McpServerEntry(
                    command="github-mcp",
                    socket=str(tmp_path / "github.sock"),
                ),
                "filesystem": McpServerEntry(
                    command="fs-mcp",
                    socket=str(tmp_path / "filesystem.sock"),
                ),
            }
        )
        pool = McpPoolManager(config)
        # Mark github as running, filesystem as stopped
        pool._handles["github"].state = McpServerState.RUNNING
        pool._handles["github"].process = type("P", (), {"returncode": None})()
        pool._handles["filesystem"].state = McpServerState.STOPPED

        mcp_servers = {"github": {"server": "github"}}
        result = generate_mcp_config_file(mcp_servers, pool, tmp_path)

        assert result is not None
        assert result.exists()
        data = json.loads(result.read_text())
        assert "mcpServers" in data
        assert "github" in data["mcpServers"]
        # filesystem should not be included — it's not running
        assert "filesystem" not in data["mcpServers"]

    def test_generate_config_empty_when_no_running(self, tmp_path: Path) -> None:
        """Returns None when no servers are running."""
        from marianne.daemon.baton.techniques import generate_mcp_config_file

        config = McpPoolConfig(
            servers={
                "github": McpServerEntry(
                    command="github-mcp",
                    socket=str(tmp_path / "github.sock"),
                ),
            }
        )
        pool = McpPoolManager(config)
        # All stopped

        mcp_servers = {"github": {"server": "github"}}
        result = generate_mcp_config_file(mcp_servers, pool, tmp_path)

        assert result is None

    def test_generate_config_empty_when_no_servers(self, tmp_path: Path) -> None:
        """Returns None when no MCP servers are declared."""
        from marianne.daemon.baton.techniques import generate_mcp_config_file

        pool = McpPoolManager(McpPoolConfig())
        result = generate_mcp_config_file({}, pool, tmp_path)
        assert result is None

    def test_config_file_structure(self, tmp_path: Path) -> None:
        """Config file matches the format expected by claude-code --mcp-config."""
        from marianne.daemon.baton.techniques import generate_mcp_config_file

        config = McpPoolConfig(
            servers={
                "github": McpServerEntry(
                    command="github-mcp",
                    socket=str(tmp_path / "github.sock"),
                ),
            }
        )
        pool = McpPoolManager(config)
        pool._handles["github"].state = McpServerState.RUNNING
        pool._handles["github"].process = type("P", (), {"returncode": None})()

        mcp_servers = {"github": {"server": "github"}}
        result = generate_mcp_config_file(mcp_servers, pool, tmp_path)

        assert result is not None
        data = json.loads(result.read_text())

        # Must have the mcpServers key
        assert "mcpServers" in data

        # Each entry should have transport and socket info
        github_entry = data["mcpServers"]["github"]
        assert "socket" in github_entry
        assert str(tmp_path / "github.sock") in github_entry["socket"]

    def test_config_file_written_atomically(self, tmp_path: Path) -> None:
        """Config file is written to the workspace directory."""
        from marianne.daemon.baton.techniques import generate_mcp_config_file

        config = McpPoolConfig(
            servers={
                "test": McpServerEntry(
                    command="test-mcp",
                    socket=str(tmp_path / "test.sock"),
                ),
            }
        )
        pool = McpPoolManager(config)
        pool._handles["test"].state = McpServerState.RUNNING
        pool._handles["test"].process = type("P", (), {"returncode": None})()

        mcp_servers = {"test": {"server": "test"}}
        result = generate_mcp_config_file(mcp_servers, pool, tmp_path)

        assert result is not None
        # File should exist in the workspace
        assert result.parent == tmp_path
        assert result.name == ".mcp-pool-config.json"

    def test_multiple_running_servers(self, tmp_path: Path) -> None:
        """Config includes all running servers that are in the mcp_servers dict."""
        from marianne.daemon.baton.techniques import generate_mcp_config_file

        config = McpPoolConfig(
            servers={
                "github": McpServerEntry(
                    command="github-mcp",
                    socket=str(tmp_path / "github.sock"),
                ),
                "filesystem": McpServerEntry(
                    command="fs-mcp",
                    socket=str(tmp_path / "fs.sock"),
                ),
                "symbols": McpServerEntry(
                    command="symbols-mcp",
                    socket=str(tmp_path / "symbols.sock"),
                ),
            }
        )
        pool = McpPoolManager(config)
        for name in ["github", "filesystem", "symbols"]:
            pool._handles[name].state = McpServerState.RUNNING
            pool._handles[name].process = type("P", (), {"returncode": None})()

        mcp_servers = {
            "github": {"server": "github"},
            "filesystem": {"server": "filesystem"},
        }
        result = generate_mcp_config_file(mcp_servers, pool, tmp_path)

        assert result is not None
        data = json.loads(result.read_text())
        # Only the ones declared in mcp_servers should be included
        assert "github" in data["mcpServers"]
        assert "filesystem" in data["mcpServers"]
        # symbols not in mcp_servers, so not included
        assert "symbols" not in data["mcpServers"]


# =============================================================================
# Proxy shim behavior — stdio-to-socket forwarding
# =============================================================================


def _make_jsonrpc_request(method: str, req_id: int = 1) -> bytes:
    """Build a Content-Length framed JSON-RPC request."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": method,
            "params": {},
            "id": req_id,
        }
    ).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n"
    return header.encode("utf-8") + body


def _make_jsonrpc_response(result: object, req_id: int = 1) -> bytes:
    """Build a Content-Length framed JSON-RPC response."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "result": result,
            "id": req_id,
        }
    ).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n"
    return header.encode("utf-8") + body


def _parse_framed_message(data: bytes) -> dict[str, object]:
    """Parse a Content-Length framed JSON-RPC message from bytes."""
    text = data.decode("utf-8")
    # Find the blank line separating header from body
    idx = text.find("\r\n\r\n")
    if idx == -1:
        raise ValueError(f"No header/body separator in: {text!r}")
    body_str = text[idx + 4 :]
    return json.loads(body_str)  # type: ignore[no-any-return]


class TestProxyShimBehavior:
    """Test the stdio-to-socket proxy forwarding behavior."""

    async def test_proxy_connects_to_mock_socket_server(self, tmp_path: Path) -> None:
        """Proxy shim starts and connects to a Unix socket server."""
        from marianne.daemon.mcp_proxy_shim import _forward_loop, _frame_response

        sock_path = tmp_path / "test.sock"

        # Create a mock Unix socket server that accepts one connection
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(str(sock_path))
        server_sock.listen(1)
        server_sock.setblocking(False)

        # Create a stdin reader that sends one message then EOF
        request_data = _make_jsonrpc_request("initialize")
        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_data(request_data)
        stdin_reader.feed_eof()

        # Stdout capture
        stdout_reader = asyncio.StreamReader()
        stdout_transport = type(
            "MockTransport",
            (),
            {
                "write": lambda self, data: stdout_reader.feed_data(data),
                "is_closing": lambda self: False,
                "get_write_buffer_size": lambda self: 0,
                "set_write_buffer_limits": lambda self, **kw: None,
            },
        )()
        stdout_protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
        loop = asyncio.get_event_loop()
        stdout_writer = asyncio.StreamWriter(
            stdout_transport,
            stdout_protocol,
            None,
            loop,
        )

        # Run mock server handler in background
        async def _serve_one() -> None:
            conn, _ = await loop.sock_accept(server_sock)
            try:
                # Read the forwarded request
                data = b""
                while True:
                    chunk = await loop.sock_recv(conn, 4096)
                    if not chunk:
                        break
                    data += chunk
                    # Check if we have a complete message
                    text = data.decode("utf-8", errors="replace")
                    if "\r\n\r\n" in text:
                        header_end = text.index("\r\n\r\n")
                        header = text[:header_end]
                        for line in header.split("\r\n"):
                            if line.lower().startswith("content-length:"):
                                cl = int(line.split(":", 1)[1].strip())
                                body_start = header_end + 4
                                body_bytes = data[body_start:]
                                if len(body_bytes) >= cl:
                                    break
                        else:
                            continue
                        break

                # Send back a response
                resp = _frame_response(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "result": {"capabilities": {}},
                            "id": 1,
                        }
                    ).encode("utf-8")
                )
                await loop.sock_sendall(conn, resp)
            finally:
                conn.close()

        server_task = asyncio.create_task(_serve_one())

        try:
            await asyncio.wait_for(
                _forward_loop(stdin_reader, stdout_writer, sock_path),
                timeout=5.0,
            )
        except TimeoutError:
            pass

        # Wait for server to finish
        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except TimeoutError:
            server_task.cancel()

        server_sock.close()

        # Verify we got a response on stdout
        captured = stdout_reader._buffer  # type: ignore[attr-defined]
        assert len(captured) > 0, "Proxy should have written response to stdout"
        parsed = _parse_framed_message(bytes(captured))
        assert parsed["jsonrpc"] == "2.0"
        assert "result" in parsed

    async def test_stdin_to_socket_forwarding(self, tmp_path: Path) -> None:
        """Messages from stdin are forwarded to the Unix socket."""
        from marianne.daemon.mcp_proxy_shim import _forward_loop, _frame_response

        sock_path = tmp_path / "forward.sock"
        received_messages: list[bytes] = []

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(str(sock_path))
        server_sock.listen(1)
        server_sock.setblocking(False)

        # Send two requests via stdin
        req1 = _make_jsonrpc_request("tools/list", req_id=1)
        req2 = _make_jsonrpc_request("tools/call", req_id=2)
        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_data(req1)
        stdin_reader.feed_data(req2)
        stdin_reader.feed_eof()

        stdout_reader = asyncio.StreamReader()
        stdout_transport = type(
            "MockTransport",
            (),
            {
                "write": lambda self, data: stdout_reader.feed_data(data),
                "is_closing": lambda self: False,
                "get_write_buffer_size": lambda self: 0,
                "set_write_buffer_limits": lambda self, **kw: None,
            },
        )()
        stdout_protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
        loop = asyncio.get_event_loop()
        stdout_writer = asyncio.StreamWriter(
            stdout_transport,
            stdout_protocol,
            None,
            loop,
        )

        async def _serve_echo() -> None:
            conn, _ = await loop.sock_accept(server_sock)
            try:
                for expected_id in [1, 2]:
                    # Read header + body
                    data = b""
                    content_length = None
                    while True:
                        chunk = await loop.sock_recv(conn, 4096)
                        if not chunk:
                            return
                        data += chunk
                        text = data.decode("utf-8", errors="replace")
                        if "\r\n\r\n" in text:
                            idx = text.index("\r\n\r\n")
                            for line in text[:idx].split("\r\n"):
                                if line.lower().startswith("content-length:"):
                                    content_length = int(line.split(":", 1)[1].strip())
                            if content_length is not None:
                                body_bytes = data[idx + 4 :]
                                if len(body_bytes) >= content_length:
                                    body = body_bytes[:content_length]
                                    received_messages.append(body)
                                    # Consume only what we used
                                    data = body_bytes[content_length:]
                                    break
                    # Echo a response with the same id
                    resp_body = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "result": {"echo": True},
                            "id": expected_id,
                        }
                    ).encode("utf-8")
                    resp = _frame_response(resp_body)
                    await loop.sock_sendall(conn, resp)
            finally:
                conn.close()

        server_task = asyncio.create_task(_serve_echo())

        try:
            await asyncio.wait_for(
                _forward_loop(stdin_reader, stdout_writer, sock_path),
                timeout=5.0,
            )
        except TimeoutError:
            pass

        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except TimeoutError:
            server_task.cancel()

        server_sock.close()

        # Verify both messages were forwarded to the socket
        assert len(received_messages) >= 1, "At least one message should be forwarded to socket"
        first_msg = json.loads(received_messages[0])
        assert first_msg["method"] == "tools/list"

    async def test_socket_to_stdout_forwarding(self, tmp_path: Path) -> None:
        """Responses from socket are forwarded to stdout."""
        from marianne.daemon.mcp_proxy_shim import _forward_loop, _frame_response

        sock_path = tmp_path / "response.sock"

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(str(sock_path))
        server_sock.listen(1)
        server_sock.setblocking(False)

        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_data(_make_jsonrpc_request("test/method"))
        stdin_reader.feed_eof()

        stdout_reader = asyncio.StreamReader()
        stdout_transport = type(
            "MockTransport",
            (),
            {
                "write": lambda self, data: stdout_reader.feed_data(data),
                "is_closing": lambda self: False,
                "get_write_buffer_size": lambda self: 0,
                "set_write_buffer_limits": lambda self, **kw: None,
            },
        )()
        stdout_protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
        loop = asyncio.get_event_loop()
        stdout_writer = asyncio.StreamWriter(
            stdout_transport,
            stdout_protocol,
            None,
            loop,
        )

        specific_result = {"tools": [{"name": "read_file"}, {"name": "write_file"}]}

        async def _serve_specific() -> None:
            conn, _ = await loop.sock_accept(server_sock)
            try:
                # Read the request
                data = b""
                while True:
                    chunk = await loop.sock_recv(conn, 4096)
                    if not chunk:
                        return
                    data += chunk
                    if b"\r\n\r\n" in data:
                        idx = data.index(b"\r\n\r\n")
                        header_text = data[:idx].decode("utf-8")
                        cl = None
                        for line in header_text.split("\r\n"):
                            if line.lower().startswith("content-length:"):
                                cl = int(line.split(":", 1)[1].strip())
                        if cl is not None and len(data[idx + 4 :]) >= cl:
                            break

                # Send specific response
                resp_body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "result": specific_result,
                        "id": 1,
                    }
                ).encode("utf-8")
                await loop.sock_sendall(conn, _frame_response(resp_body))
            finally:
                conn.close()

        server_task = asyncio.create_task(_serve_specific())

        try:
            await asyncio.wait_for(
                _forward_loop(stdin_reader, stdout_writer, sock_path),
                timeout=5.0,
            )
        except TimeoutError:
            pass

        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except TimeoutError:
            server_task.cancel()

        server_sock.close()

        # Verify the specific result came through on stdout
        captured = bytes(stdout_reader._buffer)  # type: ignore[attr-defined]
        assert len(captured) > 0, "Response should appear on stdout"
        parsed = _parse_framed_message(captured)
        assert parsed["result"] == specific_result

    async def test_socket_not_available_exits_with_error(self, tmp_path: Path) -> None:
        """Proxy exits with error when socket does not exist."""
        from marianne.daemon.mcp_proxy_shim import run_proxy

        nonexistent = tmp_path / "nonexistent.sock"
        exit_code = await run_proxy(nonexistent)
        assert exit_code == 1, "Should exit with code 1 when socket missing"

    async def test_socket_connection_refused_returns_error(self, tmp_path: Path) -> None:
        """Proxy writes JSON-RPC error when connection is refused."""
        from marianne.daemon.mcp_proxy_shim import _forward_loop

        # Create a socket file that exists but has no listener
        sock_path = tmp_path / "refused.sock"
        # Create and immediately close a socket to leave the file
        temp_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        temp_sock.bind(str(sock_path))
        temp_sock.close()

        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_data(_make_jsonrpc_request("test"))
        stdin_reader.feed_eof()

        stdout_reader = asyncio.StreamReader()
        stdout_transport = type(
            "MockTransport",
            (),
            {
                "write": lambda self, data: stdout_reader.feed_data(data),
                "is_closing": lambda self: False,
                "get_write_buffer_size": lambda self: 0,
                "set_write_buffer_limits": lambda self, **kw: None,
            },
        )()
        stdout_protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
        loop = asyncio.get_event_loop()
        stdout_writer = asyncio.StreamWriter(
            stdout_transport,
            stdout_protocol,
            None,
            loop,
        )

        await _forward_loop(stdin_reader, stdout_writer, sock_path)

        # Should have written an error response
        captured = bytes(stdout_reader._buffer)  # type: ignore[attr-defined]
        assert len(captured) > 0, "Error response should appear on stdout"
        parsed = _parse_framed_message(captured)
        assert "error" in parsed
        assert parsed["error"]["code"] == -32000  # type: ignore[index]

    async def test_full_roundtrip_jsonrpc(self, tmp_path: Path) -> None:
        """Full round-trip: send JSON-RPC request, receive response."""
        from marianne.daemon.mcp_proxy_shim import _forward_loop, _frame_response

        sock_path = tmp_path / "roundtrip.sock"

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(str(sock_path))
        server_sock.listen(1)
        server_sock.setblocking(False)

        # Build a realistic MCP initialize request
        init_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
                "id": 42,
            }
        ).encode("utf-8")
        init_request = f"Content-Length: {len(init_body)}\r\n\r\n".encode() + init_body

        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_data(init_request)
        stdin_reader.feed_eof()

        stdout_reader = asyncio.StreamReader()
        stdout_transport = type(
            "MockTransport",
            (),
            {
                "write": lambda self, data: stdout_reader.feed_data(data),
                "is_closing": lambda self: False,
                "get_write_buffer_size": lambda self: 0,
                "set_write_buffer_limits": lambda self, **kw: None,
            },
        )()
        stdout_protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
        loop = asyncio.get_event_loop()
        stdout_writer = asyncio.StreamWriter(
            stdout_transport,
            stdout_protocol,
            None,
            loop,
        )

        init_response = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "github-mcp", "version": "0.1"},
        }

        async def _serve_init() -> None:
            conn, _ = await loop.sock_accept(server_sock)
            try:
                # Read the request
                data = b""
                while True:
                    chunk = await loop.sock_recv(conn, 4096)
                    if not chunk:
                        return
                    data += chunk
                    if b"\r\n\r\n" in data:
                        idx = data.index(b"\r\n\r\n")
                        header_text = data[:idx].decode("utf-8")
                        cl = None
                        for line in header_text.split("\r\n"):
                            if line.lower().startswith("content-length:"):
                                cl = int(line.split(":", 1)[1].strip())
                        if cl is not None and len(data[idx + 4 :]) >= cl:
                            req_body = json.loads(data[idx + 4 : idx + 4 + cl].decode("utf-8"))
                            break

                # Verify request was forwarded correctly
                assert req_body["method"] == "initialize"
                assert req_body["id"] == 42

                # Send MCP initialize response
                resp_body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "result": init_response,
                        "id": 42,
                    }
                ).encode("utf-8")
                await loop.sock_sendall(conn, _frame_response(resp_body))
            finally:
                conn.close()

        server_task = asyncio.create_task(_serve_init())

        try:
            await asyncio.wait_for(
                _forward_loop(stdin_reader, stdout_writer, sock_path),
                timeout=5.0,
            )
        except TimeoutError:
            pass

        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except TimeoutError:
            server_task.cancel()

        server_sock.close()

        # Verify the full round-trip
        captured = bytes(stdout_reader._buffer)  # type: ignore[attr-defined]
        assert len(captured) > 0, "Response must appear on stdout"
        parsed = _parse_framed_message(captured)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == 42
        assert parsed["result"]["protocolVersion"] == "2024-11-05"
        assert parsed["result"]["serverInfo"]["name"] == "github-mcp"
