"""Drift detection mixin for the global learning store.

This module contains the DriftMixin class that provides drift detection
and pattern retirement functionality. Drift detection monitors how patterns
change over time in both effectiveness and epistemic confidence.

Evolution v12: Goal Drift Detection - enables proactive pattern health monitoring.
Evolution v21: Epistemic Drift Detection - complements effectiveness drift
               with belief-level monitoring.
Evolution v14: Pattern Auto-Retirement - automated pattern lifecycle management.

Extracted from global_store.py as part of the modularization effort.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Literal

from marianne.core.logging import MarianneLogger

# Import logger from base module for consistency across all mixins
from .base import _logger
from .models import (
    DriftMetrics,
    EpistemicDriftMetrics,
    EvolutionEntryInput,
    EvolutionTrajectoryEntry,
    PatternRecord,
)


class DriftMixin:
    """Mixin providing drift detection and pattern retirement functionality.

    This mixin provides methods for detecting effectiveness drift and epistemic
    drift in patterns, as well as automatic retirement of drifting patterns.

    Effectiveness drift tracks changes in success rates over time.
    Epistemic drift tracks changes in confidence/belief levels over time.

    Requires the following from the composed class:
        - _get_connection() -> context manager yielding sqlite3.Connection
    """

    # Annotations for attributes provided by the composed class (GlobalLearningStoreBase)
    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]

    # =========================================================================
    # v12 Evolution: Goal Drift Detection - Effectiveness Drift
    # =========================================================================

    def calculate_effectiveness_drift(
        self,
        pattern_id: str,
        window_size: int = 5,
        drift_threshold: float = 0.2,
    ) -> DriftMetrics | None:
        """Calculate effectiveness drift for a pattern.

        Compares the effectiveness of a pattern in its recent applications
        vs older applications to detect drift. Patterns that were once
        effective but are now declining may need investigation.

        v12 Evolution: Goal Drift Detection - enables proactive pattern
        health monitoring.

        Formula:
            drift = effectiveness_after - effectiveness_before
            drift_magnitude = |drift|
            weighted_drift = drift_magnitude / avg_grounding_confidence

        A positive drift means the pattern is improving, negative means declining.
        The weighted drift amplifies the signal when grounding confidence is low.

        Args:
            pattern_id: Pattern to analyze.
            window_size: Number of applications per window (default 5).
                        Total applications needed = 2 × window_size.
            drift_threshold: Threshold for flagging drift (default 0.2 = 20%).

        Returns:
            DriftMetrics if enough data exists, None otherwise.
        """
        with self._get_connection() as conn:
            # Get pattern name
            cursor = conn.execute(
                "SELECT pattern_name FROM patterns WHERE id = ?",
                (pattern_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            pattern_name = row["pattern_name"]

            # Fetch 2 × window_size recent applications
            # Ordered by applied_at DESC to get most recent first
            cursor = conn.execute(
                """
                SELECT pattern_led_to_success, grounding_confidence, applied_at
                FROM pattern_applications
                WHERE pattern_id = ?
                ORDER BY applied_at DESC
                LIMIT ?
                """,
                (pattern_id, window_size * 2),
            )
            applications = cursor.fetchall()

            # Need at least 2 × window_size applications for comparison
            if len(applications) < window_size * 2:
                _logger.debug(
                    f"Pattern {pattern_id} has {len(applications)} applications, "
                    f"need {window_size * 2} for drift analysis"
                )
                return None

            # Split into recent (first window_size) and older (second window_size)
            recent_apps = applications[:window_size]
            older_apps = applications[window_size : window_size * 2]

            # Calculate effectiveness for each window
            # effectiveness = success_rate with Laplace smoothing
            def calc_effectiveness(apps: list[dict[str, Any]]) -> tuple[float, list[float]]:
                successes = sum(1 for a in apps if a["pattern_led_to_success"])
                eff = (successes + 0.5) / (len(apps) + 1)  # Laplace smoothing
                grounding_vals = [
                    a["grounding_confidence"]
                    for a in apps
                    if a["grounding_confidence"] is not None
                ]
                return eff, grounding_vals

            eff_after, grounding_recent = calc_effectiveness(recent_apps)
            eff_before, grounding_older = calc_effectiveness(older_apps)

            # Calculate average grounding confidence across all applications
            all_grounding = grounding_recent + grounding_older
            avg_grounding = sum(all_grounding) / len(all_grounding) if all_grounding else 1.0

            # Calculate drift
            drift = eff_after - eff_before
            drift_magnitude = abs(drift)

            # Determine direction
            if drift > 0.05:  # Small threshold to avoid noise
                drift_direction = "positive"
            elif drift < -0.05:
                drift_direction = "negative"
            else:
                drift_direction = "stable"

            # Check if threshold exceeded (weighted by grounding)
            # Lower grounding confidence amplifies the drift signal
            weighted_magnitude = drift_magnitude / max(avg_grounding, 0.5)
            threshold_exceeded = weighted_magnitude > drift_threshold

            return DriftMetrics(
                pattern_id=pattern_id,
                pattern_name=pattern_name,
                window_size=window_size,
                effectiveness_before=eff_before,
                effectiveness_after=eff_after,
                grounding_confidence_avg=avg_grounding,
                drift_magnitude=drift_magnitude,
                drift_direction=drift_direction,
                applications_analyzed=len(applications),
                threshold_exceeded=threshold_exceeded,
            )

    def get_drifting_patterns(
        self,
        drift_threshold: float = 0.2,
        window_size: int = 5,
        limit: int = 20,
    ) -> list[DriftMetrics]:
        """Get all patterns with significant drift.

        Scans all patterns with enough application history and returns
        those that exceed the drift threshold.

        v12 Evolution: Goal Drift Detection - enables CLI display of
        drifting patterns for operator review.

        Args:
            drift_threshold: Minimum drift to include (default 0.2).
            window_size: Applications per window (default 5).
            limit: Maximum patterns to return.

        Returns:
            List of DriftMetrics for drifting patterns, sorted by
            drift_magnitude descending.
        """
        drifting: list[DriftMetrics] = []

        with self._get_connection() as conn:
            # Find patterns with at least 2 × window_size applications
            cursor = conn.execute(
                """
                SELECT pattern_id, COUNT(*) as app_count
                FROM pattern_applications
                GROUP BY pattern_id
                HAVING app_count >= ?
                """,
                (window_size * 2,),
            )
            pattern_ids = [row["pattern_id"] for row in cursor.fetchall()]

        # Calculate drift for each pattern
        for pattern_id in pattern_ids:
            metrics = self.calculate_effectiveness_drift(
                pattern_id=pattern_id,
                window_size=window_size,
                drift_threshold=drift_threshold,
            )
            if metrics and metrics.threshold_exceeded:
                drifting.append(metrics)

        # Sort by drift magnitude descending
        drifting.sort(key=lambda m: m.drift_magnitude, reverse=True)

        return drifting[:limit]

    def get_pattern_drift_summary(self) -> dict[str, Any]:
        """Get a summary of pattern drift across all patterns.

        Provides aggregate statistics for monitoring pattern health.

        v12 Evolution: Goal Drift Detection - supports dashboard/reporting.

        Returns:
            Dict with drift statistics:
            - total_patterns: Total patterns in the store
            - patterns_analyzed: Patterns with enough history for analysis
            - patterns_drifting: Patterns exceeding drift threshold
            - avg_drift_magnitude: Average drift across analyzed patterns
            - most_drifted: ID of pattern with highest drift
        """
        with self._get_connection() as conn:
            # Total patterns
            cursor = conn.execute("SELECT COUNT(*) as count FROM patterns")
            total_patterns = cursor.fetchone()["count"]

            # Patterns with enough applications (10+ for analysis)
            cursor = conn.execute(
                """
                SELECT pattern_id, COUNT(*) as app_count
                FROM pattern_applications
                GROUP BY pattern_id
                HAVING app_count >= 10
                """
            )
            analyzable_patterns = [row["pattern_id"] for row in cursor.fetchall()]

        patterns_analyzed = len(analyzable_patterns)
        if patterns_analyzed == 0:
            return {
                "total_patterns": total_patterns,
                "patterns_analyzed": 0,
                "patterns_drifting": 0,
                "avg_drift_magnitude": 0.0,
                "most_drifted": None,
            }

        # Calculate drift for each
        all_metrics: list[DriftMetrics] = []
        for pattern_id in analyzable_patterns:
            metrics = self.calculate_effectiveness_drift(pattern_id)
            if metrics:
                all_metrics.append(metrics)

        if not all_metrics:
            return {
                "total_patterns": total_patterns,
                "patterns_analyzed": patterns_analyzed,
                "patterns_drifting": 0,
                "avg_drift_magnitude": 0.0,
                "most_drifted": None,
            }

        drifting_count = sum(1 for m in all_metrics if m.threshold_exceeded)
        avg_drift = sum(m.drift_magnitude for m in all_metrics) / len(all_metrics)
        most_drifted = max(all_metrics, key=lambda m: m.drift_magnitude)

        return {
            "total_patterns": total_patterns,
            "patterns_analyzed": len(all_metrics),
            "patterns_drifting": drifting_count,
            "avg_drift_magnitude": avg_drift,
            "most_drifted": most_drifted.pattern_id if most_drifted else None,
        }

    # =========================================================================
    # v21 Evolution: Epistemic Drift Detection
    # =========================================================================

    def calculate_epistemic_drift(
        self,
        pattern_id: str,
        window_size: int = 5,
        drift_threshold: float = 0.15,
    ) -> EpistemicDriftMetrics | None:
        """Calculate epistemic drift for a pattern - how belief/confidence changes over time.

        Unlike effectiveness drift (which tracks outcome success rates), epistemic drift
        tracks how our CONFIDENCE in the pattern changes. This enables detecting belief
        degradation before effectiveness actually declines.

        v21 Evolution: Epistemic Drift Detection - complements effectiveness drift
        with belief-level monitoring.

        Formula:
            belief_change = avg_confidence_after - avg_confidence_before
            belief_entropy = std_dev(all_confidence_values) / mean(all_confidence_values)
            weighted_change = |belief_change| × (1 + belief_entropy)

        A positive belief_change means growing confidence, negative means declining.
        High entropy indicates unstable beliefs (variance in confidence).

        Args:
            pattern_id: Pattern to analyze.
            window_size: Number of applications per window (default 5).
                        Total applications needed = 2 × window_size.
            drift_threshold: Threshold for flagging epistemic drift (default 0.15 = 15%).

        Returns:
            EpistemicDriftMetrics if enough data exists, None otherwise.
        """
        with self._get_connection() as conn:
            # Get pattern name
            cursor = conn.execute(
                "SELECT pattern_name FROM patterns WHERE id = ?",
                (pattern_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            pattern_name = row["pattern_name"]

            # Fetch 2 × window_size recent applications with grounding confidence
            cursor = conn.execute(
                """
                SELECT grounding_confidence, applied_at
                FROM pattern_applications
                WHERE pattern_id = ? AND grounding_confidence IS NOT NULL
                ORDER BY applied_at DESC
                LIMIT ?
                """,
                (pattern_id, window_size * 2),
            )
            applications = cursor.fetchall()

            # Need at least 2 × window_size applications for comparison
            if len(applications) < window_size * 2:
                _logger.debug(
                    f"Pattern {pattern_id} has {len(applications)} applications with confidence, "
                    f"need {window_size * 2} for epistemic drift analysis"
                )
                return None

            # Split into recent (first window_size) and older (second window_size)
            recent_apps = applications[:window_size]
            older_apps = applications[window_size : window_size * 2]

            # Extract confidence values
            recent_confidences = [a["grounding_confidence"] for a in recent_apps]
            older_confidences = [a["grounding_confidence"] for a in older_apps]
            all_confidences = recent_confidences + older_confidences

            # Calculate average confidence for each window
            avg_confidence_after = sum(recent_confidences) / len(recent_confidences)
            avg_confidence_before = sum(older_confidences) / len(older_confidences)

            # Calculate belief change
            belief_change = avg_confidence_after - avg_confidence_before

            # Calculate belief entropy (normalized standard deviation)
            mean_confidence = sum(all_confidences) / len(all_confidences)
            if mean_confidence > 0:
                variance = sum(
                    (c - mean_confidence) ** 2 for c in all_confidences
                ) / len(all_confidences)
                std_dev = math.sqrt(variance)
                # Normalize by mean to get coefficient of variation
                belief_entropy = min(1.0, std_dev / mean_confidence)
            else:
                belief_entropy = 0.0

            # Determine direction
            if belief_change > 0.05:  # Small threshold to avoid noise
                drift_direction = "strengthening"
            elif belief_change < -0.05:
                drift_direction = "weakening"
            else:
                drift_direction = "stable"

            # Check if threshold exceeded (weighted by entropy)
            # High entropy amplifies the signal - unstable beliefs are concerning
            weighted_change = abs(belief_change) * (1 + belief_entropy)
            threshold_exceeded = weighted_change > drift_threshold

            return EpistemicDriftMetrics(
                pattern_id=pattern_id,
                pattern_name=pattern_name,
                window_size=window_size,
                confidence_before=avg_confidence_before,
                confidence_after=avg_confidence_after,
                belief_change=belief_change,
                belief_entropy=belief_entropy,
                applications_analyzed=len(applications),
                threshold_exceeded=threshold_exceeded,
                drift_direction=drift_direction,
            )

    def get_epistemic_drifting_patterns(
        self,
        drift_threshold: float = 0.15,
        window_size: int = 5,
        limit: int = 20,
    ) -> list[EpistemicDriftMetrics]:
        """Get all patterns with significant epistemic drift.

        Scans all patterns with enough application history and returns
        those that exceed the epistemic drift threshold.

        v21 Evolution: Epistemic Drift Detection - enables CLI display of
        patterns with changing beliefs for operator review.

        Args:
            drift_threshold: Minimum epistemic drift to include (default 0.15).
            window_size: Applications per window (default 5).
            limit: Maximum patterns to return.

        Returns:
            List of EpistemicDriftMetrics for drifting patterns, sorted by
            belief_change magnitude descending.
        """
        drifting: list[EpistemicDriftMetrics] = []

        with self._get_connection() as conn:
            # Find patterns with at least 2 × window_size applications WITH confidence
            cursor = conn.execute(
                """
                SELECT pattern_id, COUNT(*) as app_count
                FROM pattern_applications
                WHERE grounding_confidence IS NOT NULL
                GROUP BY pattern_id
                HAVING app_count >= ?
                """,
                (window_size * 2,),
            )
            pattern_ids = [row["pattern_id"] for row in cursor.fetchall()]

        # Calculate epistemic drift for each pattern
        for pattern_id in pattern_ids:
            metrics = self.calculate_epistemic_drift(
                pattern_id=pattern_id,
                window_size=window_size,
                drift_threshold=drift_threshold,
            )
            if metrics and metrics.threshold_exceeded:
                drifting.append(metrics)

        # Sort by belief change magnitude descending
        drifting.sort(key=lambda m: abs(m.belief_change), reverse=True)

        return drifting[:limit]

    def get_epistemic_drift_summary(self) -> dict[str, Any]:
        """Get a summary of epistemic drift across all patterns.

        Provides aggregate statistics for monitoring belief/confidence health.

        v21 Evolution: Epistemic Drift Detection - supports dashboard/reporting.

        Returns:
            Dict with epistemic drift statistics:
            - total_patterns: Total patterns in the store
            - patterns_analyzed: Patterns with enough confidence history for analysis
            - patterns_with_epistemic_drift: Patterns exceeding epistemic drift threshold
            - avg_belief_change: Average belief change across analyzed patterns
            - avg_belief_entropy: Average belief entropy (stability measure)
            - most_unstable: ID of pattern with highest epistemic drift
        """
        with self._get_connection() as conn:
            # Total patterns
            cursor = conn.execute("SELECT COUNT(*) as count FROM patterns")
            total_patterns = cursor.fetchone()["count"]

            # Patterns with enough applications with confidence (10+ for analysis)
            cursor = conn.execute(
                """
                SELECT pattern_id, COUNT(*) as app_count
                FROM pattern_applications
                WHERE grounding_confidence IS NOT NULL
                GROUP BY pattern_id
                HAVING app_count >= 10
                """
            )
            analyzable_patterns = [row["pattern_id"] for row in cursor.fetchall()]

        patterns_analyzed = len(analyzable_patterns)
        if patterns_analyzed == 0:
            return {
                "total_patterns": total_patterns,
                "patterns_analyzed": 0,
                "patterns_with_epistemic_drift": 0,
                "avg_belief_change": 0.0,
                "avg_belief_entropy": 0.0,
                "most_unstable": None,
            }

        # Calculate epistemic drift for each
        all_metrics: list[EpistemicDriftMetrics] = []
        for pattern_id in analyzable_patterns:
            metrics = self.calculate_epistemic_drift(pattern_id)
            if metrics:
                all_metrics.append(metrics)

        if not all_metrics:
            return {
                "total_patterns": total_patterns,
                "patterns_analyzed": patterns_analyzed,
                "patterns_with_epistemic_drift": 0,
                "avg_belief_change": 0.0,
                "avg_belief_entropy": 0.0,
                "most_unstable": None,
            }

        drifting_count = sum(1 for m in all_metrics if m.threshold_exceeded)
        avg_change = sum(m.belief_change for m in all_metrics) / len(all_metrics)
        avg_entropy = sum(m.belief_entropy for m in all_metrics) / len(all_metrics)
        most_unstable = max(all_metrics, key=lambda m: abs(m.belief_change))

        return {
            "total_patterns": total_patterns,
            "patterns_analyzed": len(all_metrics),
            "patterns_with_epistemic_drift": drifting_count,
            "avg_belief_change": avg_change,
            "avg_belief_entropy": avg_entropy,
            "most_unstable": most_unstable.pattern_id if most_unstable else None,
        }

    # =========================================================================
    # v14 Evolution: Pattern Auto-Retirement
    # =========================================================================

    def retire_drifting_patterns(
        self,
        drift_threshold: float = 0.2,
        window_size: int = 5,
        require_negative_drift: bool = True,
    ) -> list[tuple[str, str, float]]:
        """Retire patterns that are drifting negatively.

        Connects the drift detection infrastructure (DriftMetrics) to action.
        Patterns that have drifted significantly AND in a negative direction
        are retired by setting their priority_score to 0.

        v14 Evolution: Pattern Auto-Retirement - enables automated pattern
        lifecycle management based on empirical effectiveness drift.

        Args:
            drift_threshold: Minimum drift magnitude to consider (default 0.2).
            window_size: Applications per window for drift calculation.
            require_negative_drift: If True, only retire patterns with
                negative drift (getting worse). If False, also retire
                patterns with positive anomalous drift.

        Returns:
            List of (pattern_id, pattern_name, drift_magnitude) tuples for
            patterns that were retired.
        """
        retired: list[tuple[str, str, float]] = []

        # Get all patterns exceeding drift threshold
        drifting = self.get_drifting_patterns(
            drift_threshold=drift_threshold,
            window_size=window_size,
            limit=100,  # Process up to 100 drifting patterns
        )

        if not drifting:
            _logger.debug("No drifting patterns found - nothing to retire")
            return retired

        with self._get_connection() as conn:
            for metrics in drifting:
                # Only retire if negative drift (getting worse)
                if require_negative_drift and metrics.drift_direction != "negative":
                    _logger.debug(
                        f"Skipping {metrics.pattern_name}: drift is {metrics.drift_direction}, "
                        f"not negative"
                    )
                    continue

                # threshold_exceeded should already be True from get_drifting_patterns()
                # but double-check for safety
                if not metrics.threshold_exceeded:
                    continue

                # Retire by setting priority_score to 0
                # Also update suggested_action to document the retirement
                retirement_reason = (
                    f"Auto-retired: drift {metrics.drift_direction} "
                    f"({metrics.drift_magnitude:.2f}), "
                    f"effectiveness {metrics.effectiveness_before:.2f} → "
                    f"{metrics.effectiveness_after:.2f}"
                )

                conn.execute(
                    """
                    UPDATE patterns
                    SET priority_score = 0,
                        suggested_action = ?
                    WHERE id = ?
                    """,
                    (retirement_reason, metrics.pattern_id),
                )

                retired.append((
                    metrics.pattern_id,
                    metrics.pattern_name,
                    metrics.drift_magnitude,
                ))

                _logger.info(
                    f"Retired pattern '{metrics.pattern_name}': {retirement_reason}"
                )

        if retired:
            _logger.info(
                f"Pattern auto-retirement complete: {len(retired)} patterns retired"
            )

        return retired

    def get_retired_patterns(self, limit: int = 50) -> list[PatternRecord]:
        """Get patterns that have been retired (priority_score = 0).

        Returns patterns that were retired through auto-retirement or
        manual deprecation, useful for review and potential recovery.

        Args:
            limit: Maximum number of patterns to return.

        Returns:
            List of PatternRecord objects with priority_score = 0.
        """
        from .models import QuarantineStatus, SuccessFactors

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM patterns
                WHERE priority_score = 0
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (limit,),
            )

            records = []
            for row in cursor.fetchall():
                # Construct PatternRecord with all v19/v22 fields
                records.append(
                    PatternRecord(
                        id=row["id"],
                        pattern_type=row["pattern_type"],
                        pattern_name=row["pattern_name"],
                        description=row["description"],
                        occurrence_count=row["occurrence_count"],
                        first_seen=datetime.fromisoformat(row["first_seen"]),
                        last_seen=datetime.fromisoformat(row["last_seen"]),
                        last_confirmed=datetime.fromisoformat(row["last_confirmed"]),
                        led_to_success_count=row["led_to_success_count"],
                        led_to_failure_count=row["led_to_failure_count"],
                        effectiveness_score=row["effectiveness_score"],
                        variance=row["variance"],
                        suggested_action=row["suggested_action"],
                        context_tags=json.loads(row["context_tags"] or "[]"),
                        priority_score=row["priority_score"],
                        # v19: Quarantine & Provenance fields
                        quarantine_status=QuarantineStatus(row["quarantine_status"])
                        if row["quarantine_status"]
                        else QuarantineStatus.PENDING,
                        provenance_job_hash=row["provenance_job_hash"],
                        provenance_sheet_num=row["provenance_sheet_num"],
                        quarantined_at=datetime.fromisoformat(row["quarantined_at"])
                        if row["quarantined_at"]
                        else None,
                        validated_at=datetime.fromisoformat(row["validated_at"])
                        if row["validated_at"]
                        else None,
                        quarantine_reason=row["quarantine_reason"],
                        # v19: Trust Scoring fields
                        trust_score=row["trust_score"] if row["trust_score"] is not None else 0.5,
                        trust_calculation_date=datetime.fromisoformat(row["trust_calculation_date"])
                        if row["trust_calculation_date"]
                        else None,
                        # v22: Metacognitive Pattern Reflection fields
                        success_factors=SuccessFactors.from_dict(json.loads(row["success_factors"]))
                        if row["success_factors"]
                        else None,
                        success_factors_updated_at=datetime.fromisoformat(row["success_factors_updated_at"])
                        if row["success_factors_updated_at"]
                        else None,
                    )
                )

            return records

    # =========================================================================
    # v16 Evolution: Evolution Trajectory Tracking
    # =========================================================================

    def record_evolution_entry(
        self,
        cycle: int | None = None,
        evolutions_completed: int | None = None,
        evolutions_deferred: int | None = None,
        issue_classes: list[str] | None = None,
        cv_avg: float | None = None,
        implementation_loc: int | None = None,
        test_loc: int | None = None,
        loc_accuracy: float | None = None,
        research_candidates_resolved: int = 0,
        research_candidates_created: int = 0,
        notes: str = "",
        *,
        entry: EvolutionEntryInput | None = None,
    ) -> str:
        """Record an evolution cycle entry to the trajectory.

        v16 Evolution: Evolution Trajectory Tracking - enables Marianne to track
        its own evolution history for recursive self-improvement analysis.

        Accepts either individual keyword args (backward compatible) or
        a bundled EvolutionEntryInput dataclass via the ``entry`` kwarg.

        Args:
            cycle: Evolution cycle number (e.g., 16 for v16).
            evolutions_completed: Number of evolutions completed in this cycle.
            evolutions_deferred: Number of evolutions deferred in this cycle.
            issue_classes: Issue classes addressed (e.g., ['infrastructure_activation']).
            cv_avg: Average Consciousness Volume of selected evolutions.
            implementation_loc: Total implementation LOC for this cycle.
            test_loc: Total test LOC for this cycle.
            loc_accuracy: LOC estimation accuracy (actual/estimated as ratio).
            research_candidates_resolved: Number of research candidates resolved.
            research_candidates_created: Number of new research candidates created.
            notes: Optional notes about this evolution cycle.
            entry: Bundled input parameters (overrides individual args if provided).

        Returns:
            The ID of the created trajectory entry.

        Raises:
            sqlite3.IntegrityError: If an entry for this cycle already exists.
        """
        import uuid

        if entry is not None:
            cycle = entry.cycle
            evolutions_completed = entry.evolutions_completed
            evolutions_deferred = entry.evolutions_deferred
            issue_classes = entry.issue_classes
            cv_avg = entry.cv_avg
            implementation_loc = entry.implementation_loc
            test_loc = entry.test_loc
            loc_accuracy = entry.loc_accuracy
            research_candidates_resolved = entry.research_candidates_resolved
            research_candidates_created = entry.research_candidates_created
            notes = entry.notes

        # Validate required fields
        if cycle is None or evolutions_completed is None or evolutions_deferred is None:
            raise TypeError("cycle, evolutions_completed, evolutions_deferred are required")
        if issue_classes is None or cv_avg is None:
            raise TypeError("issue_classes, cv_avg are required")
        if implementation_loc is None or test_loc is None or loc_accuracy is None:
            raise TypeError("implementation_loc, test_loc, loc_accuracy are required")

        entry_id = str(uuid.uuid4())
        now = datetime.now()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO evolution_trajectory (
                    id, cycle, recorded_at, evolutions_completed, evolutions_deferred,
                    issue_classes, cv_avg, implementation_loc, test_loc, loc_accuracy,
                    research_candidates_resolved, research_candidates_created, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    cycle,
                    now.isoformat(),
                    evolutions_completed,
                    evolutions_deferred,
                    json.dumps(issue_classes),
                    cv_avg,
                    implementation_loc,
                    test_loc,
                    loc_accuracy,
                    research_candidates_resolved,
                    research_candidates_created,
                    notes,
                ),
            )

        _logger.info(
            f"Recorded evolution trajectory entry for cycle {cycle}: "
            f"{evolutions_completed} completed, {evolutions_deferred} deferred"
        )

        return entry_id

    def get_trajectory(
        self,
        start_cycle: int | None = None,
        end_cycle: int | None = None,
        limit: int = 50,
    ) -> list[EvolutionTrajectoryEntry]:
        """Retrieve evolution trajectory history.

        v16 Evolution: Evolution Trajectory Tracking - enables analysis of
        Marianne's evolution history over time.

        Args:
            start_cycle: Optional minimum cycle number to include.
            end_cycle: Optional maximum cycle number to include.
            limit: Maximum number of entries to return (default: 50).

        Returns:
            List of EvolutionTrajectoryEntry objects, ordered by cycle descending.
        """
        with self._get_connection() as conn:
            query = "SELECT * FROM evolution_trajectory WHERE 1=1"
            params: list[int] = []

            if start_cycle is not None:
                query += " AND cycle >= ?"
                params.append(start_cycle)

            if end_cycle is not None:
                query += " AND cycle <= ?"
                params.append(end_cycle)

            query += " ORDER BY cycle DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            entries = []

            for row in cursor.fetchall():
                entries.append(
                    EvolutionTrajectoryEntry(
                        id=row["id"],
                        cycle=row["cycle"],
                        recorded_at=datetime.fromisoformat(row["recorded_at"]),
                        evolutions_completed=row["evolutions_completed"],
                        evolutions_deferred=row["evolutions_deferred"],
                        issue_classes=json.loads(row["issue_classes"]),
                        cv_avg=row["cv_avg"],
                        implementation_loc=row["implementation_loc"],
                        test_loc=row["test_loc"],
                        loc_accuracy=row["loc_accuracy"],
                        research_candidates_resolved=row["research_candidates_resolved"] or 0,
                        research_candidates_created=row["research_candidates_created"] or 0,
                        notes=row["notes"] or "",
                    )
                )

            return entries

    def get_recurring_issues(
        self,
        min_occurrences: int = 2,
        window_cycles: int | None = None,
    ) -> dict[str, list[int]]:
        """Identify recurring issue classes across evolution cycles.

        v16 Evolution: Evolution Trajectory Tracking - enables identification
        of patterns in what types of issues Marianne addresses repeatedly.

        Args:
            min_occurrences: Minimum number of occurrences to consider recurring.
            window_cycles: Optional limit to analyze only recent N cycles.

        Returns:
            Dict mapping issue class names to list of cycles where they appeared.
            Only includes issue classes that meet the min_occurrences threshold.
        """
        with self._get_connection() as conn:
            query = "SELECT cycle, issue_classes FROM evolution_trajectory"
            params: list[int] = []

            if window_cycles is not None:
                # Get recent N cycles
                query += " ORDER BY cycle DESC LIMIT ?"
                params.append(window_cycles)
            else:
                query += " ORDER BY cycle DESC"

            cursor = conn.execute(query, params)

            # Count issue class occurrences
            issue_cycles: dict[str, list[int]] = {}

            for row in cursor.fetchall():
                cycle = row["cycle"]
                issues = json.loads(row["issue_classes"])

                for issue in issues:
                    if issue not in issue_cycles:
                        issue_cycles[issue] = []
                    issue_cycles[issue].append(cycle)

            # Filter by min_occurrences
            recurring = {
                issue: sorted(cycles, reverse=True)
                for issue, cycles in issue_cycles.items()
                if len(cycles) >= min_occurrences
            }

            return recurring

    def record_evolution_cycle(
        self,
        cycle_number: int,
        candidates_generated: int,
        candidates_applied: int,
        changes_summary: str,
        outcome: Literal["SUCCESS", "PARTIAL", "DEFERRED"],
        learning_snapshot: dict[str, Any],
    ) -> str:
        """Record evolution cycle metadata to trajectory table.

        v25 Evolution: Simplified wrapper for recording evolution cycles with
        essential metadata. Maps to the more detailed record_evolution_entry()
        internal method.

        This method provides a simpler interface focused on what evolution
        cycles need to record: how many candidates were generated/applied,
        what changed, and the outcome.

        Args:
            cycle_number: Evolution cycle number (e.g., 25 for v25).
            candidates_generated: Number of evolution candidates generated.
            candidates_applied: Number of candidates successfully applied.
            changes_summary: Git diff summary or description of changes.
            outcome: Evolution outcome - SUCCESS, PARTIAL, or DEFERRED.
            learning_snapshot: Dict containing learning metrics at time of cycle.

        Returns:
            The ID of the created trajectory entry.

        Raises:
            sqlite3.IntegrityError: If an entry for this cycle already exists.

        Example:
            >>> store = GlobalLearningStore()
            >>> entry_id = store.record_evolution_cycle(
            ...     cycle_number=25,
            ...     candidates_generated=5,
            ...     candidates_applied=3,
            ...     changes_summary="Fixed learning export, wired pattern lifecycle",
            ...     outcome="SUCCESS",
            ...     learning_snapshot={
            ...         "patterns": 6,
            ...         "entropy": 0.000,
            ...         "recovery_rate": 0.0
            ...     }
            ... )
        """
        # Map to the detailed record_evolution_entry method
        # Use defaults for fields not in the simplified interface
        return self.record_evolution_entry(
            cycle=cycle_number,
            evolutions_completed=candidates_applied,
            evolutions_deferred=candidates_generated - candidates_applied,
            issue_classes=[outcome.lower()],
            cv_avg=0.0,  # Not used in new cycles
            implementation_loc=0,  # Can be computed from git diff if needed
            test_loc=0,
            loc_accuracy=1.0,  # Default to perfect accuracy
            research_candidates_resolved=0,
            research_candidates_created=0,
            notes=(
                f"{changes_summary}\n\nOutcome: {outcome}"
                f"\nLearning snapshot: {json.dumps(learning_snapshot)}"
            ),
        )

    def get_evolution_history(self, last_n: int = 10) -> list[EvolutionTrajectoryEntry]:
        """Retrieve last N evolution cycles for context.

        v25 Evolution: Simplified wrapper for retrieving evolution history.
        Maps to the more detailed get_trajectory() method.

        This provides a simpler interface focused on getting recent evolution
        history for context in future cycles.

        Args:
            last_n: Number of recent cycles to retrieve (default: 10).

        Returns:
            List of EvolutionTrajectoryEntry objects, ordered by cycle descending
            (most recent first).

        Example:
            >>> store = GlobalLearningStore()
            >>> recent_cycles = store.get_evolution_history(last_n=5)
            >>> for entry in recent_cycles:
            ...     print(f"Cycle {entry.cycle}: {entry.evolutions_completed} completed")
        """
        return self.get_trajectory(limit=last_n)
