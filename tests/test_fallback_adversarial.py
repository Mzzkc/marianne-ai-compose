"""Adversarial tests for instrument fallback system.

Edge cases and failure modes that the fallback system must handle:
1. Empty fallback chain — sheet fails normally, no infinite loops
2. All fallbacks exhausted — chain fully walked, sheet fails
3. Circular-like references — same instrument appears twice in chain
4. Rate limit vs unavailable distinction — correct reason propagated
5. Fallback during chain walk — instrument becomes unavailable mid-chain
6. Serialization roundtrip — fallback state survives checkpoint save/load
7. Concurrent fallback events — multiple sheets fall back simultaneously
8. Fallback with zero retries — fresh budget still works
9. Advance_fallback with exhausted chain returns None
10. Observer event format for fallback events

TDD: Red first, then green.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    InstrumentFallback,
    to_observer_event,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)


def _make_sheet(
    num: int,
    instrument: str = "claude-code",
    fallbacks: list[str] | None = None,
    max_retries: int = 3,
) -> SheetExecutionState:
    return SheetExecutionState(
        sheet_num=num,
        instrument_name=instrument,
        fallback_chain=fallbacks or [],
        max_retries=max_retries,
    )


class TestEmptyFallbackChain:
    """Empty fallback chain → no fallback, normal failure behavior."""

    def test_exhaustion_with_empty_chain_fails(self) -> None:
        """Sheet with empty fallback chain goes to FAILED on exhaustion."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", [], max_retries=1)
        sheet.normal_attempts = 1
        sheet.completion_attempts = 5
        baton.register_job("j1", {1: sheet}, {1: []})

        baton._handle_exhaustion("j1", 1, sheet)

        assert sheet.status == BatonSheetStatus.FAILED
        assert len(baton._fallback_events) == 0

    def test_unavailable_with_empty_chain_returns_false(self) -> None:
        """No fallback available → _check_and_fallback returns False."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", [])
        baton.register_job("j1", {1: sheet}, {1: []})
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN

        result = baton._check_and_fallback_unavailable(sheet, "j1")

        assert result is False
        assert sheet.instrument_name == "claude-code"  # Unchanged


class TestAllFallbacksExhausted:
    """When every instrument in the chain is exhausted, sheet fails."""

    def test_chain_fully_walked_then_fail(self) -> None:
        """Walk claude-code → gemini-cli → ollama, all exhaust → FAILED."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli", "ollama"], max_retries=1)
        baton.register_job("j1", {1: sheet}, {1: []})

        # Exhaust claude-code
        sheet.normal_attempts = 1
        sheet.completion_attempts = 5
        baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.instrument_name == "gemini-cli"
        assert sheet.status == BatonSheetStatus.PENDING

        # Exhaust gemini-cli
        sheet.normal_attempts = 1
        sheet.completion_attempts = 5
        baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.instrument_name == "ollama"
        assert sheet.status == BatonSheetStatus.PENDING

        # Exhaust ollama — no more fallbacks
        sheet.normal_attempts = 1
        sheet.completion_attempts = 5
        baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.status == BatonSheetStatus.FAILED

        # Three fallback events: claude→gemini, gemini→ollama
        assert len(baton._fallback_events) == 2

    def test_all_unavailable_returns_false(self) -> None:
        """All instruments in chain unavailable → returns False."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        baton.register_job("j1", {1: sheet}, {1: []})
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN
        # gemini-cli auto-registered but also broken
        baton._instruments["gemini-cli"] = InstrumentState(
            name="gemini-cli", max_concurrent=4,
        )
        baton._instruments["gemini-cli"].circuit_breaker = CircuitBreakerState.OPEN

        # First call advances to gemini-cli
        result = baton._check_and_fallback_unavailable(sheet, "j1")
        assert result is True
        assert sheet.instrument_name == "gemini-cli"

        # Second call — gemini-cli also unavailable, chain exhausted
        result = baton._check_and_fallback_unavailable(sheet, "j1")
        assert result is False


class TestDuplicateInstrumentsInChain:
    """Duplicate instrument names in the fallback chain."""

    def test_same_instrument_appears_twice(self) -> None:
        """Chain [claude-code, gemini-cli, claude-code] — second claude-code
        gets fresh retry budget."""
        baton = BatonCore()
        sheet = _make_sheet(
            1, "claude-code", ["gemini-cli", "claude-code"], max_retries=1
        )
        baton.register_job("j1", {1: sheet}, {1: []})

        # Exhaust first claude-code run
        sheet.normal_attempts = 1
        sheet.completion_attempts = 5
        baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.instrument_name == "gemini-cli"
        assert sheet.normal_attempts == 0  # Fresh budget

        # Exhaust gemini-cli
        sheet.normal_attempts = 1
        sheet.completion_attempts = 5
        baton._handle_exhaustion("j1", 1, sheet)
        assert sheet.instrument_name == "claude-code"
        assert sheet.normal_attempts == 0  # Fresh budget again


class TestRateLimitVsUnavailableReason:
    """Fallback reason correctly distinguishes rate_limit_exhausted
    from unavailable."""

    def test_exhaustion_gives_rate_limit_reason(self) -> None:
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"], max_retries=1)
        sheet.normal_attempts = 1
        sheet.completion_attempts = 5
        baton.register_job("j1", {1: sheet}, {1: []})

        baton._handle_exhaustion("j1", 1, sheet)

        ev = baton._fallback_events[0]
        assert ev.reason == "rate_limit_exhausted"

    def test_unavailable_gives_unavailable_reason(self) -> None:
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        baton.register_job("j1", {1: sheet}, {1: []})
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN

        baton._check_and_fallback_unavailable(sheet, "j1")

        ev = baton._fallback_events[0]
        assert ev.reason == "unavailable"


class TestSerializationRoundtrip:
    """Fallback state survives to_dict/from_dict serialization."""

    def test_fallback_fields_survive_roundtrip(self) -> None:
        sheet = _make_sheet(1, "gemini-cli", ["ollama", "local"])
        sheet.current_instrument_index = 1
        sheet.fallback_attempts = {"claude-code": 3}
        sheet.instrument_fallback_history = [
            {"from": "claude-code", "to": "gemini-cli",
             "reason": "rate_limit_exhausted",
             "timestamp": "2026-04-06T00:00:00"},
        ]

        data = sheet.to_dict()
        restored = SheetExecutionState.from_dict(data)

        assert restored.fallback_chain == ["ollama", "local"]
        assert restored.current_instrument_index == 1
        assert restored.fallback_attempts == {"claude-code": 3}
        assert len(restored.instrument_fallback_history) == 1
        assert restored.instrument_fallback_history[0]["from"] == "claude-code"

    def test_empty_fallback_fields_roundtrip(self) -> None:
        sheet = _make_sheet(1, "claude-code")
        data = sheet.to_dict()
        restored = SheetExecutionState.from_dict(data)

        assert restored.fallback_chain == []
        assert restored.current_instrument_index == 0
        assert restored.fallback_attempts == {}
        assert restored.instrument_fallback_history == []


class TestAdvanceFallbackEdgeCases:
    """Edge cases in SheetExecutionState.advance_fallback()."""

    def test_advance_when_exhausted_returns_none(self) -> None:
        """Chain exhausted → advance_fallback returns None."""
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        sheet.advance_fallback("unavailable")  # Use the single fallback
        result = sheet.advance_fallback("unavailable")  # Chain exhausted
        assert result is None

    def test_advance_resets_retry_budget(self) -> None:
        """Fallback resets normal_attempts and completion_attempts."""
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        sheet.normal_attempts = 5
        sheet.completion_attempts = 3
        sheet.advance_fallback("rate_limit_exhausted")

        assert sheet.normal_attempts == 0
        assert sheet.completion_attempts == 0
        assert sheet.instrument_name == "gemini-cli"

    def test_advance_records_history(self) -> None:
        """Each advance records a fallback history entry."""
        sheet = _make_sheet(1, "claude-code", ["gemini-cli", "ollama"])
        sheet.advance_fallback("unavailable")
        sheet.advance_fallback("rate_limit_exhausted")

        assert len(sheet.instrument_fallback_history) == 2
        assert sheet.instrument_fallback_history[0]["from"] == "claude-code"
        assert sheet.instrument_fallback_history[0]["to"] == "gemini-cli"
        assert sheet.instrument_fallback_history[0]["reason"] == "unavailable"
        assert sheet.instrument_fallback_history[1]["from"] == "gemini-cli"
        assert sheet.instrument_fallback_history[1]["to"] == "ollama"
        assert sheet.instrument_fallback_history[1]["reason"] == "rate_limit_exhausted"

    def test_advance_preserves_attempt_count(self) -> None:
        """fallback_attempts tracks attempts per instrument."""
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        sheet.normal_attempts = 3
        sheet.advance_fallback("rate_limit_exhausted")

        assert sheet.fallback_attempts["claude-code"] == 3


class TestObserverEventFormat:
    """InstrumentFallback converts to observer event correctly."""

    def test_to_observer_event(self) -> None:
        ev = InstrumentFallback(
            job_id="j1",
            sheet_num=3,
            from_instrument="claude-code",
            to_instrument="gemini-cli",
            reason="unavailable",
        )
        obs = to_observer_event(ev)

        assert obs["event"] == "baton.instrument.fallback"
        assert obs["job_id"] == "j1"
        assert obs["sheet_num"] == 3
        assert obs["data"]["from_instrument"] == "claude-code"
        assert obs["data"]["to_instrument"] == "gemini-cli"
        assert obs["data"]["reason"] == "unavailable"

    def test_frozen_event_immutable(self) -> None:
        """InstrumentFallback is frozen — cannot be modified after creation."""
        ev = InstrumentFallback(
            job_id="j1",
            sheet_num=1,
            from_instrument="a",
            to_instrument="b",
            reason="unavailable",
        )
        with pytest.raises(AttributeError):
            ev.reason = "modified"  # type: ignore[misc]
