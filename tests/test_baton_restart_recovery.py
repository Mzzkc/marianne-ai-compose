"""Tests for Step 29 — Baton Restart Recovery.

When the conductor restarts, jobs that were running via the baton must be
recovered from their CheckpointState. This file tests:

1. BatonAdapter.recover_job() — rebuild baton state from checkpoint
2. Per-event state sync — baton events update CheckpointState
3. Manager integration — baton recovery during startup

TDD: Tests written first (red), then implementation (green).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import (
    BatonAdapter,
    checkpoint_to_baton_status,
    sheets_to_execution_states,
)
from marianne.daemon.baton.events import (
    DispatchRetry,
    SheetAttemptResult,
    SheetSkipped,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    SheetExecutionState,
)


# =========================================================================
# Fixtures
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    workspace: str = "/tmp/test-ws",
    prompt: str = "test prompt",
    timeout: float = 60.0,
    movement: int = 1,
    voice: int | None = None,
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=movement,
        voice=voice,
        voice_count=1,
        workspace=Path(workspace),
        instrument_name=instrument,
        prompt_template=prompt,
        timeout_seconds=timeout,
    )


def _make_checkpoint(
    job_id: str = "test-job",
    total_sheets: int = 5,
    sheet_statuses: dict[int, str] | None = None,
    sheet_attempts: dict[int, int] | None = None,
    sheet_completion_attempts: dict[int, int] | None = None,
) -> CheckpointState:
    """Create a CheckpointState with specified sheet statuses.

    Args:
        job_id: Job identifier.
        total_sheets: Total number of sheets.
        sheet_statuses: Map of sheet_num → status string. Defaults to all pending.
        sheet_attempts: Map of sheet_num → attempt_count.
        sheet_completion_attempts: Map of sheet_num → completion_attempts.
    """
    if sheet_statuses is None:
        sheet_statuses = {i: "pending" for i in range(1, total_sheets + 1)}
    if sheet_attempts is None:
        sheet_attempts = {}
    if sheet_completion_attempts is None:
        sheet_completion_attempts = {}

    sheets: dict[int, SheetState] = {}
    for num, status in sheet_statuses.items():
        sheets[num] = SheetState(
            sheet_num=num,
            status=SheetStatus(status),
            attempt_count=sheet_attempts.get(num, 0),
            completion_attempts=sheet_completion_attempts.get(num, 0),
        )

    return CheckpointState(
        job_id=job_id,
        job_name=job_id,
        total_sheets=total_sheets,
        sheets=sheets,
    )


def _make_sheets_list(count: int = 5, instrument: str = "claude-code") -> list[Sheet]:
    """Create a list of Sheet entities."""
    return [_make_sheet(num=i, instrument=instrument) for i in range(1, count + 1)]


def _make_simple_deps(count: int = 5) -> dict[int, list[int]]:
    """Create a simple linear dependency chain: 1→2→3→4→5."""
    deps: dict[int, list[int]] = {1: []}
    for i in range(2, count + 1):
        deps[i] = [i - 1]
    return deps


# =========================================================================
# Part 1: BatonAdapter.recover_job() — rebuild from checkpoint
# =========================================================================


class TestRecoverJobRegistration:
    """Test that recover_job() correctly re-registers a job with the baton."""

    def test_recover_job_exists(self) -> None:
        """recover_job() method exists on BatonAdapter."""
        adapter = BatonAdapter()
        assert hasattr(adapter, "recover_job")

    def test_recover_registers_job_with_baton(self) -> None:
        """recover_job() registers sheets with the baton core."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "completed", 2: "in_progress", 3: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        # Job should be registered with baton
        assert adapter.baton._jobs.get("test-job") is not None

    def test_recover_stores_sheet_entities(self) -> None:
        """recover_job() stores Sheet entities for prompt rendering."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(total_sheets=3)

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        # Should be able to retrieve sheets
        assert adapter.get_sheet("test-job", 1) is not None
        assert adapter.get_sheet("test-job", 2) is not None
        assert adapter.get_sheet("test-job", 3) is not None

    def test_recover_creates_completion_event(self) -> None:
        """recover_job() creates a completion event for wait_for_completion()."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        checkpoint = _make_checkpoint(total_sheets=2)

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        # Completion event should exist
        assert "test-job" in adapter._completion_events


class TestRecoverJobStatusMapping:
    """Test that recover_job() correctly maps checkpoint statuses to baton statuses."""

    def test_completed_sheets_stay_completed(self) -> None:
        """Completed sheets remain COMPLETED in the baton."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "completed", 2: "pending", 3: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.COMPLETED

    def test_failed_sheets_stay_failed(self) -> None:
        """Failed sheets remain FAILED in the baton."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "failed", 2: "pending", 3: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.FAILED

    def test_skipped_sheets_stay_skipped(self) -> None:
        """Skipped sheets remain SKIPPED in the baton."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "skipped", 2: "pending", 3: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.SKIPPED

    def test_pending_sheets_stay_pending(self) -> None:
        """Pending sheets remain PENDING in the baton."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "pending", 2: "pending", 3: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.PENDING

    def test_in_progress_sheets_reset_to_pending(self) -> None:
        """In-progress sheets are reset to PENDING — their musician died."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "completed", 2: "in_progress", 3: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 2)
        assert state is not None
        # in_progress → PENDING because the executing musician was killed
        assert state.status == BatonSheetStatus.PENDING


class TestRecoverJobAttemptPreservation:
    """Test that recover_job() preserves attempt counts from the checkpoint."""

    def test_attempt_count_preserved(self) -> None:
        """Normal attempt count carries forward from checkpoint."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        checkpoint = _make_checkpoint(
            total_sheets=2,
            sheet_statuses={1: "in_progress", 2: "pending"},
            sheet_attempts={1: 3},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        # Attempt count should be carried forward
        assert state.normal_attempts == 3

    def test_completion_attempts_preserved(self) -> None:
        """Completion attempt count carries forward from checkpoint."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        checkpoint = _make_checkpoint(
            total_sheets=2,
            sheet_statuses={1: "in_progress", 2: "pending"},
            sheet_attempts={1: 1},
            sheet_completion_attempts={1: 2},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.completion_attempts == 2

    def test_zero_attempts_for_pending_sheets(self) -> None:
        """Pending sheets that never executed have zero attempts."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        checkpoint = _make_checkpoint(
            total_sheets=2,
            sheet_statuses={1: "completed", 2: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 2)
        assert state is not None
        assert state.normal_attempts == 0
        assert state.completion_attempts == 0


class TestRecoverJobAlreadyComplete:
    """Test recover_job() behavior when all sheets are terminal."""

    def test_all_completed_signals_immediately(self) -> None:
        """When all sheets are already complete, completion event fires."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "completed", 2: "completed", 3: "completed"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        # After recovery check, all terminal → completion should be detectable
        assert adapter.baton.is_job_complete("test-job")

    def test_mixed_terminal_signals_completion(self) -> None:
        """Mix of completed/failed/skipped = still complete."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [2]}
        checkpoint = _make_checkpoint(
            total_sheets=3,
            sheet_statuses={1: "completed", 2: "failed", 3: "skipped"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        assert adapter.baton.is_job_complete("test-job")


class TestRecoverJobCostLimit:
    """Test that recover_job() restores cost limits."""

    def test_cost_limit_restored(self) -> None:
        """Cost limit from config is applied on recovery."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        checkpoint = _make_checkpoint(total_sheets=2)

        adapter.recover_job(
            "test-job", sheets, deps, checkpoint,
            max_cost_usd=10.0,
        )

        assert adapter.baton._job_cost_limits.get("test-job") == 10.0


class TestRecoverJobDispatchKick:
    """Test that recover_job() kicks dispatch for ready sheets."""

    def test_dispatch_retry_enqueued(self) -> None:
        """recover_job() puts DispatchRetry on inbox to start execution."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        checkpoint = _make_checkpoint(
            total_sheets=2,
            sheet_statuses={1: "pending", 2: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        # Inbox should have a DispatchRetry event
        assert not adapter.baton.inbox.empty()
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, DispatchRetry)


# =========================================================================
# Part 2: Per-event state sync — baton events → CheckpointState
# =========================================================================


class TestStateSyncCallback:
    """Test that the adapter can sync baton state changes to CheckpointState."""

    def test_state_sync_callback_accepted(self) -> None:
        """BatonAdapter accepts a state_sync_callback parameter."""
        callback = MagicMock()
        adapter = BatonAdapter(state_sync_callback=callback)
        assert adapter._state_sync_callback is callback

    def test_state_sync_called_on_attempt_result(self) -> None:
        """state_sync_callback is called when processing SheetAttemptResult."""
        callback = MagicMock()
        adapter = BatonAdapter(state_sync_callback=callback)
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        adapter.register_job("test-job", sheets, deps)

        # Simulate a successful attempt result
        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            attempt=1,
            instrument_name="claude-code",
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=0.05,
            duration_seconds=10.0,
        )

        # Process the event through the baton + adapter sync
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.baton.handle_event(result))
            # Call _sync_sheet_status as the main loop would
            adapter._sync_sheet_status(result)
        finally:
            loop.close()

        # Callback should have been called with job_id, sheet_num, new_status
        callback.assert_called()
        call_args = callback.call_args
        assert call_args[0][0] == "test-job"  # job_id
        assert call_args[0][1] == 1  # sheet_num

    def test_sync_maps_completed_status(self) -> None:
        """Sync callback receives correct status for completed sheets."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(1)
        deps = {1: []}
        adapter.register_job("test-job", sheets, deps)

        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            attempt=1,
            instrument_name="claude-code",
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=0.05,
            duration_seconds=10.0,
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.baton.handle_event(result))
            adapter._sync_sheet_status(result)
        finally:
            loop.close()

        # Should have synced with "completed" status
        assert len(captured) >= 1
        assert captured[-1][2] == "completed"

    def test_sync_not_called_without_callback(self) -> None:
        """No crash when state_sync_callback is None."""
        adapter = BatonAdapter()  # No callback
        sheets = _make_sheets_list(1)
        deps = {1: []}
        adapter.register_job("test-job", sheets, deps)

        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            attempt=1,
            instrument_name="claude-code",
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=0.05,
            duration_seconds=10.0,
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.baton.handle_event(result))
            # Should not crash even without a callback
            adapter._sync_sheet_status(result)
        finally:
            loop.close()

    def test_sync_on_skipped_event(self) -> None:
        """Sync callback fires for SheetSkipped events."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}
        adapter.register_job("test-job", sheets, deps)

        skip_event = SheetSkipped(
            job_id="test-job",
            sheet_num=1,
            reason="dependency failed",
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.baton.handle_event(skip_event))
            adapter._sync_sheet_status(skip_event)
        finally:
            loop.close()

        assert len(captured) >= 1
        assert captured[-1][2] == "skipped"

    def test_sync_callback_exception_does_not_crash(self) -> None:
        """If sync callback raises, the adapter logs but doesn't crash."""
        def bad_callback(job_id: str, sheet_num: int, status: str) -> None:
            raise ValueError("callback error")

        adapter = BatonAdapter(state_sync_callback=bad_callback)
        sheets = _make_sheets_list(1)
        deps = {1: []}
        adapter.register_job("test-job", sheets, deps)

        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            attempt=1,
            instrument_name="claude-code",
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=0.05,
            duration_seconds=10.0,
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.baton.handle_event(result))
            # Should not raise — exception caught internally
            adapter._sync_sheet_status(result)
        finally:
            loop.close()


# =========================================================================
# Part 3: Recovery status mapping edge cases
# =========================================================================


class TestRecoveryStatusMapping:
    """Edge cases in checkpoint-to-baton status mapping during recovery."""

    def test_unknown_status_raises(self) -> None:
        """Unknown checkpoint status raises KeyError."""
        with pytest.raises(KeyError):
            checkpoint_to_baton_status("unknown_status")

    def test_all_checkpoint_statuses_mapped(self) -> None:
        """Every SheetStatus value has a mapping to BatonSheetStatus."""
        for status in SheetStatus:
            # All should be mappable without KeyError
            baton_status = checkpoint_to_baton_status(status.value)
            assert isinstance(baton_status, BatonSheetStatus)

    def test_recovery_preserves_instrument_name(self) -> None:
        """Recovered sheets use the Sheet entity's instrument, not checkpoint."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, instrument="gemini-cli")]
        deps = {1: []}
        checkpoint = _make_checkpoint(
            total_sheets=1,
            sheet_statuses={1: "pending"},
        )

        adapter.recover_job("test-job", sheets, deps, checkpoint)

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.instrument_name == "gemini-cli"

    def test_recovery_with_max_retries(self) -> None:
        """Max retries parameter is passed through on recovery."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(1)
        deps = {1: []}
        checkpoint = _make_checkpoint(total_sheets=1)

        adapter.recover_job(
            "test-job", sheets, deps, checkpoint,
            max_retries=7,
        )

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.max_retries == 7

    def test_recovery_exhausted_retries_stay_failed(self) -> None:
        """A sheet that exhausted retries and failed stays FAILED."""
        adapter = BatonAdapter()
        sheets = _make_sheets_list(1)
        deps = {1: []}
        checkpoint = _make_checkpoint(
            total_sheets=1,
            sheet_statuses={1: "failed"},
            sheet_attempts={1: 5},
        )

        adapter.recover_job(
            "test-job", sheets, deps, checkpoint,
            max_retries=3,
        )

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.FAILED
        assert state.normal_attempts == 5
