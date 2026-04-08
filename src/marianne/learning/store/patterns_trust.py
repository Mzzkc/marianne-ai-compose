"""Pattern trust scoring mixin for GlobalLearningStore.

Provides trust score calculation and querying methods:
- calculate_trust_score: Calculate and persist trust score
- update_trust_score: Incremental trust updates
- get_high_trust_patterns: Query high-trust patterns
- get_low_trust_patterns: Query low-trust patterns
- get_patterns_for_auto_apply: Auto-apply eligibility check
- recalculate_all_trust_scores: Batch recalculation
"""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import TYPE_CHECKING

from marianne.core.logging import MarianneLogger
from marianne.learning.store.base import SQLParam, _logger
from marianne.learning.store.models import PatternRecord, QuarantineStatus

if TYPE_CHECKING:
    from marianne.learning.store.patterns_query import PatternQueryProtocol

    _TrustBase = PatternQueryProtocol
else:
    _TrustBase = object


class PatternTrustMixin(_TrustBase):
    """Mixin providing pattern trust scoring methods.

    This mixin requires that the composed class provides:
    - _get_connection(): Context manager yielding sqlite3.Connection
    - get_pattern_by_id(): For pattern lookup (from PatternQueryMixin)
    - get_patterns(): For filtered queries (from PatternQueryMixin)
    - _row_to_pattern_record(): For row conversion (from PatternQueryMixin)
    """

    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]

    def calculate_trust_score(self, pattern_id: str) -> float | None:
        """Calculate and update trust score for a pattern.

        Trust score formula:
            trust: float = 0.5 + (success_rate * 0.3) - (failure_rate * 0.4) + (age_factor * 0.2)

        Quarantined patterns get a -0.2 penalty.
        Validated patterns get a +0.1 bonus.

        Args:
            pattern_id: The pattern ID to calculate trust for.

        Returns:
            New trust score (0.0-1.0), or None if pattern not found.
        """
        pattern = self.get_pattern_by_id(pattern_id)
        if not pattern:
            return None

        total = pattern.occurrence_count
        if total == 0:
            total = 1

        success_rate = min(1.0, pattern.led_to_success_count / total)
        failure_rate = min(1.0, pattern.led_to_failure_count / total)

        now = datetime.now()
        days_since_confirmed = (now - pattern.last_confirmed).days
        age_factor = 0.9 ** (days_since_confirmed / 30.0)

        trust: float = 0.5 + (success_rate * 0.3) - (failure_rate * 0.4) + (age_factor * 0.2)

        if pattern.quarantine_status == QuarantineStatus.QUARANTINED:
            trust -= 0.2

        if pattern.quarantine_status == QuarantineStatus.VALIDATED:
            trust += 0.1

        trust = max(0.0, min(1.0, trust))

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE patterns SET
                    trust_score = ?,
                    trust_calculation_date = ?
                WHERE id = ?
                """,
                (trust, now.isoformat(), pattern_id),
            )

        _logger.debug("trust_score_calculated", pattern_id=pattern_id, trust=round(trust, 3))
        return trust

    def update_trust_score(self, pattern_id: str, delta: float) -> float | None:
        """Update trust score by a delta amount.

        Args:
            pattern_id: The pattern ID to update.
            delta: Amount to add to trust score (can be negative).

        Returns:
            New trust score after update, or None if pattern not found.
        """
        pattern = self.get_pattern_by_id(pattern_id)
        if not pattern:
            return None

        new_trust: float = max(0.0, min(1.0, float(pattern.trust_score) + delta))

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE patterns SET
                    trust_score = ?,
                    trust_calculation_date = ?
                WHERE id = ?
                """,
                (new_trust, datetime.now().isoformat(), pattern_id),
            )

        _logger.debug(
            "trust_score_updated",
            pattern_id=pattern_id,
            old_trust=round(float(pattern.trust_score), 3),
            new_trust=round(new_trust, 3),
        )
        return new_trust

    def get_high_trust_patterns(
        self,
        threshold: float = 0.7,
        limit: int = 50,
    ) -> list[PatternRecord]:
        """Get patterns with high trust scores.

        Args:
            threshold: Minimum trust score to include.
            limit: Maximum number of patterns to return.

        Returns:
            List of high-trust PatternRecord objects.
        """
        return self.get_patterns(
            min_priority=0.0,
            min_trust=threshold,
            limit=limit,
        )

    def get_low_trust_patterns(
        self,
        threshold: float = 0.3,
        limit: int = 50,
    ) -> list[PatternRecord]:
        """Get patterns with low trust scores.

        Args:
            threshold: Maximum trust score to include.
            limit: Maximum number of patterns to return.

        Returns:
            List of low-trust PatternRecord objects.
        """
        return self.get_patterns(
            min_priority=0.0,
            max_trust=threshold,
            limit=limit,
        )

    def get_patterns_for_auto_apply(
        self,
        trust_threshold: float = 0.85,
        require_validated: bool = True,
        limit: int = 3,
        context_tags: list[str] | None = None,
    ) -> list[PatternRecord]:
        """Get patterns eligible for autonomous application.

        Criteria:
        1. Trust score >= trust_threshold (default 0.85)
        2. Quarantine status == VALIDATED (if require_validated=True)
        3. Pattern is not retired

        Args:
            trust_threshold: Minimum trust score for auto-apply.
            require_validated: Require VALIDATED quarantine status.
            limit: Maximum patterns to return.
            context_tags: Optional context tags to filter by relevance.

        Returns:
            List of PatternRecord objects eligible for auto-apply.
        """
        with self._get_connection() as conn:
            query = """
                SELECT * FROM patterns
                WHERE trust_score >= ?
                AND quarantine_status != 'retired'
            """
            params: list[SQLParam] = [trust_threshold]

            if require_validated:
                query += " AND quarantine_status = 'validated'"

            query += " ORDER BY trust_score DESC, priority_score DESC"
            query += " LIMIT ?"
            params.append(limit * 2)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        patterns = [self._row_to_pattern_record(row) for row in rows]
        if context_tags:
            tags_set = set(context_tags)
            patterns = [
                p for p in patterns
                if tags_set.intersection(set(p.context_tags))
            ]

        patterns = patterns[:limit]

        _logger.debug(
            "auto_apply_patterns_found",
            count=len(patterns),
            threshold=trust_threshold,
            require_validated=require_validated,
        )
        return patterns

    def recalculate_all_trust_scores(self) -> int:
        """Recalculate trust scores for all patterns.

        Returns:
            Number of patterns updated.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT id FROM patterns")
            pattern_ids = [row["id"] for row in cursor.fetchall()]

        updated = 0
        for pattern_id in pattern_ids:
            result = self.calculate_trust_score(pattern_id)
            if result is not None:
                updated += 1

        _logger.info("trust_scores_recalculated", count=updated)
        return updated
