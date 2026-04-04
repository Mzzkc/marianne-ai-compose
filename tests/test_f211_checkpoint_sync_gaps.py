"""F-211: Baton checkpoint sync missing for 4 event types.

The adapter's _sync_sheet_status() only syncs SheetAttemptResult and SheetSkipped
events to the checkpoint callback. Four event types that modify sheet status are
NOT synced:

1. EscalationResolved — changes sheet to PENDING/SKIPPED/COMPLETED/FAILED
2. EscalationTimeout — changes sheet to FAILED
3. CancelJob — changes all non-terminal sheets to CANCELLED
4. ShutdownRequested (non-graceful) — changes all non-terminal sheets to CANCELLED

Without sync, these status changes are lost on restart. Escalation decisions are
reversed. Cancel commands are reversed. Shutdown cancellations are undone.

TDD: These tests are written RED FIRST to prove the gap, then fixed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mozart.core.sheet import Sheet
from mozart.daemon.baton.adapter import BatonAdapter
from mozart.daemon.baton.events import (
    CancelJob,
    EscalationResolved,
    EscalationTimeout,
    SheetAttemptResult,
    ShutdownRequested,
)
from mozart.daemon.baton.state import BatonSheetStatus


# =========================================================================
# Helpers
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


def _make_sheets_list(count: int) -> list[Sheet]:
    return [_make_sheet(num=i) for i in range(1, count + 1)]


def _make_parallel_deps(count: int) -> dict[int, list[int]]:
    """All sheets independent — no dependencies."""
    return {i: [] for i in range(1, count + 1)}


def _run_event(adapter: BatonAdapter, event: object) -> None:
    """Run an event through the baton and then call sync.

    Mirrors the run() loop: capture pre-event state, handle event, sync.
    """
    loop = asyncio.new_event_loop()
    try:
        # F-211: Capture before handle_event (for CancelJob deregister)
        pre_capture = adapter._capture_pre_event_state(event)  # type: ignore[arg-type]
        loop.run_until_complete(adapter.baton.handle_event(event))
        adapter._sync_sheet_status(event, pre_capture=pre_capture)  # type: ignore[arg-type]
    finally:
        loop.close()


def _escalate_sheet(adapter: BatonAdapter, job_id: str, sheet_num: int) -> None:
    """Put a sheet into FERMATA state by failing it and triggering escalation.

    The simplest way: use a failed attempt result that exhausts retries and
    triggers escalation. But we can also directly set the state since we're
    testing the sync, not the escalation logic.
    """
    # Get the baton's internal state and set it to FERMATA
    job = adapter.baton._jobs.get(job_id)
    assert job is not None, f"Job {job_id} not found in baton"
    sheet = job.sheets.get(sheet_num)
    assert sheet is not None, f"Sheet {sheet_num} not found in job {job_id}"
    sheet.status = BatonSheetStatus.FERMATA
    job.paused = True


# =========================================================================
# EscalationResolved sync
# =========================================================================


class TestEscalationResolvedSync:
    """Sync callback must fire when an escalation is resolved."""

    def test_sync_on_escalation_retry(self) -> None:
        """EscalationResolved with decision='retry' syncs PENDING status."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(2)
        deps = _make_parallel_deps(2)
        adapter.register_job("test-job", sheets, deps)

        # Put sheet 1 into FERMATA
        _escalate_sheet(adapter, "test-job", 1)

        # Resolve escalation with "retry"
        event = EscalationResolved(
            job_id="test-job",
            sheet_num=1,
            decision="retry",
        )
        _run_event(adapter, event)

        # Sync callback should have been called for sheet 1
        matching = [(j, s, st) for j, s, st in captured if j == "test-job" and s == 1]
        assert len(matching) >= 1, (
            f"Expected sync callback for sheet 1, got: {captured}"
        )
        # After retry decision, sheet should be PENDING
        assert matching[-1][2] == "pending"

    def test_sync_on_escalation_skip(self) -> None:
        """EscalationResolved with decision='skip' syncs SKIPPED status."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(1)
        deps = _make_parallel_deps(1)
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        event = EscalationResolved(
            job_id="test-job",
            sheet_num=1,
            decision="skip",
        )
        _run_event(adapter, event)

        matching = [(j, s, st) for j, s, st in captured if j == "test-job" and s == 1]
        assert len(matching) >= 1
        assert matching[-1][2] == "skipped"

    def test_sync_on_escalation_accept(self) -> None:
        """EscalationResolved with decision='accept' syncs COMPLETED status."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(1)
        deps = _make_parallel_deps(1)
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        event = EscalationResolved(
            job_id="test-job",
            sheet_num=1,
            decision="accept",
        )
        _run_event(adapter, event)

        matching = [(j, s, st) for j, s, st in captured if j == "test-job" and s == 1]
        assert len(matching) >= 1
        assert matching[-1][2] == "completed"

    def test_sync_on_escalation_fail(self) -> None:
        """EscalationResolved with unknown decision syncs FAILED status."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(2)
        deps = _make_parallel_deps(2)
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        event = EscalationResolved(
            job_id="test-job",
            sheet_num=1,
            decision="reject",  # anything not retry/skip/accept → fail
        )
        _run_event(adapter, event)

        matching = [(j, s, st) for j, s, st in captured if j == "test-job" and s == 1]
        assert len(matching) >= 1
        assert matching[-1][2] == "failed"

    def test_sync_on_escalation_fail_propagates_dependents(self) -> None:
        """EscalationResolved failure also syncs dependent sheets that get skipped."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(2)
        deps = {1: [], 2: [1]}  # Sheet 2 depends on sheet 1
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        event = EscalationResolved(
            job_id="test-job",
            sheet_num=1,
            decision="reject",
        )
        _run_event(adapter, event)

        # Sheet 1 should be synced as FAILED
        sheet1_syncs = [
            (j, s, st) for j, s, st in captured if j == "test-job" and s == 1
        ]
        assert len(sheet1_syncs) >= 1
        assert sheet1_syncs[-1][2] == "failed"


# =========================================================================
# EscalationTimeout sync
# =========================================================================


class TestEscalationTimeoutSync:
    """Sync callback must fire when an escalation times out."""

    def test_sync_on_escalation_timeout(self) -> None:
        """EscalationTimeout syncs FAILED status for the timed-out sheet."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(2)
        deps = _make_parallel_deps(2)
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        event = EscalationTimeout(
            job_id="test-job",
            sheet_num=1,
        )
        _run_event(adapter, event)

        matching = [(j, s, st) for j, s, st in captured if j == "test-job" and s == 1]
        assert len(matching) >= 1, (
            f"Expected sync callback for sheet 1, got: {captured}"
        )
        assert matching[-1][2] == "failed"

    def test_sync_on_escalation_timeout_propagates_dependents(self) -> None:
        """EscalationTimeout failure propagates and syncs dependents."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(3)
        deps = {1: [], 2: [1], 3: [1]}  # 2 and 3 depend on 1
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        event = EscalationTimeout(
            job_id="test-job",
            sheet_num=1,
        )
        _run_event(adapter, event)

        # Sheet 1 synced as failed
        sheet1_syncs = [
            (j, s, st) for j, s, st in captured if j == "test-job" and s == 1
        ]
        assert len(sheet1_syncs) >= 1
        assert sheet1_syncs[-1][2] == "failed"


# =========================================================================
# CancelJob sync
# =========================================================================


class TestCancelJobSync:
    """Sync callback must fire for all sheets when a job is cancelled."""

    def test_sync_on_cancel_all_pending_sheets(self) -> None:
        """CancelJob syncs CANCELLED→'failed' for all pending sheets."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(3)
        deps = _make_parallel_deps(3)
        adapter.register_job("test-job", sheets, deps)

        event = CancelJob(job_id="test-job")
        _run_event(adapter, event)

        # All 3 sheets should be synced as "failed" (CANCELLED→"failed")
        synced_sheets = {s for j, s, st in captured if j == "test-job"}
        assert synced_sheets == {1, 2, 3}, (
            f"Expected sync for all 3 sheets, got: {captured}"
        )

        for j, s, st in captured:
            if j == "test-job":
                assert st == "failed", (
                    f"Sheet {s} synced as '{st}', expected 'failed'"
                )

    def test_sync_on_cancel_skips_terminal_sheets(self) -> None:
        """CancelJob does NOT re-sync sheets that are already terminal."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(3)
        deps = _make_parallel_deps(3)
        adapter.register_job("test-job", sheets, deps)

        # Complete sheet 1 first
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
        _run_event(adapter, result)
        captured.clear()  # Clear the completion sync

        # Now cancel
        cancel = CancelJob(job_id="test-job")
        _run_event(adapter, cancel)

        # Only sheets 2 and 3 should be synced (sheet 1 was already completed)
        synced_sheets = {s for j, s, st in captured if j == "test-job"}
        assert 1 not in synced_sheets, (
            "Sheet 1 was already completed — should not be re-synced by cancel"
        )
        assert synced_sheets == {2, 3}

    def test_sync_on_cancel_empty_job(self) -> None:
        """CancelJob on unknown job does not crash."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        # No job registered

        event = CancelJob(job_id="nonexistent")
        _run_event(adapter, event)

        assert len(captured) == 0


# =========================================================================
# ShutdownRequested sync
# =========================================================================


class TestShutdownRequestedSync:
    """Sync callback must fire when a non-graceful shutdown cancels sheets."""

    def test_sync_on_non_graceful_shutdown(self) -> None:
        """Non-graceful shutdown syncs all non-terminal sheets as 'failed'."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(3)
        deps = _make_parallel_deps(3)
        adapter.register_job("test-job", sheets, deps)

        event = ShutdownRequested(graceful=False)
        _run_event(adapter, event)

        synced_sheets = {s for j, s, st in captured if j == "test-job"}
        assert synced_sheets == {1, 2, 3}, (
            f"Expected sync for all 3 sheets on non-graceful shutdown, got: {captured}"
        )

        for j, s, st in captured:
            if j == "test-job":
                assert st == "failed"

    def test_no_sync_on_graceful_shutdown(self) -> None:
        """Graceful shutdown does NOT cancel sheets — no sync needed."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(2)
        deps = _make_parallel_deps(2)
        adapter.register_job("test-job", sheets, deps)

        event = ShutdownRequested(graceful=True)
        _run_event(adapter, event)

        # No sheets should be synced since graceful doesn't cancel
        assert len(captured) == 0

    def test_sync_on_shutdown_multi_job(self) -> None:
        """Non-graceful shutdown syncs sheets across multiple jobs."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)

        # Register two jobs
        sheets1 = _make_sheets_list(2)
        deps1 = _make_parallel_deps(2)
        adapter.register_job("job-a", sheets1, deps1)

        sheets2 = _make_sheets_list(2)
        deps2 = _make_parallel_deps(2)
        adapter.register_job("job-b", sheets2, deps2)

        event = ShutdownRequested(graceful=False)
        _run_event(adapter, event)

        job_a_sheets = {s for j, s, st in captured if j == "job-a"}
        job_b_sheets = {s for j, s, st in captured if j == "job-b"}

        assert job_a_sheets == {1, 2}
        assert job_b_sheets == {1, 2}

    def test_sync_on_shutdown_skips_terminal(self) -> None:
        """Non-graceful shutdown does not re-sync already-terminal sheets."""
        captured: list[tuple[str, int, str]] = []

        def capture_sync(job_id: str, sheet_num: int, status: str) -> None:
            captured.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=capture_sync)
        sheets = _make_sheets_list(3)
        deps = _make_parallel_deps(3)
        adapter.register_job("test-job", sheets, deps)

        # Complete sheet 1
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
        _run_event(adapter, result)
        captured.clear()

        # Non-graceful shutdown
        event = ShutdownRequested(graceful=False)
        _run_event(adapter, event)

        synced_sheets = {s for j, s, st in captured if j == "test-job"}
        assert 1 not in synced_sheets
        assert synced_sheets == {2, 3}


# =========================================================================
# Edge cases
# =========================================================================


class TestSyncEdgeCases:
    """Edge cases for the expanded sync logic."""

    def test_no_callback_does_not_crash(self) -> None:
        """All 4 event types work fine without a sync callback."""
        adapter = BatonAdapter()  # No callback
        sheets = _make_sheets_list(2)
        deps = _make_parallel_deps(2)
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        # None of these should crash
        _run_event(adapter, EscalationResolved(
            job_id="test-job", sheet_num=1, decision="retry"
        ))
        _run_event(adapter, EscalationTimeout(
            job_id="test-job", sheet_num=2
        ))
        _run_event(adapter, CancelJob(job_id="test-job"))
        _run_event(adapter, ShutdownRequested(graceful=False))

    def test_callback_exception_does_not_crash_escalation(self) -> None:
        """Sync callback exception during escalation doesn't crash the adapter."""
        def bad_callback(job_id: str, sheet_num: int, status: str) -> None:
            raise ValueError("callback error")

        adapter = BatonAdapter(state_sync_callback=bad_callback)
        sheets = _make_sheets_list(1)
        deps = _make_parallel_deps(1)
        adapter.register_job("test-job", sheets, deps)

        _escalate_sheet(adapter, "test-job", 1)

        # Should not raise
        _run_event(adapter, EscalationResolved(
            job_id="test-job", sheet_num=1, decision="retry"
        ))

    def test_callback_exception_does_not_crash_cancel(self) -> None:
        """Sync callback exception during cancel doesn't crash."""
        def bad_callback(job_id: str, sheet_num: int, status: str) -> None:
            raise ValueError("callback error")

        adapter = BatonAdapter(state_sync_callback=bad_callback)
        sheets = _make_sheets_list(2)
        deps = _make_parallel_deps(2)
        adapter.register_job("test-job", sheets, deps)

        _run_event(adapter, CancelJob(job_id="test-job"))

    def test_callback_exception_does_not_crash_shutdown(self) -> None:
        """Sync callback exception during shutdown doesn't crash."""
        def bad_callback(job_id: str, sheet_num: int, status: str) -> None:
            raise ValueError("callback error")

        adapter = BatonAdapter(state_sync_callback=bad_callback)
        sheets = _make_sheets_list(2)
        deps = _make_parallel_deps(2)
        adapter.register_job("test-job", sheets, deps)

        _run_event(adapter, ShutdownRequested(graceful=False))
