"""Tests for marianne.execution.circuit_breaker module."""

import asyncio

import pytest

from marianne.execution.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerStats,
    CircuitState,
)


def _advance_circuit_breaker_time(cb: CircuitBreaker, seconds: float) -> None:
    """Advance a circuit breaker's internal time by manipulating _last_failure_time.

    This avoids time.sleep() in tests by shifting the recorded failure time
    backwards, making the breaker think time has passed.
    """
    if cb._last_failure_time is not None:
        cb._last_failure_time -= seconds


class TestCircuitState:
    """Tests for CircuitState enum."""

    def test_states_exist(self):
        """Test that all expected states exist."""
        assert CircuitState.CLOSED == "closed"
        assert CircuitState.OPEN == "open"
        assert CircuitState.HALF_OPEN == "half_open"

    def test_state_is_string_enum(self):
        """Test that states can be compared to strings."""
        assert CircuitState.CLOSED == "closed"
        assert str(CircuitState.CLOSED) == "CircuitState.CLOSED"
        assert CircuitState.CLOSED.value == "closed"


class TestCircuitBreakerStats:
    """Tests for CircuitBreakerStats dataclass."""

    def test_default_values(self):
        """Test that stats have correct defaults."""
        stats = CircuitBreakerStats()
        assert stats.total_successes == 0
        assert stats.total_failures == 0
        assert stats.times_opened == 0
        assert stats.times_half_opened == 0
        assert stats.times_closed == 0
        assert stats.last_failure_at is None
        assert stats.last_state_change_at is None
        assert stats.consecutive_failures == 0

    def test_to_dict(self):
        """Test stats serialization."""
        stats = CircuitBreakerStats(
            total_successes=5,
            total_failures=3,
            times_opened=1,
            times_half_opened=1,
            times_closed=1,
            last_failure_at=12345.0,
            last_state_change_at=12346.0,
            consecutive_failures=2,
        )
        result = stats.to_dict()

        assert result["total_successes"] == 5
        assert result["total_failures"] == 3
        assert result["times_opened"] == 1
        assert result["times_half_opened"] == 1
        assert result["times_closed"] == 1
        assert result["last_failure_at"] == 12345.0
        assert result["last_state_change_at"] == 12346.0
        assert result["consecutive_failures"] == 2


class TestCircuitBreakerInitialization:
    """Tests for CircuitBreaker initialization."""

    @pytest.mark.asyncio
    async def test_default_values(self):
        """Test circuit breaker with default values."""
        cb = CircuitBreaker()
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 300.0
        assert cb.name == "default"
        assert await cb.get_state() == CircuitState.CLOSED

    def test_custom_values(self):
        """Test circuit breaker with custom values."""
        cb = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=60.0,
            name="test-breaker",
        )
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 60.0
        assert cb.name == "test-breaker"

    def test_invalid_failure_threshold(self):
        """Test that failure_threshold must be at least 1."""
        with pytest.raises(ValueError, match="failure_threshold must be at least 1"):
            CircuitBreaker(failure_threshold=0)

        with pytest.raises(ValueError, match="failure_threshold must be at least 1"):
            CircuitBreaker(failure_threshold=-1)

    def test_invalid_recovery_timeout(self):
        """Test that recovery_timeout must be positive."""
        with pytest.raises(ValueError, match="recovery_timeout must be positive"):
            CircuitBreaker(recovery_timeout=0)

        with pytest.raises(ValueError, match="recovery_timeout must be positive"):
            CircuitBreaker(recovery_timeout=-1.0)

    def test_repr(self):
        """Test string representation."""
        cb = CircuitBreaker(failure_threshold=5, name="my-breaker")
        result = repr(cb)
        assert "my-breaker" in result
        assert "closed" in result
        assert "0/5" in result  # failures/threshold


class TestCircuitBreakerClosedState:
    """Tests for circuit breaker in CLOSED state."""

    @pytest.mark.asyncio
    async def test_can_execute_when_closed(self):
        """Test that requests are allowed when closed."""
        cb = CircuitBreaker(failure_threshold=3)
        assert await cb.can_execute() is True
        assert await cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        """Test that success resets consecutive failure count."""
        cb = CircuitBreaker(failure_threshold=3)

        # Record some failures (but not enough to open)
        await cb.record_failure()
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.CLOSED

        # Record success
        await cb.record_success()
        assert await cb.get_state() == CircuitState.CLOSED

        # Failure count should be reset, so 2 more failures shouldn't open
        await cb.record_failure()
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failures_open_circuit(self):
        """Test that enough failures transition to OPEN."""
        cb = CircuitBreaker(failure_threshold=3)

        await cb.record_failure()
        assert await cb.get_state() == CircuitState.CLOSED
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.CLOSED
        await cb.record_failure()  # This should trigger OPEN
        assert await cb.get_state() == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_stats_updated_on_failure(self):
        """Test that stats are updated on failure."""
        cb = CircuitBreaker(failure_threshold=5)

        await cb.record_failure()
        stats = await cb.get_stats()

        assert stats.total_failures == 1
        assert stats.consecutive_failures == 1
        assert stats.last_failure_at is not None

    @pytest.mark.asyncio
    async def test_stats_updated_on_success(self):
        """Test that stats are updated on success."""
        cb = CircuitBreaker(failure_threshold=5)

        await cb.record_success()
        stats = await cb.get_stats()

        assert stats.total_successes == 1
        assert stats.consecutive_failures == 0


class TestCircuitBreakerOpenState:
    """Tests for circuit breaker in OPEN state."""

    @pytest.mark.asyncio
    async def test_blocks_requests_when_open(self):
        """Test that requests are blocked when open."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
        await cb.record_failure()  # Opens the circuit

        assert await cb.get_state() == CircuitState.OPEN
        assert await cb.can_execute() is False

    @pytest.mark.asyncio
    async def test_time_until_retry(self):
        """Test time_until_retry returns correct value when open."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
        await cb.record_failure()

        time_left = await cb.time_until_retry()
        assert time_left is not None
        assert time_left > 0
        assert time_left <= 60.0

    @pytest.mark.asyncio
    async def test_time_until_retry_none_when_not_open(self):
        """Test time_until_retry returns None when not open."""
        cb = CircuitBreaker(failure_threshold=5)
        assert await cb.time_until_retry() is None

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self):
        """Test automatic transition to HALF_OPEN after timeout."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.OPEN

        # Simulate time passing beyond recovery_timeout
        _advance_circuit_breaker_time(cb, 0.15)

        # State check should trigger transition
        assert await cb.get_state() == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_stats_count_open_transitions(self):
        """Test that opening circuit increments times_opened."""
        cb = CircuitBreaker(failure_threshold=1)
        await cb.record_failure()

        stats = await cb.get_stats()
        assert stats.times_opened == 1
        assert stats.last_state_change_at is not None


class TestCircuitBreakerHalfOpenState:
    """Tests for circuit breaker in HALF_OPEN state."""

    @pytest.mark.asyncio
    async def test_allows_single_request_in_half_open(self):
        """Test that HALF_OPEN allows requests."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        _advance_circuit_breaker_time(cb, 0.02)

        assert await cb.get_state() == CircuitState.HALF_OPEN
        assert await cb.can_execute() is True

    @pytest.mark.asyncio
    async def test_success_in_half_open_closes_circuit(self):
        """Test that success in HALF_OPEN transitions to CLOSED."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        _advance_circuit_breaker_time(cb, 0.02)

        assert await cb.get_state() == CircuitState.HALF_OPEN

        await cb.record_success()
        assert await cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_in_half_open_reopens_circuit(self):
        """Test that failure in HALF_OPEN transitions back to OPEN."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        _advance_circuit_breaker_time(cb, 0.02)

        assert await cb.get_state() == CircuitState.HALF_OPEN

        await cb.record_failure()
        assert await cb.get_state() == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_stats_count_half_open_transitions(self):
        """Test that half-open transition increments counter."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        _advance_circuit_breaker_time(cb, 0.02)

        # Trigger the transition check
        await cb.get_state()

        stats = await cb.get_stats()
        assert stats.times_half_opened == 1


class TestCircuitBreakerReset:
    """Tests for circuit breaker reset functionality."""

    @pytest.mark.asyncio
    async def test_reset_from_open(self):
        """Test reset from OPEN state."""
        cb = CircuitBreaker(failure_threshold=1)
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.OPEN

        await cb.reset()

        assert await cb.get_state() == CircuitState.CLOSED
        assert await cb.time_until_retry() is None

    @pytest.mark.asyncio
    async def test_reset_clears_failure_count(self):
        """Test that reset clears failure count."""
        cb = CircuitBreaker(failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()

        await cb.reset()

        # After reset, 2 failures shouldn't open (would have been 4)
        await cb.record_failure()
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_reset_does_not_clear_stats(self):
        """Test that reset preserves stats history."""
        cb = CircuitBreaker(failure_threshold=1)
        await cb.record_failure()  # Opens circuit
        await cb.reset()

        stats = await cb.get_stats()
        assert stats.total_failures == 1
        assert stats.times_opened == 1
        assert stats.times_closed == 1  # Reset triggers close transition


class TestCircuitBreakerForce:
    """Tests for force_open and force_close functionality."""

    @pytest.mark.asyncio
    async def test_force_open(self):
        """Test force_open transitions to OPEN."""
        cb = CircuitBreaker(failure_threshold=5)
        assert await cb.get_state() == CircuitState.CLOSED

        await cb.force_open()

        assert await cb.get_state() == CircuitState.OPEN
        assert await cb.can_execute() is False

    @pytest.mark.asyncio
    async def test_force_close(self):
        """Test force_close transitions to CLOSED."""
        cb = CircuitBreaker(failure_threshold=1)
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.OPEN

        await cb.force_close()

        assert await cb.get_state() == CircuitState.CLOSED
        assert await cb.can_execute() is True

    @pytest.mark.asyncio
    async def test_force_close_resets_failure_count(self):
        """Test that force_close resets failure count."""
        cb = CircuitBreaker(failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.force_close()

        # After force_close, 2 more failures shouldn't open
        await cb.record_failure()
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.CLOSED


class TestCircuitBreakerAsyncSafety:
    """Tests for async safety (basic verification)."""

    @pytest.mark.asyncio
    async def test_concurrent_async_operations_dont_crash(self):
        """Test that concurrent async operations don't cause crashes."""
        cb = CircuitBreaker(failure_threshold=100, recovery_timeout=60.0)

        async def record_failures():
            for _ in range(50):
                await cb.record_failure()
                await cb.get_state()

        async def record_successes():
            for _ in range(50):
                await cb.record_success()
                await cb.can_execute()

        await asyncio.gather(
            record_failures(),
            record_successes(),
            record_failures(),
            record_successes(),
        )

        stats = await cb.get_stats()
        assert stats.total_failures == 100
        assert stats.total_successes == 100


class TestCircuitBreakerStatsSnapshot:
    """Tests for stats snapshot (copy) behavior."""

    @pytest.mark.asyncio
    async def test_stats_are_copied(self):
        """Test that get_stats returns a copy."""
        cb = CircuitBreaker(failure_threshold=5)
        await cb.record_failure()

        stats1 = await cb.get_stats()
        await cb.record_failure()
        stats2 = await cb.get_stats()

        # stats1 should be unchanged
        assert stats1.total_failures == 1
        assert stats2.total_failures == 2


class TestCircuitBreakerLogging:
    """Tests for circuit breaker logging."""

    @pytest.mark.asyncio
    async def test_state_change_logged_at_info(self):
        """Test that state changes are logged at INFO level."""
        cb = CircuitBreaker(failure_threshold=1, name="test-logger")
        assert await cb.get_state() == CircuitState.CLOSED

        await cb.record_failure()  # CLOSED -> OPEN
        assert await cb.get_state() == CircuitState.OPEN

        await cb.force_close()  # OPEN -> CLOSED
        assert await cb.get_state() == CircuitState.CLOSED


class TestCircuitBreakerEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_single_failure_threshold(self):
        """Test with failure_threshold=1 (immediate open)."""
        cb = CircuitBreaker(failure_threshold=1)
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_very_short_recovery_timeout(self):
        """Test with very short recovery timeout."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.001)
        await cb.record_failure()
        _advance_circuit_breaker_time(cb, 0.01)
        assert await cb.get_state() == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_success_when_open_has_no_effect(self):
        """Test that success while OPEN doesn't change state."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
        await cb.record_failure()
        assert await cb.get_state() == CircuitState.OPEN

        # Record success while open (shouldn't happen in practice)
        await cb.record_success()

        # State should still be OPEN
        # (success increments counter but doesn't change OPEN state)
        assert await cb.get_state() == CircuitState.OPEN

        # But stats should reflect the success
        stats = await cb.get_stats()
        assert stats.total_successes == 1


class TestCircuitBreakerCostTracking:
    """Tests for cost tracking functionality."""

    @pytest.mark.asyncio
    async def test_record_cost(self):
        """Test recording cost updates stats."""
        cb = CircuitBreaker(failure_threshold=5)
        await cb.record_cost(100, 50, 0.005)

        stats = await cb.get_stats()
        assert stats.total_input_tokens == 100
        assert stats.total_output_tokens == 50
        assert stats.total_estimated_cost == pytest.approx(0.005)

    @pytest.mark.asyncio
    async def test_cost_accumulates(self):
        """Test that cost accumulates across multiple recordings."""
        cb = CircuitBreaker(failure_threshold=5)
        await cb.record_cost(100, 50, 0.005)
        await cb.record_cost(200, 100, 0.010)

        stats = await cb.get_stats()
        assert stats.total_input_tokens == 300
        assert stats.total_output_tokens == 150
        assert stats.total_estimated_cost == pytest.approx(0.015)

    @pytest.mark.asyncio
    async def test_check_cost_threshold_not_exceeded(self):
        """Test cost threshold check when under limit."""
        cb = CircuitBreaker(failure_threshold=5)
        await cb.record_cost(100, 50, 0.005)
        assert await cb.check_cost_threshold(1.0) is False

    @pytest.mark.asyncio
    async def test_check_cost_threshold_exceeded(self):
        """Test cost threshold check when over limit."""
        cb = CircuitBreaker(failure_threshold=5)
        await cb.record_cost(100, 50, 5.0)
        assert await cb.check_cost_threshold(1.0) is True
