"""Tests for marianne.daemon.ipc.protocol — JSON-RPC 2.0 wire format models.

Also includes tests for the IPC error codes and factory functions
(marianne.daemon.ipc.errors) since they are tightly coupled to the protocol models.
"""

import pytest
from pydantic import ValidationError

from marianne.daemon.exceptions import (
    DaemonAlreadyRunningError,
    DaemonError,
    JobSubmissionError,
    ResourceExhaustedError,
)
from marianne.daemon.ipc.errors import (
    DAEMON_SHUTTING_DOWN,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    JOB_ALREADY_RUNNING,
    JOB_NOT_FOUND,
    JOB_NOT_RESUMABLE,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    RESOURCE_EXHAUSTED,
    WORKSPACE_NOT_FOUND,
    internal_error,
    invalid_params,
    invalid_request,
    make_error,
    map_exception_to_rpc_error,
    method_not_found,
    parse_error,
    rpc_error_to_exception,
)
from marianne.daemon.ipc.protocol import (
    ErrorDetail,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
)


class TestJsonRpcRequest:
    """Tests for JsonRpcRequest model."""

    def test_minimal_request(self):
        """Method-only request with defaults."""
        req = JsonRpcRequest(method="daemon.status")
        assert req.jsonrpc == "2.0"
        assert req.method == "daemon.status"
        assert req.params is None
        assert req.id is None

    def test_request_with_params_and_id(self):
        """Full request with params and numeric ID."""
        req = JsonRpcRequest(
            method="job.submit",
            params={"config_path": "/tmp/test.yaml"},
            id=42,
        )
        assert req.method == "job.submit"
        assert req.params == {"config_path": "/tmp/test.yaml"}
        assert req.id == 42

    def test_request_with_string_id(self):
        """JSON-RPC allows string IDs."""
        req = JsonRpcRequest(method="job.status", id="abc-123")
        assert req.id == "abc-123"

    def test_notification_has_no_id(self):
        """A notification is a request where id is None."""
        req = JsonRpcRequest(method="log.heartbeat")
        assert req.id is None

    def test_serialization_roundtrip(self):
        """Request survives model_dump_json -> model_validate_json roundtrip."""
        original = JsonRpcRequest(
            method="job.submit",
            params={"config_path": "/tmp/test.yaml", "fresh": True},
            id=7,
        )
        json_bytes = original.model_dump_json()
        restored = JsonRpcRequest.model_validate_json(json_bytes)
        assert restored.method == original.method
        assert restored.params == original.params
        assert restored.id == original.id

    def test_jsonrpc_field_defaults_to_2_0(self):
        """The jsonrpc field should always default to '2.0'."""
        req = JsonRpcRequest(method="test")
        dumped = req.model_dump()
        assert dumped["jsonrpc"] == "2.0"

    def test_invalid_jsonrpc_version_rejected(self):
        """Constructing with wrong jsonrpc version fails Pydantic validation."""
        with pytest.raises(ValidationError):
            JsonRpcRequest(jsonrpc="1.0", method="test")  # type: ignore[arg-type]

    def test_from_dict(self):
        """Construct from raw dict (as received over wire)."""
        raw = {"jsonrpc": "2.0", "method": "daemon.shutdown", "id": 1}
        req = JsonRpcRequest.model_validate(raw)
        assert req.method == "daemon.shutdown"
        assert req.id == 1


class TestJsonRpcResponse:
    """Tests for JsonRpcResponse model."""

    def test_success_response(self):
        """Basic success response."""
        resp = JsonRpcResponse(result={"status": "ok"}, id=1)
        assert resp.jsonrpc == "2.0"
        assert resp.result == {"status": "ok"}
        assert resp.id == 1

    def test_null_result(self):
        """Result can be None/null for void methods."""
        resp = JsonRpcResponse(result=None, id=5)
        assert resp.result is None

    def test_serialization_roundtrip(self):
        """Response survives JSON roundtrip."""
        original = JsonRpcResponse(
            result={"jobs": [{"id": "j1", "status": "running"}]},
            id=99,
        )
        json_bytes = original.model_dump_json()
        restored = JsonRpcResponse.model_validate_json(json_bytes)
        assert restored.result == original.result
        assert restored.id == original.id

    def test_string_id(self):
        """Response with string ID."""
        resp = JsonRpcResponse(result="pong", id="req-42")
        assert resp.id == "req-42"


class TestJsonRpcError:
    """Tests for JsonRpcError model."""

    def test_error_response(self):
        """Basic error response."""
        error = JsonRpcError(
            error=ErrorDetail(code=-32600, message="Invalid request"),
            id=1,
        )
        assert error.jsonrpc == "2.0"
        assert error.error.code == -32600
        assert error.error.message == "Invalid request"
        assert error.id == 1

    def test_error_with_null_id(self):
        """Error response with null ID (e.g., parse error before ID is known)."""
        error = JsonRpcError(
            error=ErrorDetail(code=-32700, message="Parse error"),
            id=None,
        )
        assert error.id is None

    def test_error_with_data(self):
        """Error response carrying extra data."""
        error = JsonRpcError(
            error=ErrorDetail(
                code=-32601,
                message="Method not found",
                data={"method": "foo.bar"},
            ),
            id=3,
        )
        assert error.error.data == {"method": "foo.bar"}

    def test_serialization_roundtrip(self):
        """Error response survives JSON roundtrip."""
        original = JsonRpcError(
            error=ErrorDetail(code=-32603, message="Internal error", data={"trace": "..."}),
            id=10,
        )
        json_bytes = original.model_dump_json()
        restored = JsonRpcError.model_validate_json(json_bytes)
        assert restored.error.code == original.error.code
        assert restored.error.message == original.error.message
        assert restored.error.data == original.error.data


class TestErrorDetail:
    """Tests for ErrorDetail model."""

    def test_minimal(self):
        """Code and message only."""
        detail = ErrorDetail(code=-32600, message="Bad request")
        assert detail.code == -32600
        assert detail.message == "Bad request"
        assert detail.data is None

    def test_with_data(self):
        """Include extra data dict."""
        detail = ErrorDetail(
            code=-32602,
            message="Invalid params",
            data={"field": "config_path", "reason": "missing"},
        )
        assert detail.data["field"] == "config_path"


# ---------------------------------------------------------------------------
# IPC error codes and factory function tests
# ---------------------------------------------------------------------------


class TestErrorCodes:
    """Tests for JSON-RPC and Marianne extension error codes."""

    def test_standard_error_codes_match_spec(self):
        """Standard JSON-RPC 2.0 error codes are in the correct range."""
        assert PARSE_ERROR == -32700
        assert INVALID_REQUEST == -32600
        assert METHOD_NOT_FOUND == -32601
        assert INVALID_PARAMS == -32602
        assert INTERNAL_ERROR == -32603

    def test_extension_error_codes_in_range(self):
        """Marianne extension codes are in the -32000 to -32099 range."""
        ext_codes = [
            JOB_NOT_FOUND,
            RESOURCE_EXHAUSTED,
            JOB_ALREADY_RUNNING,
            DAEMON_SHUTTING_DOWN,
            JOB_NOT_RESUMABLE,
            WORKSPACE_NOT_FOUND,
        ]
        for code in ext_codes:
            assert -32099 <= code <= -32000, f"Extension code {code} out of range"

    def test_all_extension_codes_are_unique(self):
        """No two extension codes share the same value."""
        ext_codes = [
            JOB_NOT_FOUND,
            RESOURCE_EXHAUSTED,
            JOB_ALREADY_RUNNING,
            DAEMON_SHUTTING_DOWN,
            JOB_NOT_RESUMABLE,
            WORKSPACE_NOT_FOUND,
        ]
        assert len(ext_codes) == len(set(ext_codes))


class TestErrorFactoryFunctions:
    """Tests for error response builder functions."""

    def test_make_error(self):
        """make_error creates a properly structured JsonRpcError."""
        err = make_error(-32600, "Bad request", 42, data={"field": "x"})
        assert isinstance(err, JsonRpcError)
        assert err.error.code == -32600
        assert err.error.message == "Bad request"
        assert err.error.data == {"field": "x"}
        assert err.id == 42

    def test_make_error_with_none_id(self):
        """make_error works with None ID (parse errors before ID is known)."""
        err = make_error(-32700, "Parse error", None)
        assert err.id is None

    def test_parse_error_factory(self):
        """parse_error() produces a -32700 error response."""
        err = parse_error()
        assert err.error.code == PARSE_ERROR
        assert "Parse error" in err.error.message
        assert err.id is None

    def test_parse_error_with_id(self):
        """parse_error() can take an explicit request ID."""
        err = parse_error(request_id=5)
        assert err.id == 5

    def test_invalid_request_factory(self):
        """invalid_request() produces a -32600 error response."""
        err = invalid_request(1, "missing 'method' field")
        assert err.error.code == INVALID_REQUEST
        assert "Invalid request" in err.error.message
        assert "missing 'method' field" in err.error.message

    def test_invalid_request_no_detail(self):
        """invalid_request() without detail gives just the base message."""
        err = invalid_request(1)
        assert err.error.message == "Invalid request"

    def test_method_not_found_factory(self):
        """method_not_found() includes the method name in message and data."""
        err = method_not_found(2, "foo.bar")
        assert err.error.code == METHOD_NOT_FOUND
        assert "foo.bar" in err.error.message
        assert err.error.data == {"method": "foo.bar"}

    def test_invalid_params_factory(self):
        """invalid_params() produces a -32602 error with detail."""
        err = invalid_params(3, "config_path is required")
        assert err.error.code == INVALID_PARAMS
        assert "config_path is required" in err.error.message

    def test_internal_error_factory(self):
        """internal_error() produces a -32603 error response."""
        err = internal_error(4, "disk full")
        assert err.error.code == INTERNAL_ERROR
        assert "disk full" in err.error.message

    def test_internal_error_no_detail(self):
        """internal_error() without detail gives just the base message."""
        err = internal_error(4)
        assert err.error.message == "Internal error"


class TestExceptionToRpcErrorMapping:
    """Tests for mapping DaemonError hierarchy to JSON-RPC error codes."""

    def test_job_submission_error(self):
        """JobSubmissionError maps to JOB_NOT_FOUND code."""
        exc = JobSubmissionError("no such job")
        err = map_exception_to_rpc_error(exc, 1)
        assert err.error.code == JOB_NOT_FOUND
        assert "no such job" in err.error.message

    def test_resource_exhausted_error(self):
        """ResourceExhaustedError maps to RESOURCE_EXHAUSTED code."""
        exc = ResourceExhaustedError("too many jobs")
        err = map_exception_to_rpc_error(exc, 2)
        assert err.error.code == RESOURCE_EXHAUSTED

    def test_daemon_already_running_error(self):
        """DaemonAlreadyRunningError maps to JOB_ALREADY_RUNNING code."""
        exc = DaemonAlreadyRunningError("pid 1234 already exists")
        err = map_exception_to_rpc_error(exc, 3)
        assert err.error.code == JOB_ALREADY_RUNNING

    def test_unknown_daemon_error_falls_back(self):
        """A DaemonError subclass not in the map falls back to INTERNAL_ERROR."""
        exc = DaemonError("generic daemon problem")
        err = map_exception_to_rpc_error(exc, 4)
        assert err.error.code == INTERNAL_ERROR


class TestRpcErrorToExceptionMapping:
    """Tests for converting JSON-RPC errors back to DaemonError exceptions."""

    def test_job_not_found_code(self):
        """JOB_NOT_FOUND code maps back to JobSubmissionError."""
        exc = rpc_error_to_exception({"code": JOB_NOT_FOUND, "message": "not found"})
        assert isinstance(exc, JobSubmissionError)
        assert "not found" in str(exc)

    def test_resource_exhausted_code(self):
        """RESOURCE_EXHAUSTED code maps back to ResourceExhaustedError."""
        exc = rpc_error_to_exception({"code": RESOURCE_EXHAUSTED, "message": "full"})
        assert isinstance(exc, ResourceExhaustedError)

    def test_job_already_running_code(self):
        """JOB_ALREADY_RUNNING code maps back to DaemonAlreadyRunningError."""
        exc = rpc_error_to_exception({"code": JOB_ALREADY_RUNNING, "message": "dup"})
        assert isinstance(exc, DaemonAlreadyRunningError)

    def test_daemon_shutting_down_code(self):
        """DAEMON_SHUTTING_DOWN code maps back to base DaemonError."""
        exc = rpc_error_to_exception({"code": DAEMON_SHUTTING_DOWN, "message": "bye"})
        assert isinstance(exc, DaemonError)

    def test_job_not_resumable_code(self):
        """JOB_NOT_RESUMABLE code maps back to JobSubmissionError."""
        exc = rpc_error_to_exception({"code": JOB_NOT_RESUMABLE, "message": "done"})
        assert isinstance(exc, JobSubmissionError)

    def test_workspace_not_found_code(self):
        """WORKSPACE_NOT_FOUND code maps back to JobSubmissionError."""
        exc = rpc_error_to_exception({"code": WORKSPACE_NOT_FOUND, "message": "missing"})
        assert isinstance(exc, JobSubmissionError)

    def test_unknown_code_falls_back_to_daemon_error(self):
        """An unrecognized code falls back to base DaemonError."""
        exc = rpc_error_to_exception({"code": -99999, "message": "alien error"})
        assert isinstance(exc, DaemonError)
        assert "alien error" in str(exc)

    def test_missing_code_defaults_to_internal(self):
        """Missing 'code' key defaults to INTERNAL_ERROR mapping."""
        exc = rpc_error_to_exception({"message": "no code"})
        assert isinstance(exc, DaemonError)

    def test_missing_message_defaults(self):
        """Missing 'message' key gives 'Unknown error'."""
        exc = rpc_error_to_exception({"code": JOB_NOT_FOUND})
        assert isinstance(exc, JobSubmissionError)
        assert "Unknown error" in str(exc)
