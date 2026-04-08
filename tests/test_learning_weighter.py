"""Tests for marianne.learning.weighter module.

Covers PatternWeighter: priority calculation, effectiveness scoring,
recency decay, frequency factor, deprecation logic, uncertainty
classification, variance calculation, and the convenience function.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from marianne.learning.weighter import (
    DEFAULT_DECAY_RATE_PER_MONTH,
    DEFAULT_EFFECTIVENESS_THRESHOLD,
    DEFAULT_EPISTEMIC_THRESHOLD,
    DEFAULT_FREQUENCY_BASE,
    DEFAULT_MIN_APPLICATIONS,
    PatternWeighter,
    calculate_priority,
)

# ─── Fixtures ──────────────────────────────────────────────────────────

NOW = datetime(2025, 6, 15, 12, 0, 0)


@pytest.fixture
def weighter() -> PatternWeighter:
    """Default-config weighter."""
    return PatternWeighter()


# ─── Effectiveness ─────────────────────────────────────────────────────


class TestEffectiveness:
    """Tests for PatternWeighter.calculate_effectiveness()."""

    def test_below_min_applications_returns_neutral(self, weighter: PatternWeighter):
        """Fewer than min_applications should return the 0.5 prior."""
        assert weighter.calculate_effectiveness(1, 0) == 0.5
        assert weighter.calculate_effectiveness(0, 1) == 0.5
        assert weighter.calculate_effectiveness(1, 1) == 0.5

    def test_at_min_applications_uses_laplace(self, weighter: PatternWeighter):
        """At exactly min_applications, uses Laplace smoothing."""
        # 3 successes, 0 failures → (3+0.5)/(3+1) = 0.875
        result = weighter.calculate_effectiveness(3, 0)
        assert result == pytest.approx(0.875)

    def test_high_success_rate(self, weighter: PatternWeighter):
        """Mostly successes yields high effectiveness."""
        result = weighter.calculate_effectiveness(10, 1)
        expected = (10 + 0.5) / (11 + 1)
        assert result == pytest.approx(expected)
        assert result > 0.8

    def test_high_failure_rate(self, weighter: PatternWeighter):
        """Mostly failures yields low effectiveness."""
        result = weighter.calculate_effectiveness(1, 10)
        expected = (1 + 0.5) / (11 + 1)
        assert result == pytest.approx(expected)
        assert result < 0.2

    def test_even_split(self, weighter: PatternWeighter):
        """50/50 split gives ~0.5."""
        result = weighter.calculate_effectiveness(5, 5)
        expected = (5 + 0.5) / (10 + 1)
        assert result == pytest.approx(expected)
        assert 0.4 < result < 0.6

    def test_zero_counts_below_threshold(self, weighter: PatternWeighter):
        """Zero successes and zero failures returns neutral prior."""
        assert weighter.calculate_effectiveness(0, 0) == 0.5

    def test_custom_min_applications(self):
        """Custom min_applications threshold is respected."""
        w = PatternWeighter(min_applications_for_effectiveness=10)
        # 5 total < 10 → neutral
        assert w.calculate_effectiveness(3, 2) == 0.5
        # 10 total >= 10 → real calculation
        result = w.calculate_effectiveness(8, 2)
        assert result != 0.5


# ─── Recency Factor ───────────────────────────────────────────────────


class TestRecencyFactor:
    """Tests for PatternWeighter.calculate_recency_factor()."""

    def test_same_day_full_recency(self, weighter: PatternWeighter):
        """Pattern confirmed today has near-perfect recency."""
        result = weighter.calculate_recency_factor(NOW, NOW)
        assert result == pytest.approx(1.0)

    def test_one_month_ago(self, weighter: PatternWeighter):
        """Pattern confirmed 30 days ago decays by ~10%."""
        one_month_ago = NOW - timedelta(days=30)
        result = weighter.calculate_recency_factor(one_month_ago, NOW)
        expected = (1.0 - DEFAULT_DECAY_RATE_PER_MONTH) ** 1.0
        assert result == pytest.approx(expected, rel=0.01)

    def test_three_months_ago(self, weighter: PatternWeighter):
        """Pattern confirmed 90 days ago decays more."""
        three_months = NOW - timedelta(days=90)
        result = weighter.calculate_recency_factor(three_months, NOW)
        expected = (1.0 - DEFAULT_DECAY_RATE_PER_MONTH) ** 3.0
        assert result == pytest.approx(expected, rel=0.01)

    def test_very_old_near_zero(self, weighter: PatternWeighter):
        """Very old pattern has near-zero recency."""
        years_ago = NOW - timedelta(days=365 * 5)
        result = weighter.calculate_recency_factor(years_ago, NOW)
        assert result < 0.01

    def test_custom_decay_rate(self):
        """Custom decay rate changes decay speed."""
        fast_decay = PatternWeighter(decay_rate_per_month=0.5)
        one_month = NOW - timedelta(days=30)
        result = fast_decay.calculate_recency_factor(one_month, NOW)
        assert result == pytest.approx(0.5, rel=0.01)

    def test_result_clamped_to_unit(self, weighter: PatternWeighter):
        """Recency is always in [0, 1]."""
        # Future date could yield >1 without clamping
        future = NOW + timedelta(days=30)
        result = weighter.calculate_recency_factor(future, NOW)
        assert 0.0 <= result <= 1.0


# ─── Frequency Factor ─────────────────────────────────────────────────


class TestFrequencyFactor:
    """Tests for PatternWeighter.calculate_frequency_factor()."""

    def test_zero_occurrences(self, weighter: PatternWeighter):
        """Zero occurrences gives 0.0."""
        assert weighter.calculate_frequency_factor(0) == 0.0

    def test_negative_occurrences(self, weighter: PatternWeighter):
        """Negative occurrences gives 0.0."""
        assert weighter.calculate_frequency_factor(-5) == 0.0

    def test_single_occurrence(self, weighter: PatternWeighter):
        """One occurrence gives a small but positive factor."""
        result = weighter.calculate_frequency_factor(1)
        assert 0.0 < result < 0.3

    def test_moderate_occurrences(self, weighter: PatternWeighter):
        """10 occurrences gives ~0.52."""
        result = weighter.calculate_frequency_factor(10)
        assert 0.4 < result < 0.65

    def test_high_occurrences_cap_at_one(self, weighter: PatternWeighter):
        """100+ occurrences caps at 1.0."""
        assert weighter.calculate_frequency_factor(100) == pytest.approx(1.0, abs=0.02)
        assert weighter.calculate_frequency_factor(1000) == 1.0

    def test_monotonically_increasing(self, weighter: PatternWeighter):
        """Frequency factor increases with occurrence count."""
        values = [weighter.calculate_frequency_factor(n) for n in [1, 5, 10, 50, 100]]
        for i in range(len(values) - 1):
            assert values[i] < values[i + 1]


# ─── Priority Calculation ─────────────────────────────────────────────


class TestCalculatePriority:
    """Tests for PatternWeighter.calculate_priority()."""

    def test_ideal_pattern_high_priority(self, weighter: PatternWeighter):
        """Pattern with many successes, recent, high frequency → high priority."""
        result = weighter.calculate_priority(
            occurrence_count=100,
            led_to_success_count=20,
            led_to_failure_count=0,
            last_confirmed=NOW,
            variance=0.0,
            now=NOW,
        )
        assert result > 0.8

    def test_poor_pattern_low_priority(self, weighter: PatternWeighter):
        """Pattern with many failures, old, low frequency → low priority."""
        result = weighter.calculate_priority(
            occurrence_count=1,
            led_to_success_count=0,
            led_to_failure_count=10,
            last_confirmed=NOW - timedelta(days=365),
            variance=0.25,
            now=NOW,
        )
        assert result < 0.1

    def test_result_clamped_0_to_1(self, weighter: PatternWeighter):
        """Priority is always clamped to [0.0, 1.0]."""
        result = weighter.calculate_priority(
            occurrence_count=10000,
            led_to_success_count=10000,
            led_to_failure_count=0,
            last_confirmed=NOW,
            variance=0.0,
            now=NOW,
        )
        assert 0.0 <= result <= 1.0

    def test_high_variance_reduces_priority(self, weighter: PatternWeighter):
        """High variance penalizes priority."""
        low_var = weighter.calculate_priority(
            occurrence_count=50,
            led_to_success_count=10,
            led_to_failure_count=0,
            last_confirmed=NOW,
            variance=0.0,
            now=NOW,
        )
        high_var = weighter.calculate_priority(
            occurrence_count=50,
            led_to_success_count=10,
            led_to_failure_count=0,
            last_confirmed=NOW,
            variance=0.5,
            now=NOW,
        )
        assert low_var > high_var

    def test_now_defaults_to_current_time(self, weighter: PatternWeighter):
        """Passing now=None uses datetime.now()."""
        result = weighter.calculate_priority(
            occurrence_count=50,
            led_to_success_count=10,
            led_to_failure_count=0,
            last_confirmed=datetime.now(),
            variance=0.0,
        )
        assert 0.0 <= result <= 1.0

    def test_variance_capped_at_one(self, weighter: PatternWeighter):
        """Variance > 1.0 still produces non-negative priority."""
        result = weighter.calculate_priority(
            occurrence_count=50,
            led_to_success_count=10,
            led_to_failure_count=0,
            last_confirmed=NOW,
            variance=2.0,
            now=NOW,
        )
        assert result == 0.0  # (1.0 - min(1.0, 2.0)) = 0 → entire product is 0


# ─── Deprecation ──────────────────────────────────────────────────────


class TestIsDeprecated:
    """Tests for PatternWeighter.is_deprecated()."""

    def test_not_deprecated_with_few_samples(self, weighter: PatternWeighter):
        """Cannot deprecate without enough data."""
        assert not weighter.is_deprecated(0, 2)
        assert not weighter.is_deprecated(1, 1)

    def test_deprecated_with_low_effectiveness(self, weighter: PatternWeighter):
        """Pattern with low success rate is deprecated."""
        # 0 success, 10 failures → effectiveness = 0.5/11 ≈ 0.045 < 0.3
        assert weighter.is_deprecated(0, 10)

    def test_not_deprecated_with_high_effectiveness(self, weighter: PatternWeighter):
        """Pattern with high success rate is not deprecated."""
        assert not weighter.is_deprecated(10, 0)
        assert not weighter.is_deprecated(8, 2)

    def test_boundary_at_threshold(self, weighter: PatternWeighter):
        """Test near the 0.3 threshold boundary."""
        # 2 success, 8 failure → (2+0.5)/11 ≈ 0.227 < 0.3 → deprecated
        assert weighter.is_deprecated(2, 8)
        # 3 success, 7 failure → (3+0.5)/11 ≈ 0.318 > 0.3 → not deprecated
        assert not weighter.is_deprecated(3, 7)


# ─── Uncertainty Classification ───────────────────────────────────────


class TestClassifyUncertainty:
    """Tests for PatternWeighter.classify_uncertainty()."""

    def test_low_variance_epistemic(self, weighter: PatternWeighter):
        """Low variance → epistemic (learnable)."""
        assert weighter.classify_uncertainty(0.1) == "epistemic"
        assert weighter.classify_uncertainty(0.0) == "epistemic"

    def test_high_variance_aleatoric(self, weighter: PatternWeighter):
        """High variance → aleatoric (inherently random)."""
        assert weighter.classify_uncertainty(0.5) == "aleatoric"
        assert weighter.classify_uncertainty(1.0) == "aleatoric"

    def test_boundary(self, weighter: PatternWeighter):
        """At the boundary: < threshold is epistemic, >= is aleatoric."""
        threshold = DEFAULT_EPISTEMIC_THRESHOLD
        assert weighter.classify_uncertainty(threshold - 0.01) == "epistemic"
        assert weighter.classify_uncertainty(threshold) == "aleatoric"

    def test_custom_threshold(self):
        """Custom threshold changes the classification boundary."""
        w = PatternWeighter(epistemic_threshold=0.8)
        assert w.classify_uncertainty(0.7) == "epistemic"
        assert w.classify_uncertainty(0.8) == "aleatoric"


# ─── Variance Calculation ─────────────────────────────────────────────


class TestCalculateVariance:
    """Tests for PatternWeighter.calculate_variance()."""

    def test_empty_outcomes(self, weighter: PatternWeighter):
        """Empty list → 0.0."""
        assert weighter.calculate_variance([]) == 0.0

    def test_single_outcome(self, weighter: PatternWeighter):
        """Single outcome → 0.0."""
        assert weighter.calculate_variance([True]) == 0.0
        assert weighter.calculate_variance([False]) == 0.0

    def test_all_same_outcomes(self, weighter: PatternWeighter):
        """All same outcomes → 0.0 variance."""
        assert weighter.calculate_variance([True, True, True, True]) == 0.0
        assert weighter.calculate_variance([False, False, False]) == 0.0

    def test_even_split_max_variance(self, weighter: PatternWeighter):
        """50/50 split → maximum variance (0.25)."""
        result = weighter.calculate_variance([True, False, True, False])
        assert result == pytest.approx(0.25)

    def test_mostly_true(self, weighter: PatternWeighter):
        """Mostly True outcomes → low variance."""
        result = weighter.calculate_variance([True, True, True, False])
        # mean=0.75, var = ((1-0.75)^2*3 + (0-0.75)^2) / 4 = (0.1875+0.5625)/4 = 0.1875
        assert result == pytest.approx(0.1875)

    def test_mostly_false(self, weighter: PatternWeighter):
        """Mostly False outcomes → low variance."""
        result = weighter.calculate_variance([False, False, False, True])
        assert result == pytest.approx(0.1875)


# ─── Convenience Function ─────────────────────────────────────────────


class TestConvenienceFunction:
    """Tests for the module-level calculate_priority function."""

    def test_returns_valid_priority(self):
        """Convenience function returns a score in [0, 1]."""
        result = calculate_priority(
            occurrence_count=50,
            led_to_success_count=10,
            led_to_failure_count=2,
            last_confirmed=datetime.now(),
            variance=0.1,
        )
        assert 0.0 <= result <= 1.0

    def test_matches_instance_method(self):
        """Convenience function gives same result as instance method."""
        w = PatternWeighter()
        now = datetime.now()
        last = now - timedelta(days=15)
        expected = w.calculate_priority(
            occurrence_count=20,
            led_to_success_count=8,
            led_to_failure_count=2,
            last_confirmed=last,
            variance=0.15,
            now=now,
        )
        result = calculate_priority(
            occurrence_count=20,
            led_to_success_count=8,
            led_to_failure_count=2,
            last_confirmed=last,
            variance=0.15,
        )
        # Both use datetime.now() internally, so they should be very close
        assert result == pytest.approx(expected, abs=0.01)


# ─── Default Constants ─────────────────────────────────────────────────


class TestDefaultConstants:
    """Verify documented default values exist."""

    def test_decay_rate(self):
        assert DEFAULT_DECAY_RATE_PER_MONTH == 0.1

    def test_effectiveness_threshold(self):
        assert DEFAULT_EFFECTIVENESS_THRESHOLD == 0.3

    def test_epistemic_threshold(self):
        assert DEFAULT_EPISTEMIC_THRESHOLD == 0.4

    def test_min_applications(self):
        assert DEFAULT_MIN_APPLICATIONS == 3

    def test_frequency_base(self):
        assert DEFAULT_FREQUENCY_BASE == 100
