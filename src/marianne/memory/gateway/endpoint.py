"""The memory gateway's Unix-socket endpoint (spec 02 BE1, BE2 teardown, BE7).

One listening socket per session, bound when the session opens
(:meth:`MemoryEndpoint.open_session`) and unlinked when it closes
(:meth:`MemoryEndpoint.close_session`). The session IS the socket: a request
never names a session, a principal or a scope.

Security property, stated exactly
---------------------------------
Admission is *same UID*, read from ``SO_PEERCRED`` at accept. That stops other
users on the host. It does NOT stop another process of the same user that
learns the socket path: such a process is admitted to that session like any
other. The only protections against siblings are an unguessable per-dispatch
path (a SHA-256 prefix, see :mod:`.paths`), living inside a ``0700``
directory, and one session per socket. No sibling isolation is claimed beyond
that. No token travels on the channel.

Wire format (``memory-gateway-wire/1``)
---------------------------------------
Newline-delimited JSON, UTF-8, one frame per line.

* request:  ``{"id": <str|int>, "tool": <name>, "args": {...}, "budget_seconds"?: <number>}``
* success:  ``{"id": ..., "ok": true, "result": {...}}``
* failure:  ``{"id": ..., "ok": false, "error": {"code": "E#", "name": ...,
  "subtype": ..., "message": ..., "remedy": ...}}`` (``errors/1``)

Requests on one connection are served strictly in order. A client-supplied
``session``/``session_id``/``principal``/``scope``/``permissions``/... field
(top level or inside ``args``) is dropped, audited as a refused attempt
(``result_status: refused``, ``ignored_fields``), and echoed as
``ignored_fields`` in the response; the call proceeds on the socket's session.

Failure policy: a malformed line, non-object, unknown tool or bad arguments
gets a typed E7 frame and the connection stays open (framing is intact). A
line over the length limit, a peer refused at accept, a connection over the
per-session limit and an idle connection are closed (after one frame where a
frame can be sent). The gateway's calls are synchronous and run in a worker
thread; if the client disconnects mid-call the wait is cancelled and the
endpoint audits ``cancelled`` (a thread cannot be interrupted, so the backend
call may still complete and the gateway will also record its own outcome).
Expiry of a session is enforced by the gateway on every call (E10); the
socket stays bound until ``close_session``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marianne.memory.errors import (
    BackendUnreachable,
    MemoryBackendError,
    SchemaViolation,
    Unauthenticated,
)
from marianne.memory.gateway.gateway import TOOL_PERMISSIONS, Gateway
from marianne.memory.gateway.paths import socket_path_for
from marianne.memory.gateway.session import Session

WIRE_VERSION = "memory-gateway-wire/1"

#: Names a client may never supply; they are ignored and audited.
FORBIDDEN_FIELDS = frozenset({
    "session", "session_id", "principal", "scope", "permissions", "dispatch_id",
    "job_id", "attachment_id", "attachment", "grant", "access",
})


@dataclass(frozen=True)
class EndpointLimits:
    max_line_bytes: int = 1_048_576
    max_connections_per_session: int = 4
    idle_timeout_seconds: float = 60.0


PeerUidReader = Callable[[socket.socket], int | None]


def read_peer_uid(sock: socket.socket) -> int | None:
    """The peer's UID from ``SO_PEERCRED`` (Linux), or ``None`` when unavailable."""
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except (OSError, AttributeError):
        return None
    _pid, uid, _gid = struct.unpack("3i", raw)
    return int(uid)


def _error_frame(frame_id: Any, exc: MemoryBackendError) -> dict[str, Any]:
    return {"id": frame_id, "ok": False, "error": {
        "code": exc.code.name, "name": exc.code.value, "subtype": exc.subtype,
        "message": exc.detail, "remedy": exc.remedy,
    }}


@dataclass
class _Bound:
    session_id: str
    path: Path
    server: asyncio.base_events.Server
    writers: set[asyncio.StreamWriter]


class MemoryEndpoint:
    """Hosts one Unix socket per gateway session under ``run_dir``."""

    def __init__(
        self, gateway: Gateway, *, run_dir: Path | str, limits: EndpointLimits | None = None,
        peer_uid_reader: PeerUidReader = read_peer_uid, uid: int | None = None,
    ) -> None:
        self._gateway = gateway
        self._run_dir = Path(run_dir)
        self._limits = limits or EndpointLimits()
        self._peer_uid = peer_uid_reader
        self._uid = os.getuid() if uid is None else uid
        self._bound: dict[str, _Bound] = {}

    # ---------------------------------------------------------- lifecycle

    async def open_session(self, **kwargs: Any) -> tuple[Session, Path]:
        """Admit a session on the gateway and bind its socket. Returns both."""
        session = self._gateway.open_session(**kwargs)
        try:
            path = await self.bind(session)
        except BaseException:
            self._gateway.close_session(session.session_id)
            raise
        return session, path

    async def bind(self, session: Session) -> Path:
        path = socket_path_for(
            self._run_dir, session.job_id, session.dispatch_id, session.attachment[0],
        )
        if session.session_id in self._bound:
            return self._bound[session.session_id].path
        with contextlib.suppress(FileNotFoundError):
            if path.is_socket() and path.lstat().st_uid == os.getuid():
                path.unlink()  # a stale socket from a crashed run
        writers: set[asyncio.StreamWriter] = set()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await self._serve(session.session_id, writers, reader, writer)

        server = await asyncio.start_unix_server(
            handler, path=os.fspath(path), limit=self._limits.max_line_bytes,
        )
        os.chmod(path, 0o600)
        self._bound[session.session_id] = _Bound(session.session_id, path, server, writers)
        return path

    async def close_session(self, session_id: str) -> None:
        """Stop listening, drop connections, unlink the socket, close the gateway session."""
        await self.unbind(session_id)
        self._gateway.close_session(session_id)

    async def unbind(self, session_id: str) -> None:
        bound = self._bound.pop(session_id, None)
        if bound is None:
            return
        bound.server.close()
        for w in list(bound.writers):
            w.close()
        with contextlib.suppress(FileNotFoundError):
            bound.path.unlink()
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(bound.server.wait_closed(), timeout=2.0)

    async def close(self) -> None:
        for sid in list(self._bound):
            await self.close_session(sid)

    def socket_path(self, session_id: str) -> Path:
        return self._bound[session_id].path

    # ---------------------------------------------------------- connection

    async def _send(self, writer: asyncio.StreamWriter, frame: dict[str, Any]) -> None:
        writer.write((json.dumps(frame, ensure_ascii=False, sort_keys=True) + "\n").encode())
        await writer.drain()

    async def _serve(
        self, session_id: str, writers: set[asyncio.StreamWriter],
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        try:
            sock = writer.get_extra_info("socket")
            uid = self._peer_uid(sock) if sock is not None else None
            if uid is None or uid != self._uid:
                await self._send(writer, _error_frame(None, Unauthenticated(
                    "peer credentials do not match the gateway's user",
                    subtype="peer_uid_mismatch",
                )))
                return
            if len(writers) >= self._limits.max_connections_per_session:
                await self._send(writer, _error_frame(None, BackendUnreachable(
                    "too many connections to this session", subtype="connection_limit",
                    remedy="reuse an open connection",
                )))
                return
            writers.add(writer)
            while True:
                try:
                    line = await asyncio.wait_for(
                        reader.readline(), timeout=self._limits.idle_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    return
                except (ValueError, asyncio.LimitOverrunError):
                    await self._send(writer, _error_frame(None, SchemaViolation(
                        f"request line exceeds {self._limits.max_line_bytes} bytes",
                        subtype="line_too_long",
                    )))
                    return
                if not line:
                    return
                if not line.endswith(b"\n"):
                    return  # EOF in the middle of a frame
                if len(line) > self._limits.max_line_bytes:
                    await self._send(writer, _error_frame(None, SchemaViolation(
                        f"request line exceeds {self._limits.max_line_bytes} bytes",
                        subtype="line_too_long",
                    )))
                    return
                await self._send(writer, await self._handle(session_id, reader, line))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _handle(
        self, session_id: str, reader: asyncio.StreamReader, line: bytes,
    ) -> dict[str, Any]:
        frame_id: Any = None
        try:
            try:
                req = json.loads(line)
            except (ValueError, RecursionError):
                raise SchemaViolation("request is not valid JSON", subtype="malformed_json",
                                      remedy="send one JSON object per line") from None
            if not isinstance(req, dict):
                raise SchemaViolation("request must be a JSON object", subtype="malformed_request")
            frame_id = req.get("id")
            if isinstance(frame_id, bool) or not isinstance(frame_id, (str, int, type(None))):
                frame_id = None
                raise SchemaViolation("id must be a string or integer", subtype="malformed_request")
            tool = req.get("tool")
            if not isinstance(tool, str) or tool not in TOOL_PERMISSIONS:
                raise SchemaViolation(
                    f"unknown tool {tool!r}", subtype="unknown_tool",
                    remedy=f"one of {sorted(TOOL_PERMISSIONS)}",
                )
            args = req.get("args", {})
            if not isinstance(args, dict):
                raise SchemaViolation("args must be an object", subtype="malformed_request")
            ignored = sorted(
                {k for k in req if k in FORBIDDEN_FIELDS} | {k for k in args if k in FORBIDDEN_FIELDS}
            )
            args = {k: v for k, v in args.items() if k not in FORBIDDEN_FIELDS}
            budget = req.get("budget_seconds")
            if budget is not None:
                if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
                    raise SchemaViolation("budget_seconds must be a positive number",
                                          subtype="malformed_request")
                args["budget_seconds"] = float(budget)
            method = getattr(self._gateway, tool)
            try:
                inspect.signature(method).bind(session_id, **args)
            except TypeError as exc:
                raise SchemaViolation(f"bad arguments for {tool}: {exc}",
                                      subtype="bad_arguments") from None
            if ignored:
                with contextlib.suppress(MemoryBackendError):
                    self._gateway.audit_endpoint_event(
                        session_id, tool=tool, result_status="refused",
                        ignored_fields=ignored,
                    )
            result = await self._call(session_id, tool, method, args, reader)
            out: dict[str, Any] = {"id": frame_id, "ok": True, "result": result}
            if ignored:
                out["ignored_fields"] = ignored
            json.dumps(out)  # fail here, typed, rather than mid-write
            return out
        except MemoryBackendError as exc:
            return _error_frame(frame_id, exc)
        except (TypeError, ValueError):
            return _error_frame(frame_id, BackendUnreachable(
                "the result could not be encoded", subtype="backend_fault",
            ))

    async def _call(
        self, session_id: str, tool: str, method: Callable[..., Any],
        args: dict[str, Any], reader: asyncio.StreamReader,
    ) -> Any:
        task = asyncio.ensure_future(asyncio.to_thread(method, session_id, **args))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.02)
                if done:
                    return task.result()
                if reader.at_eof():
                    raise asyncio.CancelledError
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(MemoryBackendError):
                self._gateway.audit_endpoint_event(
                    session_id, tool=tool, result_status="cancelled",
                    abandoned_thread=True,
                )
            # the worker thread cannot be interrupted; swallow its eventual outcome
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            raise


__all__ = [
    "FORBIDDEN_FIELDS", "WIRE_VERSION", "EndpointLimits", "MemoryEndpoint", "read_peer_uid",
]
