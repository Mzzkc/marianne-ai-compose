"""Shared MCP server pool manager for the conductor.

The conductor-side MCP pool starts one process per configured MCP server and,
for stdio servers, exposes that process through a multiplexed Unix socket
bridge. Multiple MCP clients can connect through the proxy shim; request IDs
are rewritten upstream and restored on the way back to the originating client.

Lifecycle:
- ``start_all()`` — starts all configured servers on daemon startup
- ``stop_all()`` — terminates all servers on daemon shutdown
- ``health_check(name)`` — verifies a server process is alive
- ``get_socket_path(name)`` — returns the socket path for agent access

The manager owns the stdio-to-socket bridge for ``transport: stdio`` entries.
Native socket/http transports can still use their configured socket/path as a
future extension.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
from enum import Enum
from pathlib import Path

from marianne.core.logging import get_logger
from marianne.daemon.config import McpPoolConfig
from marianne.daemon.mcp_socket_bridge import McpFraming, McpSocketBridge

_logger = get_logger("daemon.mcp_pool")

# Timeout for waiting on process termination during shutdown
_STOP_TIMEOUT_SECONDS = 10.0
_MONITOR_INTERVAL_SECONDS = 1.0
_RESTART_BACKOFF_SECONDS = 1.0
_MAX_RESTART_ATTEMPTS = 3
# A short-lived successful spawn does not reset a crash loop's retry budget.
_RESTART_STABLE_SECONDS = 30.0


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

    __slots__ = (
        "bridge", "name", "next_restart_at", "process", "restart_attempts", "started_at", "state",
    )

    def __init__(self, name: str) -> None:
        self.name = name
        self.process: asyncio.subprocess.Process | None = None
        self.bridge: McpSocketBridge | None = None
        self.state = McpServerState.STOPPED
        self.restart_attempts = 0
        self.next_restart_at = 0.0
        self.started_at = 0.0


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
        self._monitor_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._lifecycle_lock = asyncio.Lock()

    def server_names(self) -> list[str]:
        """Return the names of all configured servers."""
        return list(self._config.servers.keys())

    def is_running(self, name: str) -> bool:
        """Check if a server is currently running.

        Reconciles the recorded state against the live process/bridge: a handle
        recorded as RUNNING whose process has exited (or whose bridge is dead)
        is transitioned to FAILED as a side effect, so config generation and
        status inventories exclude the dead server immediately.
        """
        handle = self._handles.get(name)
        if handle is None or handle.state != McpServerState.RUNNING:
            return False
        alive = (
            handle.bridge.is_running if handle.bridge is not None
            else handle.process is not None and handle.process.returncode is None
        )
        if not alive:
            handle.state = McpServerState.FAILED
        return alive

    def get_status(self) -> dict[str, McpServerState]:
        """Get the state of all servers."""
        for name in self._handles:
            self.is_running(name)
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
        """Start the pool once and supervise it until stop_all()."""
        async with self._lifecycle_lock:
            if self._monitor_task is not None and not self._monitor_task.done():
                return
            self._stopping = False
            try:
                for name, handle in self._handles.items():
                    handle.restart_attempts = 0
                    handle.next_restart_at = 0.0
                    await self._start_server(name)
            except BaseException:
                await self._stop_all()
                raise
            if self._handles:
                self._monitor_task = asyncio.create_task(self._monitor(), name="mcp-pool-monitor")

    async def _start_server(self, name: str) -> None:
        """Use the same startup path for initial launch and supervised restarts."""
        entry = self._config.servers[name]
        handle = self._handles[name]
        try:
            _logger.info(
                "mcp_pool.starting_server",
                extra={"server": name, "command": entry.command},
            )

            if entry.transport == "stdio":
                framing: McpFraming = (
                    "content-length"
                    if entry.framing == "content-length"
                    else "newline"
                )
                bridge = McpSocketBridge(
                    name=name,
                    command=entry.command,
                    socket_path=Path(entry.socket),
                    upstream_framing=framing,
                )
                handle.bridge = bridge
                await bridge.start()
                handle.process = bridge.process
            else:
                # Non-stdio transports are still lifecycle-managed only.
                socket_path = Path(entry.socket)
                socket_path.parent.mkdir(parents=True, exist_ok=True)
                cmd_parts = shlex.split(entry.command)
                if not cmd_parts:
                    raise ValueError("MCP server command must not be empty")
                process = await asyncio.create_subprocess_exec(
                    cmd_parts[0],
                    *cmd_parts[1:],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                handle.process = process
            handle.state = McpServerState.RUNNING
            handle.started_at = asyncio.get_running_loop().time()

            _logger.info(
                "mcp_pool.server_started",
                extra={
                    "server": name,
                    "pid": handle.process.pid if handle.process else None,
                    "transport": entry.transport,
                },
            )

        except (
            FileNotFoundError,
            PermissionError,
            OSError,
            RuntimeError,
            TimeoutError,
            ValueError,
        ) as e:
            handle.state = McpServerState.FAILED
            _logger.error(
                "mcp_pool.server_start_failed",
                extra={"server": name, "error": str(e)},
            )

    async def _monitor(self) -> None:
        """Restart exited servers according to policy, with a bounded crash budget."""
        while not self._stopping:
            await asyncio.sleep(_MONITOR_INTERVAL_SECONDS)
            for name, handle in self._handles.items():
                if self._stopping:
                    return
                now = asyncio.get_running_loop().time()
                if self.is_running(name):
                    if now - handle.started_at >= _RESTART_STABLE_SECONDS:
                        handle.restart_attempts = 0
                    continue
                if handle.state != McpServerState.FAILED:
                    continue
                policy = self._config.servers[name].restart_policy
                exit_code = handle.process.returncode if handle.process is not None else None
                if policy == "never" or (policy == "on-failure" and exit_code == 0):
                    continue
                if handle.restart_attempts >= _MAX_RESTART_ATTEMPTS or now < handle.next_restart_at:
                    continue
                handle.restart_attempts += 1
                await self._stop_server(name)
                if self._stopping:
                    return
                await self._start_server(name)
                handle.next_restart_at = (
                    asyncio.get_running_loop().time()
                    + _RESTART_BACKOFF_SECONDS * 2 ** (handle.restart_attempts - 1)
                )

    async def stop_all(self) -> None:
        """Cancel and join supervision before stopping any server."""
        async with self._lifecycle_lock:
            await self._stop_all()

    async def _stop_all(self) -> None:
        """Clean up with lifecycle ownership, also on cancelled initial startup."""
        self._stopping = True
        monitor, self._monitor_task = self._monitor_task, None
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        for name in self._handles:
            await self._stop_server(name)

    async def _stop_server(self, name: str) -> None:
        """Release the old process and socket before shutdown or a restart."""
        handle = self._handles[name]
        if handle.bridge is not None:
            try:
                await handle.bridge.stop()
            except Exception:  # noqa: BLE001 — structured logger reports mcp_pool.bridge_stop_error
                _logger.warning(
                    "mcp_pool.bridge_stop_error",
                    extra={"server": name},
                    exc_info=True,
                )
            finally:
                handle.bridge = None
                handle.process = None
                handle.state = McpServerState.STOPPED
            return

        if handle.process is None:
            handle.state = McpServerState.STOPPED
            return

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
        except Exception:  # noqa: BLE001 — structured logger reports mcp_pool.server_stop_error
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
        if handle.bridge is not None:
            return handle.bridge.is_running
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
