"""Marianne MCP Server - Model Context Protocol integration.

This module implements an MCP server that exposes conductor-managed Marianne
score execution as tools for external AI agents. The server provides:

- Submitted-score lifecycle tools (run, pause, resume, cancel)
- Status monitoring and log streaming
- Artifact management and workspace browsing
- Score schema, instrument, validation, and conductor resources

Example:
    >>> from marianne.mcp.server import MCPServer
    >>> server = MCPServer()
    >>> await server.serve()
"""

from .server import MCPServer

__all__ = ["MCPServer"]
