"""Tests for sheet-first architecture field additions to SheetState and CheckpointState.

All new fields have defaults, ensuring backward compatibility (M-005).
Existing state data (JSON or SQLite) that lacks these fields will deserialize
correctly with default values.
"""

from __future__ import annotations

from marianne.core.checkpoint import CheckpointState, SheetState


class TestSheetStateNewFields:
    """Test new sheet-first fields on SheetState."""

    def test_defaults_for_backward_compatibility(self):
        """New fields default to None — existing data deserializes unchanged."""
        state = SheetState(sheet_num=1)
        assert state.instrument_name is None
        assert state.instrument_model is None
        assert state.movement is None
        assert state.voice is None

    def test_instrument_name_populated(self):
        """instrument_name can be set when a sheet is executed."""
        state = SheetState(sheet_num=3, instrument_name="gemini-cli")
        assert state.instrument_name == "gemini-cli"

    def test_instrument_model_populated(self):
        """instrument_model records which model was used."""
        state = SheetState(
            sheet_num=3,
            instrument_name="gemini-cli",
            instrument_model="gemini-2.5-pro",
        )
        assert state.instrument_model == "gemini-2.5-pro"

    def test_movement_and_voice_populated(self):
        """Movement and voice record the sheet's position in the score."""
        state = SheetState(
            sheet_num=7,
            movement=3,
            voice=2,
        )
        assert state.movement == 3
        assert state.voice == 2

    def test_serialization_roundtrip(self):
        """New fields survive serialization/deserialization."""
        state = SheetState(
            sheet_num=1,
            instrument_name="claude-code",
            instrument_model="claude-opus-4-6",
            movement=1,
            voice=None,
        )
        data = state.model_dump()
        restored = SheetState.model_validate(data)
        assert restored.instrument_name == "claude-code"
        assert restored.instrument_model == "claude-opus-4-6"
        assert restored.movement == 1
        assert restored.voice is None

    def test_legacy_data_without_new_fields(self):
        """Legacy data dict without new fields deserializes with defaults."""
        legacy_data = {
            "sheet_num": 5,
            "status": "completed",
            "attempt_count": 1,
        }
        state = SheetState.model_validate(legacy_data)
        assert state.sheet_num == 5
        assert state.instrument_name is None
        assert state.movement is None


class TestCheckpointStateNewFields:
    """Test new sheet-first fields on CheckpointState."""

    def test_defaults_for_backward_compatibility(self):
        """New fields default correctly — existing data unchanged."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=5,
        )
        assert state.instruments_used == []
        assert state.total_movements is None

    def test_instruments_used_populated(self):
        """instruments_used lists distinct instruments across sheets."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=5,
            instruments_used=["claude-code", "gemini-cli"],
        )
        assert len(state.instruments_used) == 2
        assert "gemini-cli" in state.instruments_used

    def test_total_movements_populated(self):
        """total_movements records the job's movement count."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=10,
            total_movements=3,
        )
        assert state.total_movements == 3

    def test_serialization_roundtrip(self):
        """New fields survive CheckpointState serialization."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=5,
            instruments_used=["claude-code"],
            total_movements=2,
        )
        data = state.model_dump()
        restored = CheckpointState.model_validate(data)
        assert restored.instruments_used == ["claude-code"]
        assert restored.total_movements == 2

    def test_legacy_data_without_new_fields(self):
        """Legacy checkpoint data deserializes with defaults."""
        legacy_data = {
            "job_id": "old-job",
            "job_name": "Old",
            "total_sheets": 3,
        }
        state = CheckpointState.model_validate(legacy_data)
        assert state.instruments_used == []
        assert state.total_movements is None
