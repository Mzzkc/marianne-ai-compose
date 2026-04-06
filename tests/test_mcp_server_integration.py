"""Tests for Mozart MCP Server Integration - Basic functionality tests."""

import tempfile
from pathlib import Path

import pytest

from marianne.mcp.server import MCPServer


class TestMCPServerIntegration:
    """Basic integration tests for MCP Server."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def mcp_server(self, temp_workspace):
        """Create MCP server instance."""
        return MCPServer(temp_workspace)

    async def test_server_initialization(self, mcp_server):
        """Test server initialization."""
        client_info = {"name": "test-client", "version": "1.0.0"}
        result = await mcp_server.initialize(client_info)

        assert "capabilities" in result
        assert "serverInfo" in result
        assert result["serverInfo"]["name"] == "mozart-mcp-server"
        assert mcp_server.initialized is True

    async def test_capabilities(self, mcp_server):
        """Test server capabilities."""
        caps = mcp_server.capabilities

        assert "tools" in caps
        assert "resources" in caps
        assert "logging" in caps
        assert caps["tools"]["listChanged"] is True
        assert caps["resources"]["subscribe"] is True

    async def test_list_tools_before_init(self, mcp_server):
        """Test listing tools before initialization."""
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await mcp_server.list_tools()

    async def test_list_tools_after_init(self, mcp_server):
        """Test listing tools after initialization."""
        await mcp_server.initialize()
        tools = await mcp_server.list_tools()

        assert isinstance(tools, list)
        assert len(tools) > 0

        # Check tool structure
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    async def test_list_resources_before_init(self, mcp_server):
        """Test listing resources before initialization."""
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await mcp_server.list_resources()

    async def test_list_resources_after_init(self, mcp_server):
        """Test listing resources after initialization."""
        await mcp_server.initialize()
        resources = await mcp_server.list_resources()

        assert isinstance(resources, list)
        assert len(resources) > 0

        # Check resource structure
        for resource in resources:
            assert "uri" in resource
            assert "name" in resource
            assert "description" in resource
            assert "mimeType" in resource

    async def test_call_tool_before_init(self, mcp_server):
        """Test calling tool before initialization."""
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await mcp_server.call_tool("mozart_artifact_list", {})

    async def test_call_tool_unknown_tool(self, mcp_server):
        """Test calling unknown tool."""
        await mcp_server.initialize()
        with pytest.raises(ValueError, match="Unknown tool"):
            await mcp_server.call_tool("unknown_tool", {})

    async def test_read_resource_before_init(self, mcp_server):
        """Test reading resource before initialization."""
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await mcp_server.read_resource("config://schema")

    async def test_read_resource_after_init(self, mcp_server):
        """Test reading resource after initialization."""
        await mcp_server.initialize()
        result = await mcp_server.read_resource("config://schema")

        assert "contents" in result
        assert len(result["contents"]) > 0
        content = result["contents"][0]
        assert "uri" in content
        assert "mimeType" in content

    async def test_read_resource_unknown_uri(self, mcp_server):
        """Test reading unknown resource URI."""
        await mcp_server.initialize()
        with pytest.raises(ValueError, match="Unknown resource URI"):
            await mcp_server.read_resource("unknown://resource")

    async def test_shutdown(self, mcp_server):
        """Test server shutdown."""
        await mcp_server.initialize()
        assert mcp_server.initialized is True

        await mcp_server.shutdown()
        assert mcp_server.initialized is False
