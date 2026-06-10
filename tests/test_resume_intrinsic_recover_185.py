"""#185: `mzt resume` intrinsically recovers FAILED jobs.

Resuming a FAILED/CANCELLED job must reset its FAILED + cascade-SKIPPED sheets
to PENDING (clearing retry budgets) and dispatch them — no separate `mzt recover`
step. Resuming a PAUSED job must preserve terminal sheets. `--from-sheet N`
resets every sheet >= N regardless of status (operator override).

The cascade-vs-deliberate SKIPPED discriminant is the codebase's existing one
(``core.py`` ``_is_dep_satisfied``): a SKIPPED sheet with ``error_code is not
None`` was cascade-blocked (work never done → reset); ``error_code is None`` is
a deliberate skip (skip_when / --start-sheet / escalation-skip → preserve).
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.daemon.manager import JobManager, JobMeta, _reset_sheets_for_resume
from marianne.daemon.registry import DaemonJobStatus


def _failed_sheet(num: int) -> SheetState:
    return SheetState(
        sheet_num=num,
        status=SheetStatus.FAILED,
        error_code="E001",
        error_message="boom",
        attempt_count=4,
        normal_attempts=3,
        completion_attempts=1,
        healing_attempts=2,
        current_instrument_index=2,
        instrument_fallback_history=[{"from": "claude", "to": "goose"}],
        fallback_attempts={"claude": 2},
    )


def _cascade_skipped(num: int) -> SheetState:
    # Cascade-skipped: blocked by a failed dependency → error_code set (E999).
    return SheetState(
        sheet_num=num,
        status=SheetStatus.SKIPPED,
        error_code="E999",
        error_message="Blocked by failed dependency: sheet 2",
    )


def _deliberate_skipped(num: int) -> SheetState:
    # Deliberately skipped (skip_when / --start-sheet / escalation-skip): no error_code.
    return SheetState(sheet_num=num, status=SheetStatus.SKIPPED)


def _completed(num: int) -> SheetState:
    return SheetState(sheet_num=num, status=SheetStatus.COMPLETED)


def _make_checkpoint(status: JobStatus, sheets: dict[int, SheetState]) -> CheckpointState:
    return CheckpointState(
        job_id="rj",
        job_name="resume-test",
        total_sheets=len(sheets),
        status=status,
        sheets=sheets,
    )


# ── SheetState.reset_for_retry() ──────────────────────────────────────────


class TestSheetStateReset:
    def test_reset_clears_status_budgets_and_error_provenance(self) -> None:
        sheet = _failed_sheet(1)
        sheet.completed_at = None  # default; just being explicit
        sheet.reset_for_retry()

        assert sheet.status == SheetStatus.PENDING
        assert sheet.attempt_count == 0
        assert sheet.normal_attempts == 0
        assert sheet.completion_attempts == 0
        assert sheet.healing_attempts == 0
        assert sheet.current_instrument_index == 0
        assert sheet.instrument_fallback_history == []
        assert sheet.fallback_attempts == {}
        assert sheet.error_code is None
        assert sheet.error_message is None
        assert sheet.completed_at is None


# ── _reset_sheets_for_resume() decision rule ───────────────────────────────


class TestResetSheetsForResume:
    def test_failed_resets_failed_and_cascade_skipped_only(self) -> None:
        cp = _make_checkpoint(
            JobStatus.FAILED,
            {
                1: _completed(1),
                2: _failed_sheet(2),
                3: _cascade_skipped(3),
                4: _deliberate_skipped(4),
                5: _completed(5),
            },
        )
        count = _reset_sheets_for_resume(cp, DaemonJobStatus.FAILED)

        assert count == 2
        assert cp.sheets[1].status == SheetStatus.COMPLETED  # preserved
        assert cp.sheets[2].status == SheetStatus.PENDING  # was FAILED
        assert cp.sheets[2].attempt_count == 0
        assert cp.sheets[2].error_code is None
        assert cp.sheets[3].status == SheetStatus.PENDING  # cascade-skipped
        assert cp.sheets[3].error_code is None
        assert cp.sheets[4].status == SheetStatus.SKIPPED  # deliberate — preserved
        assert cp.sheets[5].status == SheetStatus.COMPLETED  # preserved

    def test_cancelled_behaves_like_failed(self) -> None:
        cp = _make_checkpoint(
            JobStatus.CANCELLED,
            {1: _failed_sheet(1), 2: _deliberate_skipped(2)},
        )
        count = _reset_sheets_for_resume(cp, DaemonJobStatus.CANCELLED)

        assert count == 1
        assert cp.sheets[1].status == SheetStatus.PENDING
        assert cp.sheets[2].status == SheetStatus.SKIPPED  # deliberate — preserved

    def test_paused_preserves_all_terminal_sheets(self) -> None:
        cp = _make_checkpoint(
            JobStatus.PAUSED,
            {
                1: _completed(1),
                2: _failed_sheet(2),
                3: _cascade_skipped(3),
                4: SheetState(sheet_num=4, status=SheetStatus.PENDING),
            },
        )
        count = _reset_sheets_for_resume(cp, DaemonJobStatus.PAUSED)

        assert count == 0
        assert cp.sheets[1].status == SheetStatus.COMPLETED
        assert cp.sheets[2].status == SheetStatus.FAILED  # CRITICAL: not reset
        assert cp.sheets[2].attempt_count == 4  # budgets preserved
        assert cp.sheets[3].status == SheetStatus.SKIPPED
        assert cp.sheets[4].status == SheetStatus.PENDING

    def test_from_sheet_resets_all_at_or_above_n_regardless_of_status(self) -> None:
        cp = _make_checkpoint(
            JobStatus.FAILED,
            {
                1: _completed(1),
                2: _completed(2),
                3: _failed_sheet(3),
                4: _deliberate_skipped(4),
                5: SheetState(sheet_num=5, status=SheetStatus.PENDING),
            },
        )
        count = _reset_sheets_for_resume(cp, DaemonJobStatus.FAILED, from_sheet=3)

        assert count == 2  # sheet 3 (FAILED) + sheet 4 (deliberate SKIPPED); 5 already PENDING
        assert cp.sheets[1].status == SheetStatus.COMPLETED  # below threshold
        assert cp.sheets[2].status == SheetStatus.COMPLETED  # below threshold
        assert cp.sheets[3].status == SheetStatus.PENDING
        assert cp.sheets[4].status == SheetStatus.PENDING  # deliberate, but override
        assert cp.sheets[5].status == SheetStatus.PENDING

    def test_from_sheet_overrides_paused_guard(self) -> None:
        cp = _make_checkpoint(
            JobStatus.PAUSED,
            {1: _completed(1), 2: _completed(2)},
        )
        count = _reset_sheets_for_resume(cp, DaemonJobStatus.PAUSED, from_sheet=2)

        assert count == 1
        assert cp.sheets[1].status == SheetStatus.COMPLETED  # below threshold, preserved
        assert cp.sheets[2].status == SheetStatus.PENDING  # >= 2, reset despite PAUSED

    def test_idempotent_rerun_is_noop(self) -> None:
        cp = _make_checkpoint(JobStatus.FAILED, {1: _failed_sheet(1)})
        first = _reset_sheets_for_resume(cp, DaemonJobStatus.FAILED)
        second = _reset_sheets_for_resume(cp, DaemonJobStatus.FAILED)

        assert first == 1
        assert second == 0  # already PENDING — nothing to reset
        assert cp.sheets[1].status == SheetStatus.PENDING

    def test_none_status_preserves_unless_from_sheet(self) -> None:
        # pre_resume_status None = caller opted out of intrinsic recovery.
        cp = _make_checkpoint(JobStatus.FAILED, {1: _failed_sheet(1)})
        assert _reset_sheets_for_resume(cp, None) == 0
        assert cp.sheets[1].status == SheetStatus.FAILED
        # ...but an explicit from_sheet still overrides.
        assert _reset_sheets_for_resume(cp, None, from_sheet=1) == 1
        assert cp.sheets[1].status == SheetStatus.PENDING


# ── End-to-end threading: CLI → IPC params → resume_job → task → baton ─────


class TestFromSheetThreading:
    """from_sheet and the captured pre_resume_status must thread end-to-end."""

    @pytest.mark.asyncio
    async def test_cli_includes_from_sheet_in_params(self) -> None:
        captured: list[dict[str, Any]] = []

        async def fake_route(method: str, params: dict[str, Any], **_: Any) -> tuple[bool, Any]:
            captured.append(params)
            return True, {"job_id": "j", "status": "accepted", "message": "ok"}

        with (
            patch("marianne.daemon.detect.try_daemon_route", side_effect=fake_route),
            patch("marianne.cli.commands.resume.configure_global_logging"),
        ):
            from marianne.cli.commands.resume import _resume_job

            await _resume_job(job_id="j", config_file=None, force=False, from_sheet=4)

        assert captured and captured[0].get("from_sheet") == 4

    def _manager_with_failed_job(self) -> JobManager:
        mgr = MagicMock(spec=JobManager)
        mgr._job_meta = {
            "j": JobMeta(
                job_id="j",
                config_path=Path("/old.yaml"),
                workspace=Path("/tmp/ws"),
                status=DaemonJobStatus.FAILED,
            ),
        }
        mgr._jobs = {}
        mgr._pause_events = {}
        mgr._baton_adapter = None
        mgr._live_states = {}
        mgr.resume_job = JobManager.resume_job.__get__(mgr, JobManager)
        mgr._set_job_status = MagicMock(
            spec=lambda *a, **k: None, side_effect=_async_noop
        )
        mgr._on_task_done = MagicMock(spec=lambda job_id, task: None)
        return mgr

    @pytest.mark.asyncio
    async def test_resume_job_forwards_from_sheet_and_captured_failed_status(self) -> None:
        """resume_job must forward from_sheet AND the PRE-resume status (FAILED),
        not the QUEUED/RUNNING status it transitions to."""
        captured: list[dict[str, Any]] = []

        async def fake_task(
            job_id: str,
            workspace: Path,
            no_reload: bool = False,
            pre_resume_status: DaemonJobStatus | None = None,
            from_sheet: int | None = None,
            escalation: bool = False,
            self_healing: bool = False,
        ) -> None:
            captured.append(
                {"pre_resume_status": pre_resume_status, "from_sheet": from_sheet}
            )

        mgr = self._manager_with_failed_job()
        mgr._resume_job_task = fake_task  # type: ignore[attr-defined]

        response = await mgr.resume_job("j", from_sheet=7)
        assert response.status == "accepted"
        await asyncio.sleep(0.05)

        task = mgr._jobs.get("j")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert captured == [
            {"pre_resume_status": DaemonJobStatus.FAILED, "from_sheet": 7}
        ]

    def test_resume_via_baton_accepts_new_params(self) -> None:
        sig = inspect.signature(JobManager._resume_via_baton)
        assert "pre_resume_status" in sig.parameters
        assert "from_sheet" in sig.parameters

    def test_resume_job_task_forwards_params_to_baton(self) -> None:
        source = (
            Path(__file__).parent.parent
            / "src" / "marianne" / "daemon" / "manager.py"
        ).read_text()
        assert "pre_resume_status=pre_resume_status" in source
        assert "from_sheet=from_sheet" in source


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    return None
