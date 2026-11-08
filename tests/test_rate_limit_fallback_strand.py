"""A late rate-limit result must not strand a sheet whose fallback already advanced.

Observed on ``orchestra-memory-rev4`` (2026-11-08): four composition sheets on
``antigravity`` hit the provider's quota screen within five seconds of each
other. The first result's ``RateLimitHit`` advanced every in-flight sibling to
its fallback (``claude-code``) and relabelled it PENDING; then each sibling's
own late ``SheetAttemptResult`` arrived naming ``antigravity`` and parked the
sheet WAITING. WAITING is only woken by a ``RateLimitExpired`` or a
``clear-rate-limits`` on the sheet's *current* instrument, and ``claude-code``
was never rate-limited, so three sheets sat for hours as
"waiting for dispatch: active_execution" while nothing ran.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    RateLimitExpired,
    RateLimitHit,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


def _sheet(num: int) -> SheetExecutionState:
    return SheetExecutionState(
        sheet_num=num,
        instrument_name="antigravity",
        fallback_chain=["claude-code", "codex-cli"],
        max_retries=3,
        max_completion=5,
        status=BatonSheetStatus.DISPATCHED,
    )


def _rate_limited_result(num: int, instrument: str = "antigravity") -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j1",
        sheet_num=num,
        instrument_name=instrument,
        attempt=1,
        execution_success=False,
        rate_limited=True,
        rate_limit_wait_seconds=11_000.0,
        validations_total=1,
        validations_passed=0,
        validation_pass_rate=0.0,
    )


def _baton() -> BatonCore:
    baton = BatonCore()
    baton.register_job("j1", {10: _sheet(10), 11: _sheet(11), 12: _sheet(12)}, {})
    for name in ("antigravity", "claude-code", "codex-cli"):
        baton.register_instrument(name)
    return baton


async def _drain(baton: BatonCore) -> None:
    while not baton.inbox.empty():
        await baton.handle_event(baton.inbox.get_nowait())


class TestLateRateLimitResultAfterFallbackAdvance:
    @pytest.mark.asyncio
    async def test_sibling_results_land_pending_on_the_fallback(self) -> None:
        baton = _baton()
        job = baton._jobs["j1"]

        # Sheet 10 reports first; its RateLimitHit advances 11 and 12 too.
        await baton.handle_event(_rate_limited_result(10))
        await _drain(baton)
        for num in (10, 11, 12):
            assert job.sheets[num].instrument_name == "claude-code"
            assert job.sheets[num].status == BatonSheetStatus.PENDING

        # Sheets 11 and 12 report their own antigravity refusals late.
        await baton.handle_event(_rate_limited_result(11))
        await baton.handle_event(_rate_limited_result(12))
        await _drain(baton)

        for num in (11, 12):
            sheet = job.sheets[num]
            assert sheet.instrument_name == "claude-code", (
                "a late result must not advance the fallback a second time"
            )
            assert sheet.status == BatonSheetStatus.PENDING, (
                f"sheet {num} stranded WAITING on an instrument that is not rate-limited"
            )
        ready = {s.sheet_num for s in baton.get_ready_sheets("j1")}
        assert ready == {10, 11, 12}

    @pytest.mark.asyncio
    async def test_result_on_the_current_instrument_still_waits(self) -> None:
        """The ordinary case is unchanged: a refusal on the sheet's own instrument waits."""
        baton = _baton()
        sheet = baton._jobs["j1"].sheets[10]
        sheet.fallback_chain = []
        await baton.handle_event(_rate_limited_result(10))
        assert sheet.status == BatonSheetStatus.WAITING
        await _drain(baton)
        assert sheet.status == BatonSheetStatus.WAITING
        assert baton.get_instrument_state("antigravity").rate_limited is True  # type: ignore[union-attr]


class TestStrandedWaitingSheetsAreReleased:
    def _strand(self, baton: BatonCore, num: int) -> SheetExecutionState:
        sheet = baton._jobs["j1"].sheets[num]
        sheet.advance_fallback("rate_limit")  # now on claude-code
        sheet.status = BatonSheetStatus.WAITING  # as an older conductor left it
        return sheet

    @pytest.mark.asyncio
    async def test_another_instruments_expiry_keeps_the_contract(self) -> None:
        """An expiry moves only its own instrument's sheets (pinned elsewhere too)."""
        baton = _baton()
        stranded = self._strand(baton, 11)
        await baton.handle_event(
            RateLimitHit(instrument="antigravity", wait_seconds=60.0, job_id="j1", sheet_num=10)
        )
        await baton.handle_event(RateLimitExpired(instrument="antigravity"))
        assert stranded.status == BatonSheetStatus.WAITING

    def test_clear_all_releases_strands_even_with_nothing_limited(self) -> None:
        baton = _baton()
        stranded = self._strand(baton, 12)
        assert baton.clear_instrument_rate_limit() == 0
        assert stranded.status == BatonSheetStatus.PENDING

    def test_clear_one_instrument_leaves_other_instruments_sheets(self) -> None:
        baton = _baton()
        stranded = self._strand(baton, 12)  # WAITING on claude-code
        baton.clear_instrument_rate_limit("antigravity")
        assert stranded.status == BatonSheetStatus.WAITING

    @pytest.mark.asyncio
    async def test_waiting_on_a_limited_instrument_is_not_released(self) -> None:
        baton = _baton()
        sheet = baton._jobs["j1"].sheets[10]
        sheet.fallback_chain = []
        await baton.handle_event(_rate_limited_result(10))
        await _drain(baton)
        assert sheet.status == BatonSheetStatus.WAITING
        # An unrelated instrument's expiry must not wake a sheet whose own
        # instrument is still limited.
        await baton.handle_event(RateLimitExpired(instrument="codex-cli"))
        assert sheet.status == BatonSheetStatus.WAITING
