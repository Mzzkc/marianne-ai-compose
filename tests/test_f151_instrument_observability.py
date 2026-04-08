"""TDD tests for F-151: Per-sheet instrument name observability.

The instrument_name field on SheetState (checkpoint.py:247) exists but is
never populated. Score authors cannot see which instrument ran each sheet
in `mzt status` output. This fix populates instrument_name at execution
time in both the legacy runner and baton paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus


class TestLegacyRunnerInstrumentPopulation:
    """In the legacy runner path, instrument_name should be set on SheetState."""

    def test_sheet_state_has_instrument_name_field(self) -> None:
        """SheetState must have instrument_name as an optional field."""
        state = SheetState(sheet_num=1)
        assert hasattr(state, "instrument_name")
        assert state.instrument_name is None  # default is None

    def test_sheet_state_accepts_instrument_name(self) -> None:
        """SheetState can be created with instrument_name set."""
        state = SheetState(sheet_num=1, instrument_name="claude-code")
        assert state.instrument_name == "claude-code"

    def test_sheet_state_instrument_name_is_mutable(self) -> None:
        """instrument_name can be set after creation."""
        state = SheetState(sheet_num=1)
        state.instrument_name = "gemini-cli"
        assert state.instrument_name == "gemini-cli"

    def test_sheet_state_serializes_instrument_name(self) -> None:
        """instrument_name persists through serialization."""
        state = SheetState(sheet_num=1, instrument_name="claude-code")
        data = state.model_dump()
        assert data["instrument_name"] == "claude-code"

        restored = SheetState.model_validate(data)
        assert restored.instrument_name == "claude-code"


class TestBatonPathInstrumentPopulation:
    """In the baton path, instrument_name should be populated from Sheet entities."""

    @pytest.mark.asyncio
    async def test_run_via_baton_populates_instrument_name(self) -> None:
        """_run_via_baton should set instrument_name on live SheetStates.

        After build_sheets() produces Sheet entities (with resolved instrument
        names) and register_job() is called, the live SheetStates should have
        their instrument_name fields populated.

        F-255.2: _run_via_baton now creates the initial CheckpointState in
        _live_states with instrument_names set from Sheet entities at creation
        time — no longer a post-register fixup.
        """
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)
        manager._baton_adapter = MagicMock()
        manager._baton_adapter.wait_for_completion = AsyncMock(return_value=True)
        manager._baton_adapter.has_completed_sheets = MagicMock(return_value=True)
        manager._baton_adapter.publish_job_event = AsyncMock()

        manager._live_states = {}
        manager._job_meta = {"test-job": MagicMock(completed_new_work=False)}

        # Mock build_sheets to return Sheet entities with instrument names
        mock_sheet_1 = MagicMock()
        mock_sheet_1.num = 1
        mock_sheet_1.instrument_name = "claude-code"
        mock_sheet_1.movement = 1
        mock_sheet_2 = MagicMock()
        mock_sheet_2.num = 2
        mock_sheet_2.instrument_name = "gemini-cli"
        mock_sheet_2.movement = 1

        mock_config = MagicMock()
        mock_config.name = "test-score"
        mock_config.retry.max_retries = 3
        mock_config.cost_limits.enabled = False
        mock_config.cost_limits.max_cost_per_job = None
        mock_config.backend.type = "claude_cli"
        mock_config.parallel.enabled = False
        mock_config.cross_sheet = None

        mock_request = MagicMock()
        mock_request.self_healing = False

        with patch("marianne.core.sheet.build_sheets", return_value=[mock_sheet_1, mock_sheet_2]):
            with patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}):
                result = await JobManager._run_via_baton(
                    manager, "test-job", mock_config, mock_request,
                )

        # F-255.2: live state is now created by _run_via_baton
        live = manager._live_states["test-job"]
        assert live.sheets[1].instrument_name == "claude-code"
        assert live.sheets[2].instrument_name == "gemini-cli"

    @pytest.mark.asyncio
    async def test_run_via_baton_instrument_from_sheet_entity(self) -> None:
        """F-255.2: instrument_name comes from the Sheet entity, not a pre-existing
        live state. The live state is created fresh with instrument_name set from
        the Sheet entities at creation time."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)
        manager._baton_adapter = MagicMock()
        manager._baton_adapter.wait_for_completion = AsyncMock(return_value=True)
        manager._baton_adapter.has_completed_sheets = MagicMock(return_value=True)
        manager._baton_adapter.publish_job_event = AsyncMock()

        manager._live_states = {}
        manager._job_meta = {"test-job": MagicMock(completed_new_work=False)}

        mock_sheet = MagicMock()
        mock_sheet.num = 1
        mock_sheet.instrument_name = "gemini-cli"
        mock_sheet.movement = 1

        mock_config = MagicMock()
        mock_config.name = "test-score"
        mock_config.retry.max_retries = 3
        mock_config.cost_limits.enabled = False
        mock_config.cost_limits.max_cost_per_job = None
        mock_config.backend.type = "claude_cli"
        mock_config.parallel.enabled = False
        mock_config.cross_sheet = None

        mock_request = MagicMock()
        mock_request.self_healing = False

        with patch("marianne.core.sheet.build_sheets", return_value=[mock_sheet]):
            with patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}):
                await JobManager._run_via_baton(
                    manager, "test-job", mock_config, mock_request,
                )

        # instrument_name comes from the Sheet entity
        live = manager._live_states["test-job"]
        assert live.sheets[1].instrument_name == "gemini-cli"


class TestCheckpointInstrumentPersistence:
    """instrument_name should survive checkpoint save/load cycles."""

    def test_checkpoint_with_instrument_names_round_trips(self) -> None:
        """CheckpointState with instrument-annotated sheets serializes correctly."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            workspace=Path("/tmp/ws"),
            total_sheets=2,
        )
        state.sheets[1] = SheetState(
            sheet_num=1,
            instrument_name="claude-code",
            status=SheetStatus.COMPLETED,
        )
        state.sheets[2] = SheetState(
            sheet_num=2,
            instrument_name="gemini-cli",
            status=SheetStatus.IN_PROGRESS,
        )

        data = state.model_dump(mode="json")
        restored = CheckpointState.model_validate(data)

        assert restored.sheets[1].instrument_name == "claude-code"
        assert restored.sheets[2].instrument_name == "gemini-cli"
