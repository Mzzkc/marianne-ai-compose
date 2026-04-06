"""Tests for F-152 and F-145 — baton dispatch guards and concert chaining.

F-152 (P0): Unsupported instrument kind causes infinite silent dispatch loop.
  When backend_pool.acquire() fails (ValueError, NotImplementedError, etc.),
  the dispatch callback must fail the sheet via SheetAttemptResult, not
  silently return. Otherwise the sheet stays READY (infinite loop) or
  DISPATCHED (silent deadlock).

F-145 (P2): Baton path missing completed_new_work flag.
  _run_via_baton and _resume_via_baton must set meta.completed_new_work = True
  when the job completes new sheets, matching the monolithic execution path.
  Without this, concert chaining's zero-work guard breaks.

TDD: Tests written first (red), then implementation (green).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


# =========================================================================
# Helpers
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    workspace: str = "/tmp/test-ws",
    prompt: str = "test prompt",
    timeout: float = 60.0,
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=Path(workspace),
        instrument_name=instrument,
        prompt_template=prompt,
        timeout_seconds=timeout,
    )


def _make_execution_state(
    sheet_num: int = 1,
    instrument: str = "claude-code",
    max_retries: int = 3,
) -> SheetExecutionState:
    """Create a SheetExecutionState for testing."""
    return SheetExecutionState(
        sheet_num=sheet_num,
        instrument_name=instrument,
        max_retries=max_retries,
    )


def _drain_inbox(adapter: object) -> list[object]:
    """Drain all events from the baton inbox, return them."""
    events: list[object] = []
    while not adapter.baton.inbox.empty():  # type: ignore[union-attr]
        events.append(adapter.baton.inbox.get_nowait())  # type: ignore[union-attr]
    return events


# =========================================================================
# F-152: Dispatch guard — backend acquisition failure
# =========================================================================


class TestDispatchGuardBackendAcquireFailure:
    """When backend_pool.acquire() fails, the sheet must be marked FAILED
    via a SheetAttemptResult sent to the baton inbox — not silently dropped.

    Before the fix, two failure modes existed:
    1. ValueError/RuntimeError caught by adapter → sheet stuck in DISPATCHED
    2. NotImplementedError (unsupported kind) → sheet stuck in READY (infinite loop)
    """

    @pytest.mark.asyncio
    async def test_value_error_sends_failure_result(self) -> None:
        """ValueError from acquire() triggers a SheetAttemptResult failure event."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)  # Clear DispatchRetry from registration

        # Mock pool that raises ValueError (instrument not found)
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(
            side_effect=ValueError("Instrument 'bad-inst' not found in registry")
        )

        state = _make_execution_state(sheet_num=1)
        await adapter._dispatch_callback("test-job", 1, state)

        # No musician task should have been created
        assert len(adapter._active_tasks) == 0

        # A failure result should have been sent to the baton inbox
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.job_id == "test-job"
        assert event.sheet_num == 1
        assert event.execution_success is False
        assert event.error_classification is not None

    @pytest.mark.asyncio
    async def test_not_implemented_error_sends_failure_result(self) -> None:
        """NotImplementedError (unsupported kind) triggers a failure event."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, instrument="http-instrument")]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)  # Clear DispatchRetry from registration

        # Mock pool that raises NotImplementedError (unsupported kind)
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(
            side_effect=NotImplementedError(
                "HTTP instrument backends are not yet supported"
            )
        )

        state = _make_execution_state(sheet_num=1, instrument="http-instrument")
        await adapter._dispatch_callback("test-job", 1, state)

        # No musician task should have been created
        assert len(adapter._active_tasks) == 0

        # A failure result should have been sent to the baton inbox
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.job_id == "test-job"
        assert event.sheet_num == 1
        assert event.execution_success is False

    @pytest.mark.asyncio
    async def test_runtime_error_sends_failure_result(self) -> None:
        """RuntimeError (pool closed) triggers a failure event."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)  # Clear DispatchRetry from registration

        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(
            side_effect=RuntimeError("BackendPool is closed")
        )

        state = _make_execution_state(sheet_num=1)
        await adapter._dispatch_callback("test-job", 1, state)

        assert len(adapter._active_tasks) == 0
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.execution_success is False

    @pytest.mark.asyncio
    async def test_failure_result_includes_error_message(self) -> None:
        """The SheetAttemptResult error_message includes the original error."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)

        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(
            side_effect=ValueError("Instrument 'bogus' not found in registry")
        )

        state = _make_execution_state(sheet_num=1)
        await adapter._dispatch_callback("test-job", 1, state)

        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert "bogus" in (event.error_message or "")

    @pytest.mark.asyncio
    async def test_failure_result_includes_instrument_name(self) -> None:
        """The SheetAttemptResult instrument_name matches the failed sheet."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, instrument="gemini-cli")]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)

        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(
            side_effect=ValueError("Not found")
        )

        state = _make_execution_state(sheet_num=1, instrument="gemini-cli")
        await adapter._dispatch_callback("test-job", 1, state)

        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.instrument_name == "gemini-cli"

    @pytest.mark.asyncio
    async def test_no_backend_pool_sends_failure_result(self) -> None:
        """When backend_pool is None, dispatch sends a failure result."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)

        # Don't set backend_pool — it's None

        state = _make_execution_state(sheet_num=1)
        await adapter._dispatch_callback("test-job", 1, state)

        assert len(adapter._active_tasks) == 0
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.execution_success is False

    @pytest.mark.asyncio
    async def test_sheet_not_found_sends_failure_result(self) -> None:
        """When the sheet is not found, dispatch sends a failure result."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)

        adapter._backend_pool = MagicMock()

        # Request sheet 99 which doesn't exist
        state = _make_execution_state(sheet_num=99)
        await adapter._dispatch_callback("test-job", 99, state)

        assert len(adapter._active_tasks) == 0
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.execution_success is False
        assert event.sheet_num == 99

    @pytest.mark.asyncio
    async def test_failure_prevents_infinite_dispatch_loop(self) -> None:
        """After backend failure, the sheet should not stay in READY — it should
        progress to FAILED via the baton state machine, preventing re-dispatch."""
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.baton.dispatch import dispatch_ready

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})
        _drain_inbox(adapter)  # Clear DispatchRetry from registration

        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(
            side_effect=ValueError("Not found")
        )

        # First dispatch attempt — should send failure result
        config = adapter.baton.build_dispatch_config(max_concurrent_sheets=10)
        await dispatch_ready(adapter.baton, config, adapter._dispatch_callback)

        # Drain all events from inbox and find the SheetAttemptResult
        events = _drain_inbox(adapter)
        failure_events = [e for e in events if isinstance(e, SheetAttemptResult)]
        assert len(failure_events) == 1
        await adapter.baton.handle_event(failure_events[0])

        # After handling the failure, sheet should not be in READY
        # (it should be RETRY_SCHEDULED, FAILED, or similar — not stuck)
        sheet_state = adapter.baton.get_sheet_state("test-job", 1)
        assert sheet_state is not None
        assert sheet_state.status != BatonSheetStatus.READY


# =========================================================================
# F-145: completed_new_work flag in baton path
# =========================================================================


class TestCompletedNewWorkFlag:
    """The baton path must set meta.completed_new_work = True when a job
    completes sheets, matching the monolithic execution path.

    Without this flag, concert chaining's zero-work guard incorrectly
    aborts valid score chains that completed real work.
    """

    def test_has_completed_sheets_all_completed(self) -> None:
        """has_completed_sheets returns True when all sheets completed."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, dependencies={})

        job = adapter.baton._jobs.get("test-job")
        assert job is not None
        for s in job.sheets.values():
            s.status = BatonSheetStatus.COMPLETED

        assert adapter.has_completed_sheets("test-job") is True

    def test_has_completed_sheets_partial_success(self) -> None:
        """has_completed_sheets returns True when some sheets completed, some failed."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, dependencies={})

        job = adapter.baton._jobs.get("test-job")
        assert job is not None
        sheets_list = list(job.sheets.values())
        sheets_list[0].status = BatonSheetStatus.COMPLETED
        sheets_list[1].status = BatonSheetStatus.FAILED

        assert adapter.has_completed_sheets("test-job") is True

    def test_has_completed_sheets_none_completed(self) -> None:
        """has_completed_sheets returns False when no sheets completed."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, dependencies={})

        job = adapter.baton._jobs.get("test-job")
        assert job is not None
        for s in job.sheets.values():
            s.status = BatonSheetStatus.FAILED

        assert adapter.has_completed_sheets("test-job") is False

    def test_has_completed_sheets_unknown_job(self) -> None:
        """has_completed_sheets returns False for unregistered jobs."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        assert adapter.has_completed_sheets("nonexistent") is False

    def test_has_completed_sheets_all_skipped(self) -> None:
        """has_completed_sheets returns False when all sheets are skipped."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})

        job = adapter.baton._jobs.get("test-job")
        assert job is not None
        for s in job.sheets.values():
            s.status = BatonSheetStatus.SKIPPED

        assert adapter.has_completed_sheets("test-job") is False

    def test_completion_event_reports_success_correctly(self) -> None:
        """Adapter correctly distinguishes all-success from partial failure."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, dependencies={})

        job = adapter.baton._jobs.get("test-job")
        assert job is not None
        for s in job.sheets.values():
            s.status = BatonSheetStatus.COMPLETED

        adapter._check_completions()
        assert adapter._completion_results.get("test-job") is True

    def test_completion_event_reports_failure_correctly(self) -> None:
        """When some sheets fail, completion result is False."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, dependencies={})

        job = adapter.baton._jobs.get("test-job")
        assert job is not None
        sheets_list = list(job.sheets.values())
        sheets_list[0].status = BatonSheetStatus.COMPLETED
        sheets_list[1].status = BatonSheetStatus.FAILED

        adapter._check_completions()
        assert adapter._completion_results.get("test-job") is False
