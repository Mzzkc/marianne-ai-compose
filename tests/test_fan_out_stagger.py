"""Tests for F-099: Fan-out launch staggering.

Adding a configurable delay between parallel sheet launches reduces
rate limit surge when many sheets hit the same API simultaneously.

The baton uses inter-sheet pacing (pause_between_sheets_seconds) instead
of the removed ParallelExecutor stagger_delay_ms.
"""

from __future__ import annotations

import pytest

from marianne.core.checkpoint import SheetStatus
from marianne.core.config.execution import ParallelConfig
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import PacingComplete, SheetAttemptResult
from marianne.daemon.baton.state import SheetExecutionState

# ---------------------------------------------------------------------------
# 1. ParallelConfig accepts stagger_delay_ms (config model, still valid)
# ---------------------------------------------------------------------------


class TestParallelConfigStagger:
    """ParallelConfig must accept stagger_delay_ms field."""

    def test_default_stagger_delay(self) -> None:
        """Default stagger_delay_ms is 0 (no stagger)."""
        config = ParallelConfig()
        assert config.stagger_delay_ms == 0

    def test_custom_stagger_delay(self) -> None:
        """stagger_delay_ms can be set to a custom value."""
        config = ParallelConfig(stagger_delay_ms=100)
        assert config.stagger_delay_ms == 100

    def test_stagger_delay_in_yaml(self) -> None:
        """stagger_delay_ms is accepted from YAML config."""
        from marianne.core.config import JobConfig

        config = JobConfig.model_validate(
            {
                "name": "test",
                "sheet": {"size": 3, "total_items": 9},
                "prompt": {"template": "Test {{ sheet_num }}"},
                "parallel": {
                    "enabled": True,
                    "max_concurrent": 5,
                    "stagger_delay_ms": 150,
                },
            }
        )
        assert config.parallel.stagger_delay_ms == 150

    def test_stagger_delay_validation_non_negative(self) -> None:
        """stagger_delay_ms must be non-negative."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ParallelConfig(stagger_delay_ms=-1)

    def test_stagger_delay_max_cap(self) -> None:
        """stagger_delay_ms is capped at 5000ms."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ParallelConfig(stagger_delay_ms=6000)


# ---------------------------------------------------------------------------
# 2. Baton inter-sheet pacing — replaces ParallelExecutor stagger
# ---------------------------------------------------------------------------


class TestBatonPacingNoDelay:
    """When pacing_seconds=0, the baton dispatches immediately."""

    async def test_no_pacing_when_zero(self) -> None:
        """When pacing_seconds=0, no delay between sheet completions."""
        baton = BatonCore()

        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code") for i in range(1, 4)
        }

        baton.register_job("pacing-test", sheets, {}, pacing_seconds=0.0)

        job = baton._jobs["pacing-test"]
        assert job.pacing_seconds == 0.0
        assert not job.pacing_active


class TestBatonPacingScheduled:
    """When pacing_seconds > 0, the baton schedules a PacingComplete timer
    after a sheet completes (when no other sheets are dispatched)."""

    async def test_pacing_active_after_completion(self) -> None:
        """After a sheet completes with pacing configured, pacing_active is True."""
        baton = BatonCore()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }

        baton.register_job("pacing-test", sheets, {}, pacing_seconds=0.1)
        baton.register_instrument("claude-code", max_concurrent=4)

        job = baton._jobs["pacing-test"]
        assert job.pacing_seconds == 0.1

        await baton.handle_event(
            SheetAttemptResult(
                job_id="pacing-test",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )

        assert job.pacing_active, "pacing_active should be True after sheet 1 completes"

    async def test_pacing_cleared_on_pacing_complete_event(self) -> None:
        """PacingComplete event clears pacing_active."""
        baton = BatonCore()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }

        baton.register_job("pacing-clear", sheets, {}, pacing_seconds=0.1)

        job = baton._jobs["pacing-clear"]
        job.pacing_active = True

        await baton.handle_event(PacingComplete(job_id="pacing-clear"))

        assert not job.pacing_active, "PacingComplete should clear pacing_active"

    async def test_pacing_skipped_when_sheets_still_dispatched(self) -> None:
        """GH#167: pacing is NOT activated when other sheets are still DISPATCHED."""
        baton = BatonCore()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        sheets[2].status = SheetStatus.DISPATCHED

        baton.register_job("pacing-wave", sheets, {}, pacing_seconds=0.1)
        baton.register_instrument("claude-code", max_concurrent=4)

        await baton.handle_event(
            SheetAttemptResult(
                job_id="pacing-wave",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )

        job = baton._jobs["pacing-wave"]
        assert not job.pacing_active, (
            "pacing should NOT activate when other sheets are still DISPATCHED"
        )

    async def test_pacing_activated_even_for_single_sheet(self) -> None:
        """For a single-sheet job, pacing is still activated after completion.
        This is acceptable — the job is already done, pacing just delays
        the final completion acknowledgment."""
        baton = BatonCore()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }

        baton.register_job("single", sheets, {}, pacing_seconds=0.5)
        baton.register_instrument("claude-code", max_concurrent=4)

        await baton.handle_event(
            SheetAttemptResult(
                job_id="single",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )

        job = baton._jobs["single"]
        assert sheets[1].status == SheetStatus.COMPLETED
        assert job.pacing_active, "pacing activates after any sheet completion with pacing > 0"
