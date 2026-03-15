"""Tests for mozart.execution.runner.lifecycle module.

Covers LifecycleMixin: _finalize_summary, _get_config_summary,
_get_completed_sheets, get_summary, _get_next_sheet_dag_aware,
_initialize_state, and _should_skip_sheet.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from mozart.execution.runner.lifecycle import LifecycleMixin
from mozart.execution.runner.models import FatalError, RunSummary


# ─── Mock Runner ─────────────────────────────────────────────────────


class MockRunner(LifecycleMixin):
    """Minimal mock runner providing attributes that LifecycleMixin needs."""

    def __init__(self, config=None, backend=None, state_backend=None):
        self.config = config or _make_mock_config()
        self.backend = backend or MagicMock()
        self.state_backend = state_backend or MagicMock()
        self.console = MagicMock()
        self._logger = MagicMock()
        self._parallel_executor = None
        self._dependency_dag = None
        self._global_learning_store = None
        self._summary = None
        self._run_start_time = time.monotonic()
        self._current_state = None
        self._sheet_times = []
        self._execution_context = None
        self._shutdown_requested = False

    def _install_signal_handlers(self):
        pass

    def _remove_signal_handlers(self):
        pass

    async def _handle_graceful_shutdown(self, state):
        pass

    def _check_pause_signal(self, state):
        return False

    def _clear_pause_signal(self, state):
        pass

    async def _handle_pause_request(self, state, current_sheet):
        pass

    def _update_progress(self, state):
        pass

    async def _fire_event(self, event, sheet_num, data=None):
        pass

    async def _interruptible_sleep(self, seconds):
        await asyncio.sleep(0)

    async def _setup_isolation(self, state):
        return None

    async def _cleanup_isolation(self, state):
        pass

    async def _execute_sheet_with_recovery(self, state, sheet_num):
        pass


def _make_mock_config():
    """Create a mock JobConfig with the minimum needed attributes."""
    config = MagicMock()
    config.name = "test-job"
    config.workspace = Path("/tmp/workspace")
    config.backend.type = "claude_cli"
    config.sheet.size = 5
    config.sheet.total_items = 50
    config.sheet.total_sheets = 10
    config.sheet.skip_when = None
    config.sheet.skip_when_command = None
    config.retry.max_retries = 3
    config.retry.max_completion_attempts = 2
    config.validations = []
    config.rate_limit.wait_minutes = 15
    config.learning.enabled = True
    config.circuit_breaker.enabled = False
    config.circuit_breaker.failure_threshold = 5
    config.isolation.enabled = False
    config.isolation.mode.value = "worktree"
    config.parallel.enabled = False
    config.on_success = []
    config.concert.enabled = False
    config.model_dump.return_value = {"name": "test-job"}
    return config


def _make_state(
    *,
    total_sheets: int = 5,
    sheets: dict[int, SheetState] | None = None,
    status: JobStatus = JobStatus.RUNNING,
    rate_limit_waits: int = 0,
) -> CheckpointState:
    """Create a CheckpointState for testing."""
    state = CheckpointState(
        job_id="test-job",
        job_name="Test Job",
        total_sheets=total_sheets,
    )
    state.status = status
    state.rate_limit_waits = rate_limit_waits
    if sheets:
        state.sheets = sheets
    return state


# ─── _finalize_summary ───────────────────────────────────────────────


class TestFinalizeSummary:
    """Tests for LifecycleMixin._finalize_summary()."""

    def test_no_summary_noop(self):
        runner = MockRunner()
        runner._summary = None
        state = _make_state()
        runner._finalize_summary(state)  # Should not raise

    def test_counts_completed(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=3)
        runner._run_start_time = time.monotonic() - 10.0

        state = _make_state(total_sheets=3, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
            2: SheetState(sheet_num=2, status=SheetStatus.COMPLETED),
            3: SheetState(sheet_num=3, status=SheetStatus.FAILED),
        })

        runner._finalize_summary(state)

        assert runner._summary.completed_sheets == 2
        assert runner._summary.failed_sheets == 1

    def test_counts_skipped(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=3)
        runner._run_start_time = time.monotonic() - 5.0

        state = _make_state(total_sheets=3, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
            2: SheetState(sheet_num=2, status=SheetStatus.SKIPPED),
            3: SheetState(sheet_num=3, status=SheetStatus.COMPLETED),
        })

        runner._finalize_summary(state)

        assert runner._summary.completed_sheets == 2
        assert runner._summary.skipped_sheets == 1

    def test_counts_retries(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=2)
        runner._run_start_time = time.monotonic()

        sheet1 = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        sheet1.attempt_count = 3  # 2 retries
        sheet2 = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        sheet2.attempt_count = 1  # 0 retries

        state = _make_state(total_sheets=2, sheets={1: sheet1, 2: sheet2})
        runner._finalize_summary(state)

        assert runner._summary.total_retries == 2

    def test_success_without_retry(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=2)
        runner._run_start_time = time.monotonic()

        sheet1 = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        sheet1.success_without_retry = True
        sheet2 = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        sheet2.success_without_retry = False

        state = _make_state(total_sheets=2, sheets={1: sheet1, 2: sheet2})
        runner._finalize_summary(state)

        assert runner._summary.successes_without_retry == 1

    def test_validation_counts(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=3)
        runner._run_start_time = time.monotonic()

        sheet1 = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        sheet1.validation_passed = True
        sheet2 = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        sheet2.validation_passed = False
        sheet3 = SheetState(sheet_num=3, status=SheetStatus.COMPLETED)
        sheet3.validation_passed = True

        state = _make_state(total_sheets=3, sheets={1: sheet1, 2: sheet2, 3: sheet3})
        runner._finalize_summary(state)

        assert runner._summary.validation_pass_count == 2
        assert runner._summary.validation_fail_count == 1

    def test_rate_limit_waits_copied(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=1)
        runner._run_start_time = time.monotonic()

        state = _make_state(total_sheets=1, rate_limit_waits=5)
        runner._finalize_summary(state)

        assert runner._summary.rate_limit_waits == 5

    def test_idempotent(self):
        """Calling _finalize_summary twice gives same result."""
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=2)
        runner._run_start_time = time.monotonic()

        state = _make_state(total_sheets=2, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
            2: SheetState(sheet_num=2, status=SheetStatus.FAILED),
        })

        runner._finalize_summary(state)
        first_completed = runner._summary.completed_sheets

        runner._finalize_summary(state)  # Second call
        assert runner._summary.completed_sheets == first_completed

    def test_completion_attempts(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=1)
        runner._run_start_time = time.monotonic()

        sheet = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        sheet.completion_attempts = 3

        state = _make_state(total_sheets=1, sheets={1: sheet})
        runner._finalize_summary(state)

        assert runner._summary.total_completion_attempts == 3

    def test_sets_final_status(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=1)
        runner._run_start_time = time.monotonic()

        state = _make_state(total_sheets=1, status=JobStatus.COMPLETED)
        runner._finalize_summary(state)

        assert runner._summary.final_status == JobStatus.COMPLETED


# ─── _get_config_summary ─────────────────────────────────────────────


class TestGetConfigSummary:
    """Tests for LifecycleMixin._get_config_summary()."""

    def test_returns_dict(self):
        runner = MockRunner()
        summary = runner._get_config_summary()
        assert isinstance(summary, dict)
        assert summary["backend_type"] == "claude_cli"
        assert summary["sheet_size"] == 5
        assert summary["total_items"] == 50
        assert summary["max_retries"] == 3
        assert summary["workspace"] == "/tmp/workspace"
        assert summary["learning_enabled"] is True

    def test_isolation_disabled(self):
        runner = MockRunner()
        runner.config.isolation.enabled = False
        summary = runner._get_config_summary()
        assert summary["isolation_enabled"] is False
        assert summary["isolation_mode"] is None

    def test_isolation_enabled(self):
        runner = MockRunner()
        runner.config.isolation.enabled = True
        summary = runner._get_config_summary()
        assert summary["isolation_enabled"] is True
        assert summary["isolation_mode"] == "worktree"


# ─── get_summary ─────────────────────────────────────────────────────


class TestGetSummary:
    """Tests for LifecycleMixin.get_summary()."""

    def test_none_before_run(self):
        runner = MockRunner()
        assert runner.get_summary() is None

    def test_returns_summary_after_set(self):
        runner = MockRunner()
        runner._summary = RunSummary(job_id="test", job_name="test", total_sheets=5)
        assert runner.get_summary() is not None
        assert runner.get_summary().job_id == "test"


# ─── _get_completed_sheets ──────────────────────────────────────────


class TestGetCompletedSheets:
    """Tests for LifecycleMixin._get_completed_sheets()."""

    def test_empty_state(self):
        runner = MockRunner()
        state = _make_state()
        assert runner._get_completed_sheets(state) == set()

    def test_with_completed(self):
        runner = MockRunner()
        state = _make_state(sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
            2: SheetState(sheet_num=2, status=SheetStatus.FAILED),
            3: SheetState(sheet_num=3, status=SheetStatus.COMPLETED),
        })
        assert runner._get_completed_sheets(state) == {1, 3}


# ─── _get_next_sheet_dag_aware ───────────────────────────────────────


class TestGetNextSheetDagAware:
    """Tests for _get_next_sheet_dag_aware()."""

    def test_no_dag_uses_default(self):
        """Without DAG, delegates to state.get_next_sheet()."""
        runner = MockRunner()
        runner._dependency_dag = None
        state = _make_state(total_sheets=3)
        result = runner._get_next_sheet_dag_aware(state)
        # Default get_next_sheet returns last_completed + 1
        assert result == 1  # Starts at sheet 1

    def test_dag_ready_sheets(self):
        """With DAG, returns first ready sheet."""
        runner = MockRunner()
        dag = MagicMock()
        dag.get_ready_sheets.return_value = [2, 3]
        runner._dependency_dag = dag

        state = _make_state(total_sheets=3, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
        })
        result = runner._get_next_sheet_dag_aware(state)
        assert result == 2

    def test_dag_all_done(self):
        """All sheets completed returns None."""
        runner = MockRunner()
        dag = MagicMock()
        dag.get_ready_sheets.return_value = []
        runner._dependency_dag = dag

        state = _make_state(total_sheets=2, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
            2: SheetState(sheet_num=2, status=SheetStatus.COMPLETED),
        })
        result = runner._get_next_sheet_dag_aware(state)
        assert result is None

    def test_dag_blocked_by_failure(self):
        """Sheets blocked by failed deps are marked as skipped."""
        runner = MockRunner()
        dag = MagicMock()
        dag.get_ready_sheets.return_value = []
        runner._dependency_dag = dag

        state = _make_state(total_sheets=3, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
            2: SheetState(sheet_num=2, status=SheetStatus.FAILED),
        })
        result = runner._get_next_sheet_dag_aware(state)
        assert result is None
        # Sheet 3 should be marked as skipped
        assert state.sheets[3].status == SheetStatus.SKIPPED

    def test_dag_resume_in_progress(self):
        """In-progress sheet with satisfied deps is returned."""
        runner = MockRunner()
        dag = MagicMock()
        dag.get_dependencies.return_value = [1]
        runner._dependency_dag = dag

        state = _make_state(total_sheets=3, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED),
            2: SheetState(sheet_num=2, status=SheetStatus.IN_PROGRESS),
        })
        state.current_sheet = 2

        result = runner._get_next_sheet_dag_aware(state)
        assert result == 2

    def test_dag_resume_blocked_by_failed_dep(self):
        """In-progress sheet with failed dep is not returned."""
        runner = MockRunner()
        dag = MagicMock()
        dag.get_dependencies.return_value = [1]
        dag.get_ready_sheets.return_value = []
        runner._dependency_dag = dag

        state = _make_state(total_sheets=3, sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.FAILED),
            2: SheetState(sheet_num=2, status=SheetStatus.IN_PROGRESS),
        })
        state.current_sheet = 2

        result = runner._get_next_sheet_dag_aware(state)
        # Sheet 2 is in-progress but dep 1 failed, 3 is blocked
        assert result is None


# ─── _initialize_state ──────────────────────────────────────────────


class TestInitializeState:
    """Tests for LifecycleMixin._initialize_state()."""

    @pytest.mark.asyncio
    async def test_creates_new_state(self):
        """Creates new state when none exists."""
        runner = MockRunner()
        runner.state_backend.load = AsyncMock(return_value=None)
        runner.state_backend.save = AsyncMock()
        runner.config.model_dump.return_value = {"name": "test-job"}

        state = await runner._initialize_state(None, None)

        assert state.job_id == "test-job"
        assert state.total_sheets == 10  # from mock config
        runner.state_backend.save.assert_called()

    @pytest.mark.asyncio
    async def test_resumes_existing_state(self):
        """Returns existing state when found."""
        runner = MockRunner()
        existing = _make_state(total_sheets=5, status=JobStatus.RUNNING)
        existing.last_completed_sheet = 2
        existing.pid = None  # Not running
        runner.state_backend.load = AsyncMock(return_value=existing)

        state = await runner._initialize_state(None, None)

        assert state is existing

    @pytest.mark.asyncio
    async def test_start_sheet_override(self):
        """start_sheet overrides resume point."""
        runner = MockRunner()
        runner.state_backend.load = AsyncMock(return_value=None)
        runner.state_backend.save = AsyncMock()
        runner.config.model_dump.return_value = {"name": "test-job"}

        state = await runner._initialize_state(3, None)

        assert state.last_completed_sheet == 2  # start_sheet - 1
        # Sheets 1 and 2 should be marked as COMPLETED
        assert state.sheets[1].status == SheetStatus.COMPLETED
        assert state.sheets[2].status == SheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_zombie_detection(self):
        """Detects zombie process (dead PID) and recovers."""
        runner = MockRunner()
        existing = _make_state(total_sheets=5, status=JobStatus.RUNNING)
        existing.pid = 99999  # Very high PID, likely not running
        runner.state_backend.load = AsyncMock(return_value=existing)
        runner.state_backend.save = AsyncMock()

        with patch("os.kill", side_effect=ProcessLookupError):
            state = await runner._initialize_state(None, None)

        assert state is existing

    @pytest.mark.asyncio
    async def test_raises_if_already_running(self):
        """Raises FatalError if process is still alive."""
        runner = MockRunner()
        existing = _make_state(total_sheets=5, status=JobStatus.RUNNING)
        existing.pid = 99999
        runner.state_backend.load = AsyncMock(return_value=existing)

        # Mock os.kill to succeed (process alive) — avoids PermissionError
        # on PID 1 under non-root/WSL environments.
        with patch("os.kill", return_value=None):
            with pytest.raises(FatalError, match="already running"):
                await runner._initialize_state(None, None)


# ─── _should_skip_sheet ─────────────────────────────────────────────


class TestShouldSkipSheet:
    """Tests for _should_skip_sheet()."""

    @pytest.mark.asyncio
    async def test_no_conditions_returns_none(self):
        runner = MockRunner()
        runner.config.sheet.skip_when = None
        runner.config.sheet.skip_when_command = None
        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is None

    @pytest.mark.asyncio
    async def test_expression_true_skips(self):
        runner = MockRunner()
        runner.config.sheet.skip_when = {1: "True"}
        runner.config.sheet.skip_when_command = None
        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is not None
        assert "Condition met" in result

    @pytest.mark.asyncio
    async def test_expression_false_no_skip(self):
        runner = MockRunner()
        runner.config.sheet.skip_when = {1: "False"}
        runner.config.sheet.skip_when_command = None
        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is None

    @pytest.mark.asyncio
    async def test_expression_eval_error_runs_failopen(self):
        """Eval errors are fail-open (sheet RUNS) — Bug #116 fix."""
        runner = MockRunner()
        runner.config.sheet.skip_when = {1: "undefined_var"}
        runner.config.sheet.skip_when_command = None
        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is None  # None = don't skip = fail-open

    @pytest.mark.asyncio
    async def test_no_condition_for_sheet(self):
        """Sheet not in skip_when returns None."""
        runner = MockRunner()
        runner.config.sheet.skip_when = {2: "True"}  # Only sheet 2
        runner.config.sheet.skip_when_command = None
        state = _make_state()
        result = await runner._should_skip_sheet(1, state)  # Sheet 1
        assert result is None
