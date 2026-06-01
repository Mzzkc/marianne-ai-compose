"""Regression test for GH#170: fallback instrument registration.

When a sheet falls back from its primary instrument to a fallback instrument,
the fallback must be registered in the baton's _instruments dict. Without this,
the dispatch loop skips the sheet as "unavailable" (unregistered instrument),
but since the fallback chain is also exhausted, the sheet becomes permanently
stuck in PENDING with no recovery event.

The fix registers fallback instruments both at job registration time (upstream
defense) and at each fallback advancement point (runtime safety net).
"""

from __future__ import annotations

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import RateLimitHit
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


class TestFallbackInstrumentRegistration:
    """Fallback instruments are registered when sheets advance to them."""

    def test_auto_register_registers_fallback_instruments(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                fallback_chain=["opencode"],
            ),
        }
        baton.register_job("j1", sheets, {})
        assert "claude-code" in baton._instruments
        assert "opencode" in baton._instruments

    def test_auto_register_registers_multiple_fallbacks(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                fallback_chain=["gemini-cli", "opencode"],
            ),
        }
        baton.register_job("j1", sheets, {})
        assert "claude-code" in baton._instruments
        assert "gemini-cli" in baton._instruments
        assert "opencode" in baton._instruments

    def test_ensure_instrument_registered_new_instrument(self) -> None:
        baton = BatonCore()
        assert "new-instrument" not in baton._instruments
        baton._ensure_instrument_registered("new-instrument")
        assert "new-instrument" in baton._instruments

    def test_ensure_instrument_registered_idempotent(self) -> None:
        baton = BatonCore()
        baton._ensure_instrument_registered("existing")
        baton._ensure_instrument_registered("existing")
        assert (
            baton._instruments["existing"].max_concurrent == baton._DEFAULT_INSTRUMENT_CONCURRENCY
        )

    def test_rate_limit_fallback_registers_new_instrument(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                fallback_chain=["opencode"],
                status=BatonSheetStatus.IN_PROGRESS,
            ),
        }
        baton.register_job("j1", sheets, {})

        baton._handle_rate_limit_hit(
            RateLimitHit(
                job_id="j1",
                sheet_num=1,
                instrument="claude-code",
                wait_seconds=300,
            )
        )

        sheet = baton._jobs["j1"].sheets[1]
        assert sheet.instrument_name == "opencode"
        assert sheet.status == BatonSheetStatus.PENDING
        assert "opencode" in baton._instruments

    async def test_exhaustion_fallback_registers_new_instrument(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                fallback_chain=["opencode"],
                max_retries=3,
                status=BatonSheetStatus.IN_PROGRESS,
            ),
        }
        baton.register_job("j1", sheets, {})

        sheet = baton._jobs["j1"].sheets[1]
        sheet.normal_attempts = 3

        await baton._handle_exhaustion("j1", 1, sheet)

        assert sheet.instrument_name == "opencode"
        assert sheet.status == BatonSheetStatus.PENDING
        assert "opencode" in baton._instruments

    def test_unavailable_fallback_registers_new_instrument(self) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                fallback_chain=["opencode"],
                status=BatonSheetStatus.PENDING,
            ),
        }
        baton.register_job("j1", sheets, {})

        inst = baton._instruments["claude-code"]
        inst.rate_limited = True

        result = baton._check_and_fallback_unavailable(
            baton._jobs["j1"].sheets[1],
            "j1",
        )

        assert result is True
        sheet = baton._jobs["j1"].sheets[1]
        assert sheet.instrument_name == "opencode"
        assert "opencode" in baton._instruments

    def test_unregistered_primary_with_fallback_registers_new_instrument(
        self,
    ) -> None:
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                fallback_chain=["opencode"],
                status=BatonSheetStatus.PENDING,
            ),
        }
        baton.register_job("j1", sheets, {})

        del baton._instruments["opencode"]

        sheet = baton._jobs["j1"].sheets[1]
        sheet.instrument_name = "unknown-instrument"

        result = baton._check_and_fallback_unavailable(sheet, "j1")

        assert result is True
        assert sheet.instrument_name == "opencode"
        assert "opencode" in baton._instruments
