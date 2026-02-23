"""Async Unix domain socket client for Mozart daemon IPC.

Provides ``DaemonClient`` with two call patterns:

- ``call(method, params)``: send a JSON-RPC request, await a single response.
- ``stream(method, params)``: send a request, yield streaming notifications,
  and return the final result when the stream ends.

Plus typed convenience methods (``status``, ``submit_job``, etc.) that wrap
``call`` with Pydantic model serialization for type safety at the CLI layer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from mozart.core.logging import get_logger
from mozart.daemon.exceptions import DaemonNotRunningError
from mozart.daemon.ipc.errors import rpc_error_to_exception
from mozart.daemon.ipc.protocol import JsonRpcRequest
from mozart.daemon.types import DaemonStatus, JobRequest, JobResponse

_logger = get_logger("daemon.ipc.client")

# Match the server's limit so large status responses (CheckpointState with
# many sheets, synthesis_results, stdout_tail) aren't rejected by the
# StreamReader.  See server.py for rationale on the 16 MiB value.
_MAX_MESSAGE_BYTES = 16_777_216  # 16 MiB — same as server.MAX_MESSAGE_BYTES


class DaemonClient:
    """Async client for the Mozart daemon Unix socket IPC.

    Parameters
    ----------
    socket_path:
        Path to the Unix domain socket created by ``DaemonServer``.
    timeout:
        Seconds to wait for a response before raising ``TimeoutError``.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._socket_path = socket_path
        self._timeout = timeout
        self._next_id = 0

    def _next_request_id(self) -> int:
        """Return a monotonically increasing request ID."""
        self._next_id += 1
        return self._next_id

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _connect(
        self,
    ) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
        """Open a connection to the daemon socket.

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
            raise DaemonNotRunningError(
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
                writer.close()
                await writer.wait_closed()
            except Exception:
                _logger.debug("writer_close_failed", exc_info=True)

    # ------------------------------------------------------------------
    # RPC call (single request → single response)
    # ------------------------------------------------------------------

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a JSON-RPC request and return the result.

        Raises:
            DaemonNotRunningError: socket unreachable
            DaemonError (subclass): server returned an error response
            TimeoutError: no response within ``self._timeout``
        """
        request = JsonRpcRequest(
            method=method,
            params=params,
            id=self._next_request_id(),
        )

        async with self._connect() as (reader, writer):
            writer.write(request.model_dump_json().encode() + b"\n")
            await writer.drain()

            line = await asyncio.wait_for(
                reader.readline(), timeout=self._timeout
            )
            if not line:
                raise DaemonNotRunningError("Daemon closed connection")

            response = json.loads(line)
            if "error" in response:
                raise rpc_error_to_exception(response["error"])
            return response.get("result")

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
            writer.write(request.model_dump_json().encode() + b"\n")
            await writer.drain()

            async for raw_line in reader:
                if not raw_line:
                    break

                msg = json.loads(raw_line)

                # Check if this is the final response (has our request id)
                msg_id = msg.get("id")
                if msg_id is not None:
                    # Final response — stream is done
                    if "error" in msg:
                        raise rpc_error_to_exception(msg["error"])
                    return

                # Notification — yield params to caller
                yield msg.get("params", {})

    # ------------------------------------------------------------------
    # Typed convenience methods
    # ------------------------------------------------------------------

    async def is_daemon_running(self) -> bool:
        """Check if daemon is running by performing a lightweight health RPC.

        Uses ``daemon.health`` instead of bare socket connect so stale
        sockets left by crashed daemons are properly detected.

        Short-circuits immediately if the socket path doesn't exist,
        consistent with ``_connect()``'s guard.
        """
        if not self._socket_path.exists():
            return False
        try:
            await self.call("daemon.health")
            return True
        except (ConnectionRefusedError, FileNotFoundError, TimeoutError, OSError):
            return False
        except Exception:
            # Any other failure (malformed response, protocol error, etc.)
            # means the daemon is not functional.
            return False

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
        """Liveness probe — is the daemon process alive?"""
        return cast(dict[str, Any], await self.call("daemon.health"))

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
            "sheet_num": sheet_num, "limit": limit,
        })
        return cast(dict[str, Any], result)

    async def recover_job(
        self, job_id: str, workspace: str | None = None,
        sheet_num: int | None = None, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Request recovery data for a specific job."""
        result = await self.call("job.recover", {
            "job_id": job_id, "workspace": workspace,
            "sheet_num": sheet_num, "dry_run": dry_run,
        })
        return cast(dict[str, Any], result)


__all__ = ["DaemonClient"]
