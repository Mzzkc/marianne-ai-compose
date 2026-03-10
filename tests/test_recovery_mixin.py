"""Tests for RecoveryMixin error handling and retry logic.

Tests the recovery mixin methods in isolation:
- _try_self_healing(): Self-healing coordination
- _handle_rate_limit(): Rate limit handling with wait
- _classify_execution(): Multi-error root cause analysis
- _get_retry_delay(): Exponential backoff calculation
- _poll_broadcast_discoveries(): Pattern discovery polling
- _record_error_recovery(): Recovery outcome recording
- _check_cross_workspace_rate_limit(): Cross-workspace coordination

These tests exercise the critical error recovery paths that were
previously untested after the D3 modularization.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.backends.base import ExecutionResult
from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.core.config import JobConfig
from mozart.core.errors import ClassifiedError, ErrorCategory
from mozart.core.errors.codes import ErrorCode
from mozart.execution.runner import FatalError, JobRunner

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_config() -> JobConfig:
    """Create a sample job configuration for testing."""
    return JobConfig.model_validate({
        "name": "test-recovery-job",
        "description": "Test job for recovery mixin tests",
        "backend": {
            "type": "claude_cli",
            "skip_permissions": True,
            "cli_model": "claude-sonnet-4-5-20250929",
        },
        "sheet": {
            "size": 10,
            "total_items": 30,
        },
        "prompt": {
            "template": "Process sheet {{ sheet_num }}.",
        },
        "retry": {
            "max_retries": 3,
            "base_delay_seconds": 5,
            "exponential_base": 2,
            "max_delay_seconds": 300,
            "jitter": False,  # Disable for deterministic tests
        },
        "rate_limit": {
            "wait_minutes": 5,
            "max_waits": 3,
        },
        "circuit_breaker": {
            "cross_workspace_coordination": True,
            "honor_other_jobs_rate_limits": True,
        },
    })


@pytest.fixture
def sample_config_with_jitter(sample_config: JobConfig) -> JobConfig:
    """Config with jitter enabled for randomness tests."""
    config_dict = sample_config.model_dump()
    config_dict["retry"]["jitter"] = True
    return JobConfig.model_validate(config_dict)


@pytest.fixture
def mock_backend() -> AsyncMock:
    """Create a mock backend."""
    backend = AsyncMock()
    backend.execute = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            stdout="Task completed",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
        )
    )
    backend.health_check = AsyncMock(return_value=True)
    backend.availability_check = AsyncMock(return_value=True)
    return backend


@pytest.fixture
def mock_state_backend() -> AsyncMock:
    """Create a mock state backend."""
    backend = AsyncMock()
    backend.load = AsyncMock(return_value=None)
    backend.save = AsyncMock()
    backend.list_jobs = AsyncMock(return_value=[])
    return backend


@pytest.fixture
def mock_global_store() -> MagicMock:
    """Create a mock global learning store."""
    store = MagicMock()
    store.record_rate_limit_event = MagicMock()
    store.record_error_recovery = MagicMock()
    store.is_rate_limited = MagicMock(return_value=(False, None))
    store.check_recent_pattern_discoveries = MagicMock(return_value=[])
    return store


@pytest.fixture
def mock_healing_coordinator() -> MagicMock:
    """Create a mock self-healing coordinator."""
    # Create a mock HealingReport with the expected properties
    mock_report = MagicMock()
    mock_report.any_remedies_applied = False
    mock_report.actions_taken = []
    mock_report.issues_remaining = 1
    mock_report.should_retry = False

    coordinator = AsyncMock()
    coordinator.heal = AsyncMock(return_value=mock_report)
    return coordinator


@pytest.fixture
def runner(
    sample_config: JobConfig,
    mock_backend: AsyncMock,
    mock_state_backend: AsyncMock,
) -> JobRunner:
    """Create a JobRunner instance for testing."""
    return JobRunner(
        config=sample_config,
        backend=mock_backend,
        state_backend=mock_state_backend,
    )


@pytest.fixture
def runner_with_global_store(
    sample_config: JobConfig,
    mock_backend: AsyncMock,
    mock_state_backend: AsyncMock,
    mock_global_store: MagicMock,
) -> JobRunner:
    """Create a JobRunner with global learning store."""
    runner = JobRunner(
        config=sample_config,
        backend=mock_backend,
        state_backend=mock_state_backend,
    )
    runner._global_learning_store = mock_global_store
    return runner


# =============================================================================
# TestRetryDelayCalculation
# =============================================================================


class TestRetryDelayCalculation:
    """Tests for _get_retry_delay() method."""

    def test_first_attempt_returns_base_delay(self, runner: JobRunner) -> None:
        """First attempt should return base delay."""
        delay = runner._get_retry_delay(attempt=1)
        assert delay == 5.0  # base_delay_seconds

    def test_exponential_backoff(self, runner: JobRunner) -> None:
        """Verify exponential backoff formula."""
        # With base=5, exp=2:
        # Attempt 1: 5 * 2^0 = 5
        # Attempt 2: 5 * 2^1 = 10
        # Attempt 3: 5 * 2^2 = 20
        # Attempt 4: 5 * 2^3 = 40
        assert runner._get_retry_delay(attempt=1) == 5.0
        assert runner._get_retry_delay(attempt=2) == 10.0
        assert runner._get_retry_delay(attempt=3) == 20.0
        assert runner._get_retry_delay(attempt=4) == 40.0

    def test_max_delay_cap(self, runner: JobRunner) -> None:
        """Verify delay is capped at max_delay_seconds."""
        # With max_delay=300, attempt 10 would be 5 * 2^9 = 2560 uncapped
        delay = runner._get_retry_delay(attempt=10)
        assert delay == 300.0  # max_delay_seconds

    def test_jitter_adds_randomness(
        self,
        sample_config_with_jitter: JobConfig,
        mock_backend: AsyncMock,
        mock_state_backend: AsyncMock,
    ) -> None:
        """Verify jitter adds randomness between 50-150% of base delay.

        The jitter formula is: delay *= 0.5 + random.random()
        which gives 50-150% of the original delay.
        """
        runner = JobRunner(
            config=sample_config_with_jitter,
            backend=mock_backend,
            state_backend=mock_state_backend,
        )

        # Collect multiple samples
        delays = [runner._get_retry_delay(attempt=1) for _ in range(100)]

        # All delays should be in range [2.5, 7.5] (50-150% of base 5)
        for delay in delays:
            assert 2.5 <= delay <= 7.5

        # Should have some variation (not all the same)
        assert len(set(delays)) > 1


# =============================================================================
# TestErrorClassification
# =============================================================================


class TestErrorClassification:
    """Tests for _classify_execution() and _classify_error() methods."""

    def test_classify_rate_limit_error(self, runner: JobRunner) -> None:
        """Test classification of rate limit errors."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error: 429 Too Many Requests",
            exit_code=1,
            duration_seconds=1.0,
        )

        classification = runner._classify_execution(result)

        assert classification.primary.category == ErrorCategory.RATE_LIMIT

    def test_classify_timeout_error(self, runner: JobRunner) -> None:
        """Test classification of timeout errors."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="",
            exit_code=None,
            exit_reason="timeout",
            duration_seconds=600.0,
        )

        classification = runner._classify_execution(result)

        assert classification.primary.category == ErrorCategory.TIMEOUT

    def test_classify_error_backward_compat(self, runner: JobRunner) -> None:
        """Test _classify_error() returns primary error."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Connection refused",
            exit_code=1,
            duration_seconds=1.0,
        )

        error = runner._classify_error(result)

        assert isinstance(error, ClassifiedError)
        assert error.category == ErrorCategory.NETWORK

    def test_classification_handles_multi_error_scenarios(
        self, runner: JobRunner
    ) -> None:
        """Test that multi-error scenarios are classified correctly."""
        # Create a result that might have multiple error signals
        result = ExecutionResult(
            success=False,
            stdout="Rate limit exceeded\nValidation failed",
            stderr="Error: 429 Too Many Requests\nFile not found",
            exit_code=1,
            duration_seconds=1.0,
        )

        # This should classify and potentially log secondary errors
        classification = runner._classify_execution(result)

        # Primary should be identified
        assert classification.primary is not None


# =============================================================================
# TestRateLimitHandling
# =============================================================================


class TestRateLimitHandling:
    """Tests for _handle_rate_limit() method."""

    @pytest.mark.asyncio
    async def test_rate_limit_increments_wait_counter(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Test that rate limit handling increments the wait counter."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        # Patch asyncio.sleep to avoid actual waiting
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E101",
                suggested_wait_seconds=1.0,  # Short wait for test
            )

        assert state.rate_limit_waits == 1

    @pytest.mark.asyncio
    async def test_quota_exhaustion_increments_quota_counter(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Test that quota exhaustion increments quota_waits."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E104",  # Quota exhaustion
                suggested_wait_seconds=1.0,
            )

        assert state.quota_waits == 1
        assert state.rate_limit_waits == 0  # Should not increment

    @pytest.mark.asyncio
    async def test_max_waits_exceeded_raises_fatal_error(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Test that exceeding max waits raises FatalError."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
            rate_limit_waits=2,  # Already at max_waits - 1
        )

        with (
            pytest.raises(FatalError) as exc_info,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E101",
                suggested_wait_seconds=1.0,
            )

        assert "Exceeded maximum rate limit waits" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_quota_exhaustion_raises_when_max_exceeded(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Test that quota exhaustion raises FatalError when max_quota_waits exceeded."""
        max_quota = runner_with_global_store.config.rate_limit.max_quota_waits
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
            quota_waits=max_quota,  # At the limit — should raise
        )

        with (
            pytest.raises(FatalError, match="quota exhaustion waits"),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E104",
                suggested_wait_seconds=1.0,
            )

    @pytest.mark.asyncio
    async def test_quota_exhaustion_allows_waits_below_limit(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Test that quota exhaustion waits proceed when below max_quota_waits."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
            quota_waits=2,  # Well below default max_quota_waits
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E104",
                suggested_wait_seconds=1.0,
            )

        assert state.quota_waits == 3

    @pytest.mark.asyncio
    async def test_health_check_failure_raises_fatal_error(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Test that failed health check raises FatalError."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        # Make health check fail
        runner_with_global_store.backend.health_check = AsyncMock(return_value=False)

        with (
            pytest.raises(FatalError) as exc_info,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E101",
                suggested_wait_seconds=1.0,
            )

        assert "health check failed" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_quota_exhaustion_uses_availability_check(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Quota exhaustion (E104) uses availability_check, not health_check."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        runner_with_global_store.backend.availability_check = AsyncMock(
            return_value=True,
        )
        runner_with_global_store.backend.health_check = AsyncMock(
            return_value=True,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E104",
                suggested_wait_seconds=1.0,
            )

        runner_with_global_store.backend.availability_check.assert_called_once()
        runner_with_global_store.backend.health_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_quota_availability_check_retries_on_failure(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Availability check retries with backoff before giving up."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        # Fail twice, succeed on third attempt
        runner_with_global_store.backend.availability_check = AsyncMock(
            side_effect=[False, False, True],
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E104",
                suggested_wait_seconds=1.0,
            )

        assert runner_with_global_store.backend.availability_check.call_count == 3

    @pytest.mark.asyncio
    async def test_quota_availability_check_fatal_after_max_retries(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """FatalError raised after all availability check retries exhausted."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        runner_with_global_store.backend.availability_check = AsyncMock(
            return_value=False,
        )

        with (
            pytest.raises(FatalError) as exc_info,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E104",
                suggested_wait_seconds=1.0,
            )

        assert "availability" in str(exc_info.value).lower()
        assert runner_with_global_store.backend.availability_check.call_count == 3

    @pytest.mark.asyncio
    async def test_regular_rate_limit_still_uses_health_check(
        self,
        runner_with_global_store: JobRunner,
    ) -> None:
        """Regular rate limit (E101) still uses health_check, not availability_check."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        runner_with_global_store.backend.health_check = AsyncMock(
            return_value=True,
        )
        runner_with_global_store.backend.availability_check = AsyncMock(
            return_value=True,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E101",
                suggested_wait_seconds=1.0,
            )

        runner_with_global_store.backend.health_check.assert_called_once()
        runner_with_global_store.backend.availability_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_to_global_store(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that rate limit events are recorded to global store."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await runner_with_global_store._handle_rate_limit(
                state=state,
                error_code="E101",
                suggested_wait_seconds=60.0,
            )

        mock_global_store.record_rate_limit_event.assert_called_once()
        call_kwargs = mock_global_store.record_rate_limit_event.call_args.kwargs
        assert call_kwargs["error_code"] == "E101"
        assert call_kwargs["duration_seconds"] == 60.0


# =============================================================================
# TestCrossWorkspaceRateLimitCheck
# =============================================================================


class TestCrossWorkspaceRateLimitCheck:
    """Tests for _check_cross_workspace_rate_limit() method."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_global_store(
        self,
        runner: JobRunner,
    ) -> None:
        """Test that it returns (False, None) without global store."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        is_limited, wait_time = await runner._check_cross_workspace_rate_limit(state)

        assert is_limited is False
        assert wait_time is None

    @pytest.mark.asyncio
    async def test_returns_false_when_not_rate_limited(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that it returns (False, None) when not rate limited."""
        mock_global_store.is_rate_limited.return_value = (False, None)

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        is_limited, wait_time = (
            await runner_with_global_store._check_cross_workspace_rate_limit(state)
        )

        assert is_limited is False
        assert wait_time is None

    @pytest.mark.asyncio
    async def test_returns_true_when_rate_limited(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that it returns (True, wait_time) when rate limited."""
        mock_global_store.is_rate_limited.return_value = (True, 120.0)

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        is_limited, wait_time = (
            await runner_with_global_store._check_cross_workspace_rate_limit(state)
        )

        assert is_limited is True
        assert wait_time == 120.0

    @pytest.mark.asyncio
    async def test_handles_global_store_error_gracefully(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that errors from global store are handled gracefully."""
        mock_global_store.is_rate_limited.side_effect = sqlite3.OperationalError("Database error")

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )

        # Should not raise, should return safe default
        is_limited, wait_time = (
            await runner_with_global_store._check_cross_workspace_rate_limit(state)
        )

        assert is_limited is False
        assert wait_time is None


# =============================================================================
# TestSelfHealing
# =============================================================================


class TestSelfHealing:
    """Tests for _try_self_healing() method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_coordinator(
        self,
        runner: JobRunner,
    ) -> None:
        """Test that it returns None without healing coordinator."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error occurred",
            exit_code=1,
            duration_seconds=1.0,
        )
        error = ClassifiedError(
            error_code=ErrorCode.EXECUTION_UNKNOWN,
            category=ErrorCategory.TRANSIENT,
            message="Unknown error",
        )

        report = await runner._try_self_healing(
            result=result,
            error=error,
            config_path=None,
            sheet_num=1,
            retry_count=3,
            max_retries=3,
        )

        assert report is None

    @pytest.mark.asyncio
    async def test_calls_healing_coordinator(
        self,
        runner: JobRunner,
        mock_healing_coordinator: MagicMock,
    ) -> None:
        """Test that healing coordinator is called."""
        runner._healing_coordinator = mock_healing_coordinator

        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error occurred",
            exit_code=1,
            duration_seconds=1.0,
        )
        error = ClassifiedError(
            error_code=ErrorCode.EXECUTION_UNKNOWN,
            category=ErrorCategory.TRANSIENT,
            message="Unknown error",
        )

        report = await runner._try_self_healing(
            result=result,
            error=error,
            config_path=Path("/test/config.yaml"),
            sheet_num=1,
            retry_count=3,
            max_retries=3,
        )

        assert report is not None
        mock_healing_coordinator.heal.assert_called_once()

    @pytest.mark.asyncio
    async def test_healing_with_remedies_applied(
        self,
        runner: JobRunner,
    ) -> None:
        """Test healing report when remedies are successfully applied."""
        mock_report = MagicMock()
        mock_report.any_remedies_applied = True
        mock_report.actions_taken = [("create_workspace", "Created missing workspace directory")]
        mock_report.issues_remaining = 0
        mock_report.should_retry = True

        coordinator = AsyncMock()
        coordinator.heal = AsyncMock(return_value=mock_report)
        runner._healing_coordinator = coordinator

        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="No such directory: /workspace",
            exit_code=1,
            duration_seconds=1.0,
        )
        error = ClassifiedError(
            error_code=ErrorCode.PREFLIGHT_PATH_MISSING,
            category=ErrorCategory.CONFIGURATION,
            message="Workspace directory missing",
        )

        report = await runner._try_self_healing(
            result=result,
            error=error,
            config_path=Path("/test/config.yaml"),
            sheet_num=1,
            retry_count=3,
            max_retries=3,
        )

        assert report is not None
        assert report.any_remedies_applied is True
        assert len(report.actions_taken) == 1
        assert report.should_retry is True

    @pytest.mark.asyncio
    async def test_healing_with_no_applicable_remedies(
        self,
        runner: JobRunner,
    ) -> None:
        """Test healing when coordinator finds no applicable remedies."""
        mock_report = MagicMock()
        mock_report.any_remedies_applied = False
        mock_report.actions_taken = []
        mock_report.issues_remaining = 1
        mock_report.should_retry = False

        coordinator = AsyncMock()
        coordinator.heal = AsyncMock(return_value=mock_report)
        runner._healing_coordinator = coordinator

        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Unknown catastrophic error",
            exit_code=1,
            duration_seconds=1.0,
        )
        error = ClassifiedError(
            error_code=ErrorCode.EXECUTION_UNKNOWN,
            category=ErrorCategory.TRANSIENT,
            message="Unknown error",
        )

        report = await runner._try_self_healing(
            result=result,
            error=error,
            config_path=None,
            sheet_num=1,
            retry_count=3,
            max_retries=3,
        )

        assert report is not None
        assert report.any_remedies_applied is False
        assert report.should_retry is False


# =============================================================================
# TestBroadcastPolling
# =============================================================================


class TestBroadcastPolling:
    """Tests for _poll_broadcast_discoveries() method."""

    @pytest.mark.asyncio
    async def test_does_nothing_without_global_store(
        self,
        runner: JobRunner,
    ) -> None:
        """Test that polling does nothing without global store."""
        # Should not raise
        await runner._poll_broadcast_discoveries(job_id="test-job", sheet_num=1)

    @pytest.mark.asyncio
    async def test_calls_check_recent_pattern_discoveries(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that it calls check_recent_pattern_discoveries."""
        await runner_with_global_store._poll_broadcast_discoveries(
            job_id="test-job",
            sheet_num=1,
        )

        mock_global_store.check_recent_pattern_discoveries.assert_called_once()
        call_kwargs = mock_global_store.check_recent_pattern_discoveries.call_args.kwargs
        assert call_kwargs["exclude_job_id"] == "test-job"
        assert call_kwargs["min_effectiveness"] == 0.5
        assert call_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that polling errors don't block execution."""
        mock_global_store.check_recent_pattern_discoveries.side_effect = (
            sqlite3.OperationalError("DB error")
        )

        # Should not raise
        await runner_with_global_store._poll_broadcast_discoveries(
            job_id="test-job",
            sheet_num=1,
        )


# =============================================================================
# TestErrorRecoveryRecording
# =============================================================================


class TestErrorRecoveryRecording:
    """Tests for _record_error_recovery() method."""

    @pytest.mark.asyncio
    async def test_does_nothing_without_global_store(
        self,
        runner: JobRunner,
    ) -> None:
        """Test that recording does nothing without global store."""
        # Should not raise
        await runner._record_error_recovery(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=60.0,
            recovery_success=True,
        )

    @pytest.mark.asyncio
    async def test_records_to_global_store(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that recovery is recorded to global store."""
        await runner_with_global_store._record_error_recovery(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=65.0,
            recovery_success=True,
        )

        mock_global_store.record_error_recovery.assert_called_once_with(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=65.0,
            recovery_success=True,
        )

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(
        self,
        runner_with_global_store: JobRunner,
        mock_global_store: MagicMock,
    ) -> None:
        """Test that recording errors don't block execution."""
        mock_global_store.record_error_recovery.side_effect = sqlite3.OperationalError("DB error")

        # Should not raise
        await runner_with_global_store._record_error_recovery(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=60.0,
            recovery_success=True,
        )
