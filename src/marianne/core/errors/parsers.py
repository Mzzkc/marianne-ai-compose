"""JSON parsing utilities for error extraction.

Contains functions for parsing CLI JSON output to extract structured errors
and identifying root causes from multiple errors.
"""

from __future__ import annotations

import json
import logging

from .codes import ErrorCategory, ErrorCode, ExitReason
from .models import ClassifiedError, ErrorInfo, ParsedCliError

_logger = logging.getLogger("marianne.errors.parsers")


# Priority scores for root cause selection.
# Lower score = more likely to be root cause.
# Tier 1 (10-19): Environment issues - prevent execution entirely
# Tier 2 (20-29): Configuration issues - bad config causes cascading failures
# Tier 3 (30-39): Authentication - auth failures cause downstream errors
# Tier 4 (40-49): Network/Connection - network issues cause service errors
# Tier 5 (50-59): Service issues - specific service problems
# Tier 6 (60-69): Execution issues - runtime problems
# Tier 7 (70-79): State issues - checkpoint problems
# Tier 8 (80-89): Validation/Output - usually symptoms, not causes
# Tier 9 (90+): Unknown/Generic
ROOT_CAUSE_PRIORITY: dict[ErrorCode, int] = {
    # Tier 1: Environment issues (priority 10-19)
    ErrorCode.BACKEND_NOT_FOUND: 10,
    ErrorCode.PREFLIGHT_PATH_MISSING: 11,
    ErrorCode.PREFLIGHT_WORKING_DIR_INVALID: 12,
    ErrorCode.CONFIG_PATH_NOT_FOUND: 13,

    # Tier 2: Configuration issues (priority 20-29)
    ErrorCode.CONFIG_INVALID: 20,
    ErrorCode.CONFIG_MISSING_FIELD: 21,
    ErrorCode.CONFIG_PARSE_ERROR: 22,
    ErrorCode.CONFIG_MCP_ERROR: 23,
    ErrorCode.CONFIG_CLI_MODE_ERROR: 24,

    # Tier 3: Authentication (priority 30-39)
    ErrorCode.BACKEND_AUTH: 30,

    # Tier 4: Network/Connection (priority 40-49)
    ErrorCode.NETWORK_CONNECTION_FAILED: 40,
    ErrorCode.NETWORK_DNS_ERROR: 41,
    ErrorCode.NETWORK_SSL_ERROR: 42,
    ErrorCode.BACKEND_CONNECTION: 43,

    # Tier 5: Service Issues (priority 50-59)
    ErrorCode.RATE_LIMIT_API: 50,
    ErrorCode.RATE_LIMIT_CLI: 51,
    ErrorCode.CAPACITY_EXCEEDED: 52,
    ErrorCode.QUOTA_EXHAUSTED: 53,
    ErrorCode.BACKEND_TIMEOUT: 54,
    ErrorCode.NETWORK_TIMEOUT: 55,

    # Tier 6: Execution Issues (priority 60-69)
    ErrorCode.EXECUTION_TIMEOUT: 60,
    ErrorCode.EXECUTION_OOM: 61,
    ErrorCode.EXECUTION_CRASHED: 62,
    ErrorCode.EXECUTION_KILLED: 63,
    ErrorCode.EXECUTION_INTERRUPTED: 64,

    # Tier 7: State Issues (priority 70-79)
    ErrorCode.STATE_CORRUPTION: 70,
    ErrorCode.STATE_VERSION_MISMATCH: 71,
    ErrorCode.STATE_LOAD_FAILED: 72,
    ErrorCode.STATE_SAVE_FAILED: 73,

    # Tier 8: Validation/Output Issues (priority 80-89)
    ErrorCode.VALIDATION_FILE_MISSING: 80,
    ErrorCode.VALIDATION_CONTENT_MISMATCH: 81,
    ErrorCode.VALIDATION_COMMAND_FAILED: 82,
    ErrorCode.VALIDATION_TIMEOUT: 83,
    ErrorCode.VALIDATION_GENERIC: 84,
    ErrorCode.PREFLIGHT_PROMPT_TOO_LARGE: 85,
    ErrorCode.PREFLIGHT_VALIDATION_SETUP: 86,

    # Tier 9: Unknown/Generic (priority 90+)
    ErrorCode.EXECUTION_UNKNOWN: 90,
    ErrorCode.BACKEND_RESPONSE: 91,
    ErrorCode.UNKNOWN: 99,
}


def try_parse_json_errors(output: str, stderr: str = "") -> list[ParsedCliError]:
    """Extract errors[] array from JSON output.

    Claude CLI returns structured JSON with an `errors[]` array:
    ```json
    {
      "result": "...",
      "errors": [
        {"type": "system", "message": "Rate limit exceeded"},
        {"type": "user", "message": "spawn claude ENOENT"}
      ],
      "cost_usd": 0.05
    }
    ```

    This function parses that structure, handling:
    - Non-JSON preamble (CLI startup messages)
    - Multiple JSON objects (takes first valid one with errors[])
    - JSON in stderr (some error modes write there)
    - Truncated JSON (tries to recover)

    Args:
        output: Raw stdout from Claude CLI execution.
        stderr: Optional stderr output (some errors appear here).

    Returns:
        List of ParsedCliError objects, or empty list if parsing fails.
    """
    errors: list[ParsedCliError] = []

    # Try both stdout and stderr - errors can appear in either
    for text in [output, stderr]:
        if not text:
            continue

        found_errors = _extract_json_errors_from_text(text)
        if found_errors:
            errors.extend(found_errors)

    # Deduplicate by message (same error might appear in both streams)
    seen_messages: set[str] = set()
    unique_errors: list[ParsedCliError] = []
    for error in errors:
        if error.message not in seen_messages:
            seen_messages.add(error.message)
            unique_errors.append(error)

    return unique_errors


def _extract_json_errors_from_text(text: str) -> list[ParsedCliError]:
    """Extract errors from a single text stream.

    Handles multiple JSON objects and partial parsing.

    Args:
        text: Text that may contain JSON with errors[] array.

    Returns:
        List of ParsedCliError objects found.
    """
    errors: list[ParsedCliError] = []

    # Find all potential JSON object starts
    idx = 0
    while idx < len(text):
        json_start = text.find("{", idx)
        if json_start == -1:
            break

        # Try to find matching closing brace with bracket counting
        depth = 0
        json_end = json_start
        in_string = False
        escape_next = False

        for i in range(json_start, len(text)):
            char = text[i]

            if escape_next:
                escape_next = False
                continue

            if char == "\\":
                escape_next = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break

        if depth != 0:
            # Incomplete JSON, try parsing anyway (might work for simple cases)
            json_end = len(text)

        try:
            json_str = text[json_start:json_end]
            data = json.loads(json_str)

            if "errors" in data and isinstance(data["errors"], list):
                for item in data["errors"]:
                    if not isinstance(item, dict):
                        continue
                    error = ParsedCliError(
                        error_type=item.get("type", "unknown"),
                        message=item.get("message", ""),
                        tool_name=item.get("tool_name"),
                        metadata=item.get("metadata", {}),
                    )
                    errors.append(error)

                # Found valid errors, return them
                if errors:
                    return errors

        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            _logger.debug("Failed to parse JSON error block at offset %d: %s", json_start, exc)

        # Move past this JSON object to find next potential one
        idx = json_end if json_end > json_start else json_start + 1

    return errors


def classify_single_json_error(
    parsed_error: ParsedCliError,
    exit_code: int | None = None,
    exit_reason: ExitReason | None = None,
) -> ClassifiedError:
    """Classify a single error from the JSON errors[] array.

    This function uses type-based classification first, then falls back to
    message pattern matching. The error type from CLI ("system", "user", "tool")
    guides initial classification.

    Args:
        parsed_error: A ParsedCliError extracted from CLI JSON output.
        exit_code: Optional exit code for context.
        exit_reason: Optional exit reason for context.

    Returns:
        ClassifiedError with appropriate category and error code.
    """
    message = parsed_error.message.lower()
    error_type = parsed_error.error_type.lower()

    # === Type-based classification ===

    if error_type == "system":
        # System errors are usually API/service level
        # Check rate limit patterns
        rate_limit_indicators = [
            "rate limit", "rate_limit", "quota", "too many requests",
            "429", "hit your limit", "limit exceeded", "daily limit",
        ]
        if any(indicator in message for indicator in rate_limit_indicators):
            # Differentiate capacity vs rate limit
            capacity_indicators = ["capacity", "overloaded", "try again later", "unavailable"]
            if any(indicator in message for indicator in capacity_indicators):
                return ClassifiedError(
                    category=ErrorCategory.RATE_LIMIT,
                    message=parsed_error.message,
                    error_code=ErrorCode.CAPACITY_EXCEEDED,
                    exit_code=exit_code,
                    exit_reason=exit_reason,
                    retriable=True,
                    suggested_wait_seconds=300.0,
                )
            return ClassifiedError(
                category=ErrorCategory.RATE_LIMIT,
                message=parsed_error.message,
                error_code=ErrorCode.RATE_LIMIT_API,
                exit_code=exit_code,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=3600.0,
            )

        # Check auth patterns
        auth_indicators = ["unauthorized", "authentication", "invalid api key", "401", "403"]
        if any(indicator in message for indicator in auth_indicators):
            return ClassifiedError(
                category=ErrorCategory.AUTH,
                message=parsed_error.message,
                error_code=ErrorCode.BACKEND_AUTH,
                exit_code=exit_code,
                exit_reason=exit_reason,
                retriable=False,
            )

    elif error_type == "user":
        # User errors are usually environment/config issues
        # ENOENT is critical - often the root cause
        # Common patterns: "ENOENT", "spawn claude ENOENT", "command not found"
        if "enoent" in message or "command not found" in message:
            return ClassifiedError(
                category=ErrorCategory.CONFIGURATION,
                message=parsed_error.message,
                error_code=ErrorCode.BACKEND_NOT_FOUND,
                exit_code=exit_code,
                exit_reason=exit_reason,
                retriable=True,  # Might recover after reinstall
                suggested_wait_seconds=30.0,
                error_info=ErrorInfo(
                    reason="BINARY_NOT_FOUND",
                    # Phase 5: domain is no longer claude_cli-specific. The
                    # classifier runs against any CLI instrument, so the
                    # domain names the instrument kind ("cli") rather than
                    # a specific backend type. Callers that need the exact
                    # instrument name should read it from the job config.
                    domain="marianne.instrument.cli",
                    metadata={"original_message": parsed_error.message},
                ),
            )

        if "permission denied" in message or "access denied" in message:
            return ClassifiedError(
                category=ErrorCategory.AUTH,
                message=parsed_error.message,
                error_code=ErrorCode.BACKEND_AUTH,
                exit_code=exit_code,
                exit_reason=exit_reason,
                retriable=False,
            )

        if "no such file" in message or "not found" in message:
            return ClassifiedError(
                category=ErrorCategory.CONFIGURATION,
                message=parsed_error.message,
                error_code=ErrorCode.CONFIG_PATH_NOT_FOUND,
                exit_code=exit_code,
                exit_reason=exit_reason,
                retriable=False,
            )

    elif error_type == "tool":
        # Tool errors need message analysis
        if "mcp" in message or "server" in message:
            return ClassifiedError(
                category=ErrorCategory.CONFIGURATION,
                message=parsed_error.message,
                error_code=ErrorCode.CONFIG_MCP_ERROR,
                exit_code=exit_code,
                exit_reason=exit_reason,
                retriable=False,
            )

        # Tool execution failures are often validation issues
        return ClassifiedError(
            category=ErrorCategory.VALIDATION,
            message=parsed_error.message,
            error_code=ErrorCode.VALIDATION_COMMAND_FAILED,
            exit_code=exit_code,
            exit_reason=exit_reason,
            retriable=True,
            suggested_wait_seconds=10.0,
        )

    # === Message pattern fallback ===

    # Network errors
    network_indicators = [
        "connection refused", "connection reset", "econnrefused",
        "etimedout", "network unreachable",
    ]
    if any(indicator in message for indicator in network_indicators):
        return ClassifiedError(
            category=ErrorCategory.NETWORK,
            message=parsed_error.message,
            error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
            exit_code=exit_code,
            exit_reason=exit_reason,
            retriable=True,
            suggested_wait_seconds=30.0,
        )

    # DNS errors
    dns_indicators = ["dns", "getaddrinfo", "enotfound", "resolve"]
    if any(indicator in message for indicator in dns_indicators):
        return ClassifiedError(
            category=ErrorCategory.NETWORK,
            message=parsed_error.message,
            error_code=ErrorCode.NETWORK_DNS_ERROR,
            exit_code=exit_code,
            exit_reason=exit_reason,
            retriable=True,
            suggested_wait_seconds=30.0,
        )

    # SSL/TLS errors
    ssl_indicators = ["ssl", "tls", "certificate", "handshake"]
    if any(indicator in message for indicator in ssl_indicators):
        return ClassifiedError(
            category=ErrorCategory.NETWORK,
            message=parsed_error.message,
            error_code=ErrorCode.NETWORK_SSL_ERROR,
            exit_code=exit_code,
            exit_reason=exit_reason,
            retriable=True,
            suggested_wait_seconds=30.0,
        )

    # Timeout patterns
    timeout_indicators = ["timeout", "timed out"]
    if any(indicator in message for indicator in timeout_indicators):
        return ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message=parsed_error.message,
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            exit_code=exit_code,
            exit_reason=exit_reason,
            retriable=True,
            suggested_wait_seconds=60.0,
        )

    # Default: unknown error with the original message
    return ClassifiedError(
        category=ErrorCategory.TRANSIENT,
        message=parsed_error.message,
        error_code=ErrorCode.UNKNOWN,
        exit_code=exit_code,
        exit_reason=exit_reason,
        retriable=True,
        suggested_wait_seconds=30.0,
    )


def select_root_cause(
    errors: list[ClassifiedError],
) -> tuple[ClassifiedError, list[ClassifiedError], float]:
    """Select the most likely root cause from multiple errors.

    Uses priority-based scoring where lower score = more fundamental cause.
    Applies context modifiers for specific error combinations that commonly
    mask root causes.

    Known masking patterns:
    - ENOENT masks everything (missing binary causes cascading failures)
    - Auth errors mask rate limits (can't hit rate limit if auth fails)
    - Network errors mask service errors (can't reach service to get errors)
    - Config errors mask execution errors (bad config causes execution failure)
    - Timeout masks completion (timed out = never got to complete)

    Args:
        errors: List of classified errors to analyze.

    Returns:
        Tuple of (root_cause, symptoms, confidence).
        - root_cause: The most fundamental error that likely caused others
        - symptoms: Other errors that are likely consequences
        - confidence: 0.0-1.0 confidence in root cause identification
          (higher when there's a clear priority gap)
    """
    if not errors:
        # Return an unknown error as fallback
        unknown = ClassifiedError(
            category=ErrorCategory.FATAL,
            message="No errors provided",
            error_code=ErrorCode.UNKNOWN,
            retriable=False,
        )
        return (unknown, [], 0.0)

    if len(errors) == 1:
        return (errors[0], [], 1.0)

    # Calculate modified priorities using index-based lookup
    # (ClassifiedError is a mutable dataclass and not hashable)
    error_codes_present = {e.error_code for e in errors}
    priorities: list[int] = []

    for error in errors:
        priority = ROOT_CAUSE_PRIORITY.get(error.error_code, 99)

        # === Priority Modifiers for Common Masking Patterns ===

        # ENOENT (missing binary) masks everything - it's almost always root cause
        if error.error_code == ErrorCode.BACKEND_NOT_FOUND and any(
            e.error_code != ErrorCode.BACKEND_NOT_FOUND for e in errors
        ):
            priority -= 10  # Strong boost - ENOENT is very fundamental

        # Config path not found is similar - can't run without config
        if error.error_code == ErrorCode.CONFIG_PATH_NOT_FOUND:
            priority -= 5

        # Auth errors mask rate limits (can't be rate limited if auth fails)
        if error.error_code == ErrorCode.BACKEND_AUTH and (
            ErrorCode.RATE_LIMIT_API in error_codes_present
            or ErrorCode.RATE_LIMIT_CLI in error_codes_present
        ):
            priority -= 5

        # Network errors mask service errors
        if error.error_code in (
            ErrorCode.NETWORK_CONNECTION_FAILED,
            ErrorCode.NETWORK_DNS_ERROR,
            ErrorCode.NETWORK_SSL_ERROR,
        ) and (
            ErrorCode.BACKEND_TIMEOUT in error_codes_present
            or ErrorCode.RATE_LIMIT_API in error_codes_present
        ):
            priority -= 3

        # MCP config errors mask tool execution errors
        if (
            error.error_code == ErrorCode.CONFIG_MCP_ERROR
            and ErrorCode.VALIDATION_COMMAND_FAILED in error_codes_present
        ):
            priority -= 3

        # CLI mode errors (streaming vs JSON) are config issues that mask execution
        if error.error_code == ErrorCode.CONFIG_CLI_MODE_ERROR and any(
            e.error_code.category == "execution" for e in errors
        ):
            priority -= 3

        # Timeout is a symptom when paired with rate limits (waited too long)
        if (
            error.error_code == ErrorCode.EXECUTION_TIMEOUT
            and ErrorCode.RATE_LIMIT_API in error_codes_present
        ):
            priority += 5  # Demote timeout - rate limit is root cause

        priorities.append(priority)

    # Find minimum priority (root cause)
    min_idx = min(range(len(errors)), key=lambda i: priorities[i])
    root_cause = errors[min_idx]
    root_priority = priorities[min_idx]

    # Build symptoms list (all errors except root cause)
    symptoms = [errors[i] for i in range(len(errors)) if i != min_idx]
    symptom_priorities = [priorities[i] for i in range(len(errors)) if i != min_idx]

    # Calculate confidence based on priority gap
    # Higher gap = clearer root cause = more confidence
    if symptom_priorities:
        next_priority = min(symptom_priorities)
        gap = next_priority - root_priority

        # Base confidence starts at 0.5 for multiple errors
        # Each priority tier gap adds 5% confidence
        confidence = min(0.5 + (gap * 0.05), 1.0)

        # Boost confidence for known high-signal root causes
        if root_cause.error_code in (
            ErrorCode.BACKEND_NOT_FOUND,  # ENOENT is almost always correct
            ErrorCode.BACKEND_AUTH,  # Auth failures are clear
            ErrorCode.CONFIG_PATH_NOT_FOUND,  # Missing config is clear
        ):
            confidence = min(confidence + 0.15, 1.0)

        # Lower confidence when all errors are in same tier (ambiguous)
        if gap == 0:
            confidence = 0.4  # Significant ambiguity
    else:
        confidence = 1.0

    return (root_cause, symptoms, confidence)
