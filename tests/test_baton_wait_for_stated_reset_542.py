"""GH #542: an exhausted chain with a rate limit that states its end WAITS.

A sheet that has run out of retries and fallbacks, while some entry of its
chain is rate limited with a future expiry, is parked WAITING on the entry
that frees first. RateLimitExpired then returns it to PENDING on that entry.
"""

from __future__ import annotations

import asyncio
import time

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import RateLimitExpired
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    InstrumentState,
    SheetExecutionState,
)


def _sheet(max_retries: int = 1) -> SheetExecutionState:
    sheet = SheetExecutionState(
        sheet_num=1,
        instrument_name="codex-cli",
        fallback_chain=["claude-code", "opencode"],
        max_retries=max_retries,
    )
    sheet.remember_primary_identity()
    return sheet


def _baton(sheet: SheetExecutionState, limits: dict[str, float | None]) -> BatonCore:
    baton = BatonCore()
    for name in ("codex-cli", "claude-code", "opencode"):
        baton._instruments[name] = InstrumentState(name=name, max_concurrent=4)
    now = time.monotonic()
    for name, delta in limits.items():
        inst = baton._instruments[name]
        if delta is not None:
            inst.rate_limited = True
            inst.rate_limit_expires_at = now + delta
    baton.register_job("j1", {1: sheet}, {1: []})
    return baton


def _exhaust_at_end_of_chain(sheet: SheetExecutionState) -> None:
    sheet.current_instrument_index = len(sheet.fallback_chain)
    sheet.instrument_name = "opencode"
    sheet.normal_attempts = sheet.max_retries


def test_exhausted_chain_waits_on_earliest_expiry_and_is_redispatched() -> None:
    sheet = _sheet()
    _exhaust_at_end_of_chain(sheet)
    baton = _baton(sheet, {"codex-cli": 7200.0, "claude-code": 600.0})

    asyncio.run(baton._handle_exhaustion("j1", 1, sheet))

    assert sheet.status == BatonSheetStatus.WAITING
    assert sheet.instrument_name == "claude-code"
    assert sheet.normal_attempts == 0
    baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-code"))
    assert sheet.status == BatonSheetStatus.PENDING
    assert sheet.instrument_name == "claude-code"


def test_primary_is_a_candidate() -> None:
    sheet = _sheet()
    _exhaust_at_end_of_chain(sheet)
    baton = _baton(sheet, {"codex-cli": 300.0, "claude-code": 900.0})

    asyncio.run(baton._handle_exhaustion("j1", 1, sheet))

    assert sheet.status == BatonSheetStatus.WAITING
    assert sheet.instrument_name == "codex-cli"
    assert sheet.current_instrument_index == 0


def test_no_future_expiry_fails_as_before() -> None:
    sheet = _sheet()
    _exhaust_at_end_of_chain(sheet)
    baton = _baton(sheet, {"codex-cli": -5.0, "claude-code": None})
    sheet.error_message = "opencode exit 1"

    asyncio.run(baton._handle_exhaustion("j1", 1, sheet))

    assert sheet.status == BatonSheetStatus.FAILED


def test_unlimited_fallback_is_still_preferred_to_waiting() -> None:
    sheet = _sheet()
    baton = _baton(sheet, {"codex-cli": 600.0})
    sheet.normal_attempts = sheet.max_retries

    asyncio.run(baton._handle_exhaustion("j1", 1, sheet))

    assert sheet.status == BatonSheetStatus.PENDING
    assert sheet.instrument_name == "claude-code"


def test_second_limit_after_wait_waits_again_other_failure_fails() -> None:
    sheet = _sheet()
    _exhaust_at_end_of_chain(sheet)
    baton = _baton(sheet, {"claude-code": 600.0})
    asyncio.run(baton._handle_exhaustion("j1", 1, sheet))
    assert sheet.status == BatonSheetStatus.WAITING

    # Wait ends; the entry fails for another reason with no limit standing.
    baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-code"))
    # The rebound entry still has "opencode" after it: that fallback is used first.
    asyncio.run(baton._handle_exhaustion("j1", 1, sheet))
    sheet.normal_attempts = sheet.max_retries
    _exhaust_at_end_of_chain(sheet)
    sheet.error_message = "boom"
    asyncio.run(baton._handle_exhaustion("j1", 1, sheet))
    assert sheet.status == BatonSheetStatus.FAILED
