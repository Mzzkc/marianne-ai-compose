"""Checkpoint and state management models.

Defines the state that gets persisted between runs for resumable orchestration.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Required, TypedDict

from pydantic import BaseModel, Field, model_validator

from marianne.core.errors.codes import ErrorCategory, ExitReason
from marianne.core.logging import get_logger
from marianne.utils.time import utc_now

# Literal variant to avoid shadowing the IsolationMode enum in core.config.workspace.
IsolationModeLiteral = Literal["worktree", "none"]

# Module-level logger for checkpoint operations
_logger = get_logger("checkpoint")

# Constants for output capture
MAX_OUTPUT_CAPTURE_BYTES: int = 51200  # 50KB - last N bytes of stdout/stderr to capture

# Constants for error history (Task 10: Error History Model)
MAX_ERROR_HISTORY: int = 50  # Maximum number of error records to keep per sheet
MAX_INSTRUMENT_FALLBACK_HISTORY: int = 50  # Maximum fallback records per sheet

# Type alias for error types
ErrorType = Literal["transient", "rate_limit", "permanent"]

# --- TypedDict definitions for structured state fields ---


class ValidationDetailDict(TypedDict, total=False):
    """Schema for individual validation result entries in SheetState.validation_details.

    Most keys are optional (total=False) to support partial dicts from
    legacy data and simplified test fixtures.  ``passed`` is required
    because a validation result without a pass/fail status is meaningless.
    """

    rule_type: str
    description: str | None
    path: str | None
    pattern: str | None
    passed: Required[bool]
    actual_value: str | None
    expected_value: str | None
    error_message: str | None
    checked_at: str  # ISO format datetime
    check_duration_ms: float
    confidence: float
    confidence_factors: dict[str, float]
    failure_reason: str | None
    failure_category: str | None
    suggested_fix: str | None
    error_type: str | None


class PromptMetricsDict(TypedDict, total=False):
    """Schema for prompt analysis metrics in SheetState.prompt_metrics.

    All keys are optional (total=False) to support partial metrics from
    legacy data or simplified test fixtures.
    """

    character_count: int
    estimated_tokens: int
    line_count: int
    word_count: int
    has_file_references: bool
    referenced_paths: list[str]


class ProgressSnapshotDict(TypedDict, total=False):
    """Schema for execution progress snapshots in SheetState.progress_snapshots.

    All keys are optional (total=False) because snapshots may contain varying
    subsets depending on when they were captured.
    """

    sheet_num: int
    bytes_received: int
    lines_received: int
    elapsed_seconds: float
    phase: str  # "starting", "executing", "completed"
    snapshot_at: str  # ISO format datetime


class ErrorContextDict(TypedDict, total=False):
    """Schema for error context in CheckpointErrorRecord.context.

    All keys optional since context varies by error type.
    Values may be None when the information is not available.
    """

    exit_code: int | None
    signal: int | None
    category: str | None


class AppliedPatternDict(TypedDict):
    """Schema for a single applied pattern in SheetState.applied_patterns.

    Replaces the parallel ``applied_pattern_ids`` / ``applied_pattern_descriptions``
    lists with a single structured list for safety and clarity.
    """

    id: str
    description: str


class OutcomeDataDict(TypedDict, total=False):
    """Schema for structured outcome data in SheetState.outcome_data.

    All keys optional since this is extensible for learning/pattern recognition.
    """

    escalation_record_id: str
    escalation_skipped: bool


class SynthesisResultDict(TypedDict, total=False):
    """Schema for synthesis result entries in CheckpointState.synthesis_results.

    All keys are optional (total=False) to support partial data from
    legacy state files and test fixtures.
    """

    batch_id: str
    sheets: list[int]
    strategy: Literal["merge", "summarize", "pass_through"]
    status: Literal["pending", "ready", "done", "failed"]
    created_at: str | None  # ISO format datetime, or None if not set
    completed_at: str | None
    sheet_outputs: dict[int, str]
    synthesized_content: str | None
    error_message: str | None
    metadata: dict[str, Any]
    conflict_detection: dict[str, Any] | None


class SheetStatus(str, Enum):
    """Status of a single sheet.

    The baton tracks 11 scheduling states. Status display and persistence
    use all 11. Consumers that only care about terminal/non-terminal can
    check ``is_terminal``.
    """

    PENDING = "pending"
    READY = "ready"              # Dependencies met, eligible for dispatch
    DISPATCHED = "dispatched"    # Sent to musician, not yet running
    IN_PROGRESS = "in_progress"  # Musician actively executing (alias: RUNNING)
    WAITING = "waiting"          # Blocked on rate-limited instrument
    RETRY_SCHEDULED = "retry_scheduled"  # Failed, awaiting retry timer
    FERMATA = "fermata"          # Paused for escalation decision
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Whether this status represents a final state."""
        return self in _TERMINAL_SHEET_STATUSES

    @property
    def is_active(self) -> bool:
        """Whether this status means the sheet is currently executing."""
        return self in (SheetStatus.DISPATCHED, SheetStatus.IN_PROGRESS)


_TERMINAL_SHEET_STATUSES = frozenset({
    SheetStatus.COMPLETED,
    SheetStatus.FAILED,
    SheetStatus.SKIPPED,
    SheetStatus.CANCELLED,
})


class OutcomeCategory(str, Enum):
    """Classification of sheet execution outcome (#7)."""

    SUCCESS_FIRST_TRY = "success_first_try"
    SUCCESS_RETRY = "success_retry"
    SUCCESS_COMPLETION = "success_completion"
    FAILED_EXHAUSTED = "failed_exhausted"
    FAILED_FATAL = "failed_fatal"
    SKIPPED_BY_ESCALATION = "skipped_by_escalation"


class JobStatus(str, Enum):
    """Status of an entire job run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class CheckpointErrorRecord(BaseModel):
    """Record of a single error occurrence during sheet execution.

    Stores structured error information for debugging and pattern analysis.
    Error history is trimmed to MAX_ERROR_HISTORY records per sheet to
    prevent unbounded state growth.
    """

    timestamp: datetime = Field(
        default_factory=utc_now,
        description="When the error occurred (UTC)",
    )
    error_type: ErrorType = Field(
        description="Error classification: transient, rate_limit, or permanent",
    )
    error_code: str = Field(
        description="Error code for categorization (e.g., E001, E002)",
    )
    error_message: str = Field(
        description="Human-readable error description",
    )
    attempt_number: int = Field(
        ge=1,
        description="Which attempt this error occurred on (1-based)",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context — see ErrorContextDict for common keys "
        "(exit_code: int, signal: int, category: str). Accepts arbitrary values "
        "for extensibility.",
    )
    stdout_tail: str | None = Field(
        default=None,
        description="Last portion of stdout when error occurred",
    )
    stderr_tail: str | None = Field(
        default=None,
        description="Last portion of stderr when error occurred",
    )
    stack_trace: str | None = Field(
        default=None,
        description="Stack trace if exception was caught",
    )


# Backward compatibility alias (renamed from ErrorRecord to CheckpointErrorRecord)
ErrorRecord = CheckpointErrorRecord


class SheetState(BaseModel):
    """State for a single sheet."""

    sheet_num: int = Field(ge=1)
    status: SheetStatus = SheetStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int = 0
    exit_code: int | None = None
    error_message: str | None = None
    error_category: ErrorCategory | None = None
    error_code: str | None = Field(
        default=None,
        description="Structured error code from ErrorClassifier (e.g., 'E001', 'E006'). "
        "More specific than error_category — distinguishes stale (E006) from timeout (E001).",
    )
    validation_passed: bool | None = None
    validation_details: list[ValidationDetailDict] | None = None

    # Sheet-first architecture identity (movement 1: sheet entity model)
    instrument_name: str | None = Field(
        default=None,
        description="Instrument used for this sheet, e.g. 'claude-code', 'gemini-cli'. "
        "Populated at execution time from the resolved Sheet entity.",
    )
    instrument_model: str | None = Field(
        default=None,
        description="Model used by the instrument, e.g. 'gemini-2.5-pro'. "
        "Populated at execution time from backend metadata.",
    )
    movement: int | None = Field(
        default=None,
        description="Movement number this sheet belongs to (was: stage). "
        "Populated at execution time from the Sheet entity.",
    )
    voice: int | None = Field(
        default=None,
        description="Voice number within a harmonized movement (was: instance). "
        "None for solo movements. Populated from Sheet entity.",
    )

    # Exit signal differentiation (Task 3: Exit Signal Differentiation)
    exit_signal: int | None = Field(
        default=None,
        description="Signal number if process was killed (e.g., 9=SIGKILL, 15=SIGTERM)",
    )
    exit_reason: ExitReason | None = Field(
        default=None,
        description="Why execution ended: completed, timeout, killed, or error",
    )
    execution_duration_seconds: float | None = Field(
        default=None,
        description="How long the sheet execution took in seconds",
    )

    # Partial completion tracking
    completion_attempts: int = Field(
        default=0,
        description="Number of completion prompt attempts for partial recovery",
    )
    passed_validations: list[str] = Field(
        default_factory=list,
        description="Descriptions of validations that passed",
    )
    failed_validations: list[str] = Field(
        default_factory=list,
        description="Descriptions of validations that failed",
    )
    last_pass_percentage: float | None = Field(
        default=None,
        description="Last validation pass percentage",
    )
    execution_mode: Literal["normal", "completion", "retry"] | None = Field(
        default=None,
        description="Last execution mode: normal, completion, or retry",
    )

    # Learning metadata (Phase 1: Learning Foundation)
    outcome_data: OutcomeDataDict | None = Field(
        default=None,
        description="Structured outcome data for learning and pattern recognition",
    )
    confidence_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Aggregate confidence in outcome quality (0.0-1.0)",
    )
    learned_patterns: list[str] = Field(
        default_factory=list,
        description="Patterns recognized from this sheet execution",
    )
    similar_outcomes_count: int = Field(
        default=0,
        description="Deprecated: always 0. Retained for SQLite schema compatibility.",
    )
    success_without_retry: bool = Field(
        default=False,
        description="Whether sheet succeeded without needing any retry or completion mode",
    )
    outcome_category: OutcomeCategory | None = Field(
        default=None,
        description="Outcome classification using OutcomeCategory enum (Q015/#37).",
    )

    # Raw output capture for debugging (last N bytes to avoid memory issues)
    stdout_tail: str | None = Field(
        default=None,
        description="Last 10KB of stdout for debugging failed executions",
    )
    stderr_tail: str | None = Field(
        default=None,
        description="Last 10KB of stderr for debugging failed executions",
    )
    output_truncated: bool = Field(
        default=False,
        description="True if output was larger than capture limit and was truncated",
    )

    # Preflight metrics (Task 2: Prompt Metrics and Pre-flight Checks)
    prompt_metrics: PromptMetricsDict | None = Field(
        default=None,
        description="Prompt analysis metrics (character_count, estimated_tokens, etc.)",
    )
    preflight_warnings: list[str] = Field(
        default_factory=list,
        description="Warnings from preflight checks (large prompts, missing files, etc.)",
    )

    # Execution progress tracking (Task 4: Execution Progress Tracking)
    progress_snapshots: list[ProgressSnapshotDict] = Field(
        default_factory=list,
        description="Periodic progress records during execution (bytes, lines, phase)",
    )
    last_activity_at: datetime | None = Field(
        default=None,
        description="Last time activity was observed during execution",
    )

    # Error history tracking (Task 10: Error History Model)
    error_history: list[CheckpointErrorRecord] = Field(
        default_factory=list,
        description="History of errors encountered during sheet execution (max 10)",
    )

    # Instrument fallback history (M5: per-sheet instrument fallbacks)
    instrument_fallback_history: list[dict[str, str]] = Field(
        default_factory=list,
        description="Records each instrument fallback event: "
        "{from: str, to: str, reason: str, timestamp: str}. "
        "Persisted for resume support and post-execution analysis.",
    )

    # Scheduling fields (Phase 3: baton scheduling state, persisted for
    # crash recovery and observability — users see rate_limited/retry_scheduled
    # instead of just "in_progress")
    fire_at: datetime | None = Field(
        default=None,
        description="When this sheet should next be dispatched. None = immediately "
        "eligible. Used for retry backoff, rate limit recovery, and cron-style "
        "scheduled execution. The baton dispatches when fire_at <= now.",
    )
    rate_limit_expires_at: datetime | None = Field(
        default=None,
        description="When the rate limit blocking this sheet is expected to clear. "
        "Shown in status display so operators know when execution will resume.",
    )
    healing_attempts: int = Field(
        default=0,
        description="Number of self-healing attempts for this sheet.",
    )

    # --- Baton scheduling fields (Phase 2: unified state model) ---
    # These fields were previously on SheetExecutionState (baton/state.py).
    # Moved here so the baton writes to SheetState directly — no sync layer.

    normal_attempts: int = Field(
        default=0,
        description="Number of failed execution attempts (excludes rate-limited and "
        "successful attempts). This is the retry budget counter.",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum normal retry attempts before exhaustion.",
    )
    max_completion: int = Field(
        default=5,
        description="Maximum completion mode attempts.",
    )
    total_cost_usd: float = Field(
        default=0.0,
        description="Cumulative cost across all attempts for this sheet.",
    )
    total_duration_seconds: float = Field(
        default=0.0,
        description="Cumulative execution duration across all attempts.",
    )
    next_retry_at: float | None = Field(
        default=None,
        exclude=True,
        description="Monotonic time when next retry is scheduled. "
        "Transient — not persisted (timers are lost on restart).",
    )
    dispatched_at: float | None = Field(
        default=None,
        exclude=True,
        description="Monotonic time when the sheet was last dispatched. "
        "Transient — used by StaleCheck for hung sheet detection.",
    )
    model: str | None = Field(
        default=None,
        description="Model used by this sheet's instrument. For per-model "
        "concurrency tracking.",
    )

    # Instrument fallback fields
    fallback_chain: list[str] = Field(
        default_factory=list,
        description="Resolved fallback instrument chain. Empty = no fallbacks.",
    )
    current_instrument_index: int = Field(
        default=0,
        description="Position in the instrument chain. 0 = primary instrument.",
    )
    fallback_attempts: dict[str, int] = Field(
        default_factory=dict,
        description="Retry count per instrument in the chain.",
    )

    # Per-sheet timeout for stale detection
    sheet_timeout_seconds: float = Field(
        default=1800.0,
        description="Per-sheet execution timeout in seconds. Used by "
        "StaleCheck to detect hung sheets. Populated from Sheet entity "
        "at registration time.",
    )

    # Transient — not persisted (populated during execution, lost on restart)
    attempt_results: list[Any] = Field(
        default_factory=list,
        exclude=True,
        description="Full SheetAttemptResult for each attempt. Transient — "
        "enables the conductor's retry/completion/healing decisions.",
    )

    # Display status — derived from status + scheduling fields for human display.
    # Computed at serialization time, not stored. Allows status display to show
    # "retry in 45s" or "rate limited (clears in 12m)" without consumers
    # needing to interpret the raw enum + scheduling fields.
    display_status: str | None = Field(
        default=None,
        description="Human-readable status string derived from scheduling state. "
        "Computed by the status renderer, not authoritative.",
    )

    # Pattern feedback loop tracking (v9 evolution: Pattern Feedback Loop Closure)
    applied_patterns: list[AppliedPatternDict] = Field(
        default_factory=list,
        description="Patterns applied/injected for this sheet execution (structured list).",
    )

    @property
    def applied_pattern_ids(self) -> list[str]:
        """Backward-compatible accessor for pattern IDs."""
        return [p["id"] for p in self.applied_patterns]

    @applied_pattern_ids.setter
    def applied_pattern_ids(self, value: list[str]) -> None:
        """Backward-compatible setter: rebuilds applied_patterns from IDs.

        Preserves existing descriptions where indices match.
        """
        existing_descs = [p["description"] for p in self.applied_patterns]
        self.applied_patterns = [
            AppliedPatternDict(
                id=pid,
                description=existing_descs[i] if i < len(existing_descs) else "",
            )
            for i, pid in enumerate(value)
        ]

    @property
    def applied_pattern_descriptions(self) -> list[str]:
        """Backward-compatible accessor for pattern descriptions."""
        return [p["description"] for p in self.applied_patterns]

    @applied_pattern_descriptions.setter
    def applied_pattern_descriptions(self, value: list[str]) -> None:
        """Backward-compatible setter: rebuilds applied_patterns from descriptions.

        Preserves existing IDs where indices match.
        """
        existing_ids = [p["id"] for p in self.applied_patterns]
        self.applied_patterns = [
            AppliedPatternDict(
                id=existing_ids[i] if i < len(existing_ids) else "",
                description=desc,
            )
            for i, desc in enumerate(value)
        ]

    # Grounding integration (v11 evolution: Grounding→Pattern Integration)
    grounding_passed: bool | None = Field(
        default=None,
        description="Whether all grounding hooks passed (None if not enabled)",
    )
    grounding_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Average confidence across grounding hooks (0.0-1.0)",
    )
    grounding_guidance: str | None = Field(
        default=None,
        description="Recovery guidance from failed grounding hooks",
    )

    # Cost tracking (v4 evolution: Cost Circuit Breaker)
    input_tokens: int | None = Field(
        default=None,
        description="Input tokens consumed by this sheet execution",
    )
    output_tokens: int | None = Field(
        default=None,
        description="Output tokens produced by this sheet execution",
    )
    estimated_cost: float | None = Field(
        default=None,
        description="Estimated cost in USD for this sheet",
    )
    cost_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in cost estimate (1.0=exact, 0.7=estimated from chars)",
    )

    # Developer feedback (GH#15: Developer Feedback Mode)
    agent_feedback: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured feedback from the agent about this sheet execution. "
            "Extracted from agent output via feedback_pattern regex. "
            "Typically includes keys like 'confidence', 'blockers', 'notes'."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_patterns(cls, data: Any) -> Any:
        """Convert legacy parallel lists to structured applied_patterns."""
        if isinstance(data, dict):
            ids = data.pop("applied_pattern_ids", None)
            descs = data.pop("applied_pattern_descriptions", None)
            if (ids or descs) and "applied_patterns" not in data:
                ids = ids or []
                descs = descs or []
                max_len = max(len(ids), len(descs))
                data["applied_patterns"] = [
                    {"id": ids[i] if i < len(ids) else "",
                     "description": descs[i] if i < len(descs) else ""}
                    for i in range(max_len)
                ]
        return data

    @model_validator(mode="after")
    def _enforce_status_invariants(self) -> SheetState:
        """Warn and auto-fill when status-dependent fields are missing.

        Invariants:
        - COMPLETED → completed_at must be set
        - IN_PROGRESS → started_at must be set
        - FAILED → error_message must be set
        """
        if self.status == SheetStatus.COMPLETED and self.completed_at is None:
            _logger.debug(
                "sheet_state.invariant_autofill",
                sheet_num=self.sheet_num,
                field="completed_at",
                status="COMPLETED",
            )
            self.completed_at = utc_now()

        if self.status == SheetStatus.IN_PROGRESS and self.started_at is None:
            _logger.debug(
                "sheet_state.invariant_autofill",
                sheet_num=self.sheet_num,
                field="started_at",
                status="IN_PROGRESS",
            )
            self.started_at = utc_now()

        if self.status == SheetStatus.FAILED and self.error_message is None:
            _logger.debug(
                "sheet_state.invariant_autofill",
                sheet_num=self.sheet_num,
                field="error_message",
                status="FAILED",
            )
            self.error_message = "Unknown failure (no error message recorded)"

        return self

    # --- Baton scheduling methods (Phase 2: unified state model) ---
    # These methods were previously on SheetExecutionState (baton/state.py).
    # Now on SheetState so the baton operates on one shared object.

    def record_attempt(self, result: Any) -> None:
        """Record an attempt result and update tracking state.

        Only non-successful, non-rate-limited attempts increment
        ``normal_attempts``. Successes and rate-limited attempts are
        recorded in ``attempt_results`` for history but don't consume
        retry budget.
        """
        self.attempt_results.append(result)
        self.total_cost_usd += result.cost_usd
        self.total_duration_seconds += result.duration_seconds

        if not result.rate_limited and not result.execution_success:
            self.normal_attempts += 1

    @property
    def can_retry(self) -> bool:
        """Whether this sheet has remaining retry budget."""
        return self.normal_attempts < self.max_retries

    @property
    def can_complete(self) -> bool:
        """Whether this sheet has remaining completion mode budget."""
        return self.completion_attempts < self.max_completion

    @property
    def is_exhausted(self) -> bool:
        """Whether both retry and completion budgets are exhausted."""
        return not self.can_retry and not self.can_complete

    @property
    def total_attempts(self) -> int:
        """Total attempts across all modes."""
        return self.normal_attempts + self.completion_attempts + self.healing_attempts

    @property
    def has_fallback_available(self) -> bool:
        """Whether there is another instrument in the fallback chain to try."""
        return self.current_instrument_index < len(self.fallback_chain)

    def advance_fallback(self, reason: str) -> str | None:
        """Advance to the next instrument in the fallback chain.

        Records the transition, switches instrument_name, resets retry
        budget, and increments current_instrument_index.

        Returns the new instrument name, or None if the chain is exhausted.
        """
        if not self.has_fallback_available:
            return None

        from_instrument = self.instrument_name or ""
        self.fallback_attempts[from_instrument] = self.normal_attempts

        to_instrument = self.fallback_chain[self.current_instrument_index]
        self.current_instrument_index += 1
        self.instrument_name = to_instrument

        # Fresh retry budget for the new instrument
        self.normal_attempts = 0
        self.completion_attempts = 0

        # Record the transition
        import datetime as _dt

        self.instrument_fallback_history.append({
            "from": from_instrument,
            "to": to_instrument,
            "reason": reason,
            "timestamp": _dt.datetime.now(tz=_dt.UTC).isoformat(),
        })

        # Trim to prevent unbounded growth
        if len(self.instrument_fallback_history) > MAX_INSTRUMENT_FALLBACK_HISTORY:
            self.instrument_fallback_history = (
                self.instrument_fallback_history[-MAX_INSTRUMENT_FALLBACK_HISTORY:]
            )

        return to_instrument

    # --- Compatibility methods for Phase 2 transition ---
    # These match the SheetExecutionState dataclass API so the type alias
    # (SheetExecutionState = SheetState) works without changing callers.

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict. Compatibility with dataclass SheetExecutionState."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SheetState:
        """Restore from dict. Compatibility with dataclass SheetExecutionState."""
        return cls.model_validate(data)

    def capture_output(
        self,
        stdout: str,
        stderr: str,
        max_bytes: int = MAX_OUTPUT_CAPTURE_BYTES,
    ) -> None:
        """Capture tail of stdout/stderr for debugging.

        Stores the last `max_bytes` of each output stream. Sets output_truncated
        to True if either stream was larger than the limit.

        Credential scanning (F-003): Before storing, both streams are scanned
        for API key patterns (sk-ant-*, AKIA*, AIzaSy*, Bearer tokens) and
        matches are replaced with [REDACTED_*] placeholders. This prevents
        leaked credentials from propagating to learning store, dashboard,
        diagnostics, and MCP resources.

        Args:
            stdout: Full stdout string from execution.
            stderr: Full stderr string from execution.
            max_bytes: Maximum bytes to capture per stream (default 10KB).
        """
        from marianne.utils.credential_scanner import redact_credentials

        # Convert to bytes to measure actual size, then slice
        stdout_bytes = stdout.encode("utf-8", errors="replace")
        stderr_bytes = stderr.encode("utf-8", errors="replace")

        stdout_truncated = len(stdout_bytes) > max_bytes
        stderr_truncated = len(stderr_bytes) > max_bytes

        # Capture tail (last N bytes) and decode back to string
        if stdout_truncated:
            self.stdout_tail = stdout_bytes[-max_bytes:].decode(
                "utf-8", errors="replace"
            )
        else:
            self.stdout_tail = stdout if stdout else None

        if stderr_truncated:
            self.stderr_tail = stderr_bytes[-max_bytes:].decode(
                "utf-8", errors="replace"
            )
        else:
            self.stderr_tail = stderr if stderr else None

        # Scan and redact credential patterns before storage (F-003)
        self.stdout_tail = redact_credentials(self.stdout_tail)
        self.stderr_tail = redact_credentials(self.stderr_tail)

        self.output_truncated = stdout_truncated or stderr_truncated

    def add_error_to_history(self, error: CheckpointErrorRecord) -> None:
        """Append an error record and enforce the history size limit.

        All callers that add errors to ``error_history`` should use this
        method instead of appending directly so that the list never exceeds
        ``MAX_ERROR_HISTORY`` entries.

        Args:
            error: The error record to add.
        """
        self.error_history.append(error)
        if len(self.error_history) > MAX_ERROR_HISTORY:
            self.error_history = self.error_history[-MAX_ERROR_HISTORY:]

    def add_fallback_to_history(self, record: dict[str, str]) -> None:
        """Append a fallback record and enforce the history size limit.

        All callers that add entries to ``instrument_fallback_history``
        should use this method instead of appending directly so the list
        never exceeds ``MAX_INSTRUMENT_FALLBACK_HISTORY`` entries.

        Args:
            record: Dict with keys from, to, reason, timestamp.
        """
        self.instrument_fallback_history.append(record)
        if len(self.instrument_fallback_history) > MAX_INSTRUMENT_FALLBACK_HISTORY:
            self.instrument_fallback_history = self.instrument_fallback_history[
                -MAX_INSTRUMENT_FALLBACK_HISTORY:
            ]


class CheckpointState(BaseModel):
    """Complete checkpoint state for a job run.

    This is the primary state object that gets persisted and restored
    for resumable job execution.

    Zombie Detection:
        A job is considered a "zombie" when the state shows RUNNING status
        but the associated process (tracked by `pid`) is no longer alive.
        This can happen when:
        - External timeout wrapper sends SIGKILL
        - System crash or forced termination
        - WSL shutdown while job running

        Use `is_zombie()` to detect this state, and `mark_zombie_detected()`
        to recover from it.

    Worktree Isolation:
        When isolation is enabled, jobs execute in a separate git worktree.
        The worktree tracking fields record the worktree state for:
        - Resume operations (reuse existing worktree)
        - Cleanup on completion (remove or preserve based on outcome)
        - Debugging (know which worktree was used)
    """

    # Job identification
    job_id: str = Field(description="Unique ID for this job run")
    job_name: str = Field(description="Name from job config")
    config_hash: str | None = Field(default=None, description="Hash of config for change detection")

    # Config storage for resume (Task 3: Config Storage)
    config_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="Serialized JobConfig for resume without config file",
    )
    config_path: str | None = Field(
        default=None,
        description="Original config file path for fallback and debugging",
    )

    # Timestamps
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Progress tracking
    total_sheets: int = Field(ge=1)
    last_completed_sheet: int = Field(
        default=0, description="Last successfully completed sheet number"
    )
    current_sheet: int | None = Field(default=None, description="Currently processing sheet")
    status: JobStatus = JobStatus.PENDING

    # Sheet-level state
    sheets: dict[int, SheetState] = Field(default_factory=dict)

    # Sheet-first architecture fields (movement 1)
    instruments_used: list[str] = Field(
        default_factory=list,
        description="Distinct instrument names used across all sheets in this job. "
        "Populated from Sheet entities at execution time.",
    )
    total_movements: int | None = Field(
        default=None,
        description="Total movement count for status display grouping. "
        "None for legacy jobs that don't use movements.",
    )

    # Execution metadata
    pid: int | None = Field(default=None, description="Process ID of running orchestrator")
    error_message: str | None = None
    total_retry_count: int = Field(default=0, description="Total retries across all sheets")
    rate_limit_waits: int = Field(default=0, description="Number of rate limit waits")
    quota_waits: int = Field(default=0, description="Number of token quota exhaustion waits")
    resume_at: str | None = Field(
        default=None,
        description="ISO timestamp for when to resume after rate limit pause",
    )

    # Cumulative cost tracking (v4 evolution: Cost Circuit Breaker)
    total_input_tokens: int = Field(
        default=0,
        description="Total input tokens consumed across all sheets",
    )
    total_output_tokens: int = Field(
        default=0,
        description="Total output tokens produced across all sheets",
    )
    total_estimated_cost: float = Field(
        default=0.0,
        description="Total estimated cost in USD for the job",
    )
    cost_limit_reached: bool = Field(
        default=False,
        description="Whether a cost limit was hit, causing job pause",
    )

    # Worktree isolation tracking (v2 evolution: Worktree Isolation)
    worktree_path: str | None = Field(
        default=None,
        description="Path to active worktree for isolated execution",
    )
    worktree_branch: str | None = Field(
        default=None,
        description="Branch name in the worktree (None or '(detached)' if detached HEAD)",
    )
    worktree_locked: bool = Field(
        default=False,
        description="Whether worktree is currently locked",
    )
    worktree_base_commit: str | None = Field(
        default=None,
        description="Commit SHA the worktree was created from",
    )
    isolation_mode: IsolationModeLiteral | None = Field(
        default=None,
        description="Isolation mode used: 'worktree', 'none', or None (not configured)",
    )
    isolation_fallback_used: bool = Field(
        default=False,
        description="True if isolation was configured but fell back to workspace",
    )

    # Parallel execution tracking (v17 evolution: Parallel Sheet Execution)
    parallel_enabled: bool = Field(
        default=False,
        description="Whether parallel execution mode is enabled for this job",
    )
    parallel_max_concurrent: int = Field(
        default=1,
        description="Maximum concurrent sheets when parallel mode is enabled",
    )
    parallel_batches_executed: int = Field(
        default=0,
        description="Number of parallel batches executed so far",
    )
    sheets_in_progress: list[int] = Field(
        default_factory=list,
        description="Sheet numbers currently executing in parallel (empty if none)",
    )

    # Synthesis tracking (v18 evolution: Result Synthesizer Pattern)
    synthesis_results: dict[str, SynthesisResultDict] = Field(
        default_factory=dict,
        description="Synthesis results keyed by batch_id (v18: Result Synthesizer)",
    )

    # Hook execution results (observability: detached hook logging)
    hook_results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Persisted hook execution results for post-mortem diagnostics",
    )

    # Spec corpus tracking (Phase 1: Spec Corpus Pipeline)
    spec_corpus_hash: str = Field(
        default="",
        description="Hash of the spec corpus loaded at job start. "
        "Used for drift detection — if the corpus changes between runs, "
        "the learning store can correlate outcomes to the correct corpus version. "
        "Empty string means no spec corpus was configured.",
    )

    # Circuit breaker state history (observability: CB persistence)
    circuit_breaker_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="History of circuit breaker state transitions for post-mortem diagnostics",
    )

    @model_validator(mode="after")
    def _enforce_status_invariants(self) -> CheckpointState:
        """Auto-fill started_at when job is RUNNING.

        Invariant:
        - RUNNING → started_at must be set

        This ensures that status display shows correct elapsed time
        instead of "0.0s elapsed" (F-493).
        """
        if self.status == JobStatus.RUNNING and self.started_at is None:
            _logger.debug(
                "checkpoint_state.invariant_autofill",
                job_id=self.job_id,
                field="started_at",
                status="RUNNING",
            )
            self.started_at = utc_now()

        return self

    def record_hook_result(self, result: dict[str, Any]) -> None:
        """Append a hook result to the checkpoint state.

        Args:
            result: Serialized HookResult dict from hook execution.
        """
        self.hook_results.append(result)
        self.updated_at = utc_now()

    def record_circuit_breaker_change(
        self,
        state: str,
        trigger: str,
        consecutive_failures: int,
    ) -> None:
        """Record a circuit breaker state transition.

        Persists circuit breaker state changes so that ``mzt status``
        can display ground-truth CB state instead of inferring it from
        failure patterns.

        Args:
            state: Current CB state after transition ("closed", "open", "half_open").
            trigger: What caused the transition (e.g., "failure_recorded", "success_recorded").
            consecutive_failures: Number of consecutive failures at time of transition.
        """
        self.circuit_breaker_history.append({
            "state": state,
            "timestamp": utc_now().isoformat(),
            "trigger": trigger,
            "consecutive_failures": consecutive_failures,
        })
        self.updated_at = utc_now()

        _logger.debug(
            "circuit_breaker_change_recorded",
            job_id=self.job_id,
            state=state,
            trigger=trigger,
            consecutive_failures=consecutive_failures,
        )

    def add_synthesis(self, batch_id: str, result: SynthesisResultDict) -> None:
        """Add or update a synthesis result.

        Args:
            batch_id: The batch identifier.
            result: Synthesis result as dict (from SynthesisResult.to_dict()).
        """
        self.synthesis_results[batch_id] = result
        self.updated_at = utc_now()

        _logger.debug(
            "synthesis_added",
            job_id=self.job_id,
            batch_id=batch_id,
            status=result.get("status"),
        )

    def get_next_sheet(self) -> int | None:
        """Determine the next sheet to process.

        Returns None if all sheets are complete.
        """
        if self.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            return None

        # Check for in-progress sheet (resume from crash)
        if self.current_sheet is not None:
            sheet_state = self.sheets.get(self.current_sheet)
            if sheet_state and sheet_state.status == SheetStatus.IN_PROGRESS:
                return self.current_sheet

        # Find next pending sheet after last completed
        for sheet_num in range(self.last_completed_sheet + 1, self.total_sheets + 1):
            sheet_state = self.sheets.get(sheet_num)
            if sheet_state is None:
                return sheet_num
            if sheet_state.status in (SheetStatus.PENDING, SheetStatus.FAILED):
                return sheet_num

        return None

    def mark_sheet_started(self, sheet_num: int) -> None:
        """Mark a sheet as started."""
        previous_status = self.status
        self.current_sheet = sheet_num
        self.status = JobStatus.RUNNING
        self.updated_at = utc_now()

        if sheet_num not in self.sheets:
            self.sheets[sheet_num] = SheetState(sheet_num=sheet_num)

        sheet = self.sheets[sheet_num]
        sheet.status = SheetStatus.IN_PROGRESS
        sheet.started_at = utc_now()
        sheet.attempt_count += 1
        # Clear stale fields from previous attempts so retry starts clean.
        sheet.error_message = None
        sheet.exit_code = None
        sheet.execution_mode = None

        _logger.debug(
            "sheet_started",
            job_id=self.job_id,
            sheet_num=sheet_num,
            attempt_count=sheet.attempt_count,
            previous_status=previous_status.value,
            total_sheets=self.total_sheets,
        )

    def mark_sheet_completed(
        self,
        sheet_num: int,
        validation_passed: bool = True,
        validation_details: list[ValidationDetailDict] | None = None,
        execution_duration_seconds: float | None = None,
    ) -> None:
        """Mark a sheet as completed.

        Args:
            sheet_num: Sheet number that completed.
            validation_passed: Whether validation checks passed.
            validation_details: Detailed validation results.
            execution_duration_seconds: How long the sheet execution took.
        """
        self.updated_at = utc_now()

        if sheet_num not in self.sheets:
            self.sheets[sheet_num] = SheetState(sheet_num=sheet_num)
        sheet = self.sheets[sheet_num]
        sheet.status = SheetStatus.COMPLETED
        sheet.completed_at = utc_now()
        sheet.exit_code = 0
        sheet.validation_passed = validation_passed
        sheet.validation_details = validation_details
        if execution_duration_seconds is not None:
            sheet.execution_duration_seconds = execution_duration_seconds

        # Only advance the watermark, never retreat it (Q016/#37).
        # In parallel execution, sheets may complete out of order.
        if sheet_num > self.last_completed_sheet:
            self.last_completed_sheet = sheet_num
        self.current_sheet = None

        # Check if job is complete — all sheets must be completed, not just
        # the highest-numbered one, to handle parallel out-of-order completion.
        job_completed = (
            len(self.sheets) >= self.total_sheets
            and all(
                s.status == SheetStatus.COMPLETED
                for s in self.sheets.values()
            )
        )
        if job_completed:
            self.status = JobStatus.COMPLETED
            self.completed_at = utc_now()

        _logger.debug(
            "sheet_completed",
            job_id=self.job_id,
            sheet_num=sheet_num,
            validation_passed=validation_passed,
            attempt_count=sheet.attempt_count,
            job_completed=job_completed,
            progress=f"{self.last_completed_sheet}/{self.total_sheets}",
        )

    def mark_sheet_failed(
        self,
        sheet_num: int,
        error_message: str,
        error_category: ErrorCategory | str | None = None,
        exit_code: int | None = None,
        exit_signal: int | None = None,
        exit_reason: ExitReason | None = None,
        execution_duration_seconds: float | None = None,
        error_code: str | None = None,
    ) -> None:
        """Mark a sheet as failed.

        Args:
            sheet_num: Sheet number that failed.
            error_message: Human-readable error description.
            error_category: Error category from ErrorClassifier (e.g., "signal", "timeout").
            exit_code: Process exit code (None if killed by signal).
            exit_signal: Signal number if killed by signal (e.g., 9=SIGKILL, 15=SIGTERM).
            exit_reason: Why execution ended ("completed", "timeout", "killed", "error").
            execution_duration_seconds: How long the sheet execution took.
            error_code: Structured error code (e.g., "E001", "E006"). More specific
                than error_category — distinguishes stale (E006) from timeout (E001).
        """
        self.updated_at = utc_now()

        if sheet_num not in self.sheets:
            self.sheets[sheet_num] = SheetState(sheet_num=sheet_num)
        sheet = self.sheets[sheet_num]
        sheet.status = SheetStatus.FAILED
        sheet.completed_at = utc_now()
        sheet.error_message = error_message
        if error_category is not None and not isinstance(error_category, ErrorCategory):
            try:
                error_category = ErrorCategory(error_category)
            except ValueError:
                _logger.warning("unknown_error_category", value=error_category)
                error_category = None
        sheet.error_category = error_category
        sheet.error_code = error_code
        sheet.exit_code = exit_code
        sheet.exit_signal = exit_signal
        sheet.exit_reason = exit_reason
        if execution_duration_seconds is not None:
            sheet.execution_duration_seconds = execution_duration_seconds

        self.current_sheet = None
        self.total_retry_count += 1

        _logger.debug(
            "sheet_failed",
            job_id=self.job_id,
            sheet_num=sheet_num,
            error_category=error_category,
            exit_code=exit_code,
            exit_signal=exit_signal,
            exit_reason=exit_reason,
            attempt_count=sheet.attempt_count,
            total_retry_count=self.total_retry_count,
            error_message=error_message[:100] if error_message else None,
        )

    def mark_sheet_skipped(
        self,
        sheet_num: int,
        reason: str | None = None,
    ) -> None:
        """Mark a sheet as skipped.

        v21 Evolution: Proactive Checkpoint System - supports skipping sheets
        via checkpoint response.

        Args:
            sheet_num: Sheet number to skip.
            reason: Optional reason for skipping (stored in error_message field).
        """
        self.updated_at = utc_now()

        if sheet_num not in self.sheets:
            self.sheets[sheet_num] = SheetState(sheet_num=sheet_num)
        sheet = self.sheets[sheet_num]
        sheet.status = SheetStatus.SKIPPED
        sheet.completed_at = utc_now()
        if reason:
            sheet.error_message = reason

        self.current_sheet = None

        _logger.info(
            "sheet_skipped",
            job_id=self.job_id,
            sheet_num=sheet_num,
            reason=reason,
        )

    def mark_job_failed(self, error_message: str) -> None:
        """Mark the entire job as failed."""
        previous_status = self.status
        self.status = JobStatus.FAILED
        self.error_message = error_message
        self.pid = None  # Clear PID so stale PID doesn't block resume
        self.completed_at = utc_now()
        self.updated_at = utc_now()

        _logger.error(
            "job_failed",
            job_id=self.job_id,
            previous_status=previous_status.value,
            last_completed_sheet=self.last_completed_sheet,
            total_sheets=self.total_sheets,
            total_retry_count=self.total_retry_count,
            error_message=error_message[:200] if error_message else None,
        )

    def mark_job_paused(self) -> None:
        """Mark the job as paused."""
        previous_status = self.status
        self.status = JobStatus.PAUSED
        self.pid = None  # Clear PID so stale PID doesn't block resume
        self.updated_at = utc_now()

        _logger.info(
            "job_paused",
            job_id=self.job_id,
            previous_status=previous_status.value,
            last_completed_sheet=self.last_completed_sheet,
            total_sheets=self.total_sheets,
            current_sheet=self.current_sheet,
        )

    def get_progress(self) -> tuple[int, int]:
        """Get progress as (completed, total)."""
        completed = sum(
            1 for b in self.sheets.values()
            if b.status == SheetStatus.COMPLETED
        )
        return completed, self.total_sheets

    def get_progress_percent(self) -> float:
        """Get progress as percentage."""
        completed, total = self.get_progress()
        return (completed / total * 100) if total > 0 else 0.0

    def is_zombie(self) -> bool:
        """Check if this job is a zombie (RUNNING but process dead).

        A zombie state occurs when:
        1. Status is RUNNING
        2. PID is set
        3. Process with that PID is no longer alive

        Note: This only checks if the PID is dead. It does NOT use time-based
        stale detection, as jobs can legitimately run for hours or days.

        Returns:
            True if job appears to be a zombie, False otherwise.
        """
        # Only RUNNING jobs can be zombies
        if self.status != JobStatus.RUNNING:
            return False

        # If no PID recorded, can't determine - not a zombie by this check
        if self.pid is None:
            return False

        # Check if process is alive
        try:
            # os.kill with signal 0 checks if process exists without killing it
            os.kill(self.pid, 0)
            # Process exists - not a zombie
            return False
        except ProcessLookupError:
            # Process doesn't exist - definite zombie
            _logger.warning(
                "zombie_dead_pid_detected",
                job_id=self.job_id,
                pid=self.pid,
            )
            return True
        except PermissionError:
            # Can't check (different user) - assume not zombie
            return False
        except OSError:
            # Other OS error - assume not zombie to be safe
            return False

    def mark_zombie_detected(self, reason: str | None = None) -> None:
        """Mark this job as recovered from zombie state.

        Changes status from RUNNING to PAUSED, clears PID, and records
        the zombie recovery in the error message.

        Args:
            reason: Optional additional context about why zombie was detected.
        """
        previous_status = self.status
        previous_pid = self.pid

        self.status = JobStatus.PAUSED
        self.pid = None
        self.updated_at = utc_now()

        # Build zombie recovery message - this is informational, not an error
        # The job has been successfully recovered and can be resumed
        zombie_msg = (
            f"Recovered from stale running state (PID {previous_pid} no longer active). "
            "Job is now paused and ready to resume."
        )
        if reason:
            zombie_msg += f" Trigger: {reason}"

        # Only set error_message if there isn't already a real error
        # Zombie recovery is informational, not an error condition
        if not self.error_message:
            self.error_message = zombie_msg

        _logger.warning(
            "zombie_recovered",
            job_id=self.job_id,
            previous_status=previous_status.value,
            previous_pid=previous_pid,
            reason=reason,
        )

    def set_running_pid(self, pid: int | None = None) -> None:
        """Set the PID of the running orchestrator process.

        Call this when starting job execution to enable zombie detection.
        If pid is None, uses the current process PID.

        Args:
            pid: Process ID to record. Defaults to current process.
        """
        self.pid = pid if pid is not None else os.getpid()
        self.updated_at = utc_now()

        _logger.debug(
            "running_pid_set",
            job_id=self.job_id,
            pid=self.pid,
        )
