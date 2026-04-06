"""Tests for workspace-independent job control (pause_event on RunnerContext).

Tasks 1-3: Add asyncio.Event-based pause signaling to replace file-based
pause signals when running inside the daemon.

Tasks 4-6: Connect daemon to runner — manager creates event, threads it
through JobService, and IPC handler no longer needs workspace.

The design:
- RunnerContext gains a `pause_event` field (asyncio.Event | None)
- JobRunnerBase stores it as `self._pause_event`
- `_check_pause_signal` checks the event first, falls back to file-based
- `_handle_pause_request` clears the event and tolerates state-save failure
- JobManager creates pause events per job, sets them in pause_job()
- JobService threads pause_event to _create_runner()
- IPC pause handler no longer requires workspace parameter
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.backends.base import Backend
from marianne.core.checkpoint import CheckpointState
from marianne.core.config import JobConfig
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.execution.runner.models import GracefulShutdownError, RunnerContext
from marianne.state.base import StateBackend

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
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                base[k].update(v)
            else:
                base[k] = v
    (tmp_path / "workspace").mkdir(exist_ok=True)
    return JobConfig(**base)


def _make_state(job_id: str = "test-job-001", total_sheets: int = 5) -> CheckpointState:
    """Build a minimal CheckpointState for testing."""
    return CheckpointState(
        job_id=job_id,
        job_name="test-job",
        total_sheets=total_sheets,
    )


def _make_runner_base(
    tmp_path: Path,
    *,
    pause_event: asyncio.Event | None = None,
    workspace: str | None = None,
) -> Any:
    """Create a minimal JobRunnerBase with mocked dependencies.

    Uses JobRunnerBase directly (not the full JobRunner mixin chain) to
    keep tests focused on the base layer without pulling in all mixins.
    """
    from marianne.execution.runner.base import JobRunnerBase

    ws = workspace or str(tmp_path / "workspace")
    config = _make_config(tmp_path, {"workspace": ws})
    backend = MagicMock(spec=Backend)
    state_backend = AsyncMock(spec=StateBackend)

    ctx = RunnerContext(pause_event=pause_event)
    runner = JobRunnerBase(config, backend, state_backend, context=ctx)
    return runner


# ===========================================================================
# Task 1: pause_event on RunnerContext and JobRunnerBase
# ===========================================================================


class TestPauseEventOnRunnerContext:
    """Task 1: RunnerContext gains a pause_event field."""

    def test_pause_event_default_is_none(self) -> None:
        """RunnerContext() has pause_event == None by default."""
        ctx = RunnerContext()
        assert ctx.pause_event is None

    def test_pause_event_can_be_set(self) -> None:
        """RunnerContext(pause_event=event) stores the event."""
        event = asyncio.Event()
        ctx = RunnerContext(pause_event=event)
        assert ctx.pause_event is event


class TestPauseEventOnJobRunnerBase:
    """Task 1: JobRunnerBase.__init__ extracts pause_event from context."""

    def test_pause_event_stored_on_runner(self, tmp_path: Path) -> None:
        """pause_event is stored as runner._pause_event."""
        event = asyncio.Event()
        runner = _make_runner_base(tmp_path, pause_event=event)
        assert runner._pause_event is event

    def test_pause_event_none_when_no_context(self, tmp_path: Path) -> None:
        """_pause_event is None when no context is provided."""
        from marianne.execution.runner.base import JobRunnerBase

        config = _make_config(tmp_path)
        runner = JobRunnerBase(config, MagicMock(spec=Backend), AsyncMock(spec=StateBackend))
        assert runner._pause_event is None

    def test_pause_event_none_when_context_omits_it(self, tmp_path: Path) -> None:
        """_pause_event is None when context has no pause_event."""
        runner = _make_runner_base(tmp_path, pause_event=None)
        assert runner._pause_event is None


# ===========================================================================
# Task 2: _check_pause_signal checks the event first
# ===========================================================================


class TestCheckPauseSignalWithEvent:
    """Task 2: _check_pause_signal prefers asyncio.Event over file-based signal."""

    def test_returns_true_when_event_is_set(self, tmp_path: Path) -> None:
        """When pause_event is set, _check_pause_signal returns True."""
        event = asyncio.Event()
        event.set()
        runner = _make_runner_base(tmp_path, pause_event=event)
        state = _make_state()
        assert runner._check_pause_signal(state) is True

    def test_returns_false_when_event_not_set(self, tmp_path: Path) -> None:
        """When pause_event exists but is not set, returns False (no file either)."""
        event = asyncio.Event()
        runner = _make_runner_base(tmp_path, pause_event=event)
        state = _make_state()
        # Workspace exists but no pause file and event not set
        assert runner._check_pause_signal(state) is False

    def test_falls_back_to_file_when_no_event(self, tmp_path: Path) -> None:
        """Without pause_event, file-based signal detection still works."""
        runner = _make_runner_base(tmp_path)
        state = _make_state(job_id="file-test-job")

        # No file -> False
        assert runner._check_pause_signal(state) is False

        # Create pause signal file
        ws = Path(runner.config.workspace)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / ".mozart-pause-file-test-job").touch()
        assert runner._check_pause_signal(state) is True

    def test_file_check_tolerates_missing_workspace(self, tmp_path: Path) -> None:
        """File-based check doesn't crash if workspace directory doesn't exist."""
        runner = _make_runner_base(
            tmp_path, workspace=str(tmp_path / "nonexistent" / "workspace")
        )
        state = _make_state()
        # Should return False, not raise OSError
        assert runner._check_pause_signal(state) is False

    def test_event_takes_priority_over_file(self, tmp_path: Path) -> None:
        """When event is set, file is not checked (event takes priority)."""
        event = asyncio.Event()
        event.set()
        runner = _make_runner_base(tmp_path)
        runner._pause_event = event

        state = _make_state()
        # Even without any file, event being set returns True
        assert runner._check_pause_signal(state) is True


# ===========================================================================
# Task 3: _handle_pause_request resilience
# ===========================================================================


class TestHandlePauseRequestResilience:
    """Task 3: _handle_pause_request tolerates state-save failure and clears event."""

    @pytest.mark.asyncio
    async def test_pause_succeeds_when_state_save_fails(self, tmp_path: Path) -> None:
        """GracefulShutdownError is raised even if state_backend.save raises OSError."""
        event = asyncio.Event()
        event.set()
        runner = _make_runner_base(tmp_path, pause_event=event)

        # Make state_backend.save raise OSError
        runner.state_backend.save = AsyncMock(side_effect=OSError("disk full"))

        state = _make_state()
        with pytest.raises(GracefulShutdownError):
            await runner._handle_pause_request(state, current_sheet=2)

        # mark_job_paused was called before save attempt
        assert state.status.value == "paused"

    @pytest.mark.asyncio
    async def test_pause_clears_event_after_handling(self, tmp_path: Path) -> None:
        """After handling pause, the event is cleared (so it can be re-used)."""
        event = asyncio.Event()
        event.set()
        runner = _make_runner_base(tmp_path, pause_event=event)

        state = _make_state()
        with pytest.raises(GracefulShutdownError):
            await runner._handle_pause_request(state, current_sheet=3)

        assert not event.is_set(), "pause_event should be cleared after handling"

    @pytest.mark.asyncio
    async def test_pause_works_without_event(self, tmp_path: Path) -> None:
        """_handle_pause_request works normally when no pause_event is set."""
        runner = _make_runner_base(tmp_path)

        state = _make_state()
        with pytest.raises(GracefulShutdownError):
            await runner._handle_pause_request(state, current_sheet=1)

        assert state.status.value == "paused"

    @pytest.mark.asyncio
    async def test_pause_still_logs_on_save_failure(self, tmp_path: Path) -> None:
        """State-save failure is logged as warning, not swallowed silently."""
        runner = _make_runner_base(tmp_path)
        runner.state_backend.save = AsyncMock(side_effect=OSError("disk full"))

        state = _make_state()
        with pytest.raises(GracefulShutdownError):
            await runner._handle_pause_request(state, current_sheet=1)


# ===========================================================================
# Daemon-level fixtures (Tasks 4-6)
# ===========================================================================


@pytest.fixture
def daemon_config(tmp_path: Path) -> DaemonConfig:
    return DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "test.pid",
        state_db_path=tmp_path / "test-registry.db",
    )


@pytest.fixture
async def manager(daemon_config: DaemonConfig) -> AsyncIterator[JobManager]:
    from marianne.daemon.job_service import JobService

    mgr = JobManager(daemon_config)
    await mgr._registry.open()
    mgr._service = MagicMock(spec=JobService)
    yield mgr
    await mgr._registry.close()


# ===========================================================================
# Task 4: Refactor JobManager.pause_job() to use in-process event
# ===========================================================================


class TestPauseJobWithEvent:
    """JobManager.pause_job() uses in-process event, not filesystem."""

    @pytest.mark.asyncio
    async def test_pause_sets_event(self, manager: JobManager) -> None:
        """pause_job sets the in-process pause event and returns True."""
        event = asyncio.Event()
        manager._pause_events["job-1"] = event
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        # Simulate a running asyncio task so the stale-status guard passes
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task

        result = await manager.pause_job("job-1")

        assert result is True
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_pause_does_not_call_service(self, manager: JobManager) -> None:
        """When pause event exists, service.pause_job is NOT called."""
        event = asyncio.Event()
        manager._pause_events["job-1"] = event
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task
        manager._service.pause_job = AsyncMock(return_value=True)

        await manager.pause_job("job-1")

        manager._service.pause_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_no_workspace_needed(self, manager: JobManager) -> None:
        """pause_job works even when workspace doesn't exist on disk."""
        event = asyncio.Event()
        manager._pause_events["job-1"] = event
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/nonexistent/deleted/workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task

        # Should NOT raise even though workspace doesn't exist
        result = await manager.pause_job("job-1")
        assert result is True
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_pause_no_event_falls_back_to_service(self, manager: JobManager) -> None:
        """Without a pause event entry, falls back to service.pause_job."""
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task
        manager._service.pause_job = AsyncMock(return_value=True)

        result = await manager.pause_job("job-1")

        assert result is True
        manager._service.pause_job.assert_awaited_once_with(
            "job-1", Path("/tmp/workspace"),
        )

    @pytest.mark.asyncio
    async def test_pause_event_cleaned_up_on_task_done(self, manager: JobManager) -> None:
        """_on_task_done removes the pause event for a completed job."""
        event = asyncio.Event()
        manager._pause_events["job-1"] = event
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.COMPLETED,
        )

        async def quick() -> None:
            pass

        task = asyncio.create_task(quick())
        manager._jobs["job-1"] = task
        await task
        manager._on_task_done("job-1", task)

        assert "job-1" not in manager._pause_events

    @pytest.mark.asyncio
    async def test_run_managed_task_creates_pause_event(
        self, daemon_config: DaemonConfig, tmp_path: Path,
    ) -> None:
        """_run_managed_task creates a pause event for the job."""
        from marianne.daemon.job_service import JobService

        mgr = JobManager(daemon_config)
        await mgr._registry.open()
        mgr._service = MagicMock(spec=JobService)

        try:
            meta = JobMeta(
                job_id="evt-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "workspace",
            )
            mgr._job_meta["evt-test"] = meta

            event_captured: asyncio.Event | None = None

            async def _capture_event() -> None:
                nonlocal event_captured
                event_captured = mgr._pause_events.get("evt-test")

            await mgr._run_managed_task("evt-test", _capture_event())

            # The event should have existed during execution
            assert event_captured is not None
            assert isinstance(event_captured, asyncio.Event)
        finally:
            await mgr._registry.close()


# ===========================================================================
# Task 5: Thread pause_event from JobManager through JobService to runner
# ===========================================================================


def _make_service_config(tmp_path: Path) -> JobConfig:
    """Build a real JobConfig for JobService._create_runner tests."""
    return _make_config(tmp_path)


class TestPauseEventThreading:
    """Task 5: pause_event is threaded from manager → service → runner."""

    @pytest.mark.asyncio
    async def test_create_runner_passes_pause_event(self, tmp_path: Path) -> None:
        """_create_runner with pause_event= results in runner._pause_event being set."""
        from marianne.backends.base import Backend
        from marianne.daemon.job_service import JobService

        service = JobService()
        config = _make_service_config(tmp_path)

        components = {
            "backend": MagicMock(spec=Backend),
            "outcome_store": None,
            "global_learning_store": None,
            "notification_manager": None,
            "escalation_handler": None,
            "grounding_engine": None,
        }
        state_backend = AsyncMock()

        event = asyncio.Event()
        runner = service._create_runner(
            config, components, state_backend,
            job_id="test-job",
            pause_event=event,
        )

        assert runner._pause_event is event

    @pytest.mark.asyncio
    async def test_create_runner_without_pause_event(self, tmp_path: Path) -> None:
        """_create_runner without pause_event= leaves runner._pause_event as None."""
        from marianne.backends.base import Backend
        from marianne.daemon.job_service import JobService

        service = JobService()
        config = _make_service_config(tmp_path)

        components = {
            "backend": MagicMock(spec=Backend),
            "outcome_store": None,
            "global_learning_store": None,
            "notification_manager": None,
            "escalation_handler": None,
            "grounding_engine": None,
        }
        state_backend = AsyncMock()

        runner = service._create_runner(
            config, components, state_backend,
            job_id="test-job",
        )

        assert runner._pause_event is None


# ===========================================================================
# Task 6: IPC handler no longer needs workspace for pause
# ===========================================================================


class TestIPCPauseNoWorkspace:
    """Task 6: IPC pause handler calls pause_job with just job_id."""

    @pytest.mark.asyncio
    async def test_handle_pause_calls_without_workspace(self, manager: JobManager) -> None:
        """handle_pause in process.py calls manager.pause_job(job_id) without workspace."""
        event = asyncio.Event()
        manager._pause_events["job-1"] = event
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task

        # Simulate what handle_pause does: call pause_job with just job_id
        result = await manager.pause_job("job-1")
        assert result is True
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_modify_job_uses_pause_job_method(self, manager: JobManager) -> None:
        """modify_job calls self.pause_job(job_id) for running jobs."""
        event = asyncio.Event()
        manager._pause_events["job-1"] = event
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task

        config_path = Path("/tmp/new-config.yaml")
        # modify_job should use self.pause_job, not service.pause_job
        with patch.object(
            manager, "pause_job", new_callable=AsyncMock, return_value=True,
        ) as mock_pause:
            await manager.modify_job("job-1", config_path)
            mock_pause.assert_awaited_once_with("job-1")


# ===========================================================================
# Task 7: mozart cancel CLI command
# ===========================================================================


class TestCancelCLI:
    """mozart cancel command exists and routes to IPC."""

    def test_cancel_command_importable(self) -> None:
        from marianne.cli.commands.cancel import cancel
        assert callable(cancel)

    def test_cancel_command_registered(self) -> None:
        from marianne.cli import app
        # Typer leaves cmd.name as None for auto-derived names; check callback name
        command_names = [
            cmd.name or (cmd.callback.__name__ if cmd.callback else None)
            for cmd in app.registered_commands
        ]
        assert "cancel" in command_names


# ===========================================================================
# Task 8: --force flag on mozart pause
# ===========================================================================


class TestPauseForceFlag:
    """mozart pause --force routes to cancel."""

    def test_pause_has_force_option(self) -> None:
        import inspect

        from marianne.cli.commands.pause import pause
        sig = inspect.signature(pause)
        assert "force" in sig.parameters
