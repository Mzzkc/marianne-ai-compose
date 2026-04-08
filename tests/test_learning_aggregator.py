"""Comprehensive tests for marianne.learning.aggregator module.

Tests the pattern aggregation system that merges outcomes into the global
learning store:
- AggregationResult: initialization and repr
- EnhancedAggregationResult: initialization and repr
- PatternAggregator: initialization, aggregation, merging, pruning
- EnhancedPatternAggregator: output pattern extraction and aggregation
- Convenience functions: aggregate_job_outcomes, aggregate_job_outcomes_enhanced
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from marianne.core.checkpoint import SheetStatus
from marianne.learning.aggregator import (
    AggregationResult,
    EnhancedAggregationResult,
    EnhancedPatternAggregator,
    PatternAggregator,
    aggregate_job_outcomes,
    aggregate_job_outcomes_enhanced,
)
from marianne.learning.global_store import GlobalLearningStore, PatternRecord
from marianne.learning.outcomes import SheetOutcome
from marianne.learning.patterns import DetectedPattern, PatternType
from marianne.learning.weighter import PatternWeighter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> GlobalLearningStore:
    """Create a GlobalLearningStore with a temporary database."""
    db_path = tmp_path / "test-aggregator.db"
    return GlobalLearningStore(db_path=db_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create and return a temporary workspace path."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _make_outcome(
    sheet_id: str = "sheet-1",
    job_id: str = "test-job",
    *,
    validation_pass_rate: float = 1.0,
    retry_count: int = 0,
    success_without_retry: bool = True,
    completion_mode_used: bool = False,
    final_status: SheetStatus = SheetStatus.COMPLETED,
    stdout_tail: str = "",
    stderr_tail: str = "",
    validation_results: list | None = None,
    failure_category_counts: dict | None = None,
    semantic_patterns: list | None = None,
    fix_suggestions: list | None = None,
    patterns_applied: list | None = None,
) -> SheetOutcome:
    """Create a SheetOutcome with defaults for testing."""
    return SheetOutcome(
        sheet_id=sheet_id,
        job_id=job_id,
        validation_results=validation_results or [],
        execution_duration=10.0,
        retry_count=retry_count,
        completion_mode_used=completion_mode_used,
        final_status=final_status,
        validation_pass_rate=validation_pass_rate,
        success_without_retry=success_without_retry,
        timestamp=datetime.now(tz=UTC),
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        failure_category_counts=failure_category_counts or {},
        semantic_patterns=semantic_patterns or [],
        fix_suggestions=fix_suggestions or [],
        patterns_applied=patterns_applied or [],
    )


# ===========================================================================
# AggregationResult
# ===========================================================================

class TestAggregationResult:
    """Tests for AggregationResult initialization and repr."""

    def test_default_initialization(self) -> None:
        """AggregationResult should start with all zeroed counters."""
        result = AggregationResult()

        assert result.outcomes_recorded == 0
        assert result.patterns_detected == 0
        assert result.patterns_merged == 0
        assert result.priorities_updated is False
        assert result.errors == []

    def test_repr(self) -> None:
        """repr should include outcomes, detected, and merged counts."""
        result = AggregationResult()
        result.outcomes_recorded = 5
        result.patterns_detected = 3
        result.patterns_merged = 2

        r = repr(result)
        assert "outcomes=5" in r
        assert "detected=3" in r
        assert "merged=2" in r

    def test_errors_list_is_mutable(self) -> None:
        """Each AggregationResult should have its own errors list."""
        r1 = AggregationResult()
        r2 = AggregationResult()
        r1.errors.append("boom")

        assert r1.errors == ["boom"]
        assert r2.errors == []

    def test_repr_with_zero_values(self) -> None:
        """repr should work with default zero values."""
        result = AggregationResult()
        r = repr(result)
        assert "outcomes=0" in r
        assert "detected=0" in r
        assert "merged=0" in r


# ===========================================================================
# EnhancedAggregationResult
# ===========================================================================

class TestEnhancedAggregationResult:
    """Tests for EnhancedAggregationResult initialization and repr."""

    def test_default_initialization(self) -> None:
        """EnhancedAggregationResult should initialize with empty output fields."""
        result = EnhancedAggregationResult()

        # Parent fields
        assert result.outcomes_recorded == 0
        assert result.patterns_detected == 0
        assert result.patterns_merged == 0
        assert result.priorities_updated is False
        assert result.errors == []

        # Enhanced fields
        assert result.output_patterns == []
        assert result.output_pattern_summary == {}

    def test_repr_includes_output_patterns(self) -> None:
        """repr should include output_patterns count."""
        result = EnhancedAggregationResult()
        result.outcomes_recorded = 3
        result.patterns_detected = 2
        result.patterns_merged = 1

        r = repr(result)
        assert "EnhancedAggregationResult" in r
        assert "outcomes=3" in r
        assert "output_patterns=0" in r

    def test_inherits_from_aggregation_result(self) -> None:
        """EnhancedAggregationResult should be a subclass of AggregationResult."""
        result = EnhancedAggregationResult()
        assert isinstance(result, AggregationResult)


# ===========================================================================
# PatternAggregator: initialization
# ===========================================================================

class TestPatternAggregatorInit:
    """Tests for PatternAggregator initialization."""

    def test_init_with_store(self, store: GlobalLearningStore) -> None:
        """PatternAggregator should store the provided global_store."""
        aggregator = PatternAggregator(store)

        assert aggregator.global_store is store
        assert isinstance(aggregator.weighter, PatternWeighter)

    def test_init_with_custom_weighter(self, store: GlobalLearningStore) -> None:
        """PatternAggregator should use the provided weighter."""
        custom_weighter = PatternWeighter(decay_rate_per_month=0.2)
        aggregator = PatternAggregator(store, weighter=custom_weighter)

        assert aggregator.weighter is custom_weighter
        assert aggregator.weighter.decay_rate_per_month == 0.2

    def test_init_without_weighter_creates_default(
        self, store: GlobalLearningStore
    ) -> None:
        """PatternAggregator should create a default PatternWeighter when None."""
        aggregator = PatternAggregator(store, weighter=None)
        assert isinstance(aggregator.weighter, PatternWeighter)


# ===========================================================================
# PatternAggregator: aggregate_outcomes with empty list
# ===========================================================================

class TestAggregateOutcomesEmpty:
    """Tests for aggregate_outcomes() with empty input."""

    def test_empty_outcomes_returns_zeroed_result(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Empty outcomes list should return zeroed AggregationResult."""
        aggregator = PatternAggregator(store)
        result = aggregator.aggregate_outcomes([], workspace)

        assert result.outcomes_recorded == 0
        assert result.patterns_detected == 0
        assert result.patterns_merged == 0
        assert result.priorities_updated is False

    def test_empty_outcomes_does_not_touch_store(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Empty outcomes should not write anything to the store."""
        aggregator = PatternAggregator(store)
        aggregator.aggregate_outcomes([], workspace)

        # Store should have zero executions
        stats = store.get_execution_stats()
        assert stats["total_executions"] == 0


# ===========================================================================
# PatternAggregator: aggregate_outcomes with mock outcomes
# ===========================================================================

class TestAggregateOutcomesWithOutcomes:
    """Tests for aggregate_outcomes() with actual SheetOutcome data."""

    def test_single_outcome_records_execution(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """A single outcome should be recorded in the store."""
        aggregator = PatternAggregator(store)
        outcome = _make_outcome(sheet_id="sheet-1")

        result = aggregator.aggregate_outcomes([outcome], workspace)

        assert result.outcomes_recorded == 1
        assert result.priorities_updated is True

    def test_multiple_outcomes_recorded(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Multiple outcomes should all be recorded."""
        aggregator = PatternAggregator(store)
        outcomes = [
            _make_outcome(sheet_id="sheet-1"),
            _make_outcome(sheet_id="sheet-2"),
            _make_outcome(sheet_id="sheet-3"),
        ]

        result = aggregator.aggregate_outcomes(outcomes, workspace)

        assert result.outcomes_recorded == 3
        assert result.priorities_updated is True

    def test_model_passed_to_store(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """The model parameter should be forwarded to the store."""
        aggregator = PatternAggregator(store)
        outcome = _make_outcome(sheet_id="sheet-1")

        result = aggregator.aggregate_outcomes(
            [outcome], workspace, model="claude-3-opus"
        )

        assert result.outcomes_recorded == 1
        # Verify the execution was recorded with the model
        recent = store.get_recent_executions(limit=1)
        assert len(recent) == 1
        assert recent[0].model == "claude-3-opus"

    def test_outcomes_with_validation_failures_detect_patterns(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Outcomes with recurring validation failures should produce detected patterns."""
        aggregator = PatternAggregator(store)

        # Create multiple outcomes with the same validation failure type
        outcomes = [
            _make_outcome(
                sheet_id=f"sheet-{i}",
                validation_pass_rate=0.5,
                success_without_retry=False,
                validation_results=[
                    {"rule_type": "file_exists", "passed": False},
                ],
            )
            for i in range(5)
        ]

        result = aggregator.aggregate_outcomes(outcomes, workspace)

        assert result.outcomes_recorded == 5
        # With 5 identical validation failures, PatternDetector should detect at
        # least one pattern for 'file_exists' (frequency >= 2 threshold)
        assert result.patterns_detected >= 1


# ===========================================================================
# PatternAggregator: _merge_pattern
# ===========================================================================

class TestMergePattern:
    """Tests for the _merge_pattern internal method."""

    def test_merge_pattern_succeeds(
        self, store: GlobalLearningStore
    ) -> None:
        """_merge_pattern should record a pattern and return its ID."""
        aggregator = PatternAggregator(store)

        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="file_exists validation tends to fail",
            frequency=5,
            success_rate=0.0,
            context_tags=["validation:file_exists"],
            confidence=0.8,
        )

        pattern_id = aggregator._merge_pattern(pattern)
        assert pattern_id is not None
        assert len(pattern_id) > 0

    def test_merge_pattern_creates_correct_name_with_tag(
        self, store: GlobalLearningStore
    ) -> None:
        """_generate_pattern_name should use type:first_tag format."""
        aggregator = PatternAggregator(store)

        pattern = DetectedPattern(
            pattern_type=PatternType.RETRY_SUCCESS,
            description="Retrying works: avg 2.0 retries",
            context_tags=["retry:effective"],
        )

        name = aggregator._generate_pattern_name(pattern)
        assert name == "retry_success:retry:effective"

    def test_merge_pattern_creates_name_without_tags(
        self, store: GlobalLearningStore
    ) -> None:
        """_generate_pattern_name should fall back to truncated description."""
        aggregator = PatternAggregator(store)

        pattern = DetectedPattern(
            pattern_type=PatternType.COMPLETION_MODE,
            description="Completion mode effective",
            context_tags=[],
        )

        name = aggregator._generate_pattern_name(pattern)
        assert name.startswith("completion_mode:")
        assert "completion_mode_effective" in name

    def test_merge_pattern_handles_exception(
        self, store: GlobalLearningStore
    ) -> None:
        """_merge_pattern should return None and log warning on error."""
        aggregator = PatternAggregator(store)

        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test pattern",
            context_tags=["test"],
        )

        # Make record_pattern raise an exception
        with patch.object(
            store, "record_pattern", side_effect=RuntimeError("db error")
        ):
            result = aggregator._merge_pattern(pattern)

        assert result is None


# ===========================================================================
# PatternAggregator: merge_with_conflict_resolution
# ===========================================================================

class TestMergeWithConflictResolution:
    """Tests for the merge_with_conflict_resolution method."""

    def _make_pattern_record(
        self,
        *,
        occurrence_count: int = 10,
        effectiveness_score: float = 0.6,
        last_seen: datetime | None = None,
        suggested_action: str | None = "existing action",
    ) -> PatternRecord:
        """Create a PatternRecord for testing."""
        now = last_seen or datetime.now(tz=UTC)
        return PatternRecord(
            id="pattern-001",
            pattern_type="validation_failure",
            pattern_name="test_pattern",
            description="test description",
            occurrence_count=occurrence_count,
            first_seen=now,
            last_seen=now,
            last_confirmed=now,
            led_to_success_count=5,
            led_to_failure_count=3,
            effectiveness_score=effectiveness_score,
            variance=0.1,
            suggested_action=suggested_action,
            context_tags=["test"],
            priority_score=0.5,
        )

    def test_counts_are_summed(self, store: GlobalLearningStore) -> None:
        """Occurrence counts should be summed."""
        aggregator = PatternAggregator(store)
        existing = self._make_pattern_record(occurrence_count=10)

        new = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            frequency=3,
            success_rate=0.8,
        )

        merged = aggregator.merge_with_conflict_resolution(existing, new)
        assert merged["occurrence_count"] == 13

    def test_effectiveness_weighted_average(
        self, store: GlobalLearningStore
    ) -> None:
        """Effectiveness should be a weighted average by occurrence count."""
        aggregator = PatternAggregator(store)
        existing = self._make_pattern_record(
            occurrence_count=10, effectiveness_score=0.6
        )

        new = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            frequency=10,
            success_rate=0.8,
        )

        merged = aggregator.merge_with_conflict_resolution(existing, new)
        # (0.6 * 10 + 0.8 * 10) / 20 = 0.7
        assert abs(merged["effectiveness_score"] - 0.7) < 0.01

    def test_last_seen_is_max(self, store: GlobalLearningStore) -> None:
        """Last seen should be the maximum of the two timestamps."""
        aggregator = PatternAggregator(store)
        old_time = datetime(2025, 1, 1, tzinfo=UTC)
        new_time = datetime(2025, 6, 1, tzinfo=UTC)

        existing = self._make_pattern_record(last_seen=old_time)

        new = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            frequency=3,
            success_rate=0.8,
            last_seen=new_time,
        )

        merged = aggregator.merge_with_conflict_resolution(existing, new)
        assert "2025-06-01" in str(merged["last_seen"])

    def test_suggested_action_keeps_better_effectiveness(
        self, store: GlobalLearningStore
    ) -> None:
        """If new pattern improves effectiveness, adopt its guidance."""
        aggregator = PatternAggregator(store)
        existing = self._make_pattern_record(
            occurrence_count=5, effectiveness_score=0.3
        )

        new = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="better approach found",
            frequency=5,
            success_rate=0.9,
            context_tags=["validation:file_exists"],
        )

        merged = aggregator.merge_with_conflict_resolution(existing, new)
        # Weighted effectiveness: (0.3*5 + 0.9*5)/10 = 0.6 > 0.3
        # So the new pattern's guidance should be adopted
        assert merged["suggested_action"] == new.to_prompt_guidance()

    def test_suggested_action_keeps_existing_when_no_improvement(
        self, store: GlobalLearningStore
    ) -> None:
        """If new pattern does not improve effectiveness, keep existing action."""
        aggregator = PatternAggregator(store)
        existing = self._make_pattern_record(
            occurrence_count=100,
            effectiveness_score=0.9,
            suggested_action="keep this action",
        )

        new = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="worse approach",
            frequency=1,
            success_rate=0.1,
        )

        merged = aggregator.merge_with_conflict_resolution(existing, new)
        assert merged["suggested_action"] == "keep this action"

    def test_zero_total_gives_neutral_effectiveness(
        self, store: GlobalLearningStore
    ) -> None:
        """When both have zero counts, effectiveness should be 0.5."""
        aggregator = PatternAggregator(store)
        existing = self._make_pattern_record(
            occurrence_count=0, effectiveness_score=0.0
        )

        new = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            frequency=0,
            success_rate=0.0,
        )

        merged = aggregator.merge_with_conflict_resolution(existing, new)
        assert merged["effectiveness_score"] == 0.5


# ===========================================================================
# PatternAggregator: _update_all_priorities
# ===========================================================================

class TestUpdateAllPriorities:
    """Tests for _update_all_priorities."""

    def test_updates_priorities_after_aggregation(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Priorities should be recalculated after aggregation."""
        # Seed a pattern first
        store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="test_priority_update",
            description="testing priority updates",
            context_tags=["test"],
        )

        aggregator = PatternAggregator(store)
        outcome = _make_outcome(sheet_id="sheet-1")

        result = aggregator.aggregate_outcomes([outcome], workspace)
        assert result.priorities_updated is True

        # The pattern should still exist and have a valid priority score
        patterns = store.get_patterns(min_priority=0.0, limit=100)
        assert any(p.pattern_name == "test_priority_update" for p in patterns)


# ===========================================================================
# PatternAggregator: prune_deprecated_patterns
# ===========================================================================

class TestPruneDeprecatedPatterns:
    """Tests for prune_deprecated_patterns."""

    def test_prune_sets_priority_to_zero(
        self, store: GlobalLearningStore
    ) -> None:
        """Deprecated patterns should have their priority set to 0."""
        aggregator = PatternAggregator(store)

        # Record a pattern with enough data to deprecate
        pattern_id = store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="bad_pattern",
            description="a pattern that fails a lot",
            context_tags=["test"],
        )

        # Record enough applications to meet the min_applications threshold
        # with low success rate to trigger deprecation
        for i in range(5):
            store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec-{i}",
                pattern_led_to_success=False,
                retry_count_before=3,
                retry_count_after=3,
            )

        deprecated_count = aggregator.prune_deprecated_patterns()

        # The pattern should have been deprecated
        assert deprecated_count >= 1

        # Verify it has priority 0
        patterns = store.get_patterns(min_priority=0.0, limit=100)
        deprecated = [p for p in patterns if p.id == pattern_id]
        if deprecated:
            assert deprecated[0].priority_score == 0.0

    def test_prune_does_not_affect_healthy_patterns(
        self, store: GlobalLearningStore
    ) -> None:
        """Patterns with good effectiveness should not be deprecated."""
        aggregator = PatternAggregator(store)

        pattern_id = store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="good_pattern",
            description="a pattern that works well",
            context_tags=["test"],
        )

        # Record successful applications
        for i in range(5):
            store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec-good-{i}",
                pattern_led_to_success=True,
                retry_count_before=1,
                retry_count_after=0,
            )

        deprecated_count = aggregator.prune_deprecated_patterns()

        # The good pattern should NOT be deprecated (or none should be deprecated)
        patterns = store.get_patterns(min_priority=0.0, limit=100)
        good = [p for p in patterns if p.id == pattern_id]
        assert len(good) == 1
        assert good[0].priority_score > 0.0

    def test_prune_returns_zero_when_nothing_to_prune(
        self, store: GlobalLearningStore
    ) -> None:
        """prune_deprecated_patterns should return 0 when no patterns exist."""
        aggregator = PatternAggregator(store)
        deprecated_count = aggregator.prune_deprecated_patterns()
        assert deprecated_count == 0


# ===========================================================================
# PatternAggregator: error handling during aggregation
# ===========================================================================

class TestAggregationErrorHandling:
    """Tests for error handling during aggregation."""

    def test_pattern_merge_failure_does_not_abort_aggregation(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """If one pattern merge fails, the rest should still succeed."""
        aggregator = PatternAggregator(store)

        # Create outcomes with enough recurring failures to detect patterns
        outcomes = [
            _make_outcome(
                sheet_id=f"sheet-{i}",
                validation_pass_rate=0.0,
                success_without_retry=False,
                validation_results=[
                    {"rule_type": "file_exists", "passed": False},
                    {"rule_type": "regex_match", "passed": False},
                ],
            )
            for i in range(5)
        ]

        # Patch _merge_pattern to fail on the first call but succeed after
        call_count = 0
        original_merge = aggregator._merge_pattern

        def failing_merge(detected: DetectedPattern) -> str | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # Simulate failure
            return original_merge(detected)

        aggregator._merge_pattern = failing_merge  # type: ignore[assignment]

        result = aggregator.aggregate_outcomes(outcomes, workspace)

        # Outcomes should still be recorded
        assert result.outcomes_recorded == 5
        assert result.priorities_updated is True

    def test_record_outcome_exception_propagates(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """If record_outcome raises, the exception should propagate."""
        aggregator = PatternAggregator(store)
        outcome = _make_outcome(sheet_id="sheet-1")

        with patch.object(
            store, "record_outcome", side_effect=RuntimeError("db write failed")
        ):
            with pytest.raises(RuntimeError, match="db write failed"):
                aggregator.aggregate_outcomes([outcome], workspace)


# ===========================================================================
# EnhancedPatternAggregator
# ===========================================================================

class TestEnhancedPatternAggregator:
    """Tests for EnhancedPatternAggregator."""

    def test_init_creates_output_extractor(
        self, store: GlobalLearningStore
    ) -> None:
        """EnhancedPatternAggregator should create an OutputPatternExtractor."""
        aggregator = EnhancedPatternAggregator(store)
        assert aggregator.output_extractor is not None

    def test_aggregate_with_all_sources_empty(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Empty outcomes should return zeroed EnhancedAggregationResult."""
        aggregator = EnhancedPatternAggregator(store)
        result = aggregator.aggregate_with_all_sources([], workspace)

        assert isinstance(result, EnhancedAggregationResult)
        assert result.outcomes_recorded == 0
        assert result.output_patterns == []
        assert result.output_pattern_summary == {}

    def test_aggregate_with_stdout_patterns(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Outcomes with stdout errors should extract output patterns."""
        aggregator = EnhancedPatternAggregator(store)

        outcomes = [
            _make_outcome(
                sheet_id="sheet-1",
                stdout_tail="ImportError: No module named 'missing_module'",
            ),
            _make_outcome(
                sheet_id="sheet-2",
                stdout_tail="ImportError: No module named 'another_module'",
            ),
        ]

        result = aggregator.aggregate_with_all_sources(outcomes, workspace)

        assert result.outcomes_recorded == 2
        assert len(result.output_patterns) >= 2
        assert "import_error" in result.output_pattern_summary

    def test_aggregate_with_stderr_patterns(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Outcomes with stderr errors should extract patterns from stderr."""
        aggregator = EnhancedPatternAggregator(store)

        outcomes = [
            _make_outcome(
                sheet_id="sheet-1",
                stderr_tail="PermissionError: permission denied: /etc/secret",
            ),
        ]

        result = aggregator.aggregate_with_all_sources(outcomes, workspace)

        assert result.outcomes_recorded == 1
        assert len(result.output_patterns) >= 1

    def test_convert_output_patterns_to_detected_with_threshold(
        self, store: GlobalLearningStore
    ) -> None:
        """Only patterns with count >= 2 should be converted to DetectedPattern."""
        aggregator = EnhancedPatternAggregator(store)

        summary = {
            "import_error": 3,   # >= 2, should be converted
            "timeout": 1,        # < 2, should NOT be converted
            "rate_limit": 5,     # >= 2, should be converted
        }

        detected = aggregator._convert_output_patterns_to_detected(summary)

        names = [d.description for d in detected]
        assert len(detected) == 2
        assert any("import_error" in n for n in names)
        assert any("rate_limit" in n for n in names)
        assert not any("timeout" in n for n in names)

    def test_convert_output_patterns_confidence_capped(
        self, store: GlobalLearningStore
    ) -> None:
        """Confidence should be capped at 0.85."""
        aggregator = EnhancedPatternAggregator(store)

        summary = {"import_error": 100}  # Very high count
        detected = aggregator._convert_output_patterns_to_detected(summary)

        assert len(detected) == 1
        assert detected[0].confidence <= 0.85

    def test_convert_output_patterns_type_is_output_pattern(
        self, store: GlobalLearningStore
    ) -> None:
        """Converted patterns should have PatternType.OUTPUT_PATTERN."""
        aggregator = EnhancedPatternAggregator(store)

        summary = {"rate_limit": 3}
        detected = aggregator._convert_output_patterns_to_detected(summary)

        assert len(detected) == 1
        assert detected[0].pattern_type == PatternType.OUTPUT_PATTERN

    def test_convert_output_patterns_empty_summary(
        self, store: GlobalLearningStore
    ) -> None:
        """Empty summary should produce no detected patterns."""
        aggregator = EnhancedPatternAggregator(store)

        detected = aggregator._convert_output_patterns_to_detected({})
        assert detected == []


# ===========================================================================
# Convenience functions
# ===========================================================================

class TestConvenienceFunctions:
    """Tests for aggregate_job_outcomes and aggregate_job_outcomes_enhanced."""

    def test_aggregate_job_outcomes_with_explicit_store(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """aggregate_job_outcomes should use provided store."""
        outcome = _make_outcome(sheet_id="sheet-1")
        result = aggregate_job_outcomes(
            outcomes=[outcome],
            workspace_path=workspace,
            global_store=store,
            model="test-model",
        )

        assert isinstance(result, AggregationResult)
        assert result.outcomes_recorded == 1

    def test_aggregate_job_outcomes_empty(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """aggregate_job_outcomes with empty outcomes should return zeroed result."""
        result = aggregate_job_outcomes(
            outcomes=[],
            workspace_path=workspace,
            global_store=store,
        )

        assert result.outcomes_recorded == 0
        assert result.patterns_detected == 0

    def test_aggregate_job_outcomes_enhanced_with_explicit_store(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """aggregate_job_outcomes_enhanced should use provided store."""
        outcome = _make_outcome(
            sheet_id="sheet-1",
            stdout_tail="Traceback (most recent call last)",
        )
        result = aggregate_job_outcomes_enhanced(
            outcomes=[outcome],
            workspace_path=workspace,
            global_store=store,
        )

        assert isinstance(result, EnhancedAggregationResult)
        assert result.outcomes_recorded == 1

    def test_aggregate_job_outcomes_enhanced_empty(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """aggregate_job_outcomes_enhanced with empty outcomes should return zeroed."""
        result = aggregate_job_outcomes_enhanced(
            outcomes=[],
            workspace_path=workspace,
            global_store=store,
        )

        assert isinstance(result, EnhancedAggregationResult)
        assert result.outcomes_recorded == 0
        assert result.output_patterns == []


# ===========================================================================
# Integration: full aggregation pipeline
# ===========================================================================

class TestAggregationIntegration:
    """Integration tests exercising the full aggregation pipeline."""

    def test_full_pipeline_with_mixed_outcomes(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Full pipeline with a mix of success and failure outcomes."""
        aggregator = PatternAggregator(store)

        outcomes = [
            # Successful outcomes
            _make_outcome(sheet_id="sheet-1", validation_pass_rate=1.0),
            _make_outcome(sheet_id="sheet-2", validation_pass_rate=1.0),
            _make_outcome(sheet_id="sheet-3", validation_pass_rate=1.0),
            # Failed outcomes with the same validation failure
            _make_outcome(
                sheet_id="sheet-4",
                validation_pass_rate=0.0,
                success_without_retry=False,
                validation_results=[
                    {"rule_type": "file_exists", "passed": False}
                ],
            ),
            _make_outcome(
                sheet_id="sheet-5",
                validation_pass_rate=0.0,
                success_without_retry=False,
                validation_results=[
                    {"rule_type": "file_exists", "passed": False}
                ],
            ),
        ]

        result = aggregator.aggregate_outcomes(outcomes, workspace)

        assert result.outcomes_recorded == 5
        assert result.priorities_updated is True
        # At least the success-without-retry patterns should be detected
        # (3 successes meet the >= 3 threshold)
        assert result.patterns_detected >= 1

    def test_enhanced_pipeline_extracts_output_and_validation_patterns(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """Enhanced pipeline should merge both output and validation patterns."""
        aggregator = EnhancedPatternAggregator(store)

        outcomes = [
            _make_outcome(
                sheet_id=f"sheet-{i}",
                validation_pass_rate=0.0,
                success_without_retry=False,
                validation_results=[
                    {"rule_type": "file_exists", "passed": False}
                ],
                stdout_tail="ImportError: No module named 'missing'",
            )
            for i in range(5)
        ]

        result = aggregator.aggregate_with_all_sources(outcomes, workspace)

        assert result.outcomes_recorded == 5
        assert len(result.output_patterns) >= 5  # One import_error per outcome
        assert result.patterns_detected >= 1
        assert result.priorities_updated is True

    def test_aggregate_then_prune(
        self, store: GlobalLearningStore, workspace: Path
    ) -> None:
        """After aggregation, pruning should work on the stored patterns."""
        aggregator = PatternAggregator(store)

        # Run aggregation
        outcomes = [
            _make_outcome(sheet_id="sheet-1"),
        ]
        aggregator.aggregate_outcomes(outcomes, workspace)

        # Pruning should not crash on freshly-aggregated patterns
        deprecated_count = aggregator.prune_deprecated_patterns()
        assert isinstance(deprecated_count, int)
        assert deprecated_count >= 0

    def test_record_pattern_applications_is_noop(
        self, store: GlobalLearningStore
    ) -> None:
        """_record_pattern_applications should be a no-op (stub)."""
        aggregator = PatternAggregator(store)
        outcome = _make_outcome(sheet_id="sheet-1")

        # Should not raise
        aggregator._record_pattern_applications(outcome, ["exec-1", "exec-2"])
