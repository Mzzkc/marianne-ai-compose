"""Pattern lifecycle and promotion automation for GlobalLearningStore.

Provides automated pattern lifecycle transitions based on effectiveness metrics:
- promote_ready_patterns: Auto-promote patterns from PENDING to ACTIVE/QUARANTINED
- update_quarantine_status: Manual status updates

Lifecycle transitions:
- PENDING → ACTIVE: occurrences >= 3 AND effectiveness > 0.60
- PENDING → QUARANTINED: occurrences >= 3 AND effectiveness < 0.35
- ACTIVE → QUARANTINED: effectiveness drops below 0.30 after validation
- QUARANTINED → VALIDATED: Manual review and approval

Evolution v25 Candidate 2: This module implements the missing feedback loop
that allows patterns to mature beyond baseline effectiveness through
application → measurement → promotion.
"""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import TYPE_CHECKING

from mozart.core.logging import MozartLogger
from mozart.learning.store.base import _logger
from mozart.learning.store.models import QuarantineStatus

if TYPE_CHECKING:
    from mozart.learning.store.patterns_query import PatternQueryProtocol

    _LifecycleBase = PatternQueryProtocol
else:
    _LifecycleBase = object


# Promotion thresholds (tunable parameters)
MIN_OCCURRENCES_FOR_PROMOTION = 3
PROMOTION_EFFECTIVENESS_THRESHOLD = 0.60
QUARANTINE_EFFECTIVENESS_THRESHOLD = 0.35
DEGRADATION_THRESHOLD = 0.30  # Active patterns drop below this → quarantined


class PatternLifecycleMixin(_LifecycleBase):
    """Mixin providing pattern lifecycle and promotion automation.

    This mixin requires that the composed class provides:
    - _get_connection(): Context manager yielding sqlite3.Connection
    - get_pattern_by_id(): For pattern lookup (from PatternQueryMixin)
    """

    _logger: MozartLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]

    def promote_ready_patterns(self) -> dict[str, list[str]]:
        """Auto-promote or quarantine patterns based on effectiveness thresholds.

        Queries all PENDING patterns and transitions them based on criteria:
        - PENDING → ACTIVE: occurrences >= 3 AND effectiveness > 0.60
        - PENDING → QUARANTINED: occurrences >= 3 AND effectiveness < 0.35

        Also checks ACTIVE patterns for degradation:
        - ACTIVE → QUARANTINED: effectiveness < 0.30

        Returns:
            Dict with keys:
            - "promoted": List of pattern IDs promoted to ACTIVE
            - "quarantined": List of pattern IDs moved to QUARANTINED
            - "degraded": List of pattern IDs degraded from ACTIVE to QUARANTINED
        """
        promoted: list[str] = []
        quarantined: list[str] = []
        degraded: list[str] = []

        with self._get_connection() as conn:
            # Query PENDING patterns with enough data for promotion decision
            cursor = conn.execute(
                """
                SELECT id, effectiveness_score, occurrence_count,
                       led_to_success_count, led_to_failure_count
                FROM patterns
                WHERE quarantine_status = ?
                AND (led_to_success_count + led_to_failure_count) >= ?
                """,
                (QuarantineStatus.PENDING.value, MIN_OCCURRENCES_FOR_PROMOTION),
            )
            pending_patterns = cursor.fetchall()

            for row in pending_patterns:
                pattern_id = row["id"]
                effectiveness = row["effectiveness_score"]
                total_applications = row["led_to_success_count"] + row["led_to_failure_count"]

                if total_applications < MIN_OCCURRENCES_FOR_PROMOTION:
                    # Safety check (should be filtered by query, but explicit is better)
                    continue

                if effectiveness >= PROMOTION_EFFECTIVENESS_THRESHOLD:
                    # Promote to ACTIVE
                    conn.execute(
                        """
                        UPDATE patterns SET
                            quarantine_status = ?,
                            validated_at = ?
                        WHERE id = ?
                        """,
                        (QuarantineStatus.VALIDATED.value, datetime.now().isoformat(), pattern_id),
                    )
                    promoted.append(pattern_id)
                    _logger.info(
                        "pattern_lifecycle.promoted",
                        pattern_id=pattern_id,
                        effectiveness=round(effectiveness, 3),
                        occurrences=total_applications,
                        threshold=PROMOTION_EFFECTIVENESS_THRESHOLD,
                    )
                elif effectiveness < QUARANTINE_EFFECTIVENESS_THRESHOLD:
                    # Quarantine (ineffective)
                    conn.execute(
                        """
                        UPDATE patterns SET
                            quarantine_status = ?,
                            quarantined_at = ?
                        WHERE id = ?
                        """,
                        (
                            QuarantineStatus.QUARANTINED.value,
                            datetime.now().isoformat(),
                            pattern_id,
                        ),
                    )
                    quarantined.append(pattern_id)
                    _logger.info(
                        "pattern_lifecycle.quarantined",
                        pattern_id=pattern_id,
                        effectiveness=round(effectiveness, 3),
                        occurrences=total_applications,
                        threshold=QUARANTINE_EFFECTIVENESS_THRESHOLD,
                    )

            # Check ACTIVE/VALIDATED patterns for degradation
            cursor = conn.execute(
                """
                SELECT id, effectiveness_score,
                       led_to_success_count, led_to_failure_count
                FROM patterns
                WHERE quarantine_status IN (?, ?)
                AND effectiveness_score < ?
                AND (led_to_success_count + led_to_failure_count) >= ?
                """,
                (
                    QuarantineStatus.VALIDATED.value,
                    "active",  # Legacy value before v25
                    DEGRADATION_THRESHOLD,
                    MIN_OCCURRENCES_FOR_PROMOTION,
                ),
            )
            active_patterns = cursor.fetchall()

            for row in active_patterns:
                pattern_id = row["id"]
                effectiveness = row["effectiveness_score"]
                total_applications = row["led_to_success_count"] + row["led_to_failure_count"]

                conn.execute(
                    """
                    UPDATE patterns SET
                        quarantine_status = ?,
                        quarantined_at = ?
                    WHERE id = ?
                    """,
                    (QuarantineStatus.QUARANTINED.value, datetime.now().isoformat(), pattern_id),
                )
                degraded.append(pattern_id)
                _logger.warning(
                    "pattern_lifecycle.degraded",
                    pattern_id=pattern_id,
                    effectiveness=round(effectiveness, 3),
                    occurrences=total_applications,
                    threshold=DEGRADATION_THRESHOLD,
                )

        if promoted or quarantined or degraded:
            _logger.info(
                "pattern_lifecycle.promotion_cycle_complete",
                promoted_count=len(promoted),
                quarantined_count=len(quarantined),
                degraded_count=len(degraded),
            )

        return {
            "promoted": promoted,
            "quarantined": quarantined,
            "degraded": degraded,
        }

    def update_quarantine_status(
        self,
        pattern_id: str,
        new_status: QuarantineStatus,
    ) -> bool:
        """Manually update a pattern's quarantine status.

        This is for operator-driven lifecycle transitions that bypass
        automatic thresholds (e.g., promoting a validated pattern after
        manual review, or retiring an obsolete pattern).

        Args:
            pattern_id: The pattern to update.
            new_status: New quarantine status.

        Returns:
            True if updated, False if pattern not found.
        """
        pattern = self.get_pattern_by_id(pattern_id)
        if not pattern:
            _logger.warning(
                "pattern_lifecycle.update_not_found",
                pattern_id=pattern_id,
            )
            return False

        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            # Update status and appropriate timestamp field
            if new_status == QuarantineStatus.VALIDATED:
                conn.execute(
                    """
                    UPDATE patterns SET
                        quarantine_status = ?,
                        validated_at = ?
                    WHERE id = ?
                    """,
                    (new_status.value, now, pattern_id),
                )
            elif new_status == QuarantineStatus.QUARANTINED:
                conn.execute(
                    """
                    UPDATE patterns SET
                        quarantine_status = ?,
                        quarantined_at = ?
                    WHERE id = ?
                    """,
                    (new_status.value, now, pattern_id),
                )
            else:
                # PENDING or RETIRED — no specific timestamp
                conn.execute(
                    """
                    UPDATE patterns SET
                        quarantine_status = ?
                    WHERE id = ?
                    """,
                    (new_status.value, pattern_id),
                )

        _logger.info(
            "pattern_lifecycle.status_updated",
            pattern_id=pattern_id,
            old_status=pattern.quarantine_status.value,
            new_status=new_status.value,
        )
        return True


__all__ = [
    "PatternLifecycleMixin",
    "MIN_OCCURRENCES_FOR_PROMOTION",
    "PROMOTION_EFFECTIVENESS_THRESHOLD",
    "QUARANTINE_EFFECTIVENESS_THRESHOLD",
    "DEGRADATION_THRESHOLD",
]
