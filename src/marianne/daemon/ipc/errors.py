"""JSON-RPC 2.0 error codes and helper functions for Marianne daemon IPC.

Maps the daemon exception hierarchy (``DaemonError`` and subclasses) to
standard and Marianne-extension JSON-RPC error codes, plus convenience
builders for the most common error responses.
"""

from __future__ import annotations

from typing import Any

from marianne.daemon.exceptions import (
    DaemonAlreadyRunningError,
    DaemonError,
    JobSubmissionError,
    MethodNotFoundError,
    ResourceExhaustedError,
)
from marianne.daemon.ipc.protocol import ErrorDetail, JsonRpcError

# ---------------------------------------------------------------------------
# Standard JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# ---------------------------------------------------------------------------
# Marianne extension error codes (-32000 to -32099)
# ---------------------------------------------------------------------------

JOB_NOT_FOUND = -32000
RESOURCE_EXHAUSTED = -32001
JOB_ALREADY_RUNNING = -32002
DAEMON_SHUTTING_DOWN = -32003
JOB_NOT_RESUMABLE = -32004
WORKSPACE_NOT_FOUND = -32005


# ---------------------------------------------------------------------------
# Error response builders
# ---------------------------------------------------------------------------


def make_error(
    code: int,
    message: str,
    request_id: int | str | None,
    data: dict[str, Any] | None = None,
) -> JsonRpcError:
    """Build a ``JsonRpcError`` with the given code and message."""
    return JsonRpcError(
        error=ErrorDetail(code=code, message=message, data=data),
        id=request_id,
    )


def parse_error(request_id: int | str | None = None) -> JsonRpcError:
    """Malformed JSON received."""
    return make_error(PARSE_ERROR, "Parse error: malformed JSON", request_id)


def invalid_request(
    request_id: int | str | None, detail: str = ""
) -> JsonRpcError:
    """Missing ``jsonrpc``, ``method``, or wrong types."""
    msg = "Invalid request"
    if detail:
        msg = f"{msg}: {detail}"
    return make_error(INVALID_REQUEST, msg, request_id)


def method_not_found(
    request_id: int | str | None, method: str
) -> JsonRpcError:
    """Unknown RPC method name."""
    return make_error(
        METHOD_NOT_FOUND,
        f"Method not found: {method}",
        request_id,
        data={"method": method},
    )


def invalid_params(
    request_id: int | str | None, detail: str
) -> JsonRpcError:
    """Params failed Pydantic validation."""
    return make_error(INVALID_PARAMS, f"Invalid params: {detail}", request_id)


def internal_error(
    request_id: int | str | None, detail: str = ""
) -> JsonRpcError:
    """Unexpected server exception."""
    msg = "Internal error"
    if detail:
        msg = f"{msg}: {detail}"
    return make_error(INTERNAL_ERROR, msg, request_id)


# ---------------------------------------------------------------------------
# Exception → JSON-RPC error mapping
# ---------------------------------------------------------------------------

# Maps daemon exceptions to JSON-RPC error codes for the wire format.
# JobSubmissionError covers several job-related failures (not found, invalid
# config, workspace conflicts) — the error *message* carries the specifics,
# while the code identifies the exception class for client-side reconstruction.
_EXCEPTION_CODE_MAP: dict[type[DaemonError], int] = {
    JobSubmissionError: JOB_NOT_FOUND,
    ResourceExhaustedError: RESOURCE_EXHAUSTED,
    DaemonAlreadyRunningError: JOB_ALREADY_RUNNING,
}


def map_exception_to_rpc_error(
    exc: DaemonError, request_id: int | str | None
) -> JsonRpcError:
    """Convert a ``DaemonError`` into the appropriate ``JsonRpcError``."""
    code = _EXCEPTION_CODE_MAP.get(type(exc), INTERNAL_ERROR)
    return make_error(code, str(exc), request_id)


# ---------------------------------------------------------------------------
# JSON-RPC error → exception mapping (used by client)
# ---------------------------------------------------------------------------

_CODE_EXCEPTION_MAP: dict[int, type[DaemonError]] = {
    JOB_NOT_FOUND: JobSubmissionError,
    RESOURCE_EXHAUSTED: ResourceExhaustedError,
    JOB_ALREADY_RUNNING: DaemonAlreadyRunningError,
    DAEMON_SHUTTING_DOWN: DaemonError,
    JOB_NOT_RESUMABLE: JobSubmissionError,
    WORKSPACE_NOT_FOUND: JobSubmissionError,
    METHOD_NOT_FOUND: MethodNotFoundError,
}


def rpc_error_to_exception(error: dict[str, Any]) -> DaemonError:
    """Convert a JSON-RPC error dict into the appropriate ``DaemonError``."""
    code = error.get("code", INTERNAL_ERROR)
    message = error.get("message", "Unknown error")
    exc_cls = _CODE_EXCEPTION_MAP.get(code, DaemonError)
    return exc_cls(message)


__all__ = [
    "DAEMON_SHUTTING_DOWN",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JOB_ALREADY_RUNNING",
    "JOB_NOT_FOUND",
    "JOB_NOT_RESUMABLE",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "RESOURCE_EXHAUSTED",
    "WORKSPACE_NOT_FOUND",
    "internal_error",
    "invalid_params",
    "invalid_request",
    "make_error",
    "map_exception_to_rpc_error",
    "method_not_found",
    "parse_error",
    "rpc_error_to_exception",
]
