"""JSON-RPC 2.0 wire protocol models for Marianne daemon IPC.

Defines Pydantic v2 models for the JSON-RPC 2.0 message types used over
the Unix domain socket. These models enforce the wire format at the
serialization boundary — business logic never touches raw dicts.

Wire format: newline-delimited JSON (NDJSON). Each message is a single
JSON object terminated by ``\\n``.

Versioning (#265): ``PROTOCOL_VERSION`` is the single source of truth for
the Marianne IPC protocol version. The conductor advertises it in
``daemon.health`` and ``daemon.status`` results; clients compare it against
their own constant to detect a CLI/conductor version skew at runtime
instead of failing opaquely on a wire-format change. Bump it on ANY
breaking change to method signatures, params, or result shapes (per E-001,
such changes also require escalation).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# Marianne IPC protocol version. 0 is reserved for pre-versioning
# conductors (clients treat an absent field as 0).
PROTOCOL_VERSION: int = 1

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 base types
# ---------------------------------------------------------------------------


class JsonRpcRequest(BaseModel):
    """Inbound JSON-RPC 2.0 request.

    When ``id`` is None the message is a *notification* — the server
    MUST NOT send a response.
    """

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] | None = None
    id: int | str | None = None


class ErrorDetail(BaseModel):
    """Error payload within a JSON-RPC error response."""

    code: int
    message: str
    data: dict[str, Any] | None = None


class JsonRpcResponse(BaseModel):
    """Outbound JSON-RPC 2.0 success response."""

    jsonrpc: Literal["2.0"] = "2.0"
    result: Any
    id: int | str


class JsonRpcError(BaseModel):
    """Outbound JSON-RPC 2.0 error response."""

    jsonrpc: Literal["2.0"] = "2.0"
    error: ErrorDetail
    id: int | str | None


__all__ = [
    "PROTOCOL_VERSION",
    "ErrorDetail",
    "JsonRpcError",
    "JsonRpcRequest",
    "JsonRpcResponse",
]
