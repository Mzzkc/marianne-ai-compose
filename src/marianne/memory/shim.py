"""The musician-side memory shim: a stdio MCP server in front of one gateway socket.

A harness launches ``python -m marianne.memory.shim --socket <path>
[--memory-budget-seconds <float>]``. The shim speaks MCP over stdio
(JSON-RPC 2.0, one message per line; standard library only) and forwards each
of the six memory tools over the socket with
:class:`marianne.memory.gateway.client.MemoryGatewayClient`.

It carries no identity. The socket *is* the session: the shim never sees, sends
or advertises a session id, principal, scope, grant or credential, and its tool
schemas list only each call's own arguments. Every call's timeout is clamped to
the memory budget so the shim never outlives the gateway's own outer budget.

Failure policy: a gateway refusal (``errors/1``) is an MCP tool result with
``isError: true`` carrying code, subtype, message and remedy; a protocol fault
(bad JSON, unknown method, bad params, oversized line) is a JSON-RPC error.
A socket that is gone at startup ends the process with a non-zero status and one
stderr line; one that disappears later yields a typed tool error per call.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from marianne.memory.errors import MemoryBackendError
from marianne.memory.gateway.client import MemoryGatewayClient

SHIM_NAME = "marianne-memory-shim"
SHIM_VERSION = "1"
DEFAULT_BUDGET_SECONDS = 10.0
#: A single input line over this many bytes is refused, never buffered whole.
MAX_LINE_BYTES = 1_048_576
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object", "properties": props, "required": required,
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_INT = {"type": "integer", "minimum": 0}

#: tool name -> (description, input schema). Only the call's own arguments.
TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "declare": (
        "Record a fact in memory, with the evidence it rests on.",
        _schema({
            "fact": {"type": "string", "description": "The claim to remember."},
            "provenance": {
                "type": "object",
                "description": "Where the claim comes from; include evidence_locator.",
                "properties": {"evidence_locator": _STR},
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "tags": {"type": "array", "items": _STR},
        }, ["fact"]),
    ),
    "recall": (
        "Search memory; the answer names its sources and how to resume a truncated page.",
        _schema({
            "query": _STR,
            "limit": {"type": "integer", "minimum": 1},
            "cursor": {"type": "string", "description": "Resume point from a previous recall."},
        }, ["query"]),
    ),
    "verify": (
        "Re-check a fact against its evidence before acting on it.",
        _schema({"fact_id_or_hash": _STR, "expected_evidence": _STR}, ["fact_id_or_hash"]),
    ),
    "bind": (
        "Bind a verified fact to an action target.",
        _schema({"fact_id": _STR, "target_symbol": _STR, "evidence_receipt": _STR},
                ["fact_id", "target_symbol"]),
    ),
    "forget": (
        "Retire a fact, with a reason.",
        _schema({"fact_id": _STR, "reason": _STR}, ["fact_id"]),
    ),
    "fetch": (
        "Read a byte range of an oversized unit named by a recall.",
        _schema({"unit_id": _STR, "offset": _INT, "length": {"type": "integer", "minimum": 1}},
                ["unit_id", "offset", "length"]),
    ),
}

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _check_args(tool: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise _RpcError(INVALID_PARAMS, "arguments must be an object")
    schema = TOOLS[tool][1]
    props: dict[str, Any] = schema["properties"]
    for key in args:
        if key not in props:
            raise _RpcError(INVALID_PARAMS, f"unknown argument {key!r} for {tool}")
    for key in schema["required"]:
        if key not in args:
            raise _RpcError(INVALID_PARAMS, f"missing argument {key!r} for {tool}")
    return dict(args)


def _tool_result(payload: Any, *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _error_payload(exc: MemoryBackendError) -> dict[str, Any]:
    return {
        "code": exc.code.name, "name": exc.code.value, "subtype": exc.subtype,
        "message": exc.detail, "remedy": exc.remedy,
    }


class MemoryShim:
    """Handles MCP messages; one short-lived socket connection per tool call."""

    def __init__(self, socket_path: Path | str, budget_seconds: float = DEFAULT_BUDGET_SECONDS):
        if not math.isfinite(budget_seconds) or budget_seconds <= 0:
            raise ValueError("memory budget must be a positive, finite number of seconds")
        self._path = str(socket_path)
        self._budget = float(budget_seconds)

    def check_socket(self) -> None:
        """Raise a typed unreachable error unless the gateway socket accepts a connection."""
        MemoryGatewayClient(self._path, timeout=self._budget).close()

    # -------------------------------------------------------------- dispatch

    def handle_message(self, msg: Any) -> dict[str, Any] | None:
        """One decoded JSON-RPC message in, one response (or ``None`` for a notification)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._fail(None, INVALID_REQUEST, "not a JSON-RPC 2.0 request object")
        method = msg.get("method")
        has_id = "id" in msg
        msg_id = msg.get("id")
        if has_id and (isinstance(msg_id, bool) or not isinstance(msg_id, (str, int))):
            return self._fail(None, INVALID_REQUEST, "id must be a string or an integer")
        if not isinstance(method, str):
            if has_id and ("result" in msg or "error" in msg):
                return None  # a response to something we never asked; ignore
            return self._fail(msg_id if has_id else None, INVALID_REQUEST, "method is required")
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return self._fail(
                msg_id if has_id else None, INVALID_PARAMS, "params must be an object",
            )
        if not has_id:
            return None  # notifications (notifications/initialized, cancelled, ...) get no reply
        try:
            return {"jsonrpc": "2.0", "id": msg_id, "result": self._dispatch(method, params)}
        except _RpcError as exc:
            return self._fail(msg_id, exc.code, exc.message)

    @staticmethod
    def _fail(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            wanted = params.get("protocolVersion")
            version = wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SHIM_NAME, "version": SHIM_VERSION},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [
                {"name": name, "description": desc, "inputSchema": schema}
                for name, (desc, schema) in TOOLS.items()
            ]}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in TOOLS:
                raise _RpcError(INVALID_PARAMS, f"unknown tool {name!r}")
            args = _check_args(name, params.get("arguments", {}))
            return self._call_tool(name, args)
        raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    def _call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            client = MemoryGatewayClient(self._path, timeout=self._budget)
        except MemoryBackendError as exc:
            return _tool_result(_error_payload(exc), is_error=True)
        try:
            result = client.call(name, budget_seconds=self._budget, **args)
        except MemoryBackendError as exc:
            return _tool_result(_error_payload(exc), is_error=True)
        finally:
            client.close()
        return _tool_result(result, is_error=False)


def _read_line(stream: BinaryIO, limit: int) -> tuple[bytes | None, bool]:
    """Return ``(line, too_long)``; ``(None, False)`` at EOF. Holds at most limit+1 bytes."""
    line = stream.readline(limit + 1)
    if not line:
        return None, False
    if len(line) > limit and not line.endswith(b"\n"):
        while True:  # discard the rest of the oversized line in bounded chunks
            chunk = stream.readline(65536)
            if not chunk or chunk.endswith(b"\n"):
                break
        return b"", True
    return line, False


def serve(
    shim: MemoryShim, stdin: BinaryIO, stdout: BinaryIO, *, max_line_bytes: int = MAX_LINE_BYTES,
) -> None:
    """Run the stdio loop until stdin reaches EOF."""

    def send(obj: dict[str, Any]) -> None:
        stdout.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        stdout.flush()

    while True:
        line, too_long = _read_line(stdin, max_line_bytes)
        if line is None:
            return
        if too_long:
            send(MemoryShim._fail(
                None, INVALID_REQUEST, f"line exceeds the {max_line_bytes} byte limit",
            ))
            continue
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            send(MemoryShim._fail(None, PARSE_ERROR, "invalid JSON"))
            continue
        if isinstance(msg, list):
            send(MemoryShim._fail(None, INVALID_REQUEST, "batches are not supported"))
            continue
        reply = shim.handle_message(msg)
        if reply is not None:
            send(reply)


def main(
    argv: list[str] | None = None, *, stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None, stderr: Callable[[str], object] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m marianne.memory.shim", add_help=True)
    parser.add_argument("--socket", required=True, help="path of the gateway socket")
    parser.add_argument(
        "--memory-budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS,
        help="clamp on every call's timeout (default 10.0)",
    )
    ns = parser.parse_args(argv)
    err = stderr or (lambda s: sys.stderr.write(s + "\n"))
    try:
        shim = MemoryShim(ns.socket, ns.memory_budget_seconds)
    except ValueError as exc:
        err(f"memory shim: {exc}")
        return 2
    try:
        shim.check_socket()
    except MemoryBackendError as exc:
        err(f"memory shim: the memory socket is not available ({exc.detail})")
        return 1
    serve(shim, stdin or sys.stdin.buffer, stdout or sys.stdout.buffer)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
