"""Adversarial tests — Movement 2, Cycle 2 (Adversary).

Attack surfaces:
1. Recovery state mapping completeness and edge cases
2. Recovery with corrupted/inconsistent checkpoint data
3. Recovery + cost limit intersections
4. F-111 rate limit preservation regression
5. F-113 failure propagation regression (parallel executor)
6. Recovery + dependency propagation gaps
7. Double recovery / duplicate registration
8. Resume handler cost limit re-check (F-143 regression)
9. Terminal state resistance in recovered jobs
10. Completion detection for partially-recovered jobs
11. State sync callback correctness
12. Credential redaction coverage on recovery paths
13. Dependency satisfaction with FAILED sheets (F-113 baton side)

Every test here either proves a fix holds or probes an intersection
that could silently fail. Recovery is my specialty — the bugs hide
after the system recovers, not during the crash.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from marianne.daemon.baton.adapter import (
    _BATON_TO_CHECKPOINT,
    _CHECKPOINT_TO_BATON,
    BatonAdapter,
    baton_to_checkpoint_status,
    checkpoint_to_baton_status,
)
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    PauseJob,
    ProcessExited,
    ResumeJob,
    SheetAttemptResult,
    SheetSkipped,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    SheetExecutionState,
)

# =========================================================================
# Helpers
# =========================================================================


def _make_sheet_state(
    sheet_num: int,
    status: BatonSheetStatus = BatonSheetStatus.PENDING,
    instrument: str = "claude-code",
    max_retries: int = 3,
    max_completion: int = 5,
    normal_attempts: int = 0,
    completion_attempts: int = 0,
    total_cost_usd: float = 0.0,
) -> SheetExecutionState:
    """Create a SheetExecutionState with given parameters."""
    state = SheetExecutionState(
        sheet_num=sheet_num,
        instrument_name=instrument,
        max_retries=max_retries,
        max_completion=max_completion,
    )
    state.status = status
    state.normal_attempts = normal_attempts
    state.completion_attempts = completion_attempts
    state.total_cost_usd = total_cost_usd
    return state


def _success_result(
    job_id: str,
    sheet_num: int,
    instrument: str = "claude-code",
    cost: float = 0.5,
) -> SheetAttemptResult:
    """Create a successful SheetAttemptResult."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=1,
        execution_success=True,
        validation_pass_rate=100.0,
        validations_passed=1,
        validations_total=1,
        cost_usd=cost,
    )


def _failure_result(
    job_id: str,
    sheet_num: int,
    instrument: str = "claude-code",
    cost: float = 0.1,
    rate_limited: bool = False,
) -> SheetAttemptResult:
    """Create a failed SheetAttemptResult."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=1,
        execution_success=False,
        exit_code=1,
        validation_pass_rate=0.0,
        cost_usd=cost,
        rate_limited=rate_limited,
        error_classification="TRANSIENT",
        error_message="Command failed",
    )


# =========================================================================
# 1. State Mapping Completeness
# =========================================================================


class TestStateMappingCompleteness:
    """Verify that every status in both enums has a mapping in both directions."""

    def test_every_baton_status_maps_to_checkpoint(self) -> None:
        """Every BatonSheetStatus must have a checkpoint mapping."""
        for status in BatonSheetStatus:
            assert status in _BATON_TO_CHECKPOINT, (
                f"BatonSheetStatus.{status.name} has no checkpoint mapping"
            )

    def test_every_checkpoint_status_maps_to_baton(self) -> None:
        """Every CheckpointState status string must map to a BatonSheetStatus."""
        from marianne.core.checkpoint import SheetStatus

        for status in SheetStatus:
            assert status.value in _CHECKPOINT_TO_BATON, (
                f"SheetStatus.{status.name} ('{status.value}') has no baton mapping"
            )

    def test_baton_to_checkpoint_roundtrip_terminal_states(self) -> None:
        """Terminal states must survive a roundtrip through both mappings."""
        terminal_pairs = [
            (BatonSheetStatus.COMPLETED, "completed"),
            (BatonSheetStatus.FAILED, "failed"),
            (BatonSheetStatus.SKIPPED, "skipped"),
        ]
        for baton_status, checkpoint_status in terminal_pairs:
            cp_status = baton_to_checkpoint_status(baton_status)
            assert cp_status == checkpoint_status
            recovered = checkpoint_to_baton_status(cp_status)
            assert recovered == baton_status

    def test_cancelled_maps_to_failed_in_checkpoint(self) -> None:
        """CANCELLED has no checkpoint equivalent — must map to 'failed'."""
        cp_status = baton_to_checkpoint_status(BatonSheetStatus.CANCELLED)
        assert cp_status == "failed"

    def test_unknown_checkpoint_status_raises_key_error(self) -> None:
        """An unknown status string must raise KeyError, not silently default."""
        with pytest.raises(KeyError):
            checkpoint_to_baton_status("running_very_fast")

    def test_in_progress_maps_to_dispatched_on_recovery(self) -> None:
        """in_progress in checkpoint → DISPATCHED in baton.

        But the adapter overrides this to PENDING for in_progress sheets.
        This test documents the raw mapping behavior.
        """
        baton_status = checkpoint_to_baton_status("in_progress")
        assert baton_status == BatonSheetStatus.DISPATCHED

    def test_baton_to_checkpoint_collapses_non_terminal_correctly(self) -> None:
        """Non-terminal baton states should map to only pending or in_progress."""
        non_terminal_mappings = {
            BatonSheetStatus.PENDING: "pending",
            BatonSheetStatus.READY: "pending",
            BatonSheetStatus.DISPATCHED: "in_progress",
            BatonSheetStatus.RUNNING: "in_progress",
            BatonSheetStatus.WAITING: "in_progress",
            BatonSheetStatus.RETRY_SCHEDULED: "pending",
            BatonSheetStatus.FERMATA: "in_progress",
        }
        for baton_status, expected_cp in non_terminal_mappings.items():
            actual = baton_to_checkpoint_status(baton_status)
            assert actual == expected_cp, (
                f"{baton_status.name} → '{actual}', expected '{expected_cp}'"
            )


# =========================================================================
# 2. Recovery with Edge-Case Checkpoint Data (via Adapter)
# =========================================================================


class TestRecoveryEdgeCases:
    """Test adapter.recover_job with adversarial checkpoint states."""

    def _make_adapter(self) -> BatonAdapter:
        """Create a BatonAdapter with mocked event bus."""
        return BatonAdapter(event_bus=MagicMock())

    def _make_checkpoint(
        self,
        sheets: dict[int, dict[str, Any]],
    ) -> Any:
        """Create a mock checkpoint with given sheet data."""
        from marianne.core.checkpoint import SheetStatus

        checkpoint = MagicMock()
        mock_sheets: dict[int, MagicMock] = {}
        for num, data in sheets.items():
            sheet = MagicMock()
            status_str = data.get("status", "pending")
            sheet.status = SheetStatus(status_str)
            sheet.attempt_count = data.get("attempt_count", 0)
            sheet.completion_attempts = data.get("completion_attempts", 0)
            mock_sheets[num] = sheet
        checkpoint.sheets = mock_sheets
        return checkpoint

    def _make_mock_sheets(self, nums: list[int]) -> list[Any]:
        """Create mock Sheet objects."""
        sheets = []
        for num in nums:
            s = MagicMock()
            s.num = num
            s.instrument_name = "claude-code"
            s.movement = 1
            sheets.append(s)
        return sheets

    def test_recovery_with_extra_sheets_in_checkpoint(self) -> None:
        """Checkpoint has sheets that don't exist in current config.

        This happens when config changes between runs (sheets removed).
        The extra checkpoint sheets should be ignored.
        """
        adapter = self._make_adapter()
        sheets = self._make_mock_sheets([1, 2, 3])
        checkpoint = self._make_checkpoint({
            1: {"status": "completed"},
            2: {"status": "in_progress"},
            3: {"status": "pending"},
            4: {"status": "completed"},  # Extra — not in config
            5: {"status": "failed"},  # Extra — not in config
        })

        adapter.recover_job("test-job", sheets, {}, checkpoint, max_retries=3)

        # Only 3 sheets registered (not 5)
        job = adapter._baton._jobs.get("test-job")
        assert job is not None
        assert len(job.sheets) == 3
        assert 4 not in job.sheets
        assert 5 not in job.sheets

    def test_recovery_with_fewer_sheets_in_checkpoint(self) -> None:
        """Config has sheets that don't exist in checkpoint.

        New sheets start as PENDING with zero attempt counts.
        """
        adapter = self._make_adapter()
        sheets = self._make_mock_sheets([1, 2, 3, 4])
        checkpoint = self._make_checkpoint({
            1: {"status": "completed"},
            2: {"status": "failed"},
        })

        adapter.recover_job("test-job", sheets, {}, checkpoint, max_retries=3)

        job = adapter._baton._jobs.get("test-job")
        assert job is not None
        assert len(job.sheets) == 4
        assert job.sheets[1].status == BatonSheetStatus.COMPLETED
        assert job.sheets[2].status == BatonSheetStatus.FAILED
        assert job.sheets[3].status == BatonSheetStatus.PENDING
        assert job.sheets[3].normal_attempts == 0
        assert job.sheets[4].status == BatonSheetStatus.PENDING
        assert job.sheets[4].normal_attempts == 0

    def test_recovery_preserves_attempt_counts(self) -> None:
        """Attempt counts from checkpoint must survive recovery.

        Without this, a sheet that used 2 of 3 retries before restart
        gets 3 fresh retries — potential infinite retry loop.
        """
        adapter = self._make_adapter()
        sheets = self._make_mock_sheets([1])
        checkpoint = self._make_checkpoint({
            1: {"status": "in_progress", "attempt_count": 2, "completion_attempts": 1},
        })

        adapter.recover_job("test-job", sheets, {}, checkpoint, max_retries=3)

        job = adapter._baton._jobs.get("test-job")
        assert job is not None
        state = job.sheets[1]
        # in_progress → PENDING (musician died), but counts preserved
        assert state.status == BatonSheetStatus.PENDING
        assert state.normal_attempts == 2
        assert state.completion_attempts == 1

    def test_recovery_in_progress_resets_to_pending(self) -> None:
        """in_progress sheets must become PENDING on recovery.

        The musician executing them was killed on restart.
        """
        adapter = self._make_adapter()
        sheets = self._make_mock_sheets([1, 2, 3])
        checkpoint = self._make_checkpoint({
            1: {"status": "in_progress"},
            2: {"status": "in_progress"},
            3: {"status": "completed"},
        })

        adapter.recover_job("test-job", sheets, {}, checkpoint, max_retries=3)

        job = adapter._baton._jobs.get("test-job")
        assert job.sheets[1].status == BatonSheetStatus.PENDING
        assert job.sheets[2].status == BatonSheetStatus.PENDING
        assert job.sheets[3].status == BatonSheetStatus.COMPLETED

    def test_recovery_empty_checkpoint_all_pending(self) -> None:
        """Recovery with no sheets in checkpoint — all start fresh."""
        adapter = self._make_adapter()
        sheets = self._make_mock_sheets([1, 2, 3])
        checkpoint = self._make_checkpoint({})

        adapter.recover_job("test-job", sheets, {}, checkpoint, max_retries=3)

        job = adapter._baton._jobs.get("test-job")
        for sheet_num in [1, 2, 3]:
            assert job.sheets[sheet_num].status == BatonSheetStatus.PENDING
            assert job.sheets[sheet_num].normal_attempts == 0

    def test_recovery_terminal_states_preserved(self) -> None:
        """All terminal checkpoint states must map to terminal baton states."""
        adapter = self._make_adapter()
        sheets = self._make_mock_sheets([1, 2, 3])
        checkpoint = self._make_checkpoint({
            1: {"status": "completed"},
            2: {"status": "failed"},
            3: {"status": "skipped"},
        })

        adapter.recover_job("test-job", sheets, {}, checkpoint, max_retries=3)

        job = adapter._baton._jobs.get("test-job")
        assert job.sheets[1].status == BatonSheetStatus.COMPLETED
        assert job.sheets[2].status == BatonSheetStatus.FAILED
        assert job.sheets[3].status == BatonSheetStatus.SKIPPED


# =========================================================================
# 3. Recovery + Cost Limit Interactions
# =========================================================================


class TestRecoveryCostInteractions:
    """Test that cost limits work correctly after recovery."""

    def test_recovery_sets_cost_limit(self) -> None:
        """Cost limits passed to recover_job must be applied."""
        adapter = BatonAdapter(event_bus=MagicMock())
        sheets = [MagicMock(num=1, instrument_name="claude-code", movement=1)]
        cp = MagicMock()
        cp_sheet = MagicMock()
        cp_sheet.status = MagicMock(value="pending")
        cp_sheet.attempt_count = 0
        cp_sheet.completion_attempts = 0
        cp.sheets = {1: cp_sheet}

        adapter.recover_job("test-job", sheets, {}, cp, max_cost_usd=10.0, max_retries=3)

        assert "test-job" in adapter._baton._job_cost_limits
        assert adapter._baton._job_cost_limits["test-job"] == 10.0

    def test_recovery_without_cost_limit_has_no_limit(self) -> None:
        """No cost limit arg → no cost enforcement."""
        adapter = BatonAdapter(event_bus=MagicMock())
        sheets = [MagicMock(num=1, instrument_name="claude-code", movement=1)]
        cp = MagicMock()
        cp_sheet = MagicMock()
        cp_sheet.status = MagicMock(value="pending")
        cp_sheet.attempt_count = 0
        cp_sheet.completion_attempts = 0
        cp.sheets = {1: cp_sheet}

        adapter.recover_job("test-job", sheets, {}, cp, max_cost_usd=None, max_retries=3)

        assert "test-job" not in adapter._baton._job_cost_limits

    @pytest.mark.asyncio
    async def test_recovery_cost_limit_enforced_after_first_attempt(self) -> None:
        """After recovery, the first attempt should trigger cost limit check."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED, total_cost_usd=5.0),
            2: _make_sheet_state(2),
        }
        baton.register_job("j1", states, {})
        baton.set_job_cost_limit("j1", 10.0)

        # Sheet 2 dispatches and produces a high-cost result
        baton._jobs["j1"].sheets[2].status = BatonSheetStatus.RUNNING
        result = _success_result("j1", 2, cost=6.0)  # Total: 5+6 = 11 > 10
        await baton.handle_event(result)

        # Sheet 2 completes but job should be paused
        assert baton._jobs["j1"].sheets[2].status == BatonSheetStatus.COMPLETED
        assert baton._jobs["j1"].paused, "Job should be paused after exceeding cost limit"


# =========================================================================
# 4. F-143 Regression: Resume Handler Cost Limit Re-Check
# =========================================================================


class TestF143ResumeHandlerCostLimitRecheck:
    """Verify F-143 fix holds: _handle_resume_job checks cost after unpausing."""

    @pytest.mark.asyncio
    async def test_resume_cost_exceeded_job_stays_paused(self) -> None:
        """Resuming a cost-paused job should re-pause if cost still exceeded."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED, total_cost_usd=15.0),
            2: _make_sheet_state(2),
        }
        baton.register_job("j1", states, {})
        baton.set_job_cost_limit("j1", 10.0)

        job = baton._jobs["j1"]
        job.paused = True

        await baton.handle_event(ResumeJob(job_id="j1"))

        assert job.paused, (
            "F-143 regression: resume should re-pause when cost is exceeded"
        )

    @pytest.mark.asyncio
    async def test_resume_under_cost_limit_actually_unpauses(self) -> None:
        """Resume should work when cost is within limit."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED, total_cost_usd=5.0),
            2: _make_sheet_state(2),
        }
        baton.register_job("j1", states, {})
        baton.set_job_cost_limit("j1", 10.0)

        job = baton._jobs["j1"]
        job.paused = True

        await baton.handle_event(ResumeJob(job_id="j1"))

        assert not job.paused, "Job should be unpaused when cost is within limit"

    @pytest.mark.asyncio
    async def test_resume_clears_user_paused_even_when_cost_repauses(self) -> None:
        """When cost re-pauses after resume, user_paused must be False.

        Otherwise the job is stuck: user_paused=True means escalation
        resolution won't unpause it, but the user already explicitly resumed.
        """
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED, total_cost_usd=15.0),
            2: _make_sheet_state(2),
        }
        baton.register_job("j1", states, {})
        baton.set_job_cost_limit("j1", 10.0)

        job = baton._jobs["j1"]
        job.paused = True
        job.user_paused = True

        await baton.handle_event(ResumeJob(job_id="j1"))

        assert job.paused, "Job should be cost-paused"
        assert not job.user_paused, (
            "user_paused should be False after explicit resume, "
            "even if cost re-pauses the job"
        )


# =========================================================================
# 5. Dependency Propagation After Recovery
# =========================================================================


class TestRecoveryDependencyPropagation:
    """Test that dependency satisfaction works correctly after recovery."""

    def test_recovered_failed_dep_blocks_downstream(self) -> None:
        """A recovered FAILED sheet must prevent its dependents from dispatching.

        FAILED is NOT in _SATISFIED_BATON_STATUSES. This is correct behavior.
        """
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.FAILED),
            2: _make_sheet_state(2, BatonSheetStatus.PENDING),
            3: _make_sheet_state(3, BatonSheetStatus.PENDING),
        }
        deps = {2: [1], 3: [2]}

        baton.register_job("j1", states, deps)

        ready = baton.get_ready_sheets("j1")
        ready_nums = {s.sheet_num for s in ready}
        assert 2 not in ready_nums, "Sheet 2 depends on failed sheet 1"
        assert 3 not in ready_nums, "Sheet 3 transitively depends on failed sheet 1"

    def test_recovered_completed_dep_enables_downstream(self) -> None:
        """A recovered COMPLETED sheet must enable its dependents."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
            2: _make_sheet_state(2, BatonSheetStatus.PENDING),
        }
        deps = {2: [1]}

        baton.register_job("j1", states, deps)

        ready = baton.get_ready_sheets("j1")
        ready_nums = {s.sheet_num for s in ready}
        assert 2 in ready_nums, "Sheet 2 should be ready — dep 1 is completed"

    def test_recovered_skipped_dep_enables_downstream(self) -> None:
        """A recovered SKIPPED sheet must enable its dependents."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.SKIPPED),
            2: _make_sheet_state(2, BatonSheetStatus.PENDING),
        }
        deps = {2: [1]}

        baton.register_job("j1", states, deps)

        ready = baton.get_ready_sheets("j1")
        ready_nums = {s.sheet_num for s in ready}
        assert 2 in ready_nums, "Sheet 2 should be ready — dep 1 is skipped"

    def test_mixed_completed_and_failed_deps(self) -> None:
        """Sheet with multiple deps: one completed, one failed → blocked."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
            2: _make_sheet_state(2, BatonSheetStatus.FAILED),
            3: _make_sheet_state(3, BatonSheetStatus.PENDING),
        }
        deps = {3: [1, 2]}

        baton.register_job("j1", states, deps)

        ready = baton.get_ready_sheets("j1")
        ready_nums = {s.sheet_num for s in ready}
        assert 3 not in ready_nums, "Sheet 3 blocked by failed dep 2"


# =========================================================================
# 6. F-111 Rate Limit Regression
# =========================================================================


class TestF111RateLimitRegression:
    """Verify the F-111 fix in the parallel executor still holds."""

    def test_parallel_batch_result_preserves_exception_types(self) -> None:
        """ParallelBatchResult.exceptions must preserve exception types."""
        from marianne.execution.parallel import ParallelBatchResult
        from marianne.execution.runner.lifecycle import RateLimitExhaustedError

        error = RateLimitExhaustedError(
            "Rate limit exceeded",
            resume_after=time.time() + 300,
        )

        result = ParallelBatchResult(
            completed=[],
            failed=[1],
            exceptions={1: error},
        )

        assert isinstance(result.exceptions[1], RateLimitExhaustedError)
        assert result.exceptions[1].resume_after is not None

    def test_rate_limit_error_resume_after_survives_storage(self) -> None:
        """The resume_after timestamp must survive being stored and retrieved."""
        from marianne.execution.runner.lifecycle import RateLimitExhaustedError

        future_time = time.time() + 600
        error = RateLimitExhaustedError(
            "Rate limit exceeded",
            resume_after=future_time,
        )

        stored = error
        assert isinstance(stored, RateLimitExhaustedError)
        assert stored.resume_after == pytest.approx(future_time, abs=0.1)

    @pytest.mark.asyncio
    async def test_baton_rate_limit_does_not_consume_retry_budget(self) -> None:
        """A rate-limited attempt must NOT increment normal_attempts."""
        baton = BatonCore()
        states = {1: _make_sheet_state(1, max_retries=3)}
        baton.register_job("j1", states, {})

        baton._jobs["j1"].sheets[1].status = BatonSheetStatus.RUNNING
        result = _failure_result("j1", 1, rate_limited=True)
        await baton.handle_event(result)

        state = baton._jobs["j1"].sheets[1]
        assert state.normal_attempts == 0, "Rate-limited attempt should not consume retry budget"


# =========================================================================
# 7. F-113 Failure Propagation Regression (Parallel Executor)
# =========================================================================


class TestF113FailurePropagationRegression:
    """Verify F-113 fix holds: failed deps are propagated in parallel executor."""

    @staticmethod
    def _make_executor(deps: dict[int, list[int]], total: int = 5) -> Any:
        """Create a ParallelExecutor with a mocked runner and real DAG."""
        from marianne.execution.dag import DependencyDAG
        from marianne.execution.parallel import ParallelExecutor

        dag = DependencyDAG.from_dependencies(total, deps)
        runner = MagicMock()
        runner.dependency_dag = dag

        config = MagicMock()
        config.max_concurrent = 5
        config.fail_fast = False

        return ParallelExecutor(runner, config)

    def test_propagation_bfs_reaches_transitive_deps(self) -> None:
        """Failure of sheet 1 must propagate through entire dependency chain."""
        from marianne.core.checkpoint import SheetStatus

        executor = self._make_executor({2: [1], 3: [2], 4: [3]}, total=4)

        state = MagicMock()
        sheet_states: dict[int, MagicMock] = {}
        for num in [1, 2, 3, 4]:
            s = MagicMock()
            s.status = SheetStatus.PENDING
            sheet_states[num] = s
        state.sheets = sheet_states

        executor.propagate_failure_to_dependents(state, 1)

        assert state.sheets[2].status == SheetStatus.FAILED
        assert state.sheets[3].status == SheetStatus.FAILED
        assert state.sheets[4].status == SheetStatus.FAILED

    def test_propagation_does_not_overwrite_completed(self) -> None:
        """Failure propagation must not overwrite terminal states."""
        from marianne.core.checkpoint import SheetStatus

        executor = self._make_executor({2: [1], 3: [1]}, total=3)

        state = MagicMock()
        s2 = MagicMock()
        s2.status = SheetStatus.COMPLETED
        s3 = MagicMock()
        s3.status = SheetStatus.PENDING
        state.sheets = {1: MagicMock(status=SheetStatus.PENDING), 2: s2, 3: s3}

        executor.propagate_failure_to_dependents(state, 1)

        assert s2.status == SheetStatus.COMPLETED, "COMPLETED must not be overwritten"
        assert s3.status == SheetStatus.FAILED

    def test_propagation_diamond_dependency(self) -> None:
        """Diamond: A → B, A → C, B → D, C → D. Failure of A propagates to all."""
        from marianne.core.checkpoint import SheetStatus

        executor = self._make_executor({2: [1], 3: [1], 4: [2, 3]}, total=4)

        state = MagicMock()
        sheet_states: dict[int, MagicMock] = {}
        for num in [1, 2, 3, 4]:
            s = MagicMock()
            s.status = SheetStatus.PENDING
            sheet_states[num] = s
        state.sheets = sheet_states

        executor.propagate_failure_to_dependents(state, 1)

        assert state.sheets[2].status == SheetStatus.FAILED
        assert state.sheets[3].status == SheetStatus.FAILED
        assert state.sheets[4].status == SheetStatus.FAILED

    def test_propagation_with_no_dag_is_safe(self) -> None:
        """Propagation with no DAG should be a no-op, not crash."""
        from marianne.execution.parallel import ParallelExecutor

        runner = MagicMock()
        runner.dependency_dag = None
        config = MagicMock()
        config.max_concurrent = 5
        config.fail_fast = False

        executor = ParallelExecutor(runner, config)

        state = MagicMock()
        # Should not raise
        executor.propagate_failure_to_dependents(state, 1)


# =========================================================================
# 8. Double Recovery / Duplicate Registration
# =========================================================================


class TestDoubleRecovery:
    """Test what happens when the same job is recovered/registered twice."""

    def test_double_register_is_noop(self) -> None:
        """Registering the same job twice should not overwrite state."""
        baton = BatonCore()
        states_first = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
        }
        states_second = {
            1: _make_sheet_state(1, BatonSheetStatus.PENDING),
        }

        baton.register_job("j1", states_first, {})
        baton.register_job("j1", states_second, {})

        job = baton._jobs["j1"]
        assert job.sheets[1].status == BatonSheetStatus.COMPLETED

    def test_recover_after_register_is_noop(self) -> None:
        """recover_job on an already-registered job should be blocked by baton."""
        adapter = BatonAdapter(event_bus=MagicMock())

        # First: register directly
        states = {1: _make_sheet_state(1, BatonSheetStatus.COMPLETED)}
        adapter._baton.register_job("j1", states, {})

        # Second: recover_job
        sheets = [MagicMock(num=1, instrument_name="claude-code", movement=1)]
        cp = MagicMock()
        cp_sheet = MagicMock()
        cp_sheet.status = MagicMock(value="pending")
        cp_sheet.attempt_count = 0
        cp_sheet.completion_attempts = 0
        cp.sheets = {1: cp_sheet}

        adapter.recover_job("j1", sheets, {}, cp, max_retries=3)

        job = adapter._baton._jobs["j1"]
        assert job.sheets[1].status == BatonSheetStatus.COMPLETED


# =========================================================================
# 9. Terminal State Resistance During Recovery Events
# =========================================================================


class TestTerminalStateResistanceDuringRecovery:
    """Verify terminal states resist all events even in recovered jobs."""

    @pytest.mark.asyncio
    async def test_attempt_result_for_recovered_completed_sheet(self) -> None:
        """A late SheetAttemptResult for a recovered COMPLETED sheet is ignored."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
            2: _make_sheet_state(2),
        }
        baton.register_job("j1", states, {})

        result = _failure_result("j1", 1)
        await baton.handle_event(result)

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_skip_event_for_recovered_failed_sheet(self) -> None:
        """A SheetSkipped for a recovered FAILED sheet is ignored (F-049)."""
        baton = BatonCore()
        states = {1: _make_sheet_state(1, BatonSheetStatus.FAILED)}
        baton.register_job("j1", states, {})

        await baton.handle_event(SheetSkipped(job_id="j1", sheet_num=1, reason="test"))

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_process_exited_for_recovered_completed_sheet(self) -> None:
        """A ProcessExited for a recovered COMPLETED sheet is ignored."""
        baton = BatonCore()
        states = {1: _make_sheet_state(1, BatonSheetStatus.COMPLETED)}
        baton.register_job("j1", states, {})

        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, exit_code=1, pid=12345,
        ))

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_success_result_for_recovered_failed_sheet(self) -> None:
        """A late success result for a recovered FAILED sheet is ignored.

        This is the most dangerous case — a late success could "resurrect"
        a failed sheet if the terminal guard is missing.
        """
        baton = BatonCore()
        states = {1: _make_sheet_state(1, BatonSheetStatus.FAILED)}
        baton.register_job("j1", states, {})

        result = _success_result("j1", 1)
        await baton.handle_event(result)

        assert baton._jobs["j1"].sheets[1].status == BatonSheetStatus.FAILED


# =========================================================================
# 10. Completion Detection for Partially-Recovered Jobs
# =========================================================================


class TestCompletionDetectionAfterRecovery:
    """Test is_job_complete for recovered jobs."""

    def test_all_terminal_after_recovery_is_complete(self) -> None:
        """A recovered job where all sheets are terminal should be complete."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
            2: _make_sheet_state(2, BatonSheetStatus.FAILED),
            3: _make_sheet_state(3, BatonSheetStatus.SKIPPED),
        }
        baton.register_job("j1", states, {})

        assert baton.is_job_complete("j1")

    def test_mixed_terminal_and_pending_not_complete(self) -> None:
        """A recovered job with pending sheets is not complete."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
            2: _make_sheet_state(2, BatonSheetStatus.PENDING),
        }
        baton.register_job("j1", states, {})

        assert not baton.is_job_complete("j1")

    def test_all_completed_is_all_success(self) -> None:
        """All-completed job should report all_success=True."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
            2: _make_sheet_state(2, BatonSheetStatus.COMPLETED),
        }
        baton.register_job("j1", states, {})

        assert baton.is_job_complete("j1")
        job = baton._jobs["j1"]
        all_success = all(
            s.status == BatonSheetStatus.COMPLETED for s in job.sheets.values()
        )
        assert all_success

    def test_completed_with_failed_is_not_all_success(self) -> None:
        """Any FAILED sheet makes all_success=False."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED),
            2: _make_sheet_state(2, BatonSheetStatus.FAILED),
        }
        baton.register_job("j1", states, {})

        assert baton.is_job_complete("j1")
        job = baton._jobs["j1"]
        all_success = all(
            s.status == BatonSheetStatus.COMPLETED for s in job.sheets.values()
        )
        assert not all_success

    def test_unknown_job_is_not_complete(self) -> None:
        """is_job_complete for a non-existent job returns False."""
        baton = BatonCore()
        assert not baton.is_job_complete("nonexistent")


# =========================================================================
# 11. State Sync Callback
# =========================================================================


class TestStateSyncCallback:
    """Verify the state sync callback fires correctly via the adapter."""

    @pytest.mark.asyncio
    async def test_state_sync_fires_on_attempt_result(self) -> None:
        """State sync callback must fire when a sheet attempt completes."""
        sync_calls: list[tuple[str, int, str]] = []

        def sync_cb(job_id: str, sheet_num: int, status: str) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(
            event_bus=MagicMock(),
            state_sync_callback=sync_cb,
        )
        states = {1: _make_sheet_state(1)}
        adapter._baton.register_job("j1", states, {})
        adapter._baton._jobs["j1"].sheets[1].status = BatonSheetStatus.RUNNING

        result = _success_result("j1", 1)
        # Handle event through baton, then sync
        await adapter._baton.handle_event(result)
        adapter._sync_sheet_status(result)

        assert len(sync_calls) >= 1
        last_call = sync_calls[-1]
        assert last_call[0] == "j1"
        assert last_call[1] == 1
        # Should be "completed" in checkpoint terms
        assert last_call[2] == "completed"

    @pytest.mark.asyncio
    async def test_state_sync_fires_on_skip(self) -> None:
        """State sync callback must fire when a sheet is skipped."""
        sync_calls: list[tuple[str, int, str]] = []

        def sync_cb(job_id: str, sheet_num: int, status: str) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(
            event_bus=MagicMock(),
            state_sync_callback=sync_cb,
        )
        states = {1: _make_sheet_state(1)}
        adapter._baton.register_job("j1", states, {})

        skip_event = SheetSkipped(job_id="j1", sheet_num=1, reason="test")
        await adapter._baton.handle_event(skip_event)
        adapter._sync_sheet_status(skip_event)

        found = any(
            call[0] == "j1" and call[1] == 1 and call[2] == "skipped"
            for call in sync_calls
        )
        assert found, "State sync should fire for sheet skip events"

    @pytest.mark.asyncio
    async def test_state_sync_not_called_for_non_sheet_events(self) -> None:
        """Non-sheet events (PauseJob, ResumeJob) should not trigger sync."""
        sync_calls: list[tuple[str, int, str]] = []

        def sync_cb(job_id: str, sheet_num: int, status: str) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(
            event_bus=MagicMock(),
            state_sync_callback=sync_cb,
        )
        states = {1: _make_sheet_state(1)}
        adapter._baton.register_job("j1", states, {})

        pause_event = PauseJob(job_id="j1")
        await adapter._baton.handle_event(pause_event)
        adapter._sync_sheet_status(pause_event)

        assert len(sync_calls) == 0, "PauseJob should not trigger state sync"


# =========================================================================
# 12. Credential Redaction Coverage
# =========================================================================


class TestCredentialRedactionCoverage:
    """Verify credential redaction covers recovery-relevant paths."""

    def test_redact_anthropic_key(self) -> None:
        """Anthropic API keys must be redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        msg = "Auth failed with key sk-ant-api03-secret123"
        redacted = redact_credentials(msg)
        assert "sk-ant-api03-secret123" not in redacted
        assert "[REDACTED" in redacted

    def test_redact_github_pat(self) -> None:
        """GitHub personal access tokens must be redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        msg = "Using token ghp_abc123def456ghi789jkl012mno345pqr678"
        redacted = redact_credentials(msg)
        assert "ghp_abc123def456ghi789jkl012mno345pqr678" not in redacted

    def test_redact_preserves_normal_text(self) -> None:
        """Normal text without credentials must pass through unchanged."""
        from marianne.utils.credential_scanner import redact_credentials

        msg = "Sheet completed successfully with 0 validations"
        assert redact_credentials(msg) == msg

    def test_redact_handles_none(self) -> None:
        """None input should return None, not crash."""
        from marianne.utils.credential_scanner import redact_credentials

        result = redact_credentials(None)
        assert result is None


# =========================================================================
# 13. Pause → Resume → Cost Re-check Integration
# =========================================================================


class TestPauseResumeCostIntegration:
    """End-to-end test of the pause → cost-exceed → resume → re-check flow."""

    @pytest.mark.asyncio
    async def test_full_cost_pause_resume_cycle(self) -> None:
        """Simulate:
        1. Sheet runs and succeeds with high cost
        2. Job paused by cost limit
        3. User resumes
        4. Job re-paused by cost check
        """
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1),
            2: _make_sheet_state(2),
            3: _make_sheet_state(3),
        }
        baton.register_job("j1", states, {})
        baton.set_job_cost_limit("j1", 5.0)

        job = baton._jobs["j1"]

        # Step 1: Complete sheet 1 with high cost
        job.sheets[1].status = BatonSheetStatus.RUNNING
        result = _success_result("j1", 1, cost=6.0)
        await baton.handle_event(result)

        # Step 2: Job should be paused
        assert job.paused, "Job should be cost-paused after $6 cost on $5 limit"
        assert job.sheets[1].status == BatonSheetStatus.COMPLETED

        # Step 3: Paused job should return empty ready list
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 0, "No dispatches while cost-paused"

        # Step 4: User resumes
        await baton.handle_event(ResumeJob(job_id="j1"))

        # Step 5: Job should be re-paused (F-143 fix)
        assert job.paused, "Job should be re-paused by F-143 cost re-check"

    @pytest.mark.asyncio
    async def test_user_pause_then_resume_with_cost_exceeded(self) -> None:
        """User pauses → cost was already exceeded → user resumes → cost re-pauses."""
        baton = BatonCore()
        states = {
            1: _make_sheet_state(1, BatonSheetStatus.COMPLETED, total_cost_usd=15.0),
            2: _make_sheet_state(2),
        }
        baton.register_job("j1", states, {})
        baton.set_job_cost_limit("j1", 10.0)

        job = baton._jobs["j1"]

        # User pauses
        await baton.handle_event(PauseJob(job_id="j1"))
        assert job.paused
        assert job.user_paused

        # User resumes
        await baton.handle_event(ResumeJob(job_id="j1"))

        # Paused by cost, NOT user_paused
        assert job.paused
        assert not job.user_paused
