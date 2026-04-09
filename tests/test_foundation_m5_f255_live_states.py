"""TDD tests for F-255.2: Baton _live_states never populated.

F-255.2: When jobs run through the baton adapter, _live_states is never
populated with a CheckpointState. This means:
- mzt status shows "Full status unavailable" for baton-managed jobs
- _on_baton_state_sync returns early (no live state to update)
- Profiler and semantic analyzer see no running jobs

Fix: Create an initial CheckpointState in _live_states when a baton job
is registered (_run_via_baton) or recovered (_resume_via_baton). The
state_sync_callback then has a live state to update as sheets progress.

TDD: These tests are written RED first.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)


class TestBatonLiveStatesPopulation:
    """F-255.2: _run_via_baton must populate _live_states before
    registering the job with the baton adapter."""

    @pytest.mark.asyncio
    async def test_run_via_baton_populates_live_states(self) -> None:
        """After calling _run_via_baton, _live_states[job_id] must exist."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)
        manager._baton_adapter = MagicMock()
        manager._baton_adapter.wait_for_completion = AsyncMock(return_value=True)
        manager._baton_adapter.has_completed_sheets = MagicMock(return_value=True)
        manager._baton_adapter.publish_job_event = AsyncMock()

        manager._live_states = {}
        manager._job_meta = {"test-job": MagicMock(completed_new_work=False)}

        mock_sheet_1 = MagicMock()
        mock_sheet_1.num = 1
        mock_sheet_1.instrument_name = "claude-code"
        mock_sheet_1.movement = 1
        mock_sheet_2 = MagicMock()
        mock_sheet_2.num = 2
        mock_sheet_2.instrument_name = "claude-code"
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
        mock_request.start_sheet = None

        mock_config.pause_between_sheets_seconds = 0

        with (
            patch("marianne.core.sheet.build_sheets", return_value=[mock_sheet_1, mock_sheet_2]),
            patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}),
        ):
            result = await JobManager._run_via_baton(
                manager, "test-job", mock_config, mock_request,
            )

        # The critical assertion: _live_states must be populated
        assert "test-job" in manager._live_states, (
            "F-255.2: _run_via_baton must populate _live_states[job_id] "
            "so status display and state sync work"
        )

    @pytest.mark.asyncio
    async def test_live_state_has_correct_structure(self) -> None:
        """The live state must be a valid CheckpointState with correct fields."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)
        manager._baton_adapter = MagicMock()
        manager._baton_adapter.wait_for_completion = AsyncMock(return_value=True)
        manager._baton_adapter.has_completed_sheets = MagicMock(return_value=True)
        manager._baton_adapter.publish_job_event = AsyncMock()

        manager._live_states = {}
        manager._job_meta = {"test-job": MagicMock(completed_new_work=False)}

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
        mock_request.start_sheet = None

        mock_config.pause_between_sheets_seconds = 0

        with (
            patch("marianne.core.sheet.build_sheets", return_value=[mock_sheet_1, mock_sheet_2]),
            patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}),
        ):
            await JobManager._run_via_baton(
                manager, "test-job", mock_config, mock_request,
            )

        live = manager._live_states["test-job"]
        assert isinstance(live, CheckpointState), (
            "Live state must be a CheckpointState instance"
        )
        assert live.job_id == "test-job"
        assert live.job_name == "test-score"
        assert live.total_sheets == 2
        # After wait_for_completion (mocked all_success=True), status updates to COMPLETED
        assert live.status in (JobStatus.RUNNING, JobStatus.COMPLETED)

        # Sheet states must exist for all sheets
        assert 1 in live.sheets
        assert 2 in live.sheets
        assert live.sheets[1].status == SheetStatus.PENDING
        assert live.sheets[2].status == SheetStatus.PENDING

    @pytest.mark.asyncio
    async def test_live_state_has_instrument_names(self) -> None:
        """Sheet states in live state must have instrument_name populated."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)
        manager._baton_adapter = MagicMock()
        manager._baton_adapter.wait_for_completion = AsyncMock(return_value=True)
        manager._baton_adapter.has_completed_sheets = MagicMock(return_value=True)
        manager._baton_adapter.publish_job_event = AsyncMock()

        manager._live_states = {}
        manager._job_meta = {"test-job": MagicMock(completed_new_work=False)}

        mock_sheet_1 = MagicMock()
        mock_sheet_1.num = 1
        mock_sheet_1.instrument_name = "claude-code"
        mock_sheet_1.movement = 1

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
        mock_request.start_sheet = None

        mock_config.pause_between_sheets_seconds = 0

        with (
            patch("marianne.core.sheet.build_sheets", return_value=[mock_sheet_1]),
            patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}),
        ):
            await JobManager._run_via_baton(
                manager, "test-job", mock_config, mock_request,
            )

        live = manager._live_states["test-job"]
        assert live.sheets[1].instrument_name == "claude-code", (
            "F-151: instrument_name must be set on sheet states"
        )


class TestBatonStateSyncCallback:
    """F-255.2: _on_baton_state_sync must be able to update live states."""

    def test_state_sync_updates_sheet_status(self) -> None:
        """When _on_baton_state_sync fires, it must update the sheet status
        in the live CheckpointState."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)

        # Pre-populate _live_states (this is what our fix does)
        live = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=2,
            status=JobStatus.RUNNING,
            sheets={
                1: SheetState(sheet_num=1),
                2: SheetState(sheet_num=2),
            },
        )
        manager._live_states = {"test-job": live}

        # Call the real method
        JobManager._on_baton_state_sync(manager, "test-job", 1, "completed")

        assert live.sheets[1].status == SheetStatus.COMPLETED, (
            "F-255.2: _on_baton_state_sync must update sheet status"
        )

    def test_state_sync_noop_when_no_live_state(self) -> None:
        """When no live state exists, _on_baton_state_sync should not crash."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)
        manager._live_states = {}

        # Should not raise
        JobManager._on_baton_state_sync(manager, "missing-job", 1, "completed")

    def test_state_sync_noop_for_unknown_sheet(self) -> None:
        """When the sheet doesn't exist in the live state, no crash."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)

        live = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            sheets={1: SheetState(sheet_num=1)},
        )
        manager._live_states = {"test-job": live}

        # Sheet 99 doesn't exist — should not raise
        JobManager._on_baton_state_sync(manager, "test-job", 99, "completed")


class TestBatonResumeLiveStates:
    """F-255.2: _resume_via_baton must also populate _live_states."""

    @pytest.mark.asyncio
    async def test_resume_via_baton_populates_live_states(self) -> None:
        """After calling _resume_via_baton, _live_states[job_id] must exist
        with the recovered checkpoint data."""
        from marianne.daemon.manager import JobManager

        manager = MagicMock(spec=JobManager)
        manager._baton_adapter = MagicMock()
        manager._baton_adapter.wait_for_completion = AsyncMock(return_value=True)
        manager._baton_adapter.has_completed_sheets = MagicMock(return_value=True)
        manager._baton_adapter.publish_job_event = AsyncMock()

        manager._live_states = {}
        meta = MagicMock()
        meta.config_path = Path("/tmp/test.yaml")
        meta.completed_new_work = False
        manager._job_meta = {"test-job": meta}

        # Create a checkpoint with one completed sheet and one pending
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test-score",
            total_sheets=2,
            status=JobStatus.RUNNING,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
                2: SheetState(sheet_num=2, status=SheetStatus.PENDING),
            },
        )

        mock_sheet_1 = MagicMock()
        mock_sheet_1.num = 1
        mock_sheet_1.instrument_name = "claude-code"
        mock_sheet_1.movement = 1
        mock_sheet_2 = MagicMock()
        mock_sheet_2.num = 2
        mock_sheet_2.instrument_name = "claude-code"
        mock_sheet_2.movement = 1

        mock_config = MagicMock()
        mock_config.name = "test-score"
        mock_config.retry.max_retries = 3
        mock_config.cost_limits.enabled = False
        mock_config.cost_limits.max_cost_per_job = None
        mock_config.workspace = Path("/tmp/workspace")
        mock_config.parallel.enabled = False
        mock_config.cross_sheet = None

        # Mock _load_checkpoint to return our checkpoint
        manager._load_checkpoint = AsyncMock(return_value=checkpoint)

        with (
            patch("marianne.core.config.JobConfig.from_yaml", return_value=mock_config),
            patch("marianne.core.sheet.build_sheets", return_value=[mock_sheet_1, mock_sheet_2]),
            patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}),
        ):
            result = await JobManager._resume_via_baton(
                manager, "test-job", Path("/tmp/workspace"),
            )

        assert "test-job" in manager._live_states, (
            "F-255.2: _resume_via_baton must populate _live_states"
        )
        live = manager._live_states["test-job"]
        assert isinstance(live, CheckpointState)
        # The live state should reflect the recovered checkpoint
        assert live.sheets[1].status == SheetStatus.COMPLETED
        assert live.sheets[2].status == SheetStatus.PENDING
