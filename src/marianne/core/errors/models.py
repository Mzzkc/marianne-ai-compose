"""Data models for error classification.

Contains the dataclass models used throughout the error handling system.

This module provides:
- ParsedCliError: Structured error from CLI JSON output
- ErrorInfo: Machine-readable error metadata (Google AIP-193 inspired)
- ClassifiedError: Single error with classification and metadata
- ErrorChain: Error chain from symptom to root cause
- ClassificationResult: Multi-error result with root cause identification
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .codes import ErrorCategory, ErrorCode, ExitReason, Severity

if TYPE_CHECKING:
    from typing import Any



def _get_signal_name(sig_num: int) -> str:
    """Get human-readable signal name.

    Note: This is a local helper to avoid circular imports.
    The canonical version is in signals.py.
    """
    import signal

    try:
        return signal.Signals(sig_num).name
    except ValueError:
        return f"signal {sig_num}"


@dataclass
class ClassificationInput:
    """Bundled inputs for ``ErrorClassifier.classify_execution()``.

    Groups the execution result fields that the classifier needs, reducing the
    method's parameter count from 8 to 2 (``self`` + ``input``).  Callers can
    still pass individual keyword arguments for backward compatibility.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    exit_signal: int | None = None
    exit_reason: ExitReason | None = None
    exception: Exception | None = None
    output_format: str | None = None


@dataclass
class ParsedCliError:
    """A single error extracted from CLI JSON output.

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

    This dataclass represents one item from that array.

    Attributes:
        error_type: Error type from CLI: "system", "user", "tool".
        message: Human-readable error message.
        tool_name: For tool errors, the name of the failed tool.
        metadata: Additional structured metadata from the error.
    """

    error_type: Literal["system", "user", "tool"]
    """Error type from CLI: "system", "user", "tool"."""

    message: str
    """Human-readable error message."""

    tool_name: str | None = None
    """For tool errors, the name of the failed tool."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional structured metadata."""


@dataclass
class ErrorInfo:
    """Machine-readable error identification (Google AIP-193 inspired).

    Provides structured metadata for programmatic error handling.

    Example:
    ```python
    error_info = ErrorInfo(
        reason="BINARY_NOT_FOUND",
        domain="marianne.instrument.cli",
        metadata={
            "expected_binary": "claude",
            "search_path": "/usr/bin:/usr/local/bin",
            "suggestion": "Ensure claude CLI is installed and in PATH",
        }
    )
    ```

    Attributes:
        reason: UPPER_SNAKE_CASE identifier for the specific error reason.
        domain: Service/component identifier (e.g., "marianne.instrument.cli").
        metadata: Dynamic contextual information as key-value pairs.
    """

    reason: str
    """UPPER_SNAKE_CASE identifier for the specific error reason.
    Example: "RATE_LIMIT_EXCEEDED", "BINARY_NOT_FOUND"
    """

    domain: str
    """Service/component identifier.
    Example: "marianne.instrument.cli", "marianne.execution"
    """

    metadata: dict[str, str] = field(default_factory=dict)
    """Dynamic contextual information.
    Example: {"binary_path": "/usr/bin/claude", "exit_code": "127"}
    """


@dataclass
class ClassifiedError:
    """An error with its classification and metadata.

    ClassifiedError combines high-level category (for retry logic) with
    specific error codes (for diagnostics and logging). The error_code
    provides stable identifiers for programmatic handling while category
    determines retry behavior.
    """

    category: ErrorCategory
    message: str
    error_code: ErrorCode = field(default=ErrorCode.UNKNOWN)
    original_error: Exception | None = None
    exit_code: int | None = None
    exit_signal: int | None = None
    exit_reason: ExitReason | None = None
    retriable: bool = True
    suggested_wait_seconds: float | None = None
    error_info: ErrorInfo | None = None
    """Optional structured metadata for this error."""

    @property
    def is_rate_limit(self) -> bool:
        return self.category == ErrorCategory.RATE_LIMIT

    @property
    def is_signal_kill(self) -> bool:
        """True if process was killed by a signal."""
        return self.exit_signal is not None

    @property
    def signal_name(self) -> str | None:
        """Human-readable signal name if killed by signal."""
        if self.exit_signal is None:
            return None
        return _get_signal_name(self.exit_signal)

    @property
    def should_retry(self) -> bool:
        return self.retriable and self.category not in (ErrorCategory.AUTH, ErrorCategory.FATAL)

    @property
    def code(self) -> str:
        """Get the error code string value (e.g., 'E001')."""
        return self.error_code.value

    @property
    def severity(self) -> Severity:
        """Get the severity level for this error."""
        return self.error_code.get_severity()


@dataclass
class ErrorChain:
    """Represents a chain of errors from symptom to root cause.

    When multiple errors occur, this class helps identify the actual
    root cause vs symptoms. For example, if ENOENT and rate limit both
    appear, ENOENT is likely the root cause (missing binary prevents
    any operation).

    Attributes:
        errors: All errors in order of detection (first = earliest).
        root_cause: The error identified as the most fundamental cause.
        symptoms: Errors that are likely consequences of the root cause.
        confidence: 0.0-1.0 confidence in root cause identification.
    """

    errors: list[ClassifiedError]
    """All errors in order of detection (first = earliest)."""

    root_cause: ClassifiedError
    """The error identified as the most fundamental cause."""

    symptoms: list[ClassifiedError] = field(default_factory=list)
    """Errors that are likely consequences of the root cause."""

    confidence: float = 1.0
    """0.0-1.0 confidence in root cause identification."""

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass
class ClassificationResult:
    """Complete classification result with root cause and context.

    This is the new result type from the classifier, providing access
    to all detected errors while maintaining backward compatibility
    through the `primary` attribute.

    Example:
    ```python
    # New code - returns ClassificationResult
    classification = classifier.classify(stdout, stderr, exit_code)
    result = classification.primary  # Backward compatible

    # Access all errors
    for error in classification.all_errors:
        log.info(f"Error: {error.error_code.value} - {error.message}")
    ```

    Attributes:
        primary: The identified root cause error.
        secondary: Secondary/symptom errors for debugging.
        raw_errors: Original parsed errors from CLI JSON.
        confidence: 0.0-1.0 confidence in root cause identification.
        classification_method: How classification was done.
    """

    primary: ClassifiedError
    """The identified root cause error."""

    secondary: list[ClassifiedError] = field(default_factory=list)
    """Secondary/symptom errors for debugging."""

    raw_errors: list[ParsedCliError] = field(default_factory=list)
    """Original parsed errors from CLI JSON."""

    confidence: float = 1.0
    """0.0-1.0 confidence in root cause identification."""

    classification_method: str = "structured"
    """How classification was done: "structured", "exit_code", "regex_fallback"."""

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))

    @property
    def all_errors(self) -> list[ClassifiedError]:
        """All errors including primary and secondary."""
        return [self.primary] + self.secondary

    @property
    def error_codes(self) -> list[str]:
        """All error codes for logging/metrics."""
        return [e.error_code.value for e in self.all_errors]

    @property
    def category(self) -> ErrorCategory:
        """Category of the primary error (backward compatibility)."""
        return self.primary.category

    @property
    def message(self) -> str:
        """Message of the primary error (backward compatibility)."""
        return self.primary.message

    @property
    def error_code(self) -> ErrorCode:
        """Error code of the primary error (backward compatibility)."""
        return self.primary.error_code

    @property
    def retriable(self) -> bool:
        """Whether the primary error is retriable (backward compatibility)."""
        return self.primary.retriable

    @property
    def should_retry(self) -> bool:
        """Whether to retry based on primary error (backward compatibility)."""
        return self.primary.should_retry

    def to_error_chain(self) -> ErrorChain:
        """Convert to ErrorChain for detailed analysis."""
        return ErrorChain(
            errors=self.all_errors,
            root_cause=self.primary,
            symptoms=self.secondary,
            confidence=self.confidence,
        )
