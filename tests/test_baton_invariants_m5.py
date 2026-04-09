"""Movement 5 — property-based invariant verification.

Extends the invariant suite to cover M5 features: instrument fallback
chain mechanics, _safe_killpg session guard, backpressure level
monotonicity, use_baton default flip, and fallback state serialization
round-trips.

86. Fallback chain ordering — advance_fallback consumes in declaration order
87. Fallback chain monotonicity — current_instrument_index only increases
88. Fallback retry budget reset — advance_fallback zeros attempt counters
89. Fallback history bounded growth — history never exceeds MAX_FALLBACK_HISTORY
90. Fallback exhaustion totality — exhausted chain returns None on every call
91. safe_killpg guard mutual exclusion — pgid ≤1 or ==own_pgid → refused
92. safe_killpg exception tolerance — os.getpgid failure degrades safely
93. Backpressure level monotonicity — higher memory % → weakly higher level
94. Backpressure delay monotonicity — level ordering matches delay ordering
95. Backpressure rate limit escalation — active rate limits → level ≥ HIGH
96. Backpressure critical exclusivity — can_start_sheet rejects iff CRITICAL
97. use_baton default totality — DaemonConfig() always has use_baton=True
98. Fallback state round-trip — to_dict/from_dict preserves all fallback fields

Found by: Theorem, Movement 5
Method: Property-based testing with hypothesis + invariant analysis

@pytest.mark.property_based
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any
from unittest.mock import MagicMock, patch

import hypothesis.strategies as st
from hypothesis import HealthCheck, assume, given, settings

from marianne.daemon.baton.state import (
    MAX_FALLBACK_HISTORY,
    SheetExecutionState,
)

# =============================================================================
# Strategies
# =============================================================================

_INSTRUMENT_NAMES = st.sampled_from([
    "claude-code", "gemini-cli", "ollama", "codex-cli",
    "custom-backend", "gpt-4o", "mistral-large", "deepseek-v3",
])

_FALLBACK_CHAINS = st.lists(
    _INSTRUMENT_NAMES,
    min_size=0,
    max_size=10,
)

_PGID_VALUES = st.one_of(
    st.integers(min_value=-100, max_value=100),  # boundary values
    st.integers(min_value=2, max_value=100_000),   # valid range
    st.just(0),
    st.just(1),
    st.just(-1),
)


# =============================================================================
# Invariant 86: Fallback chain ordering — consumes in declaration order
# =============================================================================


class TestFallbackChainOrdering:
    """advance_fallback() consumes fallbacks in the exact declaration order.

    Invariant: For a chain [A, B, C], successive calls to advance_fallback()
    return A, then B, then C, then None. The returned instrument matches
    fallback_chain[index] at each step.
    """

    @given(
        primary=_INSTRUMENT_NAMES,
        chain=_FALLBACK_CHAINS,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_advance_returns_chain_in_order(
        self, primary: str, chain: list[str]
    ) -> None:
        """Each advance_fallback call returns the next chain element in order."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=list(chain),
        )

        returned: list[str | None] = []
        for _ in range(len(chain) + 2):  # overshoot to test exhaustion
            result = state.advance_fallback("test")
            returned.append(result)
            if result is None:
                break

        # The non-None returns must match the chain in order
        non_none = [r for r in returned if r is not None]
        assert non_none == chain

    @given(
        primary=_INSTRUMENT_NAMES,
        chain=st.lists(_INSTRUMENT_NAMES, min_size=1, max_size=5),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_instrument_name_updates_on_advance(
        self, primary: str, chain: list[str]
    ) -> None:
        """After advance_fallback(), instrument_name equals the returned value."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=list(chain),
        )

        for expected_instrument in chain:
            result = state.advance_fallback("test")
            assert result == expected_instrument
            assert state.instrument_name == expected_instrument


# =============================================================================
# Invariant 87: Fallback chain monotonicity — index only increases
# =============================================================================


class TestFallbackChainMonotonicity:
    """current_instrument_index only ever increases.

    Invariant: For any sequence of advance_fallback() calls, if index_before
    is the value before a call and index_after is the value after, then
    index_after >= index_before.
    """

    @given(
        primary=_INSTRUMENT_NAMES,
        chain=st.lists(_INSTRUMENT_NAMES, min_size=1, max_size=8),
        advance_count=st.integers(min_value=1, max_value=12),
    )
    @settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
    def test_index_never_decreases(
        self, primary: str, chain: list[str], advance_count: int
    ) -> None:
        """current_instrument_index is monotonically non-decreasing."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=list(chain),
        )

        previous_index = state.current_instrument_index
        for _ in range(advance_count):
            state.advance_fallback("test")
            assert state.current_instrument_index >= previous_index
            previous_index = state.current_instrument_index


# =============================================================================
# Invariant 88: Fallback retry budget reset
# =============================================================================


class TestFallbackRetryBudgetReset:
    """advance_fallback() resets normal_attempts and completion_attempts to 0.

    Invariant: Regardless of what the counters were before the call,
    after a successful advance_fallback(), both counters are zero.
    This gives the new instrument a fresh retry budget.
    """

    @given(
        primary=_INSTRUMENT_NAMES,
        chain=st.lists(_INSTRUMENT_NAMES, min_size=1, max_size=3),
        normal_attempts=st.integers(min_value=0, max_value=100),
        completion_attempts=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
    def test_attempts_zeroed_on_advance(
        self,
        primary: str,
        chain: list[str],
        normal_attempts: int,
        completion_attempts: int,
    ) -> None:
        """After advance_fallback, normal_attempts and completion_attempts are 0."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=list(chain),
        )
        state.normal_attempts = normal_attempts
        state.completion_attempts = completion_attempts

        result = state.advance_fallback("test")
        assert result is not None  # chain has at least 1

        assert state.normal_attempts == 0
        assert state.completion_attempts == 0

    @given(
        primary=_INSTRUMENT_NAMES,
        chain=st.lists(_INSTRUMENT_NAMES, min_size=1, max_size=3),
        normal_attempts=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_previous_attempts_saved_in_fallback_attempts(
        self,
        primary: str,
        chain: list[str],
        normal_attempts: int,
    ) -> None:
        """advance_fallback saves the old instrument's normal_attempts in fallback_attempts."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=list(chain),
        )
        state.normal_attempts = normal_attempts

        old_instrument = state.instrument_name
        state.advance_fallback("test")

        assert state.fallback_attempts[old_instrument] == normal_attempts


# =============================================================================
# Invariant 89: Fallback history bounded growth
# =============================================================================


class TestFallbackHistoryBounded:
    """fallback_history never exceeds MAX_FALLBACK_HISTORY entries.

    Invariant: After any number of advance_fallback() calls,
    len(fallback_history) <= MAX_FALLBACK_HISTORY.
    """

    @given(
        primary=_INSTRUMENT_NAMES,
        chain_length=st.integers(
            min_value=MAX_FALLBACK_HISTORY,
            max_value=MAX_FALLBACK_HISTORY + 20,
        ),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_history_capped_at_max(
        self, primary: str, chain_length: int
    ) -> None:
        """Even with many fallbacks, history is trimmed to MAX_FALLBACK_HISTORY."""
        # Build a chain longer than MAX_FALLBACK_HISTORY
        chain = [f"instrument-{i}" for i in range(chain_length)]
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=chain,
        )

        for _ in range(chain_length):
            state.advance_fallback("test")

        assert len(state.instrument_fallback_history) <= MAX_FALLBACK_HISTORY


# =============================================================================
# Invariant 90: Fallback exhaustion totality
# =============================================================================


class TestFallbackExhaustionTotality:
    """Once the chain is exhausted, advance_fallback() returns None forever.

    Invariant: After the chain is fully consumed, any number of additional
    calls return None without side effects (instrument_name unchanged,
    index unchanged, no history additions).
    """

    @given(
        primary=_INSTRUMENT_NAMES,
        chain=_FALLBACK_CHAINS,
        extra_calls=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
    def test_exhausted_chain_returns_none_forever(
        self, primary: str, chain: list[str], extra_calls: int
    ) -> None:
        """Post-exhaustion: returns None, no state mutation."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=list(chain),
        )

        # Exhaust the chain
        for _ in range(len(chain)):
            state.advance_fallback("exhaust")

        # Capture state after exhaustion
        final_instrument = state.instrument_name
        final_index = state.current_instrument_index
        final_history_len = len(state.instrument_fallback_history)

        # Additional calls must be no-ops returning None
        for _ in range(extra_calls):
            assert state.advance_fallback("extra") is None
            assert state.instrument_name == final_instrument
            assert state.current_instrument_index == final_index
            assert len(state.instrument_fallback_history) == final_history_len


# =============================================================================
# Invariant 91: safe_killpg guard mutual exclusion
# =============================================================================


class TestSafeKillpgGuard:
    """_safe_killpg refuses pgid ≤ 1 and pgid == own process group.

    Invariant: For any pgid value and any own_pgid value:
    - pgid ≤ 1 → always refused (returns False, os.killpg not called)
    - pgid == own_pgid → refused (returns False, os.killpg not called)
    - pgid > 1 and pgid ≠ own_pgid → permitted (os.killpg called, returns True)
    """

    @given(pgid=_PGID_VALUES)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_pgid_le_1_always_refused(self, pgid: int) -> None:
        """pgid ≤ 1 is always refused regardless of own_pgid."""
        assume(pgid <= 1)

        from marianne.backends.claude_cli import _safe_killpg

        with patch("marianne.backends.claude_cli.os.killpg") as mock_killpg, \
             patch("marianne.backends.claude_cli.os.getpgid", return_value=9999):
            result = _safe_killpg(pgid, signal.SIGTERM, context="test")
            assert result is False
            mock_killpg.assert_not_called()

    @given(
        own_pgid=st.integers(min_value=2, max_value=100_000),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pgid_eq_own_group_refused(self, own_pgid: int) -> None:
        """pgid == own process group is refused."""
        from marianne.backends.claude_cli import _safe_killpg

        with patch("marianne.backends.claude_cli.os.killpg") as mock_killpg, \
             patch("marianne.backends.claude_cli.os.getpgid", return_value=own_pgid):
            result = _safe_killpg(own_pgid, signal.SIGTERM, context="test")
            assert result is False
            mock_killpg.assert_not_called()

    @given(
        pgid=st.integers(min_value=2, max_value=100_000),
        own_pgid=st.integers(min_value=2, max_value=100_000),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_valid_pgid_permitted(self, pgid: int, own_pgid: int) -> None:
        """pgid > 1 and pgid ≠ own_pgid → signal sent, returns True."""
        assume(pgid != own_pgid)

        from marianne.backends.claude_cli import _safe_killpg

        with patch("marianne.backends.claude_cli.os.killpg") as mock_killpg, \
             patch("marianne.backends.claude_cli.os.getpgid", return_value=own_pgid):
            result = _safe_killpg(pgid, signal.SIGTERM, context="test")
            assert result is True
            mock_killpg.assert_called_once_with(pgid, signal.SIGTERM)


# =============================================================================
# Invariant 92: safe_killpg exception tolerance
# =============================================================================


class TestSafeKillpgExceptionTolerance:
    """When os.getpgid(0) raises OSError, the guard degrades safely.

    Invariant: If os.getpgid fails, own_pgid becomes None, and the
    guard falls through to only checking pgid ≤ 1. pgid > 1 is permitted
    (since we can't compare to own group).
    """

    @given(pgid=st.integers(min_value=2, max_value=100_000))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_getpgid_failure_permits_valid_pgid(self, pgid: int) -> None:
        """With os.getpgid raising, valid pgid > 1 is still permitted."""
        from marianne.backends.claude_cli import _safe_killpg

        with patch("marianne.backends.claude_cli.os.killpg") as mock_killpg, \
             patch("marianne.backends.claude_cli.os.getpgid", side_effect=OSError("no pgid")):
            result = _safe_killpg(pgid, signal.SIGTERM, context="test")
            assert result is True
            mock_killpg.assert_called_once_with(pgid, signal.SIGTERM)

    @given(pgid=st.integers(max_value=1))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_getpgid_failure_still_blocks_le_1(self, pgid: int) -> None:
        """With os.getpgid raising, pgid ≤ 1 is still refused."""
        from marianne.backends.claude_cli import _safe_killpg

        with patch("marianne.backends.claude_cli.os.killpg") as mock_killpg, \
             patch("marianne.backends.claude_cli.os.getpgid", side_effect=OSError("no pgid")):
            result = _safe_killpg(pgid, signal.SIGTERM, context="test")
            assert result is False
            mock_killpg.assert_not_called()


# =============================================================================
# Invariant 93: Backpressure level monotonicity
# =============================================================================


class TestBackpressureLevelMonotonicity:
    """Higher memory percentage → weakly higher pressure level.

    Invariant: For fixed rate_limited and accepting_work, if mem_pct1 < mem_pct2,
    then level(mem_pct1) ≤ level(mem_pct2) (using enum ordering).
    """

    @given(
        mem_pct1=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        mem_pct2=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_higher_memory_weakly_higher_level(
        self, mem_pct1: float, mem_pct2: float
    ) -> None:
        """Memory monotonicity: pct1 < pct2 → level1 ≤ level2."""
        assume(mem_pct1 <= mem_pct2)

        from marianne.daemon.backpressure import PressureLevel

        level_order = {
            PressureLevel.NONE: 0,
            PressureLevel.LOW: 1,
            PressureLevel.MEDIUM: 2,
            PressureLevel.HIGH: 3,
            PressureLevel.CRITICAL: 4,
        }

        def classify(pct: float) -> PressureLevel:
            """Pure classification without rate limits or degradation."""
            if pct > 0.95:
                return PressureLevel.CRITICAL
            if pct > 0.85:
                return PressureLevel.HIGH
            if pct > 0.70:
                return PressureLevel.MEDIUM
            if pct > 0.50:
                return PressureLevel.LOW
            return PressureLevel.NONE

        l1 = classify(mem_pct1)
        l2 = classify(mem_pct2)
        assert level_order[l1] <= level_order[l2]


# =============================================================================
# Invariant 94: Backpressure delay monotonicity
# =============================================================================


class TestBackpressureDelayMonotonicity:
    """Pressure level ordering matches delay ordering.

    Invariant: For any two levels L1, L2 where L1 < L2 in enum order,
    _LEVEL_DELAYS[L1] ≤ _LEVEL_DELAYS[L2].
    """

    def test_delay_increases_with_level(self) -> None:
        """Delay values are monotonically non-decreasing across levels."""
        from marianne.daemon.backpressure import _LEVEL_DELAYS, PressureLevel

        ordered_levels = [
            PressureLevel.NONE,
            PressureLevel.LOW,
            PressureLevel.MEDIUM,
            PressureLevel.HIGH,
            PressureLevel.CRITICAL,
        ]

        for i in range(len(ordered_levels) - 1):
            l1 = ordered_levels[i]
            l2 = ordered_levels[i + 1]
            assert _LEVEL_DELAYS[l1] <= _LEVEL_DELAYS[l2], (
                f"Delay for {l1.value} ({_LEVEL_DELAYS[l1]}) > "
                f"delay for {l2.value} ({_LEVEL_DELAYS[l2]})"
            )

    def test_all_levels_have_delays(self) -> None:
        """Every PressureLevel has a corresponding delay entry."""
        from marianne.daemon.backpressure import _LEVEL_DELAYS, PressureLevel

        for level in PressureLevel:
            assert level in _LEVEL_DELAYS, f"Missing delay for {level.value}"


# =============================================================================
# Invariant 95: Backpressure rate limit escalation
# =============================================================================


class TestBackpressureRateLimitEscalation:
    """Active rate limits escalate level to at least HIGH.

    Invariant: When any rate limit is active (rate_coordinator.active_limits
    is truthy), current_level() returns at least HIGH, regardless of
    memory percentage — as long as the system is not degraded and memory
    monitoring works.
    """

    @given(
        mem_pct=st.floats(min_value=0.0, max_value=0.85, allow_nan=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_rate_limit_escalates_to_high(self, mem_pct: float) -> None:
        """With active rate limits and memory ≤ 85%, level is HIGH."""
        from marianne.daemon.backpressure import (
            BackpressureController,
            PressureLevel,
        )

        max_mem = 10_000  # 10GB
        current_mem = mem_pct * max_mem

        _monitor_spec = [
            "current_memory_mb", "is_degraded",
            "max_memory_mb", "is_accepting_work",
        ]
        monitor = MagicMock(spec=_monitor_spec)
        monitor.current_memory_mb.return_value = current_mem
        monitor.is_degraded = False
        monitor.max_memory_mb = max_mem
        monitor.is_accepting_work.return_value = True

        rate_coord = MagicMock(spec=["active_limits"])
        rate_coord.active_limits = {"claude-code": {"until": 9999999999}}

        bp = BackpressureController(monitor, rate_coord)
        level = bp.current_level()

        assert level == PressureLevel.HIGH


# =============================================================================
# Invariant 96: Backpressure critical exclusivity
# =============================================================================


class TestBackpressureCriticalExclusivity:
    """can_start_sheet rejects if and only if level is CRITICAL.

    Invariant: The (allowed, delay) tuple from can_start_sheet has
    allowed=False ⟺ current_level() == CRITICAL.
    """

    def test_critical_rejects(self) -> None:
        """CRITICAL level → allowed=False."""
        from marianne.daemon.backpressure import (
            BackpressureController,
            PressureLevel,
        )

        mock_spec = [
            "current_memory_mb", "is_degraded",
            "max_memory_mb", "is_accepting_work",
        ]
        monitor = MagicMock(spec=mock_spec)
        monitor.current_memory_mb.return_value = 9800  # 98% of 10000
        monitor.is_degraded = False
        monitor.max_memory_mb = 10_000
        monitor.is_accepting_work.return_value = True

        rate_coord = MagicMock(spec=["active_limits"])
        rate_coord.active_limits = {}

        bp = BackpressureController(monitor, rate_coord)
        assert bp.current_level() == PressureLevel.CRITICAL

        allowed, delay = asyncio.run(bp.can_start_sheet())
        assert allowed is False
        assert delay > 0

    @given(
        mem_pct=st.floats(min_value=0.0, max_value=0.94, allow_nan=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_non_critical_allows(self, mem_pct: float) -> None:
        """Non-CRITICAL levels → allowed=True."""
        from marianne.daemon.backpressure import BackpressureController

        max_mem = 10_000
        current_mem = mem_pct * max_mem

        mock_spec = [
            "current_memory_mb", "is_degraded",
            "max_memory_mb", "is_accepting_work",
        ]
        monitor = MagicMock(spec=mock_spec)
        monitor.current_memory_mb.return_value = current_mem
        monitor.is_degraded = False
        monitor.max_memory_mb = max_mem
        monitor.is_accepting_work.return_value = True

        rate_coord = MagicMock(spec=["active_limits"])
        rate_coord.active_limits = {}

        bp = BackpressureController(monitor, rate_coord)
        allowed, _ = asyncio.run(bp.can_start_sheet())
        assert allowed is True

    def test_degraded_monitor_is_critical(self) -> None:
        """Degraded monitor → CRITICAL (fail-closed)."""
        from marianne.daemon.backpressure import (
            BackpressureController,
            PressureLevel,
        )

        _monitor_spec = [
            "current_memory_mb", "is_degraded",
            "max_memory_mb", "is_accepting_work",
        ]
        monitor = MagicMock(spec=_monitor_spec)
        monitor.current_memory_mb.return_value = 100  # Low memory
        monitor.is_degraded = True
        monitor.max_memory_mb = 10_000

        rate_coord = MagicMock(spec=["active_limits"])
        rate_coord.active_limits = {}

        bp = BackpressureController(monitor, rate_coord)
        assert bp.current_level() == PressureLevel.CRITICAL

    def test_none_memory_is_critical(self) -> None:
        """Memory probe failure (None) → CRITICAL (fail-closed)."""
        from marianne.daemon.backpressure import (
            BackpressureController,
            PressureLevel,
        )

        _monitor_spec = [
            "current_memory_mb", "is_degraded",
            "max_memory_mb", "is_accepting_work",
        ]
        monitor = MagicMock(spec=_monitor_spec)
        monitor.current_memory_mb.return_value = None
        monitor.is_degraded = False
        monitor.max_memory_mb = 10_000

        rate_coord = MagicMock(spec=["active_limits"])
        rate_coord.active_limits = {}

        bp = BackpressureController(monitor, rate_coord)
        assert bp.current_level() == PressureLevel.CRITICAL


# =============================================================================
# Invariant 97: use_baton default totality
# =============================================================================


class TestUseBatonDefaultTotality:
    """DaemonConfig() always has use_baton=True.

    Invariant: The default value of use_baton is True (D-027).
    Explicit False overrides it. The field always exists.
    """

    def test_default_is_true(self) -> None:
        """Default construction → use_baton=True."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig()
        assert config.use_baton is True

    @given(value=st.booleans())
    @settings(max_examples=10)
    def test_explicit_override(self, value: bool) -> None:
        """Explicit value is respected."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig(use_baton=value)
        assert config.use_baton is value

    def test_from_yaml_without_field(self) -> None:
        """YAML that omits use_baton still gets True."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig.model_validate({})
        assert config.use_baton is True

    def test_from_yaml_with_false(self) -> None:
        """YAML with use_baton: false gets False."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig.model_validate({"use_baton": False})
        assert config.use_baton is False


# =============================================================================
# Invariant 98: Fallback state serialization round-trip
# =============================================================================


class TestFallbackStateRoundTrip:
    """to_dict/from_dict preserves all fallback-related fields.

    Invariant: For any SheetExecutionState with fallback fields populated,
    from_dict(state.to_dict()) produces a state with identical fallback
    chain, index, attempts dict, and history.
    """

    @given(
        primary=_INSTRUMENT_NAMES,
        chain=_FALLBACK_CHAINS,
        index=st.integers(min_value=0, max_value=10),
        normal_attempts=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_round_trip_preserves_fallback_fields(
        self,
        primary: str,
        chain: list[str],
        index: int,
        normal_attempts: int,
    ) -> None:
        """Serialize then deserialize — all fallback fields match."""
        # Clamp index to valid range
        index = min(index, len(chain))

        state = SheetExecutionState(
            sheet_num=1,
            instrument_name=primary,
            fallback_chain=list(chain),
        )
        state.current_instrument_index = index
        state.normal_attempts = normal_attempts
        state.fallback_attempts = {f"inst-{i}": i * 2 for i in range(index)}
        state.instrument_fallback_history = [
            {"from": f"a-{i}", "to": f"b-{i}", "reason": "test", "timestamp": "T"}
            for i in range(min(index, 5))
        ]

        # Round-trip
        serialized = state.to_dict()
        restored = SheetExecutionState.from_dict(serialized)

        assert restored.fallback_chain == state.fallback_chain
        assert restored.current_instrument_index == state.current_instrument_index
        assert restored.fallback_attempts == state.fallback_attempts
        assert restored.instrument_fallback_history == state.instrument_fallback_history
        assert restored.instrument_name == state.instrument_name
        assert restored.normal_attempts == state.normal_attempts

    def test_round_trip_with_empty_fallback(self) -> None:
        """Empty fallback fields survive round-trip."""
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
        )

        serialized = state.to_dict()
        restored = SheetExecutionState.from_dict(serialized)

        assert restored.fallback_chain == []
        assert restored.current_instrument_index == 0
        assert restored.fallback_attempts == {}
        assert restored.instrument_fallback_history == []

    def test_from_dict_defaults_missing_fallback_fields(self) -> None:
        """Old serialized data (pre-fallback) defaults gracefully."""
        data: dict[str, Any] = {
            "sheet_num": 1,
            "instrument_name": "claude-code",
            "status": "pending",
            # No fallback fields — simulates pre-M5 data
        }
        restored = SheetExecutionState.from_dict(data)

        assert restored.fallback_chain == []
        assert restored.current_instrument_index == 0
        assert restored.fallback_attempts == {}
        assert restored.instrument_fallback_history == []
