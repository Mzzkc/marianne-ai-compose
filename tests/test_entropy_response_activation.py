"""Tests for entropy response activation (Evolution v25 - Candidate 3).

Verifies that:
1. Entropy threshold configuration is respected
2. Periodic entropy checks trigger responses when entropy drops below threshold
3. Exploration budget is injected when entropy collapse is detected
4. Quarantined patterns are revisited during entropy responses
5. Entropy response events are recorded in the learning store
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mozart.daemon.config import DaemonConfig, SemanticLearningConfig
from mozart.daemon.health import HealthChecker
from mozart.learning.patterns import PatternType
from mozart.learning.store import GlobalLearningStore
from mozart.learning.store.models import QuarantineStatus


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def learning_store(temp_db: Path) -> GlobalLearningStore:
    """Create a learning store with test database."""
    store = GlobalLearningStore(temp_db)
    yield store
    temp_db.unlink(missing_ok=True)


@pytest.fixture
def daemon_config() -> DaemonConfig:
    """Create daemon config with low entropy threshold for testing."""
    return DaemonConfig(
        learning=SemanticLearningConfig(
            enabled=True,
            entropy_threshold=0.1,
            exploration_budget=0.15,
            entropy_check_interval_seconds=60.0,
        ),
    )


@pytest.fixture
def mock_manager() -> MagicMock:
    """Create a mock job manager."""
    manager = MagicMock()
    manager.shutting_down = False
    manager._config = DaemonConfig(
        learning=SemanticLearningConfig(
            entropy_threshold=0.1,
            exploration_budget=0.15,
            entropy_check_interval_seconds=60.0,
        ),
    )
    return manager


@pytest.fixture
def mock_monitor() -> MagicMock:
    """Create a mock resource monitor."""
    return MagicMock()


def _create_homogeneous_patterns(
    store: GlobalLearningStore, pattern_count: int = 5, dominant_apps: int = 100
) -> None:
    """Create patterns with homogeneous application distribution (entropy = 0).

    Args:
        store: Learning store to populate.
        pattern_count: Number of patterns to create.
        dominant_apps: Number of applications for the dominant pattern.
    """
    # Create one dominant pattern with many applications
    dominant_id = store.record_pattern(
        pattern_type=PatternType.SEMANTIC_INSIGHT.value,
        pattern_name="dominant_pattern",
        description="Dominant pattern with many applications",
        context_tags=["test"],
    )

    # Record many applications for the dominant pattern
    for i in range(dominant_apps):
        store.record_pattern_application(
            pattern_id=dominant_id,
            execution_id=f"test-execution-{i}",
            pattern_led_to_success=True,
            grounding_confidence=0.8,
        )

    # Create other patterns with zero applications
    for i in range(pattern_count - 1):
        store.record_pattern(
            pattern_type=PatternType.SEMANTIC_INSIGHT.value,
            pattern_name=f"unused_pattern_{i}",
            description=f"Unused pattern {i}",
            context_tags=["test"],
        )


def test_entropy_calculation_with_homogeneous_patterns(
    learning_store: GlobalLearningStore,
) -> None:
    """Test that entropy calculation correctly detects homogeneous patterns."""
    # Create 5 patterns where only 1 has all applications (entropy = 0)
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    # Calculate entropy
    metrics = learning_store.calculate_pattern_entropy()

    # Verify entropy is near 0 (all applications in one pattern)
    assert metrics.shannon_entropy == 0.0, "Shannon entropy should be 0 for single pattern"
    assert metrics.diversity_index == 0.0, "Diversity index should be 0"
    assert metrics.effective_pattern_count == 1, "Only 1 pattern has applications"
    assert metrics.dominant_pattern_share == 1.0, "Dominant pattern has 100% share"


def test_entropy_check_triggers_response_below_threshold(
    learning_store: GlobalLearningStore,
) -> None:
    """Test that entropy check triggers response when below threshold."""
    # Create homogeneous patterns (entropy = 0)
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    # Check if response is needed (threshold = 0.1)
    needs_response, current_entropy, reason = (
        learning_store.check_entropy_response_needed(
            job_hash="test-job",
            entropy_threshold=0.1,
            cooldown_seconds=3600,
        )
    )

    assert needs_response is True, "Response should be needed when entropy < threshold"
    assert current_entropy == 0.0, "Entropy should be 0.0"
    assert "< threshold" in reason, "Reason should mention threshold"


def test_entropy_response_boosts_exploration_budget(
    learning_store: GlobalLearningStore,
) -> None:
    """Test that entropy response boosts exploration budget."""
    # Create homogeneous patterns
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    # Set initial budget
    initial_budget = learning_store.update_exploration_budget(
        job_hash="test-job",
        budget_value=0.10,
        adjustment_type="initial",
        entropy_at_time=0.0,
    )
    assert initial_budget.budget_value == 0.10

    # Trigger entropy response with 0.15 boost
    from mozart.learning.store.budget import (
        EntropyResponseConfig,
        EntropyTriggerContext,
    )

    trigger = EntropyTriggerContext(
        job_hash="test-job",
        entropy_at_trigger=0.0,
        threshold_used=0.1,
    )
    config = EntropyResponseConfig(
        boost_budget=True,
        budget_boost_amount=0.15,
        budget_floor=0.05,
        budget_ceiling=0.50,
    )

    response = learning_store.trigger_entropy_response(trigger=trigger, config=config)

    # Verify response
    assert response.budget_boosted is True, "Budget should be boosted"
    assert "budget_boost" in response.actions_taken

    # Verify budget increased
    new_budget = learning_store.get_exploration_budget("test-job")
    assert new_budget is not None
    assert new_budget.budget_value == 0.25, "Budget should be 0.10 + 0.15 = 0.25"
    assert new_budget.adjustment_type == "boost"


def test_entropy_response_revisits_quarantined_patterns(
    learning_store: GlobalLearningStore,
) -> None:
    """Test that entropy response revisits quarantined patterns."""
    # Create some quarantined patterns
    quarantined_ids = []
    for i in range(3):
        pattern_id = learning_store.record_pattern(
            pattern_type=PatternType.SEMANTIC_INSIGHT.value,
            pattern_name=f"quarantined_pattern_{i}",
            description=f"Quarantined pattern {i}",
            context_tags=["test"],
        )
        # Manually set to quarantined status
        with learning_store._get_connection() as conn:
            conn.execute(
                "UPDATE patterns SET quarantine_status = ? WHERE id = ?",
                (QuarantineStatus.QUARANTINED.value, pattern_id),
            )
        quarantined_ids.append(pattern_id)

    # Trigger entropy response with quarantine revisit
    from mozart.learning.store.budget import (
        EntropyResponseConfig,
        EntropyTriggerContext,
    )

    trigger = EntropyTriggerContext(
        job_hash="test-job",
        entropy_at_trigger=0.0,
        threshold_used=0.1,
    )
    config = EntropyResponseConfig(
        boost_budget=False,  # Only test quarantine revisit
        revisit_quarantine=True,
        max_quarantine_revisits=3,
    )

    response = learning_store.trigger_entropy_response(trigger=trigger, config=config)

    # Verify response
    assert response.quarantine_revisits == 3, "Should revisit 3 patterns"
    assert "quarantine_revisit" in response.actions_taken
    assert len(response.patterns_revisited) == 3

    # Verify patterns moved from QUARANTINED to PENDING
    for pattern_id in quarantined_ids:
        with learning_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT quarantine_status FROM patterns WHERE id = ?",
                (pattern_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert (
                row["quarantine_status"] == QuarantineStatus.PENDING.value
            ), "Pattern should be PENDING after revisit"


def test_entropy_response_recorded_in_database(
    learning_store: GlobalLearningStore,
) -> None:
    """Test that entropy response events are recorded in the database."""
    # Create homogeneous patterns
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    # Trigger entropy response
    from mozart.learning.store.budget import (
        EntropyResponseConfig,
        EntropyTriggerContext,
    )

    trigger = EntropyTriggerContext(
        job_hash="test-job",
        entropy_at_trigger=0.0,
        threshold_used=0.1,
    )
    config = EntropyResponseConfig(
        boost_budget=True,
        revisit_quarantine=False,
        budget_boost_amount=0.15,
    )

    response = learning_store.trigger_entropy_response(trigger=trigger, config=config)

    # Verify response recorded
    with learning_store._get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM entropy_responses WHERE id = ?",
            (response.id,),
        )
        row = cursor.fetchone()

        assert row is not None, "Response should be recorded in database"
        assert row["job_hash"] == "test-job"
        assert row["entropy_at_trigger"] == 0.0
        assert row["threshold_used"] == 0.1
        assert row["budget_boosted"] == 1


def test_cooldown_prevents_rapid_responses(
    learning_store: GlobalLearningStore,
) -> None:
    """Test that cooldown prevents rapid successive entropy responses."""
    # Create homogeneous patterns
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    # First check should trigger response
    needs_response1, _, reason1 = learning_store.check_entropy_response_needed(
        job_hash="test-job",
        entropy_threshold=0.1,
        cooldown_seconds=3600,
    )
    assert needs_response1 is True, "First check should trigger response"

    # Trigger first response
    from mozart.learning.store.budget import (
        EntropyResponseConfig,
        EntropyTriggerContext,
    )

    trigger = EntropyTriggerContext(
        job_hash="test-job",
        entropy_at_trigger=0.0,
        threshold_used=0.1,
    )
    config = EntropyResponseConfig(boost_budget=True, budget_boost_amount=0.15)

    learning_store.trigger_entropy_response(trigger=trigger, config=config)

    # Second check immediately after should NOT trigger (cooldown)
    needs_response2, _, reason2 = learning_store.check_entropy_response_needed(
        job_hash="test-job",
        entropy_threshold=0.1,
        cooldown_seconds=3600,
    )
    assert needs_response2 is False, "Second check should be blocked by cooldown"
    assert "Cooldown active" in reason2, "Reason should mention cooldown"


@pytest.mark.asyncio
async def test_health_checker_on_job_completed_increments_counter(
    mock_manager: MagicMock,
    mock_monitor: MagicMock,
    learning_store: GlobalLearningStore,
) -> None:
    """Test that on_job_completed increments the counter and triggers check at 10."""
    health = HealthChecker(
        mock_manager,
        mock_monitor,
        learning_store=learning_store,
    )

    # Create homogeneous patterns
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    # Call on_job_completed 9 times (should not trigger check)
    for _ in range(9):
        health.on_job_completed()

    assert health._completed_jobs_since_check == 9

    # 10th call should trigger check and reset counter
    # Don't patch - just verify counter resets (the task runs in background)
    health.on_job_completed()

    # Give asyncio a chance to schedule the task
    await asyncio.sleep(0.1)

    assert health._completed_jobs_since_check == 0, "Counter should reset"


@pytest.mark.asyncio
async def test_health_checker_check_entropy_and_respond_integration(
    mock_manager: MagicMock,
    mock_monitor: MagicMock,
    learning_store: GlobalLearningStore,
) -> None:
    """Test full integration of entropy check and response."""
    health = HealthChecker(
        mock_manager,
        mock_monitor,
        learning_store=learning_store,
    )

    # Create homogeneous patterns (entropy = 0)
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    # Run entropy check
    await health._check_entropy_and_respond()

    # Verify response was recorded
    last_response = learning_store.get_last_entropy_response("daemon-entropy-check")
    assert last_response is not None, "Entropy response should be recorded"
    assert last_response.entropy_at_trigger == 0.0
    assert last_response.threshold_used == 0.1
    assert last_response.budget_boosted is True

    # Verify exploration budget was increased
    budget = learning_store.get_exploration_budget("daemon-entropy-check")
    assert budget is not None
    assert budget.budget_value > 0, "Budget should be set"
    assert budget.adjustment_type == "boost"


def test_entropy_increases_after_exploration_injection(
    learning_store: GlobalLearningStore,
) -> None:
    """Test that entropy increases after exploration budget injection.

    This is a conceptual test - in practice, entropy increases when
    patterns are actually applied with the boosted exploration budget,
    not immediately after the boost.
    """
    # Create homogeneous patterns (entropy = 0)
    _create_homogeneous_patterns(learning_store, pattern_count=5, dominant_apps=100)

    initial_entropy = learning_store.calculate_pattern_entropy()
    assert initial_entropy.diversity_index == 0.0

    # Trigger entropy response
    from mozart.learning.store.budget import (
        EntropyResponseConfig,
        EntropyTriggerContext,
    )

    trigger = EntropyTriggerContext(
        job_hash="test-job",
        entropy_at_trigger=0.0,
        threshold_used=0.1,
    )
    config = EntropyResponseConfig(boost_budget=True, budget_boost_amount=0.15)

    learning_store.trigger_entropy_response(trigger=trigger, config=config)

    # Simulate new diverse applications after exploration injection
    # In real usage, the boosted budget would cause pattern selection to
    # choose diverse patterns, which then get applied and recorded
    pattern_ids = []
    for i in range(4):
        pattern_id = learning_store.record_pattern(
            pattern_type=PatternType.SEMANTIC_INSIGHT.value,
            pattern_name=f"explored_pattern_{i}",
            description=f"Pattern discovered via exploration {i}",
            context_tags=["test"],
        )
        pattern_ids.append(pattern_id)

        # Apply each pattern a few times
        for j in range(5):
            learning_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"test-execution-explore-{i}-{j}",
                pattern_led_to_success=True,
                grounding_confidence=0.7,
            )

    # Recalculate entropy after diverse applications
    new_entropy = learning_store.calculate_pattern_entropy()

    # Entropy should increase from 0.0 to > 0.0
    assert new_entropy.diversity_index > 0.0, "Entropy should increase after exploration"
    assert (
        new_entropy.effective_pattern_count > 1
    ), "Multiple patterns should now have applications"
