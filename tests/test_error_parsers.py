"""Tests for the error parsers module (marianne.core.errors.parsers)."""

from __future__ import annotations

import json

from marianne.core.errors.codes import ErrorCategory, ErrorCode
from marianne.core.errors.models import ClassifiedError, ParsedCliError
from marianne.core.errors.parsers import (
    ROOT_CAUSE_PRIORITY,
    classify_single_json_error,
    select_root_cause,
    try_parse_json_errors,
)


class TestTryParseJsonErrors:
    """Tests for try_parse_json_errors()."""

    def test_empty_output_returns_empty(self) -> None:
        assert try_parse_json_errors("", "") == []

    def test_non_json_output_returns_empty(self) -> None:
        assert try_parse_json_errors("Hello world\nNo JSON here", "") == []

    def test_json_without_errors_key_returns_empty(self) -> None:
        output = json.dumps({"result": "ok", "cost_usd": 0.01})
        assert try_parse_json_errors(output) == []

    def test_json_with_empty_errors_returns_empty(self) -> None:
        output = json.dumps({"result": "ok", "errors": []})
        assert try_parse_json_errors(output) == []

    def test_single_system_error(self) -> None:
        output = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"}
            ],
        })
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert result[0].error_type == "system"
        assert result[0].message == "Rate limit exceeded"

    def test_multiple_errors(self) -> None:
        output = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
                {"type": "user", "message": "spawn claude ENOENT"},
            ],
        })
        result = try_parse_json_errors(output)
        assert len(result) == 2
        assert result[0].message == "Rate limit exceeded"
        assert result[1].message == "spawn claude ENOENT"

    def test_tool_error_with_tool_name(self) -> None:
        output = json.dumps({
            "errors": [
                {
                    "type": "tool",
                    "message": "MCP server unreachable",
                    "tool_name": "mcp_fetch",
                    "metadata": {"server": "localhost:3000"},
                }
            ],
        })
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert result[0].error_type == "tool"
        assert result[0].tool_name == "mcp_fetch"
        assert result[0].metadata == {"server": "localhost:3000"}

    def test_non_json_preamble_before_json(self) -> None:
        """CLI startup messages precede JSON output."""
        preamble = "Starting Claude CLI v1.2.3\nInitializing...\n"
        json_part = json.dumps({
            "errors": [{"type": "system", "message": "Auth failed"}],
        })
        output = preamble + json_part
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert result[0].message == "Auth failed"

    def test_errors_in_stderr(self) -> None:
        """Some errors appear in stderr instead of stdout."""
        stderr = json.dumps({
            "errors": [{"type": "user", "message": "permission denied"}],
        })
        result = try_parse_json_errors("", stderr)
        assert len(result) == 1
        assert result[0].message == "permission denied"

    def test_deduplicates_across_stdout_stderr(self) -> None:
        """Same error in both streams is deduplicated."""
        data = json.dumps({
            "errors": [{"type": "system", "message": "Rate limit"}],
        })
        result = try_parse_json_errors(data, data)
        assert len(result) == 1

    def test_non_dict_error_items_skipped(self) -> None:
        """errors[] items that aren't dicts are skipped."""
        output = json.dumps({
            "errors": [
                "string error",
                42,
                {"type": "system", "message": "Real error"},
            ],
        })
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert result[0].message == "Real error"

    def test_missing_type_defaults_to_unknown(self) -> None:
        output = json.dumps({
            "errors": [{"message": "Something went wrong"}],
        })
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert result[0].error_type == "unknown"

    def test_missing_message_defaults_to_empty(self) -> None:
        output = json.dumps({
            "errors": [{"type": "system"}],
        })
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert result[0].message == ""


class TestClassifySingleJsonError:
    """Tests for classify_single_json_error()."""

    # --- System errors ---

    def test_system_rate_limit(self) -> None:
        error = ParsedCliError(error_type="system", message="Rate limit exceeded")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.error_code == ErrorCode.RATE_LIMIT_API
        assert result.retriable is True

    def test_system_rate_limit_429(self) -> None:
        error = ParsedCliError(error_type="system", message="HTTP 429 Too Many Requests")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_system_capacity_exceeded(self) -> None:
        error = ParsedCliError(
            error_type="system",
            message="Rate limit: service overloaded, try again later",
        )
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.error_code == ErrorCode.CAPACITY_EXCEEDED
        assert result.suggested_wait_seconds == 300.0

    def test_system_auth_failure(self) -> None:
        error = ParsedCliError(error_type="system", message="Unauthorized: invalid api key")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.AUTH
        assert result.error_code == ErrorCode.BACKEND_AUTH
        assert result.retriable is False

    def test_system_401_detection(self) -> None:
        error = ParsedCliError(error_type="system", message="HTTP 401 response")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.AUTH

    # --- User errors ---

    def test_user_enoent(self) -> None:
        error = ParsedCliError(error_type="user", message="spawn claude ENOENT")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.CONFIGURATION
        assert result.error_code == ErrorCode.BACKEND_NOT_FOUND
        assert result.error_info is not None
        assert result.error_info.reason == "BINARY_NOT_FOUND"

    def test_user_command_not_found(self) -> None:
        error = ParsedCliError(error_type="user", message="command not found: claude")
        result = classify_single_json_error(error)
        assert result.error_code == ErrorCode.BACKEND_NOT_FOUND

    def test_user_permission_denied(self) -> None:
        error = ParsedCliError(error_type="user", message="Permission denied /usr/bin/claude")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.AUTH
        assert result.retriable is False

    def test_user_file_not_found(self) -> None:
        error = ParsedCliError(error_type="user", message="No such file: config.yaml")
        result = classify_single_json_error(error)
        assert result.error_code == ErrorCode.CONFIG_PATH_NOT_FOUND
        assert result.retriable is False

    # --- Tool errors ---

    def test_tool_mcp_error(self) -> None:
        error = ParsedCliError(error_type="tool", message="MCP server failed to respond")
        result = classify_single_json_error(error)
        assert result.error_code == ErrorCode.CONFIG_MCP_ERROR

    def test_tool_generic_failure(self) -> None:
        error = ParsedCliError(error_type="tool", message="Tool execution returned error")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.VALIDATION
        assert result.error_code == ErrorCode.VALIDATION_COMMAND_FAILED

    # --- Message pattern fallback ---

    def test_network_connection_refused(self) -> None:
        error = ParsedCliError(error_type="unknown", message="ECONNREFUSED 127.0.0.1:443")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.NETWORK
        assert result.error_code == ErrorCode.NETWORK_CONNECTION_FAILED

    def test_dns_error(self) -> None:
        error = ParsedCliError(error_type="unknown", message="ENOTFOUND api.example.com")
        result = classify_single_json_error(error)
        assert result.error_code == ErrorCode.NETWORK_DNS_ERROR

    def test_ssl_error(self) -> None:
        error = ParsedCliError(error_type="unknown", message="SSL certificate verify failed")
        result = classify_single_json_error(error)
        assert result.error_code == ErrorCode.NETWORK_SSL_ERROR

    def test_timeout_message(self) -> None:
        error = ParsedCliError(error_type="unknown", message="Request timed out")
        result = classify_single_json_error(error)
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_unknown_defaults_to_transient(self) -> None:
        error = ParsedCliError(error_type="unknown", message="Something unexpected")
        result = classify_single_json_error(error)
        assert result.category == ErrorCategory.TRANSIENT
        assert result.error_code == ErrorCode.UNKNOWN
        assert result.retriable is True

    # --- Exit context ---

    def test_exit_code_passed_through(self) -> None:
        error = ParsedCliError(error_type="system", message="Rate limit")
        result = classify_single_json_error(error, exit_code=1)
        assert result.exit_code == 1

    def test_exit_reason_passed_through(self) -> None:
        error = ParsedCliError(error_type="system", message="Rate limit")
        result = classify_single_json_error(
            error, exit_reason="timeout",
        )
        assert result.exit_reason == "timeout"


class TestSelectRootCause:
    """Tests for select_root_cause()."""

    def test_empty_list_returns_unknown(self) -> None:
        root, symptoms, confidence = select_root_cause([])
        assert root.error_code == ErrorCode.UNKNOWN
        assert root.category == ErrorCategory.FATAL
        assert symptoms == []
        assert confidence == 0.0

    def test_single_error_returns_itself(self) -> None:
        error = ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limited",
            error_code=ErrorCode.RATE_LIMIT_API,
        )
        root, symptoms, confidence = select_root_cause([error])
        assert root is error
        assert symptoms == []
        assert confidence == 1.0

    def test_enoent_wins_over_rate_limit(self) -> None:
        """ENOENT (missing binary) is more fundamental than rate limiting."""
        enoent = ClassifiedError(
            category=ErrorCategory.CONFIGURATION,
            message="spawn claude ENOENT",
            error_code=ErrorCode.BACKEND_NOT_FOUND,
        )
        rate_limit = ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit exceeded",
            error_code=ErrorCode.RATE_LIMIT_API,
        )
        root, rest, _ = select_root_cause([rate_limit, enoent])
        assert root.error_code == ErrorCode.BACKEND_NOT_FOUND
        assert len(rest) == 1
        assert rest[0].error_code == ErrorCode.RATE_LIMIT_API

    def test_auth_wins_over_rate_limit(self) -> None:
        """Can't be rate limited if auth fails first."""
        auth = ClassifiedError(
            category=ErrorCategory.AUTH,
            message="Invalid API key",
            error_code=ErrorCode.BACKEND_AUTH,
        )
        rate_limit = ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit",
            error_code=ErrorCode.RATE_LIMIT_API,
        )
        root, _, _ = select_root_cause([rate_limit, auth])
        assert root.error_code == ErrorCode.BACKEND_AUTH

    def test_network_wins_over_service_errors(self) -> None:
        """Network issues prevent reaching the service."""
        network = ClassifiedError(
            category=ErrorCategory.NETWORK,
            message="Connection refused",
            error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
        )
        timeout = ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message="Backend timeout",
            error_code=ErrorCode.BACKEND_TIMEOUT,
        )
        root, _, _ = select_root_cause([timeout, network])
        assert root.error_code == ErrorCode.NETWORK_CONNECTION_FAILED

    def test_timeout_demoted_when_rate_limit_present(self) -> None:
        """Timeout is a symptom when paired with rate limit."""
        timeout = ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message="Timed out",
            error_code=ErrorCode.EXECUTION_TIMEOUT,
        )
        rate_limit = ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit",
            error_code=ErrorCode.RATE_LIMIT_API,
        )
        root, _, _ = select_root_cause([timeout, rate_limit])
        assert root.error_code == ErrorCode.RATE_LIMIT_API

    def test_config_path_missing_boosted(self) -> None:
        """Missing config is fundamental — prevents everything."""
        config_missing = ClassifiedError(
            category=ErrorCategory.CONFIGURATION,
            message="Config not found",
            error_code=ErrorCode.CONFIG_PATH_NOT_FOUND,
        )
        exec_error = ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            message="Execution crashed",
            error_code=ErrorCode.EXECUTION_CRASHED,
        )
        root, _, _ = select_root_cause([exec_error, config_missing])
        assert root.error_code == ErrorCode.CONFIG_PATH_NOT_FOUND

    def test_confidence_higher_with_clear_gap(self) -> None:
        """When priority gap is large, confidence is higher."""
        enoent = ClassifiedError(
            category=ErrorCategory.CONFIGURATION,
            message="ENOENT",
            error_code=ErrorCode.BACKEND_NOT_FOUND,
        )
        unknown = ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            message="Unknown",
            error_code=ErrorCode.UNKNOWN,
        )
        _, _, confidence = select_root_cause([unknown, enoent])
        assert confidence > 0.7

    def test_confidence_lower_with_same_tier(self) -> None:
        """When errors are in the same tier, confidence is lower than clear gap cases."""
        err1 = ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message="Execution timeout",
            error_code=ErrorCode.EXECUTION_TIMEOUT,
        )
        err2 = ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message="Execution OOM",
            error_code=ErrorCode.EXECUTION_OOM,
        )
        _, _, confidence = select_root_cause([err1, err2])
        # Same tier — confidence should be moderate (gap=1 → 0.55)
        assert confidence < 0.7

    def test_mcp_wins_over_tool_failure(self) -> None:
        """MCP config error masks tool execution failures."""
        mcp = ClassifiedError(
            category=ErrorCategory.CONFIGURATION,
            message="MCP server error",
            error_code=ErrorCode.CONFIG_MCP_ERROR,
        )
        tool = ClassifiedError(
            category=ErrorCategory.VALIDATION,
            message="Tool failed",
            error_code=ErrorCode.VALIDATION_COMMAND_FAILED,
        )
        root, _, _ = select_root_cause([tool, mcp])
        assert root.error_code == ErrorCode.CONFIG_MCP_ERROR


class TestRootCausePriority:
    """Tests for ROOT_CAUSE_PRIORITY mapping."""

    def test_all_priority_tiers_represented(self) -> None:
        """Verify all error code tiers from the docstring are present."""
        priorities = set(ROOT_CAUSE_PRIORITY.values())
        # Should have values in tiers 10-99
        assert min(priorities) >= 10
        assert max(priorities) <= 99

    def test_environment_highest_priority(self) -> None:
        """Environment issues (tier 1) have lowest numeric priority = most fundamental."""
        env_codes = [
            ErrorCode.BACKEND_NOT_FOUND,
            ErrorCode.PREFLIGHT_PATH_MISSING,
        ]
        for code in env_codes:
            assert ROOT_CAUSE_PRIORITY[code] < 20

    def test_unknown_lowest_priority(self) -> None:
        """UNKNOWN error should have highest numeric score (least specific)."""
        assert ROOT_CAUSE_PRIORITY[ErrorCode.UNKNOWN] == 99

    def test_priority_ordering(self) -> None:
        """Config issues should be prioritized over validation issues."""
        config_pri = ROOT_CAUSE_PRIORITY[ErrorCode.CONFIG_INVALID]
        validation_pri = ROOT_CAUSE_PRIORITY[ErrorCode.VALIDATION_GENERIC]
        assert config_pri < validation_pri


class TestJsonParsingEdgeCases:
    """Edge cases for JSON extraction from CLI output."""

    def test_escaped_quotes_in_message(self) -> None:
        """Backslash-escaped quotes inside error messages."""
        output = json.dumps({
            "errors": [
                {"type": "system", "message": 'Path is "C:\\\\Users\\\\test"'}
            ],
        })
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert "Users" in result[0].message
        assert "test" in result[0].message

    def test_incomplete_json_truncated(self) -> None:
        """Truncated JSON should not crash — returns empty on parse failure."""
        output = '{"errors": [{"type": "system", "message": "truncated'
        result = try_parse_json_errors(output)
        # Incomplete JSON cannot be parsed — empty result
        assert result == []

    def test_multiple_json_objects_takes_first_with_errors(self) -> None:
        """When multiple JSON objects exist, errors from first valid one win."""
        json1 = json.dumps({"result": "ok"})  # No errors
        json2 = json.dumps({
            "errors": [{"type": "user", "message": "second object error"}],
        })
        output = json1 + "\n" + json2
        result = try_parse_json_errors(output)
        assert len(result) == 1
        assert result[0].message == "second object error"

    def test_cli_mode_masks_execution_error(self) -> None:
        """CLI mode error masks execution errors (priority modifier)."""
        cli_mode = ClassifiedError(
            category=ErrorCategory.CONFIGURATION,
            message="Streaming mode not supported",
            error_code=ErrorCode.CONFIG_CLI_MODE_ERROR,
        )
        exec_error = ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            message="Execution crashed",
            error_code=ErrorCode.EXECUTION_CRASHED,
        )
        root, _, _ = select_root_cause([exec_error, cli_mode])
        assert root.error_code == ErrorCode.CONFIG_CLI_MODE_ERROR

    def test_zero_gap_confidence(self) -> None:
        """Errors with identical priority (gap=0) produce 0.4 confidence."""
        # Both errors have priority 91 in ROOT_CAUSE_PRIORITY, so gap=0
        err1 = ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            message="Error A",
            error_code=ErrorCode.BACKEND_RESPONSE,
        )
        err2 = ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            message="Error B",
            error_code=ErrorCode.BACKEND_RESPONSE,
        )
        _, _, confidence = select_root_cause([err1, err2])
        # gap == 0 → confidence = 0.4 (ambiguous)
        assert confidence == 0.4
