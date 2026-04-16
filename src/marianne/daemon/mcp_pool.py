"""Shared MCP server pool manager for the conductor.

The conductor manages a pool of MCP server processes as shared infrastructure.
One process per MCP server type, shared across all agents. Each server is
started as a subprocess and tracked for health. Agents access servers via
Unix sockets (bind-mounted into sandboxes).

Lifecycle:
- ``start_all()`` — starts all configured servers on daemon startup
- ``stop_all()`` — terminates all servers on daemon shutdown
- ``health_check(name)`` — verifies a server process is alive
- ``get_socket_path(name)`` — returns the socket path for agent access

The manager does NOT handle stdio-to-socket proxying — that is a future
enhancement. Currently it manages process lifecycle only, and the socket
path is stored for reference by the technique router.
"""

from __future__ import annotations

import asyncio
import json
import sys
from enum import Enum
from pathlib import Path

from marianne.core.logging import get_logger
from marianne.daemon.config import McpPoolConfig

_logger = get_logger("daemon.mcp_pool")

# Timeout for waiting on process termination during shutdown
_STOP_TIMEOUT_SECONDS = 10.0


class McpServerState(str, Enum):
    """State of a managed MCP server process."""

    STOPPED = "stopped"
    RUNNING = "running"
    FAILED = "failed"


class _ServerHandle:
    """Internal tracking for a single MCP server process.

    Attributes:
        name: Server name (from config key).
        process: The asyncio subprocess, or None if not started.
        state: Current server state.
    """

    __slots__ = ("name", "process", "state")

    def __init__(self, name: str) -> None:
        self.name = name
        self.process: asyncio.subprocess.Process | None = None
        self.state = McpServerState.STOPPED


class McpPoolManager:
    """Manages MCP server process lifecycle for the conductor.

    Reads McpPoolConfig and starts/stops server processes. Tracks state
    per server and provides socket path lookups for agent sandboxes.

    Usage::

        manager = McpPoolManager(config.mcp_pool)
        await manager.start_all()

        # During operation
        if await manager.health_check("github"):
            path = manager.get_socket_path("github")
            # Bind-mount path into agent sandbox

        # On shutdown
        await manager.stop_all()
    """

    def __init__(self, config: McpPoolConfig) -> None:
        self._config = config
        self._handles: dict[str, _ServerHandle] = {
            name: _ServerHandle(name) for name in config.servers
        }

    def server_names(self) -> list[str]:
        """Return the names of all configured servers."""
        return list(self._config.servers.keys())

    def is_running(self, name: str) -> bool:
        """Check if a server is currently running."""
        handle = self._handles.get(name)
        if handle is None:
            return False
        return handle.state == McpServerState.RUNNING

    def get_status(self) -> dict[str, McpServerState]:
        """Get the state of all servers."""
        return {name: handle.state for name, handle in self._handles.items()}

    def get_socket_path(self, name: str) -> Path | None:
        """Get the Unix socket path for a server.

        Args:
            name: Server name.

        Returns:
            Path to the Unix socket, or None if the server is not configured.
        """
        entry = self._config.servers.get(name)
        if entry is None:
            return None
        return Path(entry.socket)

    def get_all_socket_paths(self) -> dict[str, Path]:
        """Get socket paths for all configured servers."""
        return {
            name: Path(entry.socket)
            for name, entry in self._config.servers.items()
        }

    async def start_all(self) -> None:
        """Start all configured MCP server processes.

        Each server is started as an asyncio subprocess. Failures are
        logged and the server is marked as FAILED — other servers still
        start.
        """
        for name, entry in self._config.servers.items():
            handle = self._handles[name]
            try:
                _logger.info(
                    "mcp_pool.starting_server",
                    extra={"server": name, "command": entry.command},
                )

                # Ensure socket parent directory exists
                socket_path = Path(entry.socket)
                socket_path.parent.mkdir(parents=True, exist_ok=True)

                # Start the server process.
                # Uses create_subprocess_exec (not shell) for safety —
                # command is split into args, no shell injection risk.
                cmd_parts = entry.command.split()
                process = await asyncio.create_subprocess_exec(
                    cmd_parts[0],
                    *cmd_parts[1:],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                handle.process = process
                handle.state = McpServerState.RUNNING

                _logger.info(
                    "mcp_pool.server_started",
                    extra={"server": name, "pid": process.pid},
                )

            except (FileNotFoundError, PermissionError, OSError) as e:
                handle.state = McpServerState.FAILED
                _logger.error(
                    "mcp_pool.server_start_failed",
                    extra={"server": name, "error": str(e)},
                )

    async def stop_all(self) -> None:
        """Stop all running MCP server processes.

        Sends SIGTERM and waits up to _STOP_TIMEOUT_SECONDS for each
        process. Already-exited processes are handled gracefully.
        """
        for name, handle in self._handles.items():
            if handle.process is None:
                handle.state = McpServerState.STOPPED
                continue

            try:
                if handle.process.returncode is None:
                    # Process is still alive — terminate it
                    handle.process.terminate()
                    try:
                        await asyncio.wait_for(
                            handle.process.wait(),
                            timeout=_STOP_TIMEOUT_SECONDS,
                        )
                    except TimeoutError:
                        _logger.warning(
                            "mcp_pool.server_kill",
                            extra={"server": name, "reason": "stop_timeout"},
                        )
                        handle.process.kill()
                        await handle.process.wait()

                _logger.info(
                    "mcp_pool.server_stopped",
                    extra={
                        "server": name,
                        "exit_code": handle.process.returncode,
                    },
                )

            except ProcessLookupError:
                _logger.debug(
                    "mcp_pool.server_already_exited",
                    extra={"server": name},
                )
            except Exception:
                _logger.warning(
                    "mcp_pool.server_stop_error",
                    extra={"server": name},
                    exc_info=True,
                )
            finally:
                handle.process = None
                handle.state = McpServerState.STOPPED

    async def health_check(self, name: str) -> bool:
        """Check if a server process is alive.

        Args:
            name: Server name.

        Returns:
            True if the server process is running, False otherwise.
        """
        handle = self._handles.get(name)
        if handle is None:
            return False
        if handle.process is None:
            return False
        # returncode is None while the process is running
        return handle.process.returncode is None

    def generate_mcp_config_file(
        self,
        workspace: Path,
        *,
        server_names: list[str] | None = None,
    ) -> Path | None:
        """Generate an MCP config JSON file for MCP-native CLI instruments.

        Produces a file compatible with claude-code's ``--mcp-config`` flag,
        pointing each server at the proxy shim which bridges to the pool's
        Unix sockets.

        The config file is written atomically (write-to-temp + rename) to
        prevent partial reads by concurrent processes.

        Args:
            workspace: Directory where the config file is written.
            server_names: Optional subset of servers to include. If None,
                all running servers are included.

        Returns:
            Path to the generated config file, or None if no servers are
            running.
        """
        names = server_names or list(self._handles.keys())

        servers_config: dict[str, object] = {}
        for name in names:
            if not self.is_running(name):
                continue
            socket_path = self.get_socket_path(name)
            if socket_path is None:
                continue
            servers_config[name] = {
                "command": sys.executable,
                "args": [
                    "-m", "marianne.daemon.mcp_proxy_shim",
                    str(socket_path),
                ],
            }

        if not servers_config:
            return None

        config_data = {"mcpServers": servers_config}
        config_path = workspace / ".mcp-pool-config.json"
        tmp_path = config_path.with_suffix(".tmp")
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(config_data, indent=2))
        tmp_path.rename(config_path)

        _logger.debug(
            "mcp_pool.config_file_generated",
            extra={
                "path": str(config_path),
                "servers": list(servers_config.keys()),
            },
        )
        return config_path
