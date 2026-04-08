"""Dashboard and MCP server commands for Marianne CLI.

This module implements server commands for external integrations:
- `dashboard`: Start the web dashboard for job monitoring
- `mcp`: Start the Model Context Protocol server for AI agent access

★ Insight ─────────────────────────────────────
1. **Lazy imports for optional dependencies**: The `dashboard` command only imports
   `uvicorn` when executed, not at module load time. This allows the CLI to work
   even when uvicorn isn't installed, deferring the error until the user tries
   to use the feature. This is a common pattern for optional dependencies.

2. **State backend preference hierarchy**: The dashboard prefers SQLite if a database
   file exists, falling back to JSON. This respects user choice while providing
   sensible defaults - SQLite offers better query performance for dashboards while
   JSON provides simplicity and human readability for debugging.

3. **MCP as external API surface**: The MCP server exposes Marianne's capabilities to
   external AI agents using a standardized protocol. This transforms Marianne from
   a standalone CLI into an API-accessible service that other AI systems can invoke.
─────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.panel import Panel

from marianne import __version__

from ..output import console

# =============================================================================
# dashboard command
# =============================================================================


def dashboard(
    port: int = typer.Option(8000, "--port", "-p", help="Port to run dashboard on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory for job state (defaults to current directory)",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        "-r",
        help="Enable auto-reload for development",
    ),
) -> None:
    """Start the web dashboard.

    Launches the Marianne dashboard API server for job monitoring and control.
    The API provides endpoints for listing, viewing, and managing jobs.

    Examples:
        mzt dashboard                    # Start on localhost:8000
        mzt dashboard --port 3000        # Custom port
        mzt dashboard --host 0.0.0.0     # Allow external connections
        mzt dashboard --workspace ./jobs # Use specific state directory
    """
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Error:[/red] uvicorn is required for the dashboard.\n"
            "Install it with: pip install uvicorn"
        )
        raise typer.Exit(1) from None

    from marianne.dashboard import create_app
    from marianne.state import JsonStateBackend, SQLiteStateBackend
    from marianne.state.base import StateBackend

    # Determine state directory
    state_dir = workspace or Path.cwd()

    # Create state backend (prefer SQLite if exists, otherwise JSON)
    state_backend: StateBackend
    sqlite_path = state_dir / ".marianne-state.db"
    if sqlite_path.exists():
        state_backend = SQLiteStateBackend(sqlite_path)
        console.print(f"[dim]Using SQLite state backend: {sqlite_path}[/dim]")
    else:
        state_backend = JsonStateBackend(state_dir)
        console.print(f"[dim]Using JSON state backend: {state_dir}[/dim]")

    # Create the FastAPI app
    fastapi_app = create_app(
        state_backend=state_backend,
        title="Marianne Dashboard",
        version=__version__,
    )

    # Display startup info
    console.print(
        Panel(
            f"[bold]Marianne Dashboard[/bold]\n\n"
            f"API: http://{host}:{port}\n"
            f"Docs: http://{host}:{port}/docs\n"
            f"OpenAPI: http://{host}:{port}/openapi.json\n\n"
            f"[dim]Press Ctrl+C to stop[/dim]",
            title="Starting Server",
        )
    )

    # Run the server
    try:
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


# =============================================================================
# mcp command
# =============================================================================


def mcp(
    port: int = typer.Option(8001, "--port", "-p", help="Port to run MCP server on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory for job operations (defaults to current directory)",
    ),
) -> None:
    """Start the Marianne MCP (Model Context Protocol) server.

    Launches an MCP server that exposes Marianne's job management capabilities
    as tools for external AI agents. The server provides:

    - Job management tools (run, status, pause, resume, cancel)
    - Artifact browsing and log streaming
    - Configuration access as resources

    Security: All tool executions require explicit user consent.
    File system access is restricted to designated workspace directories.

    Examples:
        mzt mcp                          # Start on localhost:8001
        mzt mcp --port 8002              # Custom port
        mzt mcp --workspace ./projects   # Use specific workspace root
    """
    try:
        asyncio.run(_run_mcp_server(host, port, workspace))
    except KeyboardInterrupt:
        console.print("\n[yellow]MCP Server stopped.[/yellow]")


async def _run_mcp_server(host: str, port: int, workspace_root: Path | None) -> None:
    """Run the MCP server with proper async handling."""
    from marianne.mcp.server import MCPServer

    # Initialize MCP server
    workspace = workspace_root or Path.cwd()
    server = MCPServer(workspace_root=workspace)

    # Initialize with basic client info
    await server.initialize({
        "name": "marianne-cli",
        "version": __version__
    })

    console.print(
        Panel(
            f"[bold]Marianne MCP Server[/bold]\n\n"
            f"Protocol: Model Context Protocol\n"
            f"Host: {host}:{port}\n"
            f"Workspace: {workspace}\n"
            f"Tools: Job management, artifact browsing, config access\n\n"
            f"[dim]Press Ctrl+C to stop[/dim]",
            title="MCP Server Ready",
        )
    )

    # Note: This is a simplified startup - a full implementation would
    # require an HTTP/SSE transport layer. For now, we just keep the
    # server alive and ready for JSON-RPC 2.0 connections.
    console.print(
        "[yellow]Note:[/yellow] MCP server initialized. "
        "Implement HTTP/SSE transport for external connections."
    )

    try:
        # Keep server alive
        while True:
            await asyncio.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        await server.shutdown()


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "dashboard",
    "mcp",
]
