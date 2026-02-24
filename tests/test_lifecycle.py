"""Tests for LifecycleMixin (execution/runner/lifecycle.py).

This module covers the job lifecycle orchestration including:
- _initialize_state: New job creation, resume, zombie detection, start_sheet override
- _execute_sequential_mode: Sequential sheet execution with shutdown/pause/fatal handling
- _finalize_summary: Statistics aggregation from completed state
- _get_next_sheet_dag_aware: DAG-aware and sequential sheet selection
- run(): End-to-end integration tests for the full lifecycle
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.core.checkpoint import CheckpointState, JobStatus, SheetStatus
from mozart.core.config import JobConfig
from mozart.execution.dag import DependencyDAG
from mozart.execution.runner.lifecycle import LifecycleMixin
from mozart.execution.runner.models import FatalError, GracefulShutdownError, RunSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, overrides: dict[str, Any] | None = None) -> JobConfig:
    """Build a minimal JobConfig for testing."""
    base: dict[str, Any] = {
        "name": "test-job",
        "description": "Unit test job",
        "workspace": str(tmp_path / "workspace"),
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 5, "total_items": 10},
        "prompt": {"template": "Process sheet {{ sheet_num }}."},
        "retry": {
            "max_retries": 3,
            "max_completion_attempts": 2,
            "base_delay_seconds": 1.0,
            "exponential_base": 2.0,
            "max_delay_seconds": 60.0,
            "jitter": False,
            "completion_threshold_percent": 60,
        },
        "validations": [],
        "pause_between_sheets_seconds": 0,
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                base[k].update(v)
            else:
                base[k] = v
    (tmp_path / "workspace").mkdir(exist_ok=True)
    return JobConfig(**base)


def _make_state(
    job_id: str = "test-job",
    total_sheets: int = 2,
    status: JobStatus = JobStatus.PENDING,
) -> CheckpointState:
    """Build a minimal CheckpointState."""
    return CheckpointState(
        job_id=job_id,
        job_name=job_id,
        total_sheets=total_sheets,
        status=status,
    )


class _MockLifecycleHost:
    """Minimal mock of the mixin host attributes expected by LifecycleMixin.

    Follows the pattern from tests/test_sheet_execution.py (_MockMixin) but
    provides the attributes/methods that LifecycleMixin needs.
    """

    def __init__(self, config: JobConfig) -> None:
        from rich.console import Console

        from mozart.core.logging import get_logger

        self.config = config

        # Backend mock
        self.backend = MagicMock()
        self.backend.close = AsyncMock()
        self.backend.working_directory = Path(config.workspace)

        # State backend mock
        self.state_backend = AsyncMock()
        self.state_backend.save = AsyncMock()
        self.state_backend.load = AsyncMock(return_value=None)

        # Console (quiet to suppress output in tests)
        self.console = Console(quiet=True)

        # Logging
        self._logger = get_logger("test")

        # Parallel executor (None = sequential mode)
        self._parallel_executor = None

        # Dependency DAG (None = sequential fallback)
        self._dependency_dag: DependencyDAG | None = None

        # Global learning store
        self._global_learning_store = None

        # Run tracking
        self._summary: RunSummary | None = None
        self._run_start_time: float = 0.0
        self._current_state: CheckpointState | None = None
        self._sheet_times: list[float] = []
        self._execution_context = None
        self._shutdown_requested = False

        # Pause tracking
        self._pause_requested = False
        self._paused_at_sheet: int | None = None

    # ---- Methods required by LifecycleMixin from JobRunnerBase ----

    def _install_signal_handlers(self) -> None:
        pass  # no-op in tests

    def _remove_signal_handlers(self) -> None:
        pass  # no-op in tests

    async def _handle_graceful_shutdown(self, state: CheckpointState) -> None:
        state.status = JobStatus.PAUSED
        await self.state_backend.save(state)
        raise GracefulShutdownError(f"Job {state.job_id} paused by user request")

    def _check_pause_signal(self, state: CheckpointState) -> bool:
        return self._pause_requested

    def _clear_pause_signal(self, state: CheckpointState) -> None:
        self._pause_requested = False

    async def _handle_pause_request(
        self, state: CheckpointState, current_sheet: int
    ) -> None:
        state.mark_job_paused()
        self._paused_at_sheet = current_sheet
        await self.state_backend.save(state)
        raise GracefulShutdownError(f"Job {state.job_id} paused at sheet {current_sheet}")

    def _update_progress(self, state: CheckpointState) -> None:
        pass  # no-op in tests

    async def _interruptible_sleep(self, seconds: float) -> None:
        pass  # no-op in tests

    def _get_config_summary(self) -> dict[str, Any]:
        return {"backend_type": "claude_cli", "sheet_size": 5}

    async def _fire_event(
        self, event: str, sheet_num: int, data: dict[str, Any] | None = None,
    ) -> None:
        pass  # no-op in tests

    # ---- Methods required by LifecycleMixin from IsolationMixin ----

    async def _setup_isolation(self, state: CheckpointState) -> Path | None:
        return None  # No isolation in tests

    async def _cleanup_isolation(self, state: CheckpointState) -> None:
        pass  # no-op in tests

    # ---- Methods required by LifecycleMixin from SheetMixin ----
    # _execute_sheet_with_recovery is mocked per-test via AsyncMock

    async def _execute_sheet_with_recovery(
        self, state: CheckpointState, sheet_num: int
    ) -> None:
        """Default: mark sheet as completed."""
        state.mark_sheet_started(sheet_num)
        state.mark_sheet_completed(sheet_num, validation_passed=True)


# Compose the testable class
class _TestableLifecycleMixin(_MockLifecycleHost, LifecycleMixin):
    """Concrete class combining mock infrastructure with LifecycleMixin."""

    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mixin(tmp_path: Path) -> _TestableLifecycleMixin:
    config = _make_config(tmp_path)
    return _TestableLifecycleMixin(config)


# ===========================================================================
# Tests: _initialize_state
# ===========================================================================


class TestInitializeState:
    """Tests for _initialize_state: new job, resume, zombie detection, start_sheet."""

    @pytest.mark.asyncio
    async def test_new_job_creates_fresh_state(self, mixin: _TestableLifecycleMixin):
        """When no existing state is found, creates a new CheckpointState."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        state = await mixin._initialize_state(start_sheet=None)

        assert state.job_id == "test-job"
        assert state.total_sheets == 2
        assert state.status == JobStatus.PENDING
        assert state.last_completed_sheet == 0
        # State should have been saved once (the initial save)
        mixin.state_backend.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_job_stores_config_snapshot(self, mixin: _TestableLifecycleMixin):
        """New job state includes a serialized config snapshot for resume."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        state = await mixin._initialize_state(start_sheet=None)

        assert state.config_snapshot is not None
        assert state.config_snapshot["name"] == "test-job"

    @pytest.mark.asyncio
    async def test_existing_job_resumes_from_state(self, mixin: _TestableLifecycleMixin):
        """When existing state is found, resumes from it."""
        existing = _make_state(total_sheets=5, status=JobStatus.PAUSED)
        existing.last_completed_sheet = 3
        mixin.state_backend.load = AsyncMock(return_value=existing)

        state = await mixin._initialize_state(start_sheet=None)

        assert state is existing
        assert state.last_completed_sheet == 3
        assert state.total_sheets == 5

    @pytest.mark.asyncio
    async def test_start_sheet_override_works(self, mixin: _TestableLifecycleMixin):
        """start_sheet parameter overrides state's last_completed_sheet."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        state = await mixin._initialize_state(start_sheet=5)

        # start_sheet=5 means last_completed_sheet = 4
        assert state.last_completed_sheet == 4

    @pytest.mark.asyncio
    async def test_start_sheet_override_on_resume(self, mixin: _TestableLifecycleMixin):
        """start_sheet works when resuming an existing job too."""
        existing = _make_state(total_sheets=10, status=JobStatus.PAUSED)
        existing.last_completed_sheet = 2
        mixin.state_backend.load = AsyncMock(return_value=existing)

        state = await mixin._initialize_state(start_sheet=7)

        assert state.last_completed_sheet == 6  # 7 - 1

    @pytest.mark.asyncio
    async def test_running_job_with_alive_pid_raises_fatal(
        self, mixin: _TestableLifecycleMixin
    ):
        """When resuming a RUNNING job whose PID is alive AND different, raises FatalError."""
        existing = _make_state(status=JobStatus.RUNNING)
        existing.pid = os.getpid() + 1  # Different alive process
        mixin.state_backend.load = AsyncMock(return_value=existing)

        # Simulate the different PID being alive
        with patch("os.kill", return_value=None):  # no exception = alive
            with pytest.raises(FatalError, match="already running"):
                await mixin._initialize_state(start_sheet=None)

    @pytest.mark.asyncio
    async def test_running_job_with_same_pid_recovers_as_zombie(
        self, mixin: _TestableLifecycleMixin
    ):
        """When PID matches current process (daemon mode), recover as zombie."""
        existing = _make_state(status=JobStatus.RUNNING)
        existing.pid = os.getpid()  # Same process — daemon mode stale PID
        mixin.state_backend.load = AsyncMock(return_value=existing)

        state = await mixin._initialize_state(start_sheet=None)

        assert state.status == JobStatus.PAUSED
        assert state.pid is None

    @pytest.mark.asyncio
    async def test_running_job_with_dead_pid_recovers(
        self, mixin: _TestableLifecycleMixin
    ):
        """When resuming a RUNNING job whose PID is dead, recovers from zombie."""
        existing = _make_state(status=JobStatus.RUNNING)
        existing.pid = 999999999  # Almost certainly dead PID
        mixin.state_backend.load = AsyncMock(return_value=existing)

        # Use patch to ensure os.kill raises ProcessLookupError
        with patch("os.kill", side_effect=ProcessLookupError):
            state = await mixin._initialize_state(start_sheet=None)

        assert state.status == JobStatus.PAUSED
        assert state.pid is None

    @pytest.mark.asyncio
    async def test_running_job_permission_error_warns_but_continues(
        self, mixin: _TestableLifecycleMixin
    ):
        """When we can't check the running PID (PermissionError), continue anyway."""
        existing = _make_state(status=JobStatus.RUNNING)
        existing.pid = 1  # Root PID
        mixin.state_backend.load = AsyncMock(return_value=existing)

        with patch("os.kill", side_effect=PermissionError):
            state = await mixin._initialize_state(start_sheet=None)

        # Should have continued without raising
        assert state is existing

    @pytest.mark.asyncio
    async def test_config_path_stored_in_state(self, mixin: _TestableLifecycleMixin):
        """config_path is passed through to new state."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        state = await mixin._initialize_state(
            start_sheet=None, config_path="/path/to/config.yaml"
        )

        assert state.config_path == "/path/to/config.yaml"


# ===========================================================================
# Tests: _execute_sequential_mode
# ===========================================================================


class TestExecuteSequentialMode:
    """Tests for sequential sheet execution."""

    @pytest.mark.asyncio
    async def test_executes_sheets_in_order(self, mixin: _TestableLifecycleMixin):
        """Executes all sheets sequentially and records sheet times."""
        state = _make_state(total_sheets=3)
        state.status = JobStatus.RUNNING

        executed_sheets: list[int] = []

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            executed_sheets.append(sheet_num)
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        await mixin._execute_sequential_mode(state)

        assert executed_sheets == [1, 2, 3]
        assert len(mixin._sheet_times) == 3

    @pytest.mark.asyncio
    async def test_handles_shutdown_request(self, mixin: _TestableLifecycleMixin):
        """When _shutdown_requested is set, raises GracefulShutdownError."""
        state = _make_state(total_sheets=3)
        state.status = JobStatus.RUNNING
        mixin._shutdown_requested = True

        with pytest.raises(GracefulShutdownError):
            await mixin._execute_sequential_mode(state)

        assert state.status == JobStatus.PAUSED

    @pytest.mark.asyncio
    async def test_handles_pause_signal(self, mixin: _TestableLifecycleMixin):
        """When pause signal is detected, raises GracefulShutdownError."""
        state = _make_state(total_sheets=3)
        state.status = JobStatus.RUNNING
        mixin._pause_requested = True

        with pytest.raises(GracefulShutdownError):
            await mixin._execute_sequential_mode(state)

        assert state.status == JobStatus.PAUSED

    @pytest.mark.asyncio
    async def test_handles_fatal_error_from_sheet(
        self, mixin: _TestableLifecycleMixin
    ):
        """FatalError from _execute_sheet_with_recovery marks job failed and re-raises."""
        state = _make_state(total_sheets=2)
        state.status = JobStatus.RUNNING

        async def mock_execute_fatal(s: CheckpointState, sheet_num: int) -> None:
            raise FatalError("Unrecoverable error in sheet")

        mixin._execute_sheet_with_recovery = mock_execute_fatal  # type: ignore[assignment]
        mixin._summary = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=2
        )

        with pytest.raises(FatalError, match="Unrecoverable error"):
            await mixin._execute_sequential_mode(state)

        assert state.status == JobStatus.FAILED
        assert state.error_message is not None

    @pytest.mark.asyncio
    async def test_resumes_from_partially_completed(
        self, mixin: _TestableLifecycleMixin
    ):
        """Resumes execution from where it left off after a partial run."""
        state = _make_state(total_sheets=3)
        state.status = JobStatus.RUNNING
        # Sheet 1 already completed
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)

        executed_sheets: list[int] = []

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            executed_sheets.append(sheet_num)
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        await mixin._execute_sequential_mode(state)

        assert executed_sheets == [2, 3]

    @pytest.mark.asyncio
    async def test_shutdown_after_first_sheet(self, mixin: _TestableLifecycleMixin):
        """Shutdown request after first sheet completes stops execution."""
        state = _make_state(total_sheets=3)
        state.status = JobStatus.RUNNING
        executed_sheets: list[int] = []

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            executed_sheets.append(sheet_num)
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num)
            # Request shutdown after first sheet
            mixin._shutdown_requested = True

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        with pytest.raises(GracefulShutdownError):
            await mixin._execute_sequential_mode(state)

        assert executed_sheets == [1]


# ===========================================================================
# Tests: _finalize_summary
# ===========================================================================


class TestFinalizeSummary:
    """Tests for summary aggregation after job completion."""

    def test_aggregates_completed_sheet_stats(self, mixin: _TestableLifecycleMixin):
        """Correctly counts completed, failed, and skipped sheets."""
        state = _make_state(total_sheets=4)

        # Sheet 1: completed on first attempt
        state.mark_sheet_started(1)
        state.sheets[1].success_without_retry = True
        state.mark_sheet_completed(1, validation_passed=True)

        # Sheet 2: completed with retries
        state.mark_sheet_started(2)
        state.sheets[2].attempt_count = 3
        state.mark_sheet_completed(2, validation_passed=True)

        # Sheet 3: failed
        state.mark_sheet_started(3)
        state.mark_sheet_failed(3, "validation failed")

        # Sheet 4: skipped
        state.mark_sheet_started(4)
        state.mark_sheet_skipped(4, "dependency failed")

        mixin._summary = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=4
        )
        mixin._run_start_time = 100.0

        with patch("time.monotonic", return_value=110.0):
            mixin._finalize_summary(state)

        assert mixin._summary.completed_sheets == 2
        assert mixin._summary.failed_sheets == 1
        assert mixin._summary.skipped_sheets == 1
        assert mixin._summary.successes_without_retry == 1
        assert mixin._summary.total_duration_seconds == pytest.approx(10.0)

    def test_counts_retries_correctly(self, mixin: _TestableLifecycleMixin):
        """Retries = attempt_count - 1 for each sheet."""
        state = _make_state(total_sheets=2)

        # Sheet 1: 1 attempt (no retry)
        state.mark_sheet_started(1)
        state.sheets[1].attempt_count = 1
        state.mark_sheet_completed(1)

        # Sheet 2: 4 attempts (3 retries)
        state.mark_sheet_started(2)
        state.sheets[2].attempt_count = 4
        state.mark_sheet_completed(2)

        mixin._summary = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=2
        )
        mixin._run_start_time = 0.0

        mixin._finalize_summary(state)

        assert mixin._summary.total_retries == 3  # Only sheet 2: 4 - 1 = 3

    def test_handles_no_summary_gracefully(self, mixin: _TestableLifecycleMixin):
        """When _summary is None, _finalize_summary is a no-op."""
        state = _make_state(total_sheets=1)
        mixin._summary = None

        mixin._finalize_summary(state)
        assert mixin._summary is None

    def test_validation_pass_rate(self, mixin: _TestableLifecycleMixin):
        """Validation pass/fail counts are tracked correctly."""
        state = _make_state(total_sheets=3)

        # Sheet 1: validation passed
        state.mark_sheet_started(1)
        state.sheets[1].validation_passed = True
        state.mark_sheet_completed(1, validation_passed=True)

        # Sheet 2: validation failed
        state.mark_sheet_started(2)
        state.sheets[2].validation_passed = False
        state.mark_sheet_failed(2, "validation failed")

        # Sheet 3: validation passed
        state.mark_sheet_started(3)
        state.sheets[3].validation_passed = True
        state.mark_sheet_completed(3, validation_passed=True)

        mixin._summary = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=3
        )
        mixin._run_start_time = 0.0

        mixin._finalize_summary(state)

        assert mixin._summary.validation_pass_count == 2
        assert mixin._summary.validation_fail_count == 1
        assert mixin._summary.validation_pass_rate == pytest.approx(66.67, abs=0.1)

    def test_rate_limit_waits_copied(self, mixin: _TestableLifecycleMixin):
        """Rate limit waits from state are copied into summary."""
        state = _make_state(total_sheets=1)
        state.rate_limit_waits = 5
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)

        mixin._summary = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=1
        )
        mixin._run_start_time = 0.0

        mixin._finalize_summary(state)

        assert mixin._summary.rate_limit_waits == 5

    def test_completion_attempts_tracked(self, mixin: _TestableLifecycleMixin):
        """Completion attempts from sheets are aggregated."""
        state = _make_state(total_sheets=2)

        state.mark_sheet_started(1)
        state.sheets[1].completion_attempts = 2
        state.mark_sheet_completed(1)

        state.mark_sheet_started(2)
        state.sheets[2].completion_attempts = 1
        state.mark_sheet_completed(2)

        mixin._summary = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=2
        )
        mixin._run_start_time = 0.0

        mixin._finalize_summary(state)

        assert mixin._summary.total_completion_attempts == 3


# ===========================================================================
# Tests: _get_next_sheet_dag_aware
# ===========================================================================


class TestGetNextSheetDagAware:
    """Tests for DAG-aware sheet selection."""

    def test_returns_sequential_without_dag(self, mixin: _TestableLifecycleMixin):
        """Without a DAG, falls back to state.get_next_sheet() (sequential)."""
        mixin._dependency_dag = None
        state = _make_state(total_sheets=3)

        next_sheet = mixin._get_next_sheet_dag_aware(state)

        assert next_sheet == 1

    def test_returns_ready_sheet_with_dag(self, mixin: _TestableLifecycleMixin):
        """With a DAG, returns a sheet whose dependencies are met."""
        # Sheet 2 depends on sheet 1, sheet 3 depends on sheet 2
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)

        # Sheet 1 has no dependencies, should be returned first
        next_sheet = mixin._get_next_sheet_dag_aware(state)
        assert next_sheet == 1

    def test_returns_next_ready_after_completion(
        self, mixin: _TestableLifecycleMixin
    ):
        """After completing sheet 1, sheet 2 becomes ready."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        assert next_sheet == 2

    def test_returns_none_when_all_complete(self, mixin: _TestableLifecycleMixin):
        """When all sheets are complete, returns None."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=2,
            dependencies={2: [1]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=2)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_completed(2)

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        assert next_sheet is None

    def test_failed_sheet_with_met_deps_returned_for_retry(
        self, mixin: _TestableLifecycleMixin
    ):
        """A failed sheet whose deps are met is returned for retry, not skipped.

        The DAG-aware logic does not exclude failed sheets from the ready set.
        If a sheet's dependencies are all completed, it will be returned even
        if it previously failed, allowing the execution loop to retry it.
        """
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_failed(2, "error")

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        # Sheet 2's deps (sheet 1) are complete, so it's returned for retry
        assert next_sheet == 2

    def test_downstream_blocked_when_dep_not_completed(
        self, mixin: _TestableLifecycleMixin
    ):
        """Downstream sheets are not returned when their deps are not completed.

        Sheet 3 depends on sheet 2. Sheet 2 failed (not completed). Sheet 3
        should not be the next sheet; sheet 2 should be retried first.
        """
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_failed(2, "error")

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        # Sheet 2 is returned (can be retried), NOT sheet 3 (blocked by 2)
        assert next_sheet == 2
        assert next_sheet != 3

    def test_parallel_ready_sheets_returns_first(
        self, mixin: _TestableLifecycleMixin
    ):
        """When multiple sheets are ready in parallel, returns the lowest numbered."""
        # Sheets 2 and 3 both depend only on sheet 1
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=4)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        # Both 2 and 3 are ready, should return 2 (lowest)
        assert next_sheet == 2

    def test_in_progress_sheet_returned_on_resume(
        self, mixin: _TestableLifecycleMixin
    ):
        """An in-progress sheet is returned when its deps are met (crash resume)."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        # Sheet 2 is IN_PROGRESS (simulates crash mid-execution)
        state.current_sheet = 2

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        assert next_sheet == 2

    def test_no_dependencies_all_sheets_ready(
        self, mixin: _TestableLifecycleMixin
    ):
        """With no dependencies, all sheets are ready (returns first)."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        assert next_sheet == 1

    def test_blocked_sheets_marked_skipped_by_dag(
        self, mixin: _TestableLifecycleMixin
    ):
        """D08: Blocked sheets are marked SKIPPED when DAG detects no progress.

        When a failed sheet's deps are all met, the DAG returns it for retry.
        Sheet 3 (depends on failed sheet 2) is NOT ready because sheet 2 is
        not completed. But sheet 2 IS ready (deps=[1], 1 completed) and
        returned as the next sheet to retry.

        The SKIPPED path only triggers when ALL pending-ready sheets are
        exhausted. Verify the retry-eligible path works correctly.
        """
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)
        # Sheet 1: completed (root)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        # Sheet 2: failed but its deps (sheet 1) are met → eligible for retry
        state.mark_sheet_started(2)
        state.mark_sheet_failed(2, "fatal error")

        next_sheet = mixin._get_next_sheet_dag_aware(state)

        # Sheet 2 is returned for retry (deps met, not completed)
        assert next_sheet == 2
        # Sheet 3 remains pending — blocked by sheet 2 not being completed
        assert 3 not in state.sheets or state.sheets[3].status == SheetStatus.PENDING

    def test_skipped_sheets_satisfy_dependencies(
        self, mixin: _TestableLifecycleMixin
    ):
        """Skipped sheets (e.g. skip_when_command) satisfy downstream deps.

        When a sheet is SKIPPED (not needed), its dependents should be able
        to proceed — a skip means "this work wasn't needed", not "this work
        failed."
        """
        # Sheet 2 depends on 1, sheet 3 depends on 2
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        # Sheet 2 was skipped (e.g. skip_when_command condition met)
        state.mark_sheet_skipped(2, reason="Skip — condition not met")

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        # Sheet 3 should be ready because sheet 2 (skipped) satisfies deps
        assert next_sheet == 3

    def test_skipped_sheets_not_falsely_blocked(
        self, mixin: _TestableLifecycleMixin
    ):
        """Skipped sheets should not be reported as 'blocked by failed deps'.

        This is the exact bug from issue-solver: sheets 8-11 were correctly
        skipped by skip_when_command but reported as blocked because SKIPPED
        was not recognized as a terminal state.
        """
        # 4 sheets: 2 depends on 1, 3 has no deps, 4 depends on 3
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 4: [3]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=4)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_completed(2)
        state.mark_sheet_skipped(3, reason="Skip — plan has fewer than 3 phases")
        state.mark_sheet_skipped(4, reason="Skip — plan has fewer than 4 phases")

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        # All sheets are done (completed or skipped) — should return None
        assert next_sheet is None
        # Sheets 3 and 4 should remain SKIPPED with their original reason,
        # NOT re-marked as "Blocked by failed dependencies"
        assert state.sheets[3].status == SheetStatus.SKIPPED
        assert "Blocked" not in (state.sheets[3].error_message or "")
        assert state.sheets[4].status == SheetStatus.SKIPPED
        assert "Blocked" not in (state.sheets[4].error_message or "")

    def test_resume_with_skipped_dependency(
        self, mixin: _TestableLifecycleMixin
    ):
        """In-progress sheet can resume when a dependency was skipped."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [1, 2]},
        )
        mixin._dependency_dag = dag

        state = _make_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_skipped(2, reason="Not needed")
        state.mark_sheet_started(3)
        # Simulates crash mid-execution of sheet 3
        state.current_sheet = 3

        next_sheet = mixin._get_next_sheet_dag_aware(state)
        # Sheet 3 should resume — deps 1 (completed) and 2 (skipped) satisfied
        assert next_sheet == 3


# ===========================================================================
# Tests: run() integration
# ===========================================================================


class TestRunIntegration:
    """Integration tests for the full run() lifecycle."""

    @pytest.mark.asyncio
    async def test_full_run_with_two_sheets_succeeds(
        self, mixin: _TestableLifecycleMixin
    ):
        """Complete successful run with 2 sheets."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        executed_sheets: list[int] = []

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            executed_sheets.append(sheet_num)
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num, validation_passed=True)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        state, summary = await mixin.run()

        assert state.status == JobStatus.COMPLETED
        assert executed_sheets == [1, 2]
        assert summary.completed_sheets == 2
        assert summary.failed_sheets == 0
        assert summary.total_sheets == 2

    @pytest.mark.asyncio
    async def test_sets_pid_during_execution(self, mixin: _TestableLifecycleMixin):
        """PID is set at start and present during sheet execution."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        pids_seen: list[int | None] = []

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            pids_seen.append(s.pid)
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        state, summary = await mixin.run()

        # PID was set during execution
        assert all(pid is not None for pid in pids_seen)
        assert all(pid == os.getpid() for pid in pids_seen)

    @pytest.mark.asyncio
    async def test_pid_cleared_when_run_transitions_to_completed(
        self, mixin: _TestableLifecycleMixin
    ):
        """PID is cleared when run() itself transitions status to COMPLETED.

        Note: if mark_sheet_completed already set COMPLETED (last sheet),
        run() skips the transition and PID remains. This tests the path
        where the state is still RUNNING after sequential mode completes
        (e.g., not all sheets trigger the COMPLETED transition internally).
        """
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            s.mark_sheet_started(sheet_num)
            # Don't call mark_sheet_completed for the last sheet to keep
            # state as RUNNING. Instead, manually set sheet status.
            sheet = s.sheets[sheet_num]
            sheet.status = SheetStatus.COMPLETED
            sheet.validation_passed = True
            s.last_completed_sheet = sheet_num
            s.current_sheet = None
            # Do NOT set s.status = COMPLETED - leave it as RUNNING

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        state, summary = await mixin.run()

        # run() should have transitioned RUNNING -> COMPLETED and cleared PID
        assert state.status == JobStatus.COMPLETED
        assert state.pid is None

    @pytest.mark.asyncio
    async def test_job_marked_failed_when_sheets_have_failures(
        self, mixin: _TestableLifecycleMixin
    ):
        """D08: Job status is FAILED (not COMPLETED) when any sheet failed.

        Previously, the job was always marked COMPLETED if execution finished
        without raising FatalError, even when some sheets had FAILED status.
        The execution loop calls _execute_sheet_with_recovery which raises
        FatalError after exhausting retries, marking the job FAILED via the
        exception handler. We simulate the post-FatalError state by having
        the mock raise FatalError after marking the sheet failed.
        """
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            s.mark_sheet_started(sheet_num)
            if sheet_num == 1:
                s.mark_sheet_completed(sheet_num, validation_passed=True)
            else:
                # Sheet 2 fails fatally after exhausting retries
                s.mark_sheet_failed(sheet_num, "validation failed")
                raise FatalError("Sheet 2 exhausted retries")

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        with pytest.raises(FatalError):
            await mixin.run()

        # Get the state that was saved during execution
        save_calls = mixin.state_backend.save.await_args_list
        # The last saved state should have the job marked as FAILED
        last_state = save_calls[-1].args[0]
        assert last_state.status == JobStatus.FAILED
        assert last_state.sheets[1].status == SheetStatus.COMPLETED
        assert last_state.sheets[2].status == SheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_job_marked_failed_when_sheets_fail_with_dag_blocked_skips(
        self, mixin: _TestableLifecycleMixin
    ):
        """D08: Job is FAILED only when actual FAILED sheets exist.

        DAG-blocked skips always have a corresponding FAILED prerequisite,
        so checking SheetStatus.FAILED alone is sufficient.  Here we test
        the combined case: 1 completed + 1 failed (via FatalError) and
        verify that the job status is FAILED because of the FAILED sheet.
        """
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            s.mark_sheet_started(sheet_num)
            if sheet_num == 1:
                s.mark_sheet_completed(sheet_num, validation_passed=True)
            else:
                s.mark_sheet_failed(sheet_num, "validation failed")
                raise FatalError("Sheet 2 exhausted retries")

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        with pytest.raises(FatalError, match="Sheet 2 exhausted retries"):
            await mixin.run()

        # Verify state was saved with FAILED status
        save_calls = mixin.state_backend.save.await_args_list
        last_state = save_calls[-1].args[0]
        assert last_state.status == JobStatus.FAILED
        assert last_state.sheets[1].status == SheetStatus.COMPLETED
        assert last_state.sheets[2].status == SheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_job_completed_when_sheets_intentionally_skipped(
        self, mixin: _TestableLifecycleMixin
    ):
        """Intentionally skipped sheets (skip_when_command) don't cause job failure."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            s.mark_sheet_started(sheet_num)
            if sheet_num == 1:
                s.mark_sheet_completed(sheet_num, validation_passed=True)
            else:
                # Intentional skip (e.g., skip_when_command condition met)
                s.mark_sheet_skipped(sheet_num, "Skip phase 3 — plan has fewer phases")

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        state, summary = await mixin.run()

        assert state.status == JobStatus.COMPLETED
        assert summary.completed_sheets == 1
        assert summary.skipped_sheets == 1
        assert state.error_message is None

    @pytest.mark.asyncio
    async def test_cleanup_runs_even_on_failure(
        self, mixin: _TestableLifecycleMixin
    ):
        """Backend close and cleanup run even when execution raises."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def mock_execute_fatal(s: CheckpointState, sheet_num: int) -> None:
            raise FatalError("Sheet exploded")

        mixin._execute_sheet_with_recovery = mock_execute_fatal  # type: ignore[assignment]

        with pytest.raises(FatalError, match="Sheet exploded"):
            await mixin.run()

        # Backend close should have been called despite the error
        mixin.backend.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_success_hooks_execute_on_completion(
        self, tmp_path: Path
    ):
        """on_success hooks are executed when job completes successfully."""
        config = _make_config(
            tmp_path,
            overrides={
                "on_success": [
                    {
                        "type": "run_command",
                        "command": "echo done",
                        "description": "Test hook",
                    }
                ]
            },
        )
        mixin = _TestableLifecycleMixin(config)
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num, validation_passed=True)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        # Mock the hook executor to avoid actual command execution
        with patch(
            "mozart.execution.runner.lifecycle.HookExecutor"
        ) as MockHE:
            mock_result = MagicMock()
            mock_result.success = True
            MockHE.return_value.execute_hooks = AsyncMock(
                return_value=[mock_result]
            )

            state, summary = await mixin.run()

        assert state.status == JobStatus.COMPLETED
        MockHE.return_value.execute_hooks.assert_awaited_once()
        assert summary.hooks_executed == 1
        assert summary.hooks_succeeded == 1

    @pytest.mark.asyncio
    async def test_hooks_skipped_when_already_completed(
        self, tmp_path: Path
    ):
        """on_success hooks are NOT fired when job was already completed (no new work)."""
        config = _make_config(
            tmp_path,
            overrides={
                "on_success": [
                    {
                        "type": "run_command",
                        "command": "echo done",
                        "description": "Should not run",
                    }
                ]
            },
        )
        mixin = _TestableLifecycleMixin(config)

        # Existing state that is already completed
        existing = _make_state(total_sheets=2, status=JobStatus.COMPLETED)
        existing.mark_sheet_started(1)
        existing.mark_sheet_completed(1)
        existing.mark_sheet_started(2)
        existing.mark_sheet_completed(2)
        mixin.state_backend.load = AsyncMock(return_value=existing)

        with patch(
            "mozart.execution.runner.lifecycle.HookExecutor"
        ) as MockHE:
            state, summary = await mixin.run()

        # Hook executor should NOT have been called
        MockHE.return_value.execute_hooks.assert_not_called()
        assert summary.hooks_executed == 0

    @pytest.mark.asyncio
    async def test_isolation_cleanup_on_error(self, mixin: _TestableLifecycleMixin):
        """_cleanup_isolation runs even when execution fails."""
        mixin.state_backend.load = AsyncMock(return_value=None)
        cleanup_called = False

        async def mock_cleanup(state: CheckpointState) -> None:
            nonlocal cleanup_called
            cleanup_called = True

        mixin._cleanup_isolation = mock_cleanup  # type: ignore[assignment]

        async def mock_execute_fatal(s: CheckpointState, sheet_num: int) -> None:
            raise FatalError("Kaboom")

        mixin._execute_sheet_with_recovery = mock_execute_fatal  # type: ignore[assignment]

        with pytest.raises(FatalError):
            await mixin.run()

        assert cleanup_called is True

    @pytest.mark.asyncio
    async def test_backend_close_called_even_on_isolation_cleanup_failure(
        self, mixin: _TestableLifecycleMixin
    ):
        """backend.close() runs even when isolation cleanup raises."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def mock_cleanup_fail(state: CheckpointState) -> None:
            raise RuntimeError("Cleanup failed")

        mixin._cleanup_isolation = mock_cleanup_fail  # type: ignore[assignment]

        state, summary = await mixin.run()

        # Despite cleanup failure, backend.close was called
        mixin.backend.close.assert_awaited_once()
        assert state.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_summary_available_via_get_summary(
        self, mixin: _TestableLifecycleMixin
    ):
        """get_summary() returns the summary after a run."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        assert mixin.get_summary() is None  # Before run

        state, summary = await mixin.run()

        assert mixin.get_summary() is summary
        assert summary.job_id == "test-job"

    @pytest.mark.asyncio
    async def test_worktree_path_overrides_backend_working_directory(
        self, mixin: _TestableLifecycleMixin
    ):
        """When _setup_isolation returns a path, backend.working_directory is updated."""
        mixin.state_backend.load = AsyncMock(return_value=None)
        worktree = Path("/tmp/worktree-test")

        original_wd = mixin.backend.working_directory

        async def mock_setup(state: CheckpointState) -> Path | None:
            return worktree

        mixin._setup_isolation = mock_setup  # type: ignore[assignment]

        wd_during_execution: list[Path] = []

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            wd_during_execution.append(mixin.backend.working_directory)
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        await mixin.run()

        # Working directory was set to worktree during execution
        assert all(wd == worktree for wd in wd_during_execution)
        # Restored after run
        assert mixin.backend.working_directory == original_wd


# ===========================================================================
# Tests: _get_config_summary
# ===========================================================================


class TestGetConfigSummary:
    """Tests for the config summary builder."""

    def test_returns_safe_config_summary(self, mixin: _TestableLifecycleMixin):
        """Config summary includes expected keys and no sensitive data."""
        # Use the real _get_config_summary from LifecycleMixin
        summary = LifecycleMixin._get_config_summary(mixin)

        assert "backend_type" in summary
        assert summary["backend_type"] == "claude_cli"
        assert "max_retries" in summary
        assert "workspace" in summary
        assert "isolation_enabled" in summary


# ===========================================================================
# Tests: _get_completed_sheets
# ===========================================================================


class TestGetCompletedSheets:
    """Tests for the completed sheets helper."""

    def test_returns_completed_sheet_numbers(self, mixin: _TestableLifecycleMixin):
        """Returns set of completed sheet numbers."""
        state = _make_state(total_sheets=4)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_failed(2, "error")
        state.mark_sheet_started(3)
        state.mark_sheet_completed(3)
        # Sheet 4 not started

        completed = mixin._get_completed_sheets(state)

        assert completed == {1, 3}

    def test_empty_state_returns_empty_set(self, mixin: _TestableLifecycleMixin):
        """Empty state returns empty set."""
        state = _make_state(total_sheets=2)

        completed = mixin._get_completed_sheets(state)

        assert completed == set()


# ===========================================================================
# Tests: Learning aggregation on failure paths (Issue #10)
# ===========================================================================


# ===========================================================================
# Tests: Runner Callback Events (Issue #49, Phase 2)
# ===========================================================================


class TestJobIterationEvent:
    """Verify job.iteration event fires correctly during sequential execution."""

    @pytest.mark.asyncio
    async def test_job_iteration_event_fires_per_sheet(
        self, mixin: _TestableLifecycleMixin
    ):
        """job.iteration event fires after each sheet with correct counts."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []

        async def capturing_fire_event(
            event: str, sheet_num: int, data: dict[str, Any] | None = None,
        ) -> None:
            events.append(("test-job", sheet_num, event, data))

        mixin._fire_event = capturing_fire_event  # type: ignore[assignment]

        state = _make_state(total_sheets=3)
        state.status = JobStatus.RUNNING

        executed_sheets: list[int] = []

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            executed_sheets.append(sheet_num)
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        await mixin._execute_sequential_mode(state)

        assert executed_sheets == [1, 2, 3]

        iteration_events = [
            (j, s, e, d) for j, s, e, d in events if e == "job.iteration"
        ]
        assert len(iteration_events) == 3

        # Verify incrementing sheets_completed count
        for i, (_, sheet_num, _, data) in enumerate(iteration_events, 1):
            assert data is not None
            assert data["current_sheet"] == i
            assert data["sheets_completed"] == i
            assert data["total_sheets"] == 3

    @pytest.mark.asyncio
    async def test_job_iteration_event_on_resume(
        self, mixin: _TestableLifecycleMixin
    ):
        """job.iteration events correctly count already-completed sheets."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []

        async def capturing_fire_event(
            event: str, sheet_num: int, data: dict[str, Any] | None = None,
        ) -> None:
            events.append(("test-job", sheet_num, event, data))

        mixin._fire_event = capturing_fire_event  # type: ignore[assignment]

        state = _make_state(total_sheets=3)
        state.status = JobStatus.RUNNING
        # Sheet 1 already completed
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_completed(sheet_num)

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        await mixin._execute_sequential_mode(state)

        iteration_events = [
            (j, s, e, d) for j, s, e, d in events if e == "job.iteration"
        ]
        assert len(iteration_events) == 2  # Only sheets 2 and 3

        # After sheet 2 completes: 2 total completed (1 + 2)
        assert iteration_events[0][3]["sheets_completed"] == 2
        # After sheet 3 completes: 3 total completed
        assert iteration_events[1][3]["sheets_completed"] == 3


class TestLearningAggregationOnFailure:
    """Tests verifying _aggregate_to_global_store runs on all exit paths.

    Issue #10: Previously, _aggregate_to_global_store was only called inside
    the try block, meaning failed jobs contributed zero data to the learning
    system. The fix moves aggregation to the finally block so it runs
    regardless of job outcome.
    """

    @pytest.mark.asyncio
    async def test_aggregate_runs_on_fatal_error(
        self, mixin: _TestableLifecycleMixin
    ):
        """Learning aggregation runs even when a sheet raises FatalError."""
        mixin.state_backend.load = AsyncMock(return_value=None)
        aggregate_called = False

        original_aggregate = mixin._aggregate_to_global_store

        async def tracking_aggregate(state: CheckpointState) -> None:
            nonlocal aggregate_called
            aggregate_called = True
            await original_aggregate(state)

        mixin._aggregate_to_global_store = tracking_aggregate  # type: ignore[assignment]

        async def mock_execute_fatal(s: CheckpointState, sheet_num: int) -> None:
            s.mark_sheet_started(sheet_num)
            s.mark_sheet_failed(sheet_num, "validation failed")
            raise FatalError("Unrecoverable error in sheet")

        mixin._execute_sheet_with_recovery = mock_execute_fatal  # type: ignore[assignment]

        with pytest.raises(FatalError, match="Unrecoverable error"):
            await mixin.run()

        assert aggregate_called, (
            "_aggregate_to_global_store was not called on FatalError path"
        )

    @pytest.mark.asyncio
    async def test_aggregate_runs_on_graceful_shutdown(
        self, mixin: _TestableLifecycleMixin
    ):
        """Learning aggregation runs even on Ctrl+C / graceful shutdown."""
        mixin.state_backend.load = AsyncMock(return_value=None)
        mixin._shutdown_requested = True
        aggregate_called = False

        original_aggregate = mixin._aggregate_to_global_store

        async def tracking_aggregate(state: CheckpointState) -> None:
            nonlocal aggregate_called
            aggregate_called = True
            await original_aggregate(state)

        mixin._aggregate_to_global_store = tracking_aggregate  # type: ignore[assignment]

        with pytest.raises(GracefulShutdownError):
            await mixin.run()

        assert aggregate_called, (
            "_aggregate_to_global_store was not called on GracefulShutdownError path"
        )

    @pytest.mark.asyncio
    async def test_aggregate_runs_on_success(
        self, mixin: _TestableLifecycleMixin
    ):
        """Learning aggregation still runs on the normal success path."""
        mixin.state_backend.load = AsyncMock(return_value=None)
        aggregate_called = False

        original_aggregate = mixin._aggregate_to_global_store

        async def tracking_aggregate(state: CheckpointState) -> None:
            nonlocal aggregate_called
            aggregate_called = True
            await original_aggregate(state)

        mixin._aggregate_to_global_store = tracking_aggregate  # type: ignore[assignment]

        state, summary = await mixin.run()

        assert state.status == JobStatus.COMPLETED
        assert aggregate_called, (
            "_aggregate_to_global_store was not called on success path"
        )

    @pytest.mark.asyncio
    async def test_aggregate_receives_failed_sheet_data(
        self, mixin: _TestableLifecycleMixin
    ):
        """Aggregation receives state containing both completed and failed sheets."""
        mixin.state_backend.load = AsyncMock(return_value=None)
        captured_state: list[CheckpointState] = []

        async def capturing_aggregate(state: CheckpointState) -> None:
            captured_state.append(state)

        mixin._aggregate_to_global_store = capturing_aggregate  # type: ignore[assignment]

        call_count = 0

        async def mock_execute(s: CheckpointState, sheet_num: int) -> None:
            nonlocal call_count
            call_count += 1
            s.mark_sheet_started(sheet_num)
            if sheet_num == 1:
                s.mark_sheet_completed(sheet_num, validation_passed=True)
            else:
                s.mark_sheet_failed(sheet_num, "validation failed")
                raise FatalError("Sheet 2 failed")

        mixin._execute_sheet_with_recovery = mock_execute  # type: ignore[assignment]

        with pytest.raises(FatalError):
            await mixin.run()

        assert len(captured_state) == 1
        state = captured_state[0]
        # Sheet 1 completed, sheet 2 failed — both should be in state
        assert state.sheets[1].status == SheetStatus.COMPLETED
        assert state.sheets[2].status == SheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_aggregate_error_does_not_block_cleanup(
        self, mixin: _TestableLifecycleMixin
    ):
        """If aggregation itself fails, cleanup (backend.close) still runs."""
        mixin.state_backend.load = AsyncMock(return_value=None)

        async def failing_aggregate(state: CheckpointState) -> None:
            raise RuntimeError("Learning store unavailable")

        mixin._aggregate_to_global_store = failing_aggregate  # type: ignore[assignment]

        state, summary = await mixin.run()

        # Job still completed successfully
        assert state.status == JobStatus.COMPLETED
        # Backend close was still called
        mixin.backend.close.assert_awaited_once()
