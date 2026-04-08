"""TDD tests for F-112: Auto-resume after rate limit PAUSE.

When a rate limit hit occurs, the baton should schedule a RateLimitExpired
timer event so that WAITING sheets automatically resume when the limit clears.
Without this, sheets stay WAITING forever unless manually cleared.

The root cause: _handle_rate_limit_hit() sets rate_limit_expires_at but never
schedules a timer to fire RateLimitExpired. The event type and handler exist
but nothing triggers them.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    RateLimitExpired,
    RateLimitHit,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    SheetExecutionState,
)
from marianne.daemon.baton.timer import TimerWheel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_baton_with_timer() -> tuple[BatonCore, TimerWheel, asyncio.Queue]:
    """Create a BatonCore with a real TimerWheel."""
    inbox: asyncio.Queue = asyncio.Queue()
    timer = TimerWheel(inbox)
    baton = BatonCore(timer=timer)
    baton._inbox = inbox
    return baton, timer, inbox


def _make_baton_no_timer() -> BatonCore:
    """Create a BatonCore without a timer (for unit tests)."""
    return BatonCore()


def _register_simple_job(
    baton: BatonCore,
    job_id: str = "test-job",
    instrument: str = "claude-cli",
    num_sheets: int = 1,
) -> None:
    """Register a simple job with the baton."""
    sheets = {
        i: SheetExecutionState(
            sheet_num=i,
            instrument_name=instrument,
        )
        for i in range(1, num_sheets + 1)
    }
    baton.register_job(job_id, sheets, {})


# ---------------------------------------------------------------------------
# Test: Timer is scheduled on rate limit hit
# ---------------------------------------------------------------------------


class TestRateLimitTimerScheduling:
    """When a rate limit hit occurs, a RateLimitExpired timer must be scheduled."""

    def test_rate_limit_hit_schedules_expiry_timer(self) -> None:
        """_handle_rate_limit_hit must schedule a RateLimitExpired timer event."""
        baton, timer, _ = _make_baton_with_timer()
        _register_simple_job(baton, instrument="claude-cli")

        job = baton._jobs["test-job"]
        sheet = job.sheets[1]
        sheet.status = BatonSheetStatus.IN_PROGRESS

        event = RateLimitHit(
            instrument="claude-cli",
            wait_seconds=30.0,
            job_id="test-job",
            sheet_num=1,
        )
        baton._handle_rate_limit_hit(event)

        # Verify a timer was scheduled
        assert len(timer._heap) >= 1, "No timer scheduled for rate limit expiry"

        # The timer should fire a RateLimitExpired event
        timer_entry = timer._heap[0]
        assert isinstance(timer_entry.handle.event, RateLimitExpired), (
            f"Expected RateLimitExpired, got {type(timer_entry.handle.event).__name__}"
        )
        assert timer_entry.handle.event.instrument == "claude-cli"

    def test_rate_limit_timer_fires_at_correct_time(self) -> None:
        """The timer should fire approximately wait_seconds from now."""
        baton, timer, _ = _make_baton_with_timer()
        _register_simple_job(baton, instrument="claude-cli")

        before = time.monotonic()
        event = RateLimitHit(
            instrument="claude-cli",
            wait_seconds=60.0,
            job_id="test-job",
            sheet_num=1,
        )
        baton._handle_rate_limit_hit(event)
        after = time.monotonic()

        timer_entry = timer._heap[0]
        expected_min = before + 60.0
        expected_max = after + 60.0
        assert expected_min <= timer_entry.fire_at <= expected_max, (
            f"Timer fire_at {timer_entry.fire_at} not in expected range "
            f"[{expected_min}, {expected_max}]"
        )

    def test_no_timer_when_timer_is_none(self) -> None:
        """When BatonCore has no timer (tests), no crash occurs."""
        baton = _make_baton_no_timer()
        _register_simple_job(baton, instrument="claude-cli")

        event = RateLimitHit(
            instrument="claude-cli",
            wait_seconds=30.0,
            job_id="test-job",
            sheet_num=1,
        )
        baton._handle_rate_limit_hit(event)

        inst = baton._instruments.get("claude-cli")
        assert inst is not None
        assert inst.rate_limited is True

    def test_timer_scheduled_even_without_running_sheets(self) -> None:
        """Timer should be scheduled even if no sheets are currently running.

        The rate limit applies to the instrument, not a specific sheet.
        Future dispatch attempts need to know the limit is active.
        """
        baton, timer, _ = _make_baton_with_timer()
        _register_simple_job(baton, instrument="claude-cli")
        # All sheets stay PENDING — none running

        event = RateLimitHit(
            instrument="claude-cli",
            wait_seconds=45.0,
            job_id="test-job",
            sheet_num=1,
        )
        baton._handle_rate_limit_hit(event)

        assert len(timer._heap) >= 1, "Timer should be scheduled for instrument rate limit"
        timer_entry = timer._heap[0]
        assert isinstance(timer_entry.handle.event, RateLimitExpired)


class TestRateLimitAutoResume:
    """When the RateLimitExpired timer fires, WAITING sheets must resume."""

    def test_expired_event_moves_waiting_to_pending(self) -> None:
        """RateLimitExpired should move WAITING sheets back to PENDING."""
        baton = _make_baton_no_timer()
        _register_simple_job(baton, instrument="claude-cli", num_sheets=3)

        job = baton._jobs["test-job"]
        for sheet in job.sheets.values():
            sheet.status = BatonSheetStatus.WAITING
        inst = baton._instruments["claude-cli"]
        inst.rate_limited = True
        inst.rate_limit_expires_at = time.monotonic() + 30.0

        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-cli"))

        for sheet in job.sheets.values():
            assert sheet.status == BatonSheetStatus.PENDING, (
                f"Sheet {sheet.sheet_num} still {sheet.status}"
            )
        assert inst.rate_limited is False
        assert inst.rate_limit_expires_at is None

    def test_only_waiting_sheets_resume(self) -> None:
        """Sheets in terminal or other states must not be affected."""
        baton = _make_baton_no_timer()
        _register_simple_job(baton, instrument="claude-cli", num_sheets=4)

        job = baton._jobs["test-job"]
        job.sheets[1].status = BatonSheetStatus.WAITING
        job.sheets[2].status = BatonSheetStatus.COMPLETED
        job.sheets[3].status = BatonSheetStatus.FAILED
        job.sheets[4].status = BatonSheetStatus.PENDING

        inst = baton._instruments["claude-cli"]
        inst.rate_limited = True

        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-cli"))

        assert job.sheets[1].status == BatonSheetStatus.PENDING  # was WAITING
        assert job.sheets[2].status == BatonSheetStatus.COMPLETED  # untouched
        assert job.sheets[3].status == BatonSheetStatus.FAILED  # untouched
        assert job.sheets[4].status == BatonSheetStatus.PENDING  # already PENDING

    def test_only_matching_instrument_sheets_resume(self) -> None:
        """Only sheets on the rate-limited instrument should resume."""
        baton = _make_baton_no_timer()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-cli"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        baton.register_job("test-job", sheets, {})

        job = baton._jobs["test-job"]
        job.sheets[1].status = BatonSheetStatus.WAITING
        job.sheets[2].status = BatonSheetStatus.WAITING

        baton._instruments["claude-cli"].rate_limited = True
        baton._instruments["gemini-cli"].rate_limited = True

        # Only expire claude-cli
        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-cli"))

        assert job.sheets[1].status == BatonSheetStatus.PENDING  # claude: resumed
        assert job.sheets[2].status == BatonSheetStatus.WAITING  # gemini: still waiting


class TestRateLimitTimerReplacement:
    """Subsequent rate limit hits should schedule a new timer with the updated wait."""

    def test_new_rate_limit_hit_schedules_new_timer(self) -> None:
        """A second rate limit hit on the same instrument should schedule a new timer."""
        baton, timer, _ = _make_baton_with_timer()
        _register_simple_job(baton, instrument="claude-cli")

        baton._handle_rate_limit_hit(RateLimitHit(
            instrument="claude-cli", wait_seconds=30.0,
            job_id="test-job", sheet_num=1,
        ))
        first_count = len(timer._heap)

        before = time.monotonic()
        baton._handle_rate_limit_hit(RateLimitHit(
            instrument="claude-cli", wait_seconds=60.0,
            job_id="test-job", sheet_num=1,
        ))

        assert len(timer._heap) >= first_count
        non_cancelled = [e for e in timer._heap if not e.cancelled]
        latest_entry = max(non_cancelled, key=lambda e: e.fire_at)
        assert isinstance(latest_entry.handle.event, RateLimitExpired)
        assert latest_entry.fire_at >= before + 59.0


class TestEndToEndRateLimitCycle:
    """Full cycle: dispatch → rate limit → timer → auto-resume."""

    def test_full_rate_limit_recovery_cycle(self) -> None:
        """Simulate the complete lifecycle of a rate limit event."""
        baton = _make_baton_no_timer()
        _register_simple_job(baton, instrument="claude-cli")

        job = baton._jobs["test-job"]
        sheet = job.sheets[1]
        assert sheet.status == BatonSheetStatus.PENDING
        sheet.status = BatonSheetStatus.IN_PROGRESS

        baton._handle_rate_limit_hit(RateLimitHit(
            instrument="claude-cli", wait_seconds=30.0,
            job_id="test-job", sheet_num=1,
        ))
        assert sheet.status == BatonSheetStatus.WAITING
        assert baton._instruments["claude-cli"].rate_limited is True

        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-cli"))
        assert sheet.status == BatonSheetStatus.PENDING
        assert baton._instruments["claude-cli"].rate_limited is False

    def test_multi_job_rate_limit_recovery(self) -> None:
        """RateLimitExpired resumes WAITING sheets across all jobs."""
        baton = _make_baton_no_timer()
        _register_simple_job(baton, job_id="job-1", instrument="claude-cli")
        _register_simple_job(baton, job_id="job-2", instrument="claude-cli")

        for job_id in ("job-1", "job-2"):
            for sheet in baton._jobs[job_id].sheets.values():
                sheet.status = BatonSheetStatus.WAITING
        baton._instruments["claude-cli"].rate_limited = True

        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-cli"))

        for job_id in ("job-1", "job-2"):
            for sheet in baton._jobs[job_id].sheets.values():
                assert sheet.status == BatonSheetStatus.PENDING, (
                    f"{job_id} sheet {sheet.sheet_num} still {sheet.status}"
                )
