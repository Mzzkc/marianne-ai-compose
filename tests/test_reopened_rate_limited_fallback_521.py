"""A reopened loop sheet does not wait out a rate-limited primary while a fallback idles (GH #521).

``reset_for_flow`` rewinds a reopened sheet to its primary. If that primary is
rate-limited, dispatch used to skip the sheet until ``RateLimitExpired`` even
though ``_handle_rate_limit_hit`` had advanced the same sheet to its fallback a
pass earlier: with a day-long quota timer the sheet sat PENDING while three
fallback seats idled. Dispatch now advances the chain the way the in-flight
handler does; a sheet with no fallback still waits.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


async def _accept(job_id: str, sheet_num: int, sheet: SheetExecutionState) -> bool:
    return True


def _sheet(num: int, fallbacks: list[str]) -> SheetExecutionState:
    return SheetExecutionState(
        sheet_num=num,
        instrument_name="antigravity",
        fallback_chain=list(fallbacks),
        max_retries=1,
        max_completion=5,
        status=BatonSheetStatus.PENDING,
    )


def _baton(sheets: dict[int, SheetExecutionState]) -> BatonCore:
    baton = BatonCore()
    baton.register_job("j1", sheets, {})
    for name in ("antigravity", "opencode", "codex-cli"):
        baton.register_instrument(name)
    return baton


def _config(limited: set[str]) -> DispatchConfig:
    return DispatchConfig(
        max_concurrent_sheets=10,
        instrument_concurrency={},
        rate_limited_instruments=set(limited),
        open_circuit_breakers=set(),
    )


@pytest.mark.asyncio
async def test_reopened_sheet_on_limited_primary_dispatches_on_its_fallback() -> None:
    baton = _baton({3: _sheet(3, ["opencode", "codex-cli"])})
    sheet = baton._jobs["j1"].sheets[3]
    # Pass 1 already ran on the fallback; the loop reopened the sheet.
    sheet.remember_primary_identity()
    sheet.advance_fallback("rate_limit")
    sheet.reset_for_flow("loop iteration", {"pass_no": 2})
    assert sheet.instrument_name == "antigravity"

    result = await dispatch_ready(baton, _config({"antigravity"}), _accept)

    assert ("j1", 3) in result.dispatched_sheets, result
    assert sheet.instrument_name == "opencode"
    assert sheet.fallback_chain[sheet.current_instrument_index - 1] == "opencode"
    assert sheet.status is not BatonSheetStatus.WAITING


@pytest.mark.asyncio
async def test_limited_primary_with_no_fallback_still_waits() -> None:
    baton = _baton({4: _sheet(4, [])})
    sheet = baton._jobs["j1"].sheets[4]

    result = await dispatch_ready(baton, _config({"antigravity"}), _accept)

    assert ("j1", 4) not in result.dispatched_sheets
    assert sheet.instrument_name == "antigravity"
    assert sheet.status is BatonSheetStatus.PENDING
    assert sheet.dispatch_blocked_reason == "rate_limited"


@pytest.mark.asyncio
async def test_every_chain_entry_limited_waits_on_the_last() -> None:
    baton = _baton({5: _sheet(5, ["opencode"])})
    sheet = baton._jobs["j1"].sheets[5]

    result = await dispatch_ready(baton, _config({"antigravity", "opencode"}), _accept)

    assert ("j1", 5) not in result.dispatched_sheets
    assert sheet.status is BatonSheetStatus.PENDING
    assert sheet.dispatch_blocked_reason == "rate_limited"

