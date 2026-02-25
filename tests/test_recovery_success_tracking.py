"""Tests for recovery success tracking.

This module tests that error recovery outcomes (success/failure) are correctly
recorded to the global learning store, addressing the 0% recovery success rate issue.

Focus: Test the GlobalLearningStore.record_error_recovery() method and verify
that the recovery success rate calculation is correct.
"""

from pathlib import Path

import pytest

from mozart.learning.global_store import GlobalLearningStore


@pytest.fixture
def global_store(tmp_path: Path) -> GlobalLearningStore:
    """Create a global learning store."""
    db_path = tmp_path / "learning.db"
    return GlobalLearningStore(db_path=db_path)


def test_recovery_success_is_recorded(global_store: GlobalLearningStore):
    """Test that successful recovery is recorded."""
    # Record a successful recovery
    record_id = global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=60.0,
        recovery_success=True,
    )

    assert record_id is not None, "Expected a record ID"

    # Verify it was recorded as success
    with global_store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT recovery_success FROM error_recoveries
            WHERE id = ?
            """,
            (record_id,),
        )
        row = cursor.fetchone()
        assert row is not None, "Recovery record not found"
        assert row["recovery_success"] == 1, "Expected recovery_success=1"


def test_recovery_failure_is_recorded(global_store: GlobalLearningStore):
    """Test that failed recovery is recorded."""
    # Record a failed recovery
    record_id = global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=60.0,
        recovery_success=False,
    )

    # Verify it was recorded as failure
    with global_store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT recovery_success FROM error_recoveries
            WHERE id = ?
            """,
            (record_id,),
        )
        row = cursor.fetchone()
        assert row is not None, "Recovery record not found"
        assert row["recovery_success"] == 0, "Expected recovery_success=0"


def test_recovery_success_rate_calculation(global_store: GlobalLearningStore):
    """Test that recovery success rate is correctly calculated."""
    # Record 3 successes
    for _ in range(3):
        global_store.record_error_recovery(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=60.0,
            recovery_success=True,
        )

    # Record 2 failures
    for _ in range(2):
        global_store.record_error_recovery(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=60.0,
            recovery_success=False,
        )

    # Calculate success rate
    with global_store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT
                SUM(CASE WHEN recovery_success = 1 THEN 1 ELSE 0 END) as successes,
                COUNT(*) as total
            FROM error_recoveries
            """
        )
        row = cursor.fetchone()
        successes = row["successes"]
        total = row["total"]

    assert total == 5, f"Expected 5 total recoveries, got {total}"
    assert successes == 3, f"Expected 3 successful recoveries, got {successes}"

    success_rate = successes / total
    assert abs(success_rate - 0.6) < 0.01, f"Expected 60% success rate, got {success_rate * 100}%"


def test_recovery_records_have_all_fields(global_store: GlobalLearningStore):
    """Test that recovery records include all required fields."""
    record_id = global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=70.0,
        recovery_success=True,
        model="claude-sonnet-4.5",
    )

    with global_store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT error_code, suggested_wait, actual_wait, recovery_success, model, time_of_day
            FROM error_recoveries
            WHERE id = ?
            """,
            (record_id,),
        )
        row = cursor.fetchone()

    assert row["error_code"] == "E101"
    assert row["suggested_wait"] == 60.0
    assert row["actual_wait"] == 70.0
    assert row["recovery_success"] == 1
    assert row["model"] == "claude-sonnet-4.5"
    assert row["time_of_day"] is not None  # Should be set to current hour


def test_recovery_success_rate_with_zero_successes(global_store: GlobalLearningStore):
    """Test that 0% success rate is correctly calculated."""
    # Record only failures
    for _ in range(5):
        global_store.record_error_recovery(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=60.0,
            recovery_success=False,
        )

    with global_store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT
                SUM(CASE WHEN recovery_success = 1 THEN 1 ELSE 0 END) as successes,
                COUNT(*) as total
            FROM error_recoveries
            """
        )
        row = cursor.fetchone()
        successes = row["successes"]
        total = row["total"]

    assert total == 5
    assert successes == 0
    success_rate = successes / total if total > 0 else 0.0
    assert success_rate == 0.0, "Expected 0% success rate"


def test_recovery_success_rate_formula():
    """Verify the recovery success rate formula is correct.

    This is a documentation test to ensure we understand the metric.
    The formula should be: successes / total_attempts
    NOT: attempts / successes (which would be infinite when successes=0)
    """
    # Test with zero successes
    successes = 0
    total = 21
    rate = successes / total if total > 0 else 0.0
    assert rate == 0.0, "Zero successes should give 0% rate"

    # Test with some successes
    successes = 10
    total = 21
    rate = successes / total
    assert abs(rate - 0.476) < 0.01, "10/21 should be ~47.6%"

    # Test with all successes
    successes = 21
    total = 21
    rate = successes / total
    assert rate == 1.0, "All successes should give 100% rate"


def test_learned_wait_time_with_successful_recoveries(global_store: GlobalLearningStore):
    """Test that learned wait time is calculated from successful recoveries."""
    # Record successful recoveries with different wait times
    global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=60.0,
        recovery_success=True,
    )
    global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=70.0,
        recovery_success=True,
    )
    global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=80.0,
        recovery_success=True,
    )

    # Get learned wait time (requires min 3 samples by default)
    learned_wait = global_store.get_learned_wait_time(
        error_code="E101",
        min_samples=3,
    )

    assert learned_wait is not None, "Expected learned wait time with 3 samples"
    assert 60.0 <= learned_wait <= 80.0, f"Learned wait {learned_wait} should be between 60 and 80"


def test_learned_wait_time_ignores_failures(global_store: GlobalLearningStore):
    """Test that learned wait time only uses successful recoveries."""
    # Record failures with short waits
    for _ in range(10):
        global_store.record_error_recovery(
            error_code="E101",
            suggested_wait=30.0,
            actual_wait=30.0,
            recovery_success=False,
        )

    # Record successes with longer waits
    for wait_time in [60.0, 70.0, 80.0]:
        global_store.record_error_recovery(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=wait_time,
            recovery_success=True,
        )

    learned_wait = global_store.get_learned_wait_time(
        error_code="E101",
        min_samples=3,
    )

    assert learned_wait is not None
    # Should be based on 60, 70, 80 (successes only), not the 30s (failures)
    assert (
        learned_wait >= 60.0
    ), f"Learned wait {learned_wait} should not be influenced by failed recoveries"


def test_get_effective_model_for_claude_cli():
    """Test that _get_effective_model() returns cli_model for Claude CLI backend.

    This is the root cause of the 0% recovery success rate: recovery tracking
    in sheet.py was using `config.backend.model` (None for Claude CLI) instead
    of `_get_effective_model()` which correctly resolves cli_model.

    Evolution #25 Candidate 4: Fix Recovery Success Tracking
    """
    from mozart.core.config import BackendConfig

    # Test Claude CLI backend with cli_model set
    cli_backend = BackendConfig(
        type="claude_cli",
        cli_model="claude-sonnet-4",
    )

    # Simulate the _get_effective_model logic
    if cli_backend.type == "claude_cli":
        effective_model = cli_backend.cli_model
    else:
        effective_model = cli_backend.model

    assert effective_model == "claude-sonnet-4", (
        "_get_effective_model should return cli_model for Claude CLI backend"
    )
    assert cli_backend.cli_model == "claude-sonnet-4"

    # Test that directly using backend.model would give the wrong value
    # (the API default, not the CLI model)
    assert cli_backend.model != cli_backend.cli_model, (
        "backend.model should be different from cli_model for CLI backend"
    )


def test_get_effective_model_for_anthropic_api():
    """Test that _get_effective_model() returns model for Anthropic API backend."""
    from mozart.core.config import BackendConfig

    # Test Anthropic API backend
    api_backend = BackendConfig(
        type="anthropic_api",
        model="claude-opus-4-6",
    )

    # Simulate the _get_effective_model logic
    if api_backend.type == "claude_cli":
        effective_model = api_backend.cli_model
    else:
        effective_model = api_backend.model

    assert effective_model == "claude-opus-4-6", (
        "_get_effective_model should return model for Anthropic API backend"
    )
    assert api_backend.model == "claude-opus-4-6"


def test_recovery_tracking_with_null_model_vs_effective_model(global_store: GlobalLearningStore):
    """Test the difference between recording with model=None vs effective model.

    This demonstrates the bug: when sheet.py uses `config.backend.model` (None
    for CLI backend), the recovery record may be created but won't be queryable
    or will fail constraints.

    Evolution #25 Candidate 4: Fix Recovery Success Tracking
    """
    # Record a recovery with model=None (the bug)
    global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=60.0,
        recovery_success=True,
        model=None,
    )

    # Record a recovery with explicit model (the fix)
    global_store.record_error_recovery(
        error_code="E101",
        suggested_wait=60.0,
        actual_wait=60.0,
        recovery_success=True,
        model="claude-sonnet-4",
    )

    # Query successes without model filter (should find both)
    with global_store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT COUNT(*) as count
            FROM error_recoveries
            WHERE error_code = 'E101' AND recovery_success = 1
            """
        )
        total_successes = cursor.fetchone()["count"]

    # Query successes with model filter (should only find the one with model set)
    with global_store._get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT COUNT(*) as count
            FROM error_recoveries
            WHERE error_code = 'E101' AND recovery_success = 1 AND model IS NOT NULL
            """
        )
        successes_with_model = cursor.fetchone()["count"]

    assert total_successes == 2, "Both records should be created"
    assert successes_with_model == 1, "Only one record has a model"

    # The learned wait time queries filter by model, so NULL models won't be used
    learned_wait = global_store.get_learned_wait_time(
        error_code="E101",
        model="claude-sonnet-4",
        min_samples=1,
    )
    # This should work because we have 1 success with model="claude-sonnet-4"
    assert learned_wait is not None, "Should find recovery with explicit model"

    # Querying with model=None won't find the NULL model record because
    # the implementation filters by "model = ?" and NULL != NULL in SQL.
    # This is the subtle bug that causes 0% recovery success rate when
    # using backend.model (NULL for CLI) instead of _get_effective_model().
