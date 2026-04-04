"""Tests for rate limit wait_seconds upper bound capping.

Safety concern: parse_reset_time() has a minimum floor (300s) but no maximum
ceiling. An adversarial or malformed API response like "resets in 999999 hours"
could schedule a timer for ~114 years, effectively blocking the instrument
forever with no auto-recovery. The timer wheel has no upper bound either.

This test file verifies:
1. parse_reset_time() caps at RESET_TIME_MAXIMUM_WAIT_SECONDS (24h)
2. Extreme values are clamped, not passed through
3. Normal values within bounds are unaffected
4. The constant is exported and documented
"""

from __future__ import annotations

import pytest

from mozart.core.constants import (
    RESET_TIME_MAXIMUM_WAIT_SECONDS,
    RESET_TIME_MINIMUM_WAIT_SECONDS,
)
from mozart.core.errors.classifier import ErrorClassifier


@pytest.fixture
def classifier() -> ErrorClassifier:
    return ErrorClassifier()


class TestResetTimeMaximumCap:
    """parse_reset_time must cap at RESET_TIME_MAXIMUM_WAIT_SECONDS."""

    def test_maximum_constant_exists(self) -> None:
        """RESET_TIME_MAXIMUM_WAIT_SECONDS is defined and equals 24 hours."""
        assert RESET_TIME_MAXIMUM_WAIT_SECONDS == 86400.0

    def test_extreme_hours_capped(self, classifier: ErrorClassifier) -> None:
        """'resets in 999999 hours' must be capped to 24h, not 3.6 billion seconds."""
        result = classifier.parse_reset_time("resets in 999999 hours")
        assert result is not None
        assert result == RESET_TIME_MAXIMUM_WAIT_SECONDS

    def test_large_hours_capped(self, classifier: ErrorClassifier) -> None:
        """'resets in 48 hours' must be capped to 24h."""
        result = classifier.parse_reset_time("resets in 48 hours")
        assert result is not None
        assert result == RESET_TIME_MAXIMUM_WAIT_SECONDS

    def test_large_minutes_capped(self, classifier: ErrorClassifier) -> None:
        """'resets in 99999 minutes' must be capped to 24h."""
        result = classifier.parse_reset_time("resets in 99999 minutes")
        assert result is not None
        assert result == RESET_TIME_MAXIMUM_WAIT_SECONDS

    def test_normal_hours_not_capped(self, classifier: ErrorClassifier) -> None:
        """'resets in 3 hours' should return ~10800s, not be capped."""
        result = classifier.parse_reset_time("resets in 3 hours")
        assert result is not None
        assert abs(result - 10800.0) < 10
        assert result < RESET_TIME_MAXIMUM_WAIT_SECONDS

    def test_normal_minutes_not_capped(self, classifier: ErrorClassifier) -> None:
        """'resets in 30 minutes' should return ~1800s, not be capped."""
        result = classifier.parse_reset_time("resets in 30 minutes")
        assert result is not None
        assert abs(result - 1800.0) < 10
        assert result < RESET_TIME_MAXIMUM_WAIT_SECONDS

    def test_24_hours_exactly_is_at_cap(self, classifier: ErrorClassifier) -> None:
        """'resets in 24 hours' should be exactly at the cap."""
        result = classifier.parse_reset_time("resets in 24 hours")
        assert result is not None
        assert result == RESET_TIME_MAXIMUM_WAIT_SECONDS

    def test_minimum_still_enforced(self, classifier: ErrorClassifier) -> None:
        """Minimum floor still works — values below 300s are clamped up."""
        result = classifier.parse_reset_time("resets in 1 minute")
        assert result is not None
        assert result == RESET_TIME_MINIMUM_WAIT_SECONDS

    def test_absolute_time_capped(self, classifier: ErrorClassifier) -> None:
        """Absolute time formats should also respect the 24h cap.

        Since absolute times are max ~24h in the future by design
        (next-day rollover), this is more of a consistency check.
        """
        result = classifier.parse_reset_time("resets at 9pm")
        assert result is not None
        assert result <= RESET_TIME_MAXIMUM_WAIT_SECONDS
        assert result >= RESET_TIME_MINIMUM_WAIT_SECONDS

    def test_boundary_25_hours_capped(self, classifier: ErrorClassifier) -> None:
        """25 hours is just over the cap — must be clamped."""
        result = classifier.parse_reset_time("resets in 25 hours")
        assert result is not None
        assert result == RESET_TIME_MAXIMUM_WAIT_SECONDS
