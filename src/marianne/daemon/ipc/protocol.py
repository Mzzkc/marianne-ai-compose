"""JSON-RPC 2.0 wire protocol models for Marianne daemon IPC.

Defines Pydantic v2 models for the JSON-RPC 2.0 message types used over
the Unix domain socket. These models enforce the wire format at the
serialization boundary — business logic never touches raw dicts.

Wire format: newline-delimited JSON (NDJSON). Each message is a single
JSON object terminated by ``\\n``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

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
    "ErrorDetail",
    "JsonRpcError",
    "JsonRpcRequest",
    "JsonRpcResponse",
]
