"""Tests for pattern lifecycle automation (v25 evolution).

Tests the Pattern Lifecycle Validation Feedback Loop that auto-promotes
patterns from PENDING to VALIDATED/QUARANTINED based on effectiveness
and application count.
"""

import tempfile
from pathlib import Path

import pytest

from marianne.learning.global_store import GlobalLearningStore
from marianne.learning.store.models import QuarantineStatus
from marianne.learning.store.patterns_lifecycle import (
    DEGRADATION_THRESHOLD,
    MIN_OCCURRENCES_FOR_PROMOTION,
    PROMOTION_EFFECTIVENESS_THRESHOLD,
    QUARANTINE_EFFECTIVENESS_THRESHOLD,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_lifecycle.db"
        yield db_path


@pytest.fixture
def store(temp_db):
    """Create a GlobalLearningStore instance."""
    return GlobalLearningStore(temp_db)


def test_promote_ready_patterns_no_patterns(store):
    """Test promotion with no patterns in database."""
    result = store.promote_ready_patterns()
    assert result["promoted"] == []
    assert result["quarantined"] == []
    assert result["degraded"] == []


def test_promote_ready_patterns_insufficient_applications(store):
    """Test that patterns with < MIN_OCCURRENCES are not promoted."""
    # Create a pattern with high effectiveness but insufficient applications
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Test Pattern",
        description="High effectiveness, low applications",
    )

    # Record only 2 applications (below threshold of 3)
    for i in range(2):
        store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"exec_{i}",
            pattern_led_to_success=True,
        )

    result = store.promote_ready_patterns()
    assert result["promoted"] == []
    assert result["quarantined"] == []

    # Verify pattern is still PENDING
    pattern = store.get_pattern_by_id(pattern_id)
    assert pattern is not None
    assert pattern.quarantine_status == QuarantineStatus.PENDING


def test_promote_to_validated(store):
    """Test promotion from PENDING to VALIDATED when effectiveness > 0.60."""
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="High Effectiveness Pattern",
        description="Should be promoted to VALIDATED",
    )

    # Record 5 successful applications (effectiveness ~0.73 after Bayesian)
    for i in range(5):
        store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"exec_{i}",
            pattern_led_to_success=True,
        )

    result = store.promote_ready_patterns()
    assert pattern_id in result["promoted"]
    assert pattern_id not in result["quarantined"]

    # Verify pattern is now VALIDATED
    pattern = store.get_pattern_by_id(pattern_id)
    assert pattern is not None
    assert pattern.quarantine_status == QuarantineStatus.VALIDATED
    assert pattern.validated_at is not None


def test_promote_to_quarantined(store):
    """Test promotion from PENDING to QUARANTINED when effectiveness < 0.35."""
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Low Effectiveness Pattern",
        description="Should be quarantined",
    )

    # Record mostly failures (5 failures, 1 success = effectiveness ~0.24)
    for i in range(5):
        store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"exec_{i}",
            pattern_led_to_success=False,
        )
    # One success to avoid complete failure
    store.record_pattern_application(
        pattern_id=pattern_id,
        execution_id="exec_5",
        pattern_led_to_success=True,
    )

    result = store.promote_ready_patterns()
    assert pattern_id in result["quarantined"]
    assert pattern_id not in result["promoted"]

    # Verify pattern is now QUARANTINED
    pattern = store.get_pattern_by_id(pattern_id)
    assert pattern is not None
    assert pattern.quarantine_status == QuarantineStatus.QUARANTINED
    assert pattern.quarantined_at is not None


def test_degrade_validated_pattern(store):
    """Test degradation from VALIDATED to QUARANTINED when effectiveness drops."""
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Degrading Pattern",
        description="Was good, now bad",
    )

    # First, promote to VALIDATED with good effectiveness
    for i in range(5):
        store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"exec_{i}",
            pattern_led_to_success=True,
        )

    result = store.promote_ready_patterns()
    assert pattern_id in result["promoted"]

    # Manually set to VALIDATED (already should be, but explicit is better)
    store.update_quarantine_status(pattern_id, QuarantineStatus.VALIDATED)

    # Now add many failures to drop effectiveness below degradation threshold
    for i in range(15):
        store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"fail_{i}",
            pattern_led_to_success=False,
        )

    # Trigger promotion cycle (should degrade)
    result = store.promote_ready_patterns()
    assert pattern_id in result["degraded"]

    # Verify pattern is now QUARANTINED
    pattern = store.get_pattern_by_id(pattern_id)
    assert pattern is not None
    assert pattern.quarantine_status == QuarantineStatus.QUARANTINED


def test_bayesian_effectiveness_update(store):
    """Test that effectiveness scores update correctly with Bayesian math."""
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Bayesian Test Pattern",
        description="Testing Bayesian updates",
    )

    # Record 3 successes
    for i in range(3):
        store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"exec_{i}",
            pattern_led_to_success=True,
        )

    # Retrieve pattern and check effectiveness
    pattern = store.get_pattern_by_id(pattern_id)
    assert pattern is not None
    initial_effectiveness = pattern.effectiveness_score

    # Effectiveness after 3 successes should be > 0.55 (baseline)
    assert initial_effectiveness > 0.55
    assert pattern.led_to_success_count == 3
    assert pattern.led_to_failure_count == 0


def test_multiple_patterns_mixed_promotion(store):
    """Test promotion cycle with multiple patterns of varying effectiveness."""
    # Create 3 patterns: one high, one low, one mid
    high_id = store.record_pattern(
        pattern_type="test",
        pattern_name="High Pattern",
        description="High effectiveness",
    )
    low_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Low Pattern",
        description="Low effectiveness",
    )
    mid_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Mid Pattern",
        description="Mid effectiveness",
    )

    # High pattern: 5 successes
    for i in range(5):
        store.record_pattern_application(
            high_id, f"exec_high_{i}", pattern_led_to_success=True
        )

    # Low pattern: 5 failures
    for i in range(5):
        store.record_pattern_application(
            low_id, f"exec_low_{i}", pattern_led_to_success=False
        )

    # Mid pattern: 3 successes, 2 failures (effectiveness ~0.45, stays PENDING)
    for i in range(3):
        store.record_pattern_application(
            mid_id, f"exec_mid_{i}", pattern_led_to_success=True
        )
    for i in range(2):
        store.record_pattern_application(
            mid_id, f"exec_mid_fail_{i}", pattern_led_to_success=False
        )

    result = store.promote_ready_patterns()

    # Assertions
    assert high_id in result["promoted"]
    assert low_id in result["quarantined"]
    # Mid should not be in either (effectiveness between 0.35 and 0.60)
    assert mid_id not in result["promoted"]
    assert mid_id not in result["quarantined"]

    # Verify statuses
    high_pattern = store.get_pattern_by_id(high_id)
    low_pattern = store.get_pattern_by_id(low_id)
    mid_pattern = store.get_pattern_by_id(mid_id)

    assert high_pattern.quarantine_status == QuarantineStatus.VALIDATED
    assert low_pattern.quarantine_status == QuarantineStatus.QUARANTINED
    assert mid_pattern.quarantine_status == QuarantineStatus.PENDING


def test_update_quarantine_status_manual(store):
    """Test manual quarantine status update."""
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Manual Update Test",
        description="Testing manual updates",
    )

    # Manually promote to VALIDATED (bypassing auto-promotion)
    result = store.update_quarantine_status(pattern_id, QuarantineStatus.VALIDATED)
    assert result is True

    pattern = store.get_pattern_by_id(pattern_id)
    assert pattern is not None
    assert pattern.quarantine_status == QuarantineStatus.VALIDATED
    assert pattern.validated_at is not None

    # Manually quarantine
    result = store.update_quarantine_status(pattern_id, QuarantineStatus.QUARANTINED)
    assert result is True

    pattern = store.get_pattern_by_id(pattern_id)
    assert pattern.quarantine_status == QuarantineStatus.QUARANTINED
    assert pattern.quarantined_at is not None


def test_update_quarantine_status_not_found(store):
    """Test manual update on non-existent pattern."""
    result = store.update_quarantine_status("nonexistent", QuarantineStatus.VALIDATED)
    assert result is False


def test_promotion_cycle_idempotent(store):
    """Test that promotion cycle is idempotent (doesn't re-promote)."""
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Idempotent Test",
        description="Testing idempotency",
    )

    # Record 5 successes
    for i in range(5):
        store.record_pattern_application(
            pattern_id, f"exec_{i}", pattern_led_to_success=True
        )

    # First promotion
    result1 = store.promote_ready_patterns()
    assert pattern_id in result1["promoted"]

    # Second promotion (should be no-op)
    result2 = store.promote_ready_patterns()
    assert pattern_id not in result2["promoted"]  # Already VALIDATED
    assert pattern_id not in result2["quarantined"]
    assert pattern_id not in result2["degraded"]


def test_promotion_thresholds():
    """Test that promotion threshold constants are correctly defined."""
    assert MIN_OCCURRENCES_FOR_PROMOTION == 3
    assert PROMOTION_EFFECTIVENESS_THRESHOLD == 0.60
    assert QUARANTINE_EFFECTIVENESS_THRESHOLD == 0.35
    assert DEGRADATION_THRESHOLD == 0.30
    # Ensure thresholds are ordered correctly
    assert DEGRADATION_THRESHOLD < QUARANTINE_EFFECTIVENESS_THRESHOLD
    assert QUARANTINE_EFFECTIVENESS_THRESHOLD < PROMOTION_EFFECTIVENESS_THRESHOLD


def test_grounding_weighted_effectiveness(store):
    """Test that grounding confidence affects effectiveness scoring."""
    pattern_id = store.record_pattern(
        pattern_type="test",
        pattern_name="Grounding Test",
        description="Testing grounding confidence",
    )

    # Record 3 successes with high grounding confidence
    for i in range(3):
        store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"exec_{i}",
            pattern_led_to_success=True,
            grounding_confidence=0.95,  # High confidence
        )

    pattern_high = store.get_pattern_by_id(pattern_id)
    assert pattern_high is not None
    effectiveness_high = pattern_high.effectiveness_score

    # Create another pattern with same success rate but low grounding
    pattern_id_low = store.record_pattern(
        pattern_type="test",
        pattern_name="Grounding Test Low",
        description="Low grounding confidence",
    )

    for i in range(3):
        store.record_pattern_application(
            pattern_id=pattern_id_low,
            execution_id=f"exec_{i}",
            pattern_led_to_success=True,
            grounding_confidence=0.2,  # Low confidence
        )

    pattern_low = store.get_pattern_by_id(pattern_id_low)
    assert pattern_low is not None
    effectiveness_low = pattern_low.effectiveness_score

    # High grounding should yield higher effectiveness
    assert effectiveness_high > effectiveness_low
