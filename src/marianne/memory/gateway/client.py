"""A small synchronous client for the memory gateway endpoint.

Connect to a socket path, call a tool, get the result or the typed
``errors/1`` exception back. Intended for a musician-side shim. It sends only
``{id, tool, args, budget_seconds?}``; it has no way to name a session,
principal or scope, because the socket is the session. See
:mod:`marianne.memory.gateway.endpoint` for the wire format and for the exact
(same-UID, not sibling-proof) security property.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from types import TracebackType
from typing import Any

from marianne.memory.errors import (
    ERROR_CLASSES,
    BackendUnreachable,
    ErrorCode,
    MemoryBackendError,
)

DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class MemoryGatewayClient:
    def __init__(
        self, path: Path | str, *, timeout: float = 30.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._max = max_response_bytes
        self._next_id = 0
        self._buf = b""
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(str(path))
        except OSError as exc:
            self._sock.close()
            raise BackendUnreachable(
                f"cannot connect to the memory gateway socket: {exc.strerror or exc}",
                subtype="connect_failed", remedy="the session may have closed",
            ) from None

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> MemoryGatewayClient:
        return self

    def __exit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None,
    ) -> None:
        self.close()

    def _readline(self) -> bytes:
        while b"\n" not in self._buf:
            if len(self._buf) > self._max:
                raise BackendUnreachable("response too large", subtype="response_too_large")
            try:
                chunk = self._sock.recv(65536)
            except (TimeoutError, OSError) as exc:
                raise BackendUnreachable(
                    f"no response from the gateway: {exc}", subtype="transport_failed",
                ) from None
            if not chunk:
                raise BackendUnreachable(
                    "the gateway closed the connection", subtype="connection_closed",
                )
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    def call(self, tool: str, budget_seconds: float | None = None, **args: Any) -> Any:
        """Call ``tool``; return its result or raise the typed error from ``errors/1``."""
        self._next_id += 1
        req: dict[str, Any] = {"id": self._next_id, "tool": tool, "args": args}
        if budget_seconds is not None:
            req["budget_seconds"] = budget_seconds
        try:
            self._sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        except OSError as exc:
            raise BackendUnreachable(
                f"cannot send to the gateway: {exc}", subtype="transport_failed",
            ) from None
        try:
            frame = json.loads(self._readline())
        except ValueError:
            raise BackendUnreachable(
                "the gateway sent an unreadable frame", subtype="bad_frame",
            ) from None
        if not isinstance(frame, dict):
            raise BackendUnreachable("the gateway sent a bad frame", subtype="bad_frame")
        if frame.get("ok") is True:
            return frame.get("result")
        err = frame.get("error")
        if not isinstance(err, dict):
            raise BackendUnreachable("the gateway sent a bad error frame", subtype="bad_frame")
        raise _typed(err)

    def declare(self, fact: str, **kw: Any) -> Any:
        return self.call("declare", fact=fact, **kw)

    def recall(self, query: str, **kw: Any) -> Any:
        return self.call("recall", query=query, **kw)

    def verify(self, fact_id_or_hash: str, **kw: Any) -> Any:
        return self.call("verify", fact_id_or_hash=fact_id_or_hash, **kw)

    def bind(self, fact_id: str, target_symbol: str, **kw: Any) -> Any:
        return self.call("bind", fact_id=fact_id, target_symbol=target_symbol, **kw)

    def forget(self, fact_id: str, **kw: Any) -> Any:
        return self.call("forget", fact_id=fact_id, **kw)

    def fetch(self, unit_id: str, offset: int, length: int) -> Any:
        return self.call("fetch", unit_id=unit_id, offset=offset, length=length)


def _typed(err: dict[str, Any]) -> MemoryBackendError:
    try:
        code = ErrorCode[str(err.get("code"))]
    except KeyError:
        return BackendUnreachable(
            f"unknown error code {err.get('code')!r}: {err.get('message', '')}",
            subtype="unknown_error_code",
        )
    return ERROR_CLASSES[code](
        str(err.get("message", "")), subtype=str(err.get("subtype", "")),
        remedy=str(err.get("remedy", "")),
    )


__all__ = ["MemoryGatewayClient"]
