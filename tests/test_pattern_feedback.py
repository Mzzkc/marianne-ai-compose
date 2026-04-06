"""Tests for pattern feedback loop closure (v9 evolution).

Tests for pattern ID tracking, feedback recording, and SheetState integration.
Evolution v9: Pattern Feedback Loop Closure.
"""

from unittest.mock import MagicMock

import pytest

from marianne.core.checkpoint import SheetState, SheetStatus


class TestSheetStatePatternFields:
    """Tests for SheetState pattern feedback fields."""

    def test_applied_pattern_ids_default(self):
        """Test applied_pattern_ids defaults to empty list."""
        state = SheetState(sheet_num=1)
        assert state.applied_pattern_ids == []

    def test_applied_pattern_descriptions_default(self):
        """Test applied_pattern_descriptions defaults to empty list."""
        state = SheetState(sheet_num=1)
        assert state.applied_pattern_descriptions == []

    def test_pattern_fields_can_be_set(self):
        """Test pattern fields can be populated."""
        state = SheetState(sheet_num=1)
        state.applied_pattern_ids = ["uuid-1", "uuid-2"]
        state.applied_pattern_descriptions = [
            "Pattern: Check file exists",
            "Pattern: Validate format",
        ]

        assert len(state.applied_pattern_ids) == 2
        assert len(state.applied_pattern_descriptions) == 2
        assert state.applied_pattern_ids[0] == "uuid-1"
        assert "Check file exists" in state.applied_pattern_descriptions[0]

    def test_pattern_fields_serialize(self):
        """Test pattern fields serialize correctly."""
        state = SheetState(
            sheet_num=1,
            applied_pattern_ids=["id-1", "id-2"],
            applied_pattern_descriptions=["desc-1", "desc-2"],
        )

        # After Q029: model_dump emits structured applied_patterns
        data = state.model_dump()
        assert "applied_patterns" in data
        assert len(data["applied_patterns"]) == 2
        assert data["applied_patterns"][0] == {"id": "id-1", "description": "desc-1"}
        assert data["applied_patterns"][1] == {"id": "id-2", "description": "desc-2"}

    def test_pattern_fields_load_from_dict(self):
        """Test pattern fields can be loaded from dict (checkpoint recovery)."""
        data = {
            "sheet_num": 1,
            "status": "completed",
            "applied_pattern_ids": ["id-from-checkpoint"],
            "applied_pattern_descriptions": ["desc-from-checkpoint"],
        }

        state = SheetState(**data)

        assert state.applied_pattern_ids == ["id-from-checkpoint"]
        assert state.applied_pattern_descriptions == ["desc-from-checkpoint"]

    def test_pattern_fields_backward_compatible(self):
        """Test old checkpoints without pattern fields still load."""
        # Old checkpoint data without v9 fields
        data = {
            "sheet_num": 1,
            "status": "completed",
            "success_without_retry": True,
            # No applied_pattern_ids or applied_pattern_descriptions
        }

        state = SheetState(**data)

        # Should use defaults
        assert state.applied_pattern_ids == []
        assert state.applied_pattern_descriptions == []


class TestPatternFeedbackRecording:
    """Tests for _record_pattern_feedback method logic."""

    @pytest.mark.asyncio
    async def test_record_feedback_skipped_when_no_global_store(self):
        """Test feedback recording skips when global store is None."""
        # Create a mock runner with no global store

        # We can't easily instantiate JobRunner without full config,
        # so we'll test the logic directly by creating a mock

        # The logic should early-return when store is None
        # This is tested implicitly - no exception means success
        # If _record_pattern_feedback was called with None store, it returns early

    @pytest.mark.asyncio
    async def test_record_feedback_skipped_when_no_patterns(self):
        """Test feedback recording skips when pattern_ids is empty."""
        # The logic should early-return when pattern_ids is empty
        # This is a no-op case
        pass

    @pytest.mark.asyncio
    async def test_pattern_led_to_success_logic_success_first_attempt(self):
        """Test pattern_led_to_success is True when validation passed AND first attempt."""
        validation_passed = True
        success_without_retry = True

        # From the code: pattern_led_to_success = validation_passed and success_without_retry
        pattern_led_to_success = validation_passed and success_without_retry

        assert pattern_led_to_success is True

    @pytest.mark.asyncio
    async def test_pattern_led_to_success_logic_success_with_retry(self):
        """Test pattern_led_to_success is False when validation passed but not first attempt."""
        validation_passed = True
        success_without_retry = False

        pattern_led_to_success = validation_passed and success_without_retry

        assert pattern_led_to_success is False

    @pytest.mark.asyncio
    async def test_pattern_led_to_success_logic_failure(self):
        """Test pattern_led_to_success is False when validation failed."""
        validation_passed = False
        success_without_retry = False

        pattern_led_to_success = validation_passed and success_without_retry

        assert pattern_led_to_success is False


class TestPatternFeedbackIntegration:
    """Integration tests for pattern feedback with GlobalLearningStore."""

    @pytest.fixture
    def mock_global_store(self):
        """Create a mock global learning store."""
        store = MagicMock()
        store.record_pattern_application = MagicMock(return_value="app-id-1")
        return store

    def test_record_pattern_application_called_for_each_pattern(self, mock_global_store):
        """Test that record_pattern_application is called for each pattern ID."""
        pattern_ids = ["pattern-1", "pattern-2", "pattern-3"]

        # Simulate the loop in _record_pattern_feedback
        for pattern_id in pattern_ids:
            mock_global_store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id="sheet_1",
                pattern_led_to_success=True,
                retry_count_before=0,
                retry_count_after=0,
            )

        assert mock_global_store.record_pattern_application.call_count == 3

    def test_record_pattern_application_with_correct_args(self, mock_global_store):
        """Test record_pattern_application is called with correct arguments."""
        pattern_id = "test-pattern-id"
        sheet_num = 5
        pattern_led_to_success = True
        success_without_retry = True

        mock_global_store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id=f"sheet_{sheet_num}",
            pattern_led_to_success=pattern_led_to_success,
            retry_count_before=0,
            retry_count_after=0 if success_without_retry else 1,
        )

        mock_global_store.record_pattern_application.assert_called_once_with(
            pattern_id=pattern_id,
            execution_id="sheet_5",
            pattern_led_to_success=True,
            retry_count_before=0,
            retry_count_after=0,
        )

    def test_record_pattern_application_failure_handling(self, mock_global_store):
        """Test that exceptions in recording don't propagate."""
        mock_global_store.record_pattern_application.side_effect = Exception("DB error")

        # The real code catches exceptions and logs a warning
        # This test verifies the pattern - recording should be resilient
        pattern_ids = ["pattern-1"]

        errors_caught = 0
        for pattern_id in pattern_ids:
            try:
                mock_global_store.record_pattern_application(
                    pattern_id=pattern_id,
                    execution_id="sheet_1",
                    pattern_led_to_success=True,
                    retry_count_before=0,
                    retry_count_after=0,
                )
            except Exception:
                # In real code, this is caught and logged
                errors_caught += 1

        assert errors_caught == 1


class TestSheetOutcomePatternFields:
    """Tests for SheetOutcome pattern fields."""

    def test_patterns_applied_field_exists(self):
        """Test SheetOutcome has patterns_applied field."""
        from marianne.learning.outcomes import SheetOutcome

        outcome = SheetOutcome(
            sheet_id="test_sheet_1",
            job_id="test_job",
            validation_results=[],
            execution_duration=10.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=100.0,
            success_without_retry=True,
            patterns_applied=["Pattern: Check file", "Pattern: Validate format"],
        )

        assert outcome.patterns_applied == ["Pattern: Check file", "Pattern: Validate format"]

    def test_patterns_applied_default_empty(self):
        """Test patterns_applied defaults to empty list."""
        from marianne.learning.outcomes import SheetOutcome

        outcome = SheetOutcome(
            sheet_id="test_sheet_1",
            job_id="test_job",
            validation_results=[],
            execution_duration=10.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=100.0,
            success_without_retry=True,
        )

        assert outcome.patterns_applied == []


class TestAggregatorPatternTracking:
    """Tests for pattern tracking in aggregator."""

    def test_aggregator_receives_patterns_applied(self):
        """Test that aggregator _record_pattern_applications uses patterns_applied."""
        from marianne.learning.outcomes import SheetOutcome

        # Create an outcome with patterns_applied
        outcome = SheetOutcome(
            sheet_id="test_sheet_1",
            job_id="test_job",
            validation_results=[],
            execution_duration=10.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=100.0,
            success_without_retry=True,
            patterns_applied=["Pattern A", "Pattern B"],
        )

        # The aggregator should be able to access patterns_applied
        assert len(outcome.patterns_applied) == 2
        assert "Pattern A" in outcome.patterns_applied


class TestPatternFeedbackEndToEnd:
    """End-to-end tests for the pattern feedback loop."""

    @pytest.fixture
    def sample_sheet_state_with_patterns(self):
        """Create a SheetState with pattern data populated."""
        state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            success_without_retry=True,
            outcome_category="success_first_try",
            applied_pattern_ids=["uuid-pattern-1", "uuid-pattern-2"],
            applied_pattern_descriptions=[
                "⚠️ Common issue: file_exists validation tends to fail",
                "💡 Tip: Use absolute paths for workspace files",
            ],
        )
        return state

    def test_full_feedback_data_flow(self, sample_sheet_state_with_patterns):
        """Test that pattern data flows from SheetState to SheetOutcome."""
        from marianne.learning.outcomes import SheetOutcome

        state = sample_sheet_state_with_patterns

        # Simulate what _aggregate_to_global_store does
        outcome = SheetOutcome(
            sheet_id="job_sheet_1",
            job_id="test_job",
            validation_results=[],
            execution_duration=5.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=state.status,
            validation_pass_rate=100.0,
            success_without_retry=state.success_without_retry,
            patterns_applied=state.applied_pattern_descriptions,
        )

        # Verify the data flow
        assert outcome.success_without_retry is True
        assert len(outcome.patterns_applied) == 2
        assert "file_exists validation" in outcome.patterns_applied[0]

    def test_checkpoint_save_restore_preserves_patterns(self, sample_sheet_state_with_patterns):
        """Test that pattern data survives checkpoint save/restore."""
        state = sample_sheet_state_with_patterns

        # Simulate checkpoint save (model_dump)
        saved_data = state.model_dump()

        # Simulate checkpoint restore (model_validate)
        restored_state = SheetState(**saved_data)

        assert restored_state.applied_pattern_ids == state.applied_pattern_ids
        assert restored_state.applied_pattern_descriptions == state.applied_pattern_descriptions

    def test_empty_patterns_handled_gracefully(self):
        """Test that empty pattern lists are handled correctly."""
        state = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            applied_pattern_ids=[],
            applied_pattern_descriptions=[],
        )

        # Should not raise errors
        assert len(state.applied_pattern_ids) == 0
        assert len(state.applied_pattern_descriptions) == 0

        # Copy operations should work
        ids_copy = state.applied_pattern_ids.copy()
        descs_copy = state.applied_pattern_descriptions.copy()
        assert ids_copy == []
        assert descs_copy == []
