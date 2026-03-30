"""ErrorClassifier implementation for pattern-based error classification.

This is the main classifier class that analyzes stdout, stderr, exit codes,
and signals to produce ClassifiedError instances with appropriate retry behavior.
"""

from __future__ import annotations

import re
import signal
from datetime import datetime, timedelta

from mozart.core.constants import (
    DEFAULT_QUOTA_WAIT_SECONDS,
    DEFAULT_RATE_LIMIT_WAIT_SECONDS,
    RESET_TIME_MINIMUM_WAIT_SECONDS,
)
from mozart.core.logging import get_logger

from .codes import ErrorCategory, ErrorCode, ExitReason
from .models import ClassificationInput, ClassificationResult, ClassifiedError, ParsedCliError
from .parsers import classify_single_json_error, select_root_cause, try_parse_json_errors
from .signals import FATAL_SIGNALS, RETRIABLE_SIGNALS, get_signal_name

# Module-level logger for error classification
_logger = get_logger("errors")

# Log event name constant to avoid repetition across classify methods
_EVT_ERROR_CLASSIFIED = "error_classified"


# =============================================================================
# Default pattern strings for ErrorClassifier.
# Extracted to module scope so the constructor focuses on initialization logic
# and the patterns are easily reviewable/testable as data.
# =============================================================================

_DEFAULT_RATE_LIMIT_PATTERNS: list[str] = [
    r"rate.?limit",
    r"usage.?limit",
    r"quota",
    r"too many requests",
    r"429",
    r"capacity",
    r"try again later",
    r"overloaded",
    r"hit.{0,10}limit",       # "You've hit your limit"
    r"limit.{0,10}resets?",   # "limit · resets 9pm"
    r"daily.{0,10}limit",     # "daily limit reached"
]

_DEFAULT_AUTH_PATTERNS: list[str] = [
    r"unauthorized",
    r"authentication",
    r"invalid.?api.?key",
    r"permission.?denied",
    r"access.?denied",
    r"401",
    r"403",
]

_DEFAULT_NETWORK_PATTERNS: list[str] = [
    r"connection.?refused",
    r"connection.?reset",
    r"connection.?timeout",
    r"network.?unreachable",
    r"ECONNREFUSED",
    r"ETIMEDOUT",
]

_DEFAULT_DNS_PATTERNS: list[str] = [
    r"dns.?resolution",
    r"name.?resolution",
    r"getaddrinfo",
    r"could not resolve",
    r"ENOTFOUND",
]

_DEFAULT_SSL_PATTERNS: list[str] = [
    r"ssl.?error",
    r"tls.?error",
    r"certificate",
    r"SSL_ERROR",
    r"handshake.?failed",
]

_DEFAULT_CAPACITY_PATTERNS: list[str] = [
    r"capacity",
    r"try again later",
    r"overloaded",
    r"service.?unavailable",
]

_DEFAULT_QUOTA_EXHAUSTION_PATTERNS: list[str] = [
    r"token.{0,10}exhausted",
    r"token.{0,10}budget.{0,10}(used|exhausted|depleted)",
    r"usage.{0,10}(will\s+)?reset.{0,10}(at|in)",
    r"resets?.{0,10}\d+\s*[ap]m",
    r"resets?.{0,10}in\s+\d+\s*(hour|minute|min|hr)",
    r"daily.{0,10}(token|usage).{0,10}limit",
    r"hourly.{0,10}(token|usage).{0,10}limit",
    r"(used|consumed).{0,10}all.{0,10}(token|credit)",
    r"no.{0,10}(token|credit).{0,10}(left|remaining)",
    r"token.{0,10}allowance.{0,10}(used|exhausted)",
    r"recharge.{0,10}(at|in)",
]

_DEFAULT_RESET_TIME_PATTERNS: list[str] = [
    r"resets?\s+(?:at\s+)?(\d{1,2})\s*([ap]m)",
    r"resets?\s+(?:at\s+)?(\d{1,2}):(\d{2})",
    r"resets?\s+in\s+(\d+)\s*(hour|hr|minute|min)s?",
    r"reset.{0,20}(\d{1,2})\s*([ap]m)",
]

_DEFAULT_MCP_PATTERNS: list[str] = [
    r"MCP server error",
    r"mcp-config-invalid",
    r"Missing environment variables:",
    r"Plugin MCP server",
    r"MCP server .+ invalid",
]

_DEFAULT_CLI_MODE_PATTERNS: list[str] = [
    r"only prompt commands are supported in streaming mode",
    r"streaming mode.*not supported",
    r"output format.*not compatible",
]

_DEFAULT_ENOENT_PATTERNS: list[str] = [
    r"ENOENT",
    r"spawn .+ ENOENT",
    r"no such file or directory",
    r"command not found",
    r"not found in PATH",
]

_DEFAULT_STALE_PATTERNS: list[str] = [
    r"[Ss]tale execution",
    r"no output for \d+s",
    r"idle.{0,10}timeout",
]

# Output substrings that indicate a non-transient (non-retriable) failure
# for exit codes 1/2 in _classify_by_exit_code.
_NON_TRANSIENT_INDICATORS: tuple[str, ...] = (
    "permission denied",
    "access denied",
    "not permitted",
    "validation failed",
    "invalid argument",
    "syntax error",
)


def _compile_patterns(strings: list[str]) -> list[re.Pattern[str]]:
    """Compile a list of regex strings into case-insensitive Pattern objects."""
    return [re.compile(p, re.IGNORECASE) for p in strings]


# =============================================================================
# Error Classifier
# =============================================================================


class ErrorClassifier:
    """Classifies errors based on patterns and exit codes.

    Pattern matching follows the approach from run-sheet-review.sh
    which checks output for rate limit indicators.
    """

    def __init__(
        self,
        rate_limit_patterns: list[str] | None = None,
        auth_patterns: list[str] | None = None,
        network_patterns: list[str] | None = None,
    ):
        """Initialize classifier with detection patterns.

        Args:
            rate_limit_patterns: Regex patterns indicating rate limiting
            auth_patterns: Regex patterns indicating auth failures
            network_patterns: Regex patterns indicating network issues
        """
        self.rate_limit_patterns = _compile_patterns(
            rate_limit_patterns or _DEFAULT_RATE_LIMIT_PATTERNS
        )
        self.auth_patterns = _compile_patterns(auth_patterns or _DEFAULT_AUTH_PATTERNS)
        self.network_patterns = _compile_patterns(network_patterns or _DEFAULT_NETWORK_PATTERNS)
        self.dns_patterns = _compile_patterns(_DEFAULT_DNS_PATTERNS)
        self.ssl_patterns = _compile_patterns(_DEFAULT_SSL_PATTERNS)
        self.capacity_patterns = _compile_patterns(_DEFAULT_CAPACITY_PATTERNS)
        self.quota_exhaustion_patterns = _compile_patterns(_DEFAULT_QUOTA_EXHAUSTION_PATTERNS)
        self.reset_time_patterns = _compile_patterns(_DEFAULT_RESET_TIME_PATTERNS)
        self.mcp_patterns = _compile_patterns(_DEFAULT_MCP_PATTERNS)
        self.cli_mode_patterns = _compile_patterns(_DEFAULT_CLI_MODE_PATTERNS)
        self.enoent_patterns = _compile_patterns(_DEFAULT_ENOENT_PATTERNS)
        self.stale_patterns = _compile_patterns(_DEFAULT_STALE_PATTERNS)

        # Pre-computed combined regex patterns for _matches_any().
        # Each pattern list is merged into a single alternation regex so that
        # matching is a single .search() call per category.
        self._combined_cache: dict[int, re.Pattern[str]] = {}
        for attr_name in (
            "rate_limit_patterns", "auth_patterns", "network_patterns",
            "dns_patterns", "ssl_patterns", "capacity_patterns",
            "quota_exhaustion_patterns", "mcp_patterns",
            "cli_mode_patterns", "enoent_patterns", "stale_patterns",
        ):
            patterns = getattr(self, attr_name)
            if patterns:
                alternation = "|".join(f"(?:{p.pattern})" for p in patterns)
                self._combined_cache[id(patterns)] = re.compile(
                    alternation, re.IGNORECASE
                )

    def parse_reset_time(self, text: str) -> float | None:
        """Parse reset time from message and return seconds until reset.

        Supports patterns like:
        - "resets at 9pm" -> seconds until 9pm (or next day if past)
        - "resets at 21:00" -> seconds until 21:00
        - "resets in 3 hours" -> 3 * 3600 seconds
        - "resets in 30 minutes" -> 30 * 60 seconds

        Args:
            text: Error message that may contain reset time info.

        Returns:
            Seconds until reset, or None if no reset time found.
            Returns minimum of RESET_TIME_MINIMUM_WAIT_SECONDS to avoid immediate retries.
        """

        for pattern in self.reset_time_patterns:
            match = pattern.search(text)
            if not match:
                continue

            groups = match.groups()

            # Pattern: "resets in X hours/minutes"
            if (
                len(groups) == 2
                and groups[1]
                and groups[1].lower() in ("hour", "hr", "minute", "min")
            ):
                amount = int(groups[0])
                unit = groups[1].lower()
                seconds: float = amount * 3600 if unit in ("hour", "hr") else amount * 60
                return max(seconds, RESET_TIME_MINIMUM_WAIT_SECONDS)  # At least 5 minutes

            # Pattern: "resets at X:XX" (24-hour time)
            if len(groups) == 2 and groups[1] and groups[1].isdigit():
                hour = int(groups[0])
                minute = int(groups[1])
                now = datetime.now()
                reset_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if reset_time <= now:
                    reset_time += timedelta(days=1)  # Next day
                seconds = (reset_time - now).total_seconds()
                return max(seconds, RESET_TIME_MINIMUM_WAIT_SECONDS)

            # Pattern: "resets at Xpm/Xam"
            if len(groups) == 2 and groups[1] and groups[1].lower() in ("am", "pm"):
                hour = int(groups[0])
                meridiem = groups[1].lower()
                if meridiem == "pm" and hour != 12:
                    hour += 12
                elif meridiem == "am" and hour == 12:
                    hour = 0
                now = datetime.now()
                reset_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if reset_time <= now:
                    reset_time += timedelta(days=1)  # Next day
                seconds = (reset_time - now).total_seconds()
                return max(seconds, RESET_TIME_MINIMUM_WAIT_SECONDS)

        # No pattern matched, return default wait
        return None

    def classify(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        exit_signal: int | None = None,
        exit_reason: ExitReason | None = None,
        exception: Exception | None = None,
        output_format: str | None = None,
    ) -> ClassifiedError:
        """Classify an error based on output, exit code, and signal.

        Delegates to sub-classifiers in priority order:
        1. Signal-based exits (_classify_signal)
        2. Timeout exit reason
        3. Pattern-matching on output (_classify_by_pattern)
        4. Exit code analysis (_classify_by_exit_code)
        5. Unknown fallback

        Args:
            stdout: Standard output from the command
            stderr: Standard error from the command
            exit_code: Process exit code (0 = success), None if killed by signal
            exit_signal: Signal number if killed by signal
            exit_reason: Why execution ended (completed, timeout, killed, error)
            exception: Optional exception that was raised
            output_format: Backend output format ("text", "json", "stream-json").
                When "text", exit code 1 is classified as E209 (validation)
                instead of E009 (unknown).

        Returns:
            ClassifiedError with category, error_code, and metadata
        """
        combined = f"{stdout}\n{stderr}"
        if exception:
            combined += f"\n{str(exception)}"

        # 0. Negative exit codes indicate signal kills (e.g., -9 = SIGKILL)
        # Python's subprocess reports killed-by-signal as negative exit codes.
        if exit_code is not None and exit_code < 0:
            exit_signal = abs(exit_code)

        # 1. Signal-based exits
        if exit_signal is not None:
            result = self._classify_signal(
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                exception=exception,
                stdout=stdout,
                stderr=stderr,
            )
            _logger.warning(
                _EVT_ERROR_CLASSIFIED,
                category=result.category.value,
                error_code=result.error_code.value,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                retriable=result.retriable,
                suggested_wait=result.suggested_wait_seconds,
                message=result.message,
            )
            return result

        # 2. Timeout exit reason (even without signal)
        #    Differentiate stale detection (E006) from backend timeout (E001).
        #    Stale detection writes "Stale execution:" to stderr — F-097.
        if exit_reason == "timeout":
            is_stale = "stale execution" in combined.lower()
            error_code = (
                ErrorCode.EXECUTION_STALE if is_stale
                else ErrorCode.EXECUTION_TIMEOUT
            )
            message = (
                "Stale execution detected — no output activity"
                if is_stale
                else "Command timed out"
            )
            wait_seconds = 120.0 if is_stale else 60.0
            result = ClassifiedError(
                category=ErrorCategory.TIMEOUT,
                message=message,
                error_code=error_code,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=wait_seconds,
            )
            _logger.warning(
                _EVT_ERROR_CLASSIFIED,
                category=result.category.value,
                error_code=result.error_code.value,
                exit_code=exit_code,
                exit_reason=exit_reason,
                retriable=result.retriable,
                message=result.message,
            )
            return result

        # 3. Pattern-matching on output text
        pattern_result = self._classify_by_pattern(
            combined, exit_code, exit_reason, exception,
        )
        if pattern_result is not None:
            return pattern_result

        # 4. Exit code analysis (with output for non-transient detection)
        exit_code_result = self._classify_by_exit_code(
            exit_code, exit_reason, exception, combined, output_format,
        )
        if exit_code_result is not None:
            return exit_code_result

        # 5. exit_code=None: process killed or disappeared without exit code.
        # Always retriable — this is never a deterministic user error.
        # Check stderr for OOM indicators to set appropriate wait time.
        if exit_code is None:
            # OOM/kill indicators in stderr → longer wait (memory needs to free)
            oom_indicators = ("killed", "out of memory", "oom", "cannot allocate")
            stderr_lower = stderr.lower() if stderr else ""
            is_oom = any(indicator in stderr_lower for indicator in oom_indicators)
            wait_seconds = 60.0 if is_oom else 10.0
            message = (
                "Process killed (possible OOM — retrying with longer wait)"
                if is_oom
                else "Process exited without exit code (possible signal race — retrying)"
            )

            result = ClassifiedError(
                category=ErrorCategory.TRANSIENT,
                message=message,
                error_code=ErrorCode.UNKNOWN,
                original_error=exception,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=wait_seconds,
            )
            _logger.warning(
                _EVT_ERROR_CLASSIFIED,
                category=result.category.value,
                error_code=result.error_code.value,
                exit_code=exit_code,
                retriable=result.retriable,
                is_oom=is_oom,
                message=result.message,
            )
            return result

        # 6. Unknown fallback
        result = ClassifiedError(
            category=ErrorCategory.FATAL,
            message=f"Unknown error (exit_code={exit_code})",
            error_code=ErrorCode.UNKNOWN,
            original_error=exception,
            exit_code=exit_code,
            exit_signal=None,
            exit_reason=exit_reason,
            retriable=False,
        )
        _logger.warning(
            _EVT_ERROR_CLASSIFIED,
            category=result.category.value,
            error_code=result.error_code.value,
            exit_code=exit_code,
            retriable=result.retriable,
            message=result.message,
        )
        return result

    def _classify_by_pattern(
        self,
        combined: str,
        exit_code: int | None,
        exit_reason: ExitReason | None,
        exception: Exception | None,
    ) -> ClassifiedError | None:
        """Classify error by matching output text against known patterns.

        Checks patterns in priority order: quota exhaustion, rate limits,
        ENOENT, CLI mode mismatch, auth, MCP, DNS, SSL, network.

        Falls through to _classify_network_pattern() for DNS/SSL/network
        checks, which returns None if no pattern matches.
        """
        # Quota exhaustion (most specific rate limit variant)
        if self._matches_any(combined, self.quota_exhaustion_patterns):
            wait_seconds = self.parse_reset_time(combined)
            if wait_seconds is None:
                wait_seconds = DEFAULT_QUOTA_WAIT_SECONDS
            result = ClassifiedError(
                category=ErrorCategory.RATE_LIMIT,
                message="Token quota exhausted - waiting until reset",
                error_code=ErrorCode.QUOTA_EXHAUSTED,
                original_error=exception,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=wait_seconds,
            )
            _logger.warning(
                "quota_exhausted",
                component="errors",
                error_code=result.error_code.value,
                exit_code=exit_code,
                retriable=result.retriable,
                suggested_wait_seconds=wait_seconds,
                message=result.message,
            )
            return result

        # Rate limiting (most common retriable)
        if self._matches_any(combined, self.rate_limit_patterns):
            error_code = (
                ErrorCode.CAPACITY_EXCEEDED
                if self._matches_any(combined, self.capacity_patterns)
                else ErrorCode.RATE_LIMIT_API
            )
            result = ClassifiedError(
                category=ErrorCategory.RATE_LIMIT,
                message="Rate limit detected",
                error_code=error_code,
                original_error=exception,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=DEFAULT_RATE_LIMIT_WAIT_SECONDS,
            )
            _logger.warning(
                _EVT_ERROR_CLASSIFIED,
                category=result.category.value,
                error_code=result.error_code.value,
                exit_code=exit_code,
                retriable=result.retriable,
                suggested_wait=result.suggested_wait_seconds,
                message=result.message,
            )
            return result

        # Data-driven pattern checks (priority order: ENOENT, CLI mode, auth, MCP)
        # Each entry: (patterns, category, message, error_code, retriable, wait_seconds)
        _PATTERN_CHECKS: list[tuple[
            list[re.Pattern[str]], ErrorCategory, str, ErrorCode, bool, float | None
        ]] = [
            (self.enoent_patterns, ErrorCategory.CONFIGURATION,
             "Missing file or binary (ENOENT) - CLI dependency may be missing or being updated",
             ErrorCode.BACKEND_NOT_FOUND, True, 30.0),
            (self.cli_mode_patterns, ErrorCategory.CONFIGURATION,
             "CLI mode mismatch - streaming mode incompatible with operation",
             ErrorCode.CONFIG_CLI_MODE_ERROR, False, None),
            (self.auth_patterns, ErrorCategory.AUTH,
             "Authentication or authorization failure",
             ErrorCode.BACKEND_AUTH, False, None),
            (self.mcp_patterns, ErrorCategory.CONFIGURATION,
             "MCP server configuration error - check environment variables",
             ErrorCode.CONFIG_MCP_ERROR, False, None),
        ]

        for patterns, category, message, error_code, retriable, wait_secs in _PATTERN_CHECKS:
            if self._matches_any(combined, patterns):
                result = ClassifiedError(
                    category=category,
                    message=message,
                    error_code=error_code,
                    original_error=exception,
                    exit_code=exit_code,
                    exit_signal=None,
                    exit_reason=exit_reason,
                    retriable=retriable,
                    suggested_wait_seconds=wait_secs,
                )
                _logger.warning(
                    _EVT_ERROR_CLASSIFIED,
                    category=result.category.value,
                    error_code=result.error_code.value,
                    exit_code=exit_code,
                    retriable=result.retriable,
                    message=result.message,
                )
                return result

        # Network issues: DNS, SSL, generic (checked in specificity order)
        return self._classify_network_pattern(combined, exit_code, exit_reason, exception)

    def _classify_network_pattern(
        self,
        combined: str,
        exit_code: int | None,
        exit_reason: ExitReason | None,
        exception: Exception | None,
    ) -> ClassifiedError | None:
        """Classify network-related errors by pattern specificity.

        Checks DNS → SSL → generic network in order.
        Returns None if no network pattern matches.
        """
        _NETWORK_CHECKS = [
            (self.dns_patterns, "DNS resolution failed",
             ErrorCode.NETWORK_DNS_ERROR),
            (self.ssl_patterns, "SSL/TLS error",
             ErrorCode.NETWORK_SSL_ERROR),
            (self.network_patterns, "Network connectivity issue",
             ErrorCode.NETWORK_CONNECTION_FAILED),
        ]
        for patterns, message, error_code in _NETWORK_CHECKS:
            if self._matches_any(combined, patterns):
                result = ClassifiedError(
                    category=ErrorCategory.NETWORK,
                    message=message,
                    error_code=error_code,
                    original_error=exception,
                    exit_code=exit_code,
                    exit_signal=None,
                    exit_reason=exit_reason,
                    retriable=True,
                    suggested_wait_seconds=30.0,
                )
                _logger.warning(
                    _EVT_ERROR_CLASSIFIED,
                    category=result.category.value,
                    error_code=result.error_code.value,
                    exit_code=exit_code,
                    retriable=result.retriable,
                    suggested_wait=result.suggested_wait_seconds,
                    message=result.message,
                )
                return result
        return None

    def _classify_by_exit_code(
        self,
        exit_code: int | None,
        exit_reason: ExitReason | None,
        exception: Exception | None,
        combined: str = "",
        output_format: str | None = None,
    ) -> ClassifiedError | None:
        """Classify error based on process exit code.

        For exit codes 1/2, checks output for non-transient indicators before
        defaulting to TRANSIENT classification. This prevents wasting retries
        on errors like permission denied or validation failures.

        When output_format is "text", exit code 1 is treated as a validation
        scenario (E209) rather than an unknown execution error (E009), since
        Claude CLI text mode uses exit code 1 to signal "task complete but
        output may need validation".

        Returns None if exit_code is None (signal-killed) or unrecognized.
        """
        if exit_code == 0:
            return ClassifiedError(
                category=ErrorCategory.VALIDATION,
                message="Command succeeded but output validation needed",
                error_code=ErrorCode.VALIDATION_GENERIC,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
            )

        if exit_code is None:
            return None

        if exit_code in (1, 2):
            # Text mode: exit code 1 is a normal "task complete, validation
            # needed" signal — classify as VALIDATION_GENERIC (E209) to avoid
            # false E009 warnings and unnecessary 10s retry delays.
            if exit_code == 1 and output_format == "text":
                result = ClassifiedError(
                    category=ErrorCategory.VALIDATION,
                    message="Text mode completed — output validation needed",
                    error_code=ErrorCode.VALIDATION_GENERIC,
                    original_error=exception,
                    exit_code=exit_code,
                    exit_signal=None,
                    exit_reason=exit_reason,
                    retriable=True,
                    suggested_wait_seconds=None,
                )
                _logger.info(
                    _EVT_ERROR_CLASSIFIED,
                    category=result.category.value,
                    error_code=result.error_code.value,
                    exit_code=exit_code,
                    retriable=result.retriable,
                    message=result.message,
                    output_format=output_format,
                )
                return result

            # Check output for non-transient error indicators before defaulting
            # to TRANSIENT. Exit code 1 can mean permission denied, validation
            # failure, or other non-retriable errors.
            combined_lower = combined.lower()
            is_non_transient = any(
                ind in combined_lower for ind in _NON_TRANSIENT_INDICATORS
            )

            if is_non_transient:
                category = ErrorCategory.FATAL
                retriable = False
                message = f"Non-transient failure with exit code {exit_code}"
                suggested_wait = None
            else:
                category = ErrorCategory.TRANSIENT
                retriable = True
                message = f"Command failed with exit code {exit_code}"
                suggested_wait = 10.0

            result = ClassifiedError(
                category=category,
                message=message,
                error_code=ErrorCode.EXECUTION_UNKNOWN,
                original_error=exception,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=retriable,
                suggested_wait_seconds=suggested_wait,
            )
            _logger.warning(
                _EVT_ERROR_CLASSIFIED,
                category=result.category.value,
                error_code=result.error_code.value,
                exit_code=exit_code,
                retriable=result.retriable,
                suggested_wait=result.suggested_wait_seconds,
                message=result.message,
            )
            return result

        if exit_code == 124:
            result = ClassifiedError(
                category=ErrorCategory.TIMEOUT,
                message="Command timed out",
                error_code=ErrorCode.EXECUTION_TIMEOUT,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=60.0,
            )
            _logger.warning(
                _EVT_ERROR_CLASSIFIED,
                category=result.category.value,
                error_code=result.error_code.value,
                exit_code=exit_code,
                retriable=result.retriable,
                suggested_wait=result.suggested_wait_seconds,
                message=result.message,
            )
            return result

        if exit_code == 127:
            result = ClassifiedError(
                category=ErrorCategory.FATAL,
                message=f"Command not found (exit_code={exit_code})",
                error_code=ErrorCode.BACKEND_NOT_FOUND,
                original_error=exception,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=False,
            )
            _logger.warning(
                _EVT_ERROR_CLASSIFIED,
                category=result.category.value,
                error_code=result.error_code.value,
                exit_code=exit_code,
                retriable=result.retriable,
                message=result.message,
            )
            return result

        return None

    def _classify_signal(
        self,
        exit_signal: int,
        exit_reason: ExitReason | None,
        exception: Exception | None,
        stdout: str,
        stderr: str,
    ) -> ClassifiedError:
        """Classify an error when process was killed by a signal.

        Different signals have different retry behaviors:
        - SIGTERM, SIGHUP, SIGPIPE: Likely retriable (graceful termination, hangup)
        - SIGKILL: Could be OOM killer or timeout - check exit_reason
        - SIGSEGV, SIGBUS, SIGABRT: Fatal crashes, not retriable

        Returns ClassifiedError with appropriate error_code from the E0xx execution range.
        """
        signal_name = get_signal_name(exit_signal)

        # Timeout-induced kills are retriable
        if exit_reason == "timeout":
            return ClassifiedError(
                category=ErrorCategory.TIMEOUT,
                message=f"Process killed by {signal_name} due to timeout",
                error_code=ErrorCode.EXECUTION_TIMEOUT,
                original_error=exception,
                exit_code=None,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=60.0,
            )

        # Fatal signals indicate program crashes
        if exit_signal in FATAL_SIGNALS:
            return ClassifiedError(
                category=ErrorCategory.FATAL,
                message=f"Process crashed with {signal_name}",
                error_code=ErrorCode.EXECUTION_CRASHED,
                original_error=exception,
                exit_code=None,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                retriable=False,
            )

        # SIGKILL without timeout context is suspicious - could be OOM
        if exit_signal == signal.SIGKILL:
            # Check for OOM indicators in output
            # Be careful: "killed" alone is too generic, look for OOM-specific patterns
            combined = f"{stdout}\n{stderr}".lower()
            oom_indicators = [
                "oom",
                "out of memory",
                "oom-killer",
                "cannot allocate memory",
                "memory cgroup",
            ]
            if any(indicator in combined for indicator in oom_indicators):
                return ClassifiedError(
                    category=ErrorCategory.FATAL,
                    message=f"Process killed by {signal_name} (possible OOM)",
                    error_code=ErrorCode.EXECUTION_OOM,
                    original_error=exception,
                    exit_code=None,
                    exit_signal=exit_signal,
                    exit_reason=exit_reason,
                    retriable=False,  # OOM will likely recur
                )
            # SIGKILL from unknown source - try once more
            return ClassifiedError(
                category=ErrorCategory.SIGNAL,
                message=f"Process killed by {signal_name}",
                error_code=ErrorCode.EXECUTION_KILLED,
                original_error=exception,
                exit_code=None,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=30.0,
            )

        # Retriable signals
        if exit_signal in RETRIABLE_SIGNALS:
            return ClassifiedError(
                category=ErrorCategory.SIGNAL,
                message=f"Process terminated by {signal_name}",
                error_code=ErrorCode.EXECUTION_KILLED,
                original_error=exception,
                exit_code=None,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=10.0,
            )

        # SIGINT - user interrupt, not retriable
        if exit_signal == signal.SIGINT:
            return ClassifiedError(
                category=ErrorCategory.FATAL,
                message=f"Process interrupted by {signal_name}",
                error_code=ErrorCode.EXECUTION_INTERRUPTED,
                original_error=exception,
                exit_code=None,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                retriable=False,
            )

        # Unknown signal - be conservative, allow one retry
        return ClassifiedError(
            category=ErrorCategory.SIGNAL,
            message=f"Process killed by {signal_name}",
            error_code=ErrorCode.EXECUTION_KILLED,
            original_error=exception,
            exit_code=None,
            exit_signal=exit_signal,
            exit_reason=exit_reason,
            retriable=True,
            suggested_wait_seconds=30.0,
        )

    def _matches_any(self, text: str, patterns: list[re.Pattern[str]]) -> bool:
        """Check if text matches any of the patterns.

        Uses pre-compiled combined regex patterns (built in __init__) so that
        each category check is a single .search() call instead of iterating N
        patterns.
        """
        key = id(patterns)
        combined_pattern = self._combined_cache.get(key)
        if combined_pattern is None:
            alternation = "|".join(f"(?:{p.pattern})" for p in patterns)
            combined_pattern = re.compile(alternation, re.IGNORECASE)
            self._combined_cache[key] = combined_pattern
        return combined_pattern.search(text) is not None

    def classify_execution(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        exit_signal: int | None = None,
        exit_reason: ExitReason | None = None,
        exception: Exception | None = None,
        output_format: str | None = None,
        *,
        input: ClassificationInput | None = None,
    ) -> ClassificationResult:
        """Classify execution errors using structured JSON parsing with fallback.

        This is the new multi-error classification method that:
        1. Parses structured JSON errors[] from CLI output (if present)
        2. Classifies each error independently (no short-circuiting)
        3. Analyzes exit code and signal for additional context
        4. Selects root cause using priority-based scoring
        5. Returns all errors with primary/secondary designation

        This method returns ClassificationResult which provides access to
        all detected errors while maintaining backward compatibility through
        the `primary` attribute.

        Supports two calling conventions:
            1. **Keyword args** (legacy): ``classify_execution(stdout=..., stderr=..., ...)``
            2. **Bundled** (preferred): ``classify_execution(input=ClassificationInput(...))``

        When *input* is supplied, its fields take precedence over
        individual keyword arguments.

        Args:
            stdout: Standard output from the command (may contain JSON).
            stderr: Standard error from the command.
            exit_code: Process exit code (0 = success), None if killed by signal.
            exit_signal: Signal number if killed by signal.
            exit_reason: Why execution ended (completed, timeout, killed, error).
            exception: Optional exception that was raised.
            output_format: Expected output format (e.g. "json").
            input: Bundled classification input (preferred over individual kwargs).

        Returns:
            ClassificationResult with primary error, secondary errors, and metadata.

        Example:
            ```python
            result = classifier.classify_execution(stdout, stderr, exit_code)

            # Access primary (root cause) error
            if result.primary.category == ErrorCategory.RATE_LIMIT:
                wait_time = result.primary.suggested_wait_seconds

            # Access all errors for debugging
            for error in result.all_errors:
                logger.info(f"{error.error_code.value}: {error.message}")
            ```
        """
        if input is not None:
            stdout = input.stdout
            stderr = input.stderr
            exit_code = input.exit_code
            exit_signal = input.exit_signal
            exit_reason = input.exit_reason
            exception = input.exception
            output_format = input.output_format
        all_errors: list[ClassifiedError] = []
        raw_errors: list[ParsedCliError] = []
        classification_method = "structured"

        # === PHASE 1: Parse Structured JSON ===
        # Pass both stdout and stderr - errors can appear in either stream
        json_errors = try_parse_json_errors(stdout, stderr)
        raw_errors = json_errors

        if json_errors:
            for parsed_error in json_errors:
                classified = classify_single_json_error(
                    parsed_error,
                    exit_code=exit_code,
                    exit_reason=exit_reason,
                )
                all_errors.append(classified)

        # === PHASE 2: Exit Code / Signal Analysis ===
        if exit_signal is not None:
            signal_error = self._classify_signal(
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                exception=exception,
                stdout=stdout,
                stderr=stderr,
            )
            # Only add if not duplicating an existing error code
            if not any(e.error_code == signal_error.error_code for e in all_errors):
                all_errors.append(signal_error)
                if not json_errors:
                    classification_method = "exit_code"

        elif exit_reason == "timeout":
            # Differentiate stale detection (E006) from backend timeout (E001).
            # Stale detection writes "Stale execution:" to stderr — F-097.
            combined_for_stale = f"{stdout}\n{stderr}".lower()
            is_stale = "stale execution" in combined_for_stale
            timeout_code = (
                ErrorCode.EXECUTION_STALE if is_stale
                else ErrorCode.EXECUTION_TIMEOUT
            )
            timeout_message = (
                "Stale execution detected — no output activity"
                if is_stale
                else "Command timed out"
            )
            timeout_wait = 120.0 if is_stale else 60.0
            timeout_error = ClassifiedError(
                category=ErrorCategory.TIMEOUT,
                message=timeout_message,
                error_code=timeout_code,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=timeout_wait,
            )
            if not any(e.error_code == timeout_code for e in all_errors):
                all_errors.append(timeout_error)
                if not json_errors:
                    classification_method = "exit_code"

        elif exit_code is None and json_errors:
            # Process killed or disappeared without exit code, AND Phase 1
            # found JSON errors from partial output. Add a process-killed
            # error so select_root_cause can weigh it against JSON errors.
            # Without this, exit_code=None context is lost when Phase 1
            # finds errors (Phase 4 regex fallback is skipped).
            # When no JSON errors exist, Phase 4 calls classify() which
            # already handles exit_code=None correctly.
            stderr_lower = stderr.lower() if stderr else ""
            oom_indicators = ("killed", "out of memory", "oom", "cannot allocate")
            is_oom = any(indicator in stderr_lower for indicator in oom_indicators)
            wait_seconds = 60.0 if is_oom else 10.0
            message = (
                "Process killed (possible OOM — retrying with longer wait)"
                if is_oom
                else "Process exited without exit code "
                "(possible signal race — retrying)"
            )
            process_killed_error = ClassifiedError(
                category=ErrorCategory.TRANSIENT,
                message=message,
                error_code=ErrorCode.UNKNOWN,
                original_error=exception,
                exit_code=exit_code,
                exit_signal=None,
                exit_reason=exit_reason,
                retriable=True,
                suggested_wait_seconds=wait_seconds,
            )
            all_errors.append(process_killed_error)

        # === PHASE 3: Exception Analysis ===
        if exception is not None:
            exc_str = str(exception).lower()
            # Try to classify based on exception message
            if "timeout" in exc_str:
                exc_error = ClassifiedError(
                    category=ErrorCategory.TIMEOUT,
                    message=str(exception),
                    error_code=ErrorCode.EXECUTION_TIMEOUT,
                    original_error=exception,
                    exit_code=exit_code,
                    exit_reason=exit_reason,
                    retriable=True,
                    suggested_wait_seconds=60.0,
                )
            elif "connection" in exc_str or "network" in exc_str:
                exc_error = ClassifiedError(
                    category=ErrorCategory.NETWORK,
                    message=str(exception),
                    error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
                    original_error=exception,
                    exit_code=exit_code,
                    exit_reason=exit_reason,
                    retriable=True,
                    suggested_wait_seconds=30.0,
                )
            else:
                exc_error = ClassifiedError(
                    category=ErrorCategory.TRANSIENT,
                    message=str(exception),
                    error_code=ErrorCode.UNKNOWN,
                    original_error=exception,
                    exit_code=exit_code,
                    exit_reason=exit_reason,
                    retriable=True,
                    suggested_wait_seconds=30.0,
                )
            # Only add if we don't have the same error code already
            if not any(e.error_code == exc_error.error_code for e in all_errors):
                all_errors.append(exc_error)

        # === PHASE 4: Regex Fallback (only if no structured errors) ===
        if not all_errors:
            classification_method = "regex_fallback"
            fallback_error = self.classify(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                exception=exception,
                output_format=output_format,
            )
            all_errors.append(fallback_error)

        # === PHASE 4.5: Rate Limit Override (always runs) ===
        # Rate limits in stdout/stderr must never be missed, even when Phase 1
        # found structured JSON errors that masked them. F-098: Claude CLI rate
        # limit messages appear in stdout but Phase 1 may produce generic errors
        # that prevent Phase 4 from firing.
        has_rate_limit_error = any(
            e.category == ErrorCategory.RATE_LIMIT for e in all_errors
        )
        if not has_rate_limit_error:
            combined_for_rate_limit = f"{stdout}\n{stderr}"
            if self._matches_any(combined_for_rate_limit, self.rate_limit_patterns):
                # Check for quota exhaustion (more specific) first
                if self._matches_any(
                    combined_for_rate_limit, self.quota_exhaustion_patterns
                ):
                    wait_seconds = (
                        self.parse_reset_time(combined_for_rate_limit)
                        or DEFAULT_QUOTA_WAIT_SECONDS
                    )
                    rate_limit_error = ClassifiedError(
                        category=ErrorCategory.RATE_LIMIT,
                        message="Token quota exhausted — detected in output",
                        error_code=ErrorCode.QUOTA_EXHAUSTED,
                        original_error=exception,
                        exit_code=exit_code,
                        exit_signal=None,
                        exit_reason=exit_reason,
                        retriable=True,
                        suggested_wait_seconds=wait_seconds,
                    )
                else:
                    error_code = (
                        ErrorCode.CAPACITY_EXCEEDED
                        if self._matches_any(
                            combined_for_rate_limit, self.capacity_patterns
                        )
                        else ErrorCode.RATE_LIMIT_API
                    )
                    rate_limit_error = ClassifiedError(
                        category=ErrorCategory.RATE_LIMIT,
                        message="Rate limit detected in output",
                        error_code=error_code,
                        original_error=exception,
                        exit_code=exit_code,
                        exit_signal=None,
                        exit_reason=exit_reason,
                        retriable=True,
                        suggested_wait_seconds=DEFAULT_RATE_LIMIT_WAIT_SECONDS,
                    )
                all_errors.append(rate_limit_error)
                _logger.warning(
                    "rate_limit_override",
                    component="errors",
                    error_code=rate_limit_error.error_code.value,
                    message="Rate limit detected via Phase 4.5 override",
                )

        # === PHASE 5: Root Cause Selection ===
        root_cause, symptoms, confidence = select_root_cause(all_errors)

        # Log the classification result
        _logger.info(
            "execution_classified",
            method=classification_method,
            primary_code=root_cause.error_code.value,
            error_count=len(all_errors),
            confidence=confidence,
            all_codes=[e.error_code.value for e in all_errors],
        )

        return ClassificationResult(
            primary=root_cause,
            secondary=symptoms,
            raw_errors=raw_errors,
            confidence=confidence,
            classification_method=classification_method,
        )

    @classmethod
    def from_config(cls, rate_limit_patterns: list[str]) -> ErrorClassifier:
        """Create classifier from config patterns."""
        return cls(rate_limit_patterns=rate_limit_patterns)
