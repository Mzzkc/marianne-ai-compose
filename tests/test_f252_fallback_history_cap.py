"""F-252: Instrument fallback history must be bounded.

instrument_fallback_history on SheetState and fallback_history on
SheetExecutionState grow unboundedly with each fallback event. In
pathological scenarios (many fallback transitions across retries),
a single sheet could accumulate thousands of records, bloating state
files and memory. Error history already has MAX_ERROR_HISTORY = 50.
Fallback history needs the same treatment.

TDD: RED first — these tests define the contract.
"""

from __future__ import annotations

import datetime

import pytest

from marianne.core.checkpoint import (
    MAX_INSTRUMENT_FALLBACK_HISTORY,
    SheetState,
)
from marianne.daemon.baton.state import (
    MAX_FALLBACK_HISTORY,
    SheetExecutionState,
)


class TestCheckpointFallbackHistoryCap:
    """SheetState.instrument_fallback_history must respect MAX_INSTRUMENT_FALLBACK_HISTORY."""

    def test_constant_exists(self) -> None:
        """MAX_INSTRUMENT_FALLBACK_HISTORY is defined and reasonable."""
        assert MAX_INSTRUMENT_FALLBACK_HISTORY > 0
        assert MAX_INSTRUMENT_FALLBACK_HISTORY <= 200  # sanity upper bound

    def test_add_fallback_within_limit(self) -> None:
        """Adding fallback records within the limit retains all of them."""
        state = SheetState(sheet_num=1)
        for i in range(5):
            state.add_fallback_to_history({
                "from": f"instrument-{i}",
                "to": f"instrument-{i + 1}",
                "reason": "rate_limit_exhausted",
                "timestamp": datetime.datetime.now(tz=datetime.UTC).isoformat(),
            })
        assert len(state.instrument_fallback_history) == 5

    def test_add_fallback_trims_at_limit(self) -> None:
        """Exceeding MAX_INSTRUMENT_FALLBACK_HISTORY trims oldest records."""
        state = SheetState(sheet_num=1)
        overflow = MAX_INSTRUMENT_FALLBACK_HISTORY + 20
        for i in range(overflow):
            state.add_fallback_to_history({
                "from": f"instrument-{i}",
                "to": f"instrument-{i + 1}",
                "reason": "unavailable",
                "timestamp": datetime.datetime.now(tz=datetime.UTC).isoformat(),
            })
        assert len(state.instrument_fallback_history) == MAX_INSTRUMENT_FALLBACK_HISTORY
        # Most recent records are preserved (FIFO trim — oldest dropped)
        last = state.instrument_fallback_history[-1]
        assert last["from"] == f"instrument-{overflow - 1}"

    def test_add_fallback_preserves_newest(self) -> None:
        """Trimming keeps the newest entries, not the oldest."""
        state = SheetState(sheet_num=1)
        for i in range(MAX_INSTRUMENT_FALLBACK_HISTORY + 10):
            state.add_fallback_to_history({
                "from": f"inst-{i}",
                "to": f"inst-{i + 1}",
                "reason": "rate_limit_exhausted",
                "timestamp": f"2026-04-05T{i:05d}",
            })
        first = state.instrument_fallback_history[0]
        # The first 10 entries (inst-0 through inst-9) should be gone
        assert first["from"] == "inst-10"


class TestBatonStateFallbackHistoryCap:
    """SheetExecutionState.advance_fallback() must trim fallback_history."""

    def test_constant_exists(self) -> None:
        """MAX_FALLBACK_HISTORY is defined."""
        assert MAX_FALLBACK_HISTORY > 0
        assert MAX_FALLBACK_HISTORY <= 200

    def test_advance_fallback_trims_history(self) -> None:
        """After many fallback transitions, history is capped."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="primary",
            max_retries=3,
            max_completion=5,
        )
        # Build a long fallback chain
        chain_size = MAX_FALLBACK_HISTORY + 20
        state.fallback_chain = [f"fallback-{i}" for i in range(chain_size)]

        # Advance through all of them
        for _i in range(chain_size):
            result = state.advance_fallback("rate_limit_exhausted")
            if result is None:
                break

        assert len(state.instrument_fallback_history) <= MAX_FALLBACK_HISTORY

    def test_advance_fallback_preserves_newest(self) -> None:
        """The trimmed history keeps recent transitions."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="primary",
            max_retries=3,
            max_completion=5,
        )
        chain_size = MAX_FALLBACK_HISTORY + 10
        state.fallback_chain = [f"fb-{i}" for i in range(chain_size)]

        for _i in range(chain_size):
            result = state.advance_fallback("unavailable")
            if result is None:
                break

        if len(state.instrument_fallback_history) == MAX_FALLBACK_HISTORY:
            # Last entry should be the most recent transition
            last = state.instrument_fallback_history[-1]
            assert "fb-" in last["to"]

    def test_serialization_preserves_trimmed_history(self) -> None:
        """to_dict()/from_dict() roundtrip respects the cap."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="primary",
            max_retries=3,
            max_completion=5,
        )
        chain_size = MAX_FALLBACK_HISTORY + 5
        state.fallback_chain = [f"fb-{i}" for i in range(chain_size)]

        for _i in range(chain_size):
            if state.advance_fallback("rate_limit_exhausted") is None:
                break

        data = state.to_dict()
        restored = SheetExecutionState.from_dict(data)
        assert len(restored.instrument_fallback_history) <= MAX_FALLBACK_HISTORY


class TestConsistency:
    """Both caps should use the same magnitude as MAX_ERROR_HISTORY."""

    def test_fallback_caps_are_consistent(self) -> None:
        """Both checkpoint and baton caps match."""
        assert MAX_INSTRUMENT_FALLBACK_HISTORY == MAX_FALLBACK_HISTORY

    def test_reasonable_magnitude(self) -> None:
        """Cap is in a reasonable range (not too small, not too big)."""
        # Error history is 50. Fallback history should be similar magnitude.
        assert 20 <= MAX_INSTRUMENT_FALLBACK_HISTORY <= 100
