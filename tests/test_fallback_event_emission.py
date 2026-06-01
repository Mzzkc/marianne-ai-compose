"""TDD tests for InstrumentFallback event emission and EventBus publishing.

The baton core emits InstrumentFallback events when instrument fallbacks
occur — at dispatch time (``unavailable``) and at exhaustion time (reason
derived from the last attempt). These events must be collected by the
core and published to the EventBus by the adapter for observability.

Reason derivation for exhaustion-time fallback is covered by
``tests/test_baton_fallback_reason_derivation.py``.

Gap identified: _check_and_fallback_unavailable() and _handle_exhaustion()
log at INFO but never emit InstrumentFallback events. The event type exists,
the handler exists (passthrough), but the EventBus never sees them.

Tests:
1. Core collects InstrumentFallback on unavailable fallback
2. Core collects InstrumentFallback on exhaustion-time fallback
3. No fallback events emitted when no fallback occurs
4. Multiple fallbacks in a chain produce multiple events
5. Dispatch-time fallback produces event
6. Adapter publishes fallback events to EventBus
7. drain_fallback_events clears the list

TDD: Red first, then green.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.events import InstrumentFallback
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
    max_retries: int = 2,
) -> SheetExecutionState:
    return SheetExecutionState(
        sheet_num=num,
        instrument_name=instrument,
        fallback_chain=fallbacks or [],
        max_retries=max_retries,
    )


def _register_instrument(baton: BatonCore, name: str) -> None:
    """Register an instrument directly on baton._instruments."""
    baton._instruments[name] = InstrumentState(name=name, max_concurrent=4)


class TestCoreFallbackEventEmission:
    """BatonCore._fallback_events collects InstrumentFallback when
    fallbacks happen in _check_and_fallback_unavailable and _handle_exhaustion."""

    def test_unavailable_fallback_emits_event(self) -> None:
        """When instrument is unavailable (circuit breaker OPEN),
        an InstrumentFallback event is collected."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        baton.register_job("j1", {1: sheet}, {1: []})
        # Mark claude-code as unavailable
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN
        _register_instrument(baton, "gemini-cli")

        result = baton._check_and_fallback_unavailable(sheet, "j1")

        assert result is True
        assert len(baton._fallback_events) == 1
        ev = baton._fallback_events[0]
        assert isinstance(ev, InstrumentFallback)
        assert ev.from_instrument == "claude-code"
        assert ev.to_instrument == "gemini-cli"
        assert ev.reason == "unavailable"
        assert ev.job_id == "j1"
        assert ev.sheet_num == 1

    def test_circuit_breaker_fallback_emits_event(self) -> None:
        """When a circuit breaker is OPEN, fallback emits an event."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        baton.register_job("j1", {1: sheet}, {1: []})
        _register_instrument(baton, "claude-code")
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN
        _register_instrument(baton, "gemini-cli")

        result = baton._check_and_fallback_unavailable(sheet, "j1")

        assert result is True
        assert len(baton._fallback_events) == 1
        ev = baton._fallback_events[0]
        assert ev.from_instrument == "claude-code"
        assert ev.to_instrument == "gemini-cli"
        assert ev.reason == "unavailable"

    def test_no_fallback_no_event(self) -> None:
        """When instrument is available, no fallback event emitted."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        baton.register_job("j1", {1: sheet}, {1: []})
        _register_instrument(baton, "claude-code")

        result = baton._check_and_fallback_unavailable(sheet, "j1")

        assert result is False
        assert len(baton._fallback_events) == 0

    async def test_exhaustion_fallback_emits_event(self) -> None:
        """When retry budget is exhausted and a fallback is available,
        _handle_exhaustion emits an InstrumentFallback event.

        The event's ``reason`` is derived from the last recorded attempt
        (see ``BatonCore._derive_fallback_reason`` and
        ``tests/test_baton_fallback_reason_derivation.py``). With no
        recorded attempts the reason falls back to the generic
        ``"exhausted"`` — this test focuses on event emission mechanics,
        not classification.
        """
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"], max_retries=2)
        sheet.normal_attempts = 2  # Exhausted
        sheet.completion_attempts = 5
        baton.register_job("j1", {1: sheet}, {1: []})
        _register_instrument(baton, "claude-code")
        _register_instrument(baton, "gemini-cli")

        await baton._handle_exhaustion("j1", 1, sheet)

        assert len(baton._fallback_events) == 1
        ev = baton._fallback_events[0]
        assert isinstance(ev, InstrumentFallback)
        assert ev.from_instrument == "claude-code"
        assert ev.to_instrument == "gemini-cli"
        assert ev.reason == "exhausted"
        assert ev.job_id == "j1"
        assert ev.sheet_num == 1

    async def test_no_fallback_on_exhaustion_no_event(self) -> None:
        """When exhausted with no fallback chain, no event emitted."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", [], max_retries=2)
        sheet.normal_attempts = 2
        sheet.completion_attempts = 5
        baton.register_job("j1", {1: sheet}, {1: []})
        _register_instrument(baton, "claude-code")

        await baton._handle_exhaustion("j1", 1, sheet)

        assert len(baton._fallback_events) == 0

    def test_drain_fallback_events_clears_list(self) -> None:
        """drain_fallback_events() returns collected events and clears."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        baton.register_job("j1", {1: sheet}, {1: []})
        # Mark claude-code as unavailable via circuit breaker
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN
        _register_instrument(baton, "gemini-cli")

        baton._check_and_fallback_unavailable(sheet, "j1")
        assert len(baton._fallback_events) == 1

        events = baton.drain_fallback_events()
        assert len(events) == 1
        assert len(baton._fallback_events) == 0

    def test_multiple_fallbacks_produce_multiple_events(self) -> None:
        """Two sheets falling back produce two events."""
        baton = BatonCore()
        sheet1 = _make_sheet(1, "claude-code", ["gemini-cli"])
        sheet2 = _make_sheet(2, "claude-code", ["ollama"])
        baton.register_job("j1", {1: sheet1, 2: sheet2}, {1: [], 2: []})
        # Mark primary as unavailable
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN
        _register_instrument(baton, "gemini-cli")
        _register_instrument(baton, "ollama")

        baton._check_and_fallback_unavailable(sheet1, "j1")
        baton._check_and_fallback_unavailable(sheet2, "j1")

        assert len(baton._fallback_events) == 2
        assert baton._fallback_events[0].to_instrument == "gemini-cli"
        assert baton._fallback_events[1].to_instrument == "ollama"


class TestDispatchFallbackEventEmission:
    """Fallback events are emitted during dispatch_ready() when
    instruments are unavailable at dispatch time."""

    @pytest.mark.asyncio
    async def test_dispatch_fallback_produces_event(self) -> None:
        """When dispatch finds a circuit-broken instrument with a fallback,
        an InstrumentFallback event is collected."""
        baton = BatonCore()
        sheet = _make_sheet(1, "claude-code", ["gemini-cli"])
        sheet.status = BatonSheetStatus.READY
        baton.register_job("j1", {1: sheet}, {1: []})
        _register_instrument(baton, "claude-code")
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN
        _register_instrument(baton, "gemini-cli")

        dispatched: list[tuple[str, int]] = []

        async def track(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
            dispatched.append((job_id, sheet_num))

        config = DispatchConfig(open_circuit_breakers={"claude-code"})
        await dispatch_ready(baton, config, track)

        # Sheet should have been dispatched on gemini-cli
        assert len(dispatched) == 1
        assert dispatched[0] == ("j1", 1)
        # Fallback event should have been emitted
        assert len(baton._fallback_events) >= 1
        ev = baton._fallback_events[0]
        assert ev.from_instrument == "claude-code"
        assert ev.to_instrument == "gemini-cli"
        assert ev.reason == "unavailable"


class TestAdapterFallbackEventPublishing:
    """The adapter drains fallback events from the core and publishes
    them to the EventBus after each event processing cycle."""

    @pytest.mark.asyncio
    async def test_adapter_publishes_fallback_events(self) -> None:
        """After processing an event that triggers a fallback,
        the adapter publishes InstrumentFallback to the EventBus."""
        from marianne.daemon.baton.adapter import BatonAdapter

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        adapter = BatonAdapter(event_bus=mock_bus)

        # Inject a fallback event into the core's _fallback_events
        fallback_ev = InstrumentFallback(
            job_id="j1",
            sheet_num=1,
            from_instrument="claude-code",
            to_instrument="gemini-cli",
            reason="unavailable",
        )
        adapter.baton._fallback_events.append(fallback_ev)

        # Call the publish method
        await adapter._publish_fallback_events()

        # EventBus should have received the observer-format event
        assert mock_bus.publish.called
        published = mock_bus.publish.call_args[0][0]
        assert published["event"] == "baton.instrument.fallback"
        assert published["data"]["from_instrument"] == "claude-code"
        assert published["data"]["to_instrument"] == "gemini-cli"

        # Core's events should be drained
        assert len(adapter.baton._fallback_events) == 0

    @pytest.mark.asyncio
    async def test_adapter_no_publish_when_no_fallback(self) -> None:
        """When no fallback events exist, nothing is published."""
        from marianne.daemon.baton.adapter import BatonAdapter

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        adapter = BatonAdapter(event_bus=mock_bus)

        await adapter._publish_fallback_events()

        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_adapter_no_publish_when_no_bus(self) -> None:
        """When no EventBus, fallback events are still drained."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(event_bus=None)
        fallback_ev = InstrumentFallback(
            job_id="j1",
            sheet_num=1,
            from_instrument="claude-code",
            to_instrument="gemini-cli",
            reason="unavailable",
        )
        adapter.baton._fallback_events.append(fallback_ev)

        await adapter._publish_fallback_events()

        # Events drained even without bus
        assert len(adapter.baton._fallback_events) == 0
