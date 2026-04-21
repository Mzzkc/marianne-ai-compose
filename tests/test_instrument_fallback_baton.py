"""TDD tests for instrument fallback in the baton execution core.

Tests cover:
1. InstrumentFallback event type creation and observer event conversion
2. SheetExecutionState fallback fields — chain, index, per-instrument attempts
3. sheets_to_execution_states copies fallback chain from Sheet entity
4. Rate limit exhaustion triggers fallback to next instrument
5. Retry exhaustion triggers fallback to next instrument
6. Each fallback instrument gets a fresh retry budget
7. All fallbacks exhausted → sheet fails normally
8. Empty fallback chain → no fallback, fail on exhaustion
9. Fallback event serialization roundtrip (to_dict/from_dict)
10. InstrumentFallback converts to observer event format

TDD: Red first, then green.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    InstrumentFallback,
    SheetAttemptResult,
    to_observer_event,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)

# =============================================================================
# InstrumentFallback Event
# =============================================================================


class TestInstrumentFallbackEvent:
    """The InstrumentFallback event records a fallback transition."""

    def test_create_with_required_fields(self) -> None:
        event = InstrumentFallback(
            job_id="j1",
            sheet_num=3,
            from_instrument="claude-code",
            to_instrument="gemini-cli",
            reason="rate_limit_exhausted",
        )
        assert event.job_id == "j1"
        assert event.sheet_num == 3
        assert event.from_instrument == "claude-code"
        assert event.to_instrument == "gemini-cli"
        assert event.reason == "rate_limit_exhausted"
        assert event.timestamp > 0

    def test_frozen_immutable(self) -> None:
        event = InstrumentFallback(
            job_id="j1",
            sheet_num=1,
            from_instrument="a",
            to_instrument="b",
            reason="unavailable",
        )
        with pytest.raises(AttributeError):
            event.reason = "changed"  # type: ignore[misc]

    def test_to_observer_event(self) -> None:
        event = InstrumentFallback(
            job_id="j1",
            sheet_num=5,
            from_instrument="claude-code",
            to_instrument="gemini-cli",
            reason="unavailable",
        )
        obs = to_observer_event(event)
        assert obs["job_id"] == "j1"
        assert obs["sheet_num"] == 5
        assert obs["event"] == "baton.instrument.fallback"
        assert obs["data"]["from_instrument"] == "claude-code"
        assert obs["data"]["to_instrument"] == "gemini-cli"
        assert obs["data"]["reason"] == "unavailable"


# =============================================================================
# SheetExecutionState — fallback fields
# =============================================================================


class TestSheetExecutionStateFallbackFields:
    """SheetExecutionState tracks fallback chain and position."""

    def test_default_empty_fallback_chain(self) -> None:
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        assert state.fallback_chain == []
        assert state.current_instrument_index == 0
        assert state.fallback_attempts == {}

    def test_with_fallback_chain(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli", "ollama"],
        )
        assert state.fallback_chain == ["gemini-cli", "ollama"]
        assert state.current_instrument_index == 0

    def test_has_fallback_available(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli", "ollama"],
        )
        assert state.has_fallback_available is True

    def test_no_fallback_when_chain_empty(self) -> None:
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        assert state.has_fallback_available is False

    def test_no_fallback_when_all_exhausted(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli"],
            current_instrument_index=1,
        )
        assert state.has_fallback_available is False

    def test_advance_to_next_fallback(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli", "ollama"],
        )
        next_instrument = state.advance_fallback("rate_limit_exhausted")
        assert next_instrument == "gemini-cli"
        assert state.current_instrument_index == 1
        assert state.instrument_name == "gemini-cli"
        assert state.normal_attempts == 0  # Fresh retry budget

    def test_advance_fallback_resets_retry_budget(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli"],
            max_retries=3,
        )
        state.normal_attempts = 3
        assert not state.can_retry
        state.advance_fallback("rate_limit_exhausted")
        assert state.normal_attempts == 0
        assert state.can_retry

    def test_advance_fallback_records_history(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli"],
        )
        state.advance_fallback("unavailable")
        assert len(state.instrument_fallback_history) == 1
        entry = state.instrument_fallback_history[0]
        assert entry["from"] == "claude-code"
        assert entry["to"] == "gemini-cli"
        assert entry["reason"] == "unavailable"
        assert "timestamp" in entry

    def test_advance_returns_none_when_exhausted(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="gemini-cli",
            fallback_chain=["gemini-cli"],
            current_instrument_index=1,
        )
        result = state.advance_fallback("rate_limit_exhausted")
        assert result is None

    def test_serialization_roundtrip(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli", "ollama"],
            current_instrument_index=1,
            fallback_attempts={"claude-code": 3, "gemini-cli": 1},
        )
        state.instrument_fallback_history.append(
            {
                "from": "claude-code",
                "to": "gemini-cli",
                "reason": "rate_limit_exhausted",
                "timestamp": "2026-04-06T00:00:00",
            }
        )
        d = state.to_dict()
        restored = SheetExecutionState.from_dict(d)
        assert restored.fallback_chain == ["gemini-cli", "ollama"]
        assert restored.current_instrument_index == 1
        assert restored.fallback_attempts == {"claude-code": 3, "gemini-cli": 1}
        assert len(restored.instrument_fallback_history) == 1

    def test_advance_saves_attempts_per_instrument(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["gemini-cli"],
            max_retries=3,
        )
        state.normal_attempts = 3
        state.advance_fallback("rate_limit_exhausted")
        assert state.fallback_attempts["claude-code"] == 3

    def test_advance_fallback_resets_model_gh337(self) -> None:
        """GH#337: advance_fallback must clear model so fallback uses own default.

        When a sheet falls back from instrument A (configured with model X) to
        instrument B (which does not support model X), the fallback must not
        carry model X forward. Otherwise BackendPool.acquire dispatches B with
        an incompatible -m flag and the CLI exits immediately.
        """
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="gemini-cli",
            model="gemini-3.1-pro-preview",
            fallback_chain=["goose", "claude-code"],
        )
        state.advance_fallback("rate_limit_exhausted")
        assert state.instrument_name == "goose"
        assert state.model is None, (
            "model must reset so BackendPool.acquire uses the fallback "
            "profile's default_model instead of inheriting the primary's model"
        )


# =============================================================================
# sheets_to_execution_states — copies fallback chain from Sheet entity
# =============================================================================


class TestSheetsToExecutionStatesFallback:
    """sheets_to_execution_states copies fallback chains from Sheet entities."""

    def test_copies_fallback_chain(self) -> None:
        from unittest.mock import MagicMock

        from marianne.daemon.baton.adapter import sheets_to_execution_states

        sheet = MagicMock()
        sheet.num = 1
        sheet.instrument_name = "claude-code"
        sheet.instrument_fallbacks = ["gemini-cli", "ollama"]

        states = sheets_to_execution_states([sheet])
        assert states[1].fallback_chain == ["gemini-cli", "ollama"]

    def test_empty_fallback_when_none_configured(self) -> None:
        from unittest.mock import MagicMock

        from marianne.daemon.baton.adapter import sheets_to_execution_states

        sheet = MagicMock()
        sheet.num = 1
        sheet.instrument_name = "claude-code"
        sheet.instrument_fallbacks = []

        states = sheets_to_execution_states([sheet])
        assert states[1].fallback_chain == []


# =============================================================================
# BatonCore — fallback on retry exhaustion
# =============================================================================


class TestBatonCoreFallbackOnExhaustion:
    """When retries are exhausted and a fallback is available, the baton
    switches to the next instrument instead of failing the sheet."""

    async def test_fallback_on_retry_exhaustion(self) -> None:
        """Sheet with fallback chain → exhaust retries → fallback to next."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        # Simulate failed attempt that exhausts retry budget
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
                error_message="backend crashed",
            )
        )

        # Sheet should have fallen back to gemini-cli, not FAILED
        restored_sheet = baton._jobs["j1"].sheets[1]
        assert restored_sheet.instrument_name == "gemini-cli"
        assert restored_sheet.status == BatonSheetStatus.PENDING
        assert restored_sheet.current_instrument_index == 1
        assert restored_sheet.normal_attempts == 0  # Fresh budget

    async def test_no_fallback_without_chain(self) -> None:
        """Sheet without fallback chain → exhaust retries → FAILED."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
        )
        baton.register_job("j1", {1: sheet}, {})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
            )
        )

        restored_sheet = baton._jobs["j1"].sheets[1]
        assert restored_sheet.status == BatonSheetStatus.FAILED

    async def test_all_fallbacks_exhausted_then_fail(self) -> None:
        """Exhaust retries on primary AND all fallbacks → FAILED."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        # First instrument exhausted → fallback
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
            )
        )

        s = baton._jobs["j1"].sheets[1]
        assert s.instrument_name == "gemini-cli"
        assert s.status == BatonSheetStatus.PENDING

        # Simulate second instrument also failing — set to RUNNING first
        s.status = BatonSheetStatus.IN_PROGRESS
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="gemini-cli",
                attempt=1,
                execution_success=False,
            )
        )

        s = baton._jobs["j1"].sheets[1]
        assert s.status == BatonSheetStatus.FAILED

    async def test_each_fallback_gets_fresh_retry_budget(self) -> None:
        """Each instrument in the fallback chain gets max_retries attempts."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=2,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        # First attempt fails (1 of 2) — should NOT fallback yet
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
            )
        )
        s = baton._jobs["j1"].sheets[1]
        assert s.instrument_name == "claude-code"
        assert s.normal_attempts == 1

    async def test_task_failure_does_not_trigger_fallback(self) -> None:
        """Execution success with 0% validation → retry same instrument."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=0,
                validations_total=5,
                validation_pass_rate=0.0,
            )
        )

        s = baton._jobs["j1"].sheets[1]
        # Should retry on same instrument (validation failure, not instrument failure)
        assert s.instrument_name == "claude-code"


# =============================================================================
# BatonCore — fallback records history
# =============================================================================


class TestBatonCoreFallbackHistory:
    """The baton records fallback transitions in sheet history."""

    async def test_fallback_recorded_in_history(self) -> None:
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=1,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
            )
        )

        s = baton._jobs["j1"].sheets[1]
        assert len(s.instrument_fallback_history) == 1
        assert s.instrument_fallback_history[0]["from"] == "claude-code"
        assert s.instrument_fallback_history[0]["to"] == "gemini-cli"


# =============================================================================
# BatonCore — instrument availability check
# =============================================================================


class TestInstrumentAvailabilityFallback:
    """When an instrument is unavailable at check time, the baton falls
    back immediately instead of attempting execution."""

    def test_unavailable_instrument_immediate_fallback(self) -> None:
        """Circuit breaker OPEN → fallback immediately."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        # Mark claude-code as unavailable via circuit breaker
        baton._instruments["claude-code"] = InstrumentState(
            name="claude-code",
            max_concurrent=4,
        )
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN

        # Register gemini-cli as available
        baton._instruments["gemini-cli"] = InstrumentState(
            name="gemini-cli",
            max_concurrent=4,
        )

        s = baton._jobs["j1"].sheets[1]
        result = baton._check_and_fallback_unavailable(s, "j1")

        assert result is True
        assert s.instrument_name == "gemini-cli"
        assert s.current_instrument_index == 1
        assert s.status == BatonSheetStatus.PENDING

    def test_available_instrument_no_fallback(self) -> None:
        """Healthy instrument → no fallback."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        baton._instruments["claude-code"] = InstrumentState(
            name="claude-code",
            max_concurrent=4,
        )

        s = baton._jobs["j1"].sheets[1]
        result = baton._check_and_fallback_unavailable(s, "j1")

        assert result is False
        assert s.instrument_name == "claude-code"

    def test_rate_limited_instrument_fallback(self) -> None:
        """Rate-limited instrument → fallback if chain available."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        # Mark claude-code as rate-limited
        baton._instruments["claude-code"].rate_limited = True

        s = baton._jobs["j1"].sheets[1]
        result = baton._check_and_fallback_unavailable(s, "j1")

        assert result is True
        assert s.instrument_name == "gemini-cli"

    def test_no_fallback_when_chain_exhausted(self) -> None:
        """Unavailable instrument + exhausted chain → no fallback."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli"],
            current_instrument_index=1,  # Already on last fallback
        )
        baton.register_job("j1", {1: sheet}, {})

        baton._instruments["claude-code"] = InstrumentState(
            name="claude-code",
            max_concurrent=4,
        )
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN

        s = baton._jobs["j1"].sheets[1]
        result = baton._check_and_fallback_unavailable(s, "j1")

        assert result is False
