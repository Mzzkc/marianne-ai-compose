"""Pattern success factors (metacognitive reflection) mixin for GlobalLearningStore.

Provides methods for understanding WHY patterns succeed:
- update_success_factors: Record context conditions of successful application
- get_success_factors: Retrieve a pattern's success factors
- analyze_pattern_why: Generate human-readable WHY analysis
- get_patterns_with_why: Query patterns with their analysis

v22 Evolution: Metacognitive Pattern Reflection
"""

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from marianne.core.logging import MarianneLogger
from marianne.learning.store.base import _logger
from marianne.learning.store.models import PatternRecord, SuccessFactors

if TYPE_CHECKING:
    from marianne.learning.store.patterns_query import PatternQueryProtocol

    _SuccessFactorsBase = PatternQueryProtocol
else:
    _SuccessFactorsBase = object


class PatternSuccessFactorsMixin(_SuccessFactorsBase):
    """Mixin providing pattern success factor analysis methods.

    This mixin requires that the composed class provides:
    - _get_connection(): Context manager yielding sqlite3.Connection
    - get_pattern_by_id(): For pattern lookup (from PatternQueryMixin)
    - _row_to_pattern_record(): For row conversion (from PatternQueryMixin)
    - analyze_pattern_why(): Self-reference for get_patterns_with_why
    """

    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]

    def update_success_factors(
        self,
        pattern_id: str,
        validation_types: list[str] | None = None,
        error_categories: list[str] | None = None,
        prior_sheet_status: str | None = None,
        retry_iteration: int = 0,
        escalation_was_pending: bool = False,
        grounding_confidence: float | None = None,
    ) -> SuccessFactors | None:
        """Update success factors for a pattern based on a successful application.

        Captures the WHY behind pattern success — the context conditions
        that were present when the pattern worked.

        Args:
            pattern_id: The pattern that succeeded.
            validation_types: Validation types active (file, regex, artifact, etc.)
            error_categories: Error categories present (rate_limit, auth, etc.)
            prior_sheet_status: Status of prior sheet (completed, failed, skipped)
            retry_iteration: Which retry this success occurred on (0 = first)
            escalation_was_pending: Whether escalation was pending
            grounding_confidence: Grounding confidence if external validation present

        Returns:
            Updated SuccessFactors, or None if pattern not found.
        """
        pattern = self.get_pattern_by_id(pattern_id)
        if not pattern:
            _logger.warning(
                "update_success_factors_skipped",
                pattern_id=pattern_id,
                reason="pattern_not_found",
            )
            return None

        now = datetime.now()
        time_bucket = SuccessFactors.get_time_bucket(now.hour)

        if pattern.success_factors:
            factors: SuccessFactors = pattern.success_factors
            factors.occurrence_count += 1

            if validation_types:
                existing = set(factors.validation_types)
                existing.update(validation_types)
                factors.validation_types = sorted(existing)

            if error_categories:
                existing_errors = set(factors.error_categories)
                existing_errors.update(error_categories)
                factors.error_categories = sorted(existing_errors)

            if prior_sheet_status:
                factors.prior_sheet_status = prior_sheet_status
            factors.time_of_day_bucket = time_bucket
            factors.retry_iteration = retry_iteration
            factors.escalation_was_pending = escalation_was_pending
            if grounding_confidence is not None:
                factors.grounding_confidence = grounding_confidence

            total = pattern.led_to_success_count + pattern.led_to_failure_count
            if total > 0:
                factors.success_rate = pattern.led_to_success_count / total
        else:
            factors = SuccessFactors(
                validation_types=validation_types or [],
                error_categories=error_categories or [],
                prior_sheet_status=prior_sheet_status,
                time_of_day_bucket=time_bucket,
                retry_iteration=retry_iteration,
                escalation_was_pending=escalation_was_pending,
                grounding_confidence=grounding_confidence,
                occurrence_count=1,
                success_rate=1.0,
            )

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE patterns SET
                    success_factors = ?,
                    success_factors_updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(factors.to_dict()), now.isoformat(), pattern_id),
            )

        _logger.debug(
            f"Updated success factors for {pattern_id}: "
            f"{factors.occurrence_count} observations, "
            f"success_rate={factors.success_rate:.2f}"
        )
        return factors

    def get_success_factors(self, pattern_id: str) -> SuccessFactors | None:
        """Get the success factors for a pattern.

        Args:
            pattern_id: The pattern ID to get factors for.

        Returns:
            SuccessFactors if the pattern has captured factors, None otherwise.
        """
        pattern = self.get_pattern_by_id(pattern_id)
        if not pattern:
            return None
        return pattern.success_factors

    def analyze_pattern_why(self, pattern_id: str) -> dict[str, Any]:
        """Analyze WHY a pattern succeeds with structured explanation.

        Args:
            pattern_id: The pattern to analyze.

        Returns:
            Dictionary with analysis results including factors_summary,
            key_conditions, confidence, and recommendations.
        """
        pattern = self.get_pattern_by_id(pattern_id)
        if not pattern:
            return {"error": f"Pattern {pattern_id} not found"}

        result: dict[str, Any] = {
            "pattern_name": pattern.pattern_name,
            "pattern_type": pattern.pattern_type,
            "has_factors": pattern.success_factors is not None,
            "trust_score": pattern.trust_score,
            "effectiveness_score": pattern.effectiveness_score,
        }

        if not pattern.success_factors:
            result["factors_summary"] = "No success factors captured yet"
            result["key_conditions"] = []
            result["confidence"] = 0.0
            result["recommendations"] = [
                "Apply this pattern more times to capture success factors"
            ]
            return result

        factors = pattern.success_factors

        summaries = []
        if factors.validation_types:
            summaries.append(f"validation types: {', '.join(factors.validation_types)}")
        if factors.error_categories:
            summaries.append(f"error categories: {', '.join(factors.error_categories)}")
        if factors.time_of_day_bucket:
            summaries.append(f"typically succeeds in: {factors.time_of_day_bucket}")
        if factors.prior_sheet_status:
            summaries.append(f"prior sheet was: {factors.prior_sheet_status}")

        result["factors_summary"] = "; ".join(summaries) if summaries else "Context captured"

        key_conditions = []
        if factors.grounding_confidence and factors.grounding_confidence > 0.7:
            key_conditions.append(
                f"High grounding confidence ({factors.grounding_confidence:.2f})"
            )
        if factors.retry_iteration == 0:
            key_conditions.append("Succeeds on first attempt")
        elif factors.retry_iteration > 0:
            key_conditions.append(f"Often succeeds after {factors.retry_iteration} retries")
        if factors.validation_types:
            key_conditions.append(f"Works with {len(factors.validation_types)} validation types")
        if not factors.escalation_was_pending:
            key_conditions.append("Succeeds without escalation")

        result["key_conditions"] = key_conditions

        observation_confidence = min(1.0, factors.occurrence_count / 10)
        result["confidence"] = observation_confidence * factors.success_rate

        recommendations = []
        if factors.occurrence_count < 5:
            recommendations.append("Need more observations for reliable analysis")
        if factors.success_rate > 0.8:
            recommendations.append("High confidence pattern - consider for auto-apply")
        if factors.success_rate < 0.5:
            recommendations.append("Low success rate - review pattern relevance")
        if factors.time_of_day_bucket:
            recommendations.append(f"Best applied during {factors.time_of_day_bucket}")

        result["recommendations"] = recommendations
        result["observation_count"] = factors.occurrence_count
        result["success_rate"] = factors.success_rate

        return result

    def get_patterns_with_why(
        self,
        min_observations: int = 1,
        limit: int = 20,
    ) -> list[tuple[PatternRecord, dict[str, Any]]]:
        """Get patterns with their WHY analysis.

        Args:
            min_observations: Minimum success factor observations required.
            limit: Maximum number of patterns to return.

        Returns:
            List of (PatternRecord, analysis_dict) tuples.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM patterns
                WHERE success_factors IS NOT NULL
                ORDER BY priority_score DESC, trust_score DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        results = []
        for row in rows:
            pattern = self._row_to_pattern_record(row)
            if (
                pattern.success_factors
                and pattern.success_factors.occurrence_count >= min_observations
            ):
                analysis = self.analyze_pattern_why(pattern.id)
                results.append((pattern, analysis))

        return results
