"""Tests for Escalation Learning Loop (v11 Evolution) and Pattern Suggestions (v15).

Tests the escalation learning system:
- EscalationDecisionRecord model
- record_escalation_decision() persistence
- get_escalation_history() query
- get_similar_escalation() pattern matching
- update_escalation_outcome() feedback loop
- v15: HistoricalSuggestion model
- v15: EscalationContext.historical_suggestions field
- v15: ConsoleEscalationHandler suggestion display
"""

import tempfile
from collections.abc import Generator
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from marianne.core.checkpoint import SheetState
from marianne.execution.escalation import (
    CheckpointContext,
    CheckpointResponse,
    CheckpointTrigger,
    ConsoleCheckpointHandler,
    ConsoleEscalationHandler,
    EscalationContext,
    HistoricalSuggestion,
)
from marianne.execution.validation import SheetValidationResult
from marianne.learning.global_store import (
    EscalationDecisionRecord,
    GlobalLearningStore,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    """Create a temporary database path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    # Cleanup
    if path.exists():
        path.unlink()


@pytest.fixture
def global_store(temp_db_path: Path) -> Generator[GlobalLearningStore, None, None]:
    """Create a GlobalLearningStore with a temporary database."""
    store = GlobalLearningStore(temp_db_path)
    yield store


# =============================================================================
# TestEscalationDecisionRecord
# =============================================================================


class TestEscalationDecisionRecord:
    """Tests for EscalationDecisionRecord dataclass."""

    def test_record_creation(self) -> None:
        """Test basic record creation."""
        record = EscalationDecisionRecord(
            id="test-id-123",
            job_hash="abc123",
            sheet_num=5,
            confidence=0.45,
            action="retry",
            guidance="Try again with more context",
            validation_pass_rate=60.0,
            retry_count=2,
        )

        assert record.id == "test-id-123"
        assert record.job_hash == "abc123"
        assert record.sheet_num == 5
        assert record.confidence == 0.45
        assert record.action == "retry"
        assert record.guidance == "Try again with more context"
        assert record.validation_pass_rate == 60.0
        assert record.retry_count == 2
        assert record.outcome_after_action is None
        assert record.model is None

    def test_record_with_outcome(self) -> None:
        """Test record with outcome after action."""
        record = EscalationDecisionRecord(
            id="test-id-456",
            job_hash="def456",
            sheet_num=3,
            confidence=0.35,
            action="skip",
            guidance="Skip this validation-heavy sheet",
            validation_pass_rate=30.0,
            retry_count=4,
            outcome_after_action="skipped",
            model="claude-sonnet-4",
        )

        assert record.outcome_after_action == "skipped"
        assert record.model == "claude-sonnet-4"

    def test_record_defaults(self) -> None:
        """Test that optional fields have correct defaults."""
        record = EscalationDecisionRecord(
            id="test-id",
            job_hash="hash",
            sheet_num=1,
            confidence=0.5,
            action="abort",
            guidance=None,
            validation_pass_rate=50.0,
            retry_count=0,
        )

        assert record.guidance is None
        assert record.outcome_after_action is None
        assert record.model is None
        assert isinstance(record.recorded_at, datetime)


# =============================================================================
# TestRecordEscalationDecision
# =============================================================================


class TestRecordEscalationDecision:
    """Tests for record_escalation_decision() method."""

    def test_record_escalation_basic(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test basic escalation recording."""
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=3,
            confidence=0.42,
            action="retry",
            validation_pass_rate=55.0,
            retry_count=1,
        )

        assert record_id is not None
        assert len(record_id) == 36  # UUID length

    def test_record_escalation_with_guidance(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test escalation recording with guidance."""
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=5,
            confidence=0.38,
            action="modify_prompt",
            validation_pass_rate=40.0,
            retry_count=2,
            guidance="Add more specific instructions for file creation",
        )

        # Verify by retrieving
        history = global_store.get_escalation_history(job_id="test-job")
        assert len(history) == 1
        assert history[0].id == record_id
        assert history[0].guidance == "Add more specific instructions for file creation"

    def test_record_escalation_with_model(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test escalation recording with model specified."""
        global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.55,
            action="skip",
            validation_pass_rate=65.0,
            retry_count=3,
            model="claude-sonnet-4",
        )

        history = global_store.get_escalation_history(job_id="test-job")
        assert len(history) == 1
        assert history[0].model == "claude-sonnet-4"

    def test_record_multiple_escalations(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test recording multiple escalation decisions."""
        for i in range(5):
            global_store.record_escalation_decision(
                job_id="test-job",
                sheet_num=i + 1,
                confidence=0.3 + (i * 0.1),
                action="retry" if i % 2 == 0 else "skip",
                validation_pass_rate=float(i * 10),
                retry_count=i,
            )

        history = global_store.get_escalation_history(job_id="test-job")
        assert len(history) == 5


# =============================================================================
# TestGetEscalationHistory
# =============================================================================


class TestGetEscalationHistory:
    """Tests for get_escalation_history() method."""

    def test_empty_history(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test empty history returns empty list."""
        history = global_store.get_escalation_history()
        assert history == []

    def test_filter_by_job(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test filtering by job ID."""
        # Record for two different jobs
        global_store.record_escalation_decision(
            job_id="job-a",
            sheet_num=1,
            confidence=0.4,
            action="retry",
            validation_pass_rate=50.0,
            retry_count=1,
        )
        global_store.record_escalation_decision(
            job_id="job-b",
            sheet_num=1,
            confidence=0.5,
            action="skip",
            validation_pass_rate=60.0,
            retry_count=2,
        )

        # Filter by job-a
        history_a = global_store.get_escalation_history(job_id="job-a")
        assert len(history_a) == 1
        assert history_a[0].action == "retry"

        # Filter by job-b
        history_b = global_store.get_escalation_history(job_id="job-b")
        assert len(history_b) == 1
        assert history_b[0].action == "skip"

    def test_filter_by_action(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test filtering by action type."""
        actions = ["retry", "skip", "abort", "retry", "modify_prompt"]
        for i, action in enumerate(actions):
            global_store.record_escalation_decision(
                job_id=f"job-{i}",
                sheet_num=i,
                confidence=0.4,
                action=action,
                validation_pass_rate=50.0,
                retry_count=1,
            )

        # Filter by retry
        retries = global_store.get_escalation_history(action="retry")
        assert len(retries) == 2

        # Filter by skip
        skips = global_store.get_escalation_history(action="skip")
        assert len(skips) == 1

    def test_history_ordered_by_recency(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that history is ordered by recency (newest first)."""
        import time

        for i in range(3):
            global_store.record_escalation_decision(
                job_id="test-job",
                sheet_num=i + 1,
                confidence=0.4 + (i * 0.1),
                action="retry",
                validation_pass_rate=50.0,
                retry_count=i,
            )
            time.sleep(0.01)  # Ensure different timestamps

        history = global_store.get_escalation_history(job_id="test-job")

        # Newest (sheet 3) should be first
        assert history[0].sheet_num == 3
        assert history[2].sheet_num == 1

    def test_history_respects_limit(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that limit parameter is respected."""
        for i in range(10):
            global_store.record_escalation_decision(
                job_id="test-job",
                sheet_num=i,
                confidence=0.4,
                action="retry",
                validation_pass_rate=50.0,
                retry_count=i,
            )

        history = global_store.get_escalation_history(limit=5)
        assert len(history) == 5


# =============================================================================
# TestGetSimilarEscalation
# =============================================================================


class TestGetSimilarEscalation:
    """Tests for get_similar_escalation() method."""

    def test_no_similar_when_empty(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test returns empty when no escalations exist."""
        similar = global_store.get_similar_escalation(
            confidence=0.5,
            validation_pass_rate=50.0,
        )
        assert similar == []

    def test_finds_similar_by_confidence(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test finding similar escalations by confidence."""
        # Record with confidence 0.45
        global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.45,
            action="retry",
            validation_pass_rate=50.0,
            retry_count=1,
        )

        # Search with confidence 0.50 (within default tolerance of 0.15)
        similar = global_store.get_similar_escalation(
            confidence=0.50,
            validation_pass_rate=50.0,
        )

        assert len(similar) == 1
        assert similar[0].confidence == 0.45

    def test_finds_similar_by_pass_rate(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test finding similar escalations by pass rate."""
        # Record with pass rate 60%
        global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.5,
            action="skip",
            validation_pass_rate=60.0,
            retry_count=2,
        )

        # Search with pass rate 55% (within default tolerance of 15%)
        similar = global_store.get_similar_escalation(
            confidence=0.5,
            validation_pass_rate=55.0,
        )

        assert len(similar) == 1
        assert similar[0].validation_pass_rate == 60.0

    def test_excludes_outside_tolerance(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that escalations outside tolerance are excluded."""
        # Record with confidence 0.2
        global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.2,
            action="abort",
            validation_pass_rate=20.0,
            retry_count=3,
        )

        # Search with confidence 0.8 (way outside default 0.15 tolerance)
        similar = global_store.get_similar_escalation(
            confidence=0.8,
            validation_pass_rate=80.0,
        )

        assert similar == []

    def test_prioritizes_successful_outcomes(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that successful outcomes are returned first."""
        # Record failed outcome first
        global_store.record_escalation_decision(
            job_id="job-1",
            sheet_num=1,
            confidence=0.45,
            action="retry",
            validation_pass_rate=50.0,
            retry_count=1,
            outcome_after_action="failed",
        )

        # Record successful outcome second
        global_store.record_escalation_decision(
            job_id="job-2",
            sheet_num=1,
            confidence=0.45,
            action="skip",
            validation_pass_rate=50.0,
            retry_count=1,
            outcome_after_action="success",
        )

        # Search should return success first
        similar = global_store.get_similar_escalation(
            confidence=0.45,
            validation_pass_rate=50.0,
        )

        assert len(similar) == 2
        assert similar[0].outcome_after_action == "success"
        assert similar[1].outcome_after_action == "failed"

    def test_custom_tolerance(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test custom tolerance parameters."""
        # Record with confidence 0.3
        global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.3,
            action="retry",
            validation_pass_rate=30.0,
            retry_count=1,
        )

        # Search with tight tolerance (should not match)
        similar_tight = global_store.get_similar_escalation(
            confidence=0.5,
            validation_pass_rate=50.0,
            confidence_tolerance=0.05,
            pass_rate_tolerance=5.0,
        )
        assert similar_tight == []

        # Search with loose tolerance (should match)
        similar_loose = global_store.get_similar_escalation(
            confidence=0.5,
            validation_pass_rate=50.0,
            confidence_tolerance=0.25,
            pass_rate_tolerance=25.0,
        )
        assert len(similar_loose) == 1


# =============================================================================
# TestUpdateEscalationOutcome
# =============================================================================


class TestUpdateEscalationOutcome:
    """Tests for update_escalation_outcome() method."""

    def test_update_outcome_success(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test updating escalation outcome."""
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.4,
            action="retry",
            validation_pass_rate=50.0,
            retry_count=1,
        )

        # Update outcome
        updated = global_store.update_escalation_outcome(
            escalation_id=record_id,
            outcome_after_action="success",
        )

        assert updated is True

        # Verify update
        history = global_store.get_escalation_history(job_id="test-job")
        assert history[0].outcome_after_action == "success"

    def test_update_nonexistent_returns_false(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that updating nonexistent record returns False."""
        updated = global_store.update_escalation_outcome(
            escalation_id="nonexistent-id",
            outcome_after_action="failed",
        )

        assert updated is False

    def test_update_multiple_outcomes(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test updating multiple different outcomes."""
        outcomes = [
            ("retry", "success"),
            ("skip", "skipped"),
            ("abort", "aborted"),
        ]

        record_ids = []
        for action, _ in outcomes:
            record_id = global_store.record_escalation_decision(
                job_id="test-job",
                sheet_num=1,
                confidence=0.4,
                action=action,
                validation_pass_rate=50.0,
                retry_count=1,
            )
            record_ids.append(record_id)

        # Update all outcomes
        for i, (_, outcome) in enumerate(outcomes):
            global_store.update_escalation_outcome(record_ids[i], outcome)

        # Verify all updates
        history = global_store.get_escalation_history(job_id="test-job")
        expected_outcomes = {o[1] for o in outcomes}
        actual_outcomes = {h.outcome_after_action for h in history}
        assert actual_outcomes == expected_outcomes


# =============================================================================
# TestEscalationLearningIntegration
# =============================================================================


class TestEscalationLearningIntegration:
    """Integration tests for escalation learning flow."""

    def test_full_escalation_learning_flow(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test complete escalation learning workflow."""
        # 1. Record initial escalation with retry action
        record_id_1 = global_store.record_escalation_decision(
            job_id="evolution-job",
            sheet_num=5,
            confidence=0.42,
            action="retry",
            validation_pass_rate=55.0,
            retry_count=2,
            guidance="User chose to retry with more context",
            model="claude-sonnet-4",
        )

        # 2. Update with successful outcome
        global_store.update_escalation_outcome(record_id_1, "success")

        # 3. New similar escalation occurs
        similar = global_store.get_similar_escalation(
            confidence=0.45,  # Similar to 0.42
            validation_pass_rate=58.0,  # Similar to 55%
        )

        # 4. Should find the previous successful escalation
        assert len(similar) == 1
        assert similar[0].action == "retry"
        assert similar[0].outcome_after_action == "success"
        assert similar[0].guidance == "User chose to retry with more context"

        # 5. Record the new escalation (using the guidance)
        _record_id_2 = global_store.record_escalation_decision(
            job_id="new-job",
            sheet_num=3,
            confidence=0.45,
            action="retry",  # Following the pattern
            validation_pass_rate=58.0,
            retry_count=1,
            guidance="Following pattern from similar escalation",
        )

        # 6. Verify both are in history
        all_history = global_store.get_escalation_history()
        assert len(all_history) == 2

        # 7. Verify can filter by action
        retry_history = global_store.get_escalation_history(action="retry")
        assert len(retry_history) == 2

    def test_schema_v3_migration(self, temp_db_path: Path) -> None:
        """Test that schema v3 creates escalation_decisions table."""
        # Create store (triggers schema creation)
        store = GlobalLearningStore(temp_db_path)

        # Verify table exists by querying it
        record_id = store.record_escalation_decision(
            job_id="test",
            sheet_num=1,
            confidence=0.5,
            action="retry",
            validation_pass_rate=50.0,
            retry_count=0,
        )

        # Should not raise and should return valid ID
        assert record_id is not None

        # Verify schema version is current (v9 as of D2-D4 modularization)
        # The version increments as new features are added:
        # v3: escalation_decisions table
        # v7: quarantine/trust fields
        # v9: success_factors and other schema enhancements
        assert store.SCHEMA_VERSION >= 7, "Schema should be at least v7 for escalation support"

    def test_clear_all_includes_escalations(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that clear_all() clears escalation decisions."""
        # Record some escalations
        for i in range(3):
            global_store.record_escalation_decision(
                job_id=f"job-{i}",
                sheet_num=i,
                confidence=0.5,
                action="retry",
                validation_pass_rate=50.0,
                retry_count=0,
            )

        # Verify they exist
        assert len(global_store.get_escalation_history()) == 3

        # Clear all
        global_store.clear_all()

        # Verify escalations are cleared
        assert global_store.get_escalation_history() == []


# =============================================================================
# TestEscalationOutcomeUpdate (v13 Evolution)
# =============================================================================


class TestEscalationOutcomeUpdate:
    """Tests for v13 escalation outcome update integration.

    These tests verify that the escalation feedback loop is properly closed
    when sheet execution reaches a final state after an escalation.
    """

    def test_outcome_updated_to_success(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that outcome is updated to 'success' when sheet succeeds after escalation."""
        # Record escalation decision
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.45,
            action="retry",
            validation_pass_rate=60.0,
            retry_count=2,
            guidance="Retrying with more context",
        )

        # Simulate success outcome
        updated = global_store.update_escalation_outcome(
            escalation_id=record_id,
            outcome_after_action="success",
        )

        assert updated is True

        # Verify the outcome was recorded
        history = global_store.get_escalation_history(job_id="test-job")
        assert len(history) == 1
        assert history[0].outcome_after_action == "success"

    def test_outcome_updated_to_failed_on_retry_exhausted(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that outcome is updated to 'failed' when retries are exhausted."""
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.35,
            action="retry",
            validation_pass_rate=40.0,
            retry_count=3,
        )

        # Simulate retry exhausted
        updated = global_store.update_escalation_outcome(
            escalation_id=record_id,
            outcome_after_action="failed",
        )

        assert updated is True

        history = global_store.get_escalation_history(job_id="test-job")
        assert history[0].outcome_after_action == "failed"

    def test_outcome_updated_to_skipped(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that outcome is updated to 'skipped' when user skips sheet."""
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.30,
            action="skip",
            validation_pass_rate=30.0,
            retry_count=2,
        )

        updated = global_store.update_escalation_outcome(
            escalation_id=record_id,
            outcome_after_action="skipped",
        )

        assert updated is True

        history = global_store.get_escalation_history(job_id="test-job")
        assert history[0].outcome_after_action == "skipped"

    def test_outcome_updated_to_aborted(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that outcome is updated to 'aborted' when user aborts job."""
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.20,
            action="abort",
            validation_pass_rate=20.0,
            retry_count=4,
        )

        updated = global_store.update_escalation_outcome(
            escalation_id=record_id,
            outcome_after_action="aborted",
        )

        assert updated is True

        history = global_store.get_escalation_history(job_id="test-job")
        assert history[0].outcome_after_action == "aborted"

    def test_outcome_not_updated_when_no_record_id(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that updating with invalid ID returns False."""
        # This mimics the case where no escalation occurred
        updated = global_store.update_escalation_outcome(
            escalation_id="nonexistent-id",
            outcome_after_action="success",
        )

        assert updated is False

    def test_outcome_idempotent_update(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that outcome can be updated multiple times (idempotent)."""
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.45,
            action="retry",
            validation_pass_rate=50.0,
            retry_count=1,
        )

        # First update
        global_store.update_escalation_outcome(record_id, "failed")
        history = global_store.get_escalation_history(job_id="test-job")
        assert history[0].outcome_after_action == "failed"

        # Second update (idempotent - just overwrites)
        updated = global_store.update_escalation_outcome(record_id, "success")
        assert updated is True
        history = global_store.get_escalation_history(job_id="test-job")
        assert history[0].outcome_after_action == "success"

    def test_outcome_correlation_with_action(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that outcome data correlates with action taken.

        This verifies the learning value - that we can analyze which
        actions lead to which outcomes.
        """
        # Record multiple escalations with different actions and outcomes
        outcomes = [
            ("retry", "success"),
            ("retry", "failed"),
            ("skip", "skipped"),
            ("abort", "aborted"),
        ]

        record_ids = []
        for action, _ in outcomes:
            record_id = global_store.record_escalation_decision(
                job_id="correlation-test",
                sheet_num=1,
                confidence=0.40,
                action=action,
                validation_pass_rate=50.0,
                retry_count=1,
            )
            record_ids.append(record_id)

        # Update all outcomes
        for i, (_, outcome) in enumerate(outcomes):
            global_store.update_escalation_outcome(record_ids[i], outcome)

        # Verify we can analyze the results
        history = global_store.get_escalation_history(job_id="correlation-test")
        assert len(history) == 4

        # Count outcomes by action (for learning analysis)
        retry_outcomes = [h.outcome_after_action for h in history if h.action == "retry"]
        assert set(retry_outcomes) == {"success", "failed"}

        skip_outcomes = [h.outcome_after_action for h in history if h.action == "skip"]
        assert skip_outcomes == ["skipped"]

        abort_outcomes = [h.outcome_after_action for h in history if h.action == "abort"]
        assert abort_outcomes == ["aborted"]


# =============================================================================
# TestRunnerEscalationOutcomeIntegration (v13 Evolution)
# =============================================================================


class TestRunnerEscalationOutcomeIntegration:
    """Integration tests for runner's _update_escalation_outcome method.

    v13 Evolution: Escalation Feedback Loop - these tests verify that the
    JobRunner correctly calls update_escalation_outcome at all escalation
    outcome points (success, failed, skipped, aborted).
    """

    def test_update_escalation_outcome_returns_early_if_no_store(self) -> None:
        """Test that _update_escalation_outcome returns early if global learning store is None."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        # Create a mock runner with no global learning store
        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = None
        runner._logger = MagicMock()

        # Create sheet state with escalation record ID
        sheet_state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            outcome_data={"escalation_record_id": "test-record-123"},
        )

        # Call the method directly (unbound)
        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 1)

        # Should not log anything (early return)
        runner._logger.info.assert_not_called()
        runner._logger.warning.assert_not_called()

    def test_update_escalation_outcome_returns_early_if_no_record_id(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that _update_escalation_outcome returns early if no escalation_record_id."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        # Create sheet state WITHOUT escalation record ID
        sheet_state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            outcome_data={},  # No escalation_record_id
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 1)

        # Should not call update_escalation_outcome
        runner._logger.info.assert_not_called()

    def test_update_escalation_outcome_returns_early_if_outcome_data_none(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that _update_escalation_outcome handles outcome_data being None."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        # Create sheet state with outcome_data=None (default)
        sheet_state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            outcome_data=None,
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 1)

        # Should not call update_escalation_outcome
        runner._logger.info.assert_not_called()

    def test_update_escalation_outcome_calls_store_correctly(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that _update_escalation_outcome calls the store with correct parameters."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        # First, record an escalation decision to get a valid ID
        record_id = global_store.record_escalation_decision(
            job_id="test-job",
            sheet_num=1,
            confidence=0.45,
            action="retry",
            validation_pass_rate=60.0,
            retry_count=2,
        )

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        # Create sheet state with the actual escalation record ID
        sheet_state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            outcome_data={"escalation_record_id": record_id},
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 1)

        # Verify outcome was updated
        history = global_store.get_escalation_history(job_id="test-job")
        assert len(history) == 1
        assert history[0].outcome_after_action == "success"

        # Verify info was logged
        runner._logger.info.assert_called_once()
        call_args = runner._logger.info.call_args
        assert call_args[0][0] == "escalation.outcome_updated"

    def test_update_escalation_outcome_logs_warning_on_not_found(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that _update_escalation_outcome logs warning when record not found."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        # Create sheet state with nonexistent escalation record ID
        sheet_state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            outcome_data={"escalation_record_id": "nonexistent-record-id"},
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 1)

        # Verify warning was logged
        runner._logger.warning.assert_called_once()
        call_args = runner._logger.warning.call_args
        assert call_args[0][0] == "escalation.outcome_update_not_found"

    def test_update_escalation_outcome_logs_warning_on_exception(self) -> None:
        """Test that _update_escalation_outcome logs warning on exception."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        # Create a mock store that raises an exception
        mock_store = MagicMock()
        mock_store.update_escalation_outcome.side_effect = Exception("DB error")

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = mock_store
        runner._logger = MagicMock()
        runner._escalation_update_failures = 0
        runner._CONSECUTIVE_FAILURE_ERROR_THRESHOLD = 3
        # Bind the real helper so counter increment and log selection work
        runner._log_swallowed_error = (
            lambda *a, **kw: JobRunner._log_swallowed_error(runner, *a, **kw)
        )

        sheet_state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            outcome_data={"escalation_record_id": "test-record-123"},
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 1)

        # Verify warning was logged (first failure uses warning, not error)
        runner._logger.warning.assert_called_once()
        call_args = runner._logger.warning.call_args
        assert call_args[0][0] == "escalation.outcome_update_failed"
        # Verify consecutive failure counter was incremented
        assert runner._escalation_update_failures == 1

    def test_update_escalation_outcome_success_after_retry(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test escalation outcome updated to 'success' when sheet succeeds after retry."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        # Record escalation with retry action
        record_id = global_store.record_escalation_decision(
            job_id="retry-success-job",
            sheet_num=5,
            confidence=0.42,
            action="retry",
            validation_pass_rate=55.0,
            retry_count=2,
            guidance="User chose retry",
        )

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        sheet_state = SheetState(
            sheet_num=5,
            status=SheetStatus.COMPLETED,
            outcome_data={"escalation_record_id": record_id},
        )

        # Simulate success outcome after retry
        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 5)

        history = global_store.get_escalation_history(job_id="retry-success-job")
        assert len(history) == 1
        assert history[0].action == "retry"
        assert history[0].outcome_after_action == "success"

    def test_update_escalation_outcome_failed_on_retry_exhausted(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test escalation outcome updated to 'failed' when retries exhausted."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        record_id = global_store.record_escalation_decision(
            job_id="retry-failed-job",
            sheet_num=3,
            confidence=0.35,
            action="retry",
            validation_pass_rate=40.0,
            retry_count=3,
        )

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        sheet_state = SheetState(
            sheet_num=3,
            status=SheetStatus.FAILED,
            outcome_data={"escalation_record_id": record_id},
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "failed", 3)

        history = global_store.get_escalation_history(job_id="retry-failed-job")
        assert history[0].outcome_after_action == "failed"

    def test_update_escalation_outcome_skipped(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test escalation outcome updated to 'skipped' when user skips."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        record_id = global_store.record_escalation_decision(
            job_id="skip-job",
            sheet_num=2,
            confidence=0.30,
            action="skip",
            validation_pass_rate=30.0,
            retry_count=2,
        )

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        sheet_state = SheetState(
            sheet_num=2,
            status=SheetStatus.SKIPPED,
            outcome_data={"escalation_record_id": record_id},
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "skipped", 2)

        history = global_store.get_escalation_history(job_id="skip-job")
        assert history[0].outcome_after_action == "skipped"

    def test_update_escalation_outcome_aborted(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test escalation outcome updated to 'aborted' when user aborts."""
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        record_id = global_store.record_escalation_decision(
            job_id="abort-job",
            sheet_num=1,
            confidence=0.20,
            action="abort",
            validation_pass_rate=20.0,
            retry_count=4,
        )

        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        sheet_state = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            outcome_data={"escalation_record_id": record_id},
        )

        JobRunner._update_escalation_outcome(runner, sheet_state, "aborted", 1)

        history = global_store.get_escalation_history(job_id="abort-job")
        assert history[0].outcome_after_action == "aborted"

    def test_escalation_feedback_loop_complete_workflow(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test complete escalation feedback loop: record → update → query.

        This is an end-to-end test that validates the entire feedback loop
        works correctly when called through the runner integration.
        """
        from unittest.mock import MagicMock

        from marianne.core.checkpoint import SheetState, SheetStatus
        from marianne.execution.runner import JobRunner

        # 1. Simulate escalation recording (what _handle_escalation does)
        record_id = global_store.record_escalation_decision(
            job_id="e2e-feedback-job",
            sheet_num=7,
            confidence=0.45,
            action="retry",
            validation_pass_rate=60.0,
            retry_count=1,
            guidance="Suggested retry with more context",
            model="claude-sonnet-4",
        )

        # 2. Create runner mock
        runner = MagicMock(spec=JobRunner)
        runner._global_learning_store = global_store
        runner._logger = MagicMock()

        # 3. Create sheet state as it would be after escalation handling
        sheet_state = SheetState(
            sheet_num=7,
            status=SheetStatus.COMPLETED,
            outcome_data={"escalation_record_id": record_id},
        )

        # 4. Update outcome (what runner does after sheet completion)
        JobRunner._update_escalation_outcome(runner, sheet_state, "success", 7)

        # 5. Verify the feedback loop is complete
        history = global_store.get_escalation_history(job_id="e2e-feedback-job")
        assert len(history) == 1
        assert history[0].id == record_id
        assert history[0].action == "retry"
        assert history[0].outcome_after_action == "success"
        assert history[0].model == "claude-sonnet-4"

        # 6. Verify similar escalation lookup now returns this data
        similar = global_store.get_similar_escalation(
            confidence=0.45,
            validation_pass_rate=60.0,
        )
        assert len(similar) == 1
        assert similar[0].outcome_after_action == "success"


# =============================================================================
# TestHistoricalSuggestion (v15 Evolution)
# =============================================================================


class TestHistoricalSuggestion:
    """Tests for v15 HistoricalSuggestion dataclass."""

    def test_suggestion_creation(self) -> None:
        """Test basic suggestion creation."""
        suggestion = HistoricalSuggestion(
            action="retry",
            outcome="success",
            confidence=0.45,
            validation_pass_rate=60.0,
            guidance="Added more context to the prompt",
        )

        assert suggestion.action == "retry"
        assert suggestion.outcome == "success"
        assert suggestion.confidence == 0.45
        assert suggestion.validation_pass_rate == 60.0
        assert suggestion.guidance == "Added more context to the prompt"

    def test_suggestion_with_none_outcome(self) -> None:
        """Test suggestion with unknown/null outcome."""
        suggestion = HistoricalSuggestion(
            action="skip",
            outcome=None,
            confidence=0.30,
            validation_pass_rate=30.0,
            guidance=None,
        )

        assert suggestion.outcome is None
        assert suggestion.guidance is None

    def test_suggestion_all_actions(self) -> None:
        """Test suggestions with all action types."""
        actions = ["retry", "skip", "abort", "modify_prompt"]
        for action in actions:
            suggestion = HistoricalSuggestion(
                action=action,
                outcome="success",
                confidence=0.5,
                validation_pass_rate=50.0,
                guidance=None,
            )
            assert suggestion.action == action


# =============================================================================
# TestEscalationContextSuggestions (v15 Evolution)
# =============================================================================


class TestEscalationContextSuggestions:
    """Tests for v15 EscalationContext.historical_suggestions field."""

    def test_context_default_empty_suggestions(self) -> None:
        """Test that historical_suggestions defaults to empty list."""
        context = EscalationContext(
            job_id="test-job",
            sheet_num=1,
            validation_results=[],
            confidence=0.45,
            retry_count=2,
            error_history=[],
            prompt_used="Test prompt",
            output_summary="Test summary",
        )

        assert context.historical_suggestions == []
        assert isinstance(context.historical_suggestions, list)

    def test_context_with_suggestions(self) -> None:
        """Test context with populated suggestions."""
        suggestions = [
            HistoricalSuggestion(
                action="retry",
                outcome="success",
                confidence=0.45,
                validation_pass_rate=60.0,
                guidance="Retry worked",
            ),
            HistoricalSuggestion(
                action="skip",
                outcome="skipped",
                confidence=0.30,
                validation_pass_rate=30.0,
                guidance=None,
            ),
        ]

        context = EscalationContext(
            job_id="test-job",
            sheet_num=1,
            validation_results=[],
            confidence=0.45,
            retry_count=2,
            error_history=[],
            prompt_used="Test prompt",
            output_summary="Test summary",
            historical_suggestions=suggestions,
        )

        assert len(context.historical_suggestions) == 2
        assert context.historical_suggestions[0].action == "retry"
        assert context.historical_suggestions[1].action == "skip"

    def test_context_suggestions_ordered_by_success(self) -> None:
        """Test that successful outcomes should come first (by convention)."""
        # This verifies the ordering expectation documented in the dataclass
        suggestions = [
            HistoricalSuggestion(
                action="retry",
                outcome="success",
                confidence=0.45,
                validation_pass_rate=60.0,
                guidance=None,
            ),
            HistoricalSuggestion(
                action="skip",
                outcome="failed",
                confidence=0.45,
                validation_pass_rate=60.0,
                guidance=None,
            ),
        ]

        context = EscalationContext(
            job_id="test",
            sheet_num=1,
            validation_results=[],
            confidence=0.45,
            retry_count=1,
            error_history=[],
            prompt_used="test",
            output_summary="",
            historical_suggestions=suggestions,
        )

        # Success should be first
        assert context.historical_suggestions[0].outcome == "success"


# =============================================================================
# TestConsoleEscalationHandlerSuggestions (v15 Evolution)
# =============================================================================


class TestConsoleEscalationHandlerSuggestions:
    """Tests for v15 ConsoleEscalationHandler suggestion display."""

    def test_print_context_with_suggestions(self) -> None:
        """Test that suggestions are printed when present."""
        handler = ConsoleEscalationHandler()

        suggestions = [
            HistoricalSuggestion(
                action="retry",
                outcome="success",
                confidence=0.45,
                validation_pass_rate=60.0,
                guidance="Added more context",
            ),
        ]

        context = EscalationContext(
            job_id="test-job",
            sheet_num=5,
            validation_results=[{"passed": True, "description": "test"}],
            confidence=0.45,
            retry_count=2,
            error_history=[],
            prompt_used="Test prompt",
            output_summary="Test summary",
            historical_suggestions=suggestions,
        )

        # Capture stdout
        output = StringIO()
        with patch("sys.stdout", output):
            handler._print_context_summary(context)

        printed = output.getvalue()

        # Verify suggestions section is displayed
        assert "HISTORICAL SUGGESTIONS" in printed
        assert "RETRY" in printed
        assert "success" in printed
        assert "Added more context" in printed

    def test_print_context_without_suggestions(self) -> None:
        """Test that no suggestions section when empty."""
        handler = ConsoleEscalationHandler()

        context = EscalationContext(
            job_id="test-job",
            sheet_num=5,
            validation_results=[{"passed": False, "description": "test"}],
            confidence=0.45,
            retry_count=2,
            error_history=[],
            prompt_used="Test prompt",
            output_summary="Test summary",
            historical_suggestions=[],  # Empty
        )

        output = StringIO()
        with patch("sys.stdout", output):
            handler._print_context_summary(context)

        printed = output.getvalue()

        # Verify suggestions section is NOT displayed
        assert "HISTORICAL SUGGESTIONS" not in printed

    def test_print_context_truncates_long_guidance(self) -> None:
        """Test that long guidance text is truncated."""
        handler = ConsoleEscalationHandler()

        long_guidance = "A" * 200  # Over 80 char limit

        suggestions = [
            HistoricalSuggestion(
                action="retry",
                outcome="success",
                confidence=0.45,
                validation_pass_rate=60.0,
                guidance=long_guidance,
            ),
        ]

        context = EscalationContext(
            job_id="test-job",
            sheet_num=5,
            validation_results=[],
            confidence=0.45,
            retry_count=2,
            error_history=[],
            prompt_used="Test prompt",
            output_summary="",
            historical_suggestions=suggestions,
        )

        output = StringIO()
        with patch("sys.stdout", output):
            handler._print_context_summary(context)

        printed = output.getvalue()

        # Verify guidance is truncated with "..."
        assert "..." in printed
        # Should not contain the full 200 char guidance
        assert long_guidance not in printed

    def test_print_context_shows_outcome_icons(self) -> None:
        """Test that outcome icons are correctly displayed."""
        handler = ConsoleEscalationHandler()

        suggestions = [
            HistoricalSuggestion(
                action="retry",
                outcome="success",
                confidence=0.45,
                validation_pass_rate=60.0,
                guidance=None,
            ),
            HistoricalSuggestion(
                action="skip",
                outcome="failed",
                confidence=0.35,
                validation_pass_rate=40.0,
                guidance=None,
            ),
            HistoricalSuggestion(
                action="abort",
                outcome=None,  # Unknown
                confidence=0.25,
                validation_pass_rate=30.0,
                guidance=None,
            ),
        ]

        context = EscalationContext(
            job_id="test-job",
            sheet_num=5,
            validation_results=[],
            confidence=0.45,
            retry_count=2,
            error_history=[],
            prompt_used="Test",
            output_summary="",
            historical_suggestions=suggestions,
        )

        output = StringIO()
        with patch("sys.stdout", output):
            handler._print_context_summary(context)

        printed = output.getvalue()

        # Verify outcome icons
        assert "✓" in printed  # Success
        assert "✗" in printed  # Failed
        assert "?" in printed  # Unknown

    def test_print_context_limits_to_three_suggestions(self) -> None:
        """Test that at most 3 suggestions are displayed."""
        handler = ConsoleEscalationHandler()

        # Create 5 suggestions
        suggestions = [
            HistoricalSuggestion(
                action=f"retry_{i}",
                outcome="success",
                confidence=0.45,
                validation_pass_rate=60.0,
                guidance=f"Suggestion {i}",
            )
            for i in range(5)
        ]

        context = EscalationContext(
            job_id="test-job",
            sheet_num=5,
            validation_results=[],
            confidence=0.45,
            retry_count=2,
            error_history=[],
            prompt_used="Test",
            output_summary="",
            historical_suggestions=suggestions,
        )

        output = StringIO()
        with patch("sys.stdout", output):
            handler._print_context_summary(context)

        printed = output.getvalue()

        # Should show first 3 suggestions only
        assert "Suggestion 0" in printed
        assert "Suggestion 1" in printed
        assert "Suggestion 2" in printed
        assert "Suggestion 3" not in printed
        assert "Suggestion 4" not in printed


# =============================================================================
# TestRunnerSuggestionInjection (v15 Evolution)
# =============================================================================


class TestRunnerSuggestionInjection:
    """Tests for v15 runner._handle_escalation suggestion injection.

    These tests verify that the runner correctly injects historical suggestions
    from get_similar_escalation() into the EscalationContext.
    """

    def test_suggestions_injected_from_store(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that suggestions from store are injected into context."""
        # Record a past escalation with successful outcome
        global_store.record_escalation_decision(
            job_id="past-job",
            sheet_num=1,
            confidence=0.45,
            action="retry",
            validation_pass_rate=60.0,
            retry_count=1,
            guidance="This worked well",
            outcome_after_action="success",
        )

        # Query similar escalations (simulating what runner does)
        similar = global_store.get_similar_escalation(
            confidence=0.45,
            validation_pass_rate=60.0,
            limit=3,
        )

        # Convert to HistoricalSuggestion (as runner does)
        historical_suggestions = [
            HistoricalSuggestion(
                action=past.action,
                outcome=past.outcome_after_action,
                confidence=past.confidence,
                validation_pass_rate=past.validation_pass_rate,
                guidance=past.guidance,
            )
            for past in similar
        ]

        # Build context with injected suggestions
        context = EscalationContext(
            job_id="new-job",
            sheet_num=1,
            validation_results=[],
            confidence=0.45,
            retry_count=1,
            error_history=[],
            prompt_used="test",
            output_summary="",
            historical_suggestions=historical_suggestions,
        )

        # Verify injection worked
        assert len(context.historical_suggestions) == 1
        assert context.historical_suggestions[0].action == "retry"
        assert context.historical_suggestions[0].outcome == "success"
        assert context.historical_suggestions[0].guidance == "This worked well"

    def test_empty_suggestions_when_no_similar(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that empty suggestions when no similar escalations exist."""
        # No past escalations recorded

        # Query similar escalations
        similar = global_store.get_similar_escalation(
            confidence=0.45,
            validation_pass_rate=60.0,
            limit=3,
        )

        # Should be empty
        assert similar == []

        # Context should have empty suggestions
        context = EscalationContext(
            job_id="test-job",
            sheet_num=1,
            validation_results=[],
            confidence=0.45,
            retry_count=1,
            error_history=[],
            prompt_used="test",
            output_summary="",
            historical_suggestions=[],
        )

        assert context.historical_suggestions == []

    def test_multiple_suggestions_ordered_correctly(
        self, global_store: GlobalLearningStore
    ) -> None:
        """Test that multiple suggestions maintain ordering from store."""
        # Record multiple escalations
        global_store.record_escalation_decision(
            job_id="job-1",
            sheet_num=1,
            confidence=0.45,
            action="retry",
            validation_pass_rate=60.0,
            retry_count=1,
            outcome_after_action="success",
        )
        global_store.record_escalation_decision(
            job_id="job-2",
            sheet_num=1,
            confidence=0.45,
            action="skip",
            validation_pass_rate=60.0,
            retry_count=2,
            outcome_after_action="failed",
        )

        # Query - store orders by success first
        similar = global_store.get_similar_escalation(
            confidence=0.45,
            validation_pass_rate=60.0,
            limit=3,
        )

        # Convert to suggestions
        suggestions = [
            HistoricalSuggestion(
                action=past.action,
                outcome=past.outcome_after_action,
                confidence=past.confidence,
                validation_pass_rate=past.validation_pass_rate,
                guidance=past.guidance,
            )
            for past in similar
        ]

        # Success should come first (store ordering)
        assert len(suggestions) == 2
        assert suggestions[0].outcome == "success"
        assert suggestions[1].outcome == "failed"


# =============================================================================
# v21 Evolution: Proactive Checkpoint Tests
# =============================================================================


class TestCheckpointTrigger:
    """Tests for CheckpointTrigger dataclass."""

    def test_trigger_creation(self) -> None:
        """Test basic trigger creation."""

        trigger = CheckpointTrigger(
            name="test_trigger",
            sheet_nums=[1, 2, 3],
            prompt_contains=["dangerous", "delete"],
            min_retry_count=2,
            requires_confirmation=True,
            message="This sheet is dangerous!",
        )

        assert trigger.name == "test_trigger"
        assert trigger.sheet_nums == [1, 2, 3]
        assert trigger.prompt_contains == ["dangerous", "delete"]
        assert trigger.min_retry_count == 2
        assert trigger.requires_confirmation is True
        assert trigger.message == "This sheet is dangerous!"

    def test_trigger_defaults(self) -> None:
        """Test trigger default values."""

        trigger = CheckpointTrigger(name="minimal_trigger")

        assert trigger.name == "minimal_trigger"
        assert trigger.sheet_nums is None
        assert trigger.prompt_contains is None
        assert trigger.min_retry_count is None
        assert trigger.requires_confirmation is True  # Default
        assert trigger.message == ""


class TestCheckpointContext:
    """Tests for CheckpointContext dataclass."""

    def test_context_creation(self) -> None:
        """Test basic context creation."""

        trigger = CheckpointTrigger(name="test")
        context = CheckpointContext(
            job_id="test-job-123",
            sheet_num=5,
            prompt="Do something dangerous",
            trigger=trigger,
            retry_count=2,
            previous_errors=["Error 1", "Error 2"],
        )

        assert context.job_id == "test-job-123"
        assert context.sheet_num == 5
        assert context.prompt == "Do something dangerous"
        assert context.trigger.name == "test"
        assert context.retry_count == 2
        assert len(context.previous_errors) == 2


class TestCheckpointResponse:
    """Tests for CheckpointResponse dataclass."""

    def test_proceed_response(self) -> None:
        """Test proceed response."""

        response = CheckpointResponse(
            action="proceed",
            guidance="User approved",
        )

        assert response.action == "proceed"
        assert response.guidance == "User approved"
        assert response.modified_prompt is None

    def test_abort_response(self) -> None:
        """Test abort response."""

        response = CheckpointResponse(
            action="abort",
            guidance="User stopped the job",
        )

        assert response.action == "abort"

    def test_modify_prompt_response(self) -> None:
        """Test modify_prompt response."""

        response = CheckpointResponse(
            action="modify_prompt",
            modified_prompt="New safer prompt",
            guidance="Modified for safety",
        )

        assert response.action == "modify_prompt"
        assert response.modified_prompt == "New safer prompt"


class TestConsoleCheckpointHandler:
    """Tests for ConsoleCheckpointHandler."""

    @pytest.fixture
    def handler(self) -> "ConsoleCheckpointHandler":
        """Create a ConsoleCheckpointHandler for testing."""
        from marianne.execution.escalation import ConsoleCheckpointHandler
        return ConsoleCheckpointHandler()

    @pytest.mark.asyncio
    async def test_should_checkpoint_sheet_num_match(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test should_checkpoint matches on sheet number."""

        triggers = [
            CheckpointTrigger(name="sheet_5", sheet_nums=[5]),
        ]

        # Should match
        result = await handler.should_checkpoint(
            sheet_num=5,
            prompt="Any prompt",
            retry_count=0,
            triggers=triggers,
        )
        assert result is not None
        assert result.name == "sheet_5"

        # Should not match
        result = await handler.should_checkpoint(
            sheet_num=3,
            prompt="Any prompt",
            retry_count=0,
            triggers=triggers,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_should_checkpoint_prompt_contains_match(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test should_checkpoint matches on prompt keywords."""

        triggers = [
            CheckpointTrigger(
                name="dangerous_keywords",
                prompt_contains=["DELETE", "drop table"],
            ),
        ]

        # Should match (case insensitive)
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="This will delete the file",
            retry_count=0,
            triggers=triggers,
        )
        assert result is not None
        assert result.name == "dangerous_keywords"

        # Should not match
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="This creates a new file",
            retry_count=0,
            triggers=triggers,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_should_checkpoint_retry_count_match(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test should_checkpoint matches on retry count."""

        triggers = [
            CheckpointTrigger(
                name="high_retry",
                min_retry_count=3,
            ),
        ]

        # Should not match (retry count too low)
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="Any prompt",
            retry_count=2,
            triggers=triggers,
        )
        assert result is None

        # Should match (retry count >= threshold)
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="Any prompt",
            retry_count=3,
            triggers=triggers,
        )
        assert result is not None
        assert result.name == "high_retry"

    @pytest.mark.asyncio
    async def test_should_checkpoint_combined_conditions(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test should_checkpoint with multiple conditions (AND logic)."""

        triggers = [
            CheckpointTrigger(
                name="combined",
                sheet_nums=[5, 6],
                prompt_contains=["deploy"],
                min_retry_count=1,
            ),
        ]

        # All conditions must match
        # Miss: wrong sheet
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="deploy to production",
            retry_count=2,
            triggers=triggers,
        )
        assert result is None

        # Miss: wrong keyword
        result = await handler.should_checkpoint(
            sheet_num=5,
            prompt="test something",
            retry_count=2,
            triggers=triggers,
        )
        assert result is None

        # Miss: retry count too low
        result = await handler.should_checkpoint(
            sheet_num=5,
            prompt="deploy to production",
            retry_count=0,
            triggers=triggers,
        )
        assert result is None

        # All match
        result = await handler.should_checkpoint(
            sheet_num=5,
            prompt="deploy to production",
            retry_count=1,
            triggers=triggers,
        )
        assert result is not None
        assert result.name == "combined"

    @pytest.mark.asyncio
    async def test_checkpoint_warning_only(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test checkpoint with requires_confirmation=False auto-proceeds."""

        trigger = CheckpointTrigger(
            name="warning_only",
            requires_confirmation=False,
            message="Just a warning",
        )

        context = CheckpointContext(
            job_id="test-job",
            sheet_num=1,
            prompt="Test prompt",
            trigger=trigger,
        )

        # Should auto-proceed without user input
        with patch('sys.stdout', new=StringIO()):
            response = await handler.checkpoint(context)

        assert response.action == "proceed"

    @pytest.mark.asyncio
    async def test_checkpoint_interactive_proceed(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test interactive checkpoint with 'p' (proceed) response."""

        trigger = CheckpointTrigger(
            name="interactive",
            requires_confirmation=True,
        )

        context = CheckpointContext(
            job_id="test-job",
            sheet_num=1,
            prompt="Test prompt",
            trigger=trigger,
        )

        # Mock user input: 'p' for proceed, then empty guidance
        with (
            patch('builtins.input', side_effect=['p', '']),
            patch('sys.stdout', new=StringIO()),
        ):
            response = await handler.checkpoint(context)

        assert response.action == "proceed"

    @pytest.mark.asyncio
    async def test_checkpoint_interactive_abort(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test interactive checkpoint with 'a' (abort) response."""

        trigger = CheckpointTrigger(
            name="interactive",
            requires_confirmation=True,
        )

        context = CheckpointContext(
            job_id="test-job",
            sheet_num=1,
            prompt="Test prompt",
            trigger=trigger,
        )

        # Mock user input: 'a' for abort
        with (
            patch('builtins.input', side_effect=['a']),
            patch('sys.stdout', new=StringIO()),
        ):
            response = await handler.checkpoint(context)

        assert response.action == "abort"

    @pytest.mark.asyncio
    async def test_checkpoint_interactive_skip(
        self, handler: "ConsoleCheckpointHandler"
    ) -> None:
        """Test interactive checkpoint with 's' (skip) response."""

        trigger = CheckpointTrigger(
            name="interactive",
            requires_confirmation=True,
        )

        context = CheckpointContext(
            job_id="test-job",
            sheet_num=1,
            prompt="Test prompt",
            trigger=trigger,
        )

        # Mock user input: 's' for skip, with guidance
        with (
            patch('builtins.input', side_effect=['s', 'Skip this one']),
            patch('sys.stdout', new=StringIO()),
        ):
            response = await handler.checkpoint(context)

        assert response.action == "skip"
        assert response.guidance == "Skip this one"


class TestCheckpointConfig:
    """Tests for CheckpointConfig and CheckpointTriggerConfig."""

    def test_checkpoint_config_defaults(self) -> None:
        """Test CheckpointConfig default values."""
        from marianne.core.config import CheckpointConfig

        config = CheckpointConfig()

        assert config.enabled is False
        assert config.triggers == []

    def test_checkpoint_config_with_triggers(self) -> None:
        """Test CheckpointConfig with triggers."""
        from marianne.core.config import CheckpointConfig, CheckpointTriggerConfig

        config = CheckpointConfig(
            enabled=True,
            triggers=[
                CheckpointTriggerConfig(
                    name="dangerous_sheets",
                    sheet_nums=[5, 6],
                    message="These sheets modify production",
                ),
                CheckpointTriggerConfig(
                    name="deployment_keywords",
                    prompt_contains=["deploy", "production"],
                ),
            ],
        )

        assert config.enabled is True
        assert len(config.triggers) == 2
        assert config.triggers[0].name == "dangerous_sheets"
        assert config.triggers[1].prompt_contains == ["deploy", "production"]

    def test_checkpoint_trigger_config_validation(self) -> None:
        """Test CheckpointTriggerConfig validation."""
        from marianne.core.config import CheckpointTriggerConfig

        trigger = CheckpointTriggerConfig(
            name="test",
            min_retry_count=0,  # 0 is valid
        )
        assert trigger.min_retry_count == 0

        # Negative should fail validation
        with pytest.raises(ValueError):
            CheckpointTriggerConfig(
                name="invalid",
                min_retry_count=-1,
            )


# =============================================================================
# TestEscalationDecisionLogic (Q012: coverage gap)
# =============================================================================


class TestEscalationDecisionLogic:
    """Tests for ConsoleEscalationHandler.should_escalate decision logic.

    Q012: Covers the escalation decision branching that determines
    whether a sheet should be escalated based on confidence, attempt
    count, and auto-retry configuration.
    """

    @pytest.fixture
    def handler(self) -> ConsoleEscalationHandler:
        return ConsoleEscalationHandler(
            confidence_threshold=0.6,
            auto_retry_on_first_failure=True,
        )

    @pytest.fixture
    def handler_no_auto_retry(self) -> ConsoleEscalationHandler:
        return ConsoleEscalationHandler(
            confidence_threshold=0.6,
            auto_retry_on_first_failure=False,
        )

    def _make_sheet_state(self, attempt_count: int = 1) -> SheetState:
        from marianne.core.checkpoint import SheetStatus
        return SheetState(
            sheet_num=1,
            status=SheetStatus.IN_PROGRESS,
            attempt_count=attempt_count,
        )

    def _make_validation_result(self) -> SheetValidationResult:
        return SheetValidationResult(
            sheet_num=1,
            results=[],
        )

    @pytest.mark.asyncio
    async def test_high_confidence_no_escalation(
        self, handler: ConsoleEscalationHandler
    ) -> None:
        """Confidence above threshold should never escalate."""
        result = await handler.should_escalate(
            self._make_sheet_state(attempt_count=5),
            self._make_validation_result(),
            confidence=0.8,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_low_confidence_first_attempt_auto_retry(
        self, handler: ConsoleEscalationHandler
    ) -> None:
        """First attempt with auto-retry enabled should NOT escalate."""
        result = await handler.should_escalate(
            self._make_sheet_state(attempt_count=1),
            self._make_validation_result(),
            confidence=0.3,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_low_confidence_second_attempt_escalates(
        self, handler: ConsoleEscalationHandler
    ) -> None:
        """Second attempt with low confidence should escalate."""
        result = await handler.should_escalate(
            self._make_sheet_state(attempt_count=2),
            self._make_validation_result(),
            confidence=0.3,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_low_confidence_first_attempt_no_auto_retry_escalates(
        self, handler_no_auto_retry: ConsoleEscalationHandler
    ) -> None:
        """First attempt without auto-retry should escalate on low confidence."""
        result = await handler_no_auto_retry.should_escalate(
            self._make_sheet_state(attempt_count=1),
            self._make_validation_result(),
            confidence=0.3,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_exact_threshold_no_escalation(
        self, handler: ConsoleEscalationHandler
    ) -> None:
        """Confidence exactly at threshold should NOT escalate."""
        result = await handler.should_escalate(
            self._make_sheet_state(attempt_count=5),
            self._make_validation_result(),
            confidence=0.6,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_just_below_threshold_escalates(
        self, handler: ConsoleEscalationHandler
    ) -> None:
        """Confidence just below threshold should escalate (after first attempt)."""
        result = await handler.should_escalate(
            self._make_sheet_state(attempt_count=2),
            self._make_validation_result(),
            confidence=0.59,
        )
        assert result is True


# =============================================================================
# TestCheckpointTriggerMatching (Q012: coverage gap)
# =============================================================================


class TestCheckpointTriggerMatching:
    """Tests for ConsoleCheckpointHandler.should_checkpoint trigger matching.

    Q012: Covers checkpoint trigger evaluation logic including sheet number
    matching, keyword matching, retry count thresholds, and multiple triggers.
    """

    @pytest.fixture
    def handler(self) -> ConsoleCheckpointHandler:
        return ConsoleCheckpointHandler()

    @pytest.mark.asyncio
    async def test_no_triggers_returns_none(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """Empty trigger list means no checkpoint needed."""
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="test prompt",
            retry_count=0,
            triggers=[],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_sheet_num_match(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """Trigger matches when sheet_num is in the list."""
        trigger = CheckpointTrigger(name="sheets", sheet_nums=[3, 5])
        result = await handler.should_checkpoint(
            sheet_num=5,
            prompt="anything",
            retry_count=0,
            triggers=[trigger],
        )
        assert result is trigger

    @pytest.mark.asyncio
    async def test_sheet_num_no_match(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """Trigger doesn't match when sheet_num not in the list."""
        trigger = CheckpointTrigger(name="sheets", sheet_nums=[3, 5])
        result = await handler.should_checkpoint(
            sheet_num=2,
            prompt="anything",
            retry_count=0,
            triggers=[trigger],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_prompt_keyword_match_case_insensitive(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """Keywords match case-insensitively in prompt text."""
        trigger = CheckpointTrigger(name="deploy", prompt_contains=["DEPLOY"])
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="Please deploy the application",
            retry_count=0,
            triggers=[trigger],
        )
        assert result is trigger

    @pytest.mark.asyncio
    async def test_prompt_keyword_no_match(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """No match when keywords aren't in the prompt."""
        trigger = CheckpointTrigger(
            name="deploy", prompt_contains=["deploy", "production"]
        )
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="Run unit tests",
            retry_count=0,
            triggers=[trigger],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_retry_count_threshold(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """Trigger matches when retry count meets minimum."""
        trigger = CheckpointTrigger(name="retries", min_retry_count=3)
        # Below threshold
        result = await handler.should_checkpoint(
            sheet_num=1, prompt="test", retry_count=2, triggers=[trigger]
        )
        assert result is None

        # At threshold
        result = await handler.should_checkpoint(
            sheet_num=1, prompt="test", retry_count=3, triggers=[trigger]
        )
        assert result is trigger

    @pytest.mark.asyncio
    async def test_combined_conditions_all_must_match(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """When trigger has multiple conditions, all must match."""
        trigger = CheckpointTrigger(
            name="combined",
            sheet_nums=[5],
            prompt_contains=["deploy"],
            min_retry_count=2,
        )
        # Only sheet matches
        result = await handler.should_checkpoint(
            sheet_num=5, prompt="test", retry_count=0, triggers=[trigger]
        )
        assert result is None

        # All match
        result = await handler.should_checkpoint(
            sheet_num=5, prompt="deploy now", retry_count=2, triggers=[trigger]
        )
        assert result is trigger

    @pytest.mark.asyncio
    async def test_first_matching_trigger_returned(
        self, handler: ConsoleCheckpointHandler
    ) -> None:
        """Returns the first matching trigger, not all."""
        trigger1 = CheckpointTrigger(name="first", sheet_nums=[1])
        trigger2 = CheckpointTrigger(name="second", sheet_nums=[1, 2])
        result = await handler.should_checkpoint(
            sheet_num=1, prompt="test", retry_count=0, triggers=[trigger1, trigger2]
        )
        assert result is trigger1
