"""Async Unix domain socket server for Marianne daemon IPC.

Binds a Unix socket, accepts concurrent client connections, reads
newline-delimited JSON-RPC 2.0 requests, dispatches them through
``RequestHandler``, and writes responses back.  Handles connection
lifecycle and socket cleanup on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from pydantic import BaseModel

from marianne.core.logging import get_logger
from marianne.daemon.ipc.errors import (
    internal_error,
    invalid_request,
    parse_error,
    peer_denied,
    resource_exhausted,
)
from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.peercred import read_peer_credentials
from marianne.daemon.ipc.protocol import JsonRpcRequest
from marianne.daemon.socket_ownership import OwnedUnixSocket
from marianne.daemon.task_utils import log_task_exception

_logger = get_logger("daemon.ipc.server")

# Maximum size of a single JSON-RPC message.
# Must accommodate full CheckpointState payloads — jobs with many sheets,
# stdout_tail (up to 10 KB/sheet), synthesis_results, and config_snapshot
# can easily reach 4-8 MB.  Bumped from 1 MiB after real-world jobs
# (21 sheets) exceeded the limit and broke `mzt status`.
MAX_MESSAGE_BYTES = 16_777_216  # 16 MiB

# Default limit on simultaneous connected clients (FD protection).
DEFAULT_MAX_CONNECTIONS = 500

# Default limit on concurrently processing requests.
DEFAULT_MAX_CONCURRENT_REQUESTS = 50
DEFAULT_MAX_STREAMS = 50

# Per-readline idle timeout (#310). The IPC protocol is a local Unix-socket
# control channel; the CLI sends its request immediately and disconnects, so a
# connection that idles this long between complete messages is dead or stuck and
# is holding a connection slot for nothing. On expiry the server closes the
# connection (it does NOT send a JSON-RPC error — a non-reading peer can't
# receive it). Generous by default; configurable for tighter FD hygiene.
DEFAULT_READ_IDLE_TIMEOUT = 300.0


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
        Maximum simultaneous connected clients.  FD protection — idle
        connections are cheap, so the default is high (~500).
    max_concurrent_requests:
        Maximum requests being processed at once across all connections.
        This is the real concurrency control (~50).
    read_idle_timeout:
        Seconds a connection may sit between complete messages before the
        server closes it (dead/stuck client FD hygiene).
    enforce_peer_uid:
        Refuse connections whose peer UID differs from the daemon's
        effective UID.  Defense-in-depth over socket file permissions —
        the 0o660 mode admits the owning *group*, and root bypasses file
        modes entirely.  On platforms where the peer UID cannot be read
        (no ``SO_PEERCRED``) the gate fails open, because absence of the
        credential is not evidence of a foreign peer.  Unix credentials
        are user-granular: this stops *other users*, not other processes
        running as the same user.
    """

    def __init__(
        self,
        socket_path: Path,
        handler: RequestHandler,
        *,
        permissions: int = 0o660,
        backlog: int = 100,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
        max_streams: int = DEFAULT_MAX_STREAMS,
        request_timeout: float = 300.0,
        write_timeout: float = 10.0,
        read_idle_timeout: float = DEFAULT_READ_IDLE_TIMEOUT,
        enforce_peer_uid: bool = True,
    ) -> None:
        if backlog < 1 or max_streams < 1:
            raise ValueError("backlog and max_streams must be >= 1")
        if request_timeout <= 0 or write_timeout <= 0:
            raise ValueError("request_timeout and write_timeout must be > 0")
        if max_connections < 1:
            raise ValueError(f"max_connections must be >= 1, got {max_connections}")
        if max_concurrent_requests < 1:
            raise ValueError(
                f"max_concurrent_requests must be >= 1, got {max_concurrent_requests}"
            )
        if read_idle_timeout <= 0:
            raise ValueError(
                f"read_idle_timeout must be > 0, got {read_idle_timeout}"
            )

        self._socket_path = socket_path
        self._handler = handler
        self._permissions = permissions
        self._backlog = backlog
        self._max_connections = max_connections
        self._max_concurrent_requests = max_concurrent_requests
        self._read_idle_timeout = read_idle_timeout
        self._request_timeout = request_timeout
        self._write_timeout = write_timeout
        self._enforce_peer_uid = enforce_peer_uid
        self._server: asyncio.AbstractServer | None = None
        self._socket_owner = OwnedUnixSocket(socket_path)
        self._stop_task: asyncio.Task[None] | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self._connection_semaphore = asyncio.Semaphore(max_connections)
        self._request_semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._stream_semaphore = asyncio.Semaphore(max_streams)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the Unix socket and start accepting connections."""
        if self._stop_task is not None and not self._stop_task.done():
            raise RuntimeError("IPC server shutdown is still in progress")
        self._stop_task = None
        self._server = await self._socket_owner.start(
            self._accept_connection,
            permissions=self._permissions,
            backlog=self._backlog,
            limit=MAX_MESSAGE_BYTES,
        )

        _logger.info(
            "ipc_server_started",
            socket_path=str(self._socket_path),
            max_connections=self._max_connections,
            max_concurrent_requests=self._max_concurrent_requests,
        )

    async def stop(self) -> None:
        """Finish owned cleanup before propagating caller cancellation."""
        if self._stop_task is None:
            if self._server is None:
                return
            self._stop_task = asyncio.create_task(
                self._stop_resources(), name="ipc-server-stop",
            )
        cleanup = self._stop_task
        cancelled = False
        # A canceled caller must not cancel handler finalizers or abandon the
        # lifetime lock. Concurrent stop callers share the same owned cleanup.
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop_resources(self) -> None:
        server, self._server = self._server, None
        try:
            if server is not None:
                server.close()
                await server.wait_closed()
            tasks = list(self._connections)
            for task in tasks:
                task.cancel()
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        _logger.warning(
                            "ipc_server.connection_exception_during_stop",
                            error=str(result),
                            error_type=type(result).__name__,
                        )
        finally:
            self._connections.clear()
            self._socket_owner.release()
        _logger.info("ipc_server_stopped")

    @property
    def is_running(self) -> bool:
        """Return whether the server is currently accepting connections."""
        return self._server is not None and self._server.is_serving()

    # ------------------------------------------------------------------
    # Wire format
    # ------------------------------------------------------------------

    async def _write_response(
        self, writer: asyncio.StreamWriter, response: BaseModel,
    ) -> None:
        """Serialize a JSON-RPC response and write it to the stream."""
        writer.write(response.model_dump_json().encode() + b"\n")
        await asyncio.wait_for(writer.drain(), self._write_timeout)

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _accept_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Wrap each connection in a tracked task with a connection limit.

        The connection semaphore gates how many clients can be connected
        simultaneously (FD protection).  Request concurrency is controlled
        separately in ``_process_message`` via ``_request_semaphore``.
        """
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
            task.add_done_callback(self._on_connection_done)

        # Do not queue accepted sockets behind an exhausted admission gate:
        # their FDs already exist, and departed queued clients cannot reach
        # the read timeout. Semaphore acquisition below cannot suspend after
        # this check because no task switch occurs between check and acquire.
        if self._connection_semaphore.locked():
            try:
                await self._write_response(
                    writer, resource_exhausted(None, "IPC connection capacity exhausted"),
                )
            except (OSError, RuntimeError):
                pass
            finally:
                writer.close()
                with contextlib.suppress(OSError, RuntimeError):
                    await asyncio.wait_for(writer.wait_closed(), self._write_timeout)
            return
        async with self._connection_semaphore:
            await self._handle_connection(reader, writer)

    def _on_connection_done(self, task: asyncio.Task[None]) -> None:
        """Log exceptions from connection tasks before discarding."""
        self._connections.discard(task)
        log_task_exception(task, _logger, "connection_task_failed", level="warning")

    def _read_peer_uid(self, writer: asyncio.StreamWriter) -> int | None:
        """Peer's UID, or ``None`` when the platform cannot report it."""
        creds = read_peer_credentials(writer)
        return None if creds is None else creds.uid

    async def _admit_peer(
        self, writer: asyncio.StreamWriter, peer: object
    ) -> bool:
        """Peer-UID admission gate.

        Denies peers whose UID differs from the daemon's effective UID,
        answers them with ``IPC_PEER_DENIED`` (so the failure is
        diagnosable, not a silent hang), and logs the attempt.  Fails
        open only when the platform provides no peer credential at all.
        """
        peer_uid = self._read_peer_uid(writer)
        if peer_uid is None:
            _logger.warning("ipc_peercred_unavailable", peer=str(peer))
            return True

        expected_uid = os.geteuid()
        if peer_uid == expected_uid:
            return True

        _logger.warning(
            "ipc_peer_denied",
            peer=str(peer),
            client_uid=peer_uid,
            expected_uid=expected_uid,
            socket_path=str(self._socket_path),
        )
        await self._write_response(
            writer,
            peer_denied(
                None,
                f"peer uid {peer_uid} is not the conductor owner "
                f"(uid {expected_uid}); IPC clients must run as the "
                "conductor's user, or set socket.enforce_peer_uid=false",
            ),
        )
        return False

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process requests on a single client connection until it closes."""
        peer = writer.get_extra_info("peername") or "unknown"
        _logger.debug("client_connected", peer=str(peer))

        try:
            if self._enforce_peer_uid and not await self._admit_peer(
                writer, peer
            ):
                return

            while not writer.is_closing():
                try:
                    line = await asyncio.wait_for(
                        reader.readline(),
                        timeout=self._read_idle_timeout,
                    )
                except TimeoutError:
                    # Idle too long between complete messages (#310): a dead or
                    # stuck client holding a connection slot. Close it — don't
                    # send a JSON-RPC error, the peer isn't reading.
                    _logger.debug(
                        "client_idle_timeout",
                        peer=str(peer),
                        timeout=self._read_idle_timeout,
                    )
                    break
                except (asyncio.LimitOverrunError, ValueError):
                    # Message exceeded MAX_MESSAGE_BYTES; buffer is in an
                    # indeterminate state so we must close the connection.
                    _logger.warning("message_too_large", peer=str(peer))
                    await self._write_response(writer, parse_error())
                    break
                except ConnectionResetError:
                    # asyncio.wait_for can surface a reset through this path
                    # rather than the outer handler; treat it as a clean drop.
                    break

                if not line:
                    break  # Client disconnected

                # Enforce max message size (belt-and-suspenders)
                if len(line) > MAX_MESSAGE_BYTES:
                    await self._write_response(writer, parse_error())
                    continue

                await self._process_message(line, writer, reader)
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
                try:
                    await asyncio.wait_for(writer.wait_closed(), self._write_timeout)
                except TimeoutError:
                    # close() can keep flushing forever to a peer that never
                    # reads. Abort discards that buffered output and frees FD.
                    writer.transport.abort()
            except (OSError, RuntimeError):
                _logger.debug("writer_close_failed", peer=str(peer), exc_info=True)
            _logger.debug("client_disconnected", peer=str(peer))

    async def _process_message(
        self,
        line: bytes,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
    ) -> None:
        """Parse one NDJSON line and route through the handler.

        The request semaphore limits how many requests are being processed
        concurrently across all connections.  Parsing and validation happen
        outside the semaphore — only handler dispatch is gated.
        """
        # Parse JSON (outside semaphore — cheap, no I/O)
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            await self._write_response(writer, parse_error())
            return

        # Validate JSON-RPC structure (outside semaphore — cheap)
        if not isinstance(raw, dict) or "method" not in raw:
            error_resp = invalid_request(
                raw.get("id") if isinstance(raw, dict) else None,
                "missing 'method' field",
            )
            await self._write_response(writer, error_resp)
            return

        # Build typed request (outside semaphore — validation only)
        try:
            request = JsonRpcRequest.model_validate(raw)
        except Exception as exc:
            await self._write_response(
                writer, invalid_request(raw.get("id"), str(exc)),
            )
            return

        _logger.debug("request_processing", method=request.method, request_id=request.id)
        if self._handler.is_streaming(request.method):
            if self._stream_semaphore.locked():
                if request.id is not None:
                    await self._write_response(
                        writer, resource_exhausted(request.id, "IPC stream capacity exhausted"),
                    )
                return
            async with self._stream_semaphore:
                response = await self._run_stream(request, reader, writer)
            # Stream clients own a dedicated connection. Do not resume reading
            # RPCs after their handler (or EOF watcher) has consumed the stream.
            if response is not None:
                await self._write_response(writer, response)
            writer.close()
            return

        try:
            # Also bound time waiting for ordinary dispatch capacity.
            async with asyncio.timeout(self._request_timeout):
                async with self._request_semaphore:
                    response = await self._handler.handle(request, writer)
        except TimeoutError:
            _logger.warning("ipc_request_timeout", method=request.method, request_id=request.id)
            response = None if request.id is None else internal_error(
                request.id, "IPC request deadline exceeded; operation outcome may be uncertain",
            )

        # Handler returns None for notifications or streaming methods
        if response is not None:
            await self._write_response(writer, response)

    async def _run_stream(
        self, request: JsonRpcRequest, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> BaseModel | None:
        """Cancel subscriptions on peer EOF even when their producer is silent.

        Ordinary mutating handlers deliberately do not use this path: a client
        leaving must not cancel an accepted submission or another durable write.
        Streaming connections are dedicated and accept no subsequent requests.
        """
        dispatch = asyncio.create_task(self._handler.handle(request, writer))
        disconnected = asyncio.create_task(reader.read(1))
        try:
            done, _ = await asyncio.wait(
                (dispatch, disconnected), return_when=asyncio.FIRST_COMPLETED,
            )
            if dispatch in done:
                return await dispatch
            return None
        finally:
            for task in (dispatch, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(dispatch, disconnected, return_exceptions=True)


__all__ = ["DaemonServer"]
