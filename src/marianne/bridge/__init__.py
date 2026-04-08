"""Marianne Ollama Bridge components.

This package provides integration between Marianne and local Ollama models
with MCP tool support. The main components are:

- MCPProxyService: Manages MCP server subprocesses for tool execution
- ContextOptimizer: Optimizes tool context for limited context windows (Sheet 5)
- HybridRouter: Routes between Ollama and Claude based on complexity (Sheet 6)
- BridgeCoordinator: Top-level orchestrator for bridge components (Sheet 7)
"""

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

__all__ = [
    "ContentBlock",
    "MCPConnection",
    "MCPProxyService",
    "MCPTool",
    "ServerCapabilities",
    "ToolExecutionTimeout",
    "ToolNotFoundError",
    "ToolResult",
]
