"""Tests for F-111 (RateLimitExhaustedError preserved in parallel mode)
and F-113 (failed dependency propagation in parallel executor).

These are TDD tests — written before the implementation.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.execution.dag import DependencyDAG
from marianne.execution.parallel import (
    ParallelBatchResult,
    ParallelExecutionConfig,
    ParallelExecutor,
)
from marianne.execution.runner.models import FatalError, RateLimitExhaustedError


def _make_state(total: int, job_id: str = "test-job") -> CheckpointState:
    """Create a CheckpointState with all sheets initialized."""
    state = CheckpointState(
        job_id=job_id,
        job_name=job_id,
        total_sheets=total,
    )
    for i in range(1, total + 1):
        state.sheets[i] = SheetState(sheet_num=i)
    return state


def _make_runner(dag: DependencyDAG | None = None) -> MagicMock:
    """Create a mock JobRunner with required attributes."""
    runner = MagicMock()
    runner._state_lock = asyncio.Lock()
    runner.state_backend = AsyncMock()
    runner.dependency_dag = dag
    return runner


# ---------------------------------------------------------------------------
# F-111: RateLimitExhaustedError must be preserved, not flattened to string
# ---------------------------------------------------------------------------


class TestF111RateLimitPreserved:
    """When a sheet in a parallel batch raises RateLimitExhaustedError,
    the exception must be preserved in ParallelBatchResult.exceptions
    so the lifecycle can re-raise it (pausing the job, not failing it).
    """

    async def test_batch_result_preserves_rate_limit_exception(self) -> None:
        """ParallelBatchResult.exceptions should contain the original
        RateLimitExhaustedError, not just a string representation."""
        resume_after = datetime.now(UTC) + timedelta(hours=1)
        rate_err = RateLimitExhaustedError(
            "Quota exhausted",
            resume_after=resume_after,
            backend_type="claude-cli",
            quota_exhaustion=True,
        )

        runner = _make_runner()

        async def mock_execute(state: CheckpointState, sheet_num: int) -> None:
            if sheet_num == 2:
                raise rate_err

        runner._execute_sheet_with_recovery = AsyncMock(side_effect=mock_execute)

        executor = ParallelExecutor(
            runner, ParallelExecutionConfig(enabled=True, max_concurrent=3)
        )
        state = _make_state(2)

        result = await executor.execute_batch([1, 2], state)

        assert 2 in result.failed
        assert 2 in result.exceptions
        exc = result.exceptions[2]
        assert isinstance(exc, RateLimitExhaustedError)
        assert exc.resume_after == resume_after
        assert exc.backend_type == "claude-cli"
        assert exc.quota_exhaustion is True

    async def test_batch_result_has_exceptions_field(self) -> None:
        """ParallelBatchResult should have an exceptions dict."""
        result = ParallelBatchResult(sheets=[1, 2])
        assert hasattr(result, "exceptions")
        assert isinstance(result.exceptions, dict)
        assert len(result.exceptions) == 0

    async def test_non_rate_limit_errors_also_preserved(self) -> None:
        """All exception types should be preserved in exceptions dict."""
        runner = _make_runner()

        async def mock_execute(state: CheckpointState, sheet_num: int) -> None:
            if sheet_num == 1:
                raise FatalError("Auth failed")

        runner._execute_sheet_with_recovery = AsyncMock(side_effect=mock_execute)

        executor = ParallelExecutor(
            runner, ParallelExecutionConfig(enabled=True, max_concurrent=3)
        )
        state = _make_state(2)

        result = await executor.execute_batch([1, 2], state)

        assert 1 in result.exceptions
        assert isinstance(result.exceptions[1], FatalError)

    async def test_all_sheets_rate_limited(self) -> None:
        """When ALL sheets in a batch hit rate limits, every exception
        should be preserved."""
        resume_after = datetime.now(UTC) + timedelta(hours=1)
        runner = _make_runner()

        async def mock_execute(state: CheckpointState, sheet_num: int) -> None:
            raise RateLimitExhaustedError(
                f"Rate limit on sheet {sheet_num}",
                resume_after=resume_after,
                backend_type="claude-cli",
            )

        runner._execute_sheet_with_recovery = AsyncMock(side_effect=mock_execute)

        executor = ParallelExecutor(
            runner, ParallelExecutionConfig(enabled=True, max_concurrent=3)
        )
        state = _make_state(3)

        result = await executor.execute_batch([1, 2, 3], state)

        assert len(result.failed) == 3
        for sheet_num in [1, 2, 3]:
            assert sheet_num in result.exceptions
            assert isinstance(result.exceptions[sheet_num], RateLimitExhaustedError)


# ---------------------------------------------------------------------------
# F-111: Lifecycle integration — _execute_parallel_mode must handle rate limits
# ---------------------------------------------------------------------------


class TestF111LifecycleRateLimitExtraction:
    """The lifecycle should extract RateLimitExhaustedError from the batch
    result and re-raise it so the job gets PAUSED, not FAILED."""

    def test_find_rate_limit_from_batch_result(self) -> None:
        """Helper should find the first RateLimitExhaustedError."""
        from tests.test_parallel_rate_limit_and_deps import _find_rate_limit_exception

        resume_after = datetime.now(UTC) + timedelta(hours=1)
        rate_err = RateLimitExhaustedError(
            "Quota exhausted",
            resume_after=resume_after,
            backend_type="claude-cli",
            quota_exhaustion=True,
        )

        result = ParallelBatchResult(
            sheets=[1, 2],
            completed=[1],
            failed=[2],
            error_details={2: "RateLimitExhaustedError: Quota exhausted"},
            exceptions={2: rate_err},
        )

        found = _find_rate_limit_exception(result)
        assert found is rate_err

    def test_no_rate_limit_returns_none(self) -> None:
        """When no RateLimitExhaustedError exists, return None."""
        from tests.test_parallel_rate_limit_and_deps import _find_rate_limit_exception

        result = ParallelBatchResult(
            sheets=[1, 2],
            completed=[1],
            failed=[2],
            error_details={2: "FatalError: Auth failed"},
            exceptions={2: FatalError("Auth failed")},
        )

        found = _find_rate_limit_exception(result)
        assert found is None

    def test_mixed_errors_rate_limit_found(self) -> None:
        """In mixed error batches, rate limit should be extracted."""
        from tests.test_parallel_rate_limit_and_deps import _find_rate_limit_exception

        resume_after = datetime.now(UTC) + timedelta(hours=1)
        rate_err = RateLimitExhaustedError(
            "Quota exhausted",
            resume_after=resume_after,
            backend_type="claude-cli",
        )

        result = ParallelBatchResult(
            sheets=[1, 2, 3],
            completed=[],
            failed=[1, 2, 3],
            error_details={
                1: "FatalError: Auth failed",
                2: "RateLimitExhaustedError: Quota exhausted",
                3: "FatalError: Timeout",
            },
            exceptions={
                1: FatalError("Auth failed"),
                2: rate_err,
                3: FatalError("Timeout"),
            },
        )

        found = _find_rate_limit_exception(result)
        assert found is rate_err


# ---------------------------------------------------------------------------
# F-113: Failed dependencies should propagate failure, not dispatch downstream
# ---------------------------------------------------------------------------


class TestF113FailedDependencyPropagation:
    """When a sheet permanently fails, downstream dependent sheets should be
    marked FAILED with 'dependency failed' reason, NOT dispatched."""

    def test_failed_dep_blocks_downstream_batch(self) -> None:
        """After failure propagation, downstream sheets should NOT appear
        in the next parallel batch."""
        dag = DependencyDAG.from_dependencies(3, {3: [1, 2]})
        runner = _make_runner(dag)
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=False
        )
        executor = ParallelExecutor(runner, config)

        state = _make_state(3)
        state.sheets[1].status = SheetStatus.COMPLETED
        state.sheets[2].status = SheetStatus.FAILED
        executor._permanently_failed.add(2)

        # Propagate failure from sheet 2
        executor.propagate_failure_to_dependents(state, 2)

        # Sheet 3 should be FAILED with dependency message
        assert state.sheets[3].status == SheetStatus.FAILED
        assert "dependency" in (state.sheets[3].error_message or "").lower()

        # Next batch should be empty
        batch = executor.get_next_parallel_batch(state)
        assert batch == []

    def test_transitive_failure_propagation(self) -> None:
        """Failure should propagate transitively: 1 fails → 2 fails → 3 fails."""
        dag = DependencyDAG.from_dependencies(3, {2: [1], 3: [2]})
        runner = _make_runner(dag)
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=False
        )
        executor = ParallelExecutor(runner, config)

        state = _make_state(3)
        state.sheets[1].status = SheetStatus.FAILED
        executor._permanently_failed.add(1)

        executor.propagate_failure_to_dependents(state, 1)

        assert state.sheets[2].status == SheetStatus.FAILED
        assert state.sheets[3].status == SheetStatus.FAILED
        # Both should be in _permanently_failed
        assert 2 in executor._permanently_failed
        assert 3 in executor._permanently_failed

    def test_completed_sheets_not_affected_by_sibling_failure(self) -> None:
        """Completed siblings should not be touched by propagation."""
        dag = DependencyDAG.from_dependencies(3, {3: [1, 2]})
        runner = _make_runner(dag)
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=False
        )
        executor = ParallelExecutor(runner, config)

        state = _make_state(3)
        state.sheets[1].status = SheetStatus.COMPLETED
        state.sheets[2].status = SheetStatus.FAILED
        executor._permanently_failed.add(2)

        executor.propagate_failure_to_dependents(state, 2)

        assert state.sheets[1].status == SheetStatus.COMPLETED
        assert state.sheets[3].status == SheetStatus.FAILED

    def test_already_terminal_sheets_not_overwritten(self) -> None:
        """Terminal sheets (COMPLETED, SKIPPED) should not be overwritten."""
        dag = DependencyDAG.from_dependencies(3, {2: [1], 3: [1]})
        runner = _make_runner(dag)
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=False
        )
        executor = ParallelExecutor(runner, config)

        state = _make_state(3)
        state.sheets[1].status = SheetStatus.FAILED
        state.sheets[2].status = SheetStatus.COMPLETED  # Already done
        state.sheets[3].status = SheetStatus.PENDING
        executor._permanently_failed.add(1)

        executor.propagate_failure_to_dependents(state, 1)

        assert state.sheets[2].status == SheetStatus.COMPLETED
        assert state.sheets[3].status == SheetStatus.FAILED

    def test_fan_out_partial_failure_propagation(self) -> None:
        """In a fan-out where 1 of N instances fails, the synthesis stage
        that depends on ALL instances should be FAILED."""
        dag = DependencyDAG.from_dependencies(4, {4: [1, 2, 3]})
        runner = _make_runner(dag)
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=False
        )
        executor = ParallelExecutor(runner, config)

        state = _make_state(4)
        state.sheets[1].status = SheetStatus.COMPLETED
        state.sheets[2].status = SheetStatus.FAILED
        state.sheets[3].status = SheetStatus.COMPLETED
        executor._permanently_failed.add(2)

        executor.propagate_failure_to_dependents(state, 2)

        assert state.sheets[4].status == SheetStatus.FAILED
        assert "dependency" in (state.sheets[4].error_message or "").lower()

    def test_no_dag_no_propagation(self) -> None:
        """Without a DAG, propagation is a no-op."""
        runner = _make_runner(None)
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=False
        )
        executor = ParallelExecutor(runner, config)

        state = _make_state(3)
        state.sheets[1].status = SheetStatus.FAILED

        executor.propagate_failure_to_dependents(state, 1)

        assert state.sheets[2].status == SheetStatus.PENDING
        assert state.sheets[3].status == SheetStatus.PENDING

    def test_done_for_dag_excludes_permanently_failed(self) -> None:
        """After propagation, get_next_parallel_batch should NOT include
        failed dependents (they're in _permanently_failed and state is FAILED)."""
        dag = DependencyDAG.from_dependencies(4, {2: [1], 3: [1], 4: [2, 3]})
        runner = _make_runner(dag)
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=False
        )
        executor = ParallelExecutor(runner, config)

        state = _make_state(4)
        state.sheets[1].status = SheetStatus.FAILED
        executor._permanently_failed.add(1)

        # Propagate: sheets 2, 3, 4 should all be FAILED
        executor.propagate_failure_to_dependents(state, 1)

        batch = executor.get_next_parallel_batch(state)
        assert batch == []


# ---------------------------------------------------------------------------
# Helper function for F-111 lifecycle integration
# ---------------------------------------------------------------------------


def _find_rate_limit_exception(
    result: ParallelBatchResult,
) -> RateLimitExhaustedError | None:
    """Find the first RateLimitExhaustedError in a batch result's exceptions.

    Used by _execute_parallel_mode to decide whether to pause or fail.
    """
    for sheet_num in result.failed:
        exc = result.exceptions.get(sheet_num)
        if isinstance(exc, RateLimitExhaustedError):
            return exc
    return None
