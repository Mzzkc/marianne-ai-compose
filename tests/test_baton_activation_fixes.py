"""Tests for baton activation fixes — F-152, F-145, F-158.

F-152 (P0): Dispatch-time guard for unsupported instruments.
    When _dispatch_callback fails to acquire a backend, it must post a failure
    SheetAttemptResult to the baton inbox so the retry/exhaustion logic kicks
    in. Without this, the sheet stays stuck in DISPATCHED forever.

F-145 (P2): completed_new_work flag in baton paths.
    _run_via_baton and _resume_via_baton must set meta.completed_new_work
    after successful completion. Without this, concert chaining's zero-work
    guard always sees False and may abort valid chains.

F-158 (P1): Wire PromptConfig into register_job().
    _run_via_baton must construct and pass prompt_config so the PromptRenderer
    is created. Without this, baton musicians get raw templates instead of the
    full 9-layer rendering pipeline.

TDD: Tests written first (red), then implementation (green).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import BatonSheetStatus


# =========================================================================
# Fixtures
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    workspace: str = "/tmp/test-ws",
    prompt: str = "test prompt",
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=1,
        voice_count=1,
        instrument_name=instrument,
        workspace=Path(workspace),
        prompt_template=prompt,
    )


# =========================================================================
# F-152: Dispatch-time guard for unsupported instruments
# =========================================================================


class TestF152DispatchGuard:
    """When backend acquire fails, _dispatch_callback must post a failure
    result to the baton inbox so the sheet doesn't get stuck in DISPATCHED."""

    @pytest.mark.asyncio
    async def test_backend_acquire_failure_posts_attempt_result(self) -> None:
        """When BackendPool.acquire raises, a failure SheetAttemptResult
        should be posted to the baton inbox."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()

        mock_pool = MagicMock()
        mock_pool.acquire = AsyncMock(
            side_effect=ValueError("Unsupported instrument kind: http")
        )
        adapter.set_backend_pool(mock_pool)

        sheet = _make_sheet(num=1, instrument="bad-instrument")
        adapter.register_job("j1", [sheet], {1: []})

        job = adapter.baton._jobs["j1"]
        state = job.sheets[1]

        # Drain the DispatchRetry event from register_job
        while not adapter.baton.inbox.empty():
            adapter.baton.inbox.get_nowait()

        await adapter._dispatch_callback("j1", 1, state)

        assert not adapter.baton.inbox.empty()
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.job_id == "j1"
        assert event.sheet_num == 1
        assert event.execution_success is False
        assert event.instrument_name == "bad-instrument"
        assert event.error_message is not None

    @pytest.mark.asyncio
    async def test_backend_acquire_runtime_error_posts_attempt_result(self) -> None:
        """RuntimeError from acquire also triggers the guard."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()

        mock_pool = MagicMock()
        mock_pool.acquire = AsyncMock(
            side_effect=RuntimeError("Backend pool exhausted")
        )
        adapter.set_backend_pool(mock_pool)

        sheet = _make_sheet(num=3, instrument="exhausted-backend")
        adapter.register_job("j2", [sheet], {3: []})

        job = adapter.baton._jobs["j2"]
        state = job.sheets[3]

        while not adapter.baton.inbox.empty():
            adapter.baton.inbox.get_nowait()

        await adapter._dispatch_callback("j2", 3, state)

        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.execution_success is False
        assert event.sheet_num == 3

    @pytest.mark.asyncio
    async def test_no_backend_pool_posts_attempt_result(self) -> None:
        """When no BackendPool is set, dispatch should post a failure result."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        # Intentionally NOT setting backend_pool

        sheet = _make_sheet(num=1, instrument="claude-code")
        adapter.register_job("j3", [sheet], {1: []})

        job = adapter.baton._jobs["j3"]
        state = job.sheets[1]

        while not adapter.baton.inbox.empty():
            adapter.baton.inbox.get_nowait()

        await adapter._dispatch_callback("j3", 1, state)

        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.execution_success is False
        assert event.error_message is not None
        assert "backend pool" in event.error_message.lower()

    @pytest.mark.asyncio
    async def test_sheet_not_found_posts_attempt_result(self) -> None:
        """When the sheet entity is missing, dispatch should post a failure result."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        mock_pool = MagicMock()
        mock_pool.acquire = AsyncMock()
        adapter.set_backend_pool(mock_pool)

        sheet = _make_sheet(num=1, instrument="claude-code")
        adapter.register_job("j4", [sheet], {1: []})
        adapter._job_sheets["j4"].clear()  # Remove sheet mapping

        job = adapter.baton._jobs["j4"]
        state = job.sheets[1]

        while not adapter.baton.inbox.empty():
            adapter.baton.inbox.get_nowait()

        await adapter._dispatch_callback("j4", 1, state)

        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.execution_success is False

    @pytest.mark.asyncio
    async def test_failure_result_uses_correct_attempt_number(self) -> None:
        """The failure result should use the current attempt number from state."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        mock_pool = MagicMock()
        mock_pool.acquire = AsyncMock(
            side_effect=ValueError("Unsupported")
        )
        adapter.set_backend_pool(mock_pool)

        sheet = _make_sheet(num=1, instrument="bad")
        adapter.register_job("j5", [sheet], {1: []})

        job = adapter.baton._jobs["j5"]
        state = job.sheets[1]
        # Simulate previous attempts
        state.normal_attempts = 2

        while not adapter.baton.inbox.empty():
            adapter.baton.inbox.get_nowait()

        await adapter._dispatch_callback("j5", 1, state)

        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.attempt == 3  # 2 normal + 1 new

    def test_has_completed_sheets_true(self) -> None:
        """has_completed_sheets returns True when at least one sheet completed."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("j1", sheets, {1: [], 2: [1]})

        job = adapter.baton._jobs["j1"]
        job.sheets[1].status = BatonSheetStatus.COMPLETED
        job.sheets[2].status = BatonSheetStatus.FAILED

        assert adapter.has_completed_sheets("j1") is True

    def test_has_completed_sheets_false(self) -> None:
        """has_completed_sheets returns False when no sheet completed."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("j1", sheets, {1: [], 2: [1]})

        job = adapter.baton._jobs["j1"]
        job.sheets[1].status = BatonSheetStatus.FAILED
        job.sheets[2].status = BatonSheetStatus.FAILED

        assert adapter.has_completed_sheets("j1") is False

    def test_has_completed_sheets_unknown_job(self) -> None:
        """has_completed_sheets returns False for unknown jobs."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        assert adapter.has_completed_sheets("nonexistent") is False


# =========================================================================
# F-145: completed_new_work flag in baton paths
# =========================================================================


class TestF145CompletedNewWork:
    """_run_via_baton and _resume_via_baton must set meta.completed_new_work
    after successful completion."""

    @pytest.mark.asyncio
    async def test_run_via_baton_sets_completed_new_work_on_success(self) -> None:
        """After baton reports all sheets completed, completed_new_work must be True."""
        from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta

        manager = _make_mock_manager()
        adapter = manager._baton_adapter

        adapter.wait_for_completion = AsyncMock(return_value=True)
        adapter.register_job = MagicMock()
        adapter.publish_job_event = AsyncMock()
        # has_completed_sheets should return True when all succeeded
        adapter.has_completed_sheets = MagicMock(return_value=True)

        mock_config = _make_mock_config()
        mock_request = _make_mock_request()

        meta = JobMeta(
            job_id="test-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["test-job"] = meta

        with patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={1: []}):
            result = await manager._run_via_baton("test-job", mock_config, mock_request)

        assert result == DaemonJobStatus.COMPLETED
        assert meta.completed_new_work is True

    @pytest.mark.asyncio
    async def test_run_via_baton_no_completed_new_work_on_failure(self) -> None:
        """When baton reports failures, completed_new_work should not be set True."""
        from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta

        manager = _make_mock_manager()
        adapter = manager._baton_adapter

        adapter.wait_for_completion = AsyncMock(return_value=False)
        adapter.register_job = MagicMock()
        adapter.publish_job_event = AsyncMock()
        adapter.has_completed_sheets = MagicMock(return_value=False)

        mock_config = _make_mock_config()
        mock_request = _make_mock_request()

        meta = JobMeta(
            job_id="test-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["test-job"] = meta

        with patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={1: []}):
            result = await manager._run_via_baton("test-job", mock_config, mock_request)

        assert result == DaemonJobStatus.FAILED
        assert not meta.completed_new_work

    @pytest.mark.asyncio
    async def test_resume_via_baton_sets_completed_new_work_on_success(self) -> None:
        """Resume path must also set completed_new_work on success."""
        from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta

        manager = _make_mock_manager()
        adapter = manager._baton_adapter

        adapter.wait_for_completion = AsyncMock(return_value=True)
        adapter.recover_job = MagicMock()
        adapter.publish_job_event = AsyncMock()
        adapter.has_completed_sheets = MagicMock(return_value=True)

        meta = JobMeta(
            job_id="resume-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["resume-job"] = meta

        mock_checkpoint = MagicMock()
        mock_checkpoint.sheets = {}
        manager._load_checkpoint = AsyncMock(return_value=mock_checkpoint)

        mock_config = _make_mock_config()
        with patch("marianne.core.config.JobConfig") as MockJobConfig, \
             patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={1: []}):
            MockJobConfig.from_yaml.return_value = mock_config
            result = await manager._resume_via_baton("resume-job", Path("/tmp/ws"))

        assert result == DaemonJobStatus.COMPLETED
        assert meta.completed_new_work is True


# =========================================================================
# F-158: Wire PromptConfig into register_job
# =========================================================================


class TestF158PromptConfigWiring:
    """_run_via_baton must pass prompt_config to register_job()."""

    @pytest.mark.asyncio
    async def test_run_via_baton_passes_prompt_config(self) -> None:
        """register_job should receive prompt_config from the job's config."""
        from marianne.daemon.manager import DaemonJobStatus, JobMeta

        manager = _make_mock_manager()
        adapter = manager._baton_adapter

        adapter.wait_for_completion = AsyncMock(return_value=True)
        adapter.register_job = MagicMock()
        adapter.publish_job_event = AsyncMock()
        adapter.has_completed_sheets = MagicMock(return_value=True)

        mock_config = _make_mock_config()
        mock_request = _make_mock_request()

        meta = JobMeta(
            job_id="test-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["test-job"] = meta

        with patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={1: []}):
            await manager._run_via_baton("test-job", mock_config, mock_request)

        adapter.register_job.assert_called_once()
        call_kwargs = adapter.register_job.call_args.kwargs
        assert call_kwargs.get("prompt_config") is not None, (
            "prompt_config must not be None — F-158 requires it for PromptRenderer"
        )

    @pytest.mark.asyncio
    async def test_run_via_baton_passes_parallel_enabled(self) -> None:
        """register_job should receive parallel_enabled from the config."""
        from marianne.daemon.manager import DaemonJobStatus, JobMeta

        manager = _make_mock_manager()
        adapter = manager._baton_adapter

        adapter.wait_for_completion = AsyncMock(return_value=True)
        adapter.register_job = MagicMock()
        adapter.publish_job_event = AsyncMock()
        adapter.has_completed_sheets = MagicMock(return_value=True)

        mock_config = _make_mock_config(parallel=True)
        mock_request = _make_mock_request()

        meta = JobMeta(
            job_id="test-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["test-job"] = meta

        with patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={1: []}):
            await manager._run_via_baton("test-job", mock_config, mock_request)

        call_kwargs = adapter.register_job.call_args.kwargs
        assert call_kwargs.get("parallel_enabled") is True

    @pytest.mark.asyncio
    async def test_resume_via_baton_passes_prompt_config(self) -> None:
        """Resume path must also pass prompt_config."""
        from marianne.daemon.manager import DaemonJobStatus, JobMeta

        manager = _make_mock_manager()
        adapter = manager._baton_adapter

        adapter.wait_for_completion = AsyncMock(return_value=True)
        adapter.recover_job = MagicMock()
        adapter.publish_job_event = AsyncMock()
        adapter.has_completed_sheets = MagicMock(return_value=True)

        meta = JobMeta(
            job_id="resume-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["resume-job"] = meta

        mock_checkpoint = MagicMock()
        mock_checkpoint.sheets = {}
        manager._load_checkpoint = AsyncMock(return_value=mock_checkpoint)

        mock_config = _make_mock_config()
        with patch("marianne.core.config.JobConfig") as MockJobConfig, \
             patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]), \
             patch("marianne.daemon.baton.adapter.extract_dependencies", return_value={1: []}):
            MockJobConfig.from_yaml.return_value = mock_config
            await manager._resume_via_baton("resume-job", Path("/tmp/ws"))

        adapter.recover_job.assert_called_once()
        call_kwargs = adapter.recover_job.call_args.kwargs
        assert call_kwargs.get("prompt_config") is not None


# =========================================================================
# Test helpers
# =========================================================================


def _make_mock_manager() -> MagicMock:
    """Create a mock JobManager with the real _run_via_baton/_resume_via_baton."""
    from marianne.daemon.baton.adapter import BatonAdapter

    manager = MagicMock()
    manager._baton_adapter = BatonAdapter()
    manager._job_meta = {}
    manager._config_name_to_conductor_id = {}
    manager._config = MagicMock()
    manager._config.default_thinking_method = None

    # Bind the real methods we're testing
    from marianne.daemon.manager import JobManager
    manager._run_via_baton = JobManager._run_via_baton.__get__(manager)
    manager._resume_via_baton = JobManager._resume_via_baton.__get__(manager)
    manager._set_job_status = JobManager._set_job_status.__get__(manager)

    # Registry must be async-compatible since _set_job_status awaits it
    manager._registry = MagicMock()
    manager._registry.update_status = AsyncMock()
    manager._registry.save_checkpoint = AsyncMock()  # F-493: resume path persists started_at

    return manager


def _make_mock_config(parallel: bool = False) -> MagicMock:
    """Create a mock JobConfig for testing."""
    config = MagicMock()
    config.name = "test-job"
    config.backend.type = "claude_cli"
    config.retry.max_retries = 3
    config.cost_limits.enabled = False
    config.cost_limits.max_cost_per_job = None
    config.instrument = "claude-code"
    config.workspace = Path("/tmp/ws")

    # Prompt config — use a real PromptConfig
    from marianne.core.config.job import PromptConfig
    config.prompt = PromptConfig(template="test prompt")

    # Parallel execution
    config.parallel.enabled = parallel

    return config


def _make_mock_request() -> MagicMock:
    """Create a mock JobRequest for testing."""
    request = MagicMock()
    request.workspace = None
    request.fresh = False
    request.start_sheet = None
    request.self_healing = False
    request.self_healing_auto_confirm = False
    request.dry_run = False
    return request
