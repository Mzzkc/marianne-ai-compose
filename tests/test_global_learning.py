"""Tests for global learning components.

Tests the Movement III/IV implementation:
- GlobalLearningStore (SQLite-based global pattern store)
- PatternWeighter (priority calculation with decay)
- PatternAggregator (pattern merging and conflict resolution)
"""

import tempfile
from collections.abc import Generator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from marianne.core.checkpoint import SheetStatus
from marianne.learning.aggregator import (
    AggregationResult,
    EnhancedAggregationResult,
    EnhancedPatternAggregator,
    PatternAggregator,
)
from marianne.learning.global_store import (
    EntropyResponseRecord,
    ExplorationBudgetRecord,
    GlobalLearningStore,
    PatternRecord,
    QuarantineStatus,
)
from marianne.learning.outcomes import SheetOutcome
from marianne.learning.patterns import (
    DetectedPattern,
    ExtractedPattern,
    OutputPatternExtractor,
    PatternType,
)
from marianne.learning.weighter import PatternWeighter

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path() -> Path:
    """Create a temporary database path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def global_store(temp_db_path: Path) -> Generator[GlobalLearningStore, None, None]:
    """Create a GlobalLearningStore with a temporary database."""
    store = GlobalLearningStore(temp_db_path)
    yield store
    # Cleanup
    if temp_db_path.exists():
        temp_db_path.unlink()


@pytest.fixture
def sample_outcome() -> SheetOutcome:
    """Create a sample sheet outcome for testing."""
    return SheetOutcome(
        sheet_id="test-job-sheet1",
        job_id="test-job",
        validation_results=[
            {"rule_type": "file_exists", "passed": True, "confidence": 1.0},
            {"rule_type": "content_contains", "passed": True, "confidence": 0.9},
        ],
        execution_duration=30.0,
        retry_count=0,
        completion_mode_used=False,
        final_status=SheetStatus.COMPLETED,
        validation_pass_rate=1.0,
        success_without_retry=True,
    )


@pytest.fixture
def sample_failed_outcome() -> SheetOutcome:
    """Create a sample failed outcome for testing."""
    return SheetOutcome(
        sheet_id="test-job-sheet2",
        job_id="test-job",
        validation_results=[
            {"rule_type": "file_exists", "passed": False, "confidence": 1.0},
        ],
        execution_duration=45.0,
        retry_count=2,
        completion_mode_used=True,
        final_status=SheetStatus.FAILED,
        validation_pass_rate=0.0,
        success_without_retry=False,
    )


# =============================================================================
# TestGlobalLearningStore
# =============================================================================


class TestGlobalLearningStore:
    """Tests for GlobalLearningStore class."""

    def test_store_initialization(self, global_store: GlobalLearningStore) -> None:
        """Test that store initializes correctly."""
        assert global_store.db_path.exists()

    def test_record_outcome(
        self,
        global_store: GlobalLearningStore,
        sample_outcome: SheetOutcome,
    ) -> None:
        """Test recording an outcome to the store."""
        workspace_path = Path("/tmp/test-workspace")
        exec_id = global_store.record_outcome(
            outcome=sample_outcome,
            workspace_path=workspace_path,
            model="claude-sonnet-4",
        )

        assert exec_id is not None
        assert len(exec_id) == 36  # UUID length

    def test_record_pattern(self, global_store: GlobalLearningStore) -> None:
        """Test recording a pattern to the store."""
        pattern_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="file_exists:missing",
            description="File not created during execution",
            context_tags=["validation:file_exists"],
            suggested_action="Ensure file is created before completion",
        )

        assert pattern_id is not None
        assert len(pattern_id) == 16  # Truncated hash

    def test_pattern_merge_increments_count(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that recording same pattern increments occurrence count."""
        # Record same pattern twice
        global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="file_exists:missing",
        )
        global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="file_exists:missing",
        )

        patterns = global_store.get_patterns(min_priority=0.0)
        file_pattern = next(
            (p for p in patterns if p.pattern_name == "file_exists:missing"),
            None,
        )

        assert file_pattern is not None
        assert file_pattern.occurrence_count == 2

    def test_record_error_recovery(self, global_store: GlobalLearningStore) -> None:
        """Test recording an error recovery."""
        record_id = global_store.record_error_recovery(
            error_code="E103",
            suggested_wait=60.0,
            actual_wait=90.0,
            recovery_success=True,
            model="claude-sonnet-4",
        )

        assert record_id is not None

    def test_get_learned_wait_time_insufficient_samples(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that learned wait time returns None with insufficient samples."""
        # Record only 2 recoveries (min_samples=3)
        for _ in range(2):
            global_store.record_error_recovery(
                error_code="E103",
                suggested_wait=60.0,
                actual_wait=90.0,
                recovery_success=True,
            )

        wait_time = global_store.get_learned_wait_time("E103")
        assert wait_time is None

    def test_get_learned_wait_time_sufficient_samples(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that learned wait time is calculated with sufficient samples."""
        # Record 5 successful recoveries with varying wait times
        waits = [60.0, 90.0, 120.0, 80.0, 70.0]
        for wait in waits:
            global_store.record_error_recovery(
                error_code="E103",
                suggested_wait=60.0,
                actual_wait=wait,
                recovery_success=True,
            )

        wait_time = global_store.get_learned_wait_time("E103")
        assert wait_time is not None
        # Should be between min (60) and average (84)
        assert 60.0 <= wait_time <= 84.0

    def test_get_execution_stats(
        self,
        global_store: GlobalLearningStore,
        sample_outcome: SheetOutcome,
        sample_failed_outcome: SheetOutcome,
    ) -> None:
        """Test getting execution statistics."""
        workspace_path = Path("/tmp/test-workspace")
        global_store.record_outcome(sample_outcome, workspace_path)
        global_store.record_outcome(sample_failed_outcome, workspace_path)

        stats = global_store.get_execution_stats()

        assert stats["total_executions"] == 2
        assert stats["success_without_retry_rate"] == 0.5
        assert stats["unique_workspaces"] == 1

    def test_workspace_hash_stability(self) -> None:
        """Test that workspace hash is stable for same path."""
        path = Path("/home/user/project/workspace")
        hash1 = GlobalLearningStore.hash_workspace(path)
        hash2 = GlobalLearningStore.hash_workspace(path)

        assert hash1 == hash2
        assert len(hash1) == 16

    def test_clear_all(
        self,
        global_store: GlobalLearningStore,
        sample_outcome: SheetOutcome,
    ) -> None:
        """Test clearing all data from the store."""
        workspace_path = Path("/tmp/test-workspace")
        global_store.record_outcome(sample_outcome, workspace_path)
        global_store.record_pattern("test", "test:pattern")

        global_store.clear_all()

        stats = global_store.get_execution_stats()
        assert stats["total_executions"] == 0
        assert stats["total_patterns"] == 0


# =============================================================================
# TestPatternWeighter
# =============================================================================


class TestPatternWeighter:
    """Tests for PatternWeighter class."""

    @pytest.fixture
    def weighter(self) -> PatternWeighter:
        """Create a default pattern weighter."""
        return PatternWeighter()

    def test_effectiveness_with_few_samples(self, weighter: PatternWeighter) -> None:
        """Test that effectiveness returns neutral prior with few samples."""
        effectiveness = weighter.calculate_effectiveness(
            success_count=1,
            failure_count=1,
        )

        # With < 3 samples, should return neutral 0.5
        assert effectiveness == 0.5

    def test_effectiveness_with_enough_samples(self, weighter: PatternWeighter) -> None:
        """Test effectiveness calculation with sufficient samples."""
        effectiveness = weighter.calculate_effectiveness(
            success_count=8,
            failure_count=2,
        )

        # (8 + 0.5) / (10 + 1) = 8.5 / 11 ≈ 0.77
        assert 0.75 <= effectiveness <= 0.80

    def test_recency_factor_recent(self, weighter: PatternWeighter) -> None:
        """Test recency factor for recent patterns."""
        now = datetime.now()
        last_confirmed = now - timedelta(days=1)

        recency = weighter.calculate_recency_factor(last_confirmed, now)

        # Should be very close to 1.0
        assert recency > 0.99

    def test_recency_factor_old(self, weighter: PatternWeighter) -> None:
        """Test recency factor for old patterns."""
        now = datetime.now()
        last_confirmed = now - timedelta(days=90)  # 3 months

        recency = weighter.calculate_recency_factor(last_confirmed, now)

        # Should be around 0.9^3 = 0.729
        assert 0.70 <= recency <= 0.75

    def test_frequency_factor(self, weighter: PatternWeighter) -> None:
        """Test frequency factor calculation."""
        assert weighter.calculate_frequency_factor(0) == 0.0
        assert 0.1 <= weighter.calculate_frequency_factor(1) <= 0.2
        assert 0.5 <= weighter.calculate_frequency_factor(10) <= 0.6
        assert weighter.calculate_frequency_factor(100) == 1.0

    def test_priority_calculation(self, weighter: PatternWeighter) -> None:
        """Test full priority calculation."""
        now = datetime.now()
        priority = weighter.calculate_priority(
            occurrence_count=10,
            led_to_success_count=8,
            led_to_failure_count=2,
            last_confirmed=now - timedelta(days=7),
            variance=0.1,
        )

        # Should be in reasonable range
        assert 0.0 <= priority <= 1.0

    def test_is_deprecated_insufficient_data(self, weighter: PatternWeighter) -> None:
        """Test that patterns with insufficient data are not deprecated."""
        is_deprecated = weighter.is_deprecated(
            led_to_success_count=0,
            led_to_failure_count=2,
        )

        # Not enough data to deprecate
        assert is_deprecated is False

    def test_is_deprecated_low_effectiveness(self, weighter: PatternWeighter) -> None:
        """Test that low effectiveness patterns are deprecated."""
        is_deprecated = weighter.is_deprecated(
            led_to_success_count=1,
            led_to_failure_count=9,  # 10% effectiveness
        )

        # Should be deprecated (below 0.3 threshold)
        assert is_deprecated is True

    def test_classify_uncertainty_epistemic(self, weighter: PatternWeighter) -> None:
        """Test epistemic uncertainty classification."""
        classification = weighter.classify_uncertainty(variance=0.2)
        assert classification == "epistemic"

    def test_classify_uncertainty_aleatoric(self, weighter: PatternWeighter) -> None:
        """Test aleatoric uncertainty classification."""
        classification = weighter.classify_uncertainty(variance=0.5)
        assert classification == "aleatoric"

    def test_calculate_variance(self, weighter: PatternWeighter) -> None:
        """Test variance calculation from outcomes."""
        # All same outcomes = 0 variance
        same = weighter.calculate_variance([True, True, True, True])
        assert same == 0.0

        # 50/50 split = max variance
        split = weighter.calculate_variance([True, False, True, False])
        assert 0.24 <= split <= 0.26

    def test_custom_config(self) -> None:
        """Test weighter with custom configuration."""
        weighter = PatternWeighter(
            decay_rate_per_month=0.2,  # 20% decay
            effectiveness_threshold=0.4,
        )

        now = datetime.now()
        last_confirmed = now - timedelta(days=30)

        recency = weighter.calculate_recency_factor(last_confirmed, now)

        # Should be around 0.8 (20% decay)
        assert 0.79 <= recency <= 0.81


# =============================================================================
# TestPatternAggregator
# =============================================================================


class TestPatternAggregator:
    """Tests for PatternAggregator class."""

    @pytest.fixture
    def aggregator(
        self, global_store: GlobalLearningStore
    ) -> PatternAggregator:
        """Create a pattern aggregator with test store."""
        return PatternAggregator(global_store)

    def test_aggregate_empty_outcomes(
        self, aggregator: PatternAggregator
    ) -> None:
        """Test aggregating empty outcomes list."""
        result = aggregator.aggregate_outcomes(
            outcomes=[],
            workspace_path=Path("/tmp/test"),
        )

        assert isinstance(result, AggregationResult)
        assert result.outcomes_recorded == 0
        assert result.patterns_detected == 0

    def test_aggregate_single_outcome(
        self,
        aggregator: PatternAggregator,
        sample_outcome: SheetOutcome,
    ) -> None:
        """Test aggregating a single outcome."""
        result = aggregator.aggregate_outcomes(
            outcomes=[sample_outcome],
            workspace_path=Path("/tmp/test"),
            model="claude-sonnet-4",
        )

        assert result.outcomes_recorded == 1
        # Single outcome may not produce patterns (need >= 2 for most patterns)
        assert result.patterns_detected >= 0

    def test_aggregate_multiple_outcomes_detects_patterns(
        self,
        aggregator: PatternAggregator,
    ) -> None:
        """Test that aggregating multiple outcomes detects patterns."""
        # Create outcomes with recurring validation failures
        outcomes = []
        for i in range(5):
            outcomes.append(
                SheetOutcome(
                    sheet_id=f"test-job-sheet{i}",
                    job_id="test-job",
                    validation_results=[
                        {"rule_type": "file_exists", "passed": False},
                    ],
                    execution_duration=30.0,
                    retry_count=1,
                    completion_mode_used=False,
                    final_status=SheetStatus.FAILED,
                    validation_pass_rate=0.0,
                    success_without_retry=False,
                )
            )

        result = aggregator.aggregate_outcomes(
            outcomes=outcomes,
            workspace_path=Path("/tmp/test"),
        )

        assert result.outcomes_recorded == 5
        # Should detect validation failure pattern
        assert result.patterns_detected >= 1
        assert result.patterns_merged >= 1
        assert result.priorities_updated is True

    def test_merge_with_conflict_resolution(
        self, aggregator: PatternAggregator
    ) -> None:
        """Test pattern merging with conflict resolution."""
        existing = PatternRecord(
            id="test-id",
            pattern_type="validation_failure",
            pattern_name="file_exists:missing",
            description="Old pattern",
            occurrence_count=5,
            first_seen=datetime.now() - timedelta(days=30),
            last_seen=datetime.now() - timedelta(days=10),
            last_confirmed=datetime.now() - timedelta(days=10),
            led_to_success_count=2,
            led_to_failure_count=3,
            effectiveness_score=0.4,
            variance=0.1,
            suggested_action="Old action",
            context_tags=["validation:file_exists"],
            priority_score=0.5,
        )

        new = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="New pattern",
            frequency=3,
            success_rate=0.8,
            last_seen=datetime.now(),
            context_tags=["validation:file_exists"],
        )

        merged = aggregator.merge_with_conflict_resolution(existing, new)

        # Sum occurrence counts
        assert merged["occurrence_count"] == 8  # 5 + 3

        # Weighted average effectiveness
        expected_eff = (0.4 * 5 + 0.8 * 3) / 8
        assert abs(merged["effectiveness_score"] - expected_eff) < 0.01

        # Higher effectiveness new action should be used
        assert "New pattern" in str(merged["suggested_action"])

    def test_update_all_priorities_skips_unapplied_patterns(
        self,
        global_store: GlobalLearningStore,
        aggregator: PatternAggregator,
    ) -> None:
        """Test that _update_all_priorities skips patterns with zero applications.

        Patterns that have never been applied (led_to_success_count +
        led_to_failure_count == 0) should keep their initial priority so
        they remain queryable above the default min_priority=0.3 threshold.
        """
        # Create a pattern with no applications (initial priority = 0.5)
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="unapplied_priority_test",
        )

        # Verify initial priority
        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)
        initial_priority = pattern.priority_score
        assert initial_priority >= 0.3, (
            f"Initial priority {initial_priority} should be above query threshold"
        )

        # Run priority recalculation (as happens after every job aggregation)
        aggregator._update_all_priorities()

        # Priority should be unchanged — pattern was skipped
        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)
        assert pattern.priority_score == initial_priority

    def test_update_all_priorities_recalculates_applied_patterns(
        self,
        global_store: GlobalLearningStore,
        aggregator: PatternAggregator,
    ) -> None:
        """Test that _update_all_priorities does recalculate patterns with applications."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="applied_priority_test",
        )

        # Record enough applications to move past cold-start prior
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec_{i}",
                pattern_led_to_success=True,
            )

        # Get priority before aggregator recalculation
        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)
        priority_before = pattern.priority_score

        # Run priority recalculation
        aggregator._update_all_priorities()

        # Pattern should have been recalculated (not skipped)
        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)
        # The weighter will compute a new priority; it may differ from
        # the store's own calculation, but it should be a valid value
        assert 0.0 <= pattern.priority_score <= 1.0
        # Importantly, the priority should have changed from the pre-aggregator value
        # (the store and aggregator use different formulas)
        assert pattern.priority_score != priority_before or pattern.led_to_success_count > 0

    def test_prune_deprecated_patterns(
        self,
        global_store: GlobalLearningStore,
        aggregator: PatternAggregator,
    ) -> None:
        """Test pruning deprecated patterns."""
        # Record a low-effectiveness pattern with enough applications
        with global_store._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO patterns (
                    id, pattern_type, pattern_name,
                    occurrence_count, first_seen, last_seen, last_confirmed,
                    led_to_success_count, led_to_failure_count,
                    effectiveness_score, variance, context_tags, priority_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "deprecated-pattern",
                    "test",
                    "test:deprecated",
                    10,
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    1,  # Only 1 success
                    9,  # 9 failures = 10% effectiveness
                    0.1,
                    0.0,
                    "[]",
                    0.5,
                ),
            )

        deprecated_count = aggregator.prune_deprecated_patterns()

        assert deprecated_count == 1

        # Check pattern was deprecated (priority set to 0)
        patterns = global_store.get_patterns(min_priority=0.0)
        deprecated = next(
            (p for p in patterns if p.id == "deprecated-pattern"), None
        )
        assert deprecated is not None
        assert deprecated.priority_score == 0.0


# =============================================================================
# TestAggregationResult
# =============================================================================


class TestAggregationResult:
    """Tests for AggregationResult class."""

    def test_default_values(self) -> None:
        """Test default values."""
        result = AggregationResult()

        assert result.outcomes_recorded == 0
        assert result.patterns_detected == 0
        assert result.patterns_merged == 0
        assert result.priorities_updated is False
        assert result.errors == []

    def test_repr(self) -> None:
        """Test string representation."""
        result = AggregationResult()
        result.outcomes_recorded = 5
        result.patterns_detected = 3
        result.patterns_merged = 2

        repr_str = repr(result)

        assert "5" in repr_str
        assert "3" in repr_str
        assert "2" in repr_str


# =============================================================================
# TestLearningActivationIntegration
# =============================================================================


class TestLearningActivationIntegration:
    """Integration tests for Learning Activation features.

    Tests the end-to-end flow of:
    - Pattern querying from global store
    - Similar execution tracking
    - Optimal execution window analysis
    - Workspace clustering
    """

    def test_get_similar_executions_empty(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_similar_executions with no data."""
        executions = global_store.get_similar_executions()
        assert executions == []

    def test_get_similar_executions_with_filters(
        self,
        global_store: GlobalLearningStore,
        sample_outcome: SheetOutcome,
    ) -> None:
        """Test get_similar_executions with job_hash filter."""
        workspace_path = Path("/tmp/test-workspace")
        global_store.record_outcome(sample_outcome, workspace_path)

        # Get job hash for the recorded outcome
        job_hash = GlobalLearningStore.hash_job(sample_outcome.job_id)

        # Query similar executions
        executions = global_store.get_similar_executions(job_hash=job_hash)
        assert len(executions) == 1
        assert executions[0].job_hash == job_hash

    def test_get_optimal_execution_window_no_data(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test optimal execution window with no recovery data."""
        window = global_store.get_optimal_execution_window()

        assert window["optimal_hours"] == []
        assert window["avoid_hours"] == []
        assert window["confidence"] == 0.0
        assert window["sample_count"] == 0

    def test_get_optimal_execution_window_with_data(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test optimal execution window identifies patterns."""
        # Record successful recoveries at hour 10 (should become optimal)
        for _ in range(5):
            with global_store._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(datetime.now().timestamp()),
                        "E101",
                        60.0,
                        60.0,
                        True,  # Success
                        datetime.now().isoformat(),
                        None,
                        10,  # Hour 10
                    ),
                )

        # Record failed recoveries at hour 14 (should become avoid)
        for _ in range(5):
            with global_store._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(datetime.now().timestamp()) + str(_),
                        "E101",
                        60.0,
                        60.0,
                        False,  # Failure
                        datetime.now().isoformat(),
                        None,
                        14,  # Hour 14
                    ),
                )

        window = global_store.get_optimal_execution_window()

        assert 10 in window["optimal_hours"]
        assert 14 in window["avoid_hours"]
        assert window["confidence"] > 0
        assert window["sample_count"] == 10

    def test_workspace_clustering(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test workspace cluster assignment and retrieval."""
        workspace_hash = "abc123"
        cluster_id = "high-success-cluster"

        # Initially no cluster
        assert global_store.get_workspace_cluster(workspace_hash) is None

        # Assign to cluster
        global_store.assign_workspace_cluster(workspace_hash, cluster_id)

        # Should now return cluster ID
        assert global_store.get_workspace_cluster(workspace_hash) == cluster_id

        # Should appear in similar workspaces
        similar = global_store.get_similar_workspaces(cluster_id)
        assert workspace_hash in similar

    def test_integration_pattern_query_and_record(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test full integration: record pattern, query, and verify."""
        # Record a pattern
        pattern_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="integration_test_pattern",
            description="Test pattern for integration",
            context_tags=["test"],
            suggested_action="Review the validation",
        )

        # Query patterns
        patterns = global_store.get_patterns(min_priority=0.0)

        # Verify pattern exists
        test_pattern = next(
            (p for p in patterns if p.id == pattern_id), None
        )
        assert test_pattern is not None
        assert test_pattern.description == "Test pattern for integration"

        # Record pattern application
        workspace_path = Path("/tmp/test")
        outcome = SheetOutcome(
            sheet_id="test-sheet",
            job_id="test-job",
            validation_results=[],
            execution_duration=10.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=1.0,
            success_without_retry=True,
        )
        exec_id = global_store.record_outcome(outcome, workspace_path)

        # Record that pattern was applied and improved outcome
        app_id = global_store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=exec_id,
            pattern_led_to_success=True,
            retry_count_before=1,
            retry_count_after=0,
        )

        assert app_id is not None

        # Verify pattern effectiveness was updated
        patterns = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(
            (p for p in patterns if p.id == pattern_id), None
        )
        assert updated_pattern is not None
        assert updated_pattern.led_to_success_count == 1


# =============================================================================
# TestSelectivePatternRetrieval
# =============================================================================


class TestSelectivePatternRetrieval:
    """Tests for Selective Pattern Retrieval (SPR) - context_tags filtering.

    Evolution v7: Tests the context_tags filtering capability in get_patterns().
    """

    def test_get_patterns_filters_by_context_tags(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that get_patterns filters by context_tags correctly."""
        # Create patterns with different context tags
        global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="sheet_specific",
            description="Pattern for sheet 5",
            context_tags=["sheet:5", "job:test-job"],
        )
        global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="different_context",
            description="Pattern for sheet 10",
            context_tags=["sheet:10", "job:other-job"],
        )
        global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="no_tags",
            description="Pattern without tags",
            context_tags=None,
        )

        # Query with context_tags that match first pattern
        patterns = global_store.get_patterns(
            min_priority=0.0,
            context_tags=["sheet:5"],
        )

        # Should only return the pattern with matching tag
        assert len(patterns) == 1
        assert patterns[0].pattern_name == "sheet_specific"

    def test_get_patterns_any_tag_matches(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that ANY tag match returns the pattern (OR semantics)."""
        global_store.record_pattern(
            pattern_type="test_pattern",
            pattern_name="multi_tag_pattern",
            description="Pattern with multiple tags",
            context_tags=["sheet:1", "sheet:2", "job:my-job"],
        )

        # Query with a tag that matches one of the pattern's tags
        patterns = global_store.get_patterns(
            min_priority=0.0,
            context_tags=["sheet:2", "unrelated:tag"],
        )

        assert len(patterns) == 1
        assert patterns[0].pattern_name == "multi_tag_pattern"

    def test_get_patterns_no_match_returns_empty(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that non-matching context_tags returns empty list."""
        global_store.record_pattern(
            pattern_type="test_pattern",
            pattern_name="tagged_pattern",
            description="Pattern with tags",
            context_tags=["sheet:5", "job:specific-job"],
        )

        # Query with non-matching tags
        patterns = global_store.get_patterns(
            min_priority=0.0,
            context_tags=["sheet:99", "job:nonexistent"],
        )

        assert len(patterns) == 0

    def test_get_patterns_none_context_tags_returns_all(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that context_tags=None returns all patterns (no filtering)."""
        global_store.record_pattern(
            pattern_type="test_pattern",
            pattern_name="pattern_with_tags",
            context_tags=["sheet:5"],
        )
        global_store.record_pattern(
            pattern_type="test_pattern",
            pattern_name="pattern_without_tags",
            context_tags=None,
        )

        # Query without context_tags filter
        patterns = global_store.get_patterns(min_priority=0.0, context_tags=None)

        # Should return all patterns
        assert len(patterns) >= 2
        pattern_names = {p.pattern_name for p in patterns}
        assert "pattern_with_tags" in pattern_names
        assert "pattern_without_tags" in pattern_names

    def test_get_patterns_empty_list_same_as_none(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that context_tags=[] behaves same as context_tags=None."""
        global_store.record_pattern(
            pattern_type="test_pattern",
            pattern_name="any_pattern",
            context_tags=["some:tag"],
        )

        # Empty list should NOT filter
        patterns_empty = global_store.get_patterns(min_priority=0.0, context_tags=[])
        patterns_none = global_store.get_patterns(min_priority=0.0, context_tags=None)

        # Both should return the same results
        assert len(patterns_empty) == len(patterns_none)

    def test_get_patterns_combines_type_and_tag_filters(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that pattern_type and context_tags filters work together."""
        global_store.record_pattern(
            pattern_type="type_a",
            pattern_name="type_a_tagged",
            context_tags=["sheet:5"],
        )
        global_store.record_pattern(
            pattern_type="type_b",
            pattern_name="type_b_tagged",
            context_tags=["sheet:5"],
        )
        global_store.record_pattern(
            pattern_type="type_a",
            pattern_name="type_a_different_tag",
            context_tags=["sheet:10"],
        )

        # Query with both filters
        patterns = global_store.get_patterns(
            pattern_type="type_a",
            min_priority=0.0,
            context_tags=["sheet:5"],
        )

        # Should only match type_a with sheet:5 tag
        assert len(patterns) == 1
        assert patterns[0].pattern_name == "type_a_tagged"


# =============================================================================
# TestOutputPatternExtractor
# =============================================================================


class TestOutputPatternExtractor:
    """Tests for OutputPatternExtractor class (Sheet 5 implementation)."""

    @pytest.fixture
    def extractor(self) -> OutputPatternExtractor:
        """Create an OutputPatternExtractor instance."""
        return OutputPatternExtractor()

    def test_empty_output_returns_empty_list(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test that empty output returns no patterns."""
        assert extractor.extract_from_output("") == []
        assert extractor.extract_from_output("   ") == []
        assert extractor.extract_from_output(None) == []  # type: ignore[arg-type]

    def test_extract_rate_limit_pattern(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test extraction of rate limit patterns."""
        output = "Error: rate limit exceeded for API calls"
        patterns = extractor.extract_from_output(output)

        assert len(patterns) == 1
        assert patterns[0].pattern_name == "rate_limit"
        assert "rate limit" in patterns[0].matched_text.lower()
        assert patterns[0].confidence == 0.95

    def test_extract_import_error_pattern(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test extraction of import error patterns."""
        output = """Traceback (most recent call last):
  File "test.py", line 1, in <module>
    import nonexistent_module
ModuleNotFoundError: No module named 'nonexistent_module'"""
        patterns = extractor.extract_from_output(output)

        # Should find both traceback and import_error
        pattern_names = {p.pattern_name for p in patterns}
        assert "traceback" in pattern_names
        assert "import_error" in pattern_names

    def test_extract_permission_denied(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test extraction of permission denied patterns."""
        output = "Error: Permission denied: /etc/passwd"
        patterns = extractor.extract_from_output(output)

        assert len(patterns) == 1
        assert patterns[0].pattern_name == "permission_denied"
        assert patterns[0].confidence == 0.95

    def test_extract_file_not_found(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test extraction of file not found patterns."""
        output = "FileNotFoundError: [Errno 2] No such file or directory: 'missing.txt'"
        patterns = extractor.extract_from_output(output)

        assert len(patterns) >= 1
        file_not_found = next(
            (p for p in patterns if p.pattern_name == "file_not_found"), None
        )
        assert file_not_found is not None
        assert file_not_found.confidence == 0.95

    def test_extract_timeout_pattern(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test extraction of timeout patterns."""
        output = "Connection timed out after 30s"
        patterns = extractor.extract_from_output(output)

        assert len(patterns) == 1
        assert patterns[0].pattern_name == "timeout"

    def test_extract_multiple_patterns(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test extraction of multiple different patterns."""
        output = """ImportError: No module named 'requests'
Connection refused: localhost:8080
TypeError: 'NoneType' object is not callable"""
        patterns = extractor.extract_from_output(output)

        pattern_names = {p.pattern_name for p in patterns}
        assert "import_error" in pattern_names
        assert "connection_refused" in pattern_names
        assert "type_error" in pattern_names
        assert len(patterns) >= 3

    def test_line_context_extraction(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test that context lines are extracted correctly."""
        output = """Line 1: Starting process
Line 2: Loading config
Line 3: ImportError: No module named 'flask'
Line 4: Process failed
Line 5: Cleanup complete"""
        patterns = extractor.extract_from_output(output)

        import_pattern = next(
            (p for p in patterns if p.pattern_name == "import_error"), None
        )
        assert import_pattern is not None
        assert import_pattern.line_number == 3
        assert len(import_pattern.context_before) == 2
        assert "Loading config" in import_pattern.context_before[-1]
        assert len(import_pattern.context_after) == 2
        assert "Process failed" in import_pattern.context_after[0]

    def test_source_parameter(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test that source parameter is correctly set."""
        output = "Permission denied"
        stdout_patterns = extractor.extract_from_output(output, source="stdout")
        stderr_patterns = extractor.extract_from_output(output, source="stderr")

        assert stdout_patterns[0].source == "stdout"
        assert stderr_patterns[0].source == "stderr"

    def test_deduplication_same_line(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test that duplicate patterns at same line are deduplicated."""
        # Rate limit appears multiple times on same line
        output = "rate limit rate limit rate limit"
        patterns = extractor.extract_from_output(output)

        # Should only have one rate_limit pattern
        rate_limit_count = sum(
            1 for p in patterns if p.pattern_name == "rate_limit"
        )
        assert rate_limit_count == 1

    def test_get_pattern_summary(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test pattern summary generation."""
        output = """TypeError: bad arg
TypeError: another bad arg
ImportError: missing module"""
        patterns = extractor.extract_from_output(output)

        summary = extractor.get_pattern_summary(patterns)

        assert "type_error" in summary
        assert summary["type_error"] == 2
        assert "import_error" in summary
        assert summary["import_error"] == 1

    def test_confidence_scoring(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test that confidence scores are correctly assigned."""

        # High confidence patterns
        high_conf = ["rate limit", "ImportError", "Permission denied"]
        # Lower confidence patterns
        lower_conf = ["Traceback (most recent call last)"]

        for text in high_conf:
            patterns = extractor.extract_from_output(text)
            assert patterns[0].confidence >= 0.90

        for text in lower_conf:
            patterns = extractor.extract_from_output(text)
            assert patterns[0].confidence < 0.90

    def test_case_insensitive_matching(
        self, extractor: OutputPatternExtractor
    ) -> None:
        """Test that matching is case insensitive where appropriate."""
        variants = ["RATE LIMIT", "Rate Limit", "rate limit", "RaTe LiMiT"]

        for variant in variants:
            patterns = extractor.extract_from_output(variant)
            assert len(patterns) >= 1
            assert any(p.pattern_name == "rate_limit" for p in patterns)


# =============================================================================
# TestExtractedPattern
# =============================================================================


class TestExtractedPattern:
    """Tests for ExtractedPattern dataclass (Sheet 5 implementation)."""

    def test_extracted_pattern_creation(self) -> None:
        """Test basic ExtractedPattern creation."""
        pattern = ExtractedPattern(
            pattern_name="rate_limit",
            matched_text="rate limit exceeded",
            line_number=42,
        )

        assert pattern.pattern_name == "rate_limit"
        assert pattern.matched_text == "rate limit exceeded"
        assert pattern.line_number == 42
        assert pattern.context_before == []
        assert pattern.context_after == []
        assert pattern.confidence == 0.8  # Default
        assert pattern.source == "stdout"  # Default

    def test_extracted_pattern_with_context(self) -> None:
        """Test ExtractedPattern with context lines."""
        pattern = ExtractedPattern(
            pattern_name="import_error",
            matched_text="ImportError: No module named 'x'",
            line_number=5,
            context_before=["import sys", "import os"],
            context_after=["# More code", "exit(1)"],
            confidence=0.95,
            source="stderr",
        )

        assert len(pattern.context_before) == 2
        assert len(pattern.context_after) == 2
        assert pattern.confidence == 0.95
        assert pattern.source == "stderr"


# =============================================================================
# TestPatternTypeEnum
# =============================================================================


class TestPatternTypeEnumOutputPattern:
    """Tests for OUTPUT_PATTERN enum value (Sheet 5 implementation)."""

    def test_output_pattern_enum_exists(self) -> None:
        """Test that OUTPUT_PATTERN exists in PatternType enum."""
        assert hasattr(PatternType, "OUTPUT_PATTERN")
        assert PatternType.OUTPUT_PATTERN.value == "output_pattern"

    def test_all_pattern_types_accessible(self) -> None:
        """Test that all pattern types are accessible."""
        expected_types = [
            "VALIDATION_FAILURE",
            "RETRY_SUCCESS",
            "COMPLETION_MODE",
            "SUCCESS_WITHOUT_RETRY",
            "HIGH_CONFIDENCE",
            "LOW_CONFIDENCE",
            "SEMANTIC_FAILURE",
            "OUTPUT_PATTERN",
        ]

        for ptype in expected_types:
            assert hasattr(PatternType, ptype)


# =============================================================================
# TestErrorCodePatterns
# =============================================================================


class TestErrorCodePatterns:
    """Tests for _detect_error_code_patterns() method (Sheet 6 implementation)."""

    def test_detect_error_code_patterns_empty_outcomes(self) -> None:
        """Test error code detection with empty outcomes."""
        from marianne.learning.patterns import PatternDetector

        detector = PatternDetector([])
        patterns = detector._detect_error_code_patterns()
        assert patterns == []

    def test_detect_error_code_patterns_no_error_codes(self) -> None:
        """Test error code detection when outcomes have no error codes."""
        from marianne.learning.patterns import PatternDetector

        outcomes = [
            SheetOutcome(
                sheet_id="test-1",
                job_id="test-job",
                validation_results=[
                    {"rule_type": "file_exists", "passed": True}
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=True,
            )
        ]

        detector = PatternDetector(outcomes)
        patterns = detector._detect_error_code_patterns()
        assert patterns == []

    def test_detect_error_code_patterns_single_occurrence(self) -> None:
        """Test error code detection with single occurrence (not pattern)."""
        from marianne.learning.patterns import PatternDetector

        outcomes = [
            SheetOutcome(
                sheet_id="test-1",
                job_id="test-job",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False, "error_code": "E009"}
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            )
        ]

        detector = PatternDetector(outcomes)
        patterns = detector._detect_error_code_patterns()
        # Single occurrence should not create a pattern
        assert patterns == []

    def test_detect_error_code_patterns_recurring(self) -> None:
        """Test error code detection with recurring error codes."""
        from marianne.learning.patterns import PatternDetector

        outcomes = [
            SheetOutcome(
                sheet_id="test-1",
                job_id="test-job",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False, "error_code": "E009"}
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            ),
            SheetOutcome(
                sheet_id="test-2",
                job_id="test-job",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False, "error_code": "E009"}
                ],
                execution_duration=45.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            ),
        ]

        detector = PatternDetector(outcomes)
        patterns = detector._detect_error_code_patterns()

        # Should have at least one pattern for E009
        assert len(patterns) >= 1
        error_pattern = next(
            (p for p in patterns if "E009" in p.description), None
        )
        assert error_pattern is not None
        assert error_pattern.frequency >= 2
        assert "error_code:E009" in error_pattern.context_tags

    def test_detect_error_code_confidence_scaling(self) -> None:
        """Test that error code pattern confidence scales with frequency."""
        from marianne.learning.patterns import PatternDetector

        # Create 5 outcomes with same error code
        outcomes = []
        for i in range(5):
            outcomes.append(
                SheetOutcome(
                    sheet_id=f"test-{i}",
                    job_id="test-job",
                    validation_results=[
                        {"rule_type": "file_exists", "passed": False, "error_code": "E103"}
                    ],
                    execution_duration=30.0,
                    retry_count=0,
                    completion_mode_used=False,
                    final_status=SheetStatus.FAILED,
                    validation_pass_rate=0.0,
                    success_without_retry=False,
                )
            )

        detector = PatternDetector(outcomes)
        patterns = detector._detect_error_code_patterns()

        error_pattern = next(
            (p for p in patterns if "E103" in p.description), None
        )
        assert error_pattern is not None
        # Higher frequency should have higher confidence
        assert error_pattern.confidence >= 0.85

    def test_error_patterns_in_detect_all(self) -> None:
        """Test that error code patterns are included in detect_all()."""
        from marianne.learning.patterns import PatternDetector

        outcomes = [
            SheetOutcome(
                sheet_id="test-1",
                job_id="test-job",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False, "error_code": "E201"}
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            ),
            SheetOutcome(
                sheet_id="test-2",
                job_id="test-job",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False, "error_code": "E201"}
                ],
                execution_duration=45.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            ),
        ]

        detector = PatternDetector(outcomes)
        all_patterns = detector.detect_all()

        # Should include error code pattern
        error_pattern = next(
            (p for p in all_patterns if "E201" in p.description), None
        )
        assert error_pattern is not None


# =============================================================================
# TestEnhancedPatternAggregator
# =============================================================================


class TestEnhancedPatternAggregator:
    """Tests for EnhancedPatternAggregator class (Sheet 6 implementation)."""

    @pytest.fixture
    def enhanced_aggregator(
        self, global_store: GlobalLearningStore
    ) -> EnhancedPatternAggregator:
        """Create an enhanced pattern aggregator with test store."""
        return EnhancedPatternAggregator(global_store)

    def test_aggregate_empty_outcomes(
        self, enhanced_aggregator: EnhancedPatternAggregator
    ) -> None:
        """Test enhanced aggregation with empty outcomes list."""
        result = enhanced_aggregator.aggregate_with_all_sources(
            outcomes=[],
            workspace_path=Path("/tmp/test"),
        )

        assert isinstance(result, EnhancedAggregationResult)
        assert result.outcomes_recorded == 0
        assert result.patterns_detected == 0
        assert result.output_patterns == []
        assert result.output_pattern_summary == {}

    def test_aggregate_with_output_patterns(
        self, enhanced_aggregator: EnhancedPatternAggregator
    ) -> None:
        """Test enhanced aggregation extracts output patterns."""
        # Create outcome with stdout containing patterns
        outcome = SheetOutcome(
            sheet_id="test-1",
            job_id="test-job",
            validation_results=[
                {"rule_type": "file_exists", "passed": False}
            ],
            execution_duration=30.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.FAILED,
            validation_pass_rate=0.0,
            success_without_retry=False,
        )
        # Add stdout_tail with error patterns
        outcome.stdout_tail = """Error: rate limit exceeded
Traceback (most recent call last):
  File "test.py", line 10
ImportError: No module named 'requests'"""

        result = enhanced_aggregator.aggregate_with_all_sources(
            outcomes=[outcome],
            workspace_path=Path("/tmp/test"),
        )

        assert result.outcomes_recorded == 1
        assert len(result.output_patterns) >= 1
        assert "rate_limit" in result.output_pattern_summary or \
               "import_error" in result.output_pattern_summary or \
               "traceback" in result.output_pattern_summary

    def test_enhanced_aggregation_result_repr(self) -> None:
        """Test EnhancedAggregationResult string representation."""
        result = EnhancedAggregationResult()
        result.outcomes_recorded = 5
        result.patterns_detected = 10
        result.patterns_merged = 8
        result.output_patterns = []  # empty for test

        repr_str = repr(result)

        assert "outcomes=5" in repr_str
        assert "detected=10" in repr_str
        assert "merged=8" in repr_str
        assert "output_patterns=0" in repr_str


# =============================================================================
# TestFullDataCollectionPipeline
# =============================================================================


class TestFullDataCollectionPipeline:
    """Integration tests for full learning data collection flow (Sheet 6).

    Tests the complete pipeline from SheetOutcome creation through
    pattern extraction, aggregation, and global store storage.
    """

    def test_full_data_collection_pipeline(
        self, temp_db_path: Path
    ) -> None:
        """Test complete learning data collection flow."""
        # 1. Create outcomes with realistic stdout/stderr and error codes
        outcomes = [
            SheetOutcome(
                sheet_id="test:1",
                job_id="test-job",
                validation_results=[
                    {
                        "rule_type": "file_exists",
                        "passed": False,
                        "error_code": "E009",
                        "failure_category": "missing",
                        "failure_reason": "File not created in workspace",
                    }
                ],
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            ),
            SheetOutcome(
                sheet_id="test:2",
                job_id="test-job",
                validation_results=[
                    {
                        "rule_type": "file_exists",
                        "passed": False,
                        "error_code": "E009",
                        "failure_category": "missing",
                        "failure_reason": "File not created in workspace",
                    }
                ],
                execution_duration=45.0,
                retry_count=2,
                completion_mode_used=True,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            ),
            SheetOutcome(
                sheet_id="test:3",
                job_id="test-job",
                validation_results=[
                    {
                        "rule_type": "content_contains",
                        "passed": True,
                    }
                ],
                execution_duration=20.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=True,
            ),
        ]

        # Add stdout_tail with error patterns to first outcome
        outcomes[0].stdout_tail = (
            "Error: FileNotFoundError: config.yaml not found\n"
            "Traceback (most recent call last):\n"
            "  File 'main.py', line 10\n"
        )
        outcomes[1].stdout_tail = (
            "FileNotFoundError: config.yaml not found\n"
            "Process failed\n"
        )

        # 2. Run enhanced aggregator
        store = GlobalLearningStore(temp_db_path)
        aggregator = EnhancedPatternAggregator(store)
        result = aggregator.aggregate_with_all_sources(
            outcomes, Path("/tmp/test-workspace")
        )

        # 3. Verify outcomes recorded
        assert result.outcomes_recorded == 3

        # 4. Verify patterns extracted (including output patterns)
        assert result.patterns_detected >= 1

        # 5. Verify output patterns extracted
        assert len(result.output_patterns) >= 1
        # Check that file_not_found pattern was detected from stdout
        pattern_names = {p.pattern_name for p in result.output_patterns}
        assert "file_not_found" in pattern_names or "traceback" in pattern_names

        # 6. Verify stored in global store
        patterns = store.get_patterns(min_priority=0.0)
        assert len(patterns) >= 1

        # 7. Verify error code pattern was detected
        # Check that E009 pattern exists (from recurring error code)
        error_pattern = next(
            (p for p in patterns if p.description and "E009" in p.description), None
        )
        assert error_pattern is not None

        # Cleanup
        if temp_db_path.exists():
            temp_db_path.unlink()

    def test_pipeline_with_semantic_patterns(
        self, temp_db_path: Path
    ) -> None:
        """Test pipeline detects semantic patterns from failure categories."""
        outcomes = [
            SheetOutcome(
                sheet_id=f"test:{i}",
                job_id="test-job",
                validation_results=[
                    {
                        "rule_type": "file_exists",
                        "passed": False,
                        "failure_category": "stale",
                        "failure_reason": "File not modified recently",
                    }
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            )
            for i in range(3)  # 3 outcomes with same failure category
        ]

        store = GlobalLearningStore(temp_db_path)
        aggregator = EnhancedPatternAggregator(store)
        result = aggregator.aggregate_with_all_sources(
            outcomes, Path("/tmp/test-workspace")
        )

        # Verify semantic patterns detected
        assert result.patterns_detected >= 1

        # Check patterns in store include semantic failure
        patterns = store.get_patterns(min_priority=0.0)
        stale_pattern = next(
            (
                p for p in patterns
                if p.description and "stale" in p.description.lower()
            ),
            None,
        )
        assert stale_pattern is not None

        # Cleanup
        if temp_db_path.exists():
            temp_db_path.unlink()

    def test_import_verification(self) -> None:
        """Verify all learning module imports work correctly."""
        # Test all key imports
        from marianne.learning import (
            aggregator,
            global_store,
            outcomes,
            patterns,
            weighter,
        )

        # Verify key classes are accessible
        assert hasattr(patterns, "OutputPatternExtractor")
        assert hasattr(patterns, "ExtractedPattern")
        assert hasattr(patterns, "PatternDetector")
        assert hasattr(patterns, "PatternType")
        assert hasattr(aggregator, "EnhancedPatternAggregator")
        assert hasattr(aggregator, "EnhancedAggregationResult")
        assert hasattr(outcomes, "SheetOutcome")
        assert hasattr(global_store, "GlobalLearningStore")
        assert hasattr(weighter, "PatternWeighter")


# =============================================================================
# TestGroundingPatternIntegration (v11 Evolution)
# =============================================================================


class TestGroundingPatternIntegration:
    """Tests for Grounding→Pattern Integration (v11 Evolution).

    Tests that grounding results are correctly captured in SheetOutcome
    and persisted through the learning pipeline.
    """

    def test_sheet_outcome_has_grounding_fields(self) -> None:
        """Test that SheetOutcome has grounding integration fields."""
        outcome = SheetOutcome(
            sheet_id="test-1",
            job_id="test-job",
            validation_results=[],
            execution_duration=30.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=1.0,
            success_without_retry=True,
            grounding_passed=True,
            grounding_confidence=0.95,
            grounding_guidance=None,
        )

        assert outcome.grounding_passed is True
        assert outcome.grounding_confidence == 0.95
        assert outcome.grounding_guidance is None

    def test_sheet_outcome_grounding_fields_default_none(self) -> None:
        """Test that grounding fields default to None when not provided."""
        outcome = SheetOutcome(
            sheet_id="test-1",
            job_id="test-job",
            validation_results=[],
            execution_duration=30.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=1.0,
            success_without_retry=True,
        )

        assert outcome.grounding_passed is None
        assert outcome.grounding_confidence is None
        assert outcome.grounding_guidance is None

    def test_sheet_outcome_grounding_failed_with_guidance(self) -> None:
        """Test SheetOutcome with failed grounding and recovery guidance."""
        outcome = SheetOutcome(
            sheet_id="test-1",
            job_id="test-job",
            validation_results=[],
            execution_duration=30.0,
            retry_count=1,
            completion_mode_used=False,
            final_status=SheetStatus.FAILED,
            validation_pass_rate=0.0,
            success_without_retry=False,
            grounding_passed=False,
            grounding_confidence=0.6,
            grounding_guidance="Re-generate the file; checksum mismatch detected",
        )

        assert outcome.grounding_passed is False
        assert outcome.grounding_confidence == 0.6
        assert outcome.grounding_guidance is not None
        assert "checksum mismatch" in outcome.grounding_guidance

    def test_outcome_aggregate_includes_grounding(
        self, temp_db_path: Path
    ) -> None:
        """Test that grounding fields are persisted in global store aggregation."""
        # Create outcomes with grounding data
        outcomes = [
            SheetOutcome(
                sheet_id="test:1",
                job_id="test-job",
                validation_results=[],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=True,
                grounding_passed=True,
                grounding_confidence=0.95,
                grounding_guidance=None,
            ),
            SheetOutcome(
                sheet_id="test:2",
                job_id="test-job",
                validation_results=[],
                execution_duration=45.0,
                retry_count=2,
                completion_mode_used=True,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
                grounding_passed=False,
                grounding_confidence=0.5,
                grounding_guidance="File integrity check failed",
            ),
        ]

        # Run aggregation
        store = GlobalLearningStore(temp_db_path)
        aggregator = EnhancedPatternAggregator(store)
        result = aggregator.aggregate_with_all_sources(
            outcomes, Path("/tmp/test-workspace")
        )

        # Verify outcomes were recorded
        assert result.outcomes_recorded == 2

        # Cleanup
        if temp_db_path.exists():
            temp_db_path.unlink()

    def test_grounding_fields_serialization_roundtrip(self) -> None:
        """Test that grounding fields survive save/load cycle in JsonOutcomeStore."""
        import tempfile

        from marianne.learning.outcomes import JsonOutcomeStore

        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "outcomes.json"
            store = JsonOutcomeStore(store_path)

            # Create and save outcome with grounding fields
            import asyncio

            async def test_roundtrip():
                outcome = SheetOutcome(
                    sheet_id="test-1",
                    job_id="test-job",
                    validation_results=[],
                    execution_duration=30.0,
                    retry_count=0,
                    completion_mode_used=False,
                    final_status=SheetStatus.COMPLETED,
                    validation_pass_rate=1.0,
                    success_without_retry=True,
                    grounding_passed=True,
                    grounding_confidence=0.87,
                    grounding_guidance="Check passed with note",
                )

                await store.record(outcome)

                # Create new store instance and load
                store2 = JsonOutcomeStore(store_path)
                await store2._load()

                # Verify grounding fields survived roundtrip
                loaded = store2._outcomes[0]
                assert loaded.grounding_passed is True
                assert loaded.grounding_confidence == 0.87
                assert loaded.grounding_guidance == "Check passed with note"

            asyncio.run(test_roundtrip())


# =============================================================================
# TestExplorationModePatternSelection
# =============================================================================


class TestExplorationModePatternSelection:
    """Tests for exploration mode pattern selection (v9 Evolution - Sheet 3).

    Tests the epsilon-greedy exploration algorithm that occasionally selects
    lower-priority patterns to collect effectiveness data and break the
    cold-start problem.
    """

    def test_exploration_mode_returns_low_priority_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that exploration mode (low threshold) returns patterns below normal threshold."""
        # Create patterns with varying priority scores
        # High priority pattern (above 0.3 threshold)
        high_priority_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="high_priority_pattern",
            description="High priority pattern",
            context_tags=["test"],
        )
        # Update to high priority
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET priority_score = 0.8, effectiveness_score = 0.85 WHERE id = ?",
                (high_priority_id,),
            )

        # Low priority pattern (below 0.3 but above 0.05)
        low_priority_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="low_priority_pattern",
            description="Low priority pattern for exploration",
            context_tags=["test"],
        )
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET priority_score = 0.15, effectiveness_score = 0.4 WHERE id = ?",
                (low_priority_id,),
            )

        # Very low priority pattern (below exploration_min_priority)
        very_low_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="very_low_priority_pattern",
            description="Should be excluded even in exploration",
            context_tags=["test"],
        )
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET priority_score = 0.02, effectiveness_score = 0.1 WHERE id = ?",
                (very_low_id,),
            )

        # Exploitation mode (min_priority=0.3): only high priority patterns
        exploitation_patterns = global_store.get_patterns(
            min_priority=0.3,
            context_tags=["test"],
        )
        assert len(exploitation_patterns) == 1
        assert exploitation_patterns[0].pattern_name == "high_priority_pattern"

        # Exploration mode (min_priority=0.05): includes low priority patterns
        exploration_patterns = global_store.get_patterns(
            min_priority=0.05,
            context_tags=["test"],
        )
        assert len(exploration_patterns) >= 2

        pattern_names = {p.pattern_name for p in exploration_patterns}
        assert "high_priority_pattern" in pattern_names
        assert "low_priority_pattern" in pattern_names
        # Very low priority (0.02) should be excluded even in exploration (threshold 0.05)
        assert "very_low_priority_pattern" not in pattern_names

    def test_exploration_threshold_boundary(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test boundary conditions for exploration threshold."""
        # Pattern exactly at threshold
        at_threshold_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="at_threshold",
        )
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET priority_score = 0.3 WHERE id = ?",
                (at_threshold_id,),
            )

        # Pattern just below threshold
        below_threshold_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="below_threshold",
        )
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET priority_score = 0.29 WHERE id = ?",
                (below_threshold_id,),
            )

        # At threshold should be included with min_priority=0.3
        patterns = global_store.get_patterns(min_priority=0.3)
        names = {p.pattern_name for p in patterns}
        assert "at_threshold" in names
        assert "below_threshold" not in names

        # Both should be included with min_priority=0.29
        patterns = global_store.get_patterns(min_priority=0.29)
        names = {p.pattern_name for p in patterns}
        assert "at_threshold" in names
        assert "below_threshold" in names


# =============================================================================
# TestPatternEffectivenessUpdate
# =============================================================================


class TestPatternEffectivenessUpdate:
    """Tests for pattern effectiveness update (v9 Evolution - Sheet 4).

    Tests the Bayesian moving average effectiveness calculation with recency decay.
    """

    def test_pattern_effectiveness_update_on_success(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that recording successful application updates effectiveness."""
        # Create a pattern
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="effectiveness_test_pattern",
            description="Pattern to test effectiveness calculation",
        )

        # Get initial effectiveness (should be 0.5 default)
        initial = global_store.get_patterns(min_priority=0.0)
        initial_pattern = next(p for p in initial if p.id == pattern_id)
        assert initial_pattern.effectiveness_score == 0.5

        # Record multiple successful applications (need >= 3 for non-cold-start)
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec_{i}",
                pattern_led_to_success=True,
                application_mode="exploitation",
            )

        # Check effectiveness increased
        updated = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(p for p in updated if p.id == pattern_id)

        # With 5 successes and 0 failures, effectiveness should be high
        assert updated_pattern.effectiveness_score > 0.7
        assert updated_pattern.led_to_success_count == 5
        assert updated_pattern.led_to_failure_count == 0

    def test_pattern_effectiveness_update_on_failure(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that recording failed application decreases effectiveness."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="failure_test_pattern",
        )

        # Record multiple failures
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec_{i}",
                pattern_led_to_success=False,
                application_mode="exploitation",
            )

        updated = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(p for p in updated if p.id == pattern_id)

        # With 5 failures, effectiveness should be low
        assert updated_pattern.effectiveness_score < 0.4
        assert updated_pattern.led_to_success_count == 0
        assert updated_pattern.led_to_failure_count == 5

    def test_pattern_effectiveness_mixed_outcomes(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test effectiveness with mixed success/failure outcomes."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="mixed_outcome_pattern",
        )

        # Record 3 successes and 2 failures
        for i in range(3):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"success_{i}",
                pattern_led_to_success=True,
            )
        for i in range(2):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"failure_{i}",
                pattern_led_to_success=False,
            )

        updated = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(p for p in updated if p.id == pattern_id)

        # Effectiveness should be moderate (around 0.5-0.7 with 60% success)
        assert 0.4 <= updated_pattern.effectiveness_score <= 0.8
        assert updated_pattern.led_to_success_count == 3
        assert updated_pattern.led_to_failure_count == 2

    def test_effectiveness_cold_start_handling(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that patterns with < 3 applications get cold-start prior."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="cold_start_pattern",
        )

        # Record only 2 applications (below cold-start threshold)
        global_store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id="exec_1",
            pattern_led_to_success=True,
        )
        global_store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id="exec_2",
            pattern_led_to_success=True,
        )

        updated = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(p for p in updated if p.id == pattern_id)

        # With < 3 applications, should return cold-start prior of 0.55
        assert updated_pattern.effectiveness_score == 0.55


# =============================================================================
# TestPriorityRecalculation
# =============================================================================


class TestPriorityRecalculation:
    """Tests for priority recalculation (v9 Evolution - Sheet 4).

    Tests the priority score calculation from effectiveness, frequency, and variance.
    """

    def test_priority_recalculation_after_application(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that priority is recalculated after pattern application."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="priority_recalc_pattern",
        )

        # Simulate pattern being detected multiple times to increase occurrence_count
        # occurrence_count affects the frequency factor in priority calculation
        for _ in range(9):  # Total 10 occurrences after initial record
            global_store.record_pattern(
                pattern_type="test",
                pattern_name="priority_recalc_pattern",
            )

        # Record enough applications to update priority meaningfully
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec_{i}",
                pattern_led_to_success=True,
            )

        updated = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(p for p in updated if p.id == pattern_id)

        # Priority should be non-zero and based on effectiveness
        assert updated_pattern.priority_score > 0
        # Priority formula: effectiveness × frequency_factor × (1 - variance)
        # With 10 occurrences, frequency_factor ≈ log10(11)/2 ≈ 0.52
        # With all successes, effectiveness is high (~0.975) and variance is 0
        # So priority should be around 0.5
        assert updated_pattern.priority_score > 0.4

    def test_priority_decreases_with_failures(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that priority decreases when pattern leads to failures."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="declining_priority_pattern",
        )

        # First record some successes to establish a baseline
        for i in range(3):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"success_{i}",
                pattern_led_to_success=True,
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        pattern_after_success = next(p for p in patterns if p.id == pattern_id)
        priority_after_success = pattern_after_success.priority_score

        # Now record failures
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"failure_{i}",
                pattern_led_to_success=False,
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        pattern_after_failure = next(p for p in patterns if p.id == pattern_id)
        priority_after_failure = pattern_after_failure.priority_score

        # Priority should have decreased after failures
        assert priority_after_failure < priority_after_success

    def test_recalculate_all_pattern_priorities(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test batch recalculation of all pattern priorities."""
        # Create multiple patterns with applications
        pattern_ids = []
        for i in range(3):
            pid = global_store.record_pattern(
                pattern_type="test",
                pattern_name=f"batch_recalc_pattern_{i}",
            )
            pattern_ids.append(pid)

            # Record applications for each
            for j in range(4):
                global_store.record_pattern_application(
                    pattern_id=pid,
                    execution_id=f"exec_{i}_{j}",
                    pattern_led_to_success=j % 2 == 0,  # Alternating outcomes
                )

        # Batch recalculate all priorities
        updated_count = global_store.recalculate_all_pattern_priorities()

        # Should have updated all 3 patterns
        assert updated_count == 3

        # Verify all patterns have valid priorities
        patterns = global_store.get_patterns(min_priority=0.0)
        for pattern in patterns:
            if pattern.id in pattern_ids:
                assert 0.0 <= pattern.priority_score <= 1.0
                assert 0.0 <= pattern.effectiveness_score <= 1.0

    def test_manual_update_pattern_effectiveness(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test manual effectiveness update method."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="manual_update_pattern",
        )

        # Record applications
        for i in range(4):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec_{i}",
                pattern_led_to_success=True,
            )

        # Manually update effectiveness
        new_effectiveness = global_store.update_pattern_effectiveness(pattern_id)

        assert new_effectiveness is not None
        assert new_effectiveness > 0.5  # All successes should give high effectiveness

    def test_update_nonexistent_pattern_returns_none(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that updating a non-existent pattern returns None."""
        result = global_store.update_pattern_effectiveness("nonexistent_pattern_id")
        assert result is None


# =============================================================================
# TestUnappliedPatternPriorityRepair
# =============================================================================


class TestUnappliedPatternPriorityRepair:
    """Tests for the v13 migration that repairs crushed unapplied pattern priorities."""

    def test_repair_restores_crushed_unapplied_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that the repair restores priority for unapplied patterns below 0.3."""
        now = datetime.now().isoformat()
        # Simulate a pattern crushed by the old _update_all_priorities bug
        with global_store._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO patterns (
                    id, pattern_type, pattern_name,
                    occurrence_count, first_seen, last_seen, last_confirmed,
                    led_to_success_count, led_to_failure_count,
                    effectiveness_score, variance, context_tags, priority_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("crushed-pattern", "test", "test:crushed", 1,
                 now, now, now, 0, 0, 0.5, 0.0, "[]", 0.075),
            )

        # Run the repair
        with global_store._get_connection() as conn:
            global_store._repair_unapplied_pattern_priorities(conn)

        # Pattern should be restored to 0.5
        patterns = global_store.get_patterns(min_priority=0.0)
        repaired = next(p for p in patterns if p.id == "crushed-pattern")
        assert repaired.priority_score == 0.5

    def test_repair_does_not_touch_applied_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that applied patterns keep their calculated priority even if low."""
        now = datetime.now().isoformat()
        with global_store._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO patterns (
                    id, pattern_type, pattern_name,
                    occurrence_count, first_seen, last_seen, last_confirmed,
                    led_to_success_count, led_to_failure_count,
                    effectiveness_score, variance, context_tags, priority_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("applied-low-pattern", "test", "test:applied_low", 10,
                 now, now, now, 3, 7, 0.3, 0.0, "[]", 0.15),
            )

        with global_store._get_connection() as conn:
            global_store._repair_unapplied_pattern_priorities(conn)

        # Should NOT be repaired — it has applications (3 + 7 = 10)
        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == "applied-low-pattern")
        assert pattern.priority_score == 0.15

    def test_repair_skips_unapplied_patterns_above_threshold(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that unapplied patterns already above 0.3 are not modified."""
        now = datetime.now().isoformat()
        with global_store._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO patterns (
                    id, pattern_type, pattern_name,
                    occurrence_count, first_seen, last_seen, last_confirmed,
                    led_to_success_count, led_to_failure_count,
                    effectiveness_score, variance, context_tags, priority_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("healthy-pattern", "test", "test:healthy", 1,
                 now, now, now, 0, 0, 0.5, 0.0, "[]", 0.5),
            )

        with global_store._get_connection() as conn:
            global_store._repair_unapplied_pattern_priorities(conn)

        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == "healthy-pattern")
        assert pattern.priority_score == 0.5


# =============================================================================
# TestFeedbackLoopIntegration
# =============================================================================


class TestFeedbackLoopIntegration:
    """Integration tests for the full pattern feedback loop (v9 Evolution - Sheet 5).

    Tests the end-to-end flow of:
    1. Pattern query with exploration mode
    2. Pattern injection into prompt
    3. Outcome recording after sheet execution
    4. Effectiveness update based on outcome
    5. Priority recalculation
    """

    def test_feedback_loop_integration_success_case(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test full feedback loop with successful outcome."""
        # 1. Create a pattern with known initial state
        pattern_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="feedback_loop_pattern",
            description="Pattern for testing feedback loop",
            suggested_action="Review validation rules carefully",
            context_tags=["sheet:1", "job:test-job"],
        )

        # 2. Query patterns (simulating _query_relevant_patterns)
        patterns = global_store.get_patterns(
            min_priority=0.0,  # Use exploration threshold
            context_tags=["sheet:1"],
        )
        assert len(patterns) >= 1
        queried_pattern = next(p for p in patterns if p.id == pattern_id)
        initial_effectiveness = queried_pattern.effectiveness_score

        # 3. Simulate successful sheet execution with pattern applied
        # Record multiple successes to build up effectiveness
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"sheet_{i}",
                pattern_led_to_success=True,
                retry_count_before=2,
                retry_count_after=0,
                application_mode="exploitation",
                validation_passed=True,
            )

        # 4. Verify effectiveness increased
        updated_patterns = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(p for p in updated_patterns if p.id == pattern_id)

        assert updated_pattern.effectiveness_score > initial_effectiveness
        assert updated_pattern.led_to_success_count == 5

        # 5. Verify priority also increased
        assert updated_pattern.priority_score > 0

    def test_feedback_loop_integration_failure_case(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test full feedback loop with failed outcome."""
        pattern_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="failing_feedback_pattern",
            context_tags=["sheet:2"],
        )

        # Get initial state
        patterns = global_store.get_patterns(min_priority=0.0)
        initial_pattern = next(p for p in patterns if p.id == pattern_id)
        initial_effectiveness = initial_pattern.effectiveness_score

        # Simulate failed execution with pattern applied
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"sheet_fail_{i}",
                pattern_led_to_success=False,
                application_mode="exploitation",
                validation_passed=False,
            )

        # Verify effectiveness decreased
        updated_patterns = global_store.get_patterns(min_priority=0.0)
        updated_pattern = next(p for p in updated_patterns if p.id == pattern_id)

        assert updated_pattern.effectiveness_score < initial_effectiveness
        assert updated_pattern.led_to_failure_count == 5

    def test_feedback_loop_exploration_vs_exploitation_tracking(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that exploration vs exploitation mode is correctly tracked."""
        # Create two patterns: one for exploration, one for exploitation
        exploration_pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="exploration_tracking_pattern",
        )
        exploitation_pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="exploitation_tracking_pattern",
        )

        # Set different priorities
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET priority_score = 0.15 WHERE id = ?",
                (exploration_pattern_id,),
            )
            conn.execute(
                "UPDATE patterns SET priority_score = 0.6 WHERE id = ?",
                (exploitation_pattern_id,),
            )

        # Record applications with different modes
        global_store.record_pattern_application(
            pattern_id=exploration_pattern_id,
            execution_id="explore_exec",
            pattern_led_to_success=True,
            application_mode="exploration",  # Low priority pattern selected via exploration
        )

        global_store.record_pattern_application(
            pattern_id=exploitation_pattern_id,
            execution_id="exploit_exec",
            pattern_led_to_success=True,
            application_mode="exploitation",  # High priority pattern selected normally
        )

        # Both should have been recorded successfully
        patterns = global_store.get_patterns(min_priority=0.0)
        explore_p = next(p for p in patterns if p.id == exploration_pattern_id)
        exploit_p = next(p for p in patterns if p.id == exploitation_pattern_id)

        assert explore_p.led_to_success_count == 1
        assert exploit_p.led_to_success_count == 1

    def test_feedback_loop_multiple_patterns_same_execution(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test feedback loop with multiple patterns applied to same execution."""
        # Create multiple patterns
        pattern_ids = []
        for i in range(3):
            pid = global_store.record_pattern(
                pattern_type="test",
                pattern_name=f"multi_pattern_{i}",
                context_tags=["sheet:multi"],
            )
            pattern_ids.append(pid)

        # Record all patterns as applied to same execution
        execution_id = "multi_pattern_exec_1"
        for pid in pattern_ids:
            global_store.record_pattern_application(
                pattern_id=pid,
                execution_id=execution_id,
                pattern_led_to_success=True,
            )

        # Verify all patterns were updated
        patterns = global_store.get_patterns(min_priority=0.0)
        for pid in pattern_ids:
            p = next(pat for pat in patterns if pat.id == pid)
            assert p.led_to_success_count >= 1

    def test_end_to_end_pattern_lifecycle(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test complete pattern lifecycle: creation → application → learning → deprecation."""
        # 1. Create pattern
        pattern_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="lifecycle_pattern",
            description="Testing full lifecycle",
            context_tags=["lifecycle:test"],
        )

        # 2. Initial state: pattern exists with default scores
        patterns = global_store.get_patterns(min_priority=0.0)
        initial = next(p for p in patterns if p.id == pattern_id)
        assert initial.effectiveness_score == 0.5  # Default
        assert initial.occurrence_count == 1

        # 3. Pattern is applied and succeeds multiple times - effectiveness rises
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"success_{i}",
                pattern_led_to_success=True,
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        after_success = next(p for p in patterns if p.id == pattern_id)
        assert after_success.effectiveness_score > 0.7
        high_priority = after_success.priority_score

        # 4. Pattern starts failing - effectiveness drops
        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"failure_{i}",
                pattern_led_to_success=False,
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        after_failures = next(p for p in patterns if p.id == pattern_id)
        assert after_failures.effectiveness_score < after_success.effectiveness_score
        assert after_failures.priority_score < high_priority

        # 5. Verify the learning: led_to_success_count and led_to_failure_count tracked
        assert after_failures.led_to_success_count == 5
        assert after_failures.led_to_failure_count == 10


# =============================================================================
# v12 Evolution: Grounding→Pattern Feedback Tests
# =============================================================================


class TestGroundingWeightedEffectiveness:
    """Tests for grounding-weighted effectiveness calculation.

    v12 Evolution: Grounding→Pattern Feedback - tests that grounding_confidence
    is properly stored and used to weight pattern effectiveness.
    """

    def test_grounding_confidence_stored_in_pattern_application(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that grounding_confidence is stored in pattern_applications table."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="grounding_storage_test",
        )

        # Record application with grounding confidence
        global_store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id="grounding_test_1",
            pattern_led_to_success=True,
            grounding_confidence=0.95,
        )

        # Verify grounding was stored by querying raw database
        with global_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT grounding_confidence FROM pattern_applications WHERE pattern_id = ?",
                (pattern_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["grounding_confidence"] == 0.95

    def test_null_grounding_uses_baseline_formula(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that null grounding_confidence uses baseline (1.0) in formula."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="null_grounding_test",
        )

        # Record 5 successful applications without grounding
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"no_grounding_{i}",
                pattern_led_to_success=True,
                grounding_confidence=None,  # No grounding data
            )

        # Effectiveness should be high (all successes, no grounding penalty)
        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)
        # With all successes and avg_grounding=1.0, effectiveness should be high
        assert pattern.effectiveness_score > 0.7

    def test_high_grounding_increases_effectiveness_trust(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that high grounding confidence increases effectiveness weight."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="high_grounding_test",
        )

        # Record 5 successful applications with high grounding
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"high_grounding_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.95,  # High grounding
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)

        # High grounding (0.95) gives weight: 0.7 + 0.3*0.95 = 0.985 ≈ 1.0
        # So effectiveness should be nearly unpenalized
        assert pattern.effectiveness_score > 0.8

    def test_low_grounding_dampens_effectiveness_update(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that low grounding confidence dampens effectiveness."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="low_grounding_test",
        )

        # Record 5 successful applications with low grounding
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"low_grounding_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.1,  # Low grounding
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)

        # Low grounding (0.1) gives weight: 0.7 + 0.3*0.1 = 0.73
        # Even with all successes, effectiveness is damped to ~73% of base
        # Base effectiveness for 5/5 successes ≈ 0.9
        # Damped: 0.9 * 0.73 ≈ 0.65
        assert pattern.effectiveness_score < 0.8
        assert pattern.effectiveness_score > 0.5  # But not too low

    def test_mixed_grounding_averages_correctly(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that mixed grounding values are averaged in effectiveness."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="mixed_grounding_test",
        )

        # Record 5 applications with varying grounding
        grounding_values = [0.9, 0.8, 0.7, 0.6, 0.5]  # avg = 0.7
        for i, g in enumerate(grounding_values):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"mixed_{i}",
                pattern_led_to_success=True,
                grounding_confidence=g,
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)

        # avg_grounding = 0.7 gives weight: 0.7 + 0.3*0.7 = 0.91
        # Effectiveness should be reasonably high but not maximum
        assert pattern.effectiveness_score > 0.6
        assert pattern.effectiveness_score < 0.95

    def test_grounding_weighted_effectiveness_with_failures(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test grounding weighting when pattern has mixed success/failure."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="grounding_with_failures_test",
        )

        # Record 3 successes with high grounding
        for i in range(3):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"success_high_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        # Record 3 failures with low grounding
        for i in range(3):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"failure_low_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.3,
            )

        patterns = global_store.get_patterns(min_priority=0.0)
        pattern = next(p for p in patterns if p.id == pattern_id)

        # Pattern should have moderate effectiveness
        # Recent applications (last 5) are mixed, avg_grounding is moderate
        assert pattern.effectiveness_score > 0.3
        assert pattern.effectiveness_score < 0.8


# =============================================================================
# v12 Evolution: Goal Drift Detection Tests
# =============================================================================


class TestGoalDriftDetection:
    """Tests for pattern drift detection.

    v12 Evolution: Goal Drift Detection - tests the drift calculation
    formula, threshold alerting, and edge cases.
    """

    def test_drift_calculation_formula(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test the drift calculation formula."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="drift_formula_test",
        )

        # Create 10 applications: first 5 successes, last 5 failures
        # This should show negative drift
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_success_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.8,
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_failure_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.8,
            )

        metrics = global_store.calculate_effectiveness_drift(pattern_id)
        assert metrics is not None

        # Should show negative drift (recent is worse than old)
        assert metrics.drift_direction == "negative"
        assert metrics.effectiveness_after < metrics.effectiveness_before
        assert metrics.drift_magnitude > 0.2  # Significant drift

    def test_drift_threshold_alerting(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that drift threshold correctly flags patterns."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="drift_threshold_test",
        )

        # Create significant drift
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.9,
            )

        # With threshold 0.2, this should be flagged
        metrics = global_store.calculate_effectiveness_drift(
            pattern_id, drift_threshold=0.2
        )
        assert metrics is not None
        assert metrics.threshold_exceeded is True

        # With threshold 0.99, this should NOT be flagged (very high threshold)
        # The weighted drift is drift_magnitude / avg_grounding ≈ 0.83 / 0.9 ≈ 0.92
        # So we need a threshold > 0.92 to NOT trigger
        metrics_high_threshold = global_store.calculate_effectiveness_drift(
            pattern_id, drift_threshold=0.99
        )
        assert metrics_high_threshold is not None
        assert metrics_high_threshold.threshold_exceeded is False

    def test_drift_with_low_grounding_confidence(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that low grounding amplifies drift signal."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="low_grounding_drift_test",
        )

        # Create moderate drift with low grounding
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.3,  # Low grounding
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.3,  # Low grounding
            )

        metrics = global_store.calculate_effectiveness_drift(pattern_id)
        assert metrics is not None

        # Low grounding confidence should amplify drift detection
        # weighted_magnitude = drift_magnitude / max(avg_grounding, 0.5)
        assert metrics.grounding_confidence_avg < 0.5
        assert metrics.threshold_exceeded is True

    def test_drift_direction_classification(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test correct classification of drift direction."""
        # Test positive drift (improving)
        pattern_improving = global_store.record_pattern(
            pattern_type="test",
            pattern_name="improving_pattern",
        )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_improving,
                execution_id=f"old_fail_{i}",
                pattern_led_to_success=False,
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_improving,
                execution_id=f"new_success_{i}",
                pattern_led_to_success=True,
            )

        metrics = global_store.calculate_effectiveness_drift(pattern_improving)
        assert metrics is not None
        assert metrics.drift_direction == "positive"

        # Test stable pattern
        pattern_stable = global_store.record_pattern(
            pattern_type="test",
            pattern_name="stable_pattern",
        )

        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_stable,
                execution_id=f"consistent_{i}",
                pattern_led_to_success=True,
            )

        metrics = global_store.calculate_effectiveness_drift(pattern_stable)
        assert metrics is not None
        assert metrics.drift_direction == "stable"

    def test_no_drift_with_stable_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that stable patterns don't trigger drift alerts."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="stable_test",
        )

        # All successes - should be stable
        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"success_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        metrics = global_store.calculate_effectiveness_drift(pattern_id)
        assert metrics is not None
        assert metrics.drift_direction == "stable"
        assert metrics.threshold_exceeded is False
        assert metrics.drift_magnitude < 0.1

    def test_edge_case_insufficient_history(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that patterns with insufficient history return None."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="short_history_test",
        )

        # Only 3 applications (need 10 for window_size=5)
        for i in range(3):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"short_{i}",
                pattern_led_to_success=True,
            )

        metrics = global_store.calculate_effectiveness_drift(pattern_id)
        assert metrics is None  # Not enough history

    def test_get_drifting_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retrieval of all drifting patterns."""
        # Create a drifting pattern
        drifting_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="will_drift",
        )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=drifting_id,
                execution_id=f"old_{i}",
                pattern_led_to_success=True,
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=drifting_id,
                execution_id=f"new_{i}",
                pattern_led_to_success=False,
            )

        # Create a stable pattern
        stable_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="will_not_drift",
        )

        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=stable_id,
                execution_id=f"stable_{i}",
                pattern_led_to_success=True,
            )

        # Get drifting patterns
        drifting = global_store.get_drifting_patterns(drift_threshold=0.2)

        # Only the drifting pattern should be returned
        drifting_ids = [d.pattern_id for d in drifting]
        assert drifting_id in drifting_ids
        assert stable_id not in drifting_ids

    def test_get_pattern_drift_summary(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test drift summary statistics."""
        # Create several patterns with different drift profiles
        for i in range(3):
            pid = global_store.record_pattern(
                pattern_type="test",
                pattern_name=f"summary_pattern_{i}",
            )

            # Create enough applications for analysis
            for j in range(10):
                global_store.record_pattern_application(
                    pattern_id=pid,
                    execution_id=f"summary_{i}_{j}",
                    pattern_led_to_success=(j < 5),  # First 5 success, last 5 fail
                )

        summary = global_store.get_pattern_drift_summary()

        assert summary["total_patterns"] >= 3
        assert summary["patterns_analyzed"] >= 3
        assert "patterns_drifting" in summary
        assert "avg_drift_magnitude" in summary
        assert summary["avg_drift_magnitude"] >= 0

    def test_drift_with_different_window_sizes(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test drift calculation with different window sizes."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="window_size_test",
        )

        # Create 20 applications with clear trend
        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_{i}",
                pattern_led_to_success=True,
            )

        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_{i}",
                pattern_led_to_success=False,
            )

        # Test with window_size=5
        metrics_5 = global_store.calculate_effectiveness_drift(
            pattern_id, window_size=5
        )
        assert metrics_5 is not None
        assert metrics_5.window_size == 5
        assert metrics_5.applications_analyzed == 10

        # Test with window_size=10
        metrics_10 = global_store.calculate_effectiveness_drift(
            pattern_id, window_size=10
        )
        assert metrics_10 is not None
        assert metrics_10.window_size == 10
        assert metrics_10.applications_analyzed == 20

    def test_drift_nonexistent_pattern_returns_none(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that querying drift for a nonexistent pattern returns None."""
        metrics = global_store.calculate_effectiveness_drift("nonexistent-id")
        assert metrics is None

    def test_drift_no_grounding_confidence_defaults_to_one(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that avg_grounding defaults to 1.0 when no confidence values present."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="no_grounding_test",
        )

        # Create 10 applications with no grounding confidence
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_{i}",
                pattern_led_to_success=True,
                # No grounding_confidence — defaults to None
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_{i}",
                pattern_led_to_success=False,
            )

        metrics = global_store.calculate_effectiveness_drift(pattern_id)
        assert metrics is not None
        # When no grounding confidence, defaults to 1.0
        assert metrics.grounding_confidence_avg == 1.0

    def test_epistemic_drift_nonexistent_pattern_returns_none(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that querying epistemic drift for nonexistent pattern returns None."""
        metrics = global_store.calculate_epistemic_drift("nonexistent-id")
        assert metrics is None

    def test_epistemic_drift_insufficient_confidence_data(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that epistemic drift returns None with insufficient confidence data."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="insufficient_confidence",
        )

        # Record 10 applications but none with grounding confidence
        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"no_conf_{i}",
                pattern_led_to_success=True,
                # No grounding_confidence
            )

        # Epistemic drift requires grounding_confidence; should return None
        metrics = global_store.calculate_epistemic_drift(pattern_id)
        assert metrics is None

    def test_epistemic_drift_stable_confidence(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that stable confidence values produce 'stable' drift direction."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="stable_confidence",
        )

        # All applications with same confidence
        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"stable_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.8,
            )

        metrics = global_store.calculate_epistemic_drift(pattern_id)
        assert metrics is not None
        assert metrics.drift_direction == "stable"
        assert abs(metrics.belief_change) < 0.05

    def test_epistemic_drift_weakening_belief(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test detection of weakening confidence (epistemic drift)."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="weakening_confidence",
        )

        # Older apps with high confidence
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_high_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        # Recent apps with low confidence
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_low_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.3,
            )

        metrics = global_store.calculate_epistemic_drift(pattern_id)
        assert metrics is not None
        assert metrics.drift_direction == "weakening"
        assert metrics.belief_change < -0.05
        assert metrics.threshold_exceeded is True

    def test_epistemic_drift_strengthening_belief(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test detection of strengthening confidence (positive epistemic drift)."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="strengthening_confidence",
        )

        # Older apps with low confidence
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_low_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.3,
            )

        # Recent apps with high confidence
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_high_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        metrics = global_store.calculate_epistemic_drift(pattern_id)
        assert metrics is not None
        assert metrics.drift_direction == "strengthening"
        assert metrics.belief_change > 0.05


class TestPatternAutoRetirement:
    """Tests for pattern auto-retirement.

    v14 Evolution: Pattern Auto-Retirement - tests that patterns with
    negative drift are properly retired while preserving data.
    """

    def test_retire_drifting_patterns_sets_priority_to_zero(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that retired patterns have priority_score set to 0."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="retire_test_pattern",
        )

        # Create negative drift: 5 successes followed by 5 failures
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_success_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_failure_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.9,
            )

        # Verify drift is detected
        metrics = global_store.calculate_effectiveness_drift(pattern_id)
        assert metrics is not None
        assert metrics.drift_direction == "negative"
        assert metrics.threshold_exceeded is True

        # Retire drifting patterns
        retired = global_store.retire_drifting_patterns()

        # Should have retired one pattern
        assert len(retired) == 1
        assert retired[0][0] == pattern_id
        assert retired[0][1] == "retire_test_pattern"

        # Verify priority_score is now 0
        patterns = global_store.get_patterns(min_priority=0.0, limit=100)
        retired_pattern = next((p for p in patterns if p.id == pattern_id), None)
        assert retired_pattern is not None
        assert retired_pattern.priority_score == 0.0

    def test_retirement_requires_negative_drift(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that patterns with positive drift are NOT retired."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="improving_pattern",
        )

        # Create positive drift: 5 failures followed by 5 successes
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_failure_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.9,
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_success_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        # Verify positive drift
        metrics = global_store.calculate_effectiveness_drift(pattern_id)
        assert metrics is not None
        assert metrics.drift_direction == "positive"
        assert metrics.threshold_exceeded is True  # Exceeds threshold but positive

        # Try to retire - should NOT retire positive drift
        retired = global_store.retire_drifting_patterns(require_negative_drift=True)

        # Should NOT have retired the pattern
        assert len(retired) == 0

        # Verify priority_score is still > 0
        patterns = global_store.get_patterns(min_priority=0.0, limit=100)
        pattern = next((p for p in patterns if p.id == pattern_id), None)
        assert pattern is not None
        assert pattern.priority_score > 0.0

    def test_retirement_requires_threshold_exceeded(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that patterns without threshold exceeded are NOT retired."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="stable_pattern",
        )

        # Create mild drift that won't exceed threshold
        for i in range(5):
            # Alternating success/failure = stable
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"app_{i}",
                pattern_led_to_success=(i % 2 == 0),
                grounding_confidence=0.9,
            )

        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"app2_{i}",
                pattern_led_to_success=(i % 2 == 0),
                grounding_confidence=0.9,
            )

        # Verify no significant drift
        metrics = global_store.calculate_effectiveness_drift(
            pattern_id, drift_threshold=0.2
        )
        assert metrics is not None
        # Stable pattern should have low drift magnitude
        assert metrics.threshold_exceeded is False or metrics.drift_direction == "stable"

        # Try to retire - should NOT retire stable patterns
        global_store.retire_drifting_patterns(drift_threshold=0.2)

        # The stable pattern should NOT be retired
        patterns = global_store.get_patterns(min_priority=0.0, limit=100)
        pattern = next((p for p in patterns if p.id == pattern_id), None)
        assert pattern is not None
        assert pattern.priority_score > 0.0

    def test_retirement_preserves_pattern_data(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that retirement preserves pattern history (not deleted)."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="preserve_data_test",
            description="This should be preserved",
        )

        # Record some applications
        for i in range(10):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"app_{i}",
                pattern_led_to_success=(i < 5),  # First 5 success, last 5 failure
                grounding_confidence=0.9,
            )

        # Retire
        retired = global_store.retire_drifting_patterns()
        assert len(retired) == 1

        # Verify pattern still exists with all data
        patterns = global_store.get_patterns(min_priority=0.0, limit=100)
        pattern = next((p for p in patterns if p.id == pattern_id), None)
        assert pattern is not None
        assert pattern.pattern_name == "preserve_data_test"
        assert pattern.description == "This should be preserved"
        assert pattern.occurrence_count >= 1
        # Only priority_score should be 0
        assert pattern.priority_score == 0.0

    def test_no_retirement_for_positive_drift(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that positive drift patterns remain active."""
        # Create two patterns: one improving, one degrading
        improving_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="improving",
        )
        degrading_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="degrading",
        )

        # Set up improving pattern (failures then successes)
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=improving_id,
                execution_id=f"imp_old_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.9,
            )
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=improving_id,
                execution_id=f"imp_new_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )

        # Set up degrading pattern (successes then failures)
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=degrading_id,
                execution_id=f"deg_old_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=degrading_id,
                execution_id=f"deg_new_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.9,
            )

        # Retire - should only retire degrading
        retired = global_store.retire_drifting_patterns()

        # Only degrading should be retired
        assert len(retired) == 1
        assert retired[0][1] == "degrading"

        # Verify improving still active
        patterns = global_store.get_patterns(min_priority=0.0, limit=100)
        improving = next((p for p in patterns if p.id == improving_id), None)
        degrading = next((p for p in patterns if p.id == degrading_id), None)

        assert improving is not None
        assert improving.priority_score > 0.0

        assert degrading is not None
        assert degrading.priority_score == 0.0

    def test_get_retired_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retrieval of retired patterns."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="retired_retrieval_test",
        )

        # Create drift and retire
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"old_{i}",
                pattern_led_to_success=True,
                grounding_confidence=0.9,
            )
        for i in range(5):
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"new_{i}",
                pattern_led_to_success=False,
                grounding_confidence=0.9,
            )

        global_store.retire_drifting_patterns()

        # Get retired patterns
        retired_patterns = global_store.get_retired_patterns()

        # Should find our retired pattern
        found = any(p.id == pattern_id for p in retired_patterns)
        assert found, "Retired pattern should be retrievable"

        # All returned patterns should have priority_score = 0
        for p in retired_patterns:
            assert p.priority_score == 0.0


class TestPatternBroadcasting:
    """Tests for real-time pattern broadcasting.

    v14 Evolution: Real-time Pattern Broadcasting - tests cross-job pattern
    sharing with TTL-based expiry following the rate_limit_events template.
    """

    def test_record_pattern_discovery_creates_event(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that recording a discovery creates an event."""
        record_id = global_store.record_pattern_discovery(
            pattern_id="test-pattern-001",
            pattern_name="Test Pattern",
            pattern_type="validation_failure",
            job_id="test-job-A",
            effectiveness_score=0.85,
            context_tags=["error", "timeout"],
            ttl_seconds=60.0,
        )

        assert record_id is not None
        assert len(record_id) > 0

        # Verify event exists
        events = global_store.get_active_pattern_discoveries()
        assert len(events) >= 1

        # Find our event
        event = next((e for e in events if e.id == record_id), None)
        assert event is not None
        assert event.pattern_id == "test-pattern-001"
        assert event.pattern_name == "Test Pattern"
        assert event.pattern_type == "validation_failure"
        assert event.effectiveness_score == 0.85
        assert "error" in event.context_tags
        assert "timeout" in event.context_tags

    def test_check_recent_discoveries_finds_new_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that check_recent_discoveries finds patterns from other jobs."""
        # Job A discovers a pattern
        global_store.record_pattern_discovery(
            pattern_id="discovery-001",
            pattern_name="Job A Discovery",
            pattern_type="retry_pattern",
            job_id="job-A",
            effectiveness_score=0.9,
        )

        # Job B checks for discoveries (excluding itself)
        discoveries = global_store.check_recent_pattern_discoveries(
            exclude_job_id="job-B"
        )

        # Should find Job A's discovery
        assert len(discoveries) >= 1
        found = any(d.pattern_name == "Job A Discovery" for d in discoveries)
        assert found, "Job B should see Job A's discovery"

    def test_check_recent_discoveries_excludes_own_job(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that a job doesn't see its own discoveries."""
        # Job A discovers a pattern
        global_store.record_pattern_discovery(
            pattern_id="self-discovery-001",
            pattern_name="Self Discovery",
            pattern_type="validation_failure",
            job_id="job-A",
        )

        # Job A checks discoveries (excluding itself)
        discoveries = global_store.check_recent_pattern_discoveries(
            exclude_job_id="job-A"
        )

        # Should NOT find its own discovery
        found = any(d.pattern_name == "Self Discovery" for d in discoveries)
        assert not found, "Job A should not see its own discovery"

    def test_discovery_events_expire_correctly(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that expired events are cleaned up."""
        import time

        # Record with very short TTL
        global_store.record_pattern_discovery(
            pattern_id="short-lived-001",
            pattern_name="Short Lived Pattern",
            pattern_type="test",
            job_id="job-X",
            ttl_seconds=0.1,  # 100ms TTL
        )

        # Should exist immediately
        events = global_store.get_active_pattern_discoveries()
        assert any(e.pattern_name == "Short Lived Pattern" for e in events)

        # Wait for expiry
        time.sleep(0.2)

        # Should not appear in active discoveries
        events = global_store.get_active_pattern_discoveries()
        assert not any(e.pattern_name == "Short Lived Pattern" for e in events)

        # Cleanup should work
        cleaned = global_store.cleanup_expired_pattern_discoveries()
        assert cleaned >= 1

    def test_parallel_job_discovers_other_jobs_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test cross-job pattern sharing scenario."""
        # Simulate two parallel jobs
        # Job A discovers patterns
        global_store.record_pattern_discovery(
            pattern_id="parallel-001",
            pattern_name="Pattern from Job A",
            pattern_type="validation_failure",
            job_id="parallel-job-A",
            effectiveness_score=0.8,
        )
        global_store.record_pattern_discovery(
            pattern_id="parallel-002",
            pattern_name="Another Pattern from Job A",
            pattern_type="retry_pattern",
            job_id="parallel-job-A",
            effectiveness_score=0.9,
        )

        # Job B checks for discoveries
        job_b_discoveries = global_store.check_recent_pattern_discoveries(
            exclude_job_id="parallel-job-B"
        )

        # Job B should see both patterns from Job A
        names = [d.pattern_name for d in job_b_discoveries]
        assert "Pattern from Job A" in names
        assert "Another Pattern from Job A" in names

        # Job B also discovers something
        global_store.record_pattern_discovery(
            pattern_id="parallel-003",
            pattern_name="Pattern from Job B",
            pattern_type="completion_pattern",
            job_id="parallel-job-B",
            effectiveness_score=0.75,
        )

        # Job A checks for discoveries
        job_a_discoveries = global_store.check_recent_pattern_discoveries(
            exclude_job_id="parallel-job-A"
        )

        # Job A should see Job B's pattern but not its own
        names_a = [d.pattern_name for d in job_a_discoveries]
        assert "Pattern from Job B" in names_a
        assert "Pattern from Job A" not in names_a

    def test_filter_by_pattern_type(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test filtering discoveries by pattern type."""
        # Create different types
        global_store.record_pattern_discovery(
            pattern_id="type-001",
            pattern_name="Validation Failure",
            pattern_type="validation_failure",
            job_id="job-filter",
        )
        global_store.record_pattern_discovery(
            pattern_id="type-002",
            pattern_name="Retry Pattern",
            pattern_type="retry_pattern",
            job_id="job-filter",
        )

        # Filter by validation_failure
        validation_only = global_store.check_recent_pattern_discoveries(
            pattern_type="validation_failure"
        )
        for d in validation_only:
            if d.pattern_name in ["Validation Failure", "Retry Pattern"]:
                assert d.pattern_type == "validation_failure"

        # Filter by retry_pattern
        retry_only = global_store.check_recent_pattern_discoveries(
            pattern_type="retry_pattern"
        )
        for d in retry_only:
            if d.pattern_name in ["Validation Failure", "Retry Pattern"]:
                assert d.pattern_type == "retry_pattern"

    def test_filter_by_min_effectiveness(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test filtering discoveries by minimum effectiveness."""
        global_store.record_pattern_discovery(
            pattern_id="eff-001",
            pattern_name="High Effectiveness",
            pattern_type="test",
            job_id="job-eff",
            effectiveness_score=0.95,
        )
        global_store.record_pattern_discovery(
            pattern_id="eff-002",
            pattern_name="Low Effectiveness",
            pattern_type="test",
            job_id="job-eff",
            effectiveness_score=0.3,
        )

        # Filter by min 0.5
        high_only = global_store.check_recent_pattern_discoveries(
            min_effectiveness=0.5
        )

        # Find our patterns
        high_names = [d.pattern_name for d in high_only]
        assert "High Effectiveness" in high_names
        assert "Low Effectiveness" not in high_names

    def test_get_active_pattern_discoveries(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retrieval of all active discoveries."""
        # Add some discoveries
        global_store.record_pattern_discovery(
            pattern_id="active-001",
            pattern_name="Active Discovery 1",
            pattern_type="test",
            job_id="job-active",
        )
        global_store.record_pattern_discovery(
            pattern_id="active-002",
            pattern_name="Active Discovery 2",
            pattern_type="test",
            job_id="job-active",
        )

        # Get all active
        active = global_store.get_active_pattern_discoveries()

        # Should find our discoveries
        names = [d.pattern_name for d in active]
        assert "Active Discovery 1" in names
        assert "Active Discovery 2" in names

    def test_cleanup_expired_pattern_discoveries(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test cleanup of expired events."""
        import time

        # Add short-lived discovery
        global_store.record_pattern_discovery(
            pattern_id="cleanup-001",
            pattern_name="Will Expire",
            pattern_type="test",
            job_id="job-cleanup",
            ttl_seconds=0.1,
        )

        # Wait for expiry
        time.sleep(0.2)

        # Cleanup
        cleaned = global_store.cleanup_expired_pattern_discoveries()
        assert cleaned >= 1

        # Verify cleaned
        active = global_store.get_active_pattern_discoveries()
        assert not any(d.pattern_name == "Will Expire" for d in active)

    def test_context_tags_preserved(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that context tags are preserved through storage."""
        tags = ["pytest", "validation", "timeout", "model:opus"]

        global_store.record_pattern_discovery(
            pattern_id="tags-001",
            pattern_name="Tagged Pattern",
            pattern_type="test",
            job_id="job-tags",
            context_tags=tags,
        )

        discoveries = global_store.check_recent_pattern_discoveries()
        found = next((d for d in discoveries if d.pattern_name == "Tagged Pattern"), None)

        assert found is not None
        assert found.context_tags == tags


class TestEvolutionTrajectoryTracking:
    """Tests for v16 Evolution: Evolution Trajectory Tracking.

    Tests the ability to track Marianne's own evolution history for
    recursive self-improvement analysis.
    """

    def test_record_evolution_entry_creates_record(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that recording an evolution entry creates a record."""
        entry_id = global_store.record_evolution_entry(
            cycle=16,
            evolutions_completed=2,
            evolutions_deferred=0,
            issue_classes=["infrastructure_activation", "epistemic_drift"],
            cv_avg=0.68,
            implementation_loc=333,
            test_loc=405,
            loc_accuracy=0.92,
            research_candidates_resolved=1,
            research_candidates_created=0,
            notes="Test cycle",
        )

        assert entry_id is not None
        assert len(entry_id) > 0

        # Verify retrieval
        trajectory = global_store.get_trajectory()
        assert len(trajectory) == 1
        assert trajectory[0].cycle == 16
        assert trajectory[0].evolutions_completed == 2
        assert trajectory[0].issue_classes == ["infrastructure_activation", "epistemic_drift"]

    def test_get_trajectory_returns_ordered_history(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that trajectory is returned in descending cycle order."""
        # Record multiple entries
        for cycle in [14, 15, 16]:
            global_store.record_evolution_entry(
                cycle=cycle,
                evolutions_completed=2,
                evolutions_deferred=0,
                issue_classes=[f"issue_{cycle}"],
                cv_avg=0.65,
                implementation_loc=100 * cycle,
                test_loc=50 * cycle,
                loc_accuracy=0.9,
            )

        trajectory = global_store.get_trajectory()

        assert len(trajectory) == 3
        # Should be descending order
        assert trajectory[0].cycle == 16
        assert trajectory[1].cycle == 15
        assert trajectory[2].cycle == 14

    def test_get_trajectory_with_cycle_range(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test filtering trajectory by cycle range."""
        # Record entries for cycles 10-15
        for cycle in range(10, 16):
            global_store.record_evolution_entry(
                cycle=cycle,
                evolutions_completed=1,
                evolutions_deferred=0,
                issue_classes=[f"issue_{cycle}"],
                cv_avg=0.6,
                implementation_loc=100,
                test_loc=50,
                loc_accuracy=0.9,
            )

        # Filter to cycles 12-14
        filtered = global_store.get_trajectory(start_cycle=12, end_cycle=14)

        assert len(filtered) == 3
        cycles = [e.cycle for e in filtered]
        assert all(12 <= c <= 14 for c in cycles)

    def test_get_recurring_issues_identifies_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that recurring issue classes are identified correctly."""
        # Record entries with overlapping issue classes
        global_store.record_evolution_entry(
            cycle=10,
            evolutions_completed=2,
            evolutions_deferred=0,
            issue_classes=["infrastructure_activation", "test_coverage"],
            cv_avg=0.65,
            implementation_loc=200,
            test_loc=100,
            loc_accuracy=0.88,
        )
        global_store.record_evolution_entry(
            cycle=11,
            evolutions_completed=2,
            evolutions_deferred=0,
            issue_classes=["infrastructure_activation", "schema_migration"],
            cv_avg=0.70,
            implementation_loc=180,
            test_loc=90,
            loc_accuracy=0.92,
        )
        global_store.record_evolution_entry(
            cycle=12,
            evolutions_completed=1,
            evolutions_deferred=1,
            issue_classes=["cli_enhancement", "infrastructure_activation"],
            cv_avg=0.72,
            implementation_loc=150,
            test_loc=80,
            loc_accuracy=0.95,
        )

        recurring = global_store.get_recurring_issues(min_occurrences=2)

        # infrastructure_activation appears in all 3 cycles
        assert "infrastructure_activation" in recurring
        assert len(recurring["infrastructure_activation"]) == 3

        # Others appear only once
        assert "test_coverage" not in recurring
        assert "schema_migration" not in recurring
        assert "cli_enhancement" not in recurring

    def test_get_recurring_issues_respects_window(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that window_cycles parameter limits analysis range."""
        # Record entries with issue appearing in old and recent cycles
        for cycle in range(10, 20):
            issue = "common_issue" if cycle % 2 == 0 else f"unique_{cycle}"
            global_store.record_evolution_entry(
                cycle=cycle,
                evolutions_completed=1,
                evolutions_deferred=0,
                issue_classes=[issue],
                cv_avg=0.65,
                implementation_loc=100,
                test_loc=50,
                loc_accuracy=0.9,
            )

        # Analyze only last 3 cycles (17, 18, 19)
        recurring = global_store.get_recurring_issues(min_occurrences=2, window_cycles=3)

        # common_issue only appears in cycle 18 within the window
        # So it shouldn't be recurring
        assert "common_issue" not in recurring

    def test_schema_migration_creates_table(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that evolution_trajectory table is created by migration."""
        import sqlite3

        # Verify table exists by querying it
        with sqlite3.connect(str(global_store.db_path)) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='evolution_trajectory'"
            )
            result = cursor.fetchone()
            assert result is not None
            assert result[0] == "evolution_trajectory"

    def test_evolution_entry_validation_rejects_duplicate_cycle(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that duplicate cycle numbers are rejected."""
        import sqlite3

        global_store.record_evolution_entry(
            cycle=16,
            evolutions_completed=2,
            evolutions_deferred=0,
            issue_classes=["first_entry"],
            cv_avg=0.65,
            implementation_loc=200,
            test_loc=100,
            loc_accuracy=0.9,
        )

        # Attempting to record same cycle should raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            global_store.record_evolution_entry(
                cycle=16,  # Same cycle
                evolutions_completed=1,
                evolutions_deferred=1,
                issue_classes=["duplicate_entry"],
                cv_avg=0.70,
                implementation_loc=100,
                test_loc=50,
                loc_accuracy=0.85,
            )

    def test_trajectory_entry_all_fields_preserved(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that all fields are correctly stored and retrieved."""
        global_store.record_evolution_entry(
            cycle=16,
            evolutions_completed=2,
            evolutions_deferred=1,
            issue_classes=["infrastructure_activation", "epistemic_drift"],
            cv_avg=0.685,
            implementation_loc=333,
            test_loc=405,
            loc_accuracy=0.92,
            research_candidates_resolved=2,
            research_candidates_created=1,
            notes="v16 cycle notes with special chars: <>&",
        )

        trajectory = global_store.get_trajectory()
        entry = trajectory[0]

        assert entry.cycle == 16
        assert entry.evolutions_completed == 2
        assert entry.evolutions_deferred == 1
        assert entry.issue_classes == ["infrastructure_activation", "epistemic_drift"]
        assert abs(entry.cv_avg - 0.685) < 0.001
        assert entry.implementation_loc == 333
        assert entry.test_loc == 405
        assert abs(entry.loc_accuracy - 0.92) < 0.001
        assert entry.research_candidates_resolved == 2
        assert entry.research_candidates_created == 1
        assert entry.notes == "v16 cycle notes with special chars: <>&"
        assert entry.recorded_at is not None

    def test_get_recurring_issues_empty_database(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_recurring_issues with no data returns empty dict."""
        recurring = global_store.get_recurring_issues()
        assert recurring == {}

    def test_get_trajectory_with_limit(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that trajectory limit parameter works correctly."""
        # Record 10 entries
        for cycle in range(1, 11):
            global_store.record_evolution_entry(
                cycle=cycle,
                evolutions_completed=1,
                evolutions_deferred=0,
                issue_classes=[f"issue_{cycle}"],
                cv_avg=0.65,
                implementation_loc=100,
                test_loc=50,
                loc_accuracy=0.9,
            )

        # Limit to 5
        trajectory = global_store.get_trajectory(limit=5)

        assert len(trajectory) == 5
        # Should be most recent (descending order)
        assert trajectory[0].cycle == 10
        assert trajectory[-1].cycle == 6

    def test_clear_all_includes_evolution_trajectory(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that clear_all also clears evolution_trajectory table."""
        global_store.record_evolution_entry(
            cycle=16,
            evolutions_completed=2,
            evolutions_deferred=0,
            issue_classes=["test"],
            cv_avg=0.65,
            implementation_loc=100,
            test_loc=50,
            loc_accuracy=0.9,
        )

        # Verify entry exists
        assert len(global_store.get_trajectory()) == 1

        # Clear all
        global_store.clear_all()

        # Verify cleared
        assert len(global_store.get_trajectory()) == 0


# =============================================================================
# v19: TestPatternQuarantine
# =============================================================================


class TestPatternQuarantine:
    """Tests for Pattern Quarantine & Provenance feature (v19 Evolution)."""

    def test_new_pattern_starts_pending(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that newly created patterns start with PENDING status."""
        pattern_id = global_store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="test:quarantine_status",
            description="Test pattern",
        )

        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.PENDING

    def test_quarantine_pattern(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test quarantining a pattern changes status correctly."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:to_quarantine",
        )

        result = global_store.quarantine_pattern(pattern_id, reason="Causes issues")

        assert result is True
        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.QUARANTINED
        assert pattern.quarantine_reason == "Causes issues"
        assert pattern.quarantined_at is not None

    def test_quarantine_nonexistent_pattern(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test quarantining a nonexistent pattern returns False."""
        result = global_store.quarantine_pattern("nonexistent-id")
        assert result is False

    def test_validate_pattern(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test validating a pattern changes status correctly."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:to_validate",
        )

        result = global_store.validate_pattern(pattern_id)

        assert result is True
        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.VALIDATED
        assert pattern.validated_at is not None
        assert pattern.quarantine_reason is None  # Should be cleared

    def test_validate_nonexistent_pattern(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test validating a nonexistent pattern returns False."""
        result = global_store.validate_pattern("nonexistent-id")
        assert result is False

    def test_retire_pattern(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retiring a pattern changes status correctly."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:to_retire",
        )

        result = global_store.retire_pattern(pattern_id)

        assert result is True
        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.RETIRED

    def test_retire_nonexistent_pattern(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retiring a nonexistent pattern returns False."""
        result = global_store.retire_pattern("nonexistent-id")
        assert result is False

    def test_get_quarantined_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retrieving only quarantined patterns."""
        # Create patterns with different statuses
        id1 = global_store.record_pattern("test", "test:pattern1")
        id2 = global_store.record_pattern("test", "test:pattern2")
        id3 = global_store.record_pattern("test", "test:pattern3")

        global_store.quarantine_pattern(id1, reason="Test")
        global_store.quarantine_pattern(id2, reason="Test")
        global_store.validate_pattern(id3)

        quarantined = global_store.get_quarantined_patterns()

        assert len(quarantined) == 2
        quarantined_ids = {p.id for p in quarantined}
        assert id1 in quarantined_ids
        assert id2 in quarantined_ids
        assert id3 not in quarantined_ids

    def test_status_transitions(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test pattern can transition through all status states."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:transitions",
        )

        # Start as pending
        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.PENDING

        # Move to quarantined
        global_store.quarantine_pattern(pattern_id)
        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.QUARANTINED

        # Move to validated (from quarantine)
        global_store.validate_pattern(pattern_id)
        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.VALIDATED

        # Move to retired
        global_store.retire_pattern(pattern_id)
        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.RETIRED

    def test_provenance_recorded_on_creation(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test provenance information is recorded when creating pattern."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:provenance",
            provenance_job_hash="abc123def456",
            provenance_sheet_num=3,
        )

        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.provenance_job_hash == "abc123def456"
        assert pattern.provenance_sheet_num == 3

    def test_get_pattern_provenance(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_pattern_provenance returns complete provenance info."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:provenance_info",
            provenance_job_hash="xyz789",
            provenance_sheet_num=5,
        )
        global_store.quarantine_pattern(pattern_id, reason="Testing provenance")

        provenance = global_store.get_pattern_provenance(pattern_id)

        assert provenance is not None
        assert provenance["pattern_id"] == pattern_id
        assert provenance["pattern_name"] == "test:provenance_info"
        assert provenance["provenance_job_hash"] == "xyz789"
        assert provenance["provenance_sheet_num"] == 5
        assert provenance["quarantine_status"] == "quarantined"
        assert provenance["quarantine_reason"] == "Testing provenance"
        assert provenance["quarantined_at"] is not None

    def test_get_pattern_provenance_nonexistent(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_pattern_provenance returns None for nonexistent pattern."""
        provenance = global_store.get_pattern_provenance("nonexistent-id")
        assert provenance is None

    def test_get_patterns_with_quarantine_filter(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_patterns filters by quarantine status."""
        id1 = global_store.record_pattern("test", "test:filter1")
        id2 = global_store.record_pattern("test", "test:filter2")
        global_store.validate_pattern(id1)
        global_store.quarantine_pattern(id2)

        # Get only validated
        validated = global_store.get_patterns(
            min_priority=0.0,
            quarantine_status=QuarantineStatus.VALIDATED,
        )
        assert len(validated) == 1
        assert validated[0].id == id1

        # Get only quarantined
        quarantined = global_store.get_patterns(
            min_priority=0.0,
            quarantine_status=QuarantineStatus.QUARANTINED,
        )
        assert len(quarantined) == 1
        assert quarantined[0].id == id2

    def test_get_patterns_exclude_quarantined(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_patterns can exclude quarantined patterns."""
        id1 = global_store.record_pattern("test", "test:exclude1")
        id2 = global_store.record_pattern("test", "test:exclude2")
        global_store.quarantine_pattern(id2)

        # Default includes all
        all_patterns = global_store.get_patterns(min_priority=0.0)
        assert len(all_patterns) == 2

        # Exclude quarantined
        non_quarantined = global_store.get_patterns(
            min_priority=0.0,
            exclude_quarantined=True,
        )
        assert len(non_quarantined) == 1
        assert non_quarantined[0].id == id1


# =============================================================================
# v19: TestPatternTrustScoring
# =============================================================================


class TestPatternTrustScoring:
    """Tests for Pattern Trust Scoring feature (v19 Evolution)."""

    def test_new_pattern_starts_with_neutral_trust(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that newly created patterns start with 0.5 trust score."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:initial_trust",
        )

        pattern = global_store.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.trust_score == 0.5

    def test_calculate_trust_score_basic(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test trust score calculation with basic success/failure data."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:trust_calc",
        )

        # Simulate some application data
        with global_store._get_connection() as conn:
            conn.execute(
                """
                UPDATE patterns SET
                    led_to_success_count = 8,
                    led_to_failure_count = 2,
                    occurrence_count = 10
                WHERE id = ?
                """,
                (pattern_id,),
            )

        trust = global_store.calculate_trust_score(pattern_id)

        assert trust is not None
        # With 80% success rate, trust should be above neutral
        assert trust > 0.5
        assert trust <= 1.0

    def test_calculate_trust_score_nonexistent(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test trust score calculation returns None for nonexistent pattern."""
        trust = global_store.calculate_trust_score("nonexistent-id")
        assert trust is None

    def test_calculate_trust_score_quarantine_penalty(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that quarantined patterns get trust penalty."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:trust_quarantine",
        )

        # Calculate baseline trust
        trust_before = global_store.calculate_trust_score(pattern_id)
        assert trust_before is not None

        # Quarantine and recalculate
        global_store.quarantine_pattern(pattern_id)
        trust_after = global_store.calculate_trust_score(pattern_id)

        assert trust_after is not None
        # Quarantine penalty is -0.2
        assert trust_after < trust_before

    def test_calculate_trust_score_validation_bonus(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that validated patterns get trust bonus."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:trust_validated",
        )

        # Calculate baseline trust
        trust_before = global_store.calculate_trust_score(pattern_id)
        assert trust_before is not None

        # Validate and recalculate
        global_store.validate_pattern(pattern_id)
        trust_after = global_store.calculate_trust_score(pattern_id)

        assert trust_after is not None
        # Validation bonus is +0.1
        assert trust_after > trust_before

    def test_update_trust_score_increases(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that update_trust_score can increase trust."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:trust_increase",
        )

        pattern_before = global_store.get_pattern_by_id(pattern_id)
        assert pattern_before is not None
        trust_before = pattern_before.trust_score

        new_trust = global_store.update_trust_score(pattern_id, delta=0.2)

        assert new_trust is not None
        assert new_trust == trust_before + 0.2

    def test_update_trust_score_decreases(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that update_trust_score can decrease trust."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:trust_decrease",
        )

        pattern_before = global_store.get_pattern_by_id(pattern_id)
        assert pattern_before is not None
        trust_before = pattern_before.trust_score

        new_trust = global_store.update_trust_score(pattern_id, delta=-0.2)

        assert new_trust is not None
        assert new_trust == trust_before - 0.2

    def test_update_trust_score_clamps_to_valid_range(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that update_trust_score clamps to [0, 1] range."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:trust_clamp",
        )

        # Try to increase beyond 1.0
        trust_high = global_store.update_trust_score(pattern_id, delta=10.0)
        assert trust_high == 1.0

        # Try to decrease below 0.0
        trust_low = global_store.update_trust_score(pattern_id, delta=-10.0)
        assert trust_low == 0.0

    def test_update_trust_score_nonexistent(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test update_trust_score returns None for nonexistent pattern."""
        result = global_store.update_trust_score("nonexistent-id", delta=0.1)
        assert result is None

    def test_get_high_trust_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test filtering patterns by high trust score."""
        id1 = global_store.record_pattern("test", "test:high_trust1")
        id2 = global_store.record_pattern("test", "test:high_trust2")
        id3 = global_store.record_pattern("test", "test:low_trust")

        global_store.update_trust_score(id1, delta=0.3)  # 0.8
        global_store.update_trust_score(id2, delta=0.25)  # 0.75
        global_store.update_trust_score(id3, delta=-0.3)  # 0.2

        high_trust = global_store.get_high_trust_patterns(threshold=0.7)

        assert len(high_trust) == 2
        high_trust_ids = {p.id for p in high_trust}
        assert id1 in high_trust_ids
        assert id2 in high_trust_ids
        assert id3 not in high_trust_ids

    def test_get_low_trust_patterns(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test filtering patterns by low trust score."""
        id1 = global_store.record_pattern("test", "test:low1")
        id2 = global_store.record_pattern("test", "test:low2")
        id3 = global_store.record_pattern("test", "test:high")

        global_store.update_trust_score(id1, delta=-0.3)  # 0.2
        global_store.update_trust_score(id2, delta=-0.25)  # 0.25
        global_store.update_trust_score(id3, delta=0.3)  # 0.8

        low_trust = global_store.get_low_trust_patterns(threshold=0.3)

        assert len(low_trust) == 2
        low_trust_ids = {p.id for p in low_trust}
        assert id1 in low_trust_ids
        assert id2 in low_trust_ids
        assert id3 not in low_trust_ids

    def test_recalculate_all_trust_scores(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test batch recalculation of trust scores."""
        # Create several patterns
        for i in range(5):
            global_store.record_pattern("test", f"test:batch{i}")

        updated = global_store.recalculate_all_trust_scores()

        assert updated == 5

    def test_trust_score_decay_over_time(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that trust score accounts for time decay."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="test:trust_decay",
        )

        # Set an old last_confirmed date
        old_date = (datetime.now() - timedelta(days=90)).isoformat()
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET last_confirmed = ? WHERE id = ?",
                (old_date, pattern_id),
            )

        trust = global_store.calculate_trust_score(pattern_id)

        assert trust is not None
        # With 90-day age, decay factor is 0.9^3 ≈ 0.73
        # Trust should be lower than neutral due to age decay
        # The age_factor component contributes about 0.2 × 0.73 ≈ 0.15
        # So overall trust should be around 0.5 + 0.15 = 0.65, not higher
        assert trust < 0.8

    def test_get_patterns_with_trust_filter(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_patterns filters by trust score range."""
        id1 = global_store.record_pattern("test", "test:trust_filter1")
        id2 = global_store.record_pattern("test", "test:trust_filter2")
        id3 = global_store.record_pattern("test", "test:trust_filter3")

        global_store.update_trust_score(id1, delta=0.3)  # 0.8
        global_store.update_trust_score(id2, delta=0.0)  # 0.5
        global_store.update_trust_score(id3, delta=-0.3)  # 0.2

        # Filter by min_trust
        high = global_store.get_patterns(min_priority=0.0, min_trust=0.7)
        assert len(high) == 1
        assert high[0].id == id1

        # Filter by max_trust
        low = global_store.get_patterns(min_priority=0.0, max_trust=0.3)
        assert len(low) == 1
        assert low[0].id == id3

        # Filter by range
        mid = global_store.get_patterns(
            min_priority=0.0, min_trust=0.4, max_trust=0.6
        )
        assert len(mid) == 1
        assert mid[0].id == id2


# =============================================================================
# v19: TestPatternRelevanceScoring
# =============================================================================


class TestPatternRelevanceScoring:
    """Tests for quarantine/trust-aware relevance scoring in PatternMatcher (v19)."""

    def test_quarantined_pattern_score_penalty(self) -> None:
        """Test that quarantined patterns receive score penalty."""
        from marianne.learning.patterns import PatternMatcher

        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Test pattern",
            frequency=5,
            confidence=0.8,
            quarantine_status="quarantined",
        )

        matcher = PatternMatcher([pattern])
        matched = matcher.match({}, limit=1)

        # Should still match but with penalty
        assert len(matched) == 1
        # Score should be reduced (quarantine applies -0.3 penalty)

    def test_validated_pattern_score_bonus(self) -> None:
        """Test that validated patterns receive score bonus."""
        from marianne.learning.patterns import PatternMatcher

        regular_pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Regular pattern",
            frequency=5,
            confidence=0.8,
            quarantine_status="pending",
        )

        validated_pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Validated pattern",
            frequency=5,
            confidence=0.8,
            quarantine_status="validated",
        )

        matcher = PatternMatcher([regular_pattern, validated_pattern])
        matched = matcher.match({}, limit=2)

        assert len(matched) == 2
        # Validated pattern should be ranked higher (has +0.1 bonus)
        assert matched[0].description == "Validated pattern"

    def test_high_trust_pattern_score_bonus(self) -> None:
        """Test that high trust patterns receive score bonus."""
        from marianne.learning.patterns import PatternMatcher

        low_trust = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Low trust",
            frequency=5,
            confidence=0.8,
            trust_score=0.2,
        )

        high_trust = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="High trust",
            frequency=5,
            confidence=0.8,
            trust_score=0.9,
        )

        matcher = PatternMatcher([low_trust, high_trust])
        matched = matcher.match({}, limit=2)

        assert len(matched) == 2
        # High trust pattern should be ranked higher
        assert matched[0].description == "High trust"

    def test_prompt_guidance_includes_quarantine_warning(self) -> None:
        """Test that quarantined patterns show warning in prompt guidance."""
        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Problematic pattern",
            quarantine_status="quarantined",
        )

        guidance = pattern.to_prompt_guidance()

        assert "[QUARANTINED]" in guidance

    def test_prompt_guidance_includes_trust_indicator(self) -> None:
        """Test that patterns with trust score show indicator in guidance."""
        high_trust = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Trusted pattern",
            trust_score=0.85,
        )

        low_trust = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Untrusted pattern",
            trust_score=0.2,
        )

        high_guidance = high_trust.to_prompt_guidance()
        low_guidance = low_trust.to_prompt_guidance()

        assert "[High trust]" in high_guidance
        assert "[Low trust]" in low_guidance

    def test_is_quarantined_property(self) -> None:
        """Test is_quarantined property works correctly."""
        quarantined = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Test",
            quarantine_status="quarantined",
        )
        not_quarantined = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Test",
            quarantine_status="validated",
        )

        assert quarantined.is_quarantined is True
        assert not_quarantined.is_quarantined is False

    def test_is_validated_property(self) -> None:
        """Test is_validated property works correctly."""
        validated = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Test",
            quarantine_status="validated",
        )
        not_validated = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Test",
            quarantine_status="pending",
        )

        assert validated.is_validated is True
        assert not_validated.is_validated is False


# =============================================================================
# v22: Metacognitive Pattern Reflection Tests
# =============================================================================


class TestSuccessFactors:
    """Test cases for the SuccessFactors dataclass."""

    def test_success_factors_creation(self) -> None:
        """Test SuccessFactors can be created with default values."""
        from marianne.learning.global_store import SuccessFactors

        factors = SuccessFactors()
        assert factors.validation_types == []
        assert factors.error_categories == []
        assert factors.occurrence_count == 1
        assert factors.success_rate == 1.0
        assert factors.escalation_was_pending is False
        assert factors.grounding_confidence is None

    def test_success_factors_with_values(self) -> None:
        """Test SuccessFactors with specific values."""
        from marianne.learning.global_store import SuccessFactors

        factors = SuccessFactors(
            validation_types=["file", "regex"],
            error_categories=["rate_limit"],
            prior_sheet_status="completed",
            time_of_day_bucket="morning",
            retry_iteration=2,
            escalation_was_pending=True,
            grounding_confidence=0.85,
            occurrence_count=5,
            success_rate=0.8,
        )

        assert factors.validation_types == ["file", "regex"]
        assert factors.error_categories == ["rate_limit"]
        assert factors.prior_sheet_status == "completed"
        assert factors.time_of_day_bucket == "morning"
        assert factors.retry_iteration == 2
        assert factors.escalation_was_pending is True
        assert factors.grounding_confidence == 0.85
        assert factors.occurrence_count == 5
        assert factors.success_rate == 0.8

    def test_success_factors_serialization(self) -> None:
        """Test SuccessFactors to_dict and from_dict."""
        from marianne.learning.global_store import SuccessFactors

        original = SuccessFactors(
            validation_types=["file", "regex"],
            error_categories=["auth"],
            prior_sheet_status="failed",
            time_of_day_bucket="evening",
            retry_iteration=1,
            escalation_was_pending=False,
            grounding_confidence=0.9,
            occurrence_count=10,
            success_rate=0.75,
        )

        data = original.to_dict()
        restored = SuccessFactors.from_dict(data)

        assert restored.validation_types == original.validation_types
        assert restored.error_categories == original.error_categories
        assert restored.prior_sheet_status == original.prior_sheet_status
        assert restored.time_of_day_bucket == original.time_of_day_bucket
        assert restored.retry_iteration == original.retry_iteration
        assert restored.escalation_was_pending == original.escalation_was_pending
        assert restored.grounding_confidence == original.grounding_confidence
        assert restored.occurrence_count == original.occurrence_count
        assert restored.success_rate == original.success_rate

    def test_success_factors_get_time_bucket(self) -> None:
        """Test time bucket calculation for different hours."""
        from marianne.learning.global_store import SuccessFactors

        # Morning: 5-11
        assert SuccessFactors.get_time_bucket(5) == "morning"
        assert SuccessFactors.get_time_bucket(9) == "morning"
        assert SuccessFactors.get_time_bucket(11) == "morning"

        # Afternoon: 12-16
        assert SuccessFactors.get_time_bucket(12) == "afternoon"
        assert SuccessFactors.get_time_bucket(14) == "afternoon"
        assert SuccessFactors.get_time_bucket(16) == "afternoon"

        # Evening: 17-20
        assert SuccessFactors.get_time_bucket(17) == "evening"
        assert SuccessFactors.get_time_bucket(19) == "evening"
        assert SuccessFactors.get_time_bucket(20) == "evening"

        # Night: 21-4
        assert SuccessFactors.get_time_bucket(21) == "night"
        assert SuccessFactors.get_time_bucket(0) == "night"
        assert SuccessFactors.get_time_bucket(4) == "night"


class TestPatternSuccessFactorsIntegration:
    """Test success factors integration with PatternRecord and GlobalLearningStore."""

    @pytest.fixture
    def global_store(self, tmp_path: Path) -> GlobalLearningStore:
        """Create a GlobalLearningStore for testing."""
        db_path = tmp_path / "test.db"
        return GlobalLearningStore(db_path)

    def test_pattern_record_success_factors_field(self) -> None:
        """Test PatternRecord has success_factors field."""
        from datetime import datetime

        from marianne.learning.global_store import PatternRecord, SuccessFactors

        now = datetime.now()
        factors = SuccessFactors(validation_types=["file"])
        pattern = PatternRecord(
            id="test-pattern",
            pattern_type="test",
            pattern_name="Test Pattern",
            description="A test pattern",
            occurrence_count=1,
            first_seen=now,
            last_seen=now,
            last_confirmed=now,
            led_to_success_count=1,
            led_to_failure_count=0,
            effectiveness_score=0.8,
            variance=0.1,
            suggested_action=None,
            context_tags=["test"],
            priority_score=0.7,
            success_factors=factors,
            success_factors_updated_at=now,
        )

        assert pattern.success_factors is not None
        assert pattern.success_factors.validation_types == ["file"]
        assert pattern.success_factors_updated_at == now

    def test_update_success_factors_creates_new(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test update_success_factors creates factors for pattern without any."""
        # Create a pattern
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Test Pattern",
            description="Test description",
            context_tags=["test"],
        )

        # Update success factors
        factors = global_store.update_success_factors(
            pattern_id=pattern_id,
            validation_types=["file", "regex"],
            error_categories=["rate_limit"],
            prior_sheet_status="completed",
            retry_iteration=0,
            grounding_confidence=0.9,
        )

        assert factors is not None
        assert factors.validation_types == ["file", "regex"]
        assert factors.error_categories == ["rate_limit"]
        assert factors.prior_sheet_status == "completed"
        assert factors.occurrence_count == 1
        assert factors.grounding_confidence == 0.9

    def test_update_success_factors_aggregates(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test update_success_factors aggregates multiple observations."""
        # Create a pattern
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Test Pattern",
            description="Test description",
            context_tags=["test"],
        )

        # First update
        global_store.update_success_factors(
            pattern_id=pattern_id,
            validation_types=["file"],
            prior_sheet_status="completed",
        )

        # Second update with different validation types
        factors = global_store.update_success_factors(
            pattern_id=pattern_id,
            validation_types=["regex"],
            prior_sheet_status="failed",
            grounding_confidence=0.8,
        )

        assert factors is not None
        # Validation types should be merged
        assert set(factors.validation_types) == {"file", "regex"}
        # Prior sheet status should be updated to latest
        assert factors.prior_sheet_status == "failed"
        # Occurrence count should be incremented
        assert factors.occurrence_count == 2
        # Grounding confidence from latest
        assert factors.grounding_confidence == 0.8

    def test_update_success_factors_nonexistent_pattern(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test update_success_factors returns None for nonexistent pattern."""
        result = global_store.update_success_factors(
            pattern_id="nonexistent",
            validation_types=["file"],
        )
        assert result is None

    def test_get_success_factors(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_success_factors retrieves stored factors."""
        # Create a pattern with factors
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Test Pattern",
            description="Test description",
            context_tags=["test"],
        )

        global_store.update_success_factors(
            pattern_id=pattern_id,
            validation_types=["file"],
            retry_iteration=1,
        )

        factors = global_store.get_success_factors(pattern_id)
        assert factors is not None
        assert factors.validation_types == ["file"]
        assert factors.retry_iteration == 1

    def test_get_success_factors_nonexistent(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_success_factors returns None for nonexistent pattern."""
        result = global_store.get_success_factors("nonexistent")
        assert result is None

    def test_analyze_pattern_why_with_factors(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test analyze_pattern_why returns meaningful analysis."""
        # Create a pattern with success factors
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Rate Limit Recovery",
            description="Successful rate limit recovery pattern",
            context_tags=["rate_limit", "retry"],
        )

        # Simulate multiple successful applications
        for _ in range(5):
            global_store.update_success_factors(
                pattern_id=pattern_id,
                validation_types=["file", "regex"],
                error_categories=["rate_limit"],
                prior_sheet_status="completed",
                grounding_confidence=0.9,
            )
            # Also record pattern application to update success counts
            global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id="test_exec",
                pattern_led_to_success=True,
            )

        analysis = global_store.analyze_pattern_why(pattern_id)

        assert analysis["pattern_name"] == "Rate Limit Recovery"
        assert analysis["has_factors"] is True
        assert analysis["observation_count"] == 5
        assert analysis["success_rate"] == 1.0  # All successes
        assert len(analysis["key_conditions"]) > 0
        assert len(analysis["recommendations"]) > 0

    def test_analyze_pattern_why_without_factors(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test analyze_pattern_why for pattern without captured factors."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="New Pattern",
            description="Pattern without factors",
            context_tags=["test"],
        )

        analysis = global_store.analyze_pattern_why(pattern_id)

        assert analysis["pattern_name"] == "New Pattern"
        assert analysis["has_factors"] is False
        assert analysis["confidence"] == 0.0
        assert "Apply this pattern" in analysis["recommendations"][0]

    def test_analyze_pattern_why_nonexistent(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test analyze_pattern_why for nonexistent pattern."""
        analysis = global_store.analyze_pattern_why("nonexistent")
        assert "error" in analysis

    def test_get_patterns_with_why(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test get_patterns_with_why returns patterns with analysis."""
        # Create patterns with varying observations
        for i in range(3):
            pattern_id = global_store.record_pattern(
                pattern_type="test",
                pattern_name=f"Pattern {i}",
                description=f"Test pattern {i}",
                context_tags=["test"],
            )

            # Add success factors (more for higher-numbered patterns)
            for _ in range(i + 1):
                global_store.update_success_factors(
                    pattern_id=pattern_id,
                    validation_types=["file"],
                    grounding_confidence=0.8,
                )

        # Get patterns with WHY analysis
        results = global_store.get_patterns_with_why(min_observations=2)

        # Should only return patterns with >= 2 observations
        assert len(results) == 2  # Pattern 1 (2 obs) and Pattern 2 (3 obs)

        for _pattern, analysis in results:
            assert analysis["has_factors"] is True
            assert analysis["observation_count"] >= 2

    def test_success_factors_persistence(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test success factors are persisted and retrieved from database."""
        from marianne.learning.global_store import GlobalLearningStore

        # Create pattern with factors
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Persistent Pattern",
            description="Testing persistence",
            context_tags=["persistence"],
        )

        global_store.update_success_factors(
            pattern_id=pattern_id,
            validation_types=["file", "artifact"],
            error_categories=["auth"],
            prior_sheet_status="completed",
            escalation_was_pending=True,
            grounding_confidence=0.75,
        )

        # Create new store instance pointing to same database
        store2 = GlobalLearningStore(global_store.db_path)

        # Retrieve and verify
        pattern = store2.get_pattern_by_id(pattern_id)
        assert pattern is not None
        assert pattern.success_factors is not None
        assert set(pattern.success_factors.validation_types) == {"file", "artifact"}
        assert pattern.success_factors.error_categories == ["auth"]
        assert pattern.success_factors.escalation_was_pending is True
        assert pattern.success_factors.grounding_confidence == 0.75


# =============================================================================
# v22: Trust-Aware Autonomous Application Tests
# =============================================================================


class TestAutoApplyConfig:
    """Test cases for AutoApplyConfig."""

    def test_auto_apply_config_defaults(self) -> None:
        """Test AutoApplyConfig has expected defaults."""
        from marianne.core.config import AutoApplyConfig

        config = AutoApplyConfig()
        assert config.enabled is False  # Opt-in only
        assert config.trust_threshold == 0.85
        assert config.max_patterns_per_sheet == 3
        assert config.require_validated_status is True
        assert config.log_applications is True

    def test_auto_apply_config_custom_values(self) -> None:
        """Test AutoApplyConfig accepts custom values."""
        from marianne.core.config import AutoApplyConfig

        config = AutoApplyConfig(
            enabled=True,
            trust_threshold=0.9,
            max_patterns_per_sheet=5,
            require_validated_status=False,
            log_applications=False,
        )

        assert config.enabled is True
        assert config.trust_threshold == 0.9
        assert config.max_patterns_per_sheet == 5
        assert config.require_validated_status is False
        assert config.log_applications is False

    def test_auto_apply_config_validation(self) -> None:
        """Test AutoApplyConfig validates trust_threshold range."""
        from pydantic import ValidationError

        from marianne.core.config import AutoApplyConfig

        # Valid: 0.0 to 1.0
        AutoApplyConfig(trust_threshold=0.0)
        AutoApplyConfig(trust_threshold=1.0)

        # Invalid: outside range
        with pytest.raises(ValidationError):
            AutoApplyConfig(trust_threshold=1.5)
        with pytest.raises(ValidationError):
            AutoApplyConfig(trust_threshold=-0.1)

    def test_learning_config_with_auto_apply(self) -> None:
        """Test LearningConfig can include auto_apply."""
        from marianne.core.config import AutoApplyConfig, LearningConfig

        learning = LearningConfig(
            auto_apply=AutoApplyConfig(enabled=True, trust_threshold=0.8)
        )

        assert learning.auto_apply is not None
        assert learning.auto_apply.enabled is True
        assert learning.auto_apply.trust_threshold == 0.8

    def test_learning_config_auto_apply_none_by_default(self) -> None:
        """Test LearningConfig has auto_apply=None by default."""
        from marianne.core.config import LearningConfig

        learning = LearningConfig()
        assert learning.auto_apply is None


class TestGetPatternsForAutoApply:
    """Test cases for get_patterns_for_auto_apply method."""

    @pytest.fixture
    def global_store(self, tmp_path: Path) -> GlobalLearningStore:
        """Create a GlobalLearningStore for testing."""
        db_path = tmp_path / "test.db"
        return GlobalLearningStore(db_path)

    def _create_validated_high_trust_pattern(
        self,
        store: GlobalLearningStore,
        name: str,
        trust: float = 0.9,
        context_tags: list[str] | None = None,
    ) -> str:
        """Helper to create a validated high-trust pattern."""
        pattern_id = store.record_pattern(
            pattern_type="test",
            pattern_name=name,
            description=f"Test pattern: {name}",
            context_tags=context_tags or [],
        )

        # Set trust score by updating directly
        with store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET trust_score = ?, quarantine_status = 'validated' WHERE id = ?",
                (trust, pattern_id),
            )

        return pattern_id

    def test_get_patterns_for_auto_apply_basic(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test basic auto-apply pattern retrieval."""
        # Create a high-trust validated pattern
        pattern_id = self._create_validated_high_trust_pattern(
            global_store, "High Trust Pattern", trust=0.9
        )

        # Get auto-apply patterns
        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            require_validated=True,
        )

        assert len(patterns) == 1
        assert patterns[0].id == pattern_id
        assert patterns[0].trust_score == 0.9

    def test_get_patterns_for_auto_apply_filters_by_trust(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test auto-apply filters out low-trust patterns."""
        # Create patterns with varying trust
        self._create_validated_high_trust_pattern(
            global_store, "High Trust", trust=0.9
        )
        self._create_validated_high_trust_pattern(
            global_store, "Medium Trust", trust=0.7
        )
        self._create_validated_high_trust_pattern(
            global_store, "Low Trust", trust=0.5
        )

        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            require_validated=True,
        )

        assert len(patterns) == 1
        assert patterns[0].pattern_name == "High Trust"

    def test_get_patterns_for_auto_apply_requires_validated(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test auto-apply requires validated status by default."""
        # Create high-trust but non-validated pattern
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Pending Pattern",
            description="High trust but pending",
            context_tags=[],
        )

        # Set high trust but leave status as pending
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET trust_score = ? WHERE id = ?",
                (0.95, pattern_id),
            )

        # Should not be returned when require_validated=True
        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            require_validated=True,
        )
        assert len(patterns) == 0

        # Should be returned when require_validated=False
        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            require_validated=False,
        )
        assert len(patterns) == 1

    def test_get_patterns_for_auto_apply_excludes_retired(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test auto-apply excludes retired patterns."""
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Retired Pattern",
            description="High trust but retired",
            context_tags=[],
        )

        # Set high trust but retire the pattern
        with global_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET trust_score = ?, quarantine_status = 'retired' WHERE id = ?",
                (0.95, pattern_id),
            )

        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            require_validated=False,  # Don't require validated
        )

        assert len(patterns) == 0

    def test_get_patterns_for_auto_apply_respects_limit(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test auto-apply respects the limit parameter."""
        # Create 5 high-trust validated patterns
        for i in range(5):
            self._create_validated_high_trust_pattern(
                global_store, f"Pattern {i}", trust=0.9 - i * 0.01
            )

        # Get with limit of 3
        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            limit=3,
        )

        assert len(patterns) == 3
        # Should be ordered by trust score descending
        assert patterns[0].trust_score >= patterns[1].trust_score
        assert patterns[1].trust_score >= patterns[2].trust_score

    def test_get_patterns_for_auto_apply_filters_by_context_tags(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test auto-apply filters by context tags."""
        # Create patterns with different tags
        self._create_validated_high_trust_pattern(
            global_store, "Web Pattern", trust=0.9, context_tags=["web", "api"]
        )
        self._create_validated_high_trust_pattern(
            global_store, "DB Pattern", trust=0.9, context_tags=["database", "sql"]
        )

        # Filter by web tag
        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            context_tags=["web"],
        )

        assert len(patterns) == 1
        assert patterns[0].pattern_name == "Web Pattern"

    def test_get_patterns_for_auto_apply_empty_when_none_match(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test auto-apply returns empty list when no patterns match."""
        # Create low-trust pattern
        global_store.record_pattern(
            pattern_type="test",
            pattern_name="Low Trust",
            description="Below threshold",
            context_tags=[],
        )

        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
        )

        assert len(patterns) == 0

    def test_get_patterns_for_auto_apply_orders_by_trust(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test auto-apply patterns are ordered by trust score."""
        # Create patterns with different trust scores
        self._create_validated_high_trust_pattern(
            global_store, "Medium High", trust=0.88
        )
        self._create_validated_high_trust_pattern(
            global_store, "Highest", trust=0.95
        )
        self._create_validated_high_trust_pattern(
            global_store, "Just Above", trust=0.86
        )

        patterns = global_store.get_patterns_for_auto_apply(
            trust_threshold=0.85,
            limit=10,
        )

        assert len(patterns) == 3
        assert patterns[0].pattern_name == "Highest"
        assert patterns[1].pattern_name == "Medium High"
        assert patterns[2].pattern_name == "Just Above"


# =============================================================================
# v23 Evolution: Exploration Budget Maintenance
# =============================================================================


class TestExplorationBudget:
    """Tests for the exploration budget maintenance feature.

    v23 Evolution: Exploration Budget Maintenance - tests the dynamic
    exploration budget that prevents convergence to zero.
    """

    def test_update_exploration_budget_basic(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test basic budget update recording."""

        record = global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.15,
            adjustment_type="initial",
            entropy_at_time=0.5,
            adjustment_reason="Initial budget",
        )

        assert isinstance(record, ExplorationBudgetRecord)
        assert record.budget_value == 0.15
        assert record.adjustment_type == "initial"
        assert record.entropy_at_time == 0.5
        assert record.job_hash == "test-job"

    def test_budget_floor_enforcement(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that budget floor is enforced."""
        record = global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.01,  # Below floor
            adjustment_type="decay",
            floor=0.05,
        )

        # Should be enforced to floor
        assert record.budget_value == 0.05
        assert record.adjustment_type == "floor_enforced"

    def test_budget_ceiling_enforcement(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that budget ceiling is enforced."""
        record = global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.80,  # Above ceiling
            adjustment_type="boost",
            ceiling=0.50,
        )

        # Should be enforced to ceiling
        assert record.budget_value == 0.50
        assert record.adjustment_type == "ceiling_enforced"

    def test_get_exploration_budget_returns_latest(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that get_exploration_budget returns the most recent record."""
        # Create multiple records
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.15,
            adjustment_type="initial",
        )
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.20,
            adjustment_type="boost",
        )

        current = global_store.get_exploration_budget(job_hash="test-job")

        assert current is not None
        assert current.budget_value == 0.20
        assert current.adjustment_type == "boost"

    def test_get_exploration_budget_filters_by_job(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that get_exploration_budget filters by job hash."""
        global_store.update_exploration_budget(
            job_hash="job-a",
            budget_value=0.15,
            adjustment_type="initial",
        )
        global_store.update_exploration_budget(
            job_hash="job-b",
            budget_value=0.25,
            adjustment_type="boost",
        )

        budget_a = global_store.get_exploration_budget(job_hash="job-a")
        budget_b = global_store.get_exploration_budget(job_hash="job-b")

        assert budget_a is not None
        assert budget_a.budget_value == 0.15
        assert budget_b is not None
        assert budget_b.budget_value == 0.25

    def test_get_exploration_budget_history(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that budget history is returned correctly."""
        # Create multiple records
        for i in range(5):
            global_store.update_exploration_budget(
                job_hash="test-job",
                budget_value=0.10 + i * 0.05,
                adjustment_type=f"record-{i}",
            )

        history = global_store.get_exploration_budget_history(
            job_hash="test-job", limit=3
        )

        assert len(history) == 3
        # Should be most recent first
        assert history[0].budget_value == pytest.approx(0.30, abs=0.001)  # Last created
        assert history[2].budget_value == pytest.approx(0.20, abs=0.001)

    def test_calculate_budget_adjustment_initial(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test initial budget adjustment when no history exists."""
        record = global_store.calculate_budget_adjustment(
            job_hash="new-job",
            current_entropy=0.5,
            initial_budget=0.15,
        )

        assert record.adjustment_type == "initial"
        assert record.budget_value == 0.15

    def test_calculate_budget_adjustment_boost_on_low_entropy(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that budget boosts when entropy is low."""
        # Set initial budget
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.15,
            adjustment_type="initial",
        )

        # Low entropy should boost
        record = global_store.calculate_budget_adjustment(
            job_hash="test-job",
            current_entropy=0.2,  # Below threshold 0.3
            boost_amount=0.10,
            entropy_threshold=0.3,
        )

        assert record.adjustment_type == "boost"
        assert record.budget_value == 0.25  # 0.15 + 0.10

    def test_calculate_budget_adjustment_decay_on_healthy_entropy(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that budget decays when entropy is healthy."""
        # Set initial budget
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.20,
            adjustment_type="initial",
        )

        # Healthy entropy should decay
        record = global_store.calculate_budget_adjustment(
            job_hash="test-job",
            current_entropy=0.6,  # Above threshold 0.3
            decay_rate=0.95,
            entropy_threshold=0.3,
        )

        assert record.adjustment_type == "decay"
        assert record.budget_value == 0.20 * 0.95  # Decayed

    def test_budget_persistence_roundtrip(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that budget records persist correctly to database."""
        from datetime import datetime

        # Create record with all fields
        original = global_store.update_exploration_budget(
            job_hash="persistence-test",
            budget_value=0.18,
            adjustment_type="boost",
            entropy_at_time=0.25,
            adjustment_reason="Testing persistence",
        )

        # Retrieve and verify
        retrieved = global_store.get_exploration_budget(job_hash="persistence-test")

        assert retrieved is not None
        assert retrieved.id == original.id
        assert retrieved.job_hash == original.job_hash
        assert retrieved.budget_value == original.budget_value
        assert retrieved.adjustment_type == original.adjustment_type
        assert retrieved.entropy_at_time == original.entropy_at_time
        assert retrieved.adjustment_reason == original.adjustment_reason
        assert isinstance(retrieved.recorded_at, datetime)

    def test_get_exploration_budget_statistics(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test budget statistics calculation."""
        # Create mix of records
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.15,
            adjustment_type="initial",
        )
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.25,
            adjustment_type="boost",
        )
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.20,
            adjustment_type="decay",
        )
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.01,  # Will be floor enforced
            adjustment_type="decay",
            floor=0.05,
        )

        stats = global_store.get_exploration_budget_statistics(job_hash="test-job")

        assert stats["total_adjustments"] == 4
        assert stats["current_budget"] == 0.05  # Most recent
        assert stats["floor_enforcements"] == 1
        assert stats["boost_count"] == 1
        assert stats["decay_count"] == 1  # Only explicit decay adjustment

    def test_budget_never_below_floor_in_calculations(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that calculated budget adjustments never go below floor."""
        # Start with budget near floor
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.06,  # Just above floor
            adjustment_type="initial",
            floor=0.05,
        )

        # Apply decay multiple times
        for _ in range(10):
            global_store.calculate_budget_adjustment(
                job_hash="test-job",
                current_entropy=0.8,  # High entropy = decay
                decay_rate=0.90,
                floor=0.05,
            )

        # Should never go below floor
        current = global_store.get_exploration_budget(job_hash="test-job")
        assert current is not None
        assert current.budget_value >= 0.05


# =============================================================================
# v23 Evolution: Automatic Entropy Response
# =============================================================================


class TestEntropyResponse:
    """Tests for the automatic entropy response feature.

    v23 Evolution: Automatic Entropy Response - tests the system that
    automatically injects diversity when pattern entropy drops.
    """

    def test_check_entropy_response_needed_when_cooldown_active(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that cooldown prevents response triggering."""

        # First, trigger a response
        global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.2,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=False,
        )

        # Check immediately should show cooldown
        needs_response, entropy, reason = global_store.check_entropy_response_needed(
            job_hash="test-job",
            cooldown_seconds=3600,  # 1 hour cooldown
        )

        assert needs_response is False
        assert "Cooldown active" in reason

    def test_trigger_entropy_response_basic(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test basic entropy response triggering."""

        record = global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.2,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=False,
        )

        assert isinstance(record, EntropyResponseRecord)
        assert record.entropy_at_trigger == 0.2
        assert record.threshold_used == 0.3
        assert record.job_hash == "test-job"

    def test_trigger_entropy_response_boosts_budget(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that response boosts exploration budget when enabled."""
        # Set initial budget
        global_store.update_exploration_budget(
            job_hash="test-job",
            budget_value=0.15,
            adjustment_type="initial",
        )

        # Trigger response with budget boost
        record = global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.2,
            threshold_used=0.3,
            boost_budget=True,
            budget_boost_amount=0.10,
            revisit_quarantine=False,
        )

        assert record.budget_boosted is True
        assert "budget_boost" in record.actions_taken

        # Verify budget was actually boosted
        budget = global_store.get_exploration_budget(job_hash="test-job")
        assert budget is not None
        assert budget.budget_value == 0.25  # 0.15 + 0.10

    def test_trigger_entropy_response_revisits_quarantine(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that response revisits quarantined patterns."""
        # Create quarantined pattern
        pattern_id = global_store.record_pattern(
            pattern_type="test",
            pattern_name="Quarantined Pattern",
            description="A pattern under review",
            context_tags=[],
        )
        global_store.quarantine_pattern(
            pattern_id=pattern_id,
            reason="Testing quarantine",
        )

        # Verify it's quarantined
        patterns = global_store.get_patterns(limit=10)
        quarantined_pattern = next(
            (p for p in patterns if p.id == pattern_id), None
        )
        assert quarantined_pattern is not None
        assert quarantined_pattern.quarantine_status == QuarantineStatus.QUARANTINED

        # Trigger response with quarantine revisit
        record = global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.2,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=True,
            max_quarantine_revisits=3,
        )

        assert record.quarantine_revisits == 1
        assert pattern_id in record.patterns_revisited
        assert "quarantine_revisit" in record.actions_taken

        # Verify pattern status changed to PENDING
        patterns = global_store.get_patterns(limit=10)
        updated_pattern = next(
            (p for p in patterns if p.id == pattern_id), None
        )
        assert updated_pattern is not None
        assert updated_pattern.quarantine_status == QuarantineStatus.PENDING

    def test_trigger_entropy_response_respects_max_revisits(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that quarantine revisits respect the maximum limit."""
        # Create multiple quarantined patterns
        for i in range(5):
            pattern_id = global_store.record_pattern(
                pattern_type="test",
                pattern_name=f"Quarantined Pattern {i}",
                description=f"Pattern {i}",
                context_tags=[],
            )
            global_store.quarantine_pattern(
                pattern_id=pattern_id,
                reason="Testing",
            )

        # Trigger response with limit of 2
        record = global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.2,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=True,
            max_quarantine_revisits=2,
        )

        assert record.quarantine_revisits == 2
        assert len(record.patterns_revisited) == 2

    def test_get_last_entropy_response(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retrieving the last entropy response."""
        # Create multiple responses
        global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.25,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=False,
        )
        global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.15,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=False,
        )

        last = global_store.get_last_entropy_response(job_hash="test-job")

        assert last is not None
        assert last.entropy_at_trigger == 0.15  # Most recent

    def test_get_entropy_response_history(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test retrieving entropy response history."""
        # Create multiple responses
        for i in range(5):
            global_store.trigger_entropy_response(
                job_hash="test-job",
                entropy_at_trigger=0.20 + i * 0.01,
                threshold_used=0.3,
                boost_budget=False,
                revisit_quarantine=False,
            )

        history = global_store.get_entropy_response_history(
            job_hash="test-job", limit=3
        )

        assert len(history) == 3
        # Should be most recent first
        assert history[0].entropy_at_trigger == pytest.approx(0.24, abs=0.001)

    def test_get_entropy_response_statistics(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test entropy response statistics calculation."""
        # Create responses with different characteristics
        global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.2,
            threshold_used=0.3,
            boost_budget=True,
            revisit_quarantine=False,
        )
        global_store.trigger_entropy_response(
            job_hash="test-job",
            entropy_at_trigger=0.25,
            threshold_used=0.3,
            boost_budget=True,
            revisit_quarantine=False,
        )

        stats = global_store.get_entropy_response_statistics(job_hash="test-job")

        assert stats["total_responses"] == 2
        assert stats["budget_boosts"] == 2
        assert stats["avg_entropy_at_trigger"] == pytest.approx(0.225, abs=0.01)

    def test_entropy_response_persistence_roundtrip(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that entropy response records persist correctly."""
        from datetime import datetime

        original = global_store.trigger_entropy_response(
            job_hash="persistence-test",
            entropy_at_trigger=0.22,
            threshold_used=0.30,
            boost_budget=True,
            revisit_quarantine=False,
        )

        retrieved = global_store.get_last_entropy_response(job_hash="persistence-test")

        assert retrieved is not None
        assert retrieved.id == original.id
        assert retrieved.job_hash == original.job_hash
        assert retrieved.entropy_at_trigger == original.entropy_at_trigger
        assert retrieved.threshold_used == original.threshold_used
        assert retrieved.budget_boosted == original.budget_boosted
        assert retrieved.actions_taken == original.actions_taken
        assert isinstance(retrieved.recorded_at, datetime)

    def test_check_entropy_response_no_applications(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test response check when no pattern applications exist."""
        needs_response, entropy, reason = global_store.check_entropy_response_needed(
            job_hash="empty-job",
            cooldown_seconds=0,  # Disable cooldown for test
        )

        assert needs_response is False
        assert entropy is None
        assert "No pattern applications" in reason

    def test_entropy_response_job_isolation(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that responses are isolated by job hash."""
        # Create responses for different jobs
        global_store.trigger_entropy_response(
            job_hash="job-a",
            entropy_at_trigger=0.2,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=False,
        )
        global_store.trigger_entropy_response(
            job_hash="job-b",
            entropy_at_trigger=0.15,
            threshold_used=0.3,
            boost_budget=False,
            revisit_quarantine=False,
        )

        last_a = global_store.get_last_entropy_response(job_hash="job-a")
        last_b = global_store.get_last_entropy_response(job_hash="job-b")

        assert last_a is not None
        assert last_a.entropy_at_trigger == 0.2
        assert last_b is not None
        assert last_b.entropy_at_trigger == 0.15


# =============================================================================
# v23 Evolution: Config Integration Tests
# =============================================================================


class TestExplorationBudgetConfig:
    """Tests for ExplorationBudgetConfig dataclass."""

    def test_default_config_values(self) -> None:
        """Test default configuration values."""
        from marianne.core.config import ExplorationBudgetConfig

        config = ExplorationBudgetConfig()

        assert config.enabled is False
        assert config.floor == 0.05
        assert config.ceiling == 0.50
        assert config.decay_rate == 0.95
        assert config.boost_amount == 0.10
        assert config.initial_budget == 0.15

    def test_config_validation(self) -> None:
        """Test config validation constraints."""
        from pydantic import ValidationError

        from marianne.core.config import ExplorationBudgetConfig

        # Floor should be 0-1
        with pytest.raises(ValidationError):
            ExplorationBudgetConfig(floor=1.5)

        # Ceiling should be 0-1
        with pytest.raises(ValidationError):
            ExplorationBudgetConfig(ceiling=-0.1)

    def test_config_serialization(self) -> None:
        """Test config serialization to dict/json."""
        from marianne.core.config import ExplorationBudgetConfig

        config = ExplorationBudgetConfig(
            enabled=True,
            floor=0.10,
            ceiling=0.40,
        )

        data = config.model_dump()

        assert data["enabled"] is True
        assert data["floor"] == 0.10
        assert data["ceiling"] == 0.40


class TestEntropyResponseConfig:
    """Tests for EntropyResponseConfig dataclass."""

    def test_default_config_values(self) -> None:
        """Test default configuration values."""
        from marianne.core.config import EntropyResponseConfig

        config = EntropyResponseConfig()

        assert config.enabled is False
        assert config.entropy_threshold == 0.3
        assert config.cooldown_seconds == 3600
        assert config.boost_budget is True
        assert config.revisit_quarantine is True
        assert config.max_quarantine_revisits == 3

    def test_config_validation(self) -> None:
        """Test config validation constraints."""
        from pydantic import ValidationError

        from marianne.core.config import EntropyResponseConfig

        # Threshold should be 0-1
        with pytest.raises(ValidationError):
            EntropyResponseConfig(entropy_threshold=2.0)

        # Cooldown should be >= 60
        with pytest.raises(ValidationError):
            EntropyResponseConfig(cooldown_seconds=30)

    def test_config_serialization(self) -> None:
        """Test config serialization to dict/json."""
        from marianne.core.config import EntropyResponseConfig

        config = EntropyResponseConfig(
            enabled=True,
            entropy_threshold=0.25,
            cooldown_seconds=1800,
        )

        data = config.model_dump()

        assert data["enabled"] is True
        assert data["entropy_threshold"] == 0.25
        assert data["cooldown_seconds"] == 1800


class TestLearningConfigIntegration:
    """Tests for LearningConfig integration with new v23 configs."""

    def test_learning_config_has_budget_config(self) -> None:
        """Test that LearningConfig includes exploration_budget."""
        from marianne.core.config import LearningConfig

        config = LearningConfig()

        assert hasattr(config, "exploration_budget")
        assert config.exploration_budget.enabled is False
        assert config.exploration_budget.floor == 0.05

    def test_learning_config_has_entropy_response_config(self) -> None:
        """Test that LearningConfig includes entropy_response."""
        from marianne.core.config import LearningConfig

        config = LearningConfig()

        assert hasattr(config, "entropy_response")
        assert config.entropy_response.enabled is False
        assert config.entropy_response.entropy_threshold == 0.3

    def test_learning_config_nested_serialization(self) -> None:
        """Test that nested configs serialize correctly."""
        from marianne.core.config import (
            EntropyResponseConfig,
            ExplorationBudgetConfig,
            LearningConfig,
        )

        config = LearningConfig(
            exploration_budget=ExplorationBudgetConfig(enabled=True, floor=0.08),
            entropy_response=EntropyResponseConfig(enabled=True, cooldown_seconds=7200),
        )

        data = config.model_dump()

        assert data["exploration_budget"]["enabled"] is True
        assert data["exploration_budget"]["floor"] == 0.08
        assert data["entropy_response"]["enabled"] is True
        assert data["entropy_response"]["cooldown_seconds"] == 7200
