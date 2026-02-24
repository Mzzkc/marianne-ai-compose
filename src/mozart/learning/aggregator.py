"""Pattern aggregation for merging outcomes into global patterns.

This module implements the pattern aggregation strategy designed in Movement III:
- Immediate aggregation on job completion (CV 0.83)
- Conflict resolution for merging patterns
- Integration with PatternDetector and GlobalLearningStore

The aggregator runs after each job completion to detect new patterns and
merge them with existing patterns in the global store.
"""

from datetime import datetime
from pathlib import Path

from mozart.core.logging import get_logger
from mozart.learning.global_store import GlobalLearningStore, PatternRecord
from mozart.learning.outcomes import SheetOutcome
from mozart.learning.patterns import (
    DetectedPattern,
    ExtractedPattern,
    OutputPatternExtractor,
    PatternDetector,
    PatternType,
)
from mozart.learning.weighter import PatternWeighter

# Module-level logger for pattern aggregation
_logger = get_logger("learning.aggregator")


class AggregationResult:
    """Result of a pattern aggregation operation."""

    def __init__(self) -> None:
        self.outcomes_recorded: int = 0
        self.patterns_detected: int = 0
        self.patterns_merged: int = 0
        self.priorities_updated: bool = False
        self.errors: list[str] = []

    def __repr__(self) -> str:
        return (
            f"AggregationResult(outcomes={self.outcomes_recorded}, "
            f"detected={self.patterns_detected}, merged={self.patterns_merged})"
        )


class PatternAggregator:
    """Aggregates patterns from executions into the global store.

    Implements the aggregation strategy defined in Movement III:
    1. Record all sheet outcomes to executions table
    2. Run PatternDetector.detect_all() on new outcomes
    3. Merge detected patterns with existing patterns in global store
    4. Update priority_score for all affected patterns
    5. Record pattern_applications for any patterns that were applied
    6. Update error_recoveries for any error learning

    Conflict resolution strategy:
    - Same pattern: merge by increasing counts and updating timestamps
    - Different suggested_actions: keep action with higher effectiveness
    """

    def __init__(
        self,
        global_store: GlobalLearningStore,
        weighter: PatternWeighter | None = None,
    ) -> None:
        """Initialize the pattern aggregator.

        Args:
            global_store: The global learning store for persistence.
            weighter: Pattern weighter for priority calculation.
        """
        self.global_store = global_store
        self.weighter = weighter or PatternWeighter()

    def aggregate_outcomes(
        self,
        outcomes: list[SheetOutcome],
        workspace_path: Path,
        model: str | None = None,
    ) -> AggregationResult:
        """Aggregate a batch of outcomes into the global store.

        This is the main entry point called after job completion.

        Args:
            outcomes: List of sheet outcomes from the completed job.
            workspace_path: Path to the workspace for hashing.
            model: Optional model name used for execution.

        Returns:
            AggregationResult with statistics about the aggregation.
        """
        result = AggregationResult()

        if not outcomes:
            return result

        # Step 1: Record all outcomes to executions table
        execution_ids = []
        for outcome in outcomes:
            exec_id = self.global_store.record_outcome(
                outcome=outcome,
                workspace_path=workspace_path,
                model=model,
            )
            execution_ids.append(exec_id)
            result.outcomes_recorded += 1

        # Step 2: Run pattern detection on new outcomes
        detector = PatternDetector(outcomes)
        detected_patterns = detector.detect_all()
        result.patterns_detected = len(detected_patterns)

        # Step 3: Merge detected patterns with global store
        for pattern in detected_patterns:
            pattern_id = self._merge_pattern(pattern)
            if pattern_id:
                result.patterns_merged += 1

        # Step 4: Update priority scores for affected patterns
        self._update_all_priorities()
        result.priorities_updated = True

        # Step 5: Record pattern applications for effectiveness tracking
        for outcome in outcomes:
            self._record_pattern_applications(outcome, execution_ids)

        _logger.info(
            "aggregation_complete",
            outcomes=result.outcomes_recorded,
            patterns_detected=result.patterns_detected,
            patterns_merged=result.patterns_merged,
        )

        return result

    def _merge_pattern(self, detected: DetectedPattern) -> str | None:
        """Merge a detected pattern into the global store.

        If pattern exists, updates counts and timestamps.
        If new, creates the pattern.

        Args:
            detected: The detected pattern to merge.

        Returns:
            The pattern ID if successful, None otherwise.
        """
        # Map PatternType to string for storage
        pattern_type = detected.pattern_type.value
        pattern_name = self._generate_pattern_name(detected)

        try:
            pattern_id = self.global_store.record_pattern(
                pattern_type=pattern_type,
                pattern_name=pattern_name,
                description=detected.description,
                context_tags=detected.context_tags,
                suggested_action=detected.to_prompt_guidance(),
            )
            return pattern_id
        except Exception as e:
            _logger.warning(
                "pattern_merge_failed",
                pattern_name=pattern_name,
                error=str(e),
                exc_info=True,
            )
            return None

    def _generate_pattern_name(self, pattern: DetectedPattern) -> str:
        """Generate a unique name for a pattern.

        The name is used as a stable identifier for merging.

        Args:
            pattern: The detected pattern.

        Returns:
            A unique pattern name.
        """
        # Use pattern type and first context tag for uniqueness
        base = pattern.pattern_type.value
        if pattern.context_tags:
            tag = pattern.context_tags[0]
            return f"{base}:{tag}"
        # Fall back to truncated description
        desc = pattern.description[:30].replace(" ", "_").lower()
        return f"{base}:{desc}"

    def _update_all_priorities(self) -> None:
        """Update priority scores for all patterns in the store.

        Uses the weighter to recalculate priorities based on current
        effectiveness and recency.

        Patterns with zero applications (never tested via the per-sheet
        feedback path) keep their initial priority. The frequency factor
        in calculate_priority() crushes single-occurrence patterns to
        ~0.075, which falls below the default min_priority=0.3 query
        threshold and prevents them from ever being injected. By skipping
        unapplied patterns, they retain their initial 0.5 priority until
        the system has real feedback data to recalculate from.
        """
        # Get all patterns (even low priority ones for recalculation)
        max_patterns_for_reweight = 1000
        patterns = self.global_store.get_patterns(min_priority=0.0, limit=max_patterns_for_reweight)

        # Batch-compute new priorities, then update all at once with executemany
        updates: list[tuple[float, str]] = []
        for pattern in patterns:
            total_applications = pattern.led_to_success_count + pattern.led_to_failure_count
            if total_applications == 0:
                continue  # Keep initial priority — not yet tested

            new_priority = self.weighter.calculate_priority(
                occurrence_count=pattern.occurrence_count,
                led_to_success_count=pattern.led_to_success_count,
                led_to_failure_count=pattern.led_to_failure_count,
                last_confirmed=pattern.last_confirmed,
                variance=pattern.variance,
            )
            updates.append((new_priority, pattern.id))

        if updates:
            with self.global_store._get_connection() as conn:
                conn.executemany(
                    "UPDATE patterns SET priority_score = ? WHERE id = ?",
                    updates,
                )

    def _record_pattern_applications(
        self,
        outcome: SheetOutcome,  # noqa: ARG002 - reserved for future use
        execution_ids: list[str],  # noqa: ARG002 - reserved for future use
    ) -> None:
        """Stub: pattern application recording not yet implemented.

        Will correlate applied patterns with outcomes to create an
        effectiveness feedback loop. Currently a no-op.

        Args:
            outcome: The sheet outcome with patterns_applied field.
            execution_ids: List of execution IDs for this batch.
        """
        # NOT YET IMPLEMENTED: correlate applied patterns with outcomes
        pass

    def merge_with_conflict_resolution(
        self,
        existing: PatternRecord,
        new: DetectedPattern,
    ) -> dict[str, object]:
        """Merge a new pattern with an existing one using conflict resolution.

        Resolution strategy from design document:
        - occurrence_count: sum
        - effectiveness_score: weighted average by occurrence_count
        - last_seen: max
        - last_confirmed: max
        - suggested_action: keep action with higher effectiveness

        Args:
            existing: The existing pattern record.
            new: The newly detected pattern.

        Returns:
            Dict of updated field values.
        """
        # Sum occurrence counts
        merged_count = existing.occurrence_count + new.frequency

        # Weighted average for effectiveness.
        # Both effectiveness_score and success_rate are on the same 0.0-1.0
        # scale, representing how often the pattern leads to success.
        total = existing.occurrence_count + new.frequency
        if total > 0:
            weighted_effectiveness = (
                existing.effectiveness_score * existing.occurrence_count
                + new.success_rate * new.frequency
            ) / total
        else:
            weighted_effectiveness = 0.5

        # Max for timestamps
        merged_last_seen = max(existing.last_seen, new.last_seen)

        # Determine suggested action — use merged effectiveness to decide.
        # If the merged effectiveness improved over the existing score,
        # the new pattern contributed positively, so adopt its guidance.
        merged_action: str | None
        if weighted_effectiveness > existing.effectiveness_score:
            merged_action = new.to_prompt_guidance()
        else:
            merged_action = existing.suggested_action

        # Format last_seen as ISO string
        last_seen_str = (
            merged_last_seen.isoformat()
            if isinstance(merged_last_seen, datetime)
            else str(merged_last_seen)
        )

        return {
            "occurrence_count": merged_count,
            "effectiveness_score": weighted_effectiveness,
            "last_seen": last_seen_str,
            "suggested_action": merged_action,
        }

    def prune_deprecated_patterns(self) -> int:
        """Remove patterns that are below the effectiveness threshold.

        Patterns are deprecated (not deleted) if:
        - They have enough application data (>= 3)
        - Their effectiveness is below 0.3

        Returns:
            Number of patterns deprecated.
        """
        deprecated_count = 0
        patterns = self.global_store.get_patterns(min_priority=0.0, limit=1000)

        with self.global_store._get_connection() as conn:
            for pattern in patterns:
                if self.weighter.is_deprecated(
                    pattern.led_to_success_count,
                    pattern.led_to_failure_count,
                ):
                    # Set priority to 0 to effectively deprecate
                    conn.execute(
                        "UPDATE patterns SET priority_score = 0 WHERE id = ?",
                        (pattern.id,),
                    )
                    deprecated_count += 1

        if deprecated_count > 0:
            _logger.info("patterns_deprecated", count=deprecated_count)

        return deprecated_count


class EnhancedAggregationResult(AggregationResult):
    """Extended aggregation result including output pattern extraction.

    Adds fields for tracking patterns extracted from stdout/stderr output
    in addition to the standard validation-based patterns.
    """

    def __init__(self) -> None:
        super().__init__()
        self.output_patterns: list[ExtractedPattern] = []
        self.output_pattern_summary: dict[str, int] = {}

    def __repr__(self) -> str:
        return (
            f"EnhancedAggregationResult(outcomes={self.outcomes_recorded}, "
            f"detected={self.patterns_detected}, merged={self.patterns_merged}, "
            f"output_patterns={len(self.output_patterns)})"
        )


class EnhancedPatternAggregator(PatternAggregator):
    """Extended aggregator that integrates OutputPatternExtractor.

    Combines validation-based pattern detection with stdout/stderr output
    analysis to provide comprehensive learning data collection.

    This aggregator:
    1. Runs standard pattern detection (validation, retry, completion patterns)
    2. Extracts patterns from stdout_tail/stderr_tail in outcomes
    3. Creates DetectedPatterns from output patterns for global store
    4. Merges all patterns into unified learning store
    """

    def __init__(
        self,
        global_store: GlobalLearningStore,
        weighter: PatternWeighter | None = None,
    ) -> None:
        """Initialize the enhanced pattern aggregator.

        Args:
            global_store: The global learning store for persistence.
            weighter: Pattern weighter for priority calculation.
        """
        super().__init__(global_store, weighter)
        self.output_extractor = OutputPatternExtractor()

    def aggregate_with_all_sources(
        self,
        outcomes: list[SheetOutcome],
        workspace_path: Path,
        model: str | None = None,
    ) -> EnhancedAggregationResult:
        """Aggregate outcomes using all pattern sources.

        Extends standard aggregation by also extracting patterns from
        stdout/stderr output in outcomes.

        Args:
            outcomes: List of sheet outcomes from the completed job.
            workspace_path: Path to the workspace for hashing.
            model: Optional model name used for execution.

        Returns:
            EnhancedAggregationResult with output pattern statistics.
        """
        result = EnhancedAggregationResult()

        if not outcomes:
            return result

        # Step 1: Extract output patterns from all outcomes
        all_output_patterns: list[ExtractedPattern] = []
        for outcome in outcomes:
            # Extract from stdout_tail if available
            stdout_tail = getattr(outcome, "stdout_tail", None)
            if stdout_tail:
                stdout_patterns = self.output_extractor.extract_from_output(
                    stdout_tail, source="stdout"
                )
                all_output_patterns.extend(stdout_patterns)

            # Extract from stderr_tail if available
            stderr_tail = getattr(outcome, "stderr_tail", None)
            if stderr_tail:
                stderr_patterns = self.output_extractor.extract_from_output(
                    stderr_tail, source="stderr"
                )
                all_output_patterns.extend(stderr_patterns)

        result.output_patterns = all_output_patterns
        result.output_pattern_summary = self.output_extractor.get_pattern_summary(
            all_output_patterns
        )

        # Step 2: Record all outcomes to executions table
        execution_ids = []
        for outcome in outcomes:
            exec_id = self.global_store.record_outcome(
                outcome=outcome,
                workspace_path=workspace_path,
                model=model,
            )
            execution_ids.append(exec_id)
            result.outcomes_recorded += 1

        # Step 3: Run standard pattern detection on new outcomes
        detector = PatternDetector(outcomes)
        detected_patterns = detector.detect_all()
        result.patterns_detected = len(detected_patterns)

        # Step 4: Convert output patterns to DetectedPatterns for storage
        output_detected = self._convert_output_patterns_to_detected(
            result.output_pattern_summary
        )
        detected_patterns.extend(output_detected)
        result.patterns_detected += len(output_detected)

        # Step 5: Merge all detected patterns with global store
        for pattern in detected_patterns:
            pattern_id = self._merge_pattern(pattern)
            if pattern_id:
                result.patterns_merged += 1

        # Step 6: Update priority scores for affected patterns
        self._update_all_priorities()
        result.priorities_updated = True

        # Step 7: Record pattern applications for effectiveness tracking
        for outcome in outcomes:
            self._record_pattern_applications(outcome, execution_ids)

        _logger.info(
            "enhanced_aggregation_complete",
            outcomes=result.outcomes_recorded,
            patterns_detected=result.patterns_detected,
            output_patterns_detected=len(output_detected),
            patterns_merged=result.patterns_merged,
            output_patterns_extracted=len(all_output_patterns),
        )

        return result

    def _convert_output_patterns_to_detected(
        self,
        pattern_summary: dict[str, int],
    ) -> list[DetectedPattern]:
        """Convert output pattern summary to DetectedPattern objects.

        Args:
            pattern_summary: Dict mapping pattern_name to occurrence count.

        Returns:
            List of DetectedPattern objects for output patterns.
        """
        detected = []
        for pattern_name, count in pattern_summary.items():
            if count >= 2:  # Only create patterns for recurring issues
                detected.append(
                    DetectedPattern(
                        pattern_type=PatternType.OUTPUT_PATTERN,
                        description=f"Output error '{pattern_name}' detected ({count}x)",
                        frequency=count,
                        success_rate=0.0,
                        context_tags=[f"output_pattern:{pattern_name}"],
                        confidence=min(0.85, 0.5 + (count * 0.1)),
                    )
                )
        return detected


def aggregate_job_outcomes(
    outcomes: list[SheetOutcome],
    workspace_path: Path,
    global_store: GlobalLearningStore | None = None,
    model: str | None = None,
) -> AggregationResult:
    """Convenience function to aggregate outcomes after job completion.

    This is the main entry point for the aggregation system.

    Args:
        outcomes: List of sheet outcomes from the completed job.
        workspace_path: Path to the workspace for hashing.
        global_store: Optional global store (uses default if None).
        model: Optional model name used for execution.

    Returns:
        AggregationResult with statistics.
    """
    from mozart.learning.global_store import get_global_store

    store = global_store or get_global_store()
    aggregator = PatternAggregator(store)

    return aggregator.aggregate_outcomes(
        outcomes=outcomes,
        workspace_path=workspace_path,
        model=model,
    )


def aggregate_job_outcomes_enhanced(
    outcomes: list[SheetOutcome],
    workspace_path: Path,
    global_store: GlobalLearningStore | None = None,
    model: str | None = None,
) -> EnhancedAggregationResult:
    """Enhanced aggregation including output pattern extraction.

    Uses EnhancedPatternAggregator to also extract patterns from
    stdout/stderr output in outcomes.

    Args:
        outcomes: List of sheet outcomes from the completed job.
        workspace_path: Path to the workspace for hashing.
        global_store: Optional global store (uses default if None).
        model: Optional model name used for execution.

    Returns:
        EnhancedAggregationResult with output pattern statistics.
    """
    from mozart.learning.global_store import get_global_store

    store = global_store or get_global_store()
    aggregator = EnhancedPatternAggregator(store)

    return aggregator.aggregate_with_all_sources(
        outcomes=outcomes,
        workspace_path=workspace_path,
        model=model,
    )
