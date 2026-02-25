"""Pattern-related mixin for GlobalLearningStore.

This module provides the PatternMixin class that handles all pattern-related
operations including:
- Recording and updating patterns
- Pattern application tracking
- Effectiveness and priority calculations
- Quarantine lifecycle management
- Trust scoring
- Success factor analysis (metacognitive reflection)
- Pattern discovery broadcasting

Extracted from global_store.py as part of the modularization effort.

Architecture:
    PatternMixin is now composed from focused sub-mixins, each handling a
    specific domain of pattern functionality:

    - PatternQueryMixin: Core query operations (get_patterns, get_pattern_by_id, etc.)
    - PatternCrudMixin: Create/update operations and effectiveness calculations
    - PatternQuarantineMixin: Quarantine lifecycle (quarantine, validate, retire)
    - PatternTrustMixin: Trust scoring and auto-apply eligibility
    - PatternSuccessFactorsMixin: Metacognitive reflection (WHY analysis)
    - PatternBroadcastMixin: Real-time pattern discovery broadcasting

    This decomposition improves maintainability while preserving the original
    public API through composition.
"""

from mozart.learning.store.patterns_broadcast import PatternBroadcastMixin
from mozart.learning.store.patterns_crud import PatternCrudMixin
from mozart.learning.store.patterns_lifecycle import PatternLifecycleMixin
from mozart.learning.store.patterns_quarantine import PatternQuarantineMixin
from mozart.learning.store.patterns_query import PatternQueryMixin
from mozart.learning.store.patterns_success_factors import PatternSuccessFactorsMixin
from mozart.learning.store.patterns_trust import PatternTrustMixin


class PatternMixin(
    PatternCrudMixin,
    PatternQueryMixin,
    PatternQuarantineMixin,
    PatternTrustMixin,
    PatternSuccessFactorsMixin,
    PatternBroadcastMixin,
    PatternLifecycleMixin,
):
    """Mixin providing all pattern-related methods for GlobalLearningStore.

    This mixin requires that the composed class provides:
    - _get_connection(): Context manager yielding sqlite3.Connection
    - _logger: Logger instance for logging

    Composed from focused sub-mixins:
    - PatternQueryMixin: get_patterns, get_pattern_by_id, get_pattern_provenance
    - PatternCrudMixin: record_pattern, record_pattern_application, effectiveness
    - PatternQuarantineMixin: quarantine_pattern, validate_pattern, retire_pattern
    - PatternTrustMixin: calculate_trust_score, get_high/low_trust_patterns
    - PatternSuccessFactorsMixin: update_success_factors, analyze_pattern_why
    - PatternBroadcastMixin: record_pattern_discovery, check_recent_pattern_discoveries
    - PatternLifecycleMixin: promote_ready_patterns, update_quarantine_status (v25)
    """

    pass


# Export public API — backward compatible
__all__ = [
    "PatternMixin",
    # Sub-mixins for advanced usage/testing
    "PatternQueryMixin",
    "PatternCrudMixin",
    "PatternQuarantineMixin",
    "PatternTrustMixin",
    "PatternSuccessFactorsMixin",
    "PatternBroadcastMixin",
    "PatternLifecycleMixin",
]
