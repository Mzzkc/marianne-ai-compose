"""Integration tests for MCPProxyService using real subprocesses.

Complements test_mcp_proxy.py (mock-based) by testing actual subprocess
spawning, stdio JSON-RPC 2.0 communication, and process lifecycle.
"""

import sys
import textwrap

import pytest

from marianne.bridge.mcp_proxy import (
    MCPProxyService,
    ToolNotFoundError,
)
from marianne.core.config import MCPServerConfig


@pytest.fixture(scope="module")
def mcp_test_server_script(tmp_path_factory):
    """Create a minimal MCP test server that handles JSON-RPC over stdio."""
    tmp = tmp_path_factory.mktemp("mcp_server")
    script_path = tmp / "test_mcp_server.py"
    script_path.write_text(textwrap.dedent('''\
        """Minimal MCP test server for subprocess testing."""
        import json
        import os
        import sys

        def send_response(response):
            """Write JSON-RPC response to stdout."""
            sys.stdout.write(json.dumps(response) + "\\n")
            sys.stdout.flush()

        def main():
            """Read stdin line by line, process JSON-RPC requests."""
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    continue

                method = request.get("method", "")
                req_id = request.get("id")

                # Handle notifications (no id → no response)
                if req_id is None:
                    continue

                if method == "initialize":
                    send_response({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {
                                "tools": {},
                            },
                            "serverInfo": {
                                "name": "test-mcp-server",
                                "version": "0.1.0",
                            },
                        },
                    })
                elif method == "tools/list":
                    send_response({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "tools": [
                                {
                                    "name": "echo",
                                    "description": "Echo the input back",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {
                                            "message": {
                                                "type": "string",
                                                "description": "Message to echo",
                                            },
                                        },
                                        "required": ["message"],
                                    },
                                },
                                {
                                    "name": "env_check",
                                    "description": "Check env var",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {
                                            "var_name": {"type": "string"},
                                        },
                                        "required": ["var_name"],
                                    },
                                },
                            ],
                        },
                    })
                elif method == "tools/call":
                    params = request.get("params", {})
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})

                    if tool_name == "echo":
                        message = arguments.get("message", "")
                        send_response({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [
                                    {"type": "text", "text": message},
                                ],
                            },
                        })
                    elif tool_name == "env_check":
                        var_name = arguments.get("var_name", "")
                        value = os.environ.get(var_name, "<NOT SET>")
                        send_response({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [
                                    {"type": "text", "text": value},
                                ],
                            },
                        })
                    else:
                        send_response({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32601,
                                "message": f"Unknown tool: {tool_name}",
                            },
                        })
                else:
                    send_response({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    })

        if __name__ == "__main__":
            main()
    '''))
    return str(script_path)


@pytest.fixture
def make_config(mcp_test_server_script):
    """Factory for MCPServerConfig pointing to the test server."""

    def _make(
        name: str = "test-server",
        env: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> MCPServerConfig:
        return MCPServerConfig(
            name=name,
            command=sys.executable,
            args=[mcp_test_server_script],
            env=env or {},
            timeout_seconds=timeout_seconds,
        )

    return _make


# =============================================================================
# Real subprocess lifecycle tests
# =============================================================================


class TestRealSubprocessLifecycle:

    async def test_start_and_stop_real_server(self, make_config):
        config = make_config()
        proxy = MCPProxyService([config])

        await proxy.start()

        # Verify connection established
        assert "test-server" in proxy._connections
        conn = proxy._connections["test-server"]
        assert conn.process.returncode is None  # Still running
        assert conn.capabilities is not None
        assert conn.capabilities.tools is True

        await proxy.stop()

        # Verify process terminated
        assert len(proxy._connections) == 0

    async def test_context_manager_lifecycle(self, make_config):
        config = make_config()

        async with MCPProxyService([config]) as proxy:
            assert "test-server" in proxy._connections
            conn = proxy._connections["test-server"]
            pid = conn.process.pid
            assert pid is not None

        # After exit, connections should be cleared
        assert len(proxy._connections) == 0

    async def test_multiple_servers(self, make_config):
        configs = [
            make_config(name="server-a"),
            make_config(name="server-b"),
        ]
        proxy = MCPProxyService(configs)

        await proxy.start()
        assert len(proxy._connections) == 2
        assert "server-a" in proxy._connections
        assert "server-b" in proxy._connections

        # Both should have tools
        tools = await proxy.list_tools()
        # Each server has 2 tools, but same names = last one wins routing
        assert len(tools) == 4  # 2 tools x 2 servers

        await proxy.stop()


# =============================================================================
# Real JSON-RPC communication tests
# =============================================================================


class TestRealJsonRpcCommunication:

    async def test_list_tools_real_server(self, make_config):
        config = make_config()

        async with MCPProxyService([config]) as proxy:
            tools = await proxy.list_tools()

            assert len(tools) == 2
            tool_names = [t.name for t in tools]
            assert "echo" in tool_names
            assert "env_check" in tool_names

            # Verify tool schemas
            echo_tool = next(t for t in tools if t.name == "echo")
            assert echo_tool.description == "Echo the input back"
            assert echo_tool.server_name == "test-server"
            assert "message" in echo_tool.input_schema.get("properties", {})

    async def test_execute_tool_real_server(self, make_config):
        config = make_config()

        async with MCPProxyService([config]) as proxy:
            result = await proxy.execute_tool(
                "echo", {"message": "hello from test"}
            )

            assert result.is_error is False
            assert len(result.content) == 1
            assert result.content[0].type == "text"
            assert result.content[0].text == "hello from test"

    async def test_multiple_tool_calls(self, make_config):
        config = make_config()

        async with MCPProxyService([config]) as proxy:
            for i in range(5):
                result = await proxy.execute_tool(
                    "echo", {"message": f"call-{i}"}
                )
                assert result.content[0].text == f"call-{i}"

    async def test_tool_not_found_real_server(self, make_config):
        config = make_config()

        async with MCPProxyService([config]) as proxy:
            with pytest.raises(ToolNotFoundError):
                await proxy.execute_tool("nonexistent_tool", {})


# =============================================================================
# Environment variable passing tests
# =============================================================================


class TestRealEnvironmentPassing:

    async def test_env_vars_passed_to_subprocess(self, make_config):
        config = make_config(
            env={"MZT_TEST_VAR": "test_value_42"}
        )

        async with MCPProxyService([config]) as proxy:
            result = await proxy.execute_tool(
                "env_check", {"var_name": "MZT_TEST_VAR"}
            )
            assert result.content[0].text == "test_value_42"

    async def test_env_var_not_set(self, make_config):
        config = make_config()

        async with MCPProxyService([config]) as proxy:
            result = await proxy.execute_tool(
                "env_check", {"var_name": "DEFINITELY_NOT_SET_12345"}
            )
            assert result.content[0].text == "<NOT SET>"


# =============================================================================
# Process termination tests
# =============================================================================


class TestRealProcessTermination:

    async def test_graceful_termination(self, make_config):
        config = make_config()
        proxy = MCPProxyService([config])

        await proxy.start()
        conn = proxy._connections["test-server"]
        assert conn.process.pid is not None  # Process was spawned

        await proxy.stop()

        # Process should be terminated
        assert conn.process.returncode is not None

    async def test_stop_idempotent(self, make_config):
        config = make_config()

        async with MCPProxyService([config]) as proxy:
            pass
        # Second stop should be safe (connections already cleared)
        await proxy.stop()

    async def test_server_failure_on_start(self, make_config):
        bad_config = MCPServerConfig(
            name="bad-server",
            command="this-command-definitely-does-not-exist-xyz",
            args=[],
            timeout_seconds=5.0,
        )

        proxy = MCPProxyService([bad_config])
        with pytest.raises(RuntimeError, match="All.*MCP servers failed"):
            await proxy.start()

    async def test_mixed_good_and_bad_servers(self, make_config):
        good_config = make_config(name="good-server")
        bad_config = MCPServerConfig(
            name="bad-server",
            command="this-command-definitely-does-not-exist-xyz",
            args=[],
            timeout_seconds=5.0,
        )

        async with MCPProxyService([good_config, bad_config]) as proxy:
            # Good server should work
            assert "good-server" in proxy._connections
            assert "bad-server" not in proxy._connections

            tools = await proxy.list_tools()
            assert len(tools) == 2  # 2 tools from good server


# =============================================================================
# Tool cache and refresh tests
# =============================================================================


class TestRealToolCache:

    async def test_tool_cache_returns_cached_tools(self, make_config):
        config = make_config()

        async with MCPProxyService([config], tool_cache_ttl=300) as proxy:
            tools1 = await proxy.list_tools()
            tools2 = await proxy.list_tools()

            # Both calls should return same tools
            assert len(tools1) == len(tools2)
            assert [t.name for t in tools1] == [t.name for t in tools2]

    async def test_tool_routing_across_servers(self, make_config):
        config_a = make_config(name="server-a")

        async with MCPProxyService([config_a]) as proxy:
            # Verify routing table populated
            assert proxy._tool_routing.get("echo") == "server-a"
            assert proxy._tool_routing.get("env_check") == "server-a"
