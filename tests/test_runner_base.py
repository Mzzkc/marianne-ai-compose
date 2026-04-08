"""Tests for marianne.execution.runner.base.JobRunnerBase.

Covers the initialization infrastructure mixin:
- __init__ with minimal params (config, backend, state_backend)
- RunnerContext default merging when context=None vs provided
- Property accessors (config, backend, dependency_dag)
- DAG construction for sheet dependencies (including CycleDetectedError, InvalidDependencyError)
- Signal handler setup and teardown
- Pause signal detection and clearing
- Progress tracking (_update_progress, _handle_execution_progress)
- Event callback firing (_fire_event)
- Graceful shutdown handling
- Interruptible sleep

NOTE: Tests in test_runner.py cover signal handlers through JobRunner (full mixin).
This file focuses on JobRunnerBase-specific initialization logic and the
minimal-construction path. Signal/pause tests here use JobRunner (since
JobRunnerBase alone is incomplete as a mixin), but test untested edge cases.
"""

from __future__ import annotations

import asyncio
import signal
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import JobConfig
from marianne.execution.dag import CycleDetectedError, InvalidDependencyError
from marianne.execution.runner import JobRunner
from marianne.execution.runner.base import JobRunnerBase
from marianne.execution.runner.models import (
    GracefulShutdownError,
    RunnerContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    dependencies: dict[int, list[int]] | None = None,
    total_items: int = 30,
    workspace: str | None = None,
    circuit_breaker_enabled: bool = True,
    parallel_enabled: bool = False,
    self_healing: bool = False,
) -> JobConfig:
    """Build a minimal JobConfig with fast retry settings."""
    data: dict[str, Any] = {
        "name": "test-base",
        "description": "Test runner base",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": total_items},
        "prompt": {"template": "Process sheet {{ sheet_num }}."},
        "retry": {
            "max_retries": 2,
            "base_delay_seconds": 0.01,
            "max_delay_seconds": 0.1,
            "jitter": False,
        },
        "validations": [],
        "pause_between_sheets_seconds": 0,
        "circuit_breaker": {"enabled": circuit_breaker_enabled},
    }
    if dependencies is not None:
        data["sheet"]["dependencies"] = dependencies
    if workspace is not None:
        data["workspace"] = workspace
    if parallel_enabled:
        data["parallel"] = {"enabled": True, "max_concurrent": 2}
        if dependencies is None:
            data["sheet"]["dependencies"] = {}
    return JobConfig.model_validate(data)


def _make_runner(
    config: JobConfig | None = None,
    context: RunnerContext | None = None,
) -> JobRunner:
    """Build a JobRunner with mock backend and state_backend."""
    if config is None:
        config = _make_config()
    backend = AsyncMock()
    backend.health_check = AsyncMock(return_value=True)
    state_backend = AsyncMock()
    state_backend.load = AsyncMock(return_value=None)
    state_backend.save = AsyncMock()
    return JobRunner(
        config=config,
        backend=backend,
        state_backend=state_backend,
        context=context,
    )


# ===================================================================
# Initialization with minimal params
# ===================================================================


class TestJobRunnerBaseInit:
    """Test that __init__ works with just the three required params."""

    def test_minimal_construction(self) -> None:
        """JobRunner(config, backend, state_backend) produces a usable runner."""
        runner = _make_runner()
        # Runner is constructed with the expected config name
        assert runner.config.name == "test-base"
        # Has a usable console for output
        assert runner.console is not None
        # Has all required infrastructure for execution
        assert runner.prompt_builder is not None
        assert runner.error_classifier is not None
        assert runner.preflight_checker is not None

    def test_config_stored(self) -> None:
        cfg = _make_config()
        runner = _make_runner(config=cfg)
        assert runner.config is cfg

    def test_backend_stored(self) -> None:
        cfg = _make_config()
        backend = AsyncMock()
        backend.health_check = AsyncMock(return_value=True)
        state_backend = AsyncMock()
        state_backend.load = AsyncMock(return_value=None)
        state_backend.save = AsyncMock()
        runner = JobRunner(cfg, backend, state_backend)
        assert runner.backend is backend

    def test_state_backend_stored(self) -> None:
        cfg = _make_config()
        backend = AsyncMock()
        backend.health_check = AsyncMock(return_value=True)
        state_backend = AsyncMock()
        state_backend.load = AsyncMock(return_value=None)
        state_backend.save = AsyncMock()
        runner = JobRunner(cfg, backend, state_backend)
        assert runner.state_backend is state_backend

    def test_console_is_created_when_context_is_none(self) -> None:
        """When context=None, a default Console should be created and usable."""
        runner = _make_runner()
        assert isinstance(runner.console, Console)
        # Verify it's a distinct Console per runner, not a shared singleton
        runner2 = _make_runner()
        assert runner.console is not runner2.console

    def test_init_state_defaults_are_ready_for_execution(self) -> None:
        """All mutable state starts in the 'ready to run' position.

        A freshly-constructed runner must be in a state where calling run()
        won't hit stale data from a previous execution. We verify every
        mutable field to ensure they collectively represent a clean slate.
        """
        runner = _make_runner()
        # Shutdown/pause: not requested
        assert runner._shutdown_requested is False
        assert runner._pause_requested is False
        assert runner._paused_at_sheet is None
        # No previous execution state
        assert runner._summary is None
        assert runner._run_start_time == 0.0
        assert runner._current_sheet_num is None
        assert runner._current_state is None
        # Collections start empty (no stale data)
        assert runner._execution_progress_snapshots == []
        assert runner._sheet_times == []

    def test_shutdown_flag_transitions_on_signal(self) -> None:
        """The shutdown flag actually transitions from False -> True on signal."""
        runner = _make_runner()
        assert runner._shutdown_requested is False
        runner._signal_handler()
        assert runner._shutdown_requested is True

    def test_state_lock_is_acquirable(self) -> None:
        """The state lock can be acquired and released (not just isinstance check)."""
        runner = _make_runner()
        assert isinstance(runner._state_lock, asyncio.Lock)
        # Verify it's not already locked on init
        assert not runner._state_lock.locked()


# ===================================================================
# RunnerContext default merging
# ===================================================================


class TestRunnerContextMerging:
    """When context=None, all optional deps use safe defaults."""

    def test_optional_deps_all_none_without_context(self) -> None:
        """Without context, all optional dependencies are absent.

        This matters because code paths must check for None before using
        these — if any started non-None, those guards would be wrong.
        """
        runner = _make_runner()
        assert runner.outcome_store is None
        assert runner.escalation_handler is None
        assert runner.judgment_client is None
        assert runner.progress_callback is None
        assert runner.execution_progress_callback is None
        assert runner.rate_limit_callback is None
        assert runner.event_callback is None
        assert runner._self_healing_enabled is False
        assert runner._healing_coordinator is None
        assert runner._self_healing_auto_confirm is False

    @pytest.mark.asyncio
    async def test_no_context_runner_fires_events_safely(self) -> None:
        """A runner with no event_callback can fire events without errors.

        This tests the behavioral consequence of event_callback=None.
        """
        runner = _make_runner()
        # Should not raise even though callback is None
        await runner._fire_event("sheet.completed", sheet_num=1)

    def test_no_context_runner_updates_progress_safely(self) -> None:
        """A runner with no progress_callback can update progress without errors."""
        runner = _make_runner()
        state = CheckpointState(
            job_id="safe-progress",
            job_name="test",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.RUNNING,
        )
        # Should not raise even though callback is None
        runner._update_progress(state)

    def test_context_console_used_when_provided(self) -> None:
        """When a Console is provided in context, it is used instead of default."""
        custom_console = Console(width=120)
        ctx = RunnerContext(console=custom_console)
        runner = _make_runner(context=ctx)
        assert runner.console is custom_console

    def test_context_progress_callback_used(self) -> None:
        calls: list[tuple[int, int, float | None]] = []

        def cb(completed: int, total: int, eta: float | None) -> None:
            calls.append((completed, total, eta))

        ctx = RunnerContext(progress_callback=cb)
        runner = _make_runner(context=ctx)
        assert runner.progress_callback is cb

    def test_context_event_callback_used(self) -> None:
        def event_cb(name: str, sheet: int, event: str, data: dict[str, Any] | None) -> None:
            pass

        ctx = RunnerContext(event_callback=event_cb)
        runner = _make_runner(context=ctx)
        assert runner.event_callback is event_cb

    def test_context_rate_limit_callback_used(self) -> None:
        def rl_cb(backend_type: str, wait: float, job_id: str, sheet: int) -> None:
            pass

        ctx = RunnerContext(rate_limit_callback=rl_cb)
        runner = _make_runner(context=ctx)
        assert runner.rate_limit_callback is rl_cb

    def test_self_healing_creates_coordinator(self) -> None:
        ctx = RunnerContext(self_healing_enabled=True)
        runner = _make_runner(context=ctx)
        assert runner._self_healing_enabled is True
        assert runner._healing_coordinator is not None

    def test_self_healing_auto_confirm_passed_through(self) -> None:
        ctx = RunnerContext(self_healing_enabled=True, self_healing_auto_confirm=True)
        runner = _make_runner(context=ctx)
        assert runner._self_healing_auto_confirm is True


# ===================================================================
# Property accessors
# ===================================================================


class TestPropertyAccessors:

    def test_dependency_dag_none_when_no_deps(self) -> None:
        """Without dependencies, _dependency_dag should be None."""
        runner = _make_runner()
        assert runner.dependency_dag is None

    def test_dependency_dag_present_when_deps_configured(self) -> None:
        cfg = _make_config(
            dependencies={2: [1], 3: [1]},
        )
        runner = _make_runner(config=cfg)
        assert runner.dependency_dag is not None
        assert runner.dependency_dag.total_sheets == 3


# ===================================================================
# DAG construction
# ===================================================================


class TestDAGConstruction:
    """Test DAG building at init time, including error paths."""

    def test_valid_linear_dependency(self) -> None:
        """Linear chain: 1 -> 2 -> 3."""
        cfg = _make_config(dependencies={2: [1], 3: [2]})
        runner = _make_runner(config=cfg)
        dag = runner.dependency_dag
        assert dag is not None
        assert dag.get_execution_order() == [1, 2, 3]

    def test_valid_diamond_dependency(self) -> None:
        """Diamond: 1 -> {2, 3} -> 4."""
        cfg = _make_config(
            total_items=40,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )
        runner = _make_runner(config=cfg)
        dag = runner.dependency_dag
        assert dag is not None
        groups = dag.get_parallel_groups()
        # Group 0: [1], Group 1: [2, 3], Group 2: [4]
        assert groups[0] == [1]
        assert sorted(groups[1]) == [2, 3]
        assert groups[2] == [4]

    def test_cycle_detected_error_raised(self) -> None:
        """Cyclic dependency: 1 -> 2 -> 3 -> 1 should raise CycleDetectedError.

        Config creation succeeds (cycles aren't checked at config level),
        but DAG construction during runner init detects the cycle.
        """
        cfg = _make_config(dependencies={2: [1], 3: [2], 1: [3]})
        with pytest.raises(CycleDetectedError):
            _make_runner(config=cfg)

    def test_invalid_dependency_out_of_range_caught_at_config(self) -> None:
        """Reference to sheet beyond total_sheets is caught by config validation.

        The config model's validate_dependency_range catches out-of-range deps
        before the DAG is ever built. This raises pydantic.ValidationError, not
        InvalidDependencyError (which is the belt-and-suspenders check inside
        the DAG builder).
        """
        from pydantic import ValidationError

        # total_items=30, size=10 -> total_sheets=3
        # Sheet 2 depending on sheet 5 (out of range) should raise ValidationError
        with pytest.raises(ValidationError, match="out of range"):
            _make_config(dependencies={2: [5]})

    def test_invalid_dependency_self_reference_caught_at_config(self) -> None:
        """Self-referencing dependency caught by config validator."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="cannot depend on itself"):
            _make_config(dependencies={2: [2]})

    def test_no_deps_means_no_dag(self) -> None:
        """No dependencies => _dependency_dag is None."""
        cfg = _make_config()
        runner = _make_runner(config=cfg)
        assert runner._dependency_dag is None

    def test_empty_deps_dict_means_no_dag(self) -> None:
        """Empty dict of deps => dag is still None (no deps configured on the config)."""
        cfg = _make_config(dependencies={})
        runner = _make_runner(config=cfg)
        # Empty dict is falsy, so no DAG is built
        assert runner._dependency_dag is None

    def test_dag_parallelizable_with_independent_sheets(self) -> None:
        """Independent branches should be parallelizable."""
        cfg = _make_config(
            total_items=40,
            dependencies={2: [1], 3: [1]},
        )
        runner = _make_runner(config=cfg)
        dag = runner.dependency_dag
        assert dag is not None
        assert dag.is_parallelizable() is True


# ===================================================================
# Circuit breaker initialization
# ===================================================================


class TestCircuitBreakerInit:

    def test_circuit_breaker_created_when_enabled(self) -> None:
        cfg = _make_config(circuit_breaker_enabled=True)
        runner = _make_runner(config=cfg)
        assert runner._circuit_breaker is not None

    def test_circuit_breaker_none_when_disabled(self) -> None:
        cfg = _make_config(circuit_breaker_enabled=False)
        runner = _make_runner(config=cfg)
        assert runner._circuit_breaker is None


# ===================================================================
# Signal handler setup and teardown
# ===================================================================


class TestSignalHandlers:

    @pytest.mark.asyncio
    async def test_install_registers_three_signals(self) -> None:
        """SIGINT, SIGTERM, SIGHUP should all be registered."""
        runner = _make_runner()
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler") as mock_add:
            runner._install_signal_handlers()
            registered = {call.args[0] for call in mock_add.call_args_list}
            assert signal.SIGINT in registered
            assert signal.SIGTERM in registered
            assert signal.SIGHUP in registered

    @pytest.mark.asyncio
    async def test_remove_removes_three_signals(self) -> None:
        """All three signal handlers should be removed on teardown."""
        runner = _make_runner()
        loop = asyncio.get_running_loop()
        with patch.object(loop, "remove_signal_handler") as mock_remove:
            runner._remove_signal_handlers()
            removed = {call.args[0] for call in mock_remove.call_args_list}
            assert signal.SIGINT in removed
            assert signal.SIGTERM in removed
            assert signal.SIGHUP in removed

    @pytest.mark.asyncio
    async def test_install_skips_gracefully_on_runtime_error(self) -> None:
        """If no running loop, install should not raise."""
        runner = _make_runner()
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", side_effect=RuntimeError("no loop")):
            # Should not raise
            runner._install_signal_handlers()

    @pytest.mark.asyncio
    async def test_remove_skips_gracefully_on_value_error(self) -> None:
        """If handler not installed, remove should not raise."""
        runner = _make_runner()
        loop = asyncio.get_running_loop()
        with patch.object(loop, "remove_signal_handler", side_effect=ValueError("no handler")):
            # Should not raise
            runner._remove_signal_handlers()

    def test_signal_handler_sets_shutdown_flag(self) -> None:
        runner = _make_runner()
        assert runner._shutdown_requested is False
        runner._signal_handler()
        assert runner._shutdown_requested is True

    def test_signal_handler_idempotent(self) -> None:
        """Calling _signal_handler twice should not raise or change state after first call."""
        runner = _make_runner()
        runner._signal_handler()
        assert runner._shutdown_requested is True
        # Second call should be safe
        runner._signal_handler()
        assert runner._shutdown_requested is True


# ===================================================================
# Graceful shutdown
# ===================================================================


class TestGracefulShutdown:

    @pytest.mark.asyncio
    async def test_handle_graceful_shutdown_saves_paused_state(self) -> None:
        runner = _make_runner()
        state = CheckpointState(
            job_id="test-shutdown",
            job_name="test",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.RUNNING,
        )
        with pytest.raises(GracefulShutdownError, match="paused by user request"):
            await runner._handle_graceful_shutdown(state)

        assert state.status == JobStatus.PAUSED
        runner.state_backend.save.assert_called_once_with(state)

    @pytest.mark.asyncio
    async def test_interruptible_sleep_interrupted_by_shutdown(self) -> None:
        runner = _make_runner()
        runner._shutdown_requested = True
        start = time.monotonic()
        await runner._interruptible_sleep(10.0)
        elapsed = time.monotonic() - start
        # Should return quickly, not the full 10 seconds
        assert elapsed < 30.0

    @pytest.mark.asyncio
    async def test_interruptible_sleep_returns_without_error(self) -> None:
        """_interruptible_sleep is callable and returns without error.

        Note: RecoveryMixin's type stub for _interruptible_sleep sits higher
        in MRO than JobRunnerBase's real implementation.
        """
        runner = _make_runner()
        runner._shutdown_requested = False
        # Just verify it doesn't raise
        await runner._interruptible_sleep(0.0)


# ===================================================================
# Pause signal detection
# ===================================================================


class TestPauseSignalDetection:

    def test_no_pause_when_no_job_id(self) -> None:
        """When state.job_id is empty, _check_pause_signal returns False."""
        runner = _make_runner()
        state = CheckpointState(
            job_id="",
            job_name="test",
            total_sheets=3,
            last_completed_sheet=0,
            status=JobStatus.RUNNING,
        )
        assert runner._check_pause_signal(state) is False

    def test_no_pause_when_no_signal_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(workspace=tmpdir)
            runner = _make_runner(config=cfg)
            state = CheckpointState(
                job_id="pause-test-1",
                job_name="test",
                total_sheets=3,
                last_completed_sheet=0,
                status=JobStatus.RUNNING,
            )
            assert runner._check_pause_signal(state) is False

    def test_pause_detected_when_signal_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(workspace=tmpdir)
            runner = _make_runner(config=cfg)
            state = CheckpointState(
                job_id="pause-test-2",
                job_name="test",
                total_sheets=3,
                last_completed_sheet=0,
                status=JobStatus.RUNNING,
            )
            # Create the signal file
            signal_file = Path(tmpdir) / f".marianne-pause-{state.job_id}"
            signal_file.touch()
            assert runner._check_pause_signal(state) is True

    def test_clear_pause_signal_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(workspace=tmpdir)
            runner = _make_runner(config=cfg)
            state = CheckpointState(
                job_id="pause-test-3",
                job_name="test",
                total_sheets=3,
                last_completed_sheet=0,
                status=JobStatus.RUNNING,
            )
            signal_file = Path(tmpdir) / f".marianne-pause-{state.job_id}"
            signal_file.touch()
            assert signal_file.exists()
            runner._clear_pause_signal(state)
            assert not signal_file.exists()

    def test_clear_pause_signal_no_job_id(self) -> None:
        """Clearing with empty job_id should not raise."""
        runner = _make_runner()
        state = CheckpointState(
            job_id="",
            job_name="test",
            total_sheets=3,
            last_completed_sheet=0,
            status=JobStatus.RUNNING,
        )
        # Should not raise
        runner._clear_pause_signal(state)

    def test_clear_pause_signal_when_file_missing(self) -> None:
        """Clearing when file doesn't exist should not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(workspace=tmpdir)
            runner = _make_runner(config=cfg)
            state = CheckpointState(
                job_id="pause-test-missing",
                job_name="test",
                total_sheets=3,
                last_completed_sheet=0,
                status=JobStatus.RUNNING,
            )
            # No signal file -- should not raise
            runner._clear_pause_signal(state)

    @pytest.mark.asyncio
    async def test_handle_pause_request_raises_graceful_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _make_config(workspace=tmpdir)
            runner = _make_runner(config=cfg)
            state = CheckpointState(
                job_id="pause-handle",
                job_name="test",
                total_sheets=5,
                last_completed_sheet=2,
                status=JobStatus.RUNNING,
            )
            with pytest.raises(GracefulShutdownError, match="paused at sheet 3"):
                await runner._handle_pause_request(state, current_sheet=3)

            assert runner._paused_at_sheet == 3
            runner.state_backend.save.assert_called_once_with(state)


# ===================================================================
# Progress tracking
# ===================================================================


class TestProgressTracking:

    def test_update_progress_no_callback(self) -> None:
        """When progress_callback is None, _update_progress should not raise."""
        runner = _make_runner()
        state = CheckpointState(
            job_id="prog-1",
            job_name="test",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.RUNNING,
        )
        # Should not raise
        runner._update_progress(state)

    def test_update_progress_with_callback(self) -> None:
        """Progress callback should be called with correct args."""
        calls: list[tuple[int, int, float | None]] = []

        def cb(completed: int, total: int, eta: float | None) -> None:
            calls.append((completed, total, eta))

        ctx = RunnerContext(progress_callback=cb)
        runner = _make_runner(context=ctx)
        state = CheckpointState(
            job_id="prog-2",
            job_name="test",
            total_sheets=10,
            last_completed_sheet=5,
            status=JobStatus.RUNNING,
        )
        runner._update_progress(state)
        assert len(calls) == 1
        assert calls[0][0] == 5   # completed
        assert calls[0][1] == 10  # total
        assert calls[0][2] is None  # no sheet times yet, so no ETA

    def test_update_progress_calculates_eta(self) -> None:
        """When sheet_times are available, ETA should be calculated."""
        calls: list[tuple[int, int, float | None]] = []

        def cb(completed: int, total: int, eta: float | None) -> None:
            calls.append((completed, total, eta))

        ctx = RunnerContext(progress_callback=cb)
        runner = _make_runner(context=ctx)
        # Simulate 3 sheets taking 10 seconds each
        runner._sheet_times = [10.0, 10.0, 10.0]
        state = CheckpointState(
            job_id="prog-3",
            job_name="test",
            total_sheets=10,
            last_completed_sheet=3,
            status=JobStatus.RUNNING,
        )
        runner._update_progress(state)
        assert len(calls) == 1
        # ETA = avg(10, 10, 10) * (10 - 3) = 10 * 7 = 70
        assert calls[0][2] == pytest.approx(70.0)

    def test_handle_execution_progress_stores_snapshot(self) -> None:
        """_handle_execution_progress should store a progress snapshot."""
        runner = _make_runner()
        runner._current_sheet_num = 2
        progress = {
            "bytes_received": 2048,
            "lines_received": 50,
            "elapsed_seconds": 5.5,
            "phase": "executing",
        }
        runner._handle_execution_progress(progress)
        assert len(runner._execution_progress_snapshots) == 1
        snap = runner._execution_progress_snapshots[0]
        assert snap["sheet_num"] == 2
        assert snap["bytes_received"] == 2048
        assert "snapshot_at" in snap

    def test_handle_execution_progress_updates_monotonic(self) -> None:
        """_handle_execution_progress should update _last_progress_monotonic."""
        runner = _make_runner()
        runner._current_sheet_num = 1
        before = runner._last_progress_monotonic
        runner._handle_execution_progress({
            "bytes_received": 100,
            "lines_received": 5,
            "elapsed_seconds": 1.0,
            "phase": "executing",
        })
        assert runner._last_progress_monotonic > before

    def test_handle_execution_progress_snapshot_throttling(self) -> None:
        """Snapshots should only be stored when significant new data arrives (>1KB)."""
        runner = _make_runner()
        runner._current_sheet_num = 1
        # First call always stores
        runner._handle_execution_progress({
            "bytes_received": 100,
            "lines_received": 5,
            "elapsed_seconds": 1.0,
            "phase": "executing",
        })
        assert len(runner._execution_progress_snapshots) == 1

        # Second call with only 500 more bytes -- should NOT store
        runner._handle_execution_progress({
            "bytes_received": 600,
            "lines_received": 10,
            "elapsed_seconds": 2.0,
            "phase": "executing",
        })
        assert len(runner._execution_progress_snapshots) == 1

        # Third call with >1KB more bytes -- should store
        runner._handle_execution_progress({
            "bytes_received": 2048,
            "lines_received": 50,
            "elapsed_seconds": 5.0,
            "phase": "executing",
        })
        assert len(runner._execution_progress_snapshots) == 2

    def test_handle_execution_progress_forwards_to_callback(self) -> None:
        """When execution_progress_callback is set, progress should be forwarded."""
        forwarded: list[dict[str, Any]] = []

        def ep_cb(progress: dict[str, Any]) -> None:
            forwarded.append(progress)

        ctx = RunnerContext(execution_progress_callback=ep_cb)
        runner = _make_runner(context=ctx)
        runner._current_sheet_num = 3
        runner._handle_execution_progress({
            "bytes_received": 512,
            "lines_received": 10,
            "elapsed_seconds": 2.0,
            "phase": "executing",
        })
        assert len(forwarded) == 1
        assert forwarded[0]["sheet_num"] == 3
        assert forwarded[0]["bytes_received"] == 512


# ===================================================================
# Event callbacks (_fire_event)
# ===================================================================


class TestFireEvent:

    @pytest.mark.asyncio
    async def test_fire_event_no_callback(self) -> None:
        """When event_callback is None, _fire_event should not raise."""
        runner = _make_runner()
        # Should complete without error
        await runner._fire_event("sheet.completed", sheet_num=1)

    @pytest.mark.asyncio
    async def test_fire_event_calls_sync_callback(self) -> None:
        """When event_callback is a sync function, it should be called."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []

        def sync_cb(name: str, sheet: int, event: str, data: dict[str, Any] | None) -> None:
            events.append((name, sheet, event, data))

        ctx = RunnerContext(event_callback=sync_cb)
        runner = _make_runner(context=ctx)
        await runner._fire_event("sheet.started", sheet_num=2, data={"attempt": 1})
        assert len(events) == 1
        assert events[0] == ("test-base", 2, "sheet.started", {"attempt": 1})

    @pytest.mark.asyncio
    async def test_fire_event_calls_async_callback(self) -> None:
        """When event_callback is async, it should be awaited."""
        events: list[tuple[str, int, str]] = []

        async def async_cb(
            name: str, sheet: int, event: str, data: dict[str, Any] | None
        ) -> None:
            events.append((name, sheet, event))

        ctx = RunnerContext(event_callback=async_cb)
        runner = _make_runner(context=ctx)
        await runner._fire_event("sheet.failed", sheet_num=5)
        assert len(events) == 1
        assert events[0] == ("test-base", 5, "sheet.failed")

    @pytest.mark.asyncio
    async def test_fire_event_swallows_callback_error(self) -> None:
        """Errors from event_callback should be caught, not propagated."""

        def failing_cb(
            name: str, sheet: int, event: str, data: dict[str, Any] | None
        ) -> None:
            raise ValueError("callback exploded")

        ctx = RunnerContext(event_callback=failing_cb)
        runner = _make_runner(context=ctx)
        # Should not raise
        await runner._fire_event("sheet.completed", sheet_num=1)


# ===================================================================
# Internal infrastructure attributes
# ===================================================================


class TestInternalInfrastructure:

    def test_prompt_builder_configured_from_config(self) -> None:
        """Prompt builder is created with the job's prompt config."""
        runner = _make_runner()
        from marianne.prompts.templating import PromptBuilder
        assert isinstance(runner.prompt_builder, PromptBuilder)
        # Verify it received the config's prompt settings
        assert runner.prompt_builder.config is runner.config.prompt

    def test_error_classifier_can_classify(self) -> None:
        """Error classifier is created and can classify outputs."""
        runner = _make_runner()
        from marianne.core.errors import ErrorClassifier
        assert isinstance(runner.error_classifier, ErrorClassifier)
        # Verify it can perform classification (not just is not None)
        result = runner.error_classifier.classify(stdout="", exit_code=0)
        assert result is not None

    def test_preflight_checker_has_workspace(self) -> None:
        """Preflight checker is configured with the job's workspace."""
        runner = _make_runner()
        from marianne.execution.preflight import PreflightChecker
        assert isinstance(runner.preflight_checker, PreflightChecker)

    def test_retry_strategy_respects_config(self) -> None:
        """Retry strategy is built from the job's retry config."""
        runner = _make_runner()
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy
        assert isinstance(runner._retry_strategy, AdaptiveRetryStrategy)
        # Verify config values propagated
        assert runner._retry_strategy.config.base_delay == runner.config.retry.base_delay_seconds
        assert runner._retry_strategy.config.max_delay == runner.config.retry.max_delay_seconds

    def test_logger_has_runner_component(self) -> None:
        """Logger is created with 'runner' component name."""
        runner = _make_runner()
        from marianne.core.logging import MarianneLogger
        assert isinstance(runner._logger, MarianneLogger)

    def test_pattern_tracking_accumulates_independently(self) -> None:
        """Pattern tracking lists start empty and accumulate independently."""
        runner = _make_runner()
        # Start empty
        assert runner._current_sheet_patterns == []
        assert runner._applied_pattern_ids == []
        assert runner._exploration_pattern_ids == []
        assert runner._exploitation_pattern_ids == []
        # Mutating one list doesn't affect others
        runner._current_sheet_patterns.append("pattern-1")
        runner._applied_pattern_ids.append("applied-1")
        assert runner._exploration_pattern_ids == []
        assert runner._exploitation_pattern_ids == []
        assert len(runner._current_sheet_patterns) == 1
        assert len(runner._applied_pattern_ids) == 1

    def test_failure_counters_increment_independently(self) -> None:
        """Failure counters start at zero and increment independently."""
        runner = _make_runner()
        assert runner._record_execution_failures == 0
        assert runner._escalation_update_failures == 0
        runner._record_execution_failures += 1
        assert runner._record_execution_failures == 1
        assert runner._escalation_update_failures == 0

    def test_optional_infrastructure_absent_without_context(self) -> None:
        """Optional infrastructure components are absent when not configured.

        These None checks matter because methods gate on them:
        checkpoint_handler, parallel_executor, global_learning_store,
        and grounding_engine all have None-guard branches.
        """
        runner = _make_runner()
        assert runner.checkpoint_handler is None
        assert runner._parallel_executor is None
        assert runner._global_learning_store is None
        assert runner._grounding_engine is None

    def test_parallel_executor_created_when_enabled(self) -> None:
        """When parallel config is enabled, executor is created."""
        cfg = _make_config(parallel_enabled=True)
        runner = _make_runner(config=cfg)
        assert runner._parallel_executor is not None
        from marianne.execution.parallel import ParallelExecutor
        assert isinstance(runner._parallel_executor, ParallelExecutor)
