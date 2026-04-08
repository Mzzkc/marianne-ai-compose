"""Global learning store - re-exports from modular package.

This module provides backward-compatible imports for the GlobalLearningStore class
and all related models. The implementation has been modularized into the
`marianne.learning.store` package.

The original monolithic implementation (~5136 LOC) has been split into focused
mixins for better maintainability:
- models.py: All dataclasses and enums
- base.py: Core connection and schema management
- patterns.py: Pattern recording and quarantine lifecycle
- executions.py: Execution outcome recording
- rate_limits.py: Cross-workspace rate limit coordination
- drift.py: Effectiveness and epistemic drift detection
- escalation.py: Escalation decision recording
- budget.py: Exploration budget management

Usage remains unchanged:
    from marianne.learning.global_store import GlobalLearningStore
    store = GlobalLearningStore()
"""

from marianne.learning.store import (
    DEFAULT_GLOBAL_STORE_PATH,
    BudgetMixin,
    # Models - Metrics and analysis
    DriftMetrics,
    DriftMixin,
    EntropyResponseRecord,
    EpistemicDriftMetrics,
    ErrorRecoveryRecord,
    EscalationDecisionRecord,
    EscalationMixin,
    EvolutionTrajectoryEntry,
    ExecutionMixin,
    ExecutionRecord,
    # Models - Budget management
    ExplorationBudgetRecord,
    # Main class
    GlobalLearningStore,
    # Base class and constants
    GlobalLearningStoreBase,
    PatternDiscoveryEvent,
    # Mixins (for advanced usage/testing)
    PatternMixin,
    # Models - Core records
    PatternRecord,
    # Models - Enums
    QuarantineStatus,
    RateLimitEvent,
    RateLimitMixin,
    SuccessFactors,
    # Singleton accessor
    get_global_store,
)

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
    "ExplorationBudgetRecord",
    "EntropyResponseRecord",
]
