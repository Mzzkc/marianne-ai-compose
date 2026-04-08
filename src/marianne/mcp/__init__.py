"""Marianne MCP Server - Model Context Protocol integration.

This module implements an MCP server that exposes Marianne's job management
capabilities as tools for external AI agents. The server provides:

- Job lifecycle tools (run, pause, resume, cancel)
- Status monitoring and log streaming
- Artifact management and workspace browsing
- Mzt configuration as resources

Example:
    >>> from marianne.mcp.server import MCPServer
    >>> server = MCPServer()
    >>> await server.serve()
"""

from .server import MCPServer

__all__ = ["MCPServer"]
