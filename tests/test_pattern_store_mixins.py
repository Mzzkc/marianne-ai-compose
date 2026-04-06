"""Direct tests for pattern store mixin modules (D10 coverage).

Tests the pattern store sub-mixins that compose GlobalLearningStore:
- PatternCrudMixin: Bayesian effectiveness, priority scoring
- PatternTrustMixin: Trust score calculation, auto-apply eligibility
- PatternQuarantineMixin: Quarantine lifecycle transitions
- PatternSuccessFactorsMixin: Success factor capture and WHY analysis
- PatternBroadcastMixin: Cross-job pattern discovery broadcasting

These tests exercise the individual mixin methods through a real
GlobalLearningStore instance with a temporary SQLite database.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from marianne.learning.store import GlobalLearningStore
from marianne.learning.store.models import QuarantineStatus
from marianne.learning.store.patterns_crud import PatternCrudMixin

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def store() -> Generator[GlobalLearningStore, None, None]:
    """Create a GlobalLearningStore with a temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    s = GlobalLearningStore(db_path)
    yield s
    if db_path.exists():
        db_path.unlink()


def _seed_pattern(
    store: GlobalLearningStore,
    pattern_name: str = "test pattern",
    pattern_type: str = "validation_failure",
    description: str = "A test pattern",
    context_tags: list[str] | None = None,
) -> str:
    """Helper to create a pattern and return its ID."""
    return store.record_pattern(
        pattern_type=pattern_type,
        pattern_name=pattern_name,
        description=description,
        context_tags=context_tags if context_tags is not None else ["test"],
    )


# =============================================================================
# PatternCrudMixin — Bayesian effectiveness formula
# =============================================================================


class TestBayesianEffectiveness:
    """Tests for the pure-math Bayesian effectiveness function."""

    def test_equal_weight_blends_historical_and_recent(self) -> None:
        """Alpha=0.5 blends historical and recent equally."""
        result = PatternCrudMixin._bayesian_effectiveness(
            historical=0.8,
            recent=0.6,
            days_since_confirmed=0,
            avg_grounding=1.0,
            alpha=0.5,
        )
        # combined = 0.5 * 0.6 + 0.5 * 0.8 = 0.7
        # decay = 0.9^0 = 1.0
        # grounding = 0.7 + 0.3 * 1.0 = 1.0
        # result = 0.7 * 1.0 * 1.0 = 0.7
        assert abs(result - 0.7) < 0.01

    def test_recent_weighted_more_with_high_alpha(self) -> None:
        """Higher alpha gives more weight to recent data."""
        result = PatternCrudMixin._bayesian_effectiveness(
            historical=0.4,
            recent=0.9,
            days_since_confirmed=0,
            avg_grounding=1.0,
            alpha=0.9,
        )
        # combined = 0.9 * 0.9 + 0.1 * 0.4 = 0.81 + 0.04 = 0.85
        assert result > 0.8

    def test_decay_reduces_score_over_time(self) -> None:
        """Patterns not confirmed for 90 days have significantly reduced scores."""
        recent = PatternCrudMixin._bayesian_effectiveness(
            historical=0.8, recent=0.8, days_since_confirmed=0,
            avg_grounding=1.0,
        )
        stale = PatternCrudMixin._bayesian_effectiveness(
            historical=0.8, recent=0.8, days_since_confirmed=90,
            avg_grounding=1.0,
        )
        assert stale < recent
        # 90 days: decay = 0.9^3 = 0.729
        assert stale < recent * 0.75

    def test_low_grounding_reduces_score(self) -> None:
        """Low grounding confidence reduces the effectiveness score."""
        high_ground = PatternCrudMixin._bayesian_effectiveness(
            historical=0.8, recent=0.8, days_since_confirmed=0,
            avg_grounding=1.0,
        )
        low_ground = PatternCrudMixin._bayesian_effectiveness(
            historical=0.8, recent=0.8, days_since_confirmed=0,
            avg_grounding=0.0,
        )
        # grounding_weight(0.0) = 0.7, grounding_weight(1.0) = 1.0
        assert low_ground < high_ground
        assert abs(low_ground / high_ground - 0.7) < 0.01

    def test_zero_recent_with_full_alpha_returns_zero(self) -> None:
        """With alpha=1.0 and recent=0.0, effectiveness is near zero."""
        result = PatternCrudMixin._bayesian_effectiveness(
            historical=1.0, recent=0.0, days_since_confirmed=0,
            avg_grounding=1.0, alpha=1.0,
        )
        assert result < 0.01


class _PriorityMock:
    """Minimal mock providing the FREQUENCY_FACTOR_FLOOR attribute.

    _calculate_priority_score accesses self.FREQUENCY_FACTOR_FLOOR,
    so we can't pass None as self. This mock provides exactly the
    attribute the method needs without requiring a full store instance.
    """

    FREQUENCY_FACTOR_FLOOR = PatternCrudMixin.FREQUENCY_FACTOR_FLOOR


class TestPriorityScore:
    """Tests for priority score calculation."""

    _mock = _PriorityMock()

    def test_zero_occurrences_gives_zero_priority(self) -> None:
        """A pattern never seen has zero frequency factor.

        Even with the frequency floor, occurrence_count=0 gives
        log10(1)/2 = 0, and max(0.6, 0) = 0.6, but effectiveness * 0.6 * 1.0
        is still > 0 when effectiveness > 0. With the floor, a zero-occurrence
        pattern with effectiveness=1.0 gets priority = 0.6, not 0.0.
        """
        result = PatternCrudMixin._calculate_priority_score(
            self._mock,  # type: ignore[arg-type]
            effectiveness=1.0,
            occurrence_count=0,
            variance=0.0,
        )
        # With floor: max(0.6, log10(1)/2) = max(0.6, 0) = 0.6
        # priority = 1.0 * 0.6 * 1.0 = 0.6
        assert result == pytest.approx(0.6, abs=0.01)

    def test_high_variance_penalizes_priority(self) -> None:
        """High variance (inconsistent outcomes) reduces priority."""
        low_var = PatternCrudMixin._calculate_priority_score(
            self._mock,  # type: ignore[arg-type]
            effectiveness=0.8, occurrence_count=100, variance=0.1,
        )
        high_var = PatternCrudMixin._calculate_priority_score(
            self._mock,  # type: ignore[arg-type]
            effectiveness=0.8, occurrence_count=100, variance=0.9,
        )
        assert high_var < low_var
        # variance_penalty: (1-0.1)=0.9 vs (1-0.9)=0.1
        assert low_var > high_var * 5

    def test_more_occurrences_increases_priority(self) -> None:
        """More occurrences increase the frequency factor."""
        few = PatternCrudMixin._calculate_priority_score(
            self._mock,  # type: ignore[arg-type]
            effectiveness=0.8, occurrence_count=1, variance=0.0,
        )
        many = PatternCrudMixin._calculate_priority_score(
            self._mock,  # type: ignore[arg-type]
            effectiveness=0.8, occurrence_count=100, variance=0.0,
        )
        assert many > few

    def test_priority_clamped_to_0_1(self) -> None:
        """Priority score is always between 0 and 1."""
        result = PatternCrudMixin._calculate_priority_score(
            self._mock,  # type: ignore[arg-type]
            effectiveness=1.0, occurrence_count=10000, variance=0.0,
        )
        assert 0.0 <= result <= 1.0


class TestColdStartEffectiveness:
    """Tests for cold-start behavior in effectiveness calculation."""

    def test_below_min_applications_returns_neutral(
        self, store: GlobalLearningStore
    ) -> None:
        """With fewer than min_applications, effectiveness returns 0.55."""
        pid = _seed_pattern(store)
        # Record 2 applications (below default threshold of 3)
        store.record_pattern_application(pid, "exec-1", True)
        store.record_pattern_application(pid, "exec-2", True)

        # Manual effectiveness update
        eff = store.update_pattern_effectiveness(pid)
        assert eff is not None
        assert abs(eff - 0.55) < 0.01

    def test_above_min_applications_uses_bayesian(
        self, store: GlobalLearningStore
    ) -> None:
        """With enough applications, uses Bayesian formula."""
        pid = _seed_pattern(store)
        for i in range(5):
            store.record_pattern_application(pid, f"exec-{i}", True)

        eff = store.update_pattern_effectiveness(pid)
        assert eff is not None
        assert eff != 0.55  # Not the cold-start value


# =============================================================================
# PatternTrustMixin — Trust score calculations
# =============================================================================


class TestTrustScoreCalculation:
    """Tests for trust score formula and lifecycle."""

    def test_new_pattern_starts_at_neutral_trust(
        self, store: GlobalLearningStore
    ) -> None:
        """Newly recorded patterns have trust_score = 0.5."""
        pid = _seed_pattern(store)
        pattern = store.get_pattern_by_id(pid)
        assert pattern is not None
        assert pattern.trust_score == 0.5

    def test_all_successes_increases_trust(
        self, store: GlobalLearningStore
    ) -> None:
        """Pattern with high success rate gets increased trust."""
        pid = _seed_pattern(store)
        for i in range(5):
            store.record_pattern_application(pid, f"exec-{i}", True)

        trust = store.calculate_trust_score(pid)
        assert trust is not None
        assert trust > 0.5  # Should be above neutral

    def test_all_failures_decreases_trust(
        self, store: GlobalLearningStore
    ) -> None:
        """Pattern with high failure rate gets decreased trust."""
        pid = _seed_pattern(store)
        for i in range(5):
            store.record_pattern_application(pid, f"exec-{i}", False)

        trust = store.calculate_trust_score(pid)
        assert trust is not None
        assert trust < 0.5  # Should be below neutral

    def test_quarantined_pattern_gets_penalty(
        self, store: GlobalLearningStore
    ) -> None:
        """Quarantined patterns receive a -0.2 trust penalty."""
        pid = _seed_pattern(store)
        for i in range(3):
            store.record_pattern_application(pid, f"exec-{i}", True)

        trust_before = store.calculate_trust_score(pid)
        assert trust_before is not None

        store.quarantine_pattern(pid, reason="testing")
        trust_after = store.calculate_trust_score(pid)
        assert trust_after is not None
        assert abs(trust_before - trust_after - 0.2) < 0.05

    def test_validated_pattern_gets_bonus(
        self, store: GlobalLearningStore
    ) -> None:
        """Validated patterns receive a +0.1 trust bonus.

        We use a mix of successes and failures to keep trust below 1.0
        so the bonus is visible (not clamped at the ceiling).
        """
        pid = _seed_pattern(store)
        # Mix of outcomes to get trust below 1.0
        store.record_pattern_application(pid, "exec-1", True)
        store.record_pattern_application(pid, "exec-2", False)
        store.record_pattern_application(pid, "exec-3", True)

        trust_before = store.calculate_trust_score(pid)
        assert trust_before is not None
        assert trust_before < 0.9  # Ensure room for bonus

        store.validate_pattern(pid)
        trust_after = store.calculate_trust_score(pid)
        assert trust_after is not None
        assert trust_after > trust_before  # Bonus applied

    def test_trust_clamped_to_0_1(self, store: GlobalLearningStore) -> None:
        """Trust score is always in [0.0, 1.0] range."""
        pid = _seed_pattern(store)
        # Many failures + quarantine to push below 0
        for i in range(10):
            store.record_pattern_application(pid, f"exec-{i}", False)
        store.quarantine_pattern(pid)
        trust = store.calculate_trust_score(pid)
        assert trust is not None
        assert trust >= 0.0
        assert trust <= 1.0

    def test_update_trust_score_delta(
        self, store: GlobalLearningStore
    ) -> None:
        """update_trust_score applies a delta to the current trust."""
        pid = _seed_pattern(store)
        new_trust = store.update_trust_score(pid, 0.3)
        assert new_trust is not None
        assert abs(new_trust - 0.8) < 0.01  # 0.5 + 0.3

    def test_nonexistent_pattern_returns_none(
        self, store: GlobalLearningStore
    ) -> None:
        """calculate_trust_score returns None for missing patterns."""
        result = store.calculate_trust_score("nonexistent-id")
        assert result is None

    def test_recalculate_all_trust_scores(
        self, store: GlobalLearningStore
    ) -> None:
        """recalculate_all_trust_scores updates every pattern."""
        p1 = _seed_pattern(store, pattern_name="pattern 1")
        p2 = _seed_pattern(store, pattern_name="pattern 2")
        store.record_pattern_application(p1, "exec-1", True)
        store.record_pattern_application(p2, "exec-2", False)

        updated = store.recalculate_all_trust_scores()
        assert updated == 2


# =============================================================================
# PatternTrustMixin — High/low trust queries and auto-apply
# =============================================================================


class TestTrustQueries:
    """Tests for trust-based pattern queries."""

    def test_get_high_trust_patterns(
        self, store: GlobalLearningStore
    ) -> None:
        """get_high_trust_patterns returns patterns above threshold."""
        pid = _seed_pattern(store)
        store.update_trust_score(pid, 0.4)  # Trust = 0.9

        high = store.get_high_trust_patterns(threshold=0.7)
        assert len(high) == 1
        assert high[0].id == pid

    def test_get_low_trust_patterns(
        self, store: GlobalLearningStore
    ) -> None:
        """get_low_trust_patterns returns patterns below threshold."""
        pid = _seed_pattern(store)
        store.update_trust_score(pid, -0.3)  # Trust = 0.2

        low = store.get_low_trust_patterns(threshold=0.3)
        assert len(low) == 1
        assert low[0].id == pid

    def test_get_patterns_for_auto_apply_trust_threshold(
        self, store: GlobalLearningStore
    ) -> None:
        """Auto-apply requires trust >= threshold."""
        pid = _seed_pattern(store)
        store.update_trust_score(pid, 0.4)  # Trust = 0.9
        store.validate_pattern(pid)  # VALIDATED status required

        eligible = store.get_patterns_for_auto_apply(
            trust_threshold=0.85, require_validated=True
        )
        assert len(eligible) == 1

    def test_auto_apply_requires_validated_status(
        self, store: GlobalLearningStore
    ) -> None:
        """Auto-apply with require_validated=True excludes PENDING patterns."""
        pid = _seed_pattern(store)
        store.update_trust_score(pid, 0.4)  # High trust but PENDING

        eligible = store.get_patterns_for_auto_apply(
            trust_threshold=0.85, require_validated=True
        )
        assert len(eligible) == 0

    def test_auto_apply_without_validated_requirement(
        self, store: GlobalLearningStore
    ) -> None:
        """Auto-apply with require_validated=False includes all statuses."""
        pid = _seed_pattern(store)
        store.update_trust_score(pid, 0.4)  # Trust = 0.9, still PENDING

        eligible = store.get_patterns_for_auto_apply(
            trust_threshold=0.85, require_validated=False
        )
        assert len(eligible) == 1

    def test_auto_apply_excludes_retired(
        self, store: GlobalLearningStore
    ) -> None:
        """Retired patterns are never eligible for auto-apply."""
        pid = _seed_pattern(store)
        store.update_trust_score(pid, 0.4)
        store.retire_pattern(pid)

        eligible = store.get_patterns_for_auto_apply(
            trust_threshold=0.85, require_validated=False
        )
        assert len(eligible) == 0


# =============================================================================
# PatternQuarantineMixin — Lifecycle transitions
# =============================================================================


class TestQuarantineLifecycle:
    """Tests for pattern quarantine lifecycle transitions."""

    def test_quarantine_pattern(self, store: GlobalLearningStore) -> None:
        """Quarantine moves pattern to QUARANTINED status."""
        pid = _seed_pattern(store)
        result = store.quarantine_pattern(pid, reason="suspicious")
        assert result is True

        pattern = store.get_pattern_by_id(pid)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.QUARANTINED
        assert pattern.quarantine_reason == "suspicious"
        assert pattern.quarantined_at is not None

    def test_validate_pattern(self, store: GlobalLearningStore) -> None:
        """Validate moves pattern to VALIDATED status."""
        pid = _seed_pattern(store)
        result = store.validate_pattern(pid)
        assert result is True

        pattern = store.get_pattern_by_id(pid)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.VALIDATED
        assert pattern.validated_at is not None

    def test_retire_pattern(self, store: GlobalLearningStore) -> None:
        """Retire moves pattern to RETIRED status."""
        pid = _seed_pattern(store)
        result = store.retire_pattern(pid)
        assert result is True

        pattern = store.get_pattern_by_id(pid)
        assert pattern is not None
        assert pattern.quarantine_status == QuarantineStatus.RETIRED

    def test_quarantine_nonexistent_returns_false(
        self, store: GlobalLearningStore
    ) -> None:
        """Quarantine of nonexistent pattern returns False."""
        assert store.quarantine_pattern("nonexistent") is False

    def test_validate_nonexistent_returns_false(
        self, store: GlobalLearningStore
    ) -> None:
        """Validate of nonexistent pattern returns False."""
        assert store.validate_pattern("nonexistent") is False

    def test_retire_nonexistent_returns_false(
        self, store: GlobalLearningStore
    ) -> None:
        """Retire of nonexistent pattern returns False."""
        assert store.retire_pattern("nonexistent") is False

    def test_get_quarantined_patterns(
        self, store: GlobalLearningStore
    ) -> None:
        """get_quarantined_patterns returns only QUARANTINED patterns."""
        _seed_pattern(store, pattern_name="good pattern")
        p2 = _seed_pattern(store, pattern_name="bad pattern")
        p3 = _seed_pattern(store, pattern_name="retired pattern")

        store.quarantine_pattern(p2, reason="harmful")
        store.retire_pattern(p3)

        quarantined = store.get_quarantined_patterns()
        assert len(quarantined) == 1
        assert quarantined[0].id == p2

    def test_full_lifecycle_pending_to_validated(
        self, store: GlobalLearningStore
    ) -> None:
        """Pattern goes through full lifecycle: PENDING → QUARANTINED → VALIDATED."""
        pid = _seed_pattern(store)
        p = store.get_pattern_by_id(pid)
        assert p is not None
        assert p.quarantine_status == QuarantineStatus.PENDING

        store.quarantine_pattern(pid, reason="needs review")
        p = store.get_pattern_by_id(pid)
        assert p is not None
        assert p.quarantine_status == QuarantineStatus.QUARANTINED

        store.validate_pattern(pid)
        p = store.get_pattern_by_id(pid)
        assert p is not None
        assert p.quarantine_status == QuarantineStatus.VALIDATED
        assert p.quarantine_reason is None  # Cleared on validation


# =============================================================================
# PatternSuccessFactorsMixin — WHY analysis
# =============================================================================


class TestSuccessFactors:
    """Tests for success factor capture and WHY analysis."""

    def test_first_update_creates_factors(
        self, store: GlobalLearningStore
    ) -> None:
        """First update creates new SuccessFactors."""
        pid = _seed_pattern(store)
        factors = store.update_success_factors(
            pid,
            validation_types=["file_exists", "regex"],
            error_categories=["auth"],
            retry_iteration=0,
        )
        assert factors is not None
        assert factors.occurrence_count == 1
        assert "file_exists" in factors.validation_types
        assert "regex" in factors.validation_types
        assert "auth" in factors.error_categories
        assert factors.success_rate == 1.0

    def test_subsequent_update_merges_factors(
        self, store: GlobalLearningStore
    ) -> None:
        """Subsequent updates merge into existing factors."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, validation_types=["file_exists"])
        factors = store.update_success_factors(
            pid, validation_types=["regex"]
        )
        assert factors is not None
        assert factors.occurrence_count == 2
        assert "file_exists" in factors.validation_types
        assert "regex" in factors.validation_types

    def test_get_success_factors(
        self, store: GlobalLearningStore
    ) -> None:
        """get_success_factors retrieves captured factors."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, validation_types=["file_exists"])

        factors = store.get_success_factors(pid)
        assert factors is not None
        assert "file_exists" in factors.validation_types

    def test_get_success_factors_none_when_no_data(
        self, store: GlobalLearningStore
    ) -> None:
        """get_success_factors returns None for patterns without factors."""
        pid = _seed_pattern(store)
        factors = store.get_success_factors(pid)
        assert factors is None

    def test_nonexistent_pattern_returns_none(
        self, store: GlobalLearningStore
    ) -> None:
        """update_success_factors returns None for missing patterns."""
        result = store.update_success_factors("nonexistent")
        assert result is None

    def test_analyze_pattern_why_no_factors(
        self, store: GlobalLearningStore
    ) -> None:
        """analyze_pattern_why returns default when no factors captured."""
        pid = _seed_pattern(store)
        analysis = store.analyze_pattern_why(pid)
        assert analysis["has_factors"] is False
        assert analysis["confidence"] == 0.0
        assert len(analysis["recommendations"]) > 0

    def test_analyze_pattern_why_with_factors(
        self, store: GlobalLearningStore
    ) -> None:
        """analyze_pattern_why produces analysis with captured factors."""
        pid = _seed_pattern(store)
        store.record_pattern_application(pid, "exec-1", True)
        store.update_success_factors(
            pid,
            validation_types=["file_exists"],
            error_categories=["timeout"],
            retry_iteration=0,
            grounding_confidence=0.9,
        )

        analysis = store.analyze_pattern_why(pid)
        assert analysis["has_factors"] is True
        assert analysis["confidence"] > 0.0
        assert "key_conditions" in analysis
        assert any("grounding" in c.lower() for c in analysis["key_conditions"])

    def test_analyze_nonexistent_pattern(
        self, store: GlobalLearningStore
    ) -> None:
        """analyze_pattern_why returns error dict for missing patterns."""
        analysis = store.analyze_pattern_why("nonexistent")
        assert "error" in analysis

    def test_get_patterns_with_why(
        self, store: GlobalLearningStore
    ) -> None:
        """get_patterns_with_why returns patterns with analysis attached."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, validation_types=["file_exists"])

        results = store.get_patterns_with_why(min_observations=1)
        assert len(results) == 1
        pattern, analysis = results[0]
        assert pattern.id == pid
        assert "key_conditions" in analysis


# =============================================================================
# PatternBroadcastMixin — Cross-job discovery
# =============================================================================


class TestPatternBroadcast:
    """Tests for pattern discovery broadcasting."""

    def test_record_and_check_discovery(
        self, store: GlobalLearningStore
    ) -> None:
        """Recorded discoveries appear in check_recent_pattern_discoveries."""
        pid = _seed_pattern(store)
        event_id = store.record_pattern_discovery(
            pattern_id=pid,
            pattern_name="test pattern",
            pattern_type="validation_failure",
            job_id="job-a",
            effectiveness_score=0.9,
        )
        assert event_id  # Non-empty string

        discoveries = store.check_recent_pattern_discoveries(
            exclude_job_id="job-b"  # Exclude a different job
        )
        assert len(discoveries) == 1
        assert discoveries[0].pattern_id == pid

    def test_exclude_own_job_discoveries(
        self, store: GlobalLearningStore
    ) -> None:
        """Discoveries from the same job are excluded."""
        pid = _seed_pattern(store)
        store.record_pattern_discovery(
            pattern_id=pid,
            pattern_name="test pattern",
            pattern_type="validation_failure",
            job_id="job-a",
        )

        discoveries = store.check_recent_pattern_discoveries(
            exclude_job_id="job-a"
        )
        assert len(discoveries) == 0

    def test_expired_discoveries_not_returned(
        self, store: GlobalLearningStore
    ) -> None:
        """Expired discoveries are not returned by check."""
        pid = _seed_pattern(store)
        store.record_pattern_discovery(
            pattern_id=pid,
            pattern_name="test pattern",
            pattern_type="validation_failure",
            job_id="job-a",
            ttl_seconds=0.0,  # Already expired
        )

        discoveries = store.check_recent_pattern_discoveries()
        assert len(discoveries) == 0

    def test_cleanup_removes_expired(
        self, store: GlobalLearningStore
    ) -> None:
        """cleanup_expired_pattern_discoveries removes expired events."""
        pid = _seed_pattern(store)
        store.record_pattern_discovery(
            pattern_id=pid,
            pattern_name="test pattern",
            pattern_type="validation_failure",
            job_id="job-a",
            ttl_seconds=0.0,  # Already expired
        )

        deleted = store.cleanup_expired_pattern_discoveries()
        assert deleted == 1

    def test_get_active_discoveries(
        self, store: GlobalLearningStore
    ) -> None:
        """get_active_pattern_discoveries returns unexpired events."""
        pid = _seed_pattern(store)
        store.record_pattern_discovery(
            pattern_id=pid,
            pattern_name="test pattern",
            pattern_type="validation_failure",
            job_id="job-a",
            ttl_seconds=300.0,
        )

        active = store.get_active_pattern_discoveries()
        assert len(active) == 1

    def test_get_active_discoveries_by_type(
        self, store: GlobalLearningStore
    ) -> None:
        """get_active_pattern_discoveries filters by pattern_type."""
        pid = _seed_pattern(store)
        store.record_pattern_discovery(
            pattern_id=pid,
            pattern_name="test pattern",
            pattern_type="validation_failure",
            job_id="job-a",
        )

        # Matching type
        active = store.get_active_pattern_discoveries(
            pattern_type="validation_failure"
        )
        assert len(active) == 1

        # Non-matching type
        active = store.get_active_pattern_discoveries(
            pattern_type="rate_limit"
        )
        assert len(active) == 0


# =============================================================================
# PatternQueryMixin — Query and filtering
# =============================================================================


class TestPatternQuery:
    """Tests for pattern query and filtering methods."""

    def test_get_patterns_filters_by_type(
        self, store: GlobalLearningStore
    ) -> None:
        """get_patterns filters by pattern_type."""
        _seed_pattern(store, pattern_name="p1", pattern_type="validation_failure")
        _seed_pattern(store, pattern_name="p2", pattern_type="rate_limit")

        results = store.get_patterns(
            pattern_type="validation_failure", min_priority=0.0
        )
        assert all(r.pattern_type == "validation_failure" for r in results)

    def test_get_patterns_filters_by_min_priority(
        self, store: GlobalLearningStore
    ) -> None:
        """get_patterns respects min_priority threshold."""
        _seed_pattern(store)
        # Default priority is 0.5 (initial value)
        results = store.get_patterns(min_priority=0.6)
        # New patterns have priority 0.5, so filtered out at 0.6
        assert len(results) == 0

        results = store.get_patterns(min_priority=0.0)
        assert len(results) >= 1

    def test_get_patterns_context_tag_filtering(
        self, store: GlobalLearningStore
    ) -> None:
        """get_patterns filters by context_tags with ANY-match semantics."""
        _seed_pattern(store, pattern_name="tagged",
                      context_tags=["python", "validation"])
        _seed_pattern(store, pattern_name="untagged",
                      context_tags=["javascript"])

        results = store.get_patterns(
            min_priority=0.0, context_tags=["python"]
        )
        assert any(r.pattern_name == "tagged" for r in results)
        # "untagged" doesn't have "python" tag
        assert not any(r.pattern_name == "untagged" for r in results)

    def test_get_pattern_by_id(self, store: GlobalLearningStore) -> None:
        """get_pattern_by_id returns the correct pattern."""
        pid = _seed_pattern(store)
        pattern = store.get_pattern_by_id(pid)
        assert pattern is not None
        assert pattern.id == pid

    def test_get_pattern_by_id_nonexistent(
        self, store: GlobalLearningStore
    ) -> None:
        """get_pattern_by_id returns None for missing IDs."""
        assert store.get_pattern_by_id("nonexistent") is None

    def test_get_pattern_provenance(
        self, store: GlobalLearningStore
    ) -> None:
        """get_pattern_provenance returns provenance info."""
        pid = _seed_pattern(store)
        prov = store.get_pattern_provenance(pid)
        assert prov is not None
        assert prov["pattern_id"] == pid
        assert "first_seen" in prov
        assert "quarantine_status" in prov

    def test_get_pattern_provenance_nonexistent(
        self, store: GlobalLearningStore
    ) -> None:
        """get_pattern_provenance returns None for missing patterns."""
        assert store.get_pattern_provenance("nonexistent") is None

    def test_get_patterns_exclude_quarantined(
        self, store: GlobalLearningStore
    ) -> None:
        """get_patterns with exclude_quarantined=True omits QUARANTINED patterns."""
        p1 = _seed_pattern(store, pattern_name="healthy")
        p2 = _seed_pattern(store, pattern_name="suspect")
        store.quarantine_pattern(p2, reason="testing")

        results = store.get_patterns(
            min_priority=0.0, exclude_quarantined=True
        )
        result_ids = [r.id for r in results]
        assert p1 in result_ids
        assert p2 not in result_ids

    def test_get_patterns_trust_score_filtering(
        self, store: GlobalLearningStore
    ) -> None:
        """get_patterns with min_trust/max_trust filters by trust_score."""
        p1 = _seed_pattern(store, pattern_name="high trust")
        p2 = _seed_pattern(store, pattern_name="low trust")
        store.update_trust_score(p1, 0.4)   # Trust = 0.9
        store.update_trust_score(p2, -0.3)  # Trust = 0.2

        high = store.get_patterns(min_priority=0.0, min_trust=0.7)
        assert any(r.id == p1 for r in high)
        assert not any(r.id == p2 for r in high)

        low = store.get_patterns(min_priority=0.0, max_trust=0.3)
        assert any(r.id == p2 for r in low)
        assert not any(r.id == p1 for r in low)


# =============================================================================
# PatternSuccessFactorsMixin — Extended WHY coverage (Q012)
# =============================================================================


class TestSuccessFactorsExtended:
    """Extended tests for success factor update branches and analysis edge cases."""

    def test_update_merges_error_categories(
        self, store: GlobalLearningStore
    ) -> None:
        """Second update merges error_categories into existing factors."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, error_categories=["auth"])
        factors = store.update_success_factors(pid, error_categories=["timeout"])
        assert factors is not None
        assert "auth" in factors.error_categories
        assert "timeout" in factors.error_categories

    def test_update_sets_prior_sheet_status(
        self, store: GlobalLearningStore
    ) -> None:
        """Second update with prior_sheet_status replaces existing value."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, prior_sheet_status="completed")
        factors = store.update_success_factors(pid, prior_sheet_status="failed")
        assert factors is not None
        assert factors.prior_sheet_status == "failed"

    def test_update_sets_grounding_confidence(
        self, store: GlobalLearningStore
    ) -> None:
        """Second update with grounding_confidence updates existing value."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, grounding_confidence=0.5)
        factors = store.update_success_factors(pid, grounding_confidence=0.95)
        assert factors is not None
        assert factors.grounding_confidence == 0.95

    def test_update_computes_success_rate_from_applications(
        self, store: GlobalLearningStore
    ) -> None:
        """Second update recalculates success_rate from pattern application counts."""
        pid = _seed_pattern(store)
        # Record applications to set led_to_success/failure counts
        store.record_pattern_application(pid, "exec-1", True)
        store.record_pattern_application(pid, "exec-2", False)
        store.record_pattern_application(pid, "exec-3", True)

        # First update
        store.update_success_factors(pid, validation_types=["file"])
        # Second update triggers the branch that reads counts
        factors = store.update_success_factors(pid, validation_types=["regex"])
        assert factors is not None
        # 2 successes / 3 total = 0.666...
        assert 0.6 < factors.success_rate < 0.7

    def test_get_success_factors_nonexistent_pattern(
        self, store: GlobalLearningStore
    ) -> None:
        """get_success_factors returns None for nonexistent pattern."""
        result = store.get_success_factors("nonexistent-id")
        assert result is None

    def test_analyze_pattern_why_time_of_day_in_summary(
        self, store: GlobalLearningStore
    ) -> None:
        """analyze_pattern_why includes time_of_day_bucket in factors_summary."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, validation_types=["file"])
        # Factors will have a time_of_day_bucket from datetime.now()
        analysis = store.analyze_pattern_why(pid)
        assert analysis["has_factors"] is True
        # time_of_day_bucket is always set, so summary should include it
        assert "typically succeeds in" in analysis["factors_summary"]

    def test_analyze_pattern_why_prior_sheet_status_in_summary(
        self, store: GlobalLearningStore
    ) -> None:
        """analyze_pattern_why includes prior_sheet_status in factors_summary."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, prior_sheet_status="completed")
        analysis = store.analyze_pattern_why(pid)
        assert "prior sheet was: completed" in analysis["factors_summary"]

    def test_analyze_pattern_why_retry_iteration_gt_zero(
        self, store: GlobalLearningStore
    ) -> None:
        """analyze_pattern_why shows retry count when retry_iteration > 0."""
        pid = _seed_pattern(store)
        store.update_success_factors(pid, retry_iteration=3)
        analysis = store.analyze_pattern_why(pid)
        assert any("retries" in c for c in analysis["key_conditions"])

    def test_analyze_pattern_why_low_success_rate_recommendation(
        self, store: GlobalLearningStore
    ) -> None:
        """analyze_pattern_why recommends review when success_rate < 0.5."""
        pid = _seed_pattern(store)
        # Create factors with low success rate
        store.update_success_factors(pid, validation_types=["file"])
        # Manually set low success rate via DB
        with store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT success_factors FROM patterns WHERE id = ?", (pid,)
            )
            row = cursor.fetchone()
            data = json.loads(row[0])
            data["success_rate"] = 0.3
            conn.execute(
                "UPDATE patterns SET success_factors = ? WHERE id = ?",
                (json.dumps(data), pid),
            )
        analysis = store.analyze_pattern_why(pid)
        assert any("Low success rate" in r for r in analysis["recommendations"])
