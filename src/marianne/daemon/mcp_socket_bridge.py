"""Multiplexed Unix-socket bridge for stdio MCP servers.

The bridge owns one upstream stdio MCP server process and exposes a Unix socket
for multiple MCP clients. Client request IDs are rewritten to bridge-unique
upstream IDs, then restored on the way back to the originating client.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from marianne.core.logging import get_logger
from marianne.daemon.socket_ownership import OwnedUnixSocket

_logger = get_logger("daemon.mcp_socket_bridge")

McpFraming = Literal["newline", "content-length"]
_READ_CHUNK_LIMIT = 1024 * 1024
_WRITE_TIMEOUT_SECONDS = 10.0
_CLOSE_TIMEOUT_SECONDS = 0.5
_UPSTREAM_STOP_TIMEOUT_SECONDS = 10.0
_UPSTREAM_KILL_TIMEOUT_SECONDS = 2.0


@dataclass(eq=False)
class _Client:
    client_id: int
    writer: asyncio.StreamWriter
    framing: McpFraming | None = None


@dataclass
class _PendingClientResponse:
    client: _Client
    original_id: Any


class McpSocketBridge:
    """Expose one stdio MCP server process through a multiplexed Unix socket."""

    def __init__(
        self,
        *,
        name: str,
        command: str,
        socket_path: Path,
        upstream_framing: McpFraming = "newline",
    ) -> None:
        self.name = name
        self.command = command
        self.socket_path = socket_path
        self.upstream_framing = upstream_framing

        self.process: asyncio.subprocess.Process | None = None
        self._server: asyncio.AbstractServer | None = None
        self._socket_owner = OwnedUnixSocket(socket_path)
        self._clients: set[_Client] = set()
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._stop_task: asyncio.Task[None] | None = None
        self._pending: dict[int, _PendingClientResponse | asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._client_id = 0
        self._write_lock = asyncio.Lock()
        self._upstream_reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._initialize_result: dict[str, Any] = {}
        self._stopping = False

    @property
    def is_running(self) -> bool:
        """Whether the upstream process and socket server are both alive."""
        return (
            self.process is not None
            and self.process.returncode is None
            and self._server is not None
        )

    async def start(self) -> None:
        """Start the upstream process, initialize it, and bind the socket."""
        if self.is_running:
            return

        if self._stop_task is not None and not self._stop_task.done():
            await self.stop()
        self._stop_task = None
        self._stopping = False
        cmd_parts = shlex.split(self.command)
        if not cmd_parts:
            raise ValueError("MCP server command must not be empty")

        try:
            await self._socket_owner.prepare()
            process = await asyncio.create_subprocess_exec(
                cmd_parts[0],
                *cmd_parts[1:],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.process = process
            if process.stdin is None or process.stdout is None:
                raise RuntimeError(f"MCP server {self.name!r} did not expose stdio pipes")

            self._upstream_reader_task = asyncio.create_task(
                self._read_upstream_loop(),
                name=f"mcp-bridge-{self.name}-upstream",
            )
            if process.stderr is not None:
                self._stderr_task = asyncio.create_task(
                    self._drain_stderr(process.stderr),
                    name=f"mcp-bridge-{self.name}-stderr",
                )

            self._initialize_result = await self._initialize_upstream()
            self._server = await self._socket_owner.start(self._handle_client)
        except BaseException:
            await self.stop()
            raise

        _logger.info(
            "mcp_bridge.started",
            extra={
                "server": self.name,
                "pid": process.pid,
                "socket": str(self.socket_path),
            },
        )

    async def stop(self) -> None:
        """Finish owned cleanup before propagating caller cancellation."""
        self._stopping = True
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(
                self._stop_resources(),
                name=f"mcp-bridge-{self.name}-stop",
            )
        cleanup = self._stop_task
        cancelled = False
        # The pool can discard its bridge handle when stop is canceled. Keep
        # cleanup strongly owned and shielded, and do not return until the
        # upstream has been reaped and our socket lifetime lock released.
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    @staticmethod
    async def _close_client_writer(writer: asyncio.StreamWriter) -> None:
        """Discard buffered output and bound waiting for transport closure."""
        writer.close()
        writer.transport.abort()
        try:
            await asyncio.wait_for(writer.wait_closed(), _CLOSE_TIMEOUT_SECONDS)
        except (OSError, RuntimeError, TimeoutError):
            pass

    async def _stop_resources(self) -> None:
        server, self._server = self._server, None
        clients = list(self._clients)
        self._clients.clear()
        try:
            if server is not None:
                server.close()
            # Abort every client before any await: one unread client must not
            # hold the remaining clients, upstream, or lifetime lock hostage.
            for client in clients:
                client.writer.close()
                client.writer.transport.abort()

            for pending in list(self._pending.values()):
                if isinstance(pending, asyncio.Future) and not pending.done():
                    pending.cancel()
            self._pending.clear()

            tasks = [
                task
                for task in (
                    self._upstream_reader_task,
                    self._stderr_task,
                    *self._client_tasks,
                )
                if task is not None
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._upstream_reader_task = None
            self._stderr_task = None
            self._client_tasks.clear()

            closing = [self._close_client_writer(client.writer) for client in clients]
            if server is not None:
                closing.append(server.wait_closed())
            if closing:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*closing, return_exceptions=True),
                        _CLOSE_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    _logger.debug("mcp_bridge.close_timeout", extra={"server": self.name})
        finally:
            try:
                if self.process is not None:
                    await self._stop_upstream(self.process)
            finally:
                self.process = None
                self._socket_owner.release()
        _logger.info("mcp_bridge.stopped", extra={"server": self.name})

    async def _stop_upstream(self, process: asyncio.subprocess.Process) -> None:
        """Reap the owned child while draining pipes that can delay wait()."""
        if process.stdin is not None:
            process.stdin.close()

        async def drain(reader: asyncio.StreamReader) -> None:
            while await reader.read(65536):
                pass

        drains = [
            asyncio.create_task(drain(reader))
            for reader in (process.stdout, process.stderr)
            if reader is not None
        ]
        try:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
            try:
                await asyncio.wait_for(process.wait(), _UPSTREAM_STOP_TIMEOUT_SECONDS)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await asyncio.wait_for(process.wait(), _UPSTREAM_KILL_TIMEOUT_SECONDS)
        finally:
            for task in drains:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*drains, return_exceptions=True)

    async def _initialize_upstream(self) -> dict[str, Any]:
        result = await self._send_upstream_request(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "clientInfo": {
                        "name": "marianne-mcp-pool",
                        "version": "0.1.0",
                    },
                },
            }
        )
        await self._send_upstream_notification(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        initialize_result = result.get("result", {})
        return initialize_result if isinstance(initialize_result, dict) else {}

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        self._client_id += 1
        client = _Client(client_id=self._client_id, writer=writer)
        self._clients.add(client)

        try:
            while not self._stopping:
                read = await _read_jsonrpc_message(reader)
                if read is None:
                    break
                message, framing = read
                if client.framing is None:
                    client.framing = framing
                await self._handle_client_message(client, message)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            self._clients.discard(client)
            for key, pending in list(self._pending.items()):
                if isinstance(pending, _PendingClientResponse) and pending.client is client:
                    self._pending.pop(key, None)
            try:
                await self._close_client_writer(writer)
            finally:
                if task is not None:
                    self._client_tasks.discard(task)

    async def _handle_client_message(
        self,
        client: _Client,
        message: dict[str, Any],
    ) -> None:
        method = message.get("method")
        original_id = message.get("id")

        if method == "initialize" and "id" in message:
            await self._write_client_message(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": original_id,
                    "result": self._initialize_result,
                },
            )
            return

        if method == "notifications/initialized" and "id" not in message:
            return

        upstream = dict(message)
        if "id" in upstream:
            upstream_id = self._next_request_id()
            upstream["id"] = upstream_id
            self._pending[upstream_id] = _PendingClientResponse(
                client=client,
                original_id=original_id,
            )

        await self._write_upstream_message(upstream)

    async def _send_upstream_request(self, message: dict[str, Any]) -> dict[str, Any]:
        upstream_id = self._next_request_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        message = dict(message)
        message["id"] = upstream_id
        self._pending[upstream_id] = future
        await self._write_upstream_message(message)
        return await asyncio.wait_for(future, timeout=30.0)

    async def _send_upstream_notification(self, message: dict[str, Any]) -> None:
        await self._write_upstream_message(message)

    async def _write_upstream_message(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError(f"MCP bridge {self.name!r} has no upstream stdin")
        async with self._write_lock:
            self.process.stdin.write(_encode_jsonrpc_message(message, self.upstream_framing))
            await asyncio.wait_for(self.process.stdin.drain(), _WRITE_TIMEOUT_SECONDS)

    async def _read_upstream_loop(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        try:
            while not self._stopping:
                read = await _read_jsonrpc_message(self.process.stdout)
                if read is None:
                    break
                message, _framing = read
                await self._route_upstream_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._stopping:
                _logger.warning(
                    "mcp_bridge.upstream_read_failed",
                    extra={"server": self.name},
                    exc_info=True,
                )

    async def _route_upstream_message(self, message: dict[str, Any]) -> None:
        if "id" not in message:
            for client in list(self._clients):
                await self._write_client_message(client, message)
            return

        upstream_id = message.get("id")
        if not isinstance(upstream_id, int):
            return
        pending = self._pending.pop(upstream_id, None)
        if pending is None:
            return

        if isinstance(pending, asyncio.Future):
            if not pending.done():
                pending.set_result(message)
            return

        response = dict(message)
        response["id"] = pending.original_id
        await self._write_client_message(pending.client, response)

    async def _write_client_message(
        self,
        client: _Client,
        message: dict[str, Any],
    ) -> None:
        framing = client.framing or "content-length"
        try:
            client.writer.write(_encode_jsonrpc_message(message, framing))
            await asyncio.wait_for(client.writer.drain(), _WRITE_TIMEOUT_SECONDS)
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            self._clients.discard(client)
            await self._close_client_writer(client.writer)

    async def _drain_stderr(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                _logger.debug(
                    "mcp_bridge.stderr",
                    extra={
                        "server": self.name,
                        "line": line.decode(errors="replace").rstrip(),
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id


async def _read_jsonrpc_message(
    reader: asyncio.StreamReader,
) -> tuple[dict[str, Any], McpFraming] | None:
    while True:
        line = await reader.readline()
        if not line:
            return None
        if line.strip():
            break

    if line.lower().startswith(b"content-length:"):
        length = int(line.split(b":", 1)[1].strip())
        while True:
            header = await reader.readline()
            if not header:
                return None
            if header in (b"\r\n", b"\n"):
                break
        body = await reader.readexactly(length)
        return json.loads(body.decode("utf-8")), "content-length"

    if len(line) > _READ_CHUNK_LIMIT:
        raise ValueError("MCP JSON-RPC line exceeds read limit")
    return json.loads(line.decode("utf-8")), "newline"


def _encode_jsonrpc_message(message: dict[str, Any], framing: McpFraming) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if framing == "content-length":
        return f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    return body + b"\n"
