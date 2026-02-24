"""Error codes, categories, and severity levels.

Contains the structured error classification enums used throughout Mozart.

This module provides:
- ExitReason: Type alias for process exit reasons
- RetryDelays: Constants for retry timing
- Severity: Severity levels for error classification
- RetryBehavior: Precise retry recommendations per error code
- ErrorCode: Structured error codes (E0xx-E9xx)
- ErrorCategory: High-level categories for retry behavior

Error Code Taxonomy
===================

Mozart uses a structured error code system organized by category prefixes.
Each error code provides specific retry behavior guidance.

**E0xx - Execution Errors**
    Process-level failures during command execution.

    | Code | Name | Retriable | Severity | Default Delay |
    |------|------|-----------|----------|---------------|
    | E001 | EXECUTION_TIMEOUT | Yes | ERROR | 60s |
    | E002 | EXECUTION_KILLED | Yes | ERROR | 30s |
    | E003 | EXECUTION_CRASHED | No | CRITICAL | N/A |
    | E004 | EXECUTION_INTERRUPTED | No | ERROR | N/A |
    | E005 | EXECUTION_OOM | No | CRITICAL | N/A |
    | E009 | EXECUTION_UNKNOWN | Yes | ERROR | 10s |

**E1xx - Rate Limit / Capacity Errors**
    API throttling and resource exhaustion.

    | Code | Name | Retriable | Severity | Default Delay |
    |------|------|-----------|----------|---------------|
    | E101 | RATE_LIMIT_API | Yes | ERROR | 1 hour |
    | E102 | RATE_LIMIT_CLI | Yes | ERROR | 15 min |
    | E103 | CAPACITY_EXCEEDED | Yes | WARNING | 5 min |
    | E104 | QUOTA_EXHAUSTED | Yes | ERROR | Dynamic* |

    *E104 delay is parsed from API response reset time.

**E2xx - Validation Errors**
    Output validation failures (retriable - LLM may produce correct output).

    | Code | Name | Retriable | Severity | Default Delay |
    |------|------|-----------|----------|---------------|
    | E201 | VALIDATION_FILE_MISSING | Yes | ERROR | 5s |
    | E202 | VALIDATION_CONTENT_MISMATCH | Yes | ERROR | 5s |
    | E203 | VALIDATION_COMMAND_FAILED | Yes | ERROR | 10s |
    | E204 | VALIDATION_TIMEOUT | Yes | WARNING | 30s |
    | E209 | VALIDATION_GENERIC | Yes | ERROR | 5s |

**E3xx - Configuration Errors**
    Job configuration problems (not retriable - requires user fix).

    | Code | Name | Retriable | Severity |
    |------|------|-----------|----------|
    | E301 | CONFIG_INVALID | No | ERROR |
    | E302 | CONFIG_MISSING_FIELD | No | ERROR |
    | E303 | CONFIG_PATH_NOT_FOUND | No | ERROR |
    | E304 | CONFIG_PARSE_ERROR | No | ERROR |
    | E305 | CONFIG_MCP_ERROR | No | ERROR |
    | E306 | CONFIG_CLI_MODE_ERROR | No | ERROR |

**E4xx - State Errors**
    Checkpoint state issues.

    | Code | Name | Retriable | Severity |
    |------|------|-----------|----------|
    | E401 | STATE_CORRUPTION | No | CRITICAL |
    | E402 | STATE_LOAD_FAILED | Yes | ERROR |
    | E403 | STATE_SAVE_FAILED | Yes | ERROR |
    | E404 | STATE_VERSION_MISMATCH | No | ERROR |

**E5xx - Backend Errors**
    Backend service/connection issues.

    | Code | Name | Retriable | Severity | Default Delay |
    |------|------|-----------|----------|---------------|
    | E501 | BACKEND_CONNECTION | Yes | ERROR | 30s |
    | E502 | BACKEND_AUTH | No | CRITICAL | N/A |
    | E503 | BACKEND_RESPONSE | Yes | ERROR | 15s |
    | E504 | BACKEND_TIMEOUT | Yes | ERROR | 60s |
    | E505 | BACKEND_NOT_FOUND | No | CRITICAL | N/A |

**E6xx - Preflight Errors**
    Pre-execution validation failures (not retriable).

    | Code | Name | Retriable | Severity |
    |------|------|-----------|----------|
    | E601 | PREFLIGHT_PATH_MISSING | No | ERROR |
    | E602 | PREFLIGHT_PROMPT_TOO_LARGE | No | ERROR |
    | E603 | PREFLIGHT_WORKING_DIR_INVALID | No | ERROR |
    | E604 | PREFLIGHT_VALIDATION_SETUP | No | ERROR |

**E9xx - Network/Transient Errors**
    Network connectivity and transient failures.

    | Code | Name | Retriable | Severity | Default Delay |
    |------|------|-----------|----------|---------------|
    | E901 | NETWORK_CONNECTION_FAILED | Yes | ERROR | 30s |
    | E902 | NETWORK_DNS_ERROR | Yes | ERROR | 30s |
    | E903 | NETWORK_SSL_ERROR | Yes | ERROR | 30s |
    | E904 | NETWORK_TIMEOUT | Yes | ERROR | 60s |
    | E999 | UNKNOWN | Yes | ERROR | 30s |

Usage
-----

Error codes are used throughout Mozart for:
- **Programmatic routing**: Switch on error_code.category for handling logic
- **Retry decisions**: Use error_code.get_retry_behavior() for delay/retriability
- **Logging/metrics**: Structured error codes enable aggregation and alerting
- **User guidance**: Error codes map to documentation and troubleshooting guides

Example::

    result = error_classifier.classify(stdout, stderr, exit_code)
    if result.primary.error_code.category == "rate_limit":
        delay = result.primary.error_code.get_retry_behavior().delay_seconds
        await asyncio.sleep(delay)
"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Literal, NamedTuple

# =============================================================================
# Type Aliases
# =============================================================================

# Type alias for exit reasons (matches backend)
ExitReason = Literal["completed", "timeout", "killed", "error"]


# =============================================================================
# Retry Delay Constants
# =============================================================================


class RetryDelays:
    """Constants for retry delay durations.

    Centralizes magic numbers for retry timing to ensure consistency
    across the codebase and make timing decisions discoverable.

    These values represent standard delays for different error scenarios.
    Actual delays may be adjusted dynamically based on error context,
    parsed reset times, or learning from previous attempts.
    """

    # Rate limit delays
    API_RATE_LIMIT: float = 3600.0  # 1 hour for API rate limits
    CLI_RATE_LIMIT: float = 900.0  # 15 minutes for CLI rate limits
    CAPACITY_OVERLOAD: float = 300.0  # 5 minutes for service capacity issues

    # Token/quota delays
    QUOTA_EXHAUSTED_MIN: float = 300.0  # Minimum 5 minutes for quota resets
    QUOTA_EXHAUSTED_DEFAULT: float = 3600.0  # Default 1 hour if no reset time

    # Transient error delays
    NETWORK_ERROR: float = 30.0  # Network connectivity issues
    SERVICE_ERROR: float = 60.0  # Service unavailable / 5xx errors
    TIMEOUT_ERROR: float = 60.0  # Execution timeout recovery

    # Short delays for quick retries
    TRANSIENT_SHORT: float = 5.0  # Brief transient errors
    TRANSIENT_MEDIUM: float = 10.0  # Medium transient errors
    TRANSIENT_LONG: float = 30.0  # Longer transient errors

    # No delay (immediate retry or non-retriable)
    NONE: float = 0.0


# =============================================================================
# Severity Levels
# =============================================================================


class Severity(IntEnum):
    """Severity levels for error classification.

    Lower numeric value = higher severity. This allows comparisons like
    `if severity <= Severity.ERROR` to check for serious issues.

    Assignments:
    - CRITICAL: Job cannot continue, requires immediate attention
      (E003 crash, E005 OOM, E401 corruption, E502 auth, E505 binary not found)
    - ERROR: Operation failed, may be retriable (most error codes)
    - WARNING: Degraded operation, job may continue (E103 capacity, E204 validation timeout)
    - INFO: Informational, no action required (reserved for future diagnostic codes)
    """

    CRITICAL = 1
    ERROR = 2
    WARNING = 3
    INFO = 4


# =============================================================================
# Retry Behavior
# =============================================================================


class RetryBehavior(NamedTuple):
    """Precise retry behavior recommendation for a specific error code.

    Unlike ErrorCategory which provides broad retry guidelines, RetryBehavior
    encodes error-code-specific knowledge about optimal retry strategies.

    Attributes:
        delay_seconds: Recommended delay before retrying (0 = no delay).
        is_retriable: Whether this error is generally retriable.
        reason: Human-readable explanation for the retry behavior.
    """

    delay_seconds: float
    is_retriable: bool
    reason: str


# =============================================================================
# Error Codes
# =============================================================================


class ErrorCode(str, Enum):
    """Structured error codes for comprehensive error classification.

    Error codes are organized by category using numeric prefixes:
    - E0xx: Execution errors (timeouts, crashes, kills)
    - E1xx: Rate limit / capacity errors
    - E2xx: Validation errors
    - E3xx: Configuration errors
    - E4xx: State errors
    - E5xx: Backend errors
    - E6xx: Preflight errors

    Error codes are stable identifiers that can be used for:
    - Programmatic error handling and routing
    - Log aggregation and alerting
    - Documentation and troubleshooting guides
    - Metrics and observability dashboards
    """

    # E0xx: Execution errors
    EXECUTION_TIMEOUT = "E001"
    """Command execution exceeded timeout limit."""

    EXECUTION_KILLED = "E002"
    """Process was killed by a signal (external termination)."""

    EXECUTION_CRASHED = "E003"
    """Process crashed (segfault, bus error, abort, etc.)."""

    EXECUTION_INTERRUPTED = "E004"
    """Process was interrupted by user (SIGINT/Ctrl+C)."""

    EXECUTION_OOM = "E005"
    """Process was killed due to out of memory condition."""

    EXECUTION_UNKNOWN = "E009"
    """Unknown execution error with non-zero exit code."""

    # E1xx: Rate limit / capacity
    RATE_LIMIT_API = "E101"
    """API rate limit exceeded (429, quota, throttling)."""

    RATE_LIMIT_CLI = "E102"
    """CLI-level rate limiting detected."""

    CAPACITY_EXCEEDED = "E103"
    """Service capacity exceeded (overloaded, try again later)."""

    QUOTA_EXHAUSTED = "E104"
    """Token/usage quota exhausted - wait until reset time."""

    # E2xx: Validation errors
    VALIDATION_FILE_MISSING = "E201"
    """Expected output file does not exist."""

    VALIDATION_CONTENT_MISMATCH = "E202"
    """Output content does not match expected pattern."""

    VALIDATION_COMMAND_FAILED = "E203"
    """Validation command returned non-zero exit code."""

    VALIDATION_TIMEOUT = "E204"
    """Validation check timed out."""

    VALIDATION_GENERIC = "E209"
    """Generic validation failure (output validation needed)."""

    # E3xx: Configuration errors
    CONFIG_INVALID = "E301"
    """Configuration file is malformed or invalid."""

    CONFIG_MISSING_FIELD = "E302"
    """Required configuration field is missing."""

    CONFIG_PATH_NOT_FOUND = "E303"
    """Configuration file path does not exist."""

    CONFIG_PARSE_ERROR = "E304"
    """Failed to parse configuration file (YAML/JSON syntax error)."""

    CONFIG_MCP_ERROR = "E305"
    """MCP server/plugin configuration error (missing env vars, invalid config)."""

    CONFIG_CLI_MODE_ERROR = "E306"
    """Claude CLI mode mismatch (e.g., streaming mode incompatible with operation)."""

    # E4xx: State errors
    STATE_CORRUPTION = "E401"
    """Checkpoint state file is corrupted or inconsistent."""

    STATE_LOAD_FAILED = "E402"
    """Failed to load checkpoint state from storage."""

    STATE_SAVE_FAILED = "E403"
    """Failed to save checkpoint state to storage."""

    STATE_VERSION_MISMATCH = "E404"
    """Checkpoint state version is incompatible."""

    # E5xx: Backend errors
    BACKEND_CONNECTION = "E501"
    """Failed to connect to backend service."""

    BACKEND_AUTH = "E502"
    """Backend authentication or authorization failed."""

    BACKEND_RESPONSE = "E503"
    """Invalid or unexpected response from backend."""

    BACKEND_TIMEOUT = "E504"
    """Backend request timed out."""

    BACKEND_NOT_FOUND = "E505"
    """Backend executable or service not found."""

    # E6xx: Preflight errors
    PREFLIGHT_PATH_MISSING = "E601"
    """Required path does not exist (working_dir, referenced file)."""

    PREFLIGHT_PROMPT_TOO_LARGE = "E602"
    """Prompt exceeds recommended token limit."""

    PREFLIGHT_WORKING_DIR_INVALID = "E603"
    """Working directory is not accessible or not a directory."""

    PREFLIGHT_VALIDATION_SETUP = "E604"
    """Validation target path or pattern is invalid."""

    # E9xx: Transient/network errors
    NETWORK_CONNECTION_FAILED = "E901"
    """Network connection failed (refused, reset, unreachable)."""

    NETWORK_DNS_ERROR = "E902"
    """DNS resolution failed."""

    NETWORK_SSL_ERROR = "E903"
    """SSL/TLS handshake or certificate error."""

    NETWORK_TIMEOUT = "E904"
    """Network operation timed out."""

    # Fallback
    UNKNOWN = "E999"
    """Unclassified error - requires investigation."""

    @property
    def category(self) -> str:
        """Get the category prefix (first digit) of this error code.

        Returns:
            Category string like "execution", "rate_limit", "validation", etc.
        """
        category_map = {
            "0": "execution",
            "1": "rate_limit",
            "2": "validation",
            "3": "configuration",
            "4": "state",
            "5": "backend",
            "6": "preflight",
            "9": "transient",
        }
        # Error code format: "E<digit><digit><digit>"
        if len(self.value) >= 2:
            first_digit = self.value[1]
            return category_map.get(first_digit, "unknown")
        return "unknown"

    @property
    def is_retriable(self) -> bool:
        """Check if this error code is generally retriable.

        Returns:
            True if errors with this code are typically retriable.
        """
        # Non-retriable codes
        non_retriable = {
            ErrorCode.EXECUTION_CRASHED,
            ErrorCode.EXECUTION_INTERRUPTED,
            ErrorCode.EXECUTION_OOM,
            ErrorCode.BACKEND_AUTH,
            ErrorCode.BACKEND_NOT_FOUND,
            ErrorCode.CONFIG_INVALID,
            ErrorCode.CONFIG_MISSING_FIELD,
            ErrorCode.CONFIG_PATH_NOT_FOUND,
            ErrorCode.CONFIG_PARSE_ERROR,
            ErrorCode.CONFIG_MCP_ERROR,
            ErrorCode.CONFIG_CLI_MODE_ERROR,
            ErrorCode.STATE_CORRUPTION,
            ErrorCode.STATE_VERSION_MISMATCH,
            ErrorCode.PREFLIGHT_PATH_MISSING,
            ErrorCode.PREFLIGHT_PROMPT_TOO_LARGE,
            ErrorCode.PREFLIGHT_WORKING_DIR_INVALID,
            ErrorCode.PREFLIGHT_VALIDATION_SETUP,
        }
        return self not in non_retriable

    def get_retry_behavior(self) -> RetryBehavior:
        """Get precise retry behavior for this error code.

        Returns error-code-specific delay and retry recommendations.
        Uses module-level _RETRY_BEHAVIORS constant to avoid rebuilding
        the lookup table on every call.

        Returns:
            RetryBehavior with delay, retriability, and reason.
        """
        return _RETRY_BEHAVIORS.get(
            self,
            RetryBehavior(
                delay_seconds=30.0,
                is_retriable=self.is_retriable,
                reason=f"Default behavior for {self.value}",
            ),
        )

    def get_severity(self) -> Severity:
        """Get the severity level for this error code.

        Severity assignments:
        - CRITICAL: Fatal errors requiring immediate attention
        - ERROR: Most error codes (default)
        - WARNING: Degraded but potentially temporary conditions
        - INFO: Reserved for future diagnostic codes

        Returns:
            Severity level for this error code.
        """
        # Critical errors - job cannot continue
        critical_codes = {
            ErrorCode.EXECUTION_CRASHED,
            ErrorCode.EXECUTION_OOM,
            ErrorCode.STATE_CORRUPTION,
            ErrorCode.BACKEND_AUTH,
            ErrorCode.BACKEND_NOT_FOUND,
        }
        if self in critical_codes:
            return Severity.CRITICAL

        # Warning level - degraded but potentially temporary
        warning_codes = {
            ErrorCode.CAPACITY_EXCEEDED,
            ErrorCode.VALIDATION_TIMEOUT,
        }
        if self in warning_codes:
            return Severity.WARNING

        # Default to ERROR for most codes
        return Severity.ERROR


# =============================================================================
# Retry Behavior Lookup (module-level constant — avoids rebuilding per call)
# =============================================================================

_RETRY_BEHAVIORS: dict[ErrorCode, RetryBehavior] = {
    # E0xx: Execution errors
    ErrorCode.EXECUTION_TIMEOUT: RetryBehavior(
        delay_seconds=60.0, is_retriable=True,
        reason="Timeout - allow system to recover before retry",
    ),
    ErrorCode.EXECUTION_KILLED: RetryBehavior(
        delay_seconds=30.0, is_retriable=True,
        reason="Process killed externally - may be transient",
    ),
    ErrorCode.EXECUTION_CRASHED: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Process crashed - likely a bug, not transient",
    ),
    ErrorCode.EXECUTION_INTERRUPTED: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="User interrupted - respect user intent",
    ),
    ErrorCode.EXECUTION_OOM: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Out of memory - will likely recur",
    ),
    ErrorCode.EXECUTION_UNKNOWN: RetryBehavior(
        delay_seconds=10.0, is_retriable=True,
        reason="Unknown execution error - attempt retry with backoff",
    ),
    # E1xx: Rate limit / capacity
    ErrorCode.RATE_LIMIT_API: RetryBehavior(
        delay_seconds=3600.0, is_retriable=True,
        reason="API rate limit - wait for quota reset",
    ),
    ErrorCode.RATE_LIMIT_CLI: RetryBehavior(
        delay_seconds=900.0, is_retriable=True,
        reason="CLI rate limit - shorter wait than API",
    ),
    ErrorCode.CAPACITY_EXCEEDED: RetryBehavior(
        delay_seconds=300.0, is_retriable=True,
        reason="Service overloaded - wait for capacity",
    ),
    ErrorCode.QUOTA_EXHAUSTED: RetryBehavior(
        delay_seconds=0.0, is_retriable=True,
        reason="Token quota exhausted - wait until reset time",
    ),
    # E2xx: Validation errors
    ErrorCode.VALIDATION_FILE_MISSING: RetryBehavior(
        delay_seconds=5.0, is_retriable=True,
        reason="File not created - prompt may need adjustment",
    ),
    ErrorCode.VALIDATION_CONTENT_MISMATCH: RetryBehavior(
        delay_seconds=5.0, is_retriable=True,
        reason="Content doesn't match - retry with same prompt",
    ),
    ErrorCode.VALIDATION_COMMAND_FAILED: RetryBehavior(
        delay_seconds=10.0, is_retriable=True,
        reason="Validation command failed - may be transient",
    ),
    ErrorCode.VALIDATION_TIMEOUT: RetryBehavior(
        delay_seconds=30.0, is_retriable=True,
        reason="Validation timed out - allow more time",
    ),
    ErrorCode.VALIDATION_GENERIC: RetryBehavior(
        delay_seconds=5.0, is_retriable=True,
        reason="Validation needed - retry with validation",
    ),
    # E3xx: Configuration errors - generally not retriable
    ErrorCode.CONFIG_INVALID: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Invalid config - requires user fix",
    ),
    ErrorCode.CONFIG_MISSING_FIELD: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Missing config field - requires user fix",
    ),
    ErrorCode.CONFIG_PATH_NOT_FOUND: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Config file not found - requires user fix",
    ),
    ErrorCode.CONFIG_PARSE_ERROR: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Config parse error - requires user fix",
    ),
    ErrorCode.CONFIG_MCP_ERROR: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="MCP config error - requires user fix",
    ),
    ErrorCode.CONFIG_CLI_MODE_ERROR: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="CLI mode config error - requires user fix",
    ),
    # E4xx: State errors
    ErrorCode.STATE_CORRUPTION: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Corrupted state - requires manual recovery",
    ),
    ErrorCode.STATE_LOAD_FAILED: RetryBehavior(
        delay_seconds=5.0, is_retriable=True,
        reason="State load failed - may be transient I/O",
    ),
    ErrorCode.STATE_SAVE_FAILED: RetryBehavior(
        delay_seconds=5.0, is_retriable=True,
        reason="State save failed - may be transient I/O",
    ),
    ErrorCode.STATE_VERSION_MISMATCH: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="State version mismatch - requires migration",
    ),
    # E5xx: Backend errors
    ErrorCode.BACKEND_CONNECTION: RetryBehavior(
        delay_seconds=30.0, is_retriable=True,
        reason="Backend connection failed - may recover",
    ),
    ErrorCode.BACKEND_AUTH: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Auth failed - requires credential fix",
    ),
    ErrorCode.BACKEND_RESPONSE: RetryBehavior(
        delay_seconds=15.0, is_retriable=True,
        reason="Invalid backend response - may be transient",
    ),
    ErrorCode.BACKEND_TIMEOUT: RetryBehavior(
        delay_seconds=60.0, is_retriable=True,
        reason="Backend timeout - allow recovery time",
    ),
    ErrorCode.BACKEND_NOT_FOUND: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Backend not found - requires installation",
    ),
    # E6xx: Preflight errors
    ErrorCode.PREFLIGHT_PATH_MISSING: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Required path missing - requires user fix",
    ),
    ErrorCode.PREFLIGHT_PROMPT_TOO_LARGE: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Prompt too large - requires user fix",
    ),
    ErrorCode.PREFLIGHT_WORKING_DIR_INVALID: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Invalid working dir - requires user fix",
    ),
    ErrorCode.PREFLIGHT_VALIDATION_SETUP: RetryBehavior(
        delay_seconds=0.0, is_retriable=False,
        reason="Invalid validation setup - requires user fix",
    ),
    # E9xx: Transient/network errors
    ErrorCode.NETWORK_CONNECTION_FAILED: RetryBehavior(
        delay_seconds=30.0, is_retriable=True,
        reason="Network connection failed - may recover",
    ),
    ErrorCode.NETWORK_DNS_ERROR: RetryBehavior(
        delay_seconds=30.0, is_retriable=True,
        reason="DNS error - may be transient",
    ),
    ErrorCode.NETWORK_SSL_ERROR: RetryBehavior(
        delay_seconds=30.0, is_retriable=True,
        reason="SSL error - may be transient",
    ),
    ErrorCode.NETWORK_TIMEOUT: RetryBehavior(
        delay_seconds=60.0, is_retriable=True,
        reason="Network timeout - allow recovery time",
    ),
    # Fallback
    ErrorCode.UNKNOWN: RetryBehavior(
        delay_seconds=30.0, is_retriable=True,
        reason="Unknown error - attempt retry with backoff",
    ),
}


# =============================================================================
# Error Categories
# =============================================================================


class ErrorCategory(str, Enum):
    """Categories of errors with different retry behaviors."""

    RATE_LIMIT = "rate_limit"
    """Retriable with long wait - API/service is rate limiting."""

    TRANSIENT = "transient"
    """Retriable with backoff - temporary network/service issues."""

    VALIDATION = "validation"
    """Retriable - Claude ran but didn't produce expected output."""

    AUTH = "auth"
    """Fatal - authentication/authorization failure, needs user intervention."""

    NETWORK = "network"
    """Retriable with backoff - network connectivity issues."""

    TIMEOUT = "timeout"
    """Retriable - operation timed out."""

    SIGNAL = "signal"
    """Process killed by signal - may be retriable depending on signal."""

    FATAL = "fatal"
    """Non-retriable - stop job immediately."""

    CONFIGURATION = "configuration"
    """Non-retriable - configuration error needs user intervention (e.g., MCP setup)."""

    PREFLIGHT = "preflight"
    """Pre-execution check failure — config or environment not ready."""

    ESCALATION = "escalation"
    """Escalation-based abort — grounding or escalation policy triggered."""
