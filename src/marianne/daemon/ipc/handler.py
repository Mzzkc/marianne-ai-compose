"""JSON-RPC request handler — routes methods to async callables.

Provides a generic ``RequestHandler`` that maps JSON-RPC method names
(e.g. ``"daemon.status"``, ``"job.submit"``) to async handler functions.
Handles param validation errors and unknown methods, returning proper
JSON-RPC error responses.

The handler is decoupled from business logic — actual method
implementations are registered externally (by the daemon process in
Phase 2).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from marianne.core.logging import get_logger
from marianne.daemon.exceptions import DaemonError
from marianne.daemon.ipc.errors import (
    internal_error,
    invalid_params,
    map_exception_to_rpc_error,
    method_not_found,
)
from marianne.daemon.ipc.protocol import (
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
)

_logger = get_logger("daemon.ipc.handler")

# Type alias for an RPC method handler.
# Receives (params_dict, writer) and returns the result value.
MethodHandler = Callable[
    [dict[str, Any], asyncio.StreamWriter],
    Coroutine[Any, Any, Any],
]


class RequestHandler:
    """Routes JSON-RPC 2.0 requests to registered async method handlers.

    Each handler receives ``(params: dict, writer: StreamWriter)`` and
    returns the ``result`` value to embed in the JSON-RPC response.
    Streaming handlers (like ``job.logs``) may write directly to the
    writer and return ``None`` to suppress the automatic response.
    """

    def __init__(self) -> None:
        self._methods: dict[str, MethodHandler] = {}

    def register(self, method: str, handler: MethodHandler) -> None:
        """Register a handler for the given RPC method name."""
        self._methods[method] = handler

    @property
    def methods(self) -> list[str]:
        """Return the list of registered method names."""
        return list(self._methods)

    async def handle(
        self,
        request: JsonRpcRequest,
        writer: asyncio.StreamWriter,
    ) -> JsonRpcResponse | JsonRpcError | None:
        """Dispatch *request* to the appropriate handler.

        Returns:
            ``JsonRpcResponse`` on success, ``JsonRpcError`` on failure,
            or ``None`` if the request was a notification (no ``id``)
            or the handler wrote the response itself (streaming).
        """
        handler = self._methods.get(request.method)
        if handler is None:
            if request.id is None:
                _logger.debug("rpc_unknown_notification", method=request.method)
                return None  # Notification for unknown method — ignore
            return method_not_found(request.id, request.method)

        # #252: correlate IPC error logs to the job and the structured error
        # type. The handler is generic, so job_id is best-effort from params
        # (None for methods that don't carry one); error_type is the exception
        # class name (e.g. JobSubmissionError) — the diagnostic signal behind
        # the JSON-RPC code returned to the client.
        job_id = request.params.get("job_id") if isinstance(request.params, dict) else None

        try:
            result = await handler(request.params or {}, writer)
        except DaemonError as exc:
            _logger.warning(
                "rpc_handler_daemon_error",
                method=request.method,
                job_id=job_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if request.id is None:
                return None
            return map_exception_to_rpc_error(exc, request.id)
        except (TypeError, ValueError, KeyError) as exc:
            _logger.warning(
                "rpc_handler_param_error",
                method=request.method,
                job_id=job_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if request.id is None:
                return None
            return invalid_params(request.id, str(exc))
        except Exception as exc:
            _logger.error(
                "rpc_handler_internal_error",
                method=request.method,
                job_id=job_id,
                error_type=type(exc).__name__,
                error=str(exc),
                exc_info=True,
            )
            if request.id is None:
                return None
            return internal_error(request.id, str(exc))

        # Streaming handlers return None (they write responses directly)
        if result is None or request.id is None:
            return None

        return JsonRpcResponse(result=result, id=request.id)


__all__ = ["MethodHandler", "RequestHandler"]
