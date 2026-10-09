"""Async Unix domain socket client for Marianne daemon IPC.

Provides ``DaemonClient`` with two call patterns:

- ``call(method, params)``: send a JSON-RPC request, await a single response.
  Uses a ``ConnectionPool`` to reuse connections across calls.
- ``stream(method, params)``: send a request, yield streaming notifications,
  and return the final result when the stream ends.

Plus typed convenience methods (``status``, ``submit_job``, etc.) that wrap
``call`` with Pydantic model serialization for type safety at the CLI layer.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from marianne.core.constants import SHEET_NUM_KEY
from marianne.core.logging import get_logger
from marianne.daemon.exceptions import (
    DaemonError,
    DaemonNotRunningError,
    DaemonProtocolError,
    DaemonUnresponsiveError,
    ResourceExhaustedError,
)
from marianne.daemon.ipc.errors import RESOURCE_EXHAUSTED, rpc_error_to_exception
from marianne.daemon.ipc.protocol import JsonRpcRequest
from marianne.daemon.types import DaemonStatus, JobRequest, JobResponse

_logger = get_logger("daemon.ipc.client")

# Match the server's limit so large status responses (CheckpointState with
# many sheets, synthesis_results, stdout_tail) aren't rejected by the
# StreamReader.  See server.py for rationale on the 16 MiB value.
_MAX_MESSAGE_BYTES = 16_777_216  # 16 MiB — same as server.MAX_MESSAGE_BYTES

# A response lost after sending a mutation has an unknown outcome. Only
# these explicitly read-only methods may reconnect and resend once.
_SAFE_RETRY_METHODS = frozenset({
    "daemon.health", "daemon.status", "daemon.config", "daemon.ready",
    "daemon.rate_limits", "daemon.learning.patterns", "job.list", "job.status",
    "job.errors", "job.diagnose", "job.history",
})
_HEALTH_TIMEOUT_SECONDS = 2.0
_CLOSE_TIMEOUT_SECONDS = 0.05

# Default pool parameters.
_DEFAULT_POOL_SIZE = 8
_DEFAULT_MAX_IDLE_SECONDS = 60.0
_DEFAULT_CONNECT_TIMEOUT = 5.0


class ConnectionPool:
    """LIFO connection pool for Unix domain sockets.

    Internal implementation detail — not exported.  Manages a bounded set
    of reusable ``(reader, writer)`` pairs so ``DaemonClient.call()`` does
    not pay the cost of opening a fresh socket for every RPC.

    Parameters
    ----------
    socket_path:
        Unix domain socket to connect to.
    max_size:
        Maximum number of connections (checked-out + idle combined).
    max_idle_seconds:
        Connections idle longer than this are discarded on next acquire.
    connect_timeout:
        Seconds to wait when opening a new connection.
    acquire_timeout:
        Seconds to wait for the pool semaphore when all slots are busy.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        max_size: int = _DEFAULT_POOL_SIZE,
        max_idle_seconds: float = _DEFAULT_MAX_IDLE_SECONDS,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        acquire_timeout: float = 30.0,
    ) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        if max_idle_seconds <= 0:
            raise ValueError(f"max_idle_seconds must be > 0, got {max_idle_seconds}")
        if connect_timeout <= 0:
            raise ValueError(f"connect_timeout must be > 0, got {connect_timeout}")
        if acquire_timeout <= 0:
            raise ValueError(f"acquire_timeout must be > 0, got {acquire_timeout}")

        self._socket_path = socket_path
        self._max_size = max_size
        self._max_idle_seconds = max_idle_seconds
        self._connect_timeout = connect_timeout
        self._acquire_timeout = acquire_timeout

        # LIFO idle stack: (reader, writer, idle_since_monotonic)
        self._idle: list[tuple[asyncio.StreamReader, asyncio.StreamWriter, float]] = []
        self._semaphore = asyncio.Semaphore(max_size)
        self._closed = False
        self._leased: set[asyncio.StreamWriter] = set()

    @property
    def closed(self) -> bool:
        """Whether the pool has been closed."""
        return self._closed

    async def acquire(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Acquire a connection from the pool.

        Tries idle connections first (LIFO), skipping stale or broken ones.
        Opens a new connection if no idle connection is available.

        Raises:
            DaemonNotRunningError: if the pool is closed or connection fails.
            TimeoutError: if the pool semaphore cannot be acquired in time.
        """
        if self._closed:
            raise DaemonNotRunningError("Connection pool is closed")

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self._acquire_timeout,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Pool exhausted: all {self._max_size} connections in use"
            ) from exc

        # Ownership of the slot starts here, even while opening a socket.
        # CancelledError is a BaseException and must return that capacity.
        try:
            if self._closed:
                raise DaemonNotRunningError("Connection pool is closed")
            now = time.monotonic()
            while self._idle:
                reader, writer, idle_since = self._idle.pop()
                if ((now - idle_since) > self._max_idle_seconds
                        or writer.is_closing() or reader.at_eof()):
                    self._close_writer(writer)
                    continue
                self._leased.add(writer)
                return reader, writer

            reader, writer = await self._open_connection()
            if self._closed:
                self._close_writer(writer)
                raise DaemonNotRunningError("Connection pool is closed")
            self._leased.add(writer)
            return reader, writer
        except BaseException:
            self._semaphore.release()
            raise

    def release(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Return a healthy connection to the idle stack."""
        if writer not in self._leased:
            self._close_writer(writer)
            return
        self._leased.remove(writer)
        if self._closed or writer.is_closing() or reader.at_eof():
            self._close_writer(writer)
        else:
            self._idle.append((reader, writer, time.monotonic()))
        self._semaphore.release()

    def discard(self, writer: asyncio.StreamWriter) -> None:
        """Discard a checked-out connection and return its slot once."""
        self._close_writer(writer)
        if writer in self._leased:
            self._leased.remove(writer)
            self._semaphore.release()

    async def close(self) -> None:
        """Close idle and checked-out connections and wake waiting callers."""
        if self._closed:
            return
        self._closed = True
        writers = [writer for _, writer, _ in self._idle]
        self._idle.clear()
        writers.extend(self._leased)
        for writer in list(self._leased):
            self.discard(writer)
        for writer in writers:
            self._close_writer(writer)
        if writers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(w.wait_closed() for w in writers), return_exceptions=True),
                    timeout=_CLOSE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                _logger.debug("pool_close_timeout")
        _logger.debug("pool_closed")

    async def _open_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a new Unix socket connection."""
        if not self._socket_path.exists():
            raise DaemonNotRunningError(
                f"Daemon socket not found: {self._socket_path}"
            )

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    str(self._socket_path), limit=_MAX_MESSAGE_BYTES,
                ),
                timeout=self._connect_timeout,
            )
        except TimeoutError as exc:
            raise DaemonUnresponsiveError(
                f"Timeout connecting to daemon at {self._socket_path}"
            ) from exc
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            raise DaemonNotRunningError(
                f"Cannot connect to daemon at {self._socket_path}: {exc}"
            ) from exc

        _logger.debug("pool_new_connection")
        return reader, writer

    @staticmethod
    def _close_writer(writer: asyncio.StreamWriter) -> None:
        """Close a writer, logging (not silently swallowing) close errors."""
        try:
            if not writer.is_closing():
                writer.close()
            # close() alone flushes buffered requests and can keep an FD alive
            # indefinitely under peer backpressure. A discarded lease must end
            # now; its unfinished operation already has an uncertain outcome.
            writer.transport.abort()
        except (OSError, RuntimeError) as exc:
            # A failed close can leak the fd. These errors are usually benign
            # (already-reset peer), so debug level — but #254: not silent, so
            # an fd-exhaustion pattern is visible rather than invisible.
            _logger.debug("pool_close_writer_failed", error=str(exc))


class DaemonClient:
    """Async client for the Marianne daemon Unix socket IPC.

    Parameters
    ----------
    socket_path:
        Path to the Unix domain socket created by ``DaemonServer``.
    timeout:
        Seconds to wait for a response before raising ``TimeoutError``.
    pool_size:
        Maximum number of pooled connections for ``call()``.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = 30.0,
        pool_size: int = _DEFAULT_POOL_SIZE,
    ) -> None:
        self._socket_path = socket_path
        self._timeout = timeout
        self._pool_size = pool_size
        self._next_id = 0
        self._pool: ConnectionPool | None = None

    def _next_request_id(self) -> int:
        """Return a monotonically increasing request ID."""
        self._next_id += 1
        return self._next_id

    def _get_pool(self) -> ConnectionPool:
        """Lazily create the connection pool on first use."""
        if self._pool is None or self._pool.closed:
            self._pool = ConnectionPool(
                self._socket_path,
                max_size=self._pool_size,
                acquire_timeout=self._timeout,
                connect_timeout=min(self._timeout, _DEFAULT_CONNECT_TIMEOUT),
            )
        return self._pool

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the connection pool.  Safe to call multiple times."""
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    async def __aenter__(self) -> DaemonClient:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: Any,
    ) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Connection management (for stream / is_daemon_running — unpooled)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _connect(
        self,
    ) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
        """Open a fresh (unpooled) connection to the daemon socket.

        Used by ``stream()`` and health probes which need
        dedicated connections that are not returned to the pool.

        Raises ``DaemonNotRunningError`` if the socket doesn't exist or
        the connection is refused.
        """
        if not self._socket_path.exists():
            raise DaemonNotRunningError(
                f"Daemon socket not found: {self._socket_path}"
            )

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    str(self._socket_path), limit=_MAX_MESSAGE_BYTES,
                ),
                timeout=5.0,
            )
        except TimeoutError as exc:
            raise DaemonUnresponsiveError(
                f"Timeout connecting to daemon at {self._socket_path}"
            ) from exc
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            raise DaemonNotRunningError(
                f"Cannot connect to daemon at {self._socket_path}: {exc}"
            ) from exc

        try:
            yield reader, writer
        finally:
            try:
                ConnectionPool._close_writer(writer)
                await asyncio.wait_for(writer.wait_closed(), timeout=_CLOSE_TIMEOUT_SECONDS)
            except Exception:
                _logger.debug("writer_close_failed", exc_info=True)

    # ------------------------------------------------------------------
    # RPC call (single request → single response, pooled)
    # ------------------------------------------------------------------

    async def _send_and_receive(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        request: JsonRpcRequest,
    ) -> Any:
        """Send a request and read the response on an existing connection."""
        async with asyncio.timeout(self._timeout):
            writer.write(request.model_dump_json().encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        if not line:
            raise DaemonNotRunningError("Daemon closed connection")

        response = json.loads(line)
        self._validate_response(response, request.id)
        if "error" in response:
            raise rpc_error_to_exception(response["error"])
        return response["result"]

    @staticmethod
    def _validate_response(response: Any, request_id: int | str | None) -> None:
        """Refuse corrupt or unrelated envelopes before reusing a connection."""
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
            raise DaemonProtocolError("Invalid JSON-RPC response envelope")
        if ("result" in response) == ("error" in response):
            raise DaemonProtocolError("Invalid response: expected exactly one result or error")
        if "error" in response and not isinstance(response["error"], dict):
            raise DaemonProtocolError("Invalid response error object")
        # Admission refusal precedes reading the request, so no ID is known.
        if (response.get("id") is None and "error" in response
                and response["error"].get("code") == RESOURCE_EXHAUSTED):
            raise rpc_error_to_exception(response["error"])
        if (type(response.get("id")) is not type(request_id)
                or response.get("id") != request_id):
            raise DaemonProtocolError("Invalid response: request ID does not match")

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an RPC; retry transport failures only for known safe reads.

        A timeout never retries: the daemon may still be doing the operation.
        Cancellation and corrupt responses discard the connection, preventing
        late replies from contaminating the next request.
        """
        request = JsonRpcRequest(method=method, params=params, id=self._next_request_id())
        pool = self._get_pool()
        attempts = 2 if method in _SAFE_RETRY_METHODS else 1
        for attempt in range(attempts):
            reader, writer = await pool.acquire()
            try:
                result = await self._send_and_receive(reader, writer, request)
            except (
                asyncio.CancelledError, TimeoutError, DaemonProtocolError, ResourceExhaustedError,
            ):
                pool.discard(writer)
                raise
            except (OSError, DaemonNotRunningError):
                pool.discard(writer)
                if attempt + 1 == attempts:
                    raise
                _logger.debug("pool_retry_safe_read", method=method)
            except DaemonError:
                # A valid application error is a complete matching response.
                pool.release(reader, writer)
                raise
            except BaseException:
                pool.discard(writer)
                raise
            else:
                pool.release(reader, writer)
                return result

    # ------------------------------------------------------------------
    # Streaming RPC (request → notifications* → final response)
    # ------------------------------------------------------------------

    async def stream(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a JSON-RPC request and yield streaming notifications.

        Notifications (no ``id``) are yielded as dicts.  The final
        response (with ``id``) terminates the iteration.

        If the final response contains an error, raises the
        corresponding ``DaemonError``.
        """
        request = JsonRpcRequest(
            method=method,
            params=params,
            id=self._next_request_id(),
        )

        async with self._connect() as (reader, writer):
            async with asyncio.timeout(self._timeout):
                writer.write(request.model_dump_json().encode() + b"\n")
                await writer.drain()

            async for raw_line in reader:
                if not raw_line:
                    break

                msg = json.loads(raw_line)

                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                    raise DaemonProtocolError("Invalid streaming response envelope")
                if "id" in msg or "error" in msg or "result" in msg:
                    self._validate_response(msg, request.id)
                    if "error" in msg:
                        raise rpc_error_to_exception(msg["error"])
                    return
                if not isinstance(msg.get("method"), str):
                    raise DaemonProtocolError("Invalid streaming response notification")
                params = msg.get("params", {})
                if not isinstance(params, dict):
                    raise DaemonProtocolError("Invalid streaming response notification params")
                yield params
            raise DaemonNotRunningError("Daemon closed stream before final response")

    # ------------------------------------------------------------------
    # Typed convenience methods
    # ------------------------------------------------------------------

    async def is_daemon_running(self) -> bool:
        """Check if daemon is running by performing a lightweight health RPC.

        Uses a dedicated connection with a short deadline. Missing/refused
        sockets return False; an endpoint that accepts but does not respond
        raises DaemonUnresponsiveError, preserving the difference from absence.
        """
        if not self._socket_path.exists():
            return False

        async def probe() -> None:
            request = JsonRpcRequest(method="daemon.health", id=self._next_request_id())
            async with self._connect() as (reader, writer):
                try:
                    await self._send_and_receive(reader, writer, request)
                except DaemonNotRunningError as exc:
                    raise DaemonUnresponsiveError(
                        "Conductor accepted the health probe but closed before responding"
                    ) from exc

        try:
            await asyncio.wait_for(probe(), timeout=_HEALTH_TIMEOUT_SECONDS)
        except DaemonNotRunningError:
            return False
        except TimeoutError as exc:
            raise DaemonUnresponsiveError(
                "Conductor socket exists but did not respond to the health probe"
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise DaemonProtocolError(f"Invalid conductor health response: {exc}") from exc
        return True

    async def status(self) -> DaemonStatus:
        """Get daemon status."""
        result = await self.call("daemon.status")
        return DaemonStatus(**result)

    async def submit_job(self, request: JobRequest) -> JobResponse:
        """Submit a job to the daemon."""
        result = await self.call("job.submit", request.model_dump(mode="json"))
        return JobResponse(**result)

    async def get_job_status(self, job_id: str, workspace: str) -> dict[str, Any]:
        """Get status of a specific job."""
        result = await self.call("job.status", {"job_id": job_id, "workspace": workspace})
        return cast(dict[str, Any], result)

    async def pause_job(self, job_id: str, workspace: str) -> dict[str, Any]:
        """Pause a running job. Returns ``{"paused": bool}``."""
        result = await self.call("job.pause", {"job_id": job_id, "workspace": workspace})
        return cast(dict[str, Any], result)

    async def resume_job(self, job_id: str, workspace: str) -> dict[str, Any]:
        """Resume a paused job. Returns a JobResponse dict."""
        result = await self.call("job.resume", {"job_id": job_id, "workspace": workspace})
        return cast(dict[str, Any], result)

    async def cancel_job(self, job_id: str, workspace: str) -> dict[str, Any]:
        """Cancel a running or paused job. Returns ``{"cancelled": bool}``."""
        result = await self.call("job.cancel", {"job_id": job_id, "workspace": workspace})
        return cast(dict[str, Any], result)

    async def list_jobs(self) -> list[dict[str, Any]]:
        """List all jobs known to the daemon."""
        return cast(list[dict[str, Any]], await self.call("job.list"))

    async def clear_jobs(
        self,
        statuses: list[str] | None = None,
        older_than_seconds: float | None = None,
        job_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Clear terminal jobs from the daemon registry.

        Args:
            statuses: Status filter (defaults to terminal statuses).
            older_than_seconds: Only clear jobs older than this.
            job_ids: Only clear these specific job IDs.

        Returns:
            Dict with "deleted" count.
        """
        params: dict[str, Any] = {}
        if statuses is not None:
            params["statuses"] = statuses
        if older_than_seconds is not None:
            params["older_than_seconds"] = older_than_seconds
        if job_ids is not None:
            params["job_ids"] = job_ids
        return cast(dict[str, Any], await self.call("job.clear", params))

    async def config(self) -> dict[str, Any]:
        """Get the conductor's live running configuration."""
        return cast(dict[str, Any], await self.call("daemon.config"))

    async def health(self) -> dict[str, Any]:
        """Liveness probe independent of busy pooled request connections."""
        async def probe() -> dict[str, Any]:
            request = JsonRpcRequest(method="daemon.health", id=self._next_request_id())
            async with self._connect() as (reader, writer):
                return cast(dict[str, Any], await self._send_and_receive(reader, writer, request))

        return await asyncio.wait_for(
            probe(), timeout=min(self._timeout, _HEALTH_TIMEOUT_SECONDS),
        )

    async def readiness(self) -> dict[str, Any]:
        """Readiness probe — is the daemon accepting new jobs?"""
        return cast(dict[str, Any], await self.call("daemon.ready"))

    async def get_errors(self, job_id: str, workspace: str | None = None) -> dict[str, Any]:
        """Get errors for a specific job."""
        result = await self.call("job.errors", {"job_id": job_id, "workspace": workspace})
        return cast(dict[str, Any], result)

    async def diagnose(self, job_id: str, workspace: str | None = None) -> dict[str, Any]:
        """Get diagnostic data for a specific job."""
        result = await self.call("job.diagnose", {"job_id": job_id, "workspace": workspace})
        return cast(dict[str, Any], result)

    async def get_execution_history(
        self, job_id: str, workspace: str | None = None,
        sheet_num: int | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        """Get execution history for a specific job."""
        result = await self.call("job.history", {
            "job_id": job_id, "workspace": workspace,
            SHEET_NUM_KEY: sheet_num, "limit": limit,
        })
        return cast(dict[str, Any], result)

    async def recover_job(
        self, job_id: str, workspace: str | None = None,
        sheet_num: int | None = None, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Request recovery data for a specific job."""
        result = await self.call("job.recover", {
            "job_id": job_id, "workspace": workspace,
            SHEET_NUM_KEY: sheet_num, "dry_run": dry_run,
        })
        return cast(dict[str, Any], result)

    async def rate_limits(self) -> dict[str, Any]:
        """Get current rate limit state across all backends."""
        return cast(dict[str, Any], await self.call("daemon.rate_limits"))

    async def learning_patterns(self, limit: int = 20) -> dict[str, Any]:
        """Get recent learning patterns from the global store."""
        return cast(dict[str, Any], await self.call("daemon.learning.patterns", {"limit": limit}))


__all__ = ["DaemonClient"]
