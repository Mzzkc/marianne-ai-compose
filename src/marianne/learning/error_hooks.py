"""Error learning hooks for integration with ErrorClassifier.

This module implements error learning as designed in Movement III:
- Extend ErrorClassifier with learning hooks (CV 0.82)
- Track error patterns globally
- Learn adaptive wait times based on actual recovery success
- Integrate with existing ErrorClassifier without major refactoring

Error Learning Hook Integration Points:
1. on_error_classified: Called when an error is classified
   - Records error occurrence with context
   - Queries similar past errors for suggested_wait adjustment

2. on_error_recovered: Called when recovery after waiting succeeds
   - Records actual_wait and recovery_success to error_recoveries
   - Updates learned wait times

3. on_auth_failure: Distinguishes transient vs permanent auth failures
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from marianne.core.errors import (
    ClassificationResult,
    ClassifiedError,
    ErrorCategory,
    ErrorClassifier,
    ErrorCode,
)
from marianne.core.logging import get_logger

if TYPE_CHECKING:
    from marianne.learning.global_store import GlobalLearningStore

# Module-level logger for error learning hooks
_logger = get_logger("learning.error_hooks")


@dataclass
class ErrorLearningConfig:
    """Configuration for error learning.

    Attributes:
        enabled: Master switch for error learning.
        min_samples: Minimum recovery samples before using learned delay.
        learning_rate: How much to weight new observations vs existing.
        max_wait_time: Maximum wait time to suggest (cap on learning).
        min_wait_time: Minimum wait time to suggest (floor on learning).
        decay_factor: How much to decay old samples over time.
    """

    enabled: bool = True
    min_samples: int = 3
    learning_rate: float = 0.3
    max_wait_time: float = 7200.0  # 2 hours max
    min_wait_time: float = 10.0  # 10 seconds minimum
    decay_factor: float = 0.9  # Old samples decay by 10%


@dataclass
class ErrorLearningContext:
    """Context for an error learning event.

    Tracks the full context of an error for learning purposes.
    """

    error: ClassifiedError | ClassificationResult
    job_id: str
    sheet_num: int
    workspace_path: Path
    model: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    suggested_wait: float | None = None
    actual_wait: float | None = None
    recovery_success: bool | None = None

    @property
    def error_code(self) -> ErrorCode:
        """Get the error code from either ClassifiedError or ClassificationResult."""
        if isinstance(self.error, ClassificationResult):
            return self.error.primary.error_code
        return self.error.error_code

    @property
    def category(self) -> ErrorCategory:
        """Get the error category."""
        if isinstance(self.error, ClassificationResult):
            return self.error.primary.category
        return self.error.category


class ErrorLearningHooks:
    """Learning hooks for ErrorClassifier integration.

    Provides hooks that can be called at various points in error handling
    to record error patterns and learn from recovery attempts.

    The hooks follow the design pattern of non-invasive integration:
    - They can be optionally called by the baton
    - If global_store is None, hooks are no-ops
    - All operations are logged for debugging

    Usage:
        hooks = ErrorLearningHooks(global_store)

        # When an error is classified
        adjusted = hooks.on_error_classified(context)
        if adjusted.suggested_wait_seconds:
            await asyncio.sleep(adjusted.suggested_wait_seconds)

        # After recovery attempt
        hooks.on_error_recovered(context, success=True)
    """

    def __init__(
        self,
        global_store: "GlobalLearningStore | None" = None,
        config: ErrorLearningConfig | None = None,
    ) -> None:
        """Initialize error learning hooks.

        Args:
            global_store: Global learning store for persistence.
                         If None, hooks are no-ops.
            config: Error learning configuration.
        """
        self._store = global_store
        self._config = config or ErrorLearningConfig()
        self._pending_contexts: dict[str, ErrorLearningContext] = {}

    @property
    def enabled(self) -> bool:
        """Check if error learning is enabled and store is available."""
        return self._config.enabled and self._store is not None

    def on_error_classified(
        self,
        context: ErrorLearningContext,
    ) -> ClassifiedError:
        """Hook called when an error is classified.

        Records the error occurrence and potentially adjusts the suggested
        wait time based on learned patterns.

        Args:
            context: Full error context including job/sheet info.

        Returns:
            The error with potentially adjusted suggested_wait_seconds.
        """
        if not self.enabled:
            return self._get_classified_error(context)

        error = self._get_classified_error(context)

        # Record pattern for this error
        self._record_error_pattern(context)

        # Check if this is a rate limit error and we have learned data
        if error.category == ErrorCategory.RATE_LIMIT:
            adjusted_wait = self._get_learned_wait(context)
            if adjusted_wait is not None:
                _logger.info(
                    f"Adjusted wait for {error.error_code.value}: "
                    f"{error.suggested_wait_seconds}s -> {adjusted_wait}s (learned)"
                )
                # Create new error with adjusted wait
                return ClassifiedError(
                    category=error.category,
                    message=error.message,
                    error_code=error.error_code,
                    original_error=error.original_error,
                    exit_code=error.exit_code,
                    exit_signal=error.exit_signal,
                    exit_reason=error.exit_reason,
                    retriable=error.retriable,
                    suggested_wait_seconds=adjusted_wait,
                    error_info=error.error_info,
                )

        # Track this context for later recovery reporting
        context_key = self._get_context_key(context)
        self._pending_contexts[context_key] = context

        return error

    def on_error_recovered(
        self,
        context: ErrorLearningContext,
        success: bool,
    ) -> None:
        """Hook called after a recovery attempt.

        Records the actual wait time and whether recovery succeeded,
        updating the learned wait times for this error code.

        Args:
            context: Error context with actual_wait filled in.
            success: Whether the recovery attempt succeeded.
        """
        if not self.enabled or self._store is None:
            return

        error = self._get_classified_error(context)

        # Record the recovery to the global store
        if context.actual_wait is not None:
            suggested_wait = context.suggested_wait or error.suggested_wait_seconds or 0
            self._store.record_error_recovery(
                error_code=error.error_code.value,
                suggested_wait=suggested_wait,
                actual_wait=context.actual_wait,
                recovery_success=success,
                model=context.model,
            )

            _logger.debug(
                f"Recorded error recovery: {error.error_code.value} "
                f"actual_wait={context.actual_wait}s success={success}"
            )

        # Clean up pending context
        context_key = self._get_context_key(context)
        self._pending_contexts.pop(context_key, None)

    def on_auth_failure(
        self,
        context: ErrorLearningContext,
    ) -> tuple[bool, str]:
        """Hook to analyze auth failures.

        Uses historical data to determine if this auth failure is likely
        transient (worth retrying) or permanent (should fail immediately).

        Args:
            context: Error context for the auth failure.

        Returns:
            Tuple of (is_transient, reason).
            If is_transient is True, the error might recover after a delay.
        """
        if not self.enabled or self._store is None:
            return False, "No learning data available"

        error = self._get_classified_error(context)

        # Query past auth failures for this model/context
        # If we've seen successful recoveries, mark as transient
        with self._store._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN recovery_success THEN 1 ELSE 0 END) as successes,
                    COUNT(*) as total
                FROM error_recoveries
                WHERE error_code = ? AND model = ?
                """,
                (error.error_code.value, context.model),
            )
            row = cursor.fetchone()

            if row and row["total"] >= self._config.min_samples:
                success_rate = row["successes"] / row["total"]
                if success_rate > 0.3:  # >30% recovery rate suggests transient
                    return True, f"Historical recovery rate: {success_rate:.0%}"

        return False, "Insufficient recovery history or low success rate"

    def get_error_stats(self, error_code: str) -> dict[str, str | int | float]:
        """Get statistics for a specific error code.

        Args:
            error_code: The error code to query (e.g., 'E103').

        Returns:
            Dictionary with error statistics.
        """
        if not self.enabled or self._store is None:
            return {"error": "Learning not enabled"}

        with self._store._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as total_occurrences,
                    SUM(CASE WHEN recovery_success THEN 1 ELSE 0 END) as recoveries,
                    AVG(actual_wait) as avg_wait,
                    MIN(actual_wait) as min_wait,
                    MAX(actual_wait) as max_wait
                FROM error_recoveries
                WHERE error_code = ?
                """,
                (error_code,),
            )
            row = cursor.fetchone()

            if row:
                return {
                    "error_code": error_code,
                    "total_occurrences": row["total_occurrences"],
                    "successful_recoveries": row["recoveries"] or 0,
                    "recovery_rate": (
                        (row["recoveries"] / row["total_occurrences"] * 100)
                        if row["total_occurrences"] > 0
                        else 0
                    ),
                    "avg_wait_seconds": round(row["avg_wait"] or 0, 1),
                    "min_wait_seconds": round(row["min_wait"] or 0, 1),
                    "max_wait_seconds": round(row["max_wait"] or 0, 1),
                }

        return {"error_code": error_code, "total_occurrences": 0}

    def _get_classified_error(self, context: ErrorLearningContext) -> ClassifiedError:
        """Extract ClassifiedError from context."""
        if isinstance(context.error, ClassificationResult):
            return context.error.primary
        return context.error

    def _record_error_pattern(self, context: ErrorLearningContext) -> None:
        """Record an error occurrence as a pattern.

        Args:
            context: Error context to record.
        """
        if self._store is None:
            return

        error = self._get_classified_error(context)

        # Generate pattern name from error code and category
        pattern_name = f"{error.error_code.value}:{error.category.value}"

        self._store.record_pattern(
            pattern_type="error",
            pattern_name=pattern_name,
            description=error.message,
            context_tags=[
                f"error:{error.error_code.value}",
                f"category:{error.category.value}",
                f"retriable:{error.retriable}",
            ],
            suggested_action=(
                f"Wait {error.suggested_wait_seconds}s before retry"
                if error.suggested_wait_seconds
                else None
            ),
        )

    def _get_learned_wait(self, context: ErrorLearningContext) -> float | None:
        """Get learned wait time for an error.

        Args:
            context: Error context.

        Returns:
            Learned wait time in seconds, or None if not enough data.
        """
        if self._store is None:
            return None

        error = self._get_classified_error(context)

        learned = self._store.get_learned_wait_time(
            error_code=error.error_code.value,
            model=context.model,
            min_samples=self._config.min_samples,
        )

        if learned is not None:
            # Apply bounds
            learned = max(self._config.min_wait_time, learned)
            learned = min(self._config.max_wait_time, learned)

        return learned

    def _get_context_key(self, context: ErrorLearningContext) -> str:
        """Generate a unique key for tracking context."""
        return f"{context.job_id}:{context.sheet_num}:{context.error_code.value}"


# =============================================================================
# Integration helper for ErrorClassifier
# =============================================================================


def wrap_classifier_with_learning(
    classifier: "ErrorClassifier",
    global_store: "GlobalLearningStore | None" = None,
) -> tuple["ErrorClassifier", ErrorLearningHooks]:
    """Wrap an ErrorClassifier with learning hooks.

    This is a convenience function that creates learning hooks and
    returns them alongside the classifier for easy integration.

    Args:
        classifier: The ErrorClassifier to wrap.
        global_store: Global learning store for persistence.

    Returns:
        Tuple of (classifier, hooks) for use in the baton.
    """
    # Import here to avoid circular imports

    hooks = ErrorLearningHooks(global_store)
    return classifier, hooks


# =============================================================================
# Convenience function for recording recovery at baton level
# =============================================================================


def record_error_recovery(
    global_store: "GlobalLearningStore | None",
    error: ClassifiedError | ClassificationResult,
    actual_wait: float,
    success: bool,
    model: str | None = None,
) -> None:
    """Record an error recovery to the global store.

    Convenience function for use in the baton when a recovery is attempted.

    Args:
        global_store: Global learning store (no-op if None).
        error: The error that was recovered from.
        actual_wait: Actual time waited in seconds.
        success: Whether recovery succeeded.
        model: Optional model name.
    """
    if global_store is None:
        return

    error_code = (
        error.primary.error_code.value
        if isinstance(error, ClassificationResult)
        else error.error_code.value
    )

    suggested_wait = (
        error.primary.suggested_wait_seconds
        if isinstance(error, ClassificationResult)
        else error.suggested_wait_seconds
    ) or 0

    global_store.record_error_recovery(
        error_code=error_code,
        suggested_wait=suggested_wait,
        actual_wait=actual_wait,
        recovery_success=success,
        model=model,
    )
