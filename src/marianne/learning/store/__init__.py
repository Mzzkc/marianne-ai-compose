"""Global learning store with modular mixins.

This package provides the GlobalLearningStore class, which is composed from
multiple mixins, each handling a specific domain of functionality:

- PatternMixin: Pattern recording, effectiveness tracking, quarantine lifecycle
- ExecutionMixin: Execution outcome recording, statistics, similarity matching
- RateLimitMixin: Cross-workspace rate limit coordination
- DriftMixin: Effectiveness and epistemic drift detection, auto-retirement
- EscalationMixin: Escalation decision recording and learning
- BudgetMixin: Exploration budget management, entropy response

The base class (GlobalLearningStoreBase) provides:
- SQLite connection management with WAL mode
- Schema creation and migration
- Hashing utilities for workspace/job identification

Usage:
    from marianne.learning.store import GlobalLearningStore

    store = GlobalLearningStore()  # Uses default ~/.marianne/global-learning.db
    store = GlobalLearningStore(db_path=Path("/custom/path.db"))

The composed class inherits from all mixins with GlobalLearningStoreBase listed
LAST in the MRO to ensure proper initialization order. This allows mixins to
access self._get_connection() and self._logger provided by the base class.

Modularization Note:
    This package was extracted from the monolithic global_store.py (~5136 LOC)
    as part of the D2 modularization effort. The original file contained an
    82-method class that has been split into focused mixins for maintainability.
"""

# Re-export all models for convenient access
from pathlib import Path

# Re-export the base class and default path
from marianne.learning.store.base import (
    DEFAULT_GLOBAL_STORE_PATH,
    GlobalLearningStoreBase,
)
from marianne.learning.store.budget import BudgetMixin
from marianne.learning.store.drift import DriftMixin
from marianne.learning.store.escalation import EscalationMixin
from marianne.learning.store.executions import ExecutionMixin
from marianne.learning.store.models import (
    DriftMetrics,
    EntropyResponseRecord,
    EpistemicDriftMetrics,
    ErrorRecoveryRecord,
    EscalationDecisionRecord,
    EvolutionTrajectoryEntry,
    ExecutionRecord,
    ExplorationBudgetRecord,
    PatternDiscoveryEvent,
    PatternEntropyMetrics,
    PatternRecord,
    QuarantineStatus,
    RateLimitEvent,
    SuccessFactors,
)

# Import all mixins
from marianne.learning.store.patterns import PatternMixin
from marianne.learning.store.patterns_lifecycle import (
    DEGRADATION_THRESHOLD,
    MIN_OCCURRENCES_FOR_PROMOTION,
    PROMOTION_EFFECTIVENESS_THRESHOLD,
    QUARANTINE_EFFECTIVENESS_THRESHOLD,
    PatternLifecycleMixin,
)
from marianne.learning.store.rate_limits import RateLimitMixin


class GlobalLearningStore(  # type: ignore[misc]
    PatternMixin,
    ExecutionMixin,
    RateLimitMixin,
    DriftMixin,
    EscalationMixin,
    BudgetMixin,
    PatternLifecycleMixin,
    GlobalLearningStoreBase,
):
    """Global learning store combining all mixins.

    This is the primary interface for Marianne's cross-workspace learning system.
    It provides persistent storage for execution outcomes, detected patterns,
    error recovery data, and learning metrics across all Marianne workspaces.

    The class is composed from multiple mixins, each providing domain-specific
    functionality. The base class (listed last for proper MRO) provides:
    - SQLite connection management with WAL mode for concurrent access
    - Schema creation and version migration
    - Hashing utilities for workspace and job identification

    Mixin Capabilities:
        PatternMixin:
            - record_pattern(), get_patterns(), get_pattern_by_id()
            - record_pattern_application(), update_pattern_effectiveness()
            - quarantine lifecycle: quarantine_pattern(), validate_pattern()
            - trust scoring, success factor analysis
            - pattern discovery broadcasting

        ExecutionMixin:
            - record_outcome() for sheet execution outcomes
            - get_execution_stats(), get_recent_executions()
            - get_similar_executions() for learning activation
            - workspace clustering for cross-workspace correlation

        RateLimitMixin:
            - record_rate_limit_event() for cross-workspace coordination
            - get_recent_rate_limits() to check before API calls
            - Enables parallel jobs to avoid hitting same limits

        DriftMixin:
            - calculate_drift_metrics() for effectiveness drift
            - detect_epistemic_drift() for belief-level monitoring
            - auto_retire_drifting_patterns() for lifecycle management
            - get_pattern_evolution_trajectory() for historical analysis

        EscalationMixin:
            - record_escalation_decision() when handlers respond
            - get_similar_escalation() for pattern-based suggestions
            - Closes the learning loop for escalation handling

        BudgetMixin:
            - get_exploration_budget(), update_exploration_budget()
            - record_entropy_response() for diversity injection
            - Dynamic budget with floor/ceiling to prevent over-convergence

    Example:
        >>> from marianne.learning.store import GlobalLearningStore
        >>> store = GlobalLearningStore()
        >>>
        >>> # Record a pattern
        >>> store.record_pattern(
        ...     pattern_type="rate_limit_recovery",
        ...     pattern_content={"action": "exponential_backoff"},
        ...     context={"error_code": "E101"},
        ...     source_job="job-123",
        ... )
        >>>
        >>> # Query execution statistics
        >>> stats = store.get_execution_stats()
        >>> print(f"Total executions: {stats['total_executions']}")

    Attributes:
        db_path: Path to the SQLite database file.
        _logger: Module logger instance for consistent logging.

    Note:
        The database uses WAL mode for safe concurrent access from multiple
        Marianne jobs. Schema migrations are applied automatically when the
        store is initialized.
    """

    pass


# Convenience function for getting the singleton store
import threading

_global_store: GlobalLearningStore | None = None
_global_store_lock = threading.Lock()


def get_global_store(
    db_path: Path | None = None,
) -> GlobalLearningStore:
    """Get or create the global learning store singleton.

    This function provides a convenient singleton accessor for the GlobalLearningStore.
    It ensures only one store instance exists per database path, avoiding the overhead
    of creating multiple connections to the same SQLite database.

    Args:
        db_path: Optional custom database path. If None, uses the default
            path at ~/.marianne/global-learning.db.

    Returns:
        The GlobalLearningStore singleton instance.

    Example:
        >>> store = get_global_store()  # Uses default path
        >>> store = get_global_store(Path("/custom/path.db"))  # Custom path
    """
    global _global_store

    with _global_store_lock:
        if _global_store is None or (
            db_path is not None and _global_store.db_path != db_path
        ):
            _global_store = GlobalLearningStore(db_path)

    return _global_store


__all__ = [
    # Main class
    "GlobalLearningStore",
    # Singleton accessor
    "get_global_store",
    # Base class and constants
    "GlobalLearningStoreBase",
    "DEFAULT_GLOBAL_STORE_PATH",
    # Mixins (for advanced usage/testing)
    "PatternMixin",
    "ExecutionMixin",
    "RateLimitMixin",
    "DriftMixin",
    "EscalationMixin",
    "BudgetMixin",
    "PatternLifecycleMixin",
    # Pattern lifecycle constants
    "MIN_OCCURRENCES_FOR_PROMOTION",
    "PROMOTION_EFFECTIVENESS_THRESHOLD",
    "QUARANTINE_EFFECTIVENESS_THRESHOLD",
    "DEGRADATION_THRESHOLD",
    # Models - Enums
    "QuarantineStatus",
    # Models - Core records
    "PatternRecord",
    "ExecutionRecord",
    "ErrorRecoveryRecord",
    "RateLimitEvent",
    "EscalationDecisionRecord",
    "PatternDiscoveryEvent",
    # Models - Metrics and analysis
    "DriftMetrics",
    "EpistemicDriftMetrics",
    "SuccessFactors",
    "EvolutionTrajectoryEntry",
    # Models - Budget management
    "PatternEntropyMetrics",
    "ExplorationBudgetRecord",
    "EntropyResponseRecord",
]
