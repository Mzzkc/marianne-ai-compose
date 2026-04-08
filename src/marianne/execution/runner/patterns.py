"""Pattern management mixin for JobRunner.

Contains methods for querying, applying, and recording feedback on
patterns from both the local outcome store and the global learning store.
This module implements the Learning Activation pattern that bridges
stored knowledge with prompt injection.

Architecture:
    PatternsMixin is mixed with other mixins to compose the full JobRunner.
    It expects the following attributes from base.py:

    Required attributes:
        - config: JobConfig
        - _logger: MarianneLogger
        - _global_learning_store: GlobalLearningStore | None
        - outcome_store: OutcomeStore | None
        - _exploration_pattern_ids: list[str]
        - _exploitation_pattern_ids: list[str]
        - console: Console

    Provides methods:
        - _query_relevant_patterns(): Query patterns for injection
        - _record_pattern_feedback(): Record pattern application outcomes
        - _assess_failure_risk(): Assess failure risk from historical data

    Note: _aggregate_to_global_store() is defined in LifecycleMixin since
    it's called during job finalization, not per-sheet execution.
"""

from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

    from marianne.learning.global_store import GlobalLearningStore

from marianne.core.config import JobConfig
from marianne.core.logging import MarianneLogger
from marianne.learning.outcomes import OutcomeStore

# Pattern used to strip noise words for dedup grouping
_DEDUP_STRIP_RE = re.compile(r"\b(error|issue|problem|failed|failure|the|a|an)\b", re.I)
_DEDUP_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_dedup(text: str) -> str:
    """Normalize a pattern description for deduplication grouping.

    Lowercases, strips common noise words, and collapses whitespace so that
    'File not created', 'file_not_created', and 'File Not Created Error' all
    map to the same group key.
    """
    key = text.lower().replace("_", " ")
    key = _DEDUP_STRIP_RE.sub("", key)
    key = _DEDUP_WHITESPACE_RE.sub(" ", key).strip()
    # Use first 30 chars as the group key for fuzzy grouping
    return key[:30]


def _deduplicate_patterns(patterns: list[Any]) -> list[Any]:
    """Remove near-duplicate patterns, keeping the highest-scored per group.

    Groups patterns by normalized description text and retains only the one
    with the highest effectiveness_score from each group.
    """
    groups: dict[str, Any] = {}
    for pattern in patterns:
        desc = pattern.description or pattern.pattern_name
        key = _normalize_for_dedup(desc)
        existing = groups.get(key)
        if existing is None or pattern.effectiveness_score > existing.effectiveness_score:
            groups[key] = pattern
    return list(groups.values())


def build_semantic_context_tags(config: JobConfig) -> list[str]:
    """Build semantic context tags from the execution context.

    Generates tags that match the namespace used when patterns are stored
    (validation:TYPE, retry:effective, success:first_attempt, etc.) instead
    of the positional tags (sheet:N, job:X) that previously caused the
    F-009/F-144 tag namespace mismatch — where 91% of 28K+ patterns were
    never applied because query tags had zero overlap with stored tags.

    Args:
        config: The job configuration containing validation rules,
                instrument info, and other execution context.

    Returns:
        List of semantic tags matching the stored tag format.
    """
    tags: list[str] = []

    # Extract validation types from configured rules
    # These match the "validation:TYPE" tags stored by pattern discovery
    # (see learning/patterns.py:411 — context_tags=[f"validation:{vtype}"])
    seen_vtypes: set[str] = set()
    for rule in config.validations:
        if rule.type not in seen_vtypes:
            tags.append(f"validation:{rule.type}")
            seen_vtypes.add(rule.type)

    # Broad category tags that match common stored patterns.
    # These are always relevant regardless of specific sheet context:
    #   - success:first_attempt (patterns.py:514)
    #   - retry:effective (patterns.py:445)
    #   - completion:used (patterns.py:483)
    tags.append("success:first_attempt")
    tags.append("retry:effective")
    tags.append("completion:used")

    return tags


def _unknown_risk(factors: list[str]) -> dict[str, Any]:
    """Build an 'unknown' risk assessment result."""
    return {
        "risk_level": "unknown",
        "confidence": 0.0,
        "factors": factors,
        "recommended_adjustments": [],
    }


@dataclass
class PatternFeedbackContext:
    """Context for recording pattern application feedback.

    Groups the parameters needed by _record_pattern_feedback into a
    single typed object, reducing the method's parameter count from 10
    to 3 (self, pattern_ids, context).
    """

    validation_passed: bool
    success_without_retry: bool
    sheet_num: int
    grounding_confidence: float | None = None
    validation_types: list[str] | None = None
    error_categories: list[str] | None = None
    prior_sheet_status: str | None = None
    retry_iteration: int = 0
    escalation_was_pending: bool = False


class PatternsMixin:
    """Mixin providing pattern management for JobRunner.

    This mixin implements the Learning Activation pattern, bridging:
    - Global pattern store with prompt injection
    - Pattern application with feedback recording
    - Historical data with failure risk assessment

    Pattern Application Flow:
        1. _query_relevant_patterns() queries global store
        2. Patterns are injected into sheet prompts
        3. After execution, _record_pattern_feedback() records outcomes
        4. On job completion, _aggregate_to_global_store() aggregates all

    Epsilon-Greedy Exploration:
        The pattern application uses epsilon-greedy exploration to balance
        exploitation of proven patterns with exploration of untested ones.
        This addresses the cold-start problem where new patterns never get
        applied because they have low initial scores.

    Key Attributes (from base.py):
        _exploration_pattern_ids: Patterns selected via exploration
        _exploitation_pattern_ids: Patterns selected via exploitation
    """

    # Type hints for attributes provided by base.py
    config: JobConfig
    _logger: MarianneLogger
    console: Console
    _global_learning_store: GlobalLearningStore | None
    outcome_store: OutcomeStore | None
    _exploration_pattern_ids: list[str]
    _exploitation_pattern_ids: list[str]

    def _query_relevant_patterns(
        self,
        job_id: str,
        sheet_num: int,
        context_tags: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Query relevant patterns from the global learning store.

        Learning Activation: This method bridges the global learning store's
        pattern knowledge with the prompt injection system. It queries patterns
        that are relevant to the current execution context and formats them
        for injection into sheet prompts.

        Pattern Application v4: Implements epsilon-greedy exploration mode.
        When random() < exploration_rate, also queries lower-priority patterns
        (down to exploration_min_priority) to collect effectiveness data.
        This breaks the cold-start problem where patterns with low effectiveness
        scores never get applied and thus never improve.

        Args:
            job_id: Current job identifier for similar job matching.
            sheet_num: Current sheet number.
            context_tags: Optional tags for context-based filtering.

        Returns:
            Tuple of (pattern_descriptions, pattern_ids).
            - pattern_descriptions: Human-readable strings for prompt injection
            - pattern_ids: IDs for tracking pattern application outcomes
        """
        # Reset exploration tracking for this query
        self._exploration_pattern_ids = []
        self._exploitation_pattern_ids = []

        if self._global_learning_store is None:
            return [], []

        try:
            # Build context tags for filtering (auto-generate if not provided)
            # F-009/F-144 fix: use SEMANTIC tags that match the stored tag
            # namespace (validation:TYPE, retry:effective, etc.) instead of
            # positional tags (sheet:N, job:X) that had zero overlap.
            if context_tags:
                query_context_tags = context_tags
            else:
                query_context_tags = build_semantic_context_tags(self.config)

            # F-009 fix: pass instrument_name for instrument-scoped filtering.
            # get_patterns() already supports this parameter but it was never
            # passed — patterns accumulated globally with no instrument scope.
            query_instrument_name = self.config.instrument

            # Determine exploration vs exploitation mode
            exploration_rate = self.config.learning.exploration_rate
            exploration_min_priority = self.config.learning.exploration_min_priority
            is_exploration_mode = random.random() < exploration_rate

            # Standard exploitation threshold (proven patterns)
            exploitation_threshold = 0.3

            if is_exploration_mode:
                # EXPLORATION MODE: Include lower-priority patterns
                # Use the lower threshold to find untested/low-confidence patterns
                min_priority = exploration_min_priority
                self._logger.info(
                    "patterns.exploration_mode_triggered",
                    job_id=job_id,
                    sheet_num=sheet_num,
                    exploration_rate=exploration_rate,
                    min_priority=min_priority,
                    reason="collecting_effectiveness_data",
                )
            else:
                # EXPLOITATION MODE: Only proven high-priority patterns
                min_priority = exploitation_threshold

            # v22: Query auto-apply patterns first if enabled
            auto_apply_patterns: list[Any] = []
            auto_apply_config = self.config.learning.auto_apply
            if (
                auto_apply_config
                and auto_apply_config.enabled
                and self._global_learning_store
            ):
                auto_apply_patterns = self._global_learning_store.get_patterns_for_auto_apply(
                    trust_threshold=auto_apply_config.trust_threshold,
                    require_validated=auto_apply_config.require_validated_status,
                    limit=auto_apply_config.max_patterns_per_sheet,
                    context_tags=query_context_tags,
                )
                if auto_apply_patterns and auto_apply_config.log_applications:
                    self._logger.info(
                        "patterns.auto_apply_selected",
                        job_id=job_id,
                        sheet_num=sheet_num,
                        auto_apply_count=len(auto_apply_patterns),
                        pattern_ids=[p.id for p in auto_apply_patterns],
                        trust_threshold=auto_apply_config.trust_threshold,
                    )

            # Query patterns from global store with semantic context filtering
            patterns = self._global_learning_store.get_patterns(
                min_priority=min_priority,
                limit=5,
                context_tags=query_context_tags,
                instrument_name=query_instrument_name,
            )

            # Merge auto-apply patterns with regular patterns, avoiding duplicates
            if auto_apply_patterns:
                auto_apply_ids = {p.id for p in auto_apply_patterns}
                # Filter out already-selected auto-apply patterns from regular query
                patterns = [p for p in patterns if p.id not in auto_apply_ids]
                # Auto-apply patterns come first (highest trust)
                patterns = auto_apply_patterns + patterns

            # If no patterns match with context filtering, fall back to unfiltered
            if not patterns:
                self._logger.debug(
                    "patterns.query_global_fallback",
                    job_id=job_id,
                    sheet_num=sheet_num,
                    context_tags=query_context_tags,
                    reason="no_patterns_matched_context",
                    exploration_mode=is_exploration_mode,
                )
                patterns = self._global_learning_store.get_patterns(
                    min_priority=min_priority,
                    limit=5,
                    instrument_name=query_instrument_name,
                )

            if not patterns:
                return [], []

            # Deduplicate near-similar patterns to avoid contradictory advice
            patterns = _deduplicate_patterns(patterns)

            # Format patterns for prompt injection
            descriptions: list[str] = []
            pattern_ids: list[str] = []
            auto_apply_ids = {p.id for p in auto_apply_patterns} if auto_apply_patterns else set()

            for pattern in patterns:
                # Categorize as exploration vs exploitation for feedback tracking
                is_exploration = (
                    pattern.id not in auto_apply_ids
                    and pattern.priority_score < exploitation_threshold
                )
                if is_exploration:
                    self._exploration_pattern_ids.append(pattern.id)
                else:
                    self._exploitation_pattern_ids.append(pattern.id)

                # Effectiveness indicator: ✓ high, ○ moderate, ⚠ low/untested
                score = pattern.effectiveness_score
                if score > 0.7:
                    indicator = "✓"
                elif score > 0.4:
                    indicator = "○"
                else:
                    indicator = "⚠"

                desc = (
                    f"{indicator} "
                    f"{pattern.description or pattern.pattern_name} "
                    f"(seen {pattern.occurrence_count}x, "
                    f"{score:.0%} effective)"
                )
                descriptions.append(desc)
                pattern_ids.append(pattern.id)

            self._logger.debug(
                "patterns.query_global",
                job_id=job_id,
                sheet_num=sheet_num,
                patterns_found=len(descriptions),
                pattern_ids=pattern_ids,
                context_tags_used=query_context_tags,
                exploration_mode=is_exploration_mode,
                exploration_patterns=len(self._exploration_pattern_ids),
                exploitation_patterns=len(self._exploitation_pattern_ids),
                auto_apply_patterns=len(auto_apply_ids),
            )

            return descriptions, pattern_ids

        except sqlite3.IntegrityError as e:
            # Data consistency bug: FK/UNIQUE constraint violation
            # Log at error level AND warn on console so operator sees degradation
            self._logger.error(
                "patterns.query_integrity_error",
                job_id=job_id,
                sheet_num=sheet_num,
                error=str(e),
            )
            self.console.print(
                f"[yellow]Warning: Learning store integrity error on sheet {sheet_num} "
                f"— patterns degraded (FK constraint: {e})[/yellow]"
            )
            return [], []
        except (sqlite3.Error, KeyError, ValueError, OSError) as e:
            # Transient failures (db locked, disk full) shouldn't block execution
            self._logger.warning(
                "patterns.query_global_failed",
                job_id=job_id,
                sheet_num=sheet_num,
                error=str(e),
            )
            return [], []

    def _assess_failure_risk(
        self,
        job_id: str,
        sheet_num: int,
    ) -> dict[str, Any]:
        """Assess failure risk based on historical execution data.

        Learning Activation: Analyzes past executions to assess the risk
        of failure for the current sheet, enabling proactive adjustments
        to retry strategy and confidence thresholds.

        Args:
            job_id: Current job identifier.
            sheet_num: Current sheet number.

        Returns:
            Dict with risk assessment:
            - risk_level: "low", "medium", "high", or "unknown"
            - confidence: Confidence in assessment (0.0-1.0)
            - factors: List of contributing factors
            - recommended_adjustments: Suggested parameter changes
        """
        if self._global_learning_store is None:
            return _unknown_risk(["no global store available"])

        try:
            stats = self._global_learning_store.get_execution_stats()
            first_attempt_rate = stats.get("success_without_retry_rate", 0.0)
            total_executions = stats.get("total_executions", 0)

            # Assess risk based on historical success rate
            if total_executions < 10:
                risk_level = "unknown"
                confidence = 0.2
                factors = [f"insufficient data ({total_executions} executions)"]
            elif first_attempt_rate > 0.7:
                risk_level = "low"
                confidence = min(0.9, total_executions / 100)
                factors = [f"high first-attempt success rate ({first_attempt_rate:.0%})"]
            elif first_attempt_rate > 0.4:
                risk_level = "medium"
                confidence = min(0.8, total_executions / 100)
                factors = [f"moderate first-attempt success rate ({first_attempt_rate:.0%})"]
            else:
                risk_level = "high"
                confidence = min(0.9, total_executions / 100)
                factors = [f"low first-attempt success rate ({first_attempt_rate:.0%})"]

            # Check for active rate limits
            is_limited, wait_time = self._global_learning_store.is_rate_limited()
            if is_limited:
                risk_level = "high"
                factors.append(f"active rate limit (expires in {wait_time:.0f}s)")

            # Build recommendations based on risk level
            recommendations: list[str] = []
            if risk_level == "high":
                recommendations.append("consider increasing retry delays")
                recommendations.append("enable completion mode aggressively")
            elif risk_level == "medium":
                recommendations.append("monitor validation confidence closely")

            return {
                "risk_level": risk_level,
                "confidence": confidence,
                "factors": factors,
                "recommended_adjustments": recommendations,
            }

        except sqlite3.IntegrityError as e:
            self._logger.error(
                "risk_assessment.integrity_error",
                job_id=job_id,
                sheet_num=sheet_num,
                error=str(e),
            )
            return _unknown_risk([f"integrity error: {e}"])
        except (sqlite3.Error, KeyError, ValueError, OSError) as e:
            self._logger.warning(
                "risk_assessment.failed",
                job_id=job_id,
                sheet_num=sheet_num,
                error=str(e),
            )
            return _unknown_risk([f"assessment failed: {e}"])

    async def _record_pattern_feedback(
        self,
        pattern_ids: list[str],
        context: PatternFeedbackContext,
    ) -> None:
        """Record pattern application feedback to global learning store.

        This closes the pattern feedback loop by recording whether patterns
        that were applied to a sheet execution led to a successful outcome.
        Distinguishes between exploration and exploitation patterns for
        differential effectiveness calculation.

        v12 Evolution: Grounding→Pattern Feedback - now passes grounding_confidence
        to the global learning store, enabling grounding-weighted effectiveness.

        v22 Evolution: Metacognitive Pattern Reflection - now captures success
        factors (context conditions) when patterns succeed, enabling WHY analysis.

        Args:
            pattern_ids: List of pattern IDs that were applied.
            context: Feedback context containing validation results, sheet info,
                 and metacognitive context for success factor analysis.
        """
        if self._global_learning_store is None or not pattern_ids:
            return

        # Determine if the pattern led to execution success:
        # - True if validation passed AND succeeded without retry (pattern worked)
        # - False if validation failed or retries needed (pattern didn't help)
        pattern_led_to_success = context.validation_passed and context.success_without_retry

        for pattern_id in pattern_ids:
            try:
                application_mode = (
                    "exploration"
                    if pattern_id in self._exploration_pattern_ids
                    else "exploitation"
                )

                self._global_learning_store.record_pattern_application(
                    pattern_id=pattern_id,
                    execution_id=f"sheet_{context.sheet_num}",
                    pattern_led_to_success=pattern_led_to_success,
                    retry_count_before=0,  # We don't track this per-pattern
                    retry_count_after=0 if context.success_without_retry else 1,
                    application_mode=application_mode,
                    validation_passed=context.validation_passed,
                    grounding_confidence=context.grounding_confidence,
                )

                # v22 Evolution: Update success factors when pattern succeeds
                # Only capture factors when the pattern led to success
                if pattern_led_to_success:
                    self._global_learning_store.update_success_factors(
                        pattern_id=pattern_id,
                        validation_types=context.validation_types,
                        error_categories=context.error_categories,
                        prior_sheet_status=context.prior_sheet_status,
                        retry_iteration=context.retry_iteration,
                        escalation_was_pending=context.escalation_was_pending,
                        grounding_confidence=context.grounding_confidence,
                    )
                    self._logger.debug(
                        "learning.success_factors_updated",
                        pattern_id=pattern_id,
                        sheet_num=context.sheet_num,
                        validation_types=context.validation_types,
                        prior_sheet_status=context.prior_sheet_status,
                    )

                self._logger.debug(
                    "learning.pattern_feedback_recorded",
                    pattern_id=pattern_id,
                    sheet_num=context.sheet_num,
                    pattern_led_to_success=pattern_led_to_success,
                    validation_passed=context.validation_passed,
                    success_without_retry=context.success_without_retry,
                    application_mode=application_mode,
                    grounding_confidence=context.grounding_confidence,
                )
            except sqlite3.IntegrityError as e:
                # FK/UNIQUE constraint violation — data consistency bug
                # Log at error level AND warn on console for operator visibility
                self._logger.error(
                    "learning.pattern_feedback_integrity_error",
                    pattern_id=pattern_id,
                    sheet_num=context.sheet_num,
                    error=str(e),
                )
                self.console.print(
                    f"[yellow]Warning: Pattern feedback integrity error on sheet "
                    f"{context.sheet_num} — feedback for pattern {pattern_id[:8]} "
                    f"not recorded (FK constraint: {e})[/yellow]"
                )
            except (sqlite3.Error, KeyError, ValueError, OSError) as e:
                # Transient failures should not block execution
                self._logger.warning(
                    "learning.pattern_feedback_failed",
                    pattern_id=pattern_id,
                    sheet_num=context.sheet_num,
                    error=str(e),
                )

