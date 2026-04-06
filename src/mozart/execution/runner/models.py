"""Data models for the job runner.

Contains dataclasses, enums, and exceptions used by the JobRunner
and its mixin components. These are extracted to enable clean imports
and avoid circular dependencies during modularization.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from rich.console import Console

if TYPE_CHECKING:
    from collections import deque

    from mozart.backends.base import ExecutionResult
    from mozart.core.checkpoint import CheckpointState
    from mozart.execution.grounding import GroundingEngine, GroundingResult
    from mozart.execution.parallel import ResourceChecker
    from mozart.execution.retry_strategy import ErrorRecord
    from mozart.execution.validation import SheetValidationResult
    from mozart.learning.global_store import GlobalLearningStore

from mozart.core.checkpoint import JobStatus
from mozart.execution.escalation import ConsoleCheckpointHandler, ConsoleEscalationHandler
from mozart.execution.hooks import HookResult
from mozart.learning.judgment import JudgmentClient
from mozart.learning.outcomes import OutcomeStore


@dataclass
class RunSummary:
    """Summary of a completed job run.

    Tracks key metrics for display at job completion:
    - Total execution time
    - Sheet success/failure counts
    - Validation pass rate
    - Retry statistics
    - Cost tracking
    """

    job_id: str
    job_name: str
    total_sheets: int
    completed_sheets: int = 0
    failed_sheets: int = 0
    skipped_sheets: int = 0
    total_duration_seconds: float = 0.0
    total_retries: int = 0
    total_completion_attempts: int = 0
    rate_limit_waits: int = 0
    validation_pass_count: int = 0
    validation_fail_count: int = 0
    successes_without_retry: int = 0
    final_status: JobStatus = field(default=JobStatus.PENDING)

    # Cost tracking (v4 evolution: Cost Circuit Breaker)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost: float = 0.0
    cost_limit_hit: bool = False

    # Hook execution results (Concert orchestration)
    hook_results: list[HookResult] = field(default_factory=list)
    hooks_executed: int = 0
    hooks_succeeded: int = 0
    hooks_failed: int = 0

    def __post_init__(self) -> None:
        if self.completed_sheets > self.total_sheets:
            raise ValueError(
                f"completed_sheets ({self.completed_sheets}) "
                f"exceeds total_sheets ({self.total_sheets})"
            )

    @property
    def success_rate(self) -> float:
        """Calculate sheet success rate as percentage.

        Skipped sheets are excluded from the denominator since they were
        never attempted (e.g., skip_when_command conditions met).  This
        prevents intentionally skipped sheets from dragging the rate down.
        """
        executed = self.total_sheets - self.skipped_sheets
        if executed == 0:
            return 0.0
        return (self.completed_sheets / executed) * 100

    @property
    def validation_pass_rate(self) -> float:
        """Calculate validation pass rate as percentage."""
        total = self.validation_pass_count + self.validation_fail_count
        if total == 0:
            return 100.0  # No validations = 100% pass
        return (self.validation_pass_count / total) * 100

    @property
    def success_without_retry_rate(self) -> float:
        """Calculate success-without-retry rate as percentage."""
        if self.completed_sheets == 0:
            return 0.0
        return (self.successes_without_retry / self.completed_sheets) * 100

    def to_dict(self) -> dict[str, Any]:
        """Convert summary to dictionary for JSON output."""
        return {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "status": self.final_status.value,
            "duration_seconds": round(self.total_duration_seconds, 2),
            "duration_formatted": self._format_duration(self.total_duration_seconds),
            "sheets": {
                "total": self.total_sheets,
                "completed": self.completed_sheets,
                "failed": self.failed_sheets,
                "skipped": self.skipped_sheets,
                "success_rate": round(self.success_rate, 1),
            },
            "validation": {
                "passed": self.validation_pass_count,
                "failed": self.validation_fail_count,
                "pass_rate": round(self.validation_pass_rate, 1),
            },
            "execution": {
                "total_retries": self.total_retries,
                "completion_attempts": self.total_completion_attempts,
                "rate_limit_waits": self.rate_limit_waits,
                "successes_without_retry": self.successes_without_retry,
                "success_without_retry_rate": round(self.success_without_retry_rate, 1),
            },
        }

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"


@dataclass
class GroundingDecisionContext:
    """Context from grounding hooks for completion mode decisions.

    Encapsulates grounding results to inform decision-making about
    whether to retry, complete, or escalate. This integrates external
    validation signals into the adaptive execution flow.

    v10 Evolution: Grounding→Completion Integration
    """

    passed: bool
    """Whether all grounding hooks passed."""

    message: str
    """Human-readable summary of grounding results."""

    confidence: float = 1.0
    """Average confidence across all grounding results (0.0-1.0)."""

    should_escalate: bool = False
    """Whether any grounding hook recommends escalation."""

    recovery_guidance: str | None = None
    """Optional guidance for recovery from grounding failures."""

    hooks_executed: int = 0
    """Number of grounding hooks that were executed."""

    def __post_init__(self) -> None:
        """Clamp confidence to [0.0, 1.0] range."""
        self.confidence = max(0.0, min(1.0, self.confidence))

    @classmethod
    def from_results(cls, results: list[GroundingResult]) -> GroundingDecisionContext:
        """Build context from grounding results list.

        Args:
            results: List of GroundingResult from grounding hooks.

        Returns:
            GroundingDecisionContext summarizing the results.
        """
        if not results:
            return cls(passed=True, message="No grounding hooks executed", hooks_executed=0)

        passed = all(r.passed for r in results)
        confidences = [r.confidence for r in results]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 1.0
        should_escalate = any(r.should_escalate for r in results)

        # Collect recovery guidance from failed hooks
        failed = [r for r in results if not r.passed]
        recovery_guidance = None
        if failed:
            guidance_parts = [r.recovery_guidance for r in failed if r.recovery_guidance]
            if guidance_parts:
                recovery_guidance = "; ".join(guidance_parts)

        # Build message
        if passed:
            message = f"All {len(results)} grounding check(s) passed"
        else:
            failures = ", ".join(f"{r.hook_name}: {r.message}" for r in failed)
            message = f"{len(failed)}/{len(results)} grounding check(s) failed: {failures}"

        return cls(
            passed=passed,
            message=message,
            confidence=avg_confidence,
            should_escalate=should_escalate,
            recovery_guidance=recovery_guidance,
            hooks_executed=len(results),
        )

    @classmethod
    def disabled(cls) -> GroundingDecisionContext:
        """Create context when grounding is disabled.

        Returns:
            GroundingDecisionContext indicating grounding was not run.
        """
        return cls(passed=True, message="Grounding not enabled", hooks_executed=0)


class SheetExecutionMode(str, Enum):
    """Mode of sheet execution."""

    NORMAL = "normal"
    """Standard first-time execution."""

    COMPLETION = "completion"
    """Completion mode after partial success - focused on missing items."""

    RETRY = "retry"
    """Full retry after completion mode exhausted or minority passed."""

    ESCALATE = "escalate"
    """Escalation mode - low confidence requires external decision."""


@dataclass
class SheetExecutionSetup:
    """Encapsulates the setup phase results for _execute_sheet_with_recovery.

    Groups all initialization state needed before the main execution loop begins.
    This enables cleaner separation between setup and execution phases.
    """

    original_prompt: str
    """The base prompt built from template, patterns, and validation rules."""

    current_prompt: str
    """Active prompt (may be modified by checkpoint system before loop)."""

    current_mode: SheetExecutionMode
    """Initial execution mode (normally NORMAL)."""

    max_retries: int
    """Maximum retry attempts from config."""

    max_completion: int
    """Maximum completion-mode attempts from config."""

    relevant_patterns: list[str] = field(default_factory=list)
    """Learned patterns injected into the prompt."""

    preflight_warnings: int = 0
    """Number of preflight warnings (non-fatal)."""

    preflight_token_estimate: int = 0
    """Estimated token count from preflight metrics."""


@dataclass
class ValidationSuccessContext:
    """Context passed to _handle_validation_success after all validations pass.

    Groups the 11 keyword parameters into a single context object,
    matching the SheetExecutionSetup pattern used for the setup phase.
    """

    state: CheckpointState
    """Current checkpoint state."""

    sheet_num: int
    """Sheet number being executed."""

    result: ExecutionResult
    """ExecutionResult from the backend."""

    validation_result: SheetValidationResult
    """SheetValidationResult with all validation details."""

    validation_duration: float
    """Time spent running validations (seconds)."""

    current_prompt: str
    """The prompt that was executed."""

    normal_attempts: int
    """Number of normal retry attempts used."""

    completion_attempts: int
    """Number of completion-mode attempts used."""

    execution_start_time: float
    """Monotonic timestamp when execution started."""

    execution_history: deque[ExecutionResult]
    """Deque of ExecutionResults from this sheet's attempts."""

    pending_recovery: dict[str, Any] | None
    """Pending self-healing recovery context, if any."""


@dataclass
class ExecutionFailureContext:
    """Context passed to _handle_execution_failure for non-success error handling.

    Groups the parameters needed to classify errors, manage retries,
    and decide between healing, adaptive abort, or normal retry.
    """

    state: CheckpointState
    """Current checkpoint state."""

    sheet_num: int
    """Sheet number being executed."""

    result: ExecutionResult
    """ExecutionResult from the backend."""

    validation_result: SheetValidationResult
    """SheetValidationResult with validation details."""

    passed_count: int
    """Number of validations that passed."""

    failed_count: int
    """Number of validations that failed."""

    error_history: list[ErrorRecord]
    """List of ErrorRecord for adaptive retry analysis."""

    normal_attempts: int
    """Current normal retry attempt count."""

    max_retries: int
    """Maximum retry attempts from config."""

    healing_attempts: int
    """Current healing cycle count."""

    max_healing_cycles: int
    """Maximum healing cycles allowed."""

    pending_recovery: dict[str, Any] | None
    """Pending recovery context from previous retry."""

    grounding_ctx: GroundingDecisionContext
    """GroundingDecisionContext for pattern feedback."""


@dataclass
class FailureHandlingResult:
    """Result from _handle_execution_failure indicating what the caller should do.

    Uses action field for flow control:
    - "continue": Continue the while loop (retry)
    - "fatal": Raise FatalError with the given message
    """

    action: Literal["continue", "fatal"]
    """Either 'continue' or 'fatal'."""

    normal_attempts: int
    """Updated normal attempt counter."""

    healing_attempts: int
    """Updated healing attempt counter."""

    pending_recovery: dict[str, Any] | None
    """Updated pending recovery context."""

    fatal_message: str = ""
    """Error message if action is 'fatal'."""


@dataclass
class ModeDecisionContext:
    """Input context for _apply_mode_decision.

    Groups the 12 parameters of ``_apply_mode_decision`` into a single
    context object, matching the ValidationSuccessContext /
    ExecutionFailureContext pattern used elsewhere.
    """

    state: CheckpointState
    """Current checkpoint state."""

    sheet_num: int
    """Sheet number being executed."""

    validation_result: SheetValidationResult
    """Current validation results."""

    execution_history: deque[ExecutionResult]
    """History of execution results."""

    original_prompt: str
    """Original prompt (for reset on retry)."""

    current_prompt: str
    """Currently active prompt."""

    current_mode: SheetExecutionMode
    """Current execution mode."""

    normal_attempts: int
    """Normal retry attempt count."""

    completion_attempts: int
    """Completion-mode attempt count."""

    max_retries: int
    """Maximum normal retries."""

    max_completion: int
    """Maximum completion attempts."""

    pass_pct: float
    """Current validation pass percentage."""


@dataclass
class ModeDecisionResult:
    """Result from _apply_mode_decision indicating what the caller should do.

    Groups the outcome of judgment/completion/escalation/retry mode selection
    into a single result, reducing control flow complexity in the main loop.

    Flow control:
    - "continue": Re-enter the while loop (completion mode, retry, escalation retry)
    - "return": Exit the function (escalation skip)
    - "fatal": Raise FatalError with the given message
    """

    action: Literal["continue", "return", "fatal"]
    """Flow control action for the caller."""

    current_prompt: str
    """Updated prompt (may be modified by completion/escalation/judgment)."""

    current_mode: SheetExecutionMode
    """Updated execution mode."""

    normal_attempts: int
    """Updated normal attempt counter."""

    completion_attempts: int
    """Updated completion attempt counter."""

    fatal_message: str = ""
    """Error message if action is 'fatal'."""


class FatalError(Exception):
    """Non-recoverable error that should stop the job."""

    pass


class RateLimitExhaustedError(FatalError):
    """Rate limit or quota exhaustion — job should PAUSE, not FAIL.

    Subclasses FatalError for backward compatibility: existing
    ``except FatalError`` blocks still catch it, but more specific
    ``except RateLimitExhaustedError`` blocks intercept first when
    ordered before ``except FatalError``.

    Attributes:
        resume_after: When the rate limit resets (ISO datetime), or None.
        backend_type: Which backend hit the limit (e.g., "claude-cli").
        quota_exhaustion: True if daily/monthly quota is exhausted,
            False if it's a per-minute rate limit.
    """

    def __init__(
        self,
        message: str,
        resume_after: datetime | None = None,
        backend_type: str = "unknown",
        quota_exhaustion: bool = False,
    ) -> None:
        super().__init__(message)
        self.resume_after = resume_after
        self.backend_type = backend_type
        self.quota_exhaustion = quota_exhaustion


class GracefulShutdownError(Exception):
    """Raised when Ctrl+C is pressed to trigger graceful shutdown.

    This exception is caught by the runner to save state before exiting.
    """

    pass


@dataclass
class RunnerContext:
    """Optional context components for JobRunner.

    Groups optional dependencies that configure learning, escalation,
    and progress reporting. Using a context object reduces the parameter
    count in JobRunner.__init__ from 11 to 5 required parameters plus
    one optional context object.

    Usage:
        # Without context (minimal setup)
        runner = JobRunner(config, backend, state_backend)

        # With learning enabled
        context = RunnerContext(
            outcome_store=JsonOutcomeStore(workspace),
            global_learning_store=get_global_store(),
        )
        runner = JobRunner(config, backend, state_backend, context=context)
    """

    # Learning components
    outcome_store: OutcomeStore | None = None
    """Store for recording sheet outcomes (Phase 1 learning)."""

    escalation_handler: ConsoleEscalationHandler | None = None
    """Handler for low-confidence decisions (Phase 2 escalation)."""

    checkpoint_handler: ConsoleCheckpointHandler | None = None
    """Handler for proactive pre-execution checkpoints (v21 Evolution)."""

    judgment_client: JudgmentClient | None = None
    """Client for TDF-aligned execution decisions (Phase 4 Recursive Light)."""

    global_learning_store: GlobalLearningStore | None = None
    """Store for cross-workspace learning (Evolution #3, #8)."""

    # External validation
    grounding_engine: GroundingEngine | None = None
    """Engine for external validation of sheet outputs (v8 Grounding)."""

    # Daemon integration
    daemon_managed: bool = False
    """When True, post-success hooks are suppressed (daemon executes them)."""

    # UI and progress reporting
    console: Console | None = None
    """Rich console for output display."""

    progress_callback: Callable[[int, int, float | None], None] | None = None
    """Callback for progress updates: (completed, total, eta_seconds)."""

    execution_progress_callback: Callable[[dict[str, Any]], None] | None = None
    """Callback for real-time execution progress during sheet runs."""

    # Daemon integration callbacks
    rate_limit_callback: Callable[[str, float, str, int], Any] | None = None
    """Async callback to notify daemon rate coordinator on rate limit hits.

    Signature: (backend_type, wait_seconds, job_id, sheet_num) → Awaitable[None].
    When set, called from _handle_rate_limit() so the daemon's
    RateLimitCoordinator receives live data for cross-job coordination.
    """

    event_callback: Callable[[str, int, str, dict[str, Any] | None], Any] | None = None
    """Async callback to notify daemon of runner lifecycle events.

    Signature: (job_id, sheet_num, event, data) -> Awaitable[None].
    """

    # Preflight thresholds (from daemon config)
    token_warning_threshold: int | None = None
    """Override preflight token warning threshold. None uses module default."""

    token_error_threshold: int | None = None
    """Override preflight token error threshold. None uses module default."""

    # In-process pause signaling (workspace-independent job control)
    pause_event: asyncio.Event | None = None
    """Async event set by the daemon's JobManager to request graceful pause.

    When set, the runner checks this event at sheet boundaries (via
    _check_pause_signal) instead of polling the filesystem for a
    `.mozart-pause-{job_id}` file.  This enables workspace-independent
    job control — the daemon can pause a job without needing to know
    or write to its workspace directory.
    """

    # Self-healing configuration (v11 Evolution: Self-Healing)
    self_healing_enabled: bool = False
    """Enable automatic diagnosis and remediation when retries are exhausted."""

    self_healing_auto_confirm: bool = False
    """Auto-confirm suggested fixes (equivalent to --yes flag)."""

    resource_checker: ResourceChecker | None = field(
        default=None,
        metadata={"description": "Resource checker for parallel execution backpressure."},
    )


# Re-export commonly used types from other modules for convenience
# This allows other runner modules to import from models.py instead
# of managing multiple import sources
__all__ = [
    "ExecutionFailureContext",
    "FailureHandlingResult",
    "FatalError",
    "GracefulShutdownError",
    "GroundingDecisionContext",
    "RateLimitExhaustedError",
    "RunSummary",
    "RunnerContext",
    "SheetExecutionMode",
    "SheetExecutionSetup",
    "ValidationSuccessContext",
]
