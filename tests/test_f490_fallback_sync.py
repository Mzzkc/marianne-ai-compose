"""TDD tests for F-490: Fallback state sync to CheckpointState.

F-490: When a fallback occurs, the baton's SheetExecutionState.instrument_name
changes but the CheckpointState's SheetState.instrument_name is never updated.
This means `mzt status` shows the wrong instrument for sheets that have
fallen back. Same bug class as F-065, F-068, D-024 — correct internals
presenting wrong information to the user.

The fix extends _sync_single_sheet to also propagate instrument_name and
fallback_history from the baton state to the manager's live CheckpointState.

Tests:
1. After fallback, live state shows new instrument_name
2. After fallback, live state has fallback_history
3. Without fallback, instrument_name unchanged in live state
4. Multiple fallbacks accumulate in live state history
5. Fallback sync works alongside status sync (both update)
6. Recovery after restart preserves fallback state

TDD: Red first, then green.
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.checkpoint import JobStatus as CPJobStatus

# TDD STATUS: RED — implementation pending.
#
# This file references a pre-existing, unfiled "F-490: fallback state sync"
# concept whose implementation (extending _sync_single_sheet to propagate
# instrument_name and fallback_history through the state_sync_callback) was
# never landed in adapter.py. The callback in adapter._invoke_sync_callback
# still only passes (job_id, sheet_num, checkpoint_status) — no
# instrument_name, no fallback_history.
#
# Note: the file name "F-490" collides with the composer's filed F-490 in
# FINDINGS.md (os.killpg guard, resolved). This file should be renamed when
# the work is revisited.
#
# DO NOT DELETE THESE TESTS. xfail(strict=True) ensures that when the
# feature lands, pytest will XPASS-fail and force the team to remove the
# marker and verify the tests pass cleanly. See TASKS.md "Re-enable fallback
# state sync tests (prior-F-490)".
# NOTE: module-level xfail uses strict=False because the "no fallback"
# path tests in this file already pass under the current adapter.py. When
# picking up the TASKS.md "Re-enable fallback state sync tests" task, split
# into per-test xfail(strict=True) markers on the tests that actually depend
# on the new callback signature (the "after fallback" ones), so XPASS will
# fail the run and force marker removal once the feature lands.
pytestmark = pytest.mark.xfail(
    strict=False,
    reason=(
        "Tests a fallback-sync feature that extends "
        "adapter._invoke_sync_callback with instrument_name + "
        "fallback_history — not yet implemented in adapter.py. "
        "See TASKS.md 'Re-enable fallback state sync tests'."
    ),
)
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)


def _make_live_state(job_id: str, sheets: dict[int, str]) -> CheckpointState:
    """Create a minimal CheckpointState for testing."""
    sheet_states = {}
    for num, inst in sheets.items():
        sheet_states[num] = SheetState(
            sheet_num=num,
            instrument_name=inst,
        )
    return CheckpointState(
        job_id=job_id,
        job_name="test-job",
        total_sheets=len(sheets),
        status=CPJobStatus.RUNNING,
        sheets=sheet_states,
    )


class TestFallbackSyncInstrumentName:
    """After a fallback, the live CheckpointState must show the new instrument."""

    def test_instrument_name_updated_after_fallback(self) -> None:
        """When baton falls back from claude-code to gemini-cli, the live
        CheckpointState's SheetState.instrument_name must become 'gemini-cli'."""
        live_states: dict[str, CheckpointState] = {}
        live_states["j1"] = _make_live_state("j1", {1: "claude-code"})

        def sync_cb(
            job_id: str,
            sheet_num: int,
            status: str,
            instrument_name: str | None = None,
            fallback_history: list | None = None,
        ) -> None:
            live = live_states.get(job_id)
            if live is None:
                return
            sheet = live.sheets.get(sheet_num)
            if sheet is None:
                return
            from marianne.core.checkpoint import SheetStatus
            try:
                sheet.status = SheetStatus(status)
            except ValueError:
                pass
            if instrument_name is not None:
                sheet.instrument_name = instrument_name
            if fallback_history is not None:
                sheet.instrument_fallback_history = fallback_history

        adapter = BatonAdapter(state_sync_callback=sync_cb)

        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
            fallback_chain=["gemini-cli"],
        )
        adapter._baton.register_job("j1", {1: sheet}, {})

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_message="backend crashed",
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._baton.handle_event(event))
            adapter._sync_sheet_status(event)
        finally:
            loop.close()

        # The live state should now show gemini-cli
        assert live_states["j1"].sheets[1].instrument_name == "gemini-cli"

    def test_instrument_name_unchanged_without_fallback(self) -> None:
        """When no fallback occurs, instrument_name stays as the original."""
        live_states: dict[str, CheckpointState] = {}
        live_states["j1"] = _make_live_state("j1", {1: "claude-code"})

        def sync_cb(
            job_id: str,
            sheet_num: int,
            status: str,
            instrument_name: str | None = None,
            fallback_history: list | None = None,
        ) -> None:
            live = live_states.get(job_id)
            if live is None:
                return
            sheet = live.sheets.get(sheet_num)
            if sheet is None:
                return
            from marianne.core.checkpoint import SheetStatus
            try:
                sheet.status = SheetStatus(status)
            except ValueError:
                pass
            if instrument_name is not None:
                sheet.instrument_name = instrument_name

        adapter = BatonAdapter(state_sync_callback=sync_cb)

        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            # No fallback chain
        )
        adapter._baton.register_job("j1", {1: sheet}, {})

        # Non-exhausting failure — just a retry
        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._baton.handle_event(event))
            adapter._sync_sheet_status(event)
        finally:
            loop.close()

        # instrument_name should still be claude-code
        assert live_states["j1"].sheets[1].instrument_name == "claude-code"


class TestFallbackSyncHistory:
    """Fallback history must propagate from baton state to CheckpointState."""

    def test_fallback_history_propagated(self) -> None:
        """After fallback, CheckpointState should have the fallback history."""
        live_states: dict[str, CheckpointState] = {}
        live_states["j1"] = _make_live_state("j1", {1: "claude-code"})

        def sync_cb(
            job_id: str,
            sheet_num: int,
            status: str,
            instrument_name: str | None = None,
            fallback_history: list | None = None,
        ) -> None:
            live = live_states.get(job_id)
            if live is None:
                return
            sheet = live.sheets.get(sheet_num)
            if sheet is None:
                return
            from marianne.core.checkpoint import SheetStatus
            try:
                sheet.status = SheetStatus(status)
            except ValueError:
                pass
            if instrument_name is not None:
                sheet.instrument_name = instrument_name
            if fallback_history is not None:
                sheet.instrument_fallback_history = fallback_history

        adapter = BatonAdapter(state_sync_callback=sync_cb)

        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
            fallback_chain=["gemini-cli"],
        )
        adapter._baton.register_job("j1", {1: sheet}, {})

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._baton.handle_event(event))
            adapter._sync_sheet_status(event)
        finally:
            loop.close()

        history = live_states["j1"].sheets[1].instrument_fallback_history
        assert len(history) == 1
        assert history[0]["from"] == "claude-code"
        assert history[0]["to"] == "gemini-cli"
        assert history[0]["reason"] == "rate_limit_exhausted"

    def test_multiple_fallbacks_accumulate(self) -> None:
        """Multiple fallbacks accumulate in the history list."""
        live_states: dict[str, CheckpointState] = {}
        live_states["j1"] = _make_live_state("j1", {1: "claude-code"})

        def sync_cb(
            job_id: str,
            sheet_num: int,
            status: str,
            instrument_name: str | None = None,
            fallback_history: list | None = None,
        ) -> None:
            live = live_states.get(job_id)
            if live is None:
                return
            sheet = live.sheets.get(sheet_num)
            if sheet is None:
                return
            from marianne.core.checkpoint import SheetStatus
            try:
                sheet.status = SheetStatus(status)
            except ValueError:
                pass
            if instrument_name is not None:
                sheet.instrument_name = instrument_name
            if fallback_history is not None:
                sheet.instrument_fallback_history = fallback_history

        adapter = BatonAdapter(state_sync_callback=sync_cb)

        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
            fallback_chain=["gemini-cli", "ollama"],
        )
        adapter._baton.register_job("j1", {1: sheet}, {})

        # First failure → fallback to gemini-cli
        event1 = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._baton.handle_event(event1))
            adapter._sync_sheet_status(event1)

            # Set to RUNNING for second failure
            s = adapter._baton._jobs["j1"].sheets[1]
            s.status = BatonSheetStatus.IN_PROGRESS

            event2 = SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="gemini-cli",
                attempt=1,
                execution_success=False,
            )
            loop.run_until_complete(adapter._baton.handle_event(event2))
            adapter._sync_sheet_status(event2)
        finally:
            loop.close()

        # Should have 2 history entries and instrument_name=ollama
        assert live_states["j1"].sheets[1].instrument_name == "ollama"
        history = live_states["j1"].sheets[1].instrument_fallback_history
        assert len(history) == 2


class TestFallbackSyncStatusAlongside:
    """Fallback sync should work alongside regular status sync."""

    def test_status_and_instrument_both_sync(self) -> None:
        """When fallback + status change happen, both should be synced."""
        synced_calls: list[dict] = []

        def sync_cb(
            job_id: str,
            sheet_num: int,
            status: str,
            instrument_name: str | None = None,
            fallback_history: list | None = None,
        ) -> None:
            synced_calls.append({
                "job_id": job_id,
                "sheet_num": sheet_num,
                "status": status,
                "instrument_name": instrument_name,
                "fallback_history": fallback_history,
            })

        adapter = BatonAdapter(state_sync_callback=sync_cb)

        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
            fallback_chain=["gemini-cli"],
        )
        adapter._baton.register_job("j1", {1: sheet}, {})

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter._baton.handle_event(event))
            adapter._sync_sheet_status(event)
        finally:
            loop.close()

        assert len(synced_calls) > 0
        last = synced_calls[-1]
        # Status should be synced (PENDING after fallback)
        assert last["status"] == "pending"
        # Instrument name should be the new one
        assert last["instrument_name"] == "gemini-cli"
