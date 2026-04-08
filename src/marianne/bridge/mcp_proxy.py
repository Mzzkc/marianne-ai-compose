"""MCP Proxy Service for managing MCP server subprocesses.

Provides MCP client functionality for Marianne, enabling tool discovery and
execution through MCP servers. This is an IN-PROCESS manager, not a separate
proxy process (see ADR-001 in architecture design).

The service:
- Spawns MCP server subprocesses with proper lifecycle management
- Handles JSON-RPC 2.0 communication over stdio pipes
- Caches tool manifests with configurable TTL
- Executes tools and translates results

Security Note: This module uses asyncio.create_subprocess_exec() which is the
safe subprocess method in Python - it does NOT use shell=True, so there is no
shell injection risk. Arguments are passed as a list, not interpolated into
a shell command string. This mirrors the pattern in claude_cli.py.

Example usage:
    async with MCPProxyService(servers=[config]) as proxy:
        tools = await proxy.list_tools()
        result = await proxy.execute_tool("read_file", {"path": "/tmp/test"})
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from marianne.core.logging import get_logger

if TYPE_CHECKING:
    from marianne.core.config import MCPServerConfig

# Module-level logger
_logger = get_logger("bridge.mcp_proxy")

# MCP Protocol version
MCP_PROTOCOL_VERSION = "2025-11-25"


@dataclass
class ContentBlock:
    """Content block in tool results."""

    type: Literal["text", "image", "resource"]
    text: str | None = None
    data: str | None = None  # base64 for images
    mime_type: str | None = None
    uri: str | None = None  # for resources


@dataclass
class ToolResult:
    """Result from tool execution."""

    content: list[ContentBlock]
    is_error: bool = False


@dataclass
class MCPTool:
    """Represents an MCP tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str  # Which server owns this tool
    annotations: dict[str, Any] | None = None  # readOnlyHint, etc.


@dataclass
class ServerCapabilities:
    """Capabilities reported by MCP server."""

    tools: bool = False
    resources: bool = False
    prompts: bool = False
    logging: bool = False


@dataclass
class MCPConnection:
    """Active connection to an MCP server."""

    config: MCPServerConfig
    process: asyncio.subprocess.Process
    stdin: asyncio.StreamWriter
    stdout: asyncio.StreamReader
    capabilities: ServerCapabilities | None = None
    tools: list[MCPTool] = field(default_factory=list)
    last_tool_refresh: float = 0.0


class ToolNotFoundError(Exception):
    """Raised when requested tool doesn't exist."""


class ToolExecutionTimeout(Exception):
    """Raised when tool execution times out."""


class MCPProxyService:
    """MCP client that manages server subprocesses and executes tools.

    This service acts as an MCP CLIENT - it spawns and communicates with
    MCP SERVERS. Marianne's existing MCP server (for external integration)
    is separate from this client functionality.

    Lifecycle:
        1. start() - Spawn server processes and perform initialize handshake
        2. list_tools() - Get available tools (cached with TTL)
        3. execute_tool() - Call a specific tool
        4. stop() - Clean shutdown of all servers
    """

    def __init__(
        self,
        servers: list[MCPServerConfig],
        tool_cache_ttl: int = 300,
        request_timeout: float = 30.0,
    ) -> None:
        """Initialize MCP proxy service.

        Args:
            servers: List of MCP server configurations to connect to
            tool_cache_ttl: Seconds before refreshing tool list from servers
            request_timeout: Default timeout for JSON-RPC requests
        """
        self.servers = servers
        self.tool_cache_ttl = tool_cache_ttl
        self.request_timeout = request_timeout

        # Active connections keyed by server name
        self._connections: dict[str, MCPConnection] = {}

        # Tool name to server name mapping for routing
        self._tool_routing: dict[str, str] = {}

        # Request ID counter
        self._request_id = 0

    async def start(self) -> None:
        """Start all configured MCP servers.

        For each server:
        1. Spawn subprocess with asyncio.create_subprocess_exec
        2. Send initialize request (JSON-RPC)
        3. Wait for initialize response
        4. Send initialized notification
        5. Call tools/list to cache available tools

        Raises:
            RuntimeError: If no servers could be started (total failure).
        """
        _logger.info("mcp_proxy_starting", server_count=len(self.servers))

        failed_servers: list[str] = []

        for config in self.servers:
            try:
                conn = await self._start_server(config)
                self._connections[config.name] = conn

                # Initialize handshake
                await self._initialize_server(conn)

                # Fetch initial tool list
                await self._refresh_tools(conn)

                _logger.info(
                    "mcp_server_started",
                    server=config.name,
                    tool_count=len(conn.tools),
                )

            except Exception as e:
                _logger.error(
                    "mcp_server_start_failed",
                    server=config.name,
                    error=str(e),
                )
                failed_servers.append(config.name)
                # Terminate orphaned subprocess before removing connection
                partial_conn = self._connections.pop(config.name, None)
                if partial_conn is not None and partial_conn.process.returncode is None:
                    try:
                        partial_conn.process.kill()
                        await partial_conn.process.wait()
                    except (ProcessLookupError, OSError):
                        pass
                # Continue with other servers - don't fail entirely

        if failed_servers:
            _logger.warning(
                "mcp_proxy_partial_startup",
                failed_servers=failed_servers,
                connected_servers=len(self._connections),
                total_configured=len(self.servers),
            )

        if self.servers and not self._connections:
            raise RuntimeError(
                f"All {len(self.servers)} MCP servers failed to start: "
                f"{', '.join(failed_servers)}"
            )

        _logger.info(
            "mcp_proxy_started",
            connected_servers=len(self._connections),
            total_tools=len(self._tool_routing),
        )

    async def stop(self) -> None:
        """Stop all MCP server subprocesses.

        Sends SIGTERM and waits for graceful shutdown, then SIGKILL if needed.
        """
        _logger.info("mcp_proxy_stopping", server_count=len(self._connections))

        for name, conn in self._connections.items():
            try:
                # Try graceful termination
                conn.process.terminate()
                try:
                    await asyncio.wait_for(conn.process.wait(), timeout=5.0)
                except TimeoutError:
                    # Force kill
                    conn.process.kill()
                    await conn.process.wait()

                _logger.debug("mcp_server_stopped", server=name)

            except Exception as e:
                _logger.warning(
                    "mcp_server_stop_error",
                    server=name,
                    error=str(e),
                )

        self._connections.clear()
        self._tool_routing.clear()
        _logger.info("mcp_proxy_stopped")

    async def __aenter__(self) -> MCPProxyService:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.stop()

    async def list_tools(self) -> list[MCPTool]:
        """Get all available tools from all servers.

        Uses cached tool list if within TTL, otherwise refreshes.

        Returns:
            List of MCPTool objects from all connected servers
        """
        current_time = time.monotonic()
        all_tools: list[MCPTool] = []

        for name, conn in self._connections.items():
            # Check if cache is stale
            if current_time - conn.last_tool_refresh > self.tool_cache_ttl:
                try:
                    await self._refresh_tools(conn)
                except Exception as e:
                    _logger.warning(
                        "tool_refresh_failed",
                        server=name,
                        error=str(e),
                    )

            all_tools.extend(conn.tools)

        return all_tools

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute a tool by name.

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool

        Returns:
            ToolResult with content and error status

        Raises:
            ToolNotFoundError: If tool doesn't exist
            ToolExecutionTimeout: If execution times out
        """
        # Find which server owns this tool
        server_name = self._tool_routing.get(tool_name)
        if not server_name:
            raise ToolNotFoundError(f"Tool not found: {tool_name}")

        conn = self._connections.get(server_name)
        if not conn:
            raise ToolNotFoundError(f"Server not connected: {server_name}")

        _logger.debug(
            "tool_execute_start",
            tool=tool_name,
            server=server_name,
        )

        try:
            # Send tools/call request
            result = await self._send_jsonrpc(
                conn,
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments,
                },
                timeout=conn.config.timeout_seconds or self.request_timeout,
            )

            # Parse result
            return self._parse_tool_result(result)

        except TimeoutError as e:
            raise ToolExecutionTimeout(f"Tool {tool_name} timed out") from e

    async def _start_server(self, config: MCPServerConfig) -> MCPConnection:
        """Start a single MCP server subprocess.

        Uses asyncio.create_subprocess_exec (shell-injection safe) to spawn
        the MCP server process. This is the same pattern used by claude_cli.py.

        Args:
            config: Server configuration

        Returns:
            MCPConnection with active process
        """
        # Build environment
        env = os.environ.copy()
        env.update(config.env or {})

        # Determine working directory
        cwd = config.working_dir

        _logger.debug(
            "starting_mcp_server",
            name=config.name,
            command=config.command,
            args=config.args,
        )

        # Spawn subprocess using create_subprocess_exec (NOT shell=True)
        # This is shell-injection safe as arguments are passed as a list
        process = await asyncio.create_subprocess_exec(
            config.command,
            *config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        if not process.stdin or not process.stdout:
            raise RuntimeError(f"Failed to create pipes for {config.name}")

        return MCPConnection(
            config=config,
            process=process,
            stdin=process.stdin,
            stdout=process.stdout,
        )

    async def _initialize_server(self, conn: MCPConnection) -> None:
        """Perform MCP initialize handshake.

        Sends initialize request, waits for response, then sends
        initialized notification as per MCP protocol.

        Args:
            conn: Active connection to initialize
        """
        # Send initialize request
        init_result = await self._send_jsonrpc(
            conn,
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "clientInfo": {
                    "name": "marianne-ollama-bridge",
                    "version": "0.1.0",
                },
            },
        )

        # Parse capabilities
        caps = init_result.get("capabilities", {})
        conn.capabilities = ServerCapabilities(
            tools="tools" in caps,
            resources="resources" in caps,
            prompts="prompts" in caps,
            logging="logging" in caps,
        )

        # Send initialized notification (no response expected)
        await self._send_notification(conn, "notifications/initialized", {})

        _logger.debug(
            "mcp_server_initialized",
            server=conn.config.name,
            capabilities=conn.capabilities,
        )

    async def _refresh_tools(self, conn: MCPConnection) -> None:
        """Refresh tool list from server.

        Args:
            conn: Connection to refresh tools from
        """
        result = await self._send_jsonrpc(conn, "tools/list", {})
        tools_data = result.get("tools", [])

        conn.tools = []
        for tool_data in tools_data:
            tool = MCPTool(
                name=tool_data.get("name", ""),
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_name=conn.config.name,
                annotations=tool_data.get("annotations"),
            )
            conn.tools.append(tool)
            # Update routing table
            self._tool_routing[tool.name] = conn.config.name

        conn.last_tool_refresh = time.monotonic()

    async def _send_jsonrpc(
        self,
        conn: MCPConnection,
        method: str,
        params: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send JSON-RPC request and wait for response.

        Args:
            conn: Connection to send on
            method: RPC method name
            params: Method parameters
            timeout: Optional timeout override

        Returns:
            Result from response

        Raises:
            asyncio.TimeoutError: If request times out
            RuntimeError: If response is an error
        """
        self._request_id += 1
        request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        # Send request
        request_line = json.dumps(request) + "\n"
        conn.stdin.write(request_line.encode())
        await conn.stdin.drain()

        # Read response
        effective_timeout = timeout or self.request_timeout
        response = await asyncio.wait_for(
            self._read_jsonrpc(conn, request_id),
            timeout=effective_timeout,
        )

        # Check for error
        if "error" in response:
            error = response["error"]
            raise RuntimeError(
                f"MCP error {error.get('code')}: {error.get('message')}"
            )

        result: dict[str, Any] = response.get("result", {})
        return result

    async def _send_notification(
        self,
        conn: MCPConnection,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Send JSON-RPC notification (no response expected).

        Args:
            conn: Connection to send on
            method: Notification method name
            params: Method parameters
        """
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        notification_line = json.dumps(notification) + "\n"
        conn.stdin.write(notification_line.encode())
        await conn.stdin.drain()

    async def _read_jsonrpc(
        self,
        conn: MCPConnection,
        expected_id: int,
    ) -> dict[str, Any]:
        """Read JSON-RPC response matching expected ID.

        Filters out notifications while waiting for response.

        Args:
            conn: Connection to read from
            expected_id: Request ID to match

        Returns:
            Response dict with matching ID
        """
        while True:
            line = await conn.stdout.readline()
            if not line:
                raise RuntimeError("Connection closed unexpectedly")

            try:
                data: dict[str, Any] = json.loads(line.decode())
            except json.JSONDecodeError:
                continue  # Skip malformed lines

            # Skip notifications (no id field)
            if "id" not in data:
                continue

            # Check if this is our response
            if data.get("id") == expected_id:
                return data

    def _parse_tool_result(self, result: dict[str, Any]) -> ToolResult:
        """Parse MCP tool/call result into ToolResult.

        Args:
            result: Raw result from tools/call response

        Returns:
            Parsed ToolResult
        """
        content_blocks: list[ContentBlock] = []
        is_error = result.get("isError", False)

        for block in result.get("content", []):
            block_type = block.get("type", "text")

            if block_type == "text":
                content_blocks.append(
                    ContentBlock(type="text", text=block.get("text", ""))
                )
            elif block_type == "image":
                content_blocks.append(
                    ContentBlock(
                        type="image",
                        data=block.get("data"),
                        mime_type=block.get("mimeType"),
                    )
                )
            elif block_type == "resource":
                content_blocks.append(
                    ContentBlock(
                        type="resource",
                        uri=block.get("uri"),
                        text=block.get("text"),
                    )
                )

        return ToolResult(content=content_blocks, is_error=is_error)

