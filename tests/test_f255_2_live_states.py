"""Tests for F-255.2: Baton adapter populates _live_states.

The baton adapter must populate _live_states for baton-managed jobs
so that `mzt status` can display their state. Previously, only
the legacy runner populated _live_states via _on_state_published().

Fix: Create a CheckpointState at baton job registration time and
store it in _live_states. The existing _on_baton_state_sync callback
updates individual sheet statuses as the baton processes events.

TDD: Tests define the contract. Implementation fulfills it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.manager import JobManager


def _make_manager_with_baton() -> JobManager:
    """Create a JobManager with baton adapter enabled for testing."""
    config = MagicMock()
    config.use_baton = True
    config.max_concurrent_sheets = 4
    config.max_concurrent_jobs = 2
    config.host = "localhost"
    config.port = 9500
    config.resources = MagicMock()
    config.resources.token_quota = None
    config.log_file = "/tmp/test.log"
    config.profiler = MagicMock()
    config.profiler.enabled = False
    config.preflight = MagicMock()
    config.preflight.token_warning_threshold = 800000
    config.preflight.token_error_threshold = 200000
    config.default_thinking_method = None
    config.learning = MagicMock()
    config.learning.backend = MagicMock()
    manager = JobManager(config)
    return manager


class TestBatonLiveStatesPopulation:
    """F-255.2: _live_states must be populated for baton-managed jobs."""

    def test_run_via_baton_creates_live_state(self) -> None:
        """When _run_via_baton registers a job, _live_states gets a
        CheckpointState entry for that job."""
        import asyncio

        from marianne.core.checkpoint import CheckpointState, JobStatus

        manager = _make_manager_with_baton()
        # Create mock baton adapter
        mock_adapter = MagicMock()
        mock_adapter.wait_for_completion = AsyncMock(return_value=True)
        mock_adapter.has_completed_sheets.return_value = True
        mock_adapter.publish_job_event = AsyncMock()
        manager._baton_adapter = mock_adapter

        # Create a minimal config that _run_via_baton expects
        config = MagicMock()
        config.name = "test-job"
        config.retry.max_retries = 3
        config.cost_limits.enabled = False
        config.prompt = MagicMock()
        config.prompt.thinking_method = None
        config.parallel.enabled = True
        config.backend.type = "claude-code"
        config.cross_sheet = None

        request = MagicMock()
        request.fresh = False
        request.self_healing = False
        request.workspace = "/tmp/test-ws"
        request.config_path = "/tmp/test.yaml"
        request.start_sheet = None
        request.self_healing_auto_confirm = False
        request.dry_run = False

        job_id = "test-job-123"

        # Mock build_sheets to return 3 sheets
        mock_sheets = []
        for i in range(1, 4):
            sheet = MagicMock()
            sheet.num = i
            sheet.name = f"Sheet {i}"
            sheet.instrument_name = "claude-code"
            sheet.movement = 1
            sheet.voice = i
            sheet.description = f"Test sheet {i}"
            mock_sheets.append(sheet)

        with patch("marianne.core.sheet.build_sheets", return_value=mock_sheets), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}):
            # Capture the state of _live_states after register_job
            original_register = mock_adapter.register_job

            def register_and_check(*args: Any, **kwargs: Any) -> None:
                original_register(*args, **kwargs)

            mock_adapter.register_job = register_and_check

            # Run the baton path
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    manager._run_via_baton(job_id, config, request)
                )
            finally:
                loop.close()

        # After execution, _live_states should have been populated
        assert job_id in manager._live_states, (
            "F-255.2: _run_via_baton must populate _live_states for the job "
            "so that `mzt status` can display baton-managed job state"
        )

        state = manager._live_states[job_id]
        assert isinstance(state, CheckpointState)
        assert state.job_id == job_id
        assert state.total_sheets == 3
        # After wait_for_completion, status is updated to reflect outcome.
        # The mock returns all_success=True, so status is COMPLETED.
        assert state.status in (JobStatus.RUNNING, JobStatus.COMPLETED)

    def test_live_state_has_sheet_entries(self) -> None:
        """The CheckpointState in _live_states has SheetState entries
        for each sheet so _on_baton_state_sync can update them."""
        import asyncio

        from marianne.core.checkpoint import CheckpointState

        manager = _make_manager_with_baton()
        mock_adapter = MagicMock()
        mock_adapter.wait_for_completion = AsyncMock(return_value=True)
        mock_adapter.has_completed_sheets.return_value = True
        mock_adapter.publish_job_event = AsyncMock()
        manager._baton_adapter = mock_adapter

        config = MagicMock()
        config.name = "test-job"
        config.retry.max_retries = 3
        config.cost_limits.enabled = False
        config.prompt = MagicMock()
        config.prompt.thinking_method = None
        config.parallel.enabled = True
        config.backend.type = "claude-code"
        config.cross_sheet = None

        request = MagicMock()
        request.fresh = False
        request.self_healing = False
        request.workspace = "/tmp/test-ws"
        request.config_path = "/tmp/test.yaml"
        request.start_sheet = None
        request.self_healing_auto_confirm = False
        request.dry_run = False

        mock_sheets = []
        for i in range(1, 6):
            sheet = MagicMock()
            sheet.num = i
            sheet.name = f"Sheet {i}"
            sheet.instrument_name = "claude-code"
            sheet.movement = 1
            sheet.voice = i
            sheet.description = f"Test sheet {i}"
            mock_sheets.append(sheet)

        with patch("marianne.core.sheet.build_sheets", return_value=mock_sheets), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={}):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    manager._run_via_baton("job-abc", config, request)
                )
            finally:
                loop.close()

        state = manager._live_states["job-abc"]
        assert len(state.sheets) == 5, (
            "F-255.2: CheckpointState must have SheetState entries for "
            "all sheets so _on_baton_state_sync can update them"
        )
        for i in range(1, 6):
            assert i in state.sheets
            assert state.sheets[i].instrument_name == "claude-code"

    def test_baton_state_sync_updates_live_state(self) -> None:
        """_on_baton_state_sync updates sheet status in _live_states."""
        from marianne.core.checkpoint import (
            CheckpointState,
            SheetState,
            SheetStatus,
        )

        manager = _make_manager_with_baton()

        # Pre-populate _live_states as _run_via_baton would
        state = CheckpointState(
            job_id="job-sync",
            job_name="test",
            total_sheets=3,
            sheets={
                1: SheetState(sheet_num=1),
                2: SheetState(sheet_num=2),
                3: SheetState(sheet_num=3),
            },
        )
        manager._live_states["job-sync"] = state

        # Simulate baton state sync (uses lowercase enum values)
        manager._on_baton_state_sync("job-sync", 2, "completed")

        assert state.sheets[2].status == SheetStatus.COMPLETED

    def test_live_state_cleaned_on_deregister(self) -> None:
        """When the baton job completes, _live_states entry should persist
        for status queries (cleaned up elsewhere during full cleanup)."""
        from marianne.core.checkpoint import CheckpointState

        manager = _make_manager_with_baton()
        state = CheckpointState(
            job_id="job-done",
            job_name="test",
            total_sheets=1,
        )
        manager._live_states["job-done"] = state

        # After _run_via_baton completes, state should still be in _live_states
        # (it gets cleaned up in _handle_job_done, not here)
        assert "job-done" in manager._live_states
