"""Async Unix domain socket server for Marianne daemon IPC.

Binds a Unix socket, accepts concurrent client connections, reads
newline-delimited JSON-RPC 2.0 requests, dispatches them through
``RequestHandler``, and writes responses back.  Handles connection
lifecycle and socket cleanup on shutdown.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from pydantic import BaseModel

from marianne.core.logging import get_logger
from marianne.daemon.ipc.errors import invalid_request, parse_error
from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.protocol import JsonRpcRequest
from marianne.daemon.task_utils import log_task_exception

_logger = get_logger("daemon.ipc.server")

# Maximum size of a single JSON-RPC message.
# Must accommodate full CheckpointState payloads — jobs with many sheets,
# stdout_tail (up to 10 KB/sheet), synthesis_results, and config_snapshot
# can easily reach 4-8 MB.  Bumped from 1 MiB after real-world jobs
# (21 sheets) exceeded the limit and broke `mzt status`.
MAX_MESSAGE_BYTES = 16_777_216  # 16 MiB

# Default limit on concurrent client connections.
DEFAULT_MAX_CONNECTIONS = 20


class DaemonServer:
    """Async Unix domain socket server with JSON-RPC 2.0 routing.

    Parameters
    ----------
    socket_path:
        Filesystem path for the Unix domain socket.
    handler:
        ``RequestHandler`` that dispatches JSON-RPC methods.
    permissions:
        Octal file permissions applied to the socket after creation.
        Defaults to ``0o660`` (owner + group read/write).
    max_connections:
        Maximum concurrent client connections.
    """

    def __init__(
        self,
        socket_path: Path,
        handler: RequestHandler,
        *,
        permissions: int = 0o660,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
    ) -> None:
        self._socket_path = socket_path
        self._handler = handler
        self._permissions = permissions
        self._max_connections = max_connections
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self._connection_semaphore = asyncio.Semaphore(max_connections)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the Unix socket and start accepting connections."""
        # Ensure parent directory exists
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Reject symlinks to prevent redirection attacks
        if self._socket_path.is_symlink():
            raise OSError(
                f"Socket path is a symlink (possible attack): {self._socket_path}"
            )
        # Remove stale socket from a previous unclean shutdown
        self._socket_path.unlink(missing_ok=True)

        self._server = await asyncio.start_unix_server(
            self._accept_connection,
            path=str(self._socket_path),
            limit=MAX_MESSAGE_BYTES,
        )
        os.chmod(self._socket_path, self._permissions)

        _logger.info(
            "ipc_server_started",
            socket_path=str(self._socket_path),
            max_connections=self._max_connections,
        )

    async def stop(self) -> None:
        """Stop the server, cancel active connections, and remove the socket."""
        if self._server is None:
            return

        # Stop accepting new connections
        self._server.close()
        await self._server.wait_closed()

        # Cancel all active connection tasks
        for task in self._connections:
            task.cancel()
        if self._connections:
            results = await asyncio.gather(*self._connections, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    _logger.warning(
                        "ipc_server.connection_exception_during_stop",
                        error=str(result),
                        error_type=type(result).__name__,
                    )
        self._connections.clear()

        # Clean up socket file
        self._socket_path.unlink(missing_ok=True)
        self._server = None

        _logger.info("ipc_server_stopped")

    @property
    def is_running(self) -> bool:
        """Return whether the server is currently accepting connections."""
        return self._server is not None and self._server.is_serving()

    # ------------------------------------------------------------------
    # Wire format
    # ------------------------------------------------------------------

    @staticmethod
    async def _write_response(
        writer: asyncio.StreamWriter, response: BaseModel,
    ) -> None:
        """Serialize a JSON-RPC response and write it to the stream."""
        writer.write(response.model_dump_json().encode() + b"\n")
        await writer.drain()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _accept_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Wrap each connection in a tracked task with a concurrency limit."""
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
            task.add_done_callback(self._on_connection_done)

        async with self._connection_semaphore:
            await self._handle_connection(reader, writer)

    def _on_connection_done(self, task: asyncio.Task[None]) -> None:
        """Log exceptions from connection tasks before discarding."""
        self._connections.discard(task)
        log_task_exception(task, _logger, "connection_task_failed", level="warning")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process requests on a single client connection until it closes."""
        peer = writer.get_extra_info("peername") or "unknown"
        _logger.debug("client_connected", peer=str(peer))

        try:
            while True:
                try:
                    line = await reader.readline()
                except asyncio.LimitOverrunError:
                    # Message exceeded MAX_MESSAGE_BYTES; buffer is in an
                    # indeterminate state so we must close the connection.
                    _logger.warning("message_too_large", peer=str(peer))
                    await self._write_response(writer, parse_error())
                    break

                if not line:
                    break  # Client disconnected

                # Enforce max message size (belt-and-suspenders)
                if len(line) > MAX_MESSAGE_BYTES:
                    await self._write_response(writer, parse_error())
                    continue

                await self._process_message(line, writer)
        except ConnectionResetError:
            _logger.debug("client_disconnected", peer=str(peer), reason="reset")
        except asyncio.CancelledError:
            _logger.debug("client_disconnected", peer=str(peer), reason="cancelled")
        except (OSError, RuntimeError) as exc:
            _logger.warning(
                "connection_error",
                peer=str(peer),
                error=str(exc),
            )
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, RuntimeError):
                _logger.debug("writer_close_failed", peer=str(peer), exc_info=True)
            _logger.debug("client_disconnected", peer=str(peer))

    async def _process_message(
        self,
        line: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Parse one NDJSON line and route through the handler."""
        # Parse JSON
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            await self._write_response(writer, parse_error())
            return

        # Validate JSON-RPC structure
        if not isinstance(raw, dict) or "method" not in raw:
            error_resp = invalid_request(
                raw.get("id") if isinstance(raw, dict) else None,
                "missing 'method' field",
            )
            await self._write_response(writer, error_resp)
            return

        # Build typed request
        try:
            request = JsonRpcRequest.model_validate(raw)
        except Exception as exc:
            await self._write_response(
                writer, invalid_request(raw.get("id"), str(exc)),
            )
            return

        # Dispatch to handler
        response = await self._handler.handle(request, writer)

        # Handler returns None for notifications or streaming methods
        if response is not None:
            await self._write_response(writer, response)


__all__ = ["DaemonServer"]
