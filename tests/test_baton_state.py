"""Tests for the baton's state model — the conductor's execution memory.

The baton state model tracks everything the conductor needs to make
scheduling decisions: per-sheet execution state, per-instrument health,
per-job tracking, and attempt context for musicians.

TDD: These tests define the contract. Implementation fulfills it.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import (
    AttemptContext,
    AttemptMode,
    BatonJobState,
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)


# =============================================================================
# BatonSheetStatus enum
# =============================================================================


class TestBatonSheetStatus:
    """The baton's sheet status enum has more states than the old SheetStatus."""

    def test_has_pending(self) -> None:
        assert BatonSheetStatus.PENDING.value == "pending"

    def test_has_ready(self) -> None:
        assert BatonSheetStatus.READY.value == "ready"

    def test_has_dispatched(self) -> None:
        assert BatonSheetStatus.DISPATCHED.value == "dispatched"

    def test_has_running(self) -> None:
        assert BatonSheetStatus.RUNNING.value == "running"

    def test_has_completed(self) -> None:
        assert BatonSheetStatus.COMPLETED.value == "completed"

    def test_has_failed(self) -> None:
        assert BatonSheetStatus.FAILED.value == "failed"

    def test_has_skipped(self) -> None:
        assert BatonSheetStatus.SKIPPED.value == "skipped"

    def test_has_waiting(self) -> None:
        """Waiting on rate-limited instrument."""
        assert BatonSheetStatus.WAITING.value == "waiting"

    def test_has_fermata(self) -> None:
        """Paused for human/AI escalation decision."""
        assert BatonSheetStatus.FERMATA.value == "fermata"

    def test_is_terminal_for_completed(self) -> None:
        assert BatonSheetStatus.COMPLETED.is_terminal

    def test_is_terminal_for_failed(self) -> None:
        assert BatonSheetStatus.FAILED.is_terminal

    def test_is_terminal_for_skipped(self) -> None:
        assert BatonSheetStatus.SKIPPED.is_terminal

    def test_is_not_terminal_for_pending(self) -> None:
        assert not BatonSheetStatus.PENDING.is_terminal

    def test_is_not_terminal_for_running(self) -> None:
        assert not BatonSheetStatus.RUNNING.is_terminal


# =============================================================================
# AttemptMode enum
# =============================================================================


class TestAttemptMode:
    """The mode a sheet attempt runs in."""

    def test_has_normal(self) -> None:
        assert AttemptMode.NORMAL.value == "normal"

    def test_has_completion(self) -> None:
        assert AttemptMode.COMPLETION.value == "completion"

    def test_has_healing(self) -> None:
        assert AttemptMode.HEALING.value == "healing"


# =============================================================================
# AttemptContext
# =============================================================================


class TestAttemptContext:
    """AttemptContext carries conductor-provided context to the musician."""

    def test_minimal_creation(self) -> None:
        ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        assert ctx.attempt_number == 1
        assert ctx.mode == AttemptMode.NORMAL
        assert ctx.completion_prompt_suffix is None
        assert ctx.healing_context is None
        assert ctx.previous_results is None
        assert ctx.learned_patterns is None

    def test_completion_mode(self) -> None:
        ctx = AttemptContext(
            attempt_number=2,
            mode=AttemptMode.COMPLETION,
            completion_prompt_suffix="Fix the failing validations above.",
        )
        assert ctx.mode == AttemptMode.COMPLETION
        assert ctx.completion_prompt_suffix is not None

    def test_healing_mode(self) -> None:
        ctx = AttemptContext(
            attempt_number=4,
            mode=AttemptMode.HEALING,
            healing_context={"diagnosis": "auth failure", "suggestion": "check key"},
        )
        assert ctx.mode == AttemptMode.HEALING
        assert ctx.healing_context is not None

    def test_with_learned_patterns(self) -> None:
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            learned_patterns=["Use structured output", "Always validate JSON"],
        )
        assert len(ctx.learned_patterns) == 2

    def test_with_previous_results(self) -> None:
        result = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code", attempt=1,
            execution_success=False, error_classification="TRANSIENT",
        )
        ctx = AttemptContext(
            attempt_number=2,
            mode=AttemptMode.NORMAL,
            previous_results=[result],
        )
        assert len(ctx.previous_results) == 1


# =============================================================================
# SheetExecutionState
# =============================================================================


class TestSheetExecutionState:
    """Per-sheet tracking in the conductor's memory."""

    def test_creation_with_defaults(self) -> None:
        state = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
        )
        assert state.sheet_num == 1
        assert state.instrument_name == "claude-code"
        assert state.status == BatonSheetStatus.PENDING
        assert state.normal_attempts == 0
        assert state.completion_attempts == 0
        assert state.healing_attempts == 0
        assert state.max_retries == 3
        assert state.max_completion == 5
        assert state.attempt_results == []
        assert state.next_retry_at is None
        assert state.total_cost_usd == 0.0

    def test_creation_with_custom_limits(self) -> None:
        state = SheetExecutionState(
            sheet_num=5,
            instrument_name="gemini-cli",
            max_retries=10,
            max_completion=8,
        )
        assert state.max_retries == 10
        assert state.max_completion == 8

    def test_record_attempt_success_does_not_consume_retry(self) -> None:
        """Successful attempts don't consume retry budget."""
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        result = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code", attempt=1,
            execution_success=True, cost_usd=0.05, duration_seconds=12.5,
            validations_passed=3, validations_total=3, validation_pass_rate=100.0,
        )
        state.record_attempt(result)
        assert state.normal_attempts == 0  # Success doesn't consume retry budget
        assert len(state.attempt_results) == 1
        assert state.total_cost_usd == pytest.approx(0.05)
        assert state.total_duration_seconds == pytest.approx(12.5)

    def test_record_attempt_failure_increments_retry_count(self) -> None:
        """Failed attempts consume retry budget."""
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        result = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code", attempt=1,
            execution_success=False, cost_usd=0.10, duration_seconds=5.0,
            error_classification="TRANSIENT",
        )
        state.record_attempt(result)
        assert state.normal_attempts == 1  # Failure consumes retry budget
        assert len(state.attempt_results) == 1
        assert state.total_cost_usd == pytest.approx(0.10)

    def test_record_rate_limited_attempt_does_not_increment_retries(self) -> None:
        """Rate limits are tempo changes, not failures."""
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        result = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code", attempt=1,
            execution_success=False, rate_limited=True,
        )
        state.record_attempt(result)
        assert state.normal_attempts == 0  # NOT incremented
        assert len(state.attempt_results) == 1  # still recorded

    def test_record_failed_attempt_increments_normal(self) -> None:
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        result = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code", attempt=1,
            execution_success=False, error_classification="TRANSIENT",
        )
        state.record_attempt(result)
        assert state.normal_attempts == 1

    def test_can_retry_when_under_limit(self) -> None:
        state = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=3,
        )
        state.normal_attempts = 2
        assert state.can_retry is True

    def test_cannot_retry_when_at_limit(self) -> None:
        state = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=3,
        )
        state.normal_attempts = 3
        assert state.can_retry is False

    def test_can_complete_when_under_limit(self) -> None:
        state = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_completion=5,
        )
        state.completion_attempts = 4
        assert state.can_complete is True

    def test_cannot_complete_when_at_limit(self) -> None:
        state = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_completion=5,
        )
        state.completion_attempts = 5
        assert state.can_complete is False

    def test_is_exhausted_when_both_limits_reached(self) -> None:
        state = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=3, max_completion=5,
        )
        state.normal_attempts = 3
        state.completion_attempts = 5
        assert state.is_exhausted is True

    def test_status_transitions(self) -> None:
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        assert state.status == BatonSheetStatus.PENDING

        state.status = BatonSheetStatus.READY
        assert state.status == BatonSheetStatus.READY

        state.status = BatonSheetStatus.DISPATCHED
        assert state.status == BatonSheetStatus.DISPATCHED

        state.status = BatonSheetStatus.RUNNING
        assert state.status == BatonSheetStatus.RUNNING

        state.status = BatonSheetStatus.COMPLETED
        assert state.status == BatonSheetStatus.COMPLETED

    def test_total_attempts_property(self) -> None:
        state = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        state.normal_attempts = 2
        state.completion_attempts = 3
        state.healing_attempts = 1
        assert state.total_attempts == 6


# =============================================================================
# InstrumentState
# =============================================================================


class TestInstrumentState:
    """Per-instrument tracking — rate limits, circuit breakers, concurrency."""

    def test_creation_defaults(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        assert state.name == "claude-code"
        assert state.max_concurrent == 4
        assert state.running_count == 0
        assert state.rate_limited is False
        assert state.rate_limit_expires_at is None
        assert state.circuit_breaker == CircuitBreakerState.CLOSED
        assert state.consecutive_failures == 0

    def test_is_available_when_healthy(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        assert state.is_available is True

    def test_is_not_available_when_rate_limited(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.rate_limited = True
        state.rate_limit_expires_at = time.monotonic() + 3600
        assert state.is_available is False

    def test_is_not_available_when_circuit_open(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.circuit_breaker = CircuitBreakerState.OPEN
        assert state.is_available is False

    def test_is_available_when_circuit_half_open(self) -> None:
        """Half-open allows one probe request."""
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.circuit_breaker = CircuitBreakerState.HALF_OPEN
        assert state.is_available is True

    def test_at_capacity_when_running_equals_max(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.running_count = 4
        assert state.at_capacity is True

    def test_not_at_capacity_when_under_max(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.running_count = 3
        assert state.at_capacity is False

    def test_record_success_resets_consecutive_failures(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.consecutive_failures = 5
        state.record_success()
        assert state.consecutive_failures == 0

    def test_record_failure_increments_consecutive(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.record_failure()
        assert state.consecutive_failures == 1
        state.record_failure()
        assert state.consecutive_failures == 2

    def test_record_failure_trips_circuit_breaker(self) -> None:
        state = InstrumentState(
            name="claude-code", max_concurrent=4,
            circuit_breaker_threshold=3,
        )
        state.record_failure()
        state.record_failure()
        assert state.circuit_breaker == CircuitBreakerState.CLOSED
        state.record_failure()  # threshold reached
        assert state.circuit_breaker == CircuitBreakerState.OPEN

    def test_success_after_half_open_closes_circuit(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.circuit_breaker = CircuitBreakerState.HALF_OPEN
        state.record_success()
        assert state.circuit_breaker == CircuitBreakerState.CLOSED

    def test_failure_after_half_open_reopens_circuit(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.circuit_breaker = CircuitBreakerState.HALF_OPEN
        state.record_failure()
        assert state.circuit_breaker == CircuitBreakerState.OPEN


# =============================================================================
# CircuitBreakerState enum
# =============================================================================


class TestCircuitBreakerState:
    """Circuit breaker has three states."""

    def test_closed(self) -> None:
        assert CircuitBreakerState.CLOSED.value == "closed"

    def test_open(self) -> None:
        assert CircuitBreakerState.OPEN.value == "open"

    def test_half_open(self) -> None:
        assert CircuitBreakerState.HALF_OPEN.value == "half_open"


# =============================================================================
# BatonJobState
# =============================================================================


class TestBatonJobState:
    """Per-job tracking in the baton."""

    def test_creation(self) -> None:
        state = BatonJobState(
            job_id="j1",
            total_sheets=10,
        )
        assert state.job_id == "j1"
        assert state.total_sheets == 10
        assert state.paused is False
        assert state.pacing_active is False
        assert state.total_cost_usd == 0.0
        assert len(state.sheets) == 0

    def test_register_sheet(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=3)
        job.register_sheet(SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
        ))
        assert len(job.sheets) == 1
        assert 1 in job.sheets

    def test_get_sheet(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=3)
        sheet = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        job.register_sheet(sheet)
        assert job.get_sheet(1) is sheet

    def test_get_sheet_missing_returns_none(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=3)
        assert job.get_sheet(999) is None

    def test_completed_count(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=3)
        s1 = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        s2 = SheetExecutionState(sheet_num=2, instrument_name="claude-code")
        s3 = SheetExecutionState(sheet_num=3, instrument_name="claude-code")
        s1.status = BatonSheetStatus.COMPLETED
        s2.status = BatonSheetStatus.RUNNING
        s3.status = BatonSheetStatus.SKIPPED
        job.register_sheet(s1)
        job.register_sheet(s2)
        job.register_sheet(s3)
        assert job.completed_count == 1
        assert job.terminal_count == 2  # completed + skipped

    def test_is_complete_when_all_terminal(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=2)
        s1 = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        s2 = SheetExecutionState(sheet_num=2, instrument_name="claude-code")
        s1.status = BatonSheetStatus.COMPLETED
        s2.status = BatonSheetStatus.SKIPPED
        job.register_sheet(s1)
        job.register_sheet(s2)
        assert job.is_complete is True

    def test_is_not_complete_when_sheets_running(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=2)
        s1 = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        s2 = SheetExecutionState(sheet_num=2, instrument_name="claude-code")
        s1.status = BatonSheetStatus.COMPLETED
        s2.status = BatonSheetStatus.RUNNING
        job.register_sheet(s1)
        job.register_sheet(s2)
        assert job.is_complete is False

    def test_running_sheets(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=3)
        s1 = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        s2 = SheetExecutionState(sheet_num=2, instrument_name="claude-code")
        s3 = SheetExecutionState(sheet_num=3, instrument_name="claude-code")
        s1.status = BatonSheetStatus.RUNNING
        s2.status = BatonSheetStatus.COMPLETED
        s3.status = BatonSheetStatus.RUNNING
        job.register_sheet(s1)
        job.register_sheet(s2)
        job.register_sheet(s3)
        running = job.running_sheets
        assert len(running) == 2
        assert all(s.status == BatonSheetStatus.RUNNING for s in running)

    def test_total_cost_aggregation(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=2)
        s1 = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        s2 = SheetExecutionState(sheet_num=2, instrument_name="claude-code")
        s1.total_cost_usd = 0.50
        s2.total_cost_usd = 0.30
        job.register_sheet(s1)
        job.register_sheet(s2)
        assert job.total_cost_usd == pytest.approx(0.80)

    def test_has_any_failed(self) -> None:
        job = BatonJobState(job_id="j1", total_sheets=2)
        s1 = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        s2 = SheetExecutionState(sheet_num=2, instrument_name="claude-code")
        s1.status = BatonSheetStatus.COMPLETED
        s2.status = BatonSheetStatus.FAILED
        job.register_sheet(s1)
        job.register_sheet(s2)
        assert job.has_any_failed is True


# =============================================================================
# Serialization to/from dict (for SQLite persistence)
# =============================================================================


class TestStateSerialization:
    """State models can be serialized to dicts and restored."""

    def test_sheet_state_to_dict(self) -> None:
        state = SheetExecutionState(
            sheet_num=5,
            instrument_name="gemini-cli",
            max_retries=10,
        )
        state.status = BatonSheetStatus.RUNNING
        state.normal_attempts = 2
        d = state.to_dict()
        assert d["sheet_num"] == 5
        assert d["instrument_name"] == "gemini-cli"
        assert d["status"] == "running"
        assert d["normal_attempts"] == 2

    def test_sheet_state_from_dict(self) -> None:
        d: dict[str, Any] = {
            "sheet_num": 5,
            "instrument_name": "gemini-cli",
            "status": "running",
            "normal_attempts": 2,
            "completion_attempts": 1,
            "healing_attempts": 0,
            "max_retries": 10,
            "max_completion": 5,
            "total_cost_usd": 0.25,
            "total_duration_seconds": 45.0,
        }
        state = SheetExecutionState.from_dict(d)
        assert state.sheet_num == 5
        assert state.instrument_name == "gemini-cli"
        assert state.status == BatonSheetStatus.RUNNING
        assert state.normal_attempts == 2
        assert state.max_retries == 10

    def test_instrument_state_to_dict(self) -> None:
        state = InstrumentState(name="claude-code", max_concurrent=4)
        state.consecutive_failures = 2
        d = state.to_dict()
        assert d["name"] == "claude-code"
        assert d["max_concurrent"] == 4
        assert d["consecutive_failures"] == 2

    def test_instrument_state_from_dict(self) -> None:
        d: dict[str, Any] = {
            "name": "claude-code",
            "max_concurrent": 4,
            "rate_limited": False,
            "rate_limit_expires_at": None,
            "circuit_breaker": "closed",
            "consecutive_failures": 0,
            "circuit_breaker_threshold": 5,
        }
        state = InstrumentState.from_dict(d)
        assert state.name == "claude-code"
        assert state.circuit_breaker == CircuitBreakerState.CLOSED

    def test_roundtrip_sheet_state(self) -> None:
        """to_dict → from_dict preserves all fields."""
        original = SheetExecutionState(
            sheet_num=3,
            instrument_name="aider",
            max_retries=7,
            max_completion=10,
        )
        original.status = BatonSheetStatus.WAITING
        original.normal_attempts = 4
        original.completion_attempts = 2
        original.total_cost_usd = 1.23

        restored = SheetExecutionState.from_dict(original.to_dict())
        assert restored.sheet_num == original.sheet_num
        assert restored.instrument_name == original.instrument_name
        assert restored.status == original.status
        assert restored.normal_attempts == original.normal_attempts
        assert restored.completion_attempts == original.completion_attempts
        assert restored.total_cost_usd == pytest.approx(original.total_cost_usd)

    def test_roundtrip_instrument_state(self) -> None:
        original = InstrumentState(name="codex-cli", max_concurrent=2)
        original.circuit_breaker = CircuitBreakerState.HALF_OPEN
        original.consecutive_failures = 3

        restored = InstrumentState.from_dict(original.to_dict())
        assert restored.name == original.name
        assert restored.circuit_breaker == original.circuit_breaker
        assert restored.consecutive_failures == original.consecutive_failures
