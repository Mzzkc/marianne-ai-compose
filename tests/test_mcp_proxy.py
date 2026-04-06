"""Tests for MCPProxyService.

Verifies MCP proxy functionality including:
- Server lifecycle (start/stop)
- JSON-RPC communication
- Tool discovery and caching
- Tool execution
- Error handling
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.bridge.mcp_proxy import (
    ContentBlock,
    MCPConnection,
    MCPProxyService,
    MCPTool,
    ServerCapabilities,
    ToolExecutionTimeout,
    ToolNotFoundError,
    ToolResult,
)
from marianne.core.config import MCPServerConfig

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_mcp_server_config():
    """Sample MCP server configuration."""
    return MCPServerConfig(
        name="test-server",
        command="node",
        args=["test-server.js"],
        env={"TEST_VAR": "value"},
        timeout_seconds=30.0,
    )


@pytest.fixture
def sample_tools_list_response():
    """Sample tools/list JSON-RPC response."""
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read contents of a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path"},
                        },
                        "required": ["path"],
                    },
                },
                {
                    "name": "write_file",
                    "description": "Write to a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            ]
        },
    }


@pytest.fixture
def sample_initialize_response():
    """Sample initialize JSON-RPC response."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "capabilities": {
                "tools": {},
                "resources": {},
            },
            "serverInfo": {
                "name": "test-server",
                "version": "1.0.0",
            },
        },
    }


@pytest.fixture
def sample_tool_call_response():
    """Sample tools/call JSON-RPC response.

    Note: id=1 because MCPProxyService._request_id starts at 0 and is
    incremented to 1 before the first request.
    """
    return {
        "jsonrpc": "2.0",
        "id": 1,  # Must match first request ID
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "Hello World",
                }
            ],
            "isError": False,
        },
    }


@pytest.fixture
def sample_tool_error_response():
    """Sample tools/call error response.

    Note: id=1 because MCPProxyService._request_id starts at 0 and is
    incremented to 1 before the first request.
    """
    return {
        "jsonrpc": "2.0",
        "id": 1,  # Must match first request ID
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "File not found: /nonexistent",
                }
            ],
            "isError": True,
        },
    }


@pytest.fixture
def mock_subprocess():
    """Create mock subprocess with stdin/stdout."""
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode = None

    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()

    stdout = AsyncMock()

    process.stdin = stdin
    process.stdout = stdout
    process.stderr = MagicMock()
    process.wait = AsyncMock()
    process.terminate = MagicMock()
    process.kill = MagicMock()

    return process


# =============================================================================
# Test ContentBlock
# =============================================================================


class TestContentBlock:
    """Tests for ContentBlock dataclass."""

    def test_text_content(self):
        """Test text content block."""
        block = ContentBlock(type="text", text="Hello World")
        assert block.type == "text"
        assert block.text == "Hello World"

    def test_image_content(self):
        """Test image content block."""
        block = ContentBlock(
            type="image",
            data="base64data==",
            mime_type="image/png",
        )
        assert block.type == "image"
        assert block.data == "base64data=="

    def test_resource_content(self):
        """Test resource content block."""
        block = ContentBlock(
            type="resource",
            uri="file:///tmp/test.txt",
        )
        assert block.type == "resource"
        assert block.uri == "file:///tmp/test.txt"


# =============================================================================
# Test ToolResult
# =============================================================================


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result(self):
        """Test successful tool result."""
        result = ToolResult(
            content=[ContentBlock(type="text", text="Success")],
            is_error=False,
        )
        assert result.is_error is False
        assert len(result.content) == 1

    def test_error_result(self):
        """Test error tool result."""
        result = ToolResult(
            content=[ContentBlock(type="text", text="Error occurred")],
            is_error=True,
        )
        assert result.is_error is True


# =============================================================================
# Test MCPTool
# =============================================================================


class TestMCPTool:
    """Tests for MCPTool dataclass."""

    def test_tool_attributes(self):
        """Test MCPTool attributes."""
        tool = MCPTool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}},
            server_name="test-server",
            annotations={"readOnlyHint": True},
        )
        assert tool.name == "test_tool"
        assert tool.server_name == "test-server"
        assert tool.annotations == {"readOnlyHint": True}


# =============================================================================
# Test ServerCapabilities
# =============================================================================


class TestServerCapabilities:
    """Tests for ServerCapabilities dataclass."""

    def test_default_capabilities(self):
        """Test default capabilities."""
        caps = ServerCapabilities()
        assert caps.tools is False
        assert caps.resources is False
        assert caps.prompts is False
        assert caps.logging is False

    def test_set_capabilities(self):
        """Test setting capabilities."""
        caps = ServerCapabilities(tools=True, resources=True)
        assert caps.tools is True
        assert caps.resources is True


# =============================================================================
# Test MCPProxyService Initialization
# =============================================================================


class TestMCPProxyServiceInit:
    """Tests for MCPProxyService initialization."""

    def test_init_with_configs(self, sample_mcp_server_config):
        """Test initialization with server configs."""
        proxy = MCPProxyService(
            servers=[sample_mcp_server_config],
            tool_cache_ttl=600,
            request_timeout=60.0,
        )
        assert len(proxy.servers) == 1
        assert proxy.tool_cache_ttl == 600
        assert proxy.request_timeout == 60.0

    def test_init_empty_servers(self):
        """Test initialization with no servers."""
        proxy = MCPProxyService(servers=[])
        assert len(proxy.servers) == 0


# =============================================================================
# Test Server Lifecycle
# =============================================================================


@pytest.mark.asyncio
class TestServerLifecycle:
    """Tests for server start/stop lifecycle."""

    async def test_start_server_success(
        self,
        sample_mcp_server_config,
        mock_subprocess,
        sample_initialize_response,
        sample_tools_list_response,
    ):
        """Test successful server startup."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        # Mock subprocess creation
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_subprocess,
        ):
            # Mock readline to return initialize and tools/list responses
            mock_subprocess.stdout.readline = AsyncMock(
                side_effect=[
                    json.dumps(sample_initialize_response).encode() + b"\n",
                    json.dumps(sample_tools_list_response).encode() + b"\n",
                ]
            )

            await proxy.start()

        assert "test-server" in proxy._connections
        assert len(proxy._tool_routing) == 2

    async def test_start_all_servers_fail_raises(
        self,
        sample_mcp_server_config,
    ):
        """Test that total server failure raises RuntimeError."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("Command not found"),
        ):
            with pytest.raises(RuntimeError, match="All 1 MCP servers failed"):
                await proxy.start()

        assert len(proxy._connections) == 0

    async def test_stop_servers(
        self,
        sample_mcp_server_config,
        mock_subprocess,
    ):
        """Test server shutdown."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        # Manually add a connection
        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_subprocess,
            stdin=mock_subprocess.stdin,
            stdout=mock_subprocess.stdout,
        )
        proxy._connections["test-server"] = conn
        proxy._tool_routing["read_file"] = "test-server"

        await proxy.stop()

        mock_subprocess.terminate.assert_called_once()
        assert len(proxy._connections) == 0
        assert len(proxy._tool_routing) == 0

    async def test_context_manager(
        self,
        sample_mcp_server_config,
        mock_subprocess,
        sample_initialize_response,
        sample_tools_list_response,
    ):
        """Test async context manager protocol."""
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_subprocess,
        ):
            mock_subprocess.stdout.readline = AsyncMock(
                side_effect=[
                    json.dumps(sample_initialize_response).encode() + b"\n",
                    json.dumps(sample_tools_list_response).encode() + b"\n",
                ]
            )

            async with MCPProxyService(
                servers=[sample_mcp_server_config]
            ) as proxy:
                assert "test-server" in proxy._connections

        # After context exit, connections should be cleared
        assert len(proxy._connections) == 0


# =============================================================================
# Test Tool Discovery
# =============================================================================


@pytest.mark.asyncio
class TestToolDiscovery:
    """Tests for tool discovery and caching."""

    async def test_list_tools(self, sample_mcp_server_config, mock_subprocess):
        """Test listing tools from connected servers."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        # Add mock connection with tools
        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_subprocess,
            stdin=mock_subprocess.stdin,
            stdout=mock_subprocess.stdout,
            tools=[
                MCPTool(
                    name="read_file",
                    description="Read file",
                    input_schema={},
                    server_name="test-server",
                ),
            ],
            last_tool_refresh=1000000.0,  # Recent refresh
        )
        proxy._connections["test-server"] = conn

        tools = await proxy.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "read_file"


# =============================================================================
# Test Tool Execution
# =============================================================================


@pytest.mark.asyncio
class TestToolExecution:
    """Tests for tool execution."""

    async def test_execute_tool_success(
        self,
        sample_mcp_server_config,
        mock_subprocess,
        sample_tool_call_response,
    ):
        """Test successful tool execution."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_subprocess,
            stdin=mock_subprocess.stdin,
            stdout=mock_subprocess.stdout,
        )
        proxy._connections["test-server"] = conn
        proxy._tool_routing["read_file"] = "test-server"

        mock_subprocess.stdout.readline = AsyncMock(
            return_value=json.dumps(sample_tool_call_response).encode() + b"\n"
        )

        result = await proxy.execute_tool("read_file", {"path": "/tmp/test"})

        assert result.is_error is False
        assert len(result.content) == 1
        assert result.content[0].text == "Hello World"

    async def test_execute_tool_error_result(
        self,
        sample_mcp_server_config,
        mock_subprocess,
        sample_tool_error_response,
    ):
        """Test tool execution returning error."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_subprocess,
            stdin=mock_subprocess.stdin,
            stdout=mock_subprocess.stdout,
        )
        proxy._connections["test-server"] = conn
        proxy._tool_routing["read_file"] = "test-server"

        mock_subprocess.stdout.readline = AsyncMock(
            return_value=json.dumps(sample_tool_error_response).encode() + b"\n"
        )

        result = await proxy.execute_tool("read_file", {"path": "/nonexistent"})

        assert result.is_error is True

    async def test_execute_tool_not_found(self, sample_mcp_server_config):
        """Test executing non-existent tool."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        with pytest.raises(ToolNotFoundError, match="Tool not found"):
            await proxy.execute_tool("nonexistent_tool", {})

    async def test_execute_tool_server_not_connected(self, sample_mcp_server_config):
        """Test executing tool when server disconnected."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])
        proxy._tool_routing["read_file"] = "disconnected-server"

        with pytest.raises(ToolNotFoundError, match="Server not connected"):
            await proxy.execute_tool("read_file", {"path": "/tmp/test"})


# =============================================================================
# Test JSON-RPC Communication
# =============================================================================


@pytest.mark.asyncio
class TestJsonRpc:
    """Tests for JSON-RPC communication."""

    async def test_send_jsonrpc_success(
        self,
        sample_mcp_server_config,
        mock_subprocess,
    ):
        """Test successful JSON-RPC request/response."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_subprocess,
            stdin=mock_subprocess.stdin,
            stdout=mock_subprocess.stdout,
        )

        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"status": "ok"},
        }
        mock_subprocess.stdout.readline = AsyncMock(
            return_value=json.dumps(response).encode() + b"\n"
        )

        result = await proxy._send_jsonrpc(conn, "test_method", {"param": "value"})

        assert result == {"status": "ok"}
        mock_subprocess.stdin.write.assert_called_once()

    async def test_send_jsonrpc_error_response(
        self,
        sample_mcp_server_config,
        mock_subprocess,
    ):
        """Test JSON-RPC error response handling."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_subprocess,
            stdin=mock_subprocess.stdin,
            stdout=mock_subprocess.stdout,
        )

        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32600,
                "message": "Invalid Request",
            },
        }
        mock_subprocess.stdout.readline = AsyncMock(
            return_value=json.dumps(error_response).encode() + b"\n"
        )

        with pytest.raises(RuntimeError, match="MCP error"):
            await proxy._send_jsonrpc(conn, "test_method", {})

    async def test_read_jsonrpc_skips_notifications(
        self,
        sample_mcp_server_config,
        mock_subprocess,
    ):
        """Test that notifications are skipped when reading responses."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_subprocess,
            stdin=mock_subprocess.stdin,
            stdout=mock_subprocess.stdout,
        )

        notification = {"jsonrpc": "2.0", "method": "notification"}
        response = {"jsonrpc": "2.0", "id": 1, "result": {}}

        mock_subprocess.stdout.readline = AsyncMock(
            side_effect=[
                json.dumps(notification).encode() + b"\n",
                json.dumps(response).encode() + b"\n",
            ]
        )

        result = await proxy._read_jsonrpc(conn, expected_id=1)
        assert result["id"] == 1


# =============================================================================
# Test Parse Tool Result
# =============================================================================


class TestParseToolResult:
    """Tests for parsing tool results."""

    def test_parse_text_result(self, sample_mcp_server_config):
        """Test parsing text content result."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        raw_result = {
            "content": [{"type": "text", "text": "Hello World"}],
            "isError": False,
        }

        result = proxy._parse_tool_result(raw_result)

        assert result.is_error is False
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == "Hello World"

    def test_parse_image_result(self, sample_mcp_server_config):
        """Test parsing image content result."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        raw_result = {
            "content": [
                {
                    "type": "image",
                    "data": "base64data==",
                    "mimeType": "image/png",
                }
            ],
            "isError": False,
        }

        result = proxy._parse_tool_result(raw_result)

        assert result.content[0].type == "image"
        assert result.content[0].data == "base64data=="
        assert result.content[0].mime_type == "image/png"

    def test_parse_resource_result(self, sample_mcp_server_config):
        """Test parsing resource content result."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        raw_result = {
            "content": [
                {
                    "type": "resource",
                    "uri": "file:///tmp/test.txt",
                    "text": "Resource text",
                }
            ],
            "isError": False,
        }

        result = proxy._parse_tool_result(raw_result)

        assert result.content[0].type == "resource"
        assert result.content[0].uri == "file:///tmp/test.txt"

    def test_parse_multiple_content_blocks(self, sample_mcp_server_config):
        """Test parsing result with multiple content blocks."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        raw_result = {
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ],
            "isError": False,
        }

        result = proxy._parse_tool_result(raw_result)

        assert len(result.content) == 2


# =============================================================================
# Edge Case Tests (FIX-18: Coverage Gap Filling)
# =============================================================================


def _make_mock_conn(config: MCPServerConfig) -> MCPConnection:
    """Create an MCPConnection with a mock process for edge-case tests."""
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdout = AsyncMock()
    return MCPConnection(
        config=config,
        process=mock_proc,
        stdin=mock_proc.stdin,
        stdout=mock_proc.stdout,
    )


class TestReadJsonRpcEdgeCases:
    """Edge case tests for _read_jsonrpc response parsing."""

    @pytest.mark.asyncio
    async def test_connection_closed_raises_error(self, sample_mcp_server_config):
        """Test that connection closed (empty line) raises RuntimeError."""
        conn = _make_mock_conn(sample_mcp_server_config)
        conn.stdout.readline = AsyncMock(return_value=b"")

        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        with pytest.raises(RuntimeError, match="Connection closed"):
            await proxy._read_jsonrpc(conn, expected_id=1)

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(self, sample_mcp_server_config):
        """Test that malformed JSON lines are silently skipped."""
        conn = _make_mock_conn(sample_mcp_server_config)

        valid_response = {"jsonrpc": "2.0", "id": 1, "result": {"data": "ok"}}
        conn.stdout.readline = AsyncMock(side_effect=[
            b"not valid json\n",
            b"{broken: json\n",
            json.dumps(valid_response).encode() + b"\n",
        ])

        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        result = await proxy._read_jsonrpc(conn, expected_id=1)
        assert result["id"] == 1
        assert result["result"]["data"] == "ok"

    @pytest.mark.asyncio
    async def test_mismatched_id_skipped(self, sample_mcp_server_config):
        """Test that responses with wrong ID are skipped."""
        conn = _make_mock_conn(sample_mcp_server_config)

        wrong_id = {"jsonrpc": "2.0", "id": 99, "result": {}}
        right_id = {"jsonrpc": "2.0", "id": 5, "result": {"match": True}}
        conn.stdout.readline = AsyncMock(side_effect=[
            json.dumps(wrong_id).encode() + b"\n",
            json.dumps(right_id).encode() + b"\n",
        ])

        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        result = await proxy._read_jsonrpc(conn, expected_id=5)
        assert result["result"]["match"] is True


class TestToolCacheTTL:
    """Tests for tool cache TTL behavior."""

    @pytest.mark.asyncio
    async def test_list_tools_uses_cache_when_fresh(self, sample_mcp_server_config):
        """Test that list_tools returns cached tools when TTL not expired."""
        import time

        proxy = MCPProxyService(servers=[sample_mcp_server_config], tool_cache_ttl=300)

        # Manually inject a connection with cached tools
        mock_proc = MagicMock()
        tool = MCPTool(
            name="cached_tool",
            description="A cached tool",
            input_schema={},
            server_name="test-server",
        )
        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_proc,
            stdin=MagicMock(),
            stdout=MagicMock(),
            tools=[tool],
            last_tool_refresh=time.monotonic(),  # Just refreshed
        )
        proxy._connections["test-server"] = conn

        tools = await proxy.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "cached_tool"


class TestToolExecutionTimeout:
    """Tests for tool execution timeout behavior."""

    @pytest.mark.asyncio
    async def test_timeout_wraps_asyncio_timeout(self, sample_mcp_server_config):
        """Test that asyncio.TimeoutError is wrapped in ToolExecutionTimeout."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        # Set up routing
        proxy._tool_routing["slow_tool"] = "test-server"

        # Set up connection with a mock that times out
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_proc,
            stdin=mock_proc.stdin,
            stdout=MagicMock(),
        )
        proxy._connections["test-server"] = conn

        # Patch _send_jsonrpc to raise TimeoutError
        proxy._send_jsonrpc = AsyncMock(side_effect=TimeoutError())

        with pytest.raises(ToolExecutionTimeout, match="slow_tool"):
            await proxy.execute_tool("slow_tool", {})


# =============================================================================
# Subprocess Management Tests (Q005)
# =============================================================================


@pytest.fixture
def two_server_configs():
    """Two server configs for multi-server tests."""
    return [
        MCPServerConfig(
            name="server-a",
            command="node",
            args=["server-a.js", "--port", "3001"],
            env={"API_KEY": "secret-a"},
            timeout_seconds=30.0,
            working_dir="/tmp/server-a",
        ),
        MCPServerConfig(
            name="server-b",
            command="python",
            args=["-m", "mcp_server"],
            env={"API_KEY": "secret-b"},
            timeout_seconds=60.0,
        ),
    ]


@pytest.mark.asyncio
class TestSubprocessSpawn:
    """Tests for subprocess creation via _start_server."""

    async def test_start_server_passes_correct_args(self):
        """Verify create_subprocess receives command, args, pipes, env, cwd."""
        config = MCPServerConfig(
            name="spawn-test",
            command="/usr/bin/node",
            args=["server.js", "--verbose"],
            env={"MY_VAR": "my_value"},
            timeout_seconds=10.0,
            working_dir="/tmp/workdir",
        )
        proxy = MCPProxyService(servers=[config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = AsyncMock()

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_create:
            conn = await proxy._start_server(config)

            mock_create.assert_called_once()
            call_args = mock_create.call_args

            # Positional args: command + args
            assert call_args.args[0] == "/usr/bin/node"
            assert call_args.args[1] == "server.js"
            assert call_args.args[2] == "--verbose"

            # Keyword args: pipes
            assert call_args.kwargs["stdin"] == asyncio.subprocess.PIPE
            assert call_args.kwargs["stdout"] == asyncio.subprocess.PIPE
            assert call_args.kwargs["stderr"] == asyncio.subprocess.PIPE

            # Keyword args: cwd
            assert call_args.kwargs["cwd"] == "/tmp/workdir"

            # Keyword args: env includes both os.environ + config env
            passed_env = call_args.kwargs["env"]
            assert passed_env["MY_VAR"] == "my_value"
            # os.environ keys are also present
            assert "PATH" in passed_env

        assert conn.process is mock_proc

    async def test_start_server_no_working_dir(self):
        """Verify cwd=None when working_dir not set."""
        config = MCPServerConfig(
            name="no-cwd",
            command="echo",
            args=[],
        )
        proxy = MCPProxyService(servers=[config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = AsyncMock()

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_create:
            await proxy._start_server(config)

            assert mock_create.call_args.kwargs["cwd"] is None

    async def test_start_server_no_env_override(self):
        """Verify base os.environ is used when config env is empty."""
        config = MCPServerConfig(
            name="no-env",
            command="echo",
            args=[],
        )
        proxy = MCPProxyService(servers=[config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = AsyncMock()

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_create:
            await proxy._start_server(config)

            passed_env = mock_create.call_args.kwargs["env"]
            # Should have os.environ but no extra keys
            assert "PATH" in passed_env

    async def test_start_server_pipe_failure_raises(self):
        """When stdin or stdout is None, _start_server raises RuntimeError."""
        config = MCPServerConfig(
            name="pipe-fail",
            command="echo",
            args=[],
        )
        proxy = MCPProxyService(servers=[config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.stdin = None
        mock_proc.stdout = None

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError, match="Failed to create pipes"):
                await proxy._start_server(config)


@pytest.mark.asyncio
class TestSubprocessStop:
    """Tests for subprocess termination in stop()."""

    async def test_stop_graceful_termination(self, sample_mcp_server_config):
        """Verify terminate() is called first and process.wait() is awaited."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_proc,
            stdin=MagicMock(),
            stdout=MagicMock(),
        )
        proxy._connections["test-server"] = conn

        await proxy.stop()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called()
        mock_proc.kill.assert_not_called()

    async def test_stop_force_kill_on_timeout(self, sample_mcp_server_config):
        """Verify kill() is called when terminate() + wait() times out."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        # First wait times out (during graceful), second succeeds (after kill)
        mock_proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), 0])

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_proc,
            stdin=MagicMock(),
            stdout=MagicMock(),
        )
        proxy._connections["test-server"] = conn

        await proxy.stop()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        # wait() called twice: once with timeout, once after kill
        assert mock_proc.wait.call_count == 2

    async def test_stop_handles_exception_per_server(self):
        """Verify stop() continues to next server even if one raises."""
        config_a = MCPServerConfig(name="a", command="x", args=[])
        config_b = MCPServerConfig(name="b", command="y", args=[])
        proxy = MCPProxyService(servers=[config_a, config_b])

        mock_a = MagicMock(spec=asyncio.subprocess.Process)
        mock_a.terminate = MagicMock(
            side_effect=ProcessLookupError("no such process")
        )
        mock_a.wait = AsyncMock()

        mock_b = MagicMock(spec=asyncio.subprocess.Process)
        mock_b.terminate = MagicMock()
        mock_b.wait = AsyncMock(return_value=0)
        mock_b.kill = MagicMock()

        proxy._connections["a"] = MCPConnection(
            config=config_a,
            process=mock_a,
            stdin=MagicMock(),
            stdout=MagicMock(),
        )
        proxy._connections["b"] = MCPConnection(
            config=config_b,
            process=mock_b,
            stdin=MagicMock(),
            stdout=MagicMock(),
        )

        # Should not raise even though server "a" throws
        await proxy.stop()

        # Server "b" was still terminated
        mock_b.terminate.assert_called_once()
        assert len(proxy._connections) == 0


@pytest.mark.asyncio
class TestPartialStartup:
    """Tests for partial server startup scenarios."""

    async def test_partial_startup_succeeds_with_warning(self, two_server_configs):
        """When one server fails and one succeeds, start() succeeds."""
        proxy = MCPProxyService(servers=two_server_configs)

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = AsyncMock()

        init_resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"capabilities": {"tools": {}}},
        }
        tools_resp = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": []},
        }

        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("server-a command not found")
            # server-b succeeds
            mock_proc.stdout.readline = AsyncMock(
                side_effect=[
                    json.dumps(init_resp).encode() + b"\n",
                    json.dumps(tools_resp).encode() + b"\n",
                ]
            )
            return mock_proc

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            side_effect=mock_create,
        ):
            await proxy.start()  # Should not raise

        # Only server-b connected
        assert len(proxy._connections) == 1
        assert "server-b" in proxy._connections

    async def test_all_servers_fail_raises(self, two_server_configs):
        """When all servers fail, start() raises RuntimeError."""
        proxy = MCPProxyService(servers=two_server_configs)

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            side_effect=OSError("Command not found"),
        ):
            with pytest.raises(RuntimeError, match="All 2 MCP servers failed"):
                await proxy.start()

        assert len(proxy._connections) == 0

    async def test_empty_servers_list_starts_fine(self):
        """With no servers configured, start() is a no-op (no error)."""
        proxy = MCPProxyService(servers=[])
        await proxy.start()
        assert len(proxy._connections) == 0

    async def test_start_failure_during_initialize_handshake(
        self, sample_mcp_server_config
    ):
        """Server spawns OK but initialize handshake fails."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        # Connection closes immediately during readline
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError, match="All 1 MCP servers failed"):
                await proxy.start()

    async def test_start_failure_during_tool_refresh(
        self, sample_mcp_server_config
    ):
        """Server init OK but tools/list fails → server treated as failed."""
        proxy = MCPProxyService(servers=[sample_mcp_server_config])

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = AsyncMock()

        init_resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"capabilities": {"tools": {}}},
        }
        # tools/list returns connection closed
        mock_proc.stdout.readline = AsyncMock(
            side_effect=[
                json.dumps(init_resp).encode() + b"\n",
                b"",  # Connection dies during tools/list
            ]
        )

        with patch(
            "marianne.bridge.mcp_proxy.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError, match="All 1 MCP servers failed"):
                await proxy.start()


@pytest.mark.asyncio
class TestToolCacheRefreshSubprocess:
    """Tests for stale cache triggering subprocess communication."""

    async def test_stale_cache_triggers_refresh(self, sample_mcp_server_config):
        """When tool cache TTL expires, list_tools refreshes from server."""
        proxy = MCPProxyService(
            servers=[sample_mcp_server_config], tool_cache_ttl=0
        )

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = AsyncMock()

        tools_resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "refreshed_tool",
                        "description": "Fresh tool",
                        "inputSchema": {},
                    }
                ]
            },
        }
        mock_proc.stdout.readline = AsyncMock(
            return_value=json.dumps(tools_resp).encode() + b"\n"
        )

        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_proc,
            stdin=mock_proc.stdin,
            stdout=mock_proc.stdout,
            tools=[],
            last_tool_refresh=0.0,  # Very stale
        )
        proxy._connections["test-server"] = conn

        tools = await proxy.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "refreshed_tool"
        mock_proc.stdin.write.assert_called()  # Request was sent

    async def test_cache_refresh_failure_returns_stale(
        self, sample_mcp_server_config
    ):
        """When refresh fails, stale cached tools are still returned."""
        proxy = MCPProxyService(
            servers=[sample_mcp_server_config], tool_cache_ttl=0
        )

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = AsyncMock()
        # Refresh will fail
        mock_proc.stdout.readline = AsyncMock(return_value=b"")

        stale_tool = MCPTool(
            name="stale_tool",
            description="Stale",
            input_schema={},
            server_name="test-server",
        )
        conn = MCPConnection(
            config=sample_mcp_server_config,
            process=mock_proc,
            stdin=mock_proc.stdin,
            stdout=mock_proc.stdout,
            tools=[stale_tool],
            last_tool_refresh=0.0,
        )
        proxy._connections["test-server"] = conn

        tools = await proxy.list_tools()

        # Stale tools returned even though refresh failed
        assert len(tools) == 1
        assert tools[0].name == "stale_tool"


@pytest.mark.asyncio
class TestMultiServerToolRouting:
    """Tests for tool routing across multiple servers."""

    async def test_tools_routed_to_correct_server(self):
        """Tools from different servers are routed correctly."""
        config_a = MCPServerConfig(name="fs-server", command="x", args=[])
        config_b = MCPServerConfig(name="db-server", command="y", args=[])
        proxy = MCPProxyService(servers=[config_a, config_b])

        mock_a = MagicMock()
        mock_a.stdin = MagicMock()
        mock_a.stdin.write = MagicMock()
        mock_a.stdin.drain = AsyncMock()
        mock_a.stdout = AsyncMock()

        mock_b = MagicMock()
        mock_b.stdin = MagicMock()
        mock_b.stdin.write = MagicMock()
        mock_b.stdin.drain = AsyncMock()
        mock_b.stdout = AsyncMock()

        tool_call_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "from-fs"}],
                "isError": False,
            },
        }
        mock_a.stdout.readline = AsyncMock(
            return_value=json.dumps(tool_call_result).encode() + b"\n"
        )

        conn_a = MCPConnection(
            config=config_a,
            process=mock_a,
            stdin=mock_a.stdin,
            stdout=mock_a.stdout,
            tools=[
                MCPTool(
                    name="read_file",
                    description="Read",
                    input_schema={},
                    server_name="fs-server",
                )
            ],
        )
        conn_b = MCPConnection(
            config=config_b,
            process=mock_b,
            stdin=mock_b.stdin,
            stdout=mock_b.stdout,
            tools=[
                MCPTool(
                    name="query_db",
                    description="Query",
                    input_schema={},
                    server_name="db-server",
                )
            ],
        )

        proxy._connections["fs-server"] = conn_a
        proxy._connections["db-server"] = conn_b
        proxy._tool_routing["read_file"] = "fs-server"
        proxy._tool_routing["query_db"] = "db-server"

        result = await proxy.execute_tool("read_file", {"path": "/tmp"})

        assert result.content[0].text == "from-fs"
        # Only fs-server's stdin was used
        mock_a.stdin.write.assert_called()
        mock_b.stdin.write.assert_not_called()
