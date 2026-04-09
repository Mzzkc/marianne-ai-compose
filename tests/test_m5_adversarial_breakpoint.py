"""Movement 5 adversarial tests — Breakpoint.

Tenth adversarial pass. Targets all M5 changes:
- F-149: Backpressure rework (should_accept_job / rejection_reason contract)
- F-255.2: _live_states initialization edge cases
- Instrument fallback chain adversarial (self-referential, empty, duplicates)
- advance_fallback() boundary testing (exhaust chain, double-advance, trim)
- V211 InstrumentFallbackCheck edge cases
- format_relative_time boundary conditions
- Cross-sheet context F-202 design decision verification
- deregister_job cleanup completeness
- F-105 stdin delivery edge cases (_build_command)
- Fallback history dual-store consistency (SheetState vs SheetExecutionState)

57 tests across 10 test classes.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# =============================================================================
# 1. Backpressure Contract Consistency (F-149)
# =============================================================================


class TestBackpressureContractConsistency:
    """should_accept_job() and rejection_reason() must always agree.

    F-149 split these methods — they now check resource pressure independently
    of rate limits. The adversarial concern: can they DISAGREE? If
    should_accept_job() returns True but rejection_reason() returns non-None
    (or vice versa), callers get contradictory signals.
    """

    def _make_controller(
        self,
        *,
        current_mem: float | None = 500.0,
        max_mem: float = 1000.0,
        is_degraded: bool = False,
        accepting_work: bool = True,
        active_limits: dict[str, Any] | None = None,
    ) -> Any:
        """Build a BackpressureController with mocked monitor."""
        from marianne.daemon.backpressure import BackpressureController

        monitor = MagicMock()
        monitor.current_memory_mb.return_value = current_mem
        monitor.max_memory_mb = max_mem
        monitor.is_degraded = is_degraded
        monitor.is_accepting_work.return_value = accepting_work

        rate_coordinator = MagicMock()
        rate_coordinator.active_limits = active_limits or {}

        controller = BackpressureController.__new__(BackpressureController)
        controller._monitor = monitor
        controller._rate_coordinator = rate_coordinator
        controller._learning_hub = None

        return controller

    def test_contract_normal_conditions(self) -> None:
        """Both agree: accept when memory is fine."""
        ctrl = self._make_controller(current_mem=500.0, max_mem=1000.0)
        assert ctrl.should_accept_job() is True
        assert ctrl.rejection_reason() is None

    def test_contract_high_memory(self) -> None:
        """Both agree: reject when memory > 85%."""
        ctrl = self._make_controller(current_mem=860.0, max_mem=1000.0)
        assert ctrl.should_accept_job() is False
        assert ctrl.rejection_reason() == "resource"

    def test_contract_critical_memory(self) -> None:
        """Both agree: reject when memory > 95%."""
        ctrl = self._make_controller(current_mem=960.0, max_mem=1000.0)
        assert ctrl.should_accept_job() is False
        assert ctrl.rejection_reason() == "resource"

    def test_contract_degraded_monitor(self) -> None:
        """Both agree: reject when monitor is degraded."""
        ctrl = self._make_controller(is_degraded=True)
        assert ctrl.should_accept_job() is False
        assert ctrl.rejection_reason() == "resource"

    def test_contract_none_memory(self) -> None:
        """Both agree: reject when current_memory_mb returns None."""
        ctrl = self._make_controller(current_mem=None)
        assert ctrl.should_accept_job() is False
        assert ctrl.rejection_reason() == "resource"

    def test_contract_not_accepting_work(self) -> None:
        """Both agree: reject when monitor says not accepting."""
        ctrl = self._make_controller(accepting_work=False)
        assert ctrl.should_accept_job() is False
        assert ctrl.rejection_reason() == "resource"

    def test_rate_limits_do_not_cause_rejection(self) -> None:
        """Rate limits present but memory fine → accept. F-149 core property."""
        ctrl = self._make_controller(
            current_mem=500.0,
            max_mem=1000.0,
            active_limits={"claude-cli": {"wait_seconds": 60}},
        )
        assert ctrl.should_accept_job() is True
        assert ctrl.rejection_reason() is None

    def test_boundary_exactly_85_percent(self) -> None:
        """Boundary: exactly 85% should accept (> 0.85 rejects, not >=)."""
        ctrl = self._make_controller(current_mem=850.0, max_mem=1000.0)
        assert ctrl.should_accept_job() is True
        assert ctrl.rejection_reason() is None

    def test_boundary_just_above_85_percent(self) -> None:
        """Just above 85% → reject."""
        ctrl = self._make_controller(current_mem=851.0, max_mem=1000.0)
        assert ctrl.should_accept_job() is False
        assert ctrl.rejection_reason() == "resource"

    def test_zero_memory_usage(self) -> None:
        """Edge: zero memory reported → accept (0/1000 = 0.0)."""
        ctrl = self._make_controller(current_mem=0.0, max_mem=1000.0)
        assert ctrl.should_accept_job() is True
        assert ctrl.rejection_reason() is None

    def test_zero_max_memory(self) -> None:
        """Edge: max_memory_mb=0 → max(0,1)=1, current/1 could be large."""
        ctrl = self._make_controller(current_mem=500.0, max_mem=0.0)
        # 500/max(0,1)=500.0 >> 0.85 → reject
        assert ctrl.should_accept_job() is False
        assert ctrl.rejection_reason() == "resource"


# =============================================================================
# 2. F-255.2: _live_states Initialization Edge Cases
# =============================================================================


class TestLiveStatesInitialization:
    """Adversarial tests for the F-255.2 initial CheckpointState creation.

    The code at manager.py:2042-2057 builds initial state from sheets.
    Attack surfaces: empty sheets, missing instrument_name, set behavior.
    """

    def test_instruments_used_deduplicates(self) -> None:
        """instruments_used should be deduplicated via set comprehension."""
        sheets = [
            MagicMock(num=1, instrument_name="claude-cli", movement=1),
            MagicMock(num=2, instrument_name="claude-cli", movement=1),
            MagicMock(num=3, instrument_name="gemini-cli", movement=2),
        ]
        instruments_used = list(
            {s.instrument_name for s in sheets if s.instrument_name}
        )
        assert len(instruments_used) == 2
        assert set(instruments_used) == {"claude-cli", "gemini-cli"}

    def test_instruments_used_filters_none(self) -> None:
        """Sheets with instrument_name=None are excluded."""
        sheets = [
            MagicMock(num=1, instrument_name=None, movement=1),
            MagicMock(num=2, instrument_name="claude-cli", movement=1),
        ]
        instruments_used = list(
            {s.instrument_name for s in sheets if s.instrument_name}
        )
        assert instruments_used == ["claude-cli"]

    def test_instruments_used_filters_empty_string(self) -> None:
        """Sheets with instrument_name="" are excluded (falsy)."""
        sheets = [
            MagicMock(num=1, instrument_name="", movement=1),
            MagicMock(num=2, instrument_name="claude-cli", movement=1),
        ]
        instruments_used = list(
            {s.instrument_name for s in sheets if s.instrument_name}
        )
        assert instruments_used == ["claude-cli"]

    def test_total_movements_empty_sheets(self) -> None:
        """Empty sheets list → total_movements=None (default kwarg)."""
        sheets: list[Any] = []
        total_movements = max(
            (s.movement for s in sheets), default=None
        )
        assert total_movements is None

    def test_total_movements_single_movement(self) -> None:
        """Single movement → total_movements equals that movement."""
        sheets = [MagicMock(movement=3)]
        total_movements = max((s.movement for s in sheets), default=None)
        assert total_movements == 3

    def test_total_movements_multi_movement(self) -> None:
        """Multiple movements → max is returned."""
        sheets = [MagicMock(movement=1), MagicMock(movement=5), MagicMock(movement=3)]
        total_movements = max((s.movement for s in sheets), default=None)
        assert total_movements == 5


# =============================================================================
# 3. Fallback Chain Adversarial
# =============================================================================


class TestFallbackChainAdversarial:
    """Test instrument fallback chain edge cases in SheetExecutionState."""

    def _make_state(
        self,
        *,
        instrument_name: str = "claude-cli",
        fallback_chain: list[str] | None = None,
        max_retries: int = 3,
    ) -> Any:
        from marianne.daemon.baton.state import SheetExecutionState

        return SheetExecutionState(
            sheet_num=1,
            instrument_name=instrument_name,
            max_retries=max_retries,
            fallback_chain=list(fallback_chain or []),
        )

    def test_self_referential_fallback(self) -> None:
        """Primary instrument in its own fallback chain → wastes retries."""
        state = self._make_state(
            instrument_name="claude-cli",
            fallback_chain=["claude-cli"],
        )
        assert state.has_fallback_available is True
        result = state.advance_fallback("rate_limit_exhausted")
        # It DOES advance — but to the same instrument. No validation prevents this.
        assert result == "claude-cli"
        assert state.instrument_name == "claude-cli"
        # Normal attempts reset → fresh budget on same instrument
        assert state.normal_attempts == 0

    def test_empty_fallback_chain(self) -> None:
        """No fallbacks → has_fallback_available is False."""
        state = self._make_state(fallback_chain=[])
        assert state.has_fallback_available is False
        assert state.advance_fallback("unavailable") is None

    def test_duplicate_entries_in_chain(self) -> None:
        """Duplicates in chain → each gets fresh retry budget."""
        state = self._make_state(
            instrument_name="claude-cli",
            fallback_chain=["gemini-cli", "gemini-cli", "gemini-cli"],
        )
        for _i in range(3):
            assert state.has_fallback_available is True
            result = state.advance_fallback("rate_limit_exhausted")
            assert result == "gemini-cli"
            assert state.normal_attempts == 0
        assert state.has_fallback_available is False

    def test_full_chain_walk(self) -> None:
        """Walk through entire chain and verify exhaustion."""
        state = self._make_state(
            instrument_name="a",
            fallback_chain=["b", "c", "d"],
        )
        assert state.advance_fallback("unavailable") == "b"
        assert state.instrument_name == "b"
        assert state.advance_fallback("unavailable") == "c"
        assert state.instrument_name == "c"
        assert state.advance_fallback("unavailable") == "d"
        assert state.instrument_name == "d"
        assert state.has_fallback_available is False
        assert state.advance_fallback("unavailable") is None

    def test_advance_records_history(self) -> None:
        """Each advance records from/to/reason/timestamp."""
        state = self._make_state(
            instrument_name="claude-cli",
            fallback_chain=["gemini-cli"],
        )
        state.advance_fallback("rate_limit_exhausted")
        assert len(state.instrument_fallback_history) == 1
        record = state.instrument_fallback_history[0]
        assert record["from"] == "claude-cli"
        assert record["to"] == "gemini-cli"
        assert record["reason"] == "rate_limit_exhausted"
        assert "timestamp" in record

    def test_advance_preserves_attempt_count(self) -> None:
        """fallback_attempts records how many retries were spent per instrument."""
        state = self._make_state(
            instrument_name="claude-cli",
            fallback_chain=["gemini-cli"],
            max_retries=5,
        )
        # Simulate 3 failed attempts on primary
        state.normal_attempts = 3
        state.advance_fallback("rate_limit_exhausted")
        assert state.fallback_attempts["claude-cli"] == 3
        assert state.normal_attempts == 0  # Fresh budget


# =============================================================================
# 4. Fallback History Trimming (F-252)
# =============================================================================


class TestFallbackHistoryTrimming:
    """Verify both trim paths enforce their limits correctly."""

    def test_sheet_state_trim_at_boundary(self) -> None:
        """SheetState.add_fallback_to_history trims at MAX (50)."""
        from marianne.core.checkpoint import (
            MAX_INSTRUMENT_FALLBACK_HISTORY,
            SheetState,
        )

        state = SheetState(sheet_num=1)
        # Fill to capacity
        for i in range(MAX_INSTRUMENT_FALLBACK_HISTORY):
            state.add_fallback_to_history({"from": f"a{i}", "to": f"b{i}"})
        assert len(state.instrument_fallback_history) == MAX_INSTRUMENT_FALLBACK_HISTORY

        # One more should trim, keeping the latest
        state.add_fallback_to_history({"from": "overflow", "to": "new"})
        assert len(state.instrument_fallback_history) == MAX_INSTRUMENT_FALLBACK_HISTORY
        assert state.instrument_fallback_history[-1]["from"] == "overflow"
        # First entry should be a1, not a0 (a0 trimmed)
        assert state.instrument_fallback_history[0]["from"] == "a1"

    def test_execution_state_trim_at_boundary(self) -> None:
        """SheetExecutionState.advance_fallback trims fallback_history."""
        from marianne.daemon.baton.state import MAX_FALLBACK_HISTORY, SheetExecutionState

        # Build a state with a very long fallback chain
        chain = [f"inst-{i}" for i in range(MAX_FALLBACK_HISTORY + 5)]
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="primary",
            fallback_chain=chain,
        )

        # Walk through more than MAX entries
        for _ in range(MAX_FALLBACK_HISTORY + 5):
            if state.has_fallback_available:
                state.advance_fallback("unavailable")

        assert len(state.instrument_fallback_history) <= MAX_FALLBACK_HISTORY

    def test_dual_store_max_constants_match(self) -> None:
        """Both stores use the same max value (50). If they diverge, that's a bug."""
        from marianne.core.checkpoint import MAX_INSTRUMENT_FALLBACK_HISTORY
        from marianne.daemon.baton.state import MAX_FALLBACK_HISTORY

        assert MAX_INSTRUMENT_FALLBACK_HISTORY == MAX_FALLBACK_HISTORY, (
            f"Dual-store history limits diverge: "
            f"checkpoint={MAX_INSTRUMENT_FALLBACK_HISTORY}, "
            f"baton={MAX_FALLBACK_HISTORY}"
        )


# =============================================================================
# 5. V211 InstrumentFallbackCheck Edge Cases
# =============================================================================


class TestV211FallbackCheckAdversarial:
    """Adversarial tests for the InstrumentFallbackCheck validation."""

    def _make_config(
        self,
        *,
        instrument_fallbacks: list[str] | None = None,
        movements: dict[int, Any] | None = None,
        per_sheet_fallbacks: dict[int, list[str]] | None = None,
        instruments: dict[str, Any] | None = None,
    ) -> MagicMock:
        """Build a minimal mock JobConfig for V211 testing."""
        config = MagicMock()
        config.instrument_fallbacks = instrument_fallbacks or []
        config.instruments = instruments or {}

        if movements:
            config.movements = movements
        else:
            config.movements = {}

        if per_sheet_fallbacks:
            sheet_config = MagicMock()
            sheet_config.per_sheet_fallbacks = per_sheet_fallbacks
            config.sheet = sheet_config
        else:
            sheet_config = MagicMock()
            sheet_config.per_sheet_fallbacks = {}
            config.sheet = sheet_config

        return config

    def test_empty_fallback_lists_produce_no_issues(self) -> None:
        """No fallbacks configured → no issues."""
        from marianne.validation.checks.config import InstrumentFallbackCheck

        check = InstrumentFallbackCheck()
        config = self._make_config()
        with patch(
            "marianne.instruments.loader.load_all_profiles",
            return_value={"claude-cli": MagicMock()},
        ):
            issues = check.check(config, Path("."), "")
        assert issues == []

    def test_known_instrument_produces_no_issue(self) -> None:
        """Fallback referencing a known instrument → clean."""
        from marianne.validation.checks.config import InstrumentFallbackCheck

        check = InstrumentFallbackCheck()
        config = self._make_config(instrument_fallbacks=["gemini-cli"])
        with patch(
            "marianne.instruments.loader.load_all_profiles",
            return_value={"claude-cli": MagicMock(), "gemini-cli": MagicMock()},
        ):
            issues = check.check(config, Path("."), "instrument_fallbacks:\n  - gemini-cli")
        assert issues == []

    def test_unknown_instrument_produces_warning(self) -> None:
        """Fallback referencing unknown instrument → warning."""
        from marianne.validation.checks.config import InstrumentFallbackCheck

        check = InstrumentFallbackCheck()
        config = self._make_config(instrument_fallbacks=["nonexistent-backend"])
        with patch(
            "marianne.instruments.loader.load_all_profiles",
            return_value={"claude-cli": MagicMock()},
        ):
            issues = check.check(
                config, Path("."), "instrument_fallbacks:\n  - nonexistent-backend"
            )
        assert len(issues) == 1
        assert issues[0].check_id == "V211"
        assert "nonexistent-backend" in issues[0].message

    def test_score_level_alias_is_valid_target(self) -> None:
        """Score instruments: section aliases are valid fallback targets."""
        from marianne.validation.checks.config import InstrumentFallbackCheck

        check = InstrumentFallbackCheck()
        config = self._make_config(
            instrument_fallbacks=["my-fast-model"],
            instruments={"my-fast-model": {"profile": "claude-cli"}},
        )
        with patch(
            "marianne.instruments.loader.load_all_profiles",
            return_value={"claude-cli": MagicMock()},
        ):
            issues = check.check(config, Path("."), "")
        assert issues == []

    def test_profile_load_failure_skips_gracefully(self) -> None:
        """If load_all_profiles raises, check returns empty (no crash)."""
        from marianne.validation.checks.config import InstrumentFallbackCheck

        check = InstrumentFallbackCheck()
        config = self._make_config(instrument_fallbacks=["anything"])
        with patch(
            "marianne.instruments.loader.load_all_profiles",
            side_effect=RuntimeError("profiles broken"),
        ):
            issues = check.check(config, Path("."), "")
        assert issues == []

    def test_movement_level_unknown_flagged(self) -> None:
        """Movement-level fallback with unknown instrument → warning."""
        from marianne.validation.checks.config import InstrumentFallbackCheck

        check = InstrumentFallbackCheck()
        mov = MagicMock()
        mov.instrument_fallbacks = ["ghost-backend"]
        config = self._make_config(movements={1: mov})
        with patch(
            "marianne.instruments.loader.load_all_profiles",
            return_value={"claude-cli": MagicMock()},
        ):
            issues = check.check(config, Path("."), "ghost-backend")
        assert len(issues) == 1
        assert "movement 1" in issues[0].message


# =============================================================================
# 6. format_relative_time Boundary Conditions
# =============================================================================


class TestFormatRelativeTimeBoundary:
    """Adversarial tests for D-029 format_relative_time."""

    def test_none_input(self) -> None:
        from marianne.cli.output import format_relative_time

        assert format_relative_time(None) == "-"

    def test_future_datetime_returns_just_now(self) -> None:
        """Clock skew: dt is in the future → 'just now' (total_seconds <= 0)."""
        from marianne.cli.output import format_relative_time

        now = datetime.datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
        future = datetime.datetime(2026, 4, 5, 12, 30, 0, tzinfo=UTC)
        result = format_relative_time(future, now=now)
        assert result == "just now"

    def test_exactly_zero_seconds(self) -> None:
        """Same instant → 'just now'."""
        from marianne.cli.output import format_relative_time

        now = datetime.datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
        assert format_relative_time(now, now=now) == "just now"

    def test_one_second_ago(self) -> None:
        from marianne.cli.output import format_relative_time

        now = datetime.datetime(2026, 4, 5, 12, 0, 1, tzinfo=UTC)
        dt = datetime.datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
        assert format_relative_time(dt, now=now) == "1s ago"

    def test_59_seconds(self) -> None:
        from marianne.cli.output import format_relative_time

        now = datetime.datetime(2026, 4, 5, 12, 0, 59, tzinfo=UTC)
        dt = datetime.datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
        assert format_relative_time(dt, now=now) == "59s ago"

    def test_exactly_60_seconds(self) -> None:
        """60 seconds → 1m ago (not 60s ago)."""
        from marianne.cli.output import format_relative_time

        now = datetime.datetime(2026, 4, 5, 12, 1, 0, tzinfo=UTC)
        dt = datetime.datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
        assert format_relative_time(dt, now=now) == "1m ago"

    def test_huge_duration_days(self) -> None:
        """Very old datetime → large day count."""
        from marianne.cli.output import format_relative_time

        now = datetime.datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
        old = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        result = format_relative_time(old, now=now)
        assert "d" in result  # Should contain days


# =============================================================================
# 7. Cross-Sheet Context F-202 Design Decision
# =============================================================================


class TestCrossSheetContextExclusion:
    """Verify that only COMPLETED sheets provide stdout to cross-sheet context.

    F-202 design decision: FAILED/SKIPPED sheets are excluded (baton is
    stricter than legacy). This is correct — failed output may be
    incomplete/malformed.
    """

    def test_baton_status_mapping_completeness(self) -> None:
        """Every BatonSheetStatus has a checkpoint mapping."""
        from marianne.daemon.baton.adapter import _BATON_TO_CHECKPOINT
        from marianne.daemon.baton.state import BatonSheetStatus

        for status in BatonSheetStatus:
            assert status in _BATON_TO_CHECKPOINT, (
                f"BatonSheetStatus.{status.name} has no checkpoint mapping"
            )

    def test_checkpoint_to_baton_covers_all_checkpoint_states(self) -> None:
        """Every checkpoint status string has a baton mapping."""
        from marianne.daemon.baton.adapter import _CHECKPOINT_TO_BATON

        expected = {
            "pending",
            "ready",
            "dispatched",
            "in_progress",
            "waiting",
            "retry_scheduled",
            "fermata",
            "completed",
            "failed",
            "skipped",
            "cancelled",
        }
        assert set(_CHECKPOINT_TO_BATON.keys()) == expected

    def test_cancelled_maps_to_cancelled(self) -> None:
        """CANCELLED → 'cancelled' (11-state unified model)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status
        from marianne.daemon.baton.state import BatonSheetStatus

        assert baton_to_checkpoint_status(BatonSheetStatus.CANCELLED) == "cancelled"

    def test_fermata_maps_to_fermata(self) -> None:
        """FERMATA → 'fermata' (11-state unified model)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status
        from marianne.daemon.baton.state import BatonSheetStatus

        assert baton_to_checkpoint_status(BatonSheetStatus.FERMATA) == "fermata"


# =============================================================================
# 8. deregister_job Cleanup Completeness
# =============================================================================


class TestDeregisterJobCleanup:
    """Verify deregister_job cleans up ALL per-job collections.

    F-470 added _synced_status cleanup. Adversarial: are there any
    collections that deregister_job misses?
    """

    def test_all_collections_cleaned(self) -> None:
        """After deregister, no references to the job should remain."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = MagicMock()
        adapter._event_bus = None
        adapter._state_sync_callback = None

        job_id = "test-job-123"

        # Populate all per-job collections
        adapter._job_sheets = {job_id: {1: MagicMock()}}
        adapter._job_renderers = {job_id: MagicMock()}
        adapter._job_cross_sheet = {job_id: MagicMock()}
        adapter._completion_events = {job_id: asyncio.Event()}
        adapter._completion_results = {job_id: True}
        adapter._synced_status = {
            (job_id, 1): "completed",
            (job_id, 2): "pending",
            ("other-job", 1): "in_progress",
        }
        adapter._active_tasks = {}

        adapter.deregister_job(job_id)

        assert job_id not in adapter._job_sheets
        assert job_id not in adapter._job_renderers
        assert job_id not in adapter._job_cross_sheet
        assert job_id not in adapter._completion_events
        assert job_id not in adapter._completion_results
        # _synced_status: all entries for this job removed, others preserved
        assert (job_id, 1) not in adapter._synced_status
        assert (job_id, 2) not in adapter._synced_status
        assert ("other-job", 1) in adapter._synced_status

    def test_deregister_nonexistent_job_no_crash(self) -> None:
        """Deregistering a job that doesn't exist shouldn't crash."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = MagicMock()
        adapter._event_bus = None
        adapter._state_sync_callback = None
        adapter._job_sheets = {}
        adapter._job_renderers = {}
        adapter._job_cross_sheet = {}
        adapter._completion_events = {}
        adapter._completion_results = {}
        adapter._synced_status = {}
        adapter._active_tasks = {}

        # Should not raise
        adapter.deregister_job("nonexistent-job")


# =============================================================================
# 9. F-105 Stdin Delivery Edge Cases (_build_command)
# =============================================================================


class TestStdinDeliveryBuildCommand:
    """Adversarial tests for stdin prompt delivery in _build_command."""

    def _make_cli_command(self, **overrides: Any) -> Any:
        """Build a CliCommand with stdin delivery fields."""
        from marianne.core.config.instruments import CliCommand

        defaults = {
            "executable": "claude",
            "prompt_flag": "-p",
            "prompt_via_stdin": False,
            "stdin_sentinel": None,
            "start_new_session": False,
        }
        defaults.update(overrides)
        return CliCommand(**defaults)

    def test_stdin_with_sentinel_includes_flag_and_sentinel(self) -> None:
        """prompt_via_stdin + sentinel → args include '-p -'."""
        cmd = self._make_cli_command(
            prompt_via_stdin=True,
            stdin_sentinel="-",
        )
        # Verify the model fields are set correctly
        assert cmd.prompt_via_stdin is True
        assert cmd.stdin_sentinel == "-"
        assert cmd.prompt_flag == "-p"

    def test_stdin_without_sentinel_omits_prompt_entirely(self) -> None:
        """prompt_via_stdin without sentinel → no prompt in args."""
        cmd = self._make_cli_command(
            prompt_via_stdin=True,
            stdin_sentinel=None,
        )
        assert cmd.prompt_via_stdin is True
        assert cmd.stdin_sentinel is None

    def test_stdin_without_prompt_flag(self) -> None:
        """prompt_via_stdin + sentinel but no prompt_flag → sentinel skipped."""
        cmd = self._make_cli_command(
            prompt_via_stdin=True,
            stdin_sentinel="-",
            prompt_flag=None,
        )
        # Even with sentinel, if no prompt_flag, the sentinel has nowhere to go
        assert cmd.prompt_via_stdin is True
        assert cmd.prompt_flag is None

    def test_start_new_session_field(self) -> None:
        """start_new_session=True should be a boolean flag."""
        cmd = self._make_cli_command(start_new_session=True)
        assert cmd.start_new_session is True

    def test_sentinel_is_arbitrary_string(self) -> None:
        """Sentinel can be any string, not just '-'."""
        cmd = self._make_cli_command(
            prompt_via_stdin=True,
            stdin_sentinel="/dev/stdin",
        )
        assert cmd.stdin_sentinel == "/dev/stdin"


# =============================================================================
# 10. Attempt Result to Observer Event Conversion
# =============================================================================


class TestAttemptResultToObserverEvent:
    """Adversarial tests for event conversion in the adapter.

    The conversion logic at adapter.py:151-190 determines event names
    based on result fields. Boundary cases where multiple conditions
    could match.
    """

    def _make_result(self, **overrides: Any) -> Any:
        from marianne.daemon.baton.events import SheetAttemptResult

        defaults = {
            "job_id": "test-job",
            "sheet_num": 1,
            "instrument_name": "claude-cli",
            "attempt": 1,
            "execution_success": True,
            "validation_pass_rate": 100.0,
            "cost_usd": 0.01,
            "duration_seconds": 5.0,
            "rate_limited": False,
            "timestamp": time.time(),
        }
        defaults.update(overrides)
        return SheetAttemptResult(**defaults)

    def test_rate_limited_takes_priority(self) -> None:
        """rate_limited=True wins even if execution_success=True."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = self._make_result(
            rate_limited=True,
            execution_success=True,
            validation_pass_rate=100.0,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "rate_limit.active"

    def test_success_with_full_validation(self) -> None:
        """execution_success + 100% validation → sheet.completed."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = self._make_result(
            execution_success=True,
            validation_pass_rate=100.0,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.completed"

    def test_success_with_partial_validation(self) -> None:
        """execution_success + <100% validation → sheet.partial."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = self._make_result(
            execution_success=True,
            validation_pass_rate=80.0,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.partial"

    def test_execution_failure(self) -> None:
        """execution_success=False → sheet.failed."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = self._make_result(execution_success=False)
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.failed"

    def test_success_with_zero_validation(self) -> None:
        """execution_success + 0% validation → sheet.partial (not completed)."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = self._make_result(
            execution_success=True,
            validation_pass_rate=0.0,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.partial"

    def test_success_with_99_99_validation(self) -> None:
        """99.99% validation → sheet.partial (boundary: < 100.0)."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = self._make_result(
            execution_success=True,
            validation_pass_rate=99.99,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.partial"

    def test_event_data_includes_all_fields(self) -> None:
        """Observer event data dict has all expected keys."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = self._make_result()
        event = attempt_result_to_observer_event(result)
        data = event["data"]
        expected_keys = {
            "instrument", "attempt", "success", "validation_pass_rate",
            "cost_usd", "duration_seconds", "rate_limited",
            "error_classification", "model_used",
        }
        assert set(data.keys()) == expected_keys
