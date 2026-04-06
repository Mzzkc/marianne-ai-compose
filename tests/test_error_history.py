"""Tests for error history tracking functionality (Task 10: Error History Model).

This module tests the ErrorRecord model and error history tracking in SheetState.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from marianne.core.checkpoint import (
    MAX_ERROR_HISTORY,
    ErrorRecord,
    SheetState,
)
from tests.helpers import record_error_on_sheet


class TestErrorRecordModel:
    """Tests for the ErrorRecord model."""

    def test_create_minimal_error_record(self):
        """Test creating an ErrorRecord with only required fields."""
        record = ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message="Connection failed",
            attempt_number=1,
        )

        assert record.error_type == "transient"
        assert record.error_code == "E001"
        assert record.error_message == "Connection failed"
        assert record.attempt_number == 1
        assert record.context == {}
        assert record.stdout_tail is None
        assert record.stderr_tail is None
        assert record.stack_trace is None
        assert record.timestamp is not None

    def test_create_full_error_record(self):
        """Test creating an ErrorRecord with all fields."""
        record = ErrorRecord(
            error_type="rate_limit",
            error_code="E429",
            error_message="Rate limit exceeded",
            attempt_number=3,
            context={"exit_code": 1, "category": "api_limit"},
            stdout_tail="last output",
            stderr_tail="error output",
            stack_trace="Traceback...",
        )

        assert record.error_type == "rate_limit"
        assert record.error_code == "E429"
        assert record.error_message == "Rate limit exceeded"
        assert record.attempt_number == 3
        assert record.context == {"exit_code": 1, "category": "api_limit"}
        assert record.stdout_tail == "last output"
        assert record.stderr_tail == "error output"
        assert record.stack_trace == "Traceback..."

    def test_error_type_validation(self):
        """Test that error_type only accepts valid literals."""
        # Valid types
        ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message="test",
            attempt_number=1,
        )
        ErrorRecord(
            error_type="rate_limit",
            error_code="E001",
            error_message="test",
            attempt_number=1,
        )
        ErrorRecord(
            error_type="permanent",
            error_code="E001",
            error_message="test",
            attempt_number=1,
        )

        # Invalid type should raise validation error
        with pytest.raises(ValidationError):
            ErrorRecord(
                error_type="invalid_type",  # type: ignore[arg-type]
                error_code="E001",
                error_message="test",
                attempt_number=1,
            )

    def test_attempt_number_must_be_positive(self):
        """Test that attempt_number must be >= 1."""
        # Valid attempt number
        ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message="test",
            attempt_number=1,
        )

        # Zero should fail
        with pytest.raises(ValidationError):
            ErrorRecord(
                error_type="transient",
                error_code="E001",
                error_message="test",
                attempt_number=0,
            )

        # Negative should fail
        with pytest.raises(ValidationError):
            ErrorRecord(
                error_type="transient",
                error_code="E001",
                error_message="test",
                attempt_number=-1,
            )

    def test_timestamp_has_default(self):
        """Test that timestamp is automatically set to current UTC time."""
        before = datetime.now(UTC)
        record = ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message="test",
            attempt_number=1,
        )
        after = datetime.now(UTC)

        assert record.timestamp >= before
        assert record.timestamp <= after

    def test_timestamp_can_be_explicit(self):
        """Test that timestamp can be explicitly provided."""
        explicit_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        record = ErrorRecord(
            timestamp=explicit_time,
            error_type="transient",
            error_code="E001",
            error_message="test",
            attempt_number=1,
        )

        assert record.timestamp == explicit_time

    def test_error_record_serialization(self):
        """Test that ErrorRecord survives JSON serialization."""
        record = ErrorRecord(
            error_type="permanent",
            error_code="E500",
            error_message="Fatal error",
            attempt_number=5,
            context={"signal": 9, "killed": True},
            stdout_tail="output",
            stderr_tail="errors",
        )

        data = record.model_dump(mode="json")
        loaded = ErrorRecord.model_validate(data)

        assert loaded.error_type == record.error_type
        assert loaded.error_code == record.error_code
        assert loaded.error_message == record.error_message
        assert loaded.attempt_number == record.attempt_number
        assert loaded.context == record.context
        assert loaded.stdout_tail == record.stdout_tail
        assert loaded.stderr_tail == record.stderr_tail


class TestSheetStateErrorHistory:
    """Tests for error_history field on SheetState."""

    def test_error_history_default_empty(self):
        """Test that error_history defaults to empty list."""
        state = SheetState(sheet_num=1)
        assert state.error_history == []

    def test_error_history_field_type(self):
        """Test that error_history is a list of ErrorRecord."""
        records = [
            ErrorRecord(
                error_type="transient",
                error_code="E001",
                error_message="Error 1",
                attempt_number=1,
            ),
            ErrorRecord(
                error_type="rate_limit",
                error_code="E429",
                error_message="Error 2",
                attempt_number=2,
            ),
        ]

        state = SheetState(sheet_num=1, error_history=records)
        assert len(state.error_history) == 2
        assert all(isinstance(r, ErrorRecord) for r in state.error_history)


class TestErrorHistoryMaxConstant:
    """Tests for MAX_ERROR_HISTORY constant."""

    def test_max_error_history_value(self):
        """Test that MAX_ERROR_HISTORY is 50."""
        assert MAX_ERROR_HISTORY == 50


class TestErrorHistorySerialization:
    """Tests for error history serialization and deserialization."""

    def test_error_history_serialization(self):
        """Test that error_history survives JSON serialization."""
        state = SheetState(sheet_num=1)
        record_error_on_sheet(
            state,
            error_type="transient",
            error_code="E001",
            error_message="Test error",
            attempt=1,
            exit_code=1,
        )

        data = state.model_dump(mode="json")
        loaded = SheetState.model_validate(data)

        assert len(loaded.error_history) == 1
        record = loaded.error_history[0]
        assert record.error_type == "transient"
        assert record.error_code == "E001"
        assert record.error_message == "Test error"
        assert record.attempt_number == 1
        assert record.context["exit_code"] == 1

    def test_error_history_serialization_multiple(self):
        """Test serialization with multiple error records."""
        state = SheetState(sheet_num=1)
        for i in range(5):
            record_error_on_sheet(
                state,
                error_type="transient" if i % 2 == 0 else "permanent",
                error_code=f"E{i:03d}",
                error_message=f"Error {i}",
                attempt=i + 1,
            )

        data = state.model_dump(mode="json")
        loaded = SheetState.model_validate(data)

        assert len(loaded.error_history) == 5
        for i, record in enumerate(loaded.error_history):
            assert record.error_code == f"E{i:03d}"
            assert record.attempt_number == i + 1

    def test_backwards_compatibility_missing_error_history(self):
        """Test loading old state without error_history field."""
        # Simulate old state data without error_history
        old_data = {
            "sheet_num": 1,
            "status": "completed",
            "attempt_count": 2,
            # No error_history field
        }

        # Should load successfully with empty default
        loaded = SheetState.model_validate(old_data)
        assert loaded.error_history == []


class TestErrorHistoryIntegration:
    """Integration tests for error history with other SheetState functionality."""

    def test_error_history_with_output_capture(self):
        """Test error history works alongside output capture."""
        state = SheetState(sheet_num=1)

        # Capture some output
        state.capture_output("stdout content", "stderr content")

        # Record error referencing captured output
        record_error_on_sheet(
            state,
            error_type="permanent",
            error_code="E001",
            error_message="Execution failed",
            attempt=1,
            stdout_tail=state.stdout_tail,
            stderr_tail=state.stderr_tail,
        )

        # Both should work independently
        assert state.stdout_tail == "stdout content"
        assert state.error_history[0].stdout_tail == "stdout content"

    def test_error_history_preserves_other_fields(self):
        """Test that using error history doesn't affect other SheetState fields."""
        state = SheetState(
            sheet_num=5,
            attempt_count=3,
            exit_code=1,
            error_message="Previous error",
            confidence_score=0.75,
        )

        record_error_on_sheet(
            state,
            error_type="transient",
            error_code="E001",
            error_message="New error",
            attempt=3,
        )

        # Original fields should be unchanged
        assert state.sheet_num == 5
        assert state.attempt_count == 3
        assert state.exit_code == 1
        assert state.error_message == "Previous error"
        assert state.confidence_score == 0.75
        # New history should be added
        assert len(state.error_history) == 1


class TestErrorRecordEdgeCases:
    """Edge case tests for error handling."""

    def test_empty_error_message(self):
        """Test that empty error message is allowed."""
        record = ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message="",
            attempt_number=1,
        )
        assert record.error_message == ""

    def test_long_error_message(self):
        """Test handling of very long error messages."""
        long_message = "x" * 10000
        record = ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message=long_message,
            attempt_number=1,
        )
        assert record.error_message == long_message

    def test_unicode_in_error_message(self):
        """Test unicode characters in error message."""
        unicode_message = "Error: 连接失败 🔥 Ошибка сети"
        record = ErrorRecord(
            error_type="transient",
            error_code="E001",
            error_message=unicode_message,
            attempt_number=1,
        )
        assert record.error_message == unicode_message

    def test_nested_context_dict(self):
        """Test that context can contain nested structures."""
        nested_context = {
            "outer": {"inner": {"deep": "value"}},
            "list": [1, 2, {"nested": True}],
        }

        state = SheetState(sheet_num=1)
        record_error_on_sheet(
            state,
            error_type="transient",
            error_code="E001",
            error_message="Error",
            attempt=1,
            **nested_context,
        )

        record = state.error_history[0]
        assert record.context["outer"]["inner"]["deep"] == "value"
        assert record.context["list"][2]["nested"] is True

    def test_context_with_none_values(self):
        """Test that context can contain None values."""
        state = SheetState(sheet_num=1)
        record_error_on_sheet(
            state,
            error_type="transient",
            error_code="E001",
            error_message="Error",
            attempt=1,
            some_field=None,
            other_field="value",
        )

        record = state.error_history[0]
        assert record.context["some_field"] is None
        assert record.context["other_field"] == "value"
