"""Tests for mozart.daemon.job_service module."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import JobConfig
from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.job_service import JobService
from marianne.daemon.output import NullOutput

# Path to the fixture config (shared across all test classes)
FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "test-daemon-job.yaml"


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def null_output() -> NullOutput:
    """Create a NullOutput for test isolation."""
    return NullOutput()


@pytest.fixture
def job_service(null_output: NullOutput) -> JobService:
    """Create a JobService with NullOutput."""
    return JobService(output=null_output)


@pytest.fixture
def sample_job_config(tmp_path: Path) -> JobConfig:
    """Create a real JobConfig from the test fixture YAML.

    Uses a real config instead of MagicMock to catch schema drift
    and ensure tests validate real attribute access patterns.
    Learning is disabled to match the baseline expectation of most tests;
    tests that need learning create their own config copy.
    """
    config = JobConfig.from_yaml(FIXTURE_CONFIG)
    # Override workspace to a unique tmp_path per test and bump sheet count
    # for tests that rely on 5 sheets; disable learning for isolation
    return config.model_copy(update={
        "workspace": tmp_path / "test-workspace",
        "sheet": config.sheet.model_copy(update={"size": 2, "total_items": 10}),
        "learning": config.learning.model_copy(update={"enabled": False}),
    })


# ─── Instantiation ────────────────────────────────────────────────────────


class TestJobServiceInstantiation:
    """Tests for JobService construction."""

    def test_default_output_is_null(self):
        """Test JobService uses NullOutput when none provided."""
        service = JobService()
        assert isinstance(service._output, NullOutput)

    def test_custom_output(self, null_output: NullOutput):
        """Test JobService accepts custom output."""
        service = JobService(output=null_output)
        assert service._output is null_output

    def test_learning_store_none_by_default(self):
        """Test global_learning_store defaults to None."""
        service = JobService()
        assert service._learning_store is None

    def test_custom_learning_store(self):
        """Test JobService accepts custom learning store."""
        mock_store = MagicMock()
        service = JobService(global_learning_store=mock_store)
        assert service._learning_store is mock_store


# ─── start_job ────────────────────────────────────────────────────────────


class TestStartJob:
    """Tests for JobService.start_job()."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_pending_summary(
        self, job_service: JobService, sample_job_config: JobConfig
    ):
        """Test dry_run=True returns summary without executing."""
        summary = await job_service.start_job(
            sample_job_config,
            dry_run=True,
        )

        assert summary.job_id == "test-daemon-job"
        assert summary.final_status == JobStatus.PENDING
        assert summary.total_sheets == 5

    @pytest.mark.asyncio
    async def test_start_job_creates_workspace(
        self, job_service: JobService, sample_job_config: JobConfig
    ):
        """Test start_job creates workspace directory."""
        assert not sample_job_config.workspace.exists()

        await job_service.start_job(sample_job_config, dry_run=True)

        assert sample_job_config.workspace.exists()


# ─── pause_job ────────────────────────────────────────────────────────────


class TestPauseJob:
    """Tests for JobService.pause_job()."""

    @pytest.mark.asyncio
    async def test_pause_creates_signal_file(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test pause_job creates a signal file in the workspace."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Create a mock state that's RUNNING
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.RUNNING,
        )

        with patch.object(
            JobService, "_find_job_state", new_callable=AsyncMock
        ) as mock_find:
            mock_backend = AsyncMock()
            mock_find.return_value = (state, mock_backend)
            result = await job_service.pause_job("test-job", workspace)

        assert result is True
        signal_file = workspace / ".mozart-pause-test-job"
        assert signal_file.exists()

    @pytest.mark.asyncio
    async def test_pause_non_running_job_raises(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test pause_job raises when job is not RUNNING."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.COMPLETED,
        )

        with patch.object(
            JobService, "_find_job_state", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = (state, AsyncMock())

            with pytest.raises(JobSubmissionError, match="not running"):
                await job_service.pause_job("test-job", workspace)

    @pytest.mark.asyncio
    async def test_pause_paused_job_raises(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test pause_job raises when job is already PAUSED."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.PAUSED,
        )

        with patch.object(
            JobService, "_find_job_state", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = (state, AsyncMock())

            with pytest.raises(JobSubmissionError, match="not running"):
                await job_service.pause_job("test-job", workspace)


# ─── get_status ───────────────────────────────────────────────────────────


class TestGetStatus:
    """Tests for JobService.get_status()."""

    @pytest.mark.asyncio
    async def test_returns_state_when_found(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test get_status returns CheckpointState when job exists."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.RUNNING,
        )

        mock_backend = AsyncMock()
        mock_backend.load = AsyncMock(return_value=state)

        with patch.object(
            JobService, "_create_state_backend", return_value=mock_backend
        ):
            result = await job_service.get_status("test-job", tmp_path)

        assert result is not None
        assert result.job_id == "test-job"
        assert result.status == JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test get_status returns None when job doesn't exist."""
        mock_backend = AsyncMock()
        mock_backend.load = AsyncMock(return_value=None)

        with patch.object(
            JobService, "_create_state_backend", return_value=mock_backend
        ):
            result = await job_service.get_status("nonexistent", tmp_path)

        assert result is None


class TestPublishingBackend:
    """Tests for _PublishingBackend state-publish wrapper."""

    @pytest.mark.asyncio
    async def test_save_publishes_state(self):
        """save() delegates to inner backend and fires the callback."""
        from marianne.daemon.job_service import _PublishingBackend

        inner = AsyncMock()
        published: list[CheckpointState] = []

        def callback(state: CheckpointState) -> None:
            published.append(state)

        backend = _PublishingBackend(inner, callback)
        state = CheckpointState(
            job_id="pub-test", job_name="pub-test",
            total_sheets=3, status=JobStatus.RUNNING,
        )

        await backend.save(state)

        inner.save.assert_awaited_once_with(state)
        assert len(published) == 1
        assert published[0].job_id == "pub-test"

    @pytest.mark.asyncio
    async def test_callback_error_does_not_propagate(self):
        """Callback failure must not break state saving."""
        from marianne.daemon.job_service import _PublishingBackend

        inner = AsyncMock()

        def bad_callback(state: CheckpointState) -> None:
            raise RuntimeError("callback boom")

        backend = _PublishingBackend(inner, bad_callback)
        state = CheckpointState(
            job_id="err-test", job_name="err-test",
            total_sheets=2, status=JobStatus.RUNNING,
        )

        # Should not raise
        await backend.save(state)
        inner.save.assert_awaited_once_with(state)

    @pytest.mark.asyncio
    async def test_delegates_other_methods(self):
        """Non-save methods are delegated to the inner backend."""
        from marianne.daemon.job_service import _PublishingBackend

        inner = AsyncMock()
        inner.load = AsyncMock(return_value=None)

        backend = _PublishingBackend(inner, lambda s: None)

        await backend.load("some-job")
        inner.load.assert_awaited_once_with("some-job")


class TestWrapStateBackend:
    """Tests for JobService._wrap_state_backend()."""

    def test_wraps_when_callback_set(self):
        """With a publish callback, returns a _PublishingBackend."""
        from marianne.daemon.job_service import _PublishingBackend

        service = JobService(state_publish_callback=lambda s: None)
        inner = AsyncMock()
        wrapped = service._wrap_state_backend(inner)
        assert isinstance(wrapped, _PublishingBackend)

    def test_passthrough_when_no_callback(self):
        """Without a publish callback, returns the backend unchanged."""
        service = JobService()
        inner = AsyncMock()
        result = service._wrap_state_backend(inner)
        assert result is inner


# ─── create_backend (execution.setup) ─────────────────────────────────────


class TestCreateBackend:
    """Tests for execution.setup.create_backend().

    These test the shared setup module that both CLI and daemon use.
    """

    def test_claude_cli_backend(self):
        """Test create_backend creates ClaudeCliBackend for claude_cli."""
        from marianne.execution.setup import create_backend

        mock_config = MagicMock()
        mock_config.backend.type = "claude_cli"

        with patch(
            "marianne.backends.claude_cli.ClaudeCliBackend.from_config"
        ) as mock_from_config:
            mock_from_config.return_value = MagicMock()
            create_backend(mock_config)
            mock_from_config.assert_called_once_with(mock_config.backend)

    def test_anthropic_api_backend(self):
        """Test create_backend creates AnthropicApiBackend for anthropic_api."""
        from marianne.execution.setup import create_backend

        mock_config = MagicMock()
        mock_config.backend.type = "anthropic_api"

        with patch(
            "marianne.backends.anthropic_api.AnthropicApiBackend.from_config"
        ) as mock_from_config:
            mock_from_config.return_value = MagicMock()
            create_backend(mock_config)
            mock_from_config.assert_called_once_with(mock_config.backend)

    def test_recursive_light_backend(self):
        """Test create_backend creates RecursiveLightBackend for recursive_light."""
        from marianne.execution.setup import create_backend

        mock_config = MagicMock()
        mock_config.backend.type = "recursive_light"

        with patch(
            "marianne.backends.recursive_light.RecursiveLightBackend.from_config"
        ) as mock_from_config:
            mock_from_config.return_value = MagicMock()
            create_backend(mock_config)
            mock_from_config.assert_called_once_with(mock_config.backend)

    def test_unknown_backend_falls_back_to_claude_cli(self):
        """Test create_backend defaults to ClaudeCliBackend for unknown types."""
        from marianne.execution.setup import create_backend

        mock_config = MagicMock()
        mock_config.backend.type = "unknown_type"

        with patch(
            "marianne.backends.claude_cli.ClaudeCliBackend.from_config"
        ) as mock_from_config:
            mock_from_config.return_value = MagicMock()
            create_backend(mock_config)
            mock_from_config.assert_called_once_with(mock_config.backend)


# ─── _create_state_backend ────────────────────────────────────────────────


class TestCreateStateBackend:
    """Tests for JobService._create_state_backend()."""

    def test_json_backend_default(self, job_service: JobService, tmp_path: Path):
        """Test _create_state_backend creates JsonStateBackend by default."""
        backend = job_service._create_state_backend(tmp_path)
        from marianne.state import JsonStateBackend

        assert isinstance(backend, JsonStateBackend)

    def test_json_backend_explicit(self, job_service: JobService, tmp_path: Path):
        """Test _create_state_backend creates JsonStateBackend for 'json'."""
        backend = job_service._create_state_backend(tmp_path, "json")
        from marianne.state import JsonStateBackend

        assert isinstance(backend, JsonStateBackend)

    def test_sqlite_backend(self, job_service: JobService, tmp_path: Path):
        """Test _create_state_backend creates SQLiteStateBackend for 'sqlite'."""
        backend = job_service._create_state_backend(tmp_path, "sqlite")
        from marianne.state import SQLiteStateBackend

        assert isinstance(backend, SQLiteStateBackend)


# ─── _find_job_state ──────────────────────────────────────────────────────


class TestFindJobState:
    """Tests for JobService._find_job_state()."""

    @pytest.mark.asyncio
    async def test_workspace_not_found_raises(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test _find_job_state raises when workspace doesn't exist."""
        nonexistent = tmp_path / "does-not-exist"

        with pytest.raises(JobSubmissionError, match="Workspace not found"):
            await job_service._find_job_state("test-job", nonexistent)

    @pytest.mark.asyncio
    async def test_job_not_found_raises(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test _find_job_state raises when job doesn't exist in any backend."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with pytest.raises(JobSubmissionError, match="not found in workspace"):
            await job_service._find_job_state("nonexistent-job", workspace)

    @pytest.mark.asyncio
    async def test_finds_job_in_json_backend(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test _find_job_state finds job via JsonStateBackend."""
        from marianne.state import JsonStateBackend

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Save a state via the JSON backend directly
        backend = JsonStateBackend(workspace)
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.RUNNING,
        )
        await backend.save(state)

        found_state, found_backend = await job_service._find_job_state(
            "test-job", workspace
        )
        assert found_state.job_id == "test-job"
        assert found_state.status == JobStatus.RUNNING


# ─── resume_job validation ────────────────────────────────────────────────


class TestResumeJobValidation:
    """Tests for resume_job edge cases and validation."""

    @pytest.mark.asyncio
    async def test_resume_completed_job_raises(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test resume_job raises for completed jobs."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.COMPLETED,
        )

        with patch.object(
            JobService, "_find_job_state", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = (state, AsyncMock())

            with pytest.raises(JobSubmissionError, match="already completed"):
                await job_service.resume_job("test-job", workspace)

    @pytest.mark.asyncio
    async def test_resume_pending_job_raises(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test resume_job raises for pending jobs."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.PENDING,
        )

        with patch.object(
            JobService, "_find_job_state", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = (state, AsyncMock())

            with pytest.raises(JobSubmissionError, match="not been started"):
                await job_service.resume_job("test-job", workspace)


# ─── _reconstruct_config ─────────────────────────────────────────────────


class TestReconstructConfig:
    """Tests for JobService._reconstruct_config()."""

    def test_explicit_config_takes_priority(self, job_service: JobService):
        """Test explicit config is returned directly (priority 1)."""
        mock_config = MagicMock()
        mock_state = MagicMock()

        result, was_reloaded = job_service._reconstruct_config(mock_state, config=mock_config)
        assert result is mock_config
        assert was_reloaded is True

    def test_no_config_source_raises(self, job_service: JobService):
        """Test raises when no config source available."""
        mock_state = MagicMock()
        mock_state.config_snapshot = None
        mock_state.config_path = None

        with pytest.raises(JobSubmissionError, match="no config available"):
            job_service._reconstruct_config(mock_state)

    def test_auto_reload_from_config_path(self, job_service: JobService, tmp_path: Path):
        """Config should auto-reload from stored config_path when file exists."""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(
            "name: reloaded-job\n"
            "backend:\n  type: claude_cli\n"
            "sheet:\n  size: 3\n  total_items: 9\n"
            "prompt:\n  template: 'Test {{ sheet_num }}'\n"
        )
        mock_state = MagicMock()
        mock_state.config_path = str(config_file)
        mock_state.config_snapshot = {"name": "old-snapshot"}

        result, was_reloaded = job_service._reconstruct_config(mock_state)
        assert result.name == "reloaded-job"
        assert was_reloaded is True

    def test_snapshot_fallback_when_file_missing(self, job_service: JobService, tmp_path: Path):
        """Should fall back to snapshot when config file doesn't exist."""
        mock_state = MagicMock()
        mock_state.config_path = str(tmp_path / "deleted.yaml")
        mock_state.config_snapshot = {
            "name": "snapshot-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 3, "total_items": 9},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }

        result, was_reloaded = job_service._reconstruct_config(mock_state)
        assert result.name == "snapshot-job"
        assert was_reloaded is False

    def test_snapshot_fallback_when_no_config_path(self, job_service: JobService):
        """Should fall back to snapshot when no config_path stored."""
        mock_state = MagicMock()
        mock_state.config_path = None
        mock_state.config_snapshot = {
            "name": "snapshot-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 3, "total_items": 9},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }

        result, was_reloaded = job_service._reconstruct_config(mock_state)
        assert result.name == "snapshot-job"
        assert was_reloaded is False

    def test_no_reload_skips_auto_reload(self, job_service: JobService, tmp_path: Path):
        """no_reload=True should skip auto-reload and use snapshot."""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(
            "name: reloaded-job\n"
            "backend:\n  type: claude_cli\n"
            "sheet:\n  size: 3\n  total_items: 9\n"
            "prompt:\n  template: 'Test {{ sheet_num }}'\n"
        )
        mock_state = MagicMock()
        mock_state.config_path = str(config_file)
        mock_state.config_snapshot = {
            "name": "snapshot-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 3, "total_items": 9},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }

        result, was_reloaded = job_service._reconstruct_config(mock_state, no_reload=True)
        assert result.name == "snapshot-job"
        assert was_reloaded is False

    def test_explicit_config_path_overrides_stored(self, job_service: JobService, tmp_path: Path):
        """Explicit config_path should override state.config_path for auto-reload."""
        config_file = tmp_path / "override.yaml"
        config_file.write_text(
            "name: override-job\n"
            "backend:\n  type: claude_cli\n"
            "sheet:\n  size: 3\n  total_items: 9\n"
            "prompt:\n  template: 'Test {{ sheet_num }}'\n"
        )
        mock_state = MagicMock()
        mock_state.config_path = str(tmp_path / "old.yaml")
        mock_state.config_snapshot = {"name": "snapshot"}

        result, was_reloaded = job_service._reconstruct_config(
            mock_state, config_path=config_file,
        )
        assert result.name == "override-job"
        assert was_reloaded is True

    def test_config_snapshot_used_when_available(
        self, job_service: JobService, sample_job_config: JobConfig
    ):
        """Test config_snapshot is used as priority 3."""
        from marianne.core.config import JobConfig

        # Create a real minimal config dict
        snapshot = {
            "name": "test-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "{{ sheet_num }}"},
        }

        mock_state = MagicMock()
        mock_state.config_snapshot = snapshot
        mock_state.config_path = None

        result, was_reloaded = job_service._reconstruct_config(mock_state)
        assert isinstance(result, JobConfig)
        assert result.name == "test-job"
        assert was_reloaded is False


# ─── start_job full execution ────────────────────────────────────────────


class TestStartJobExecution:
    """Tests for start_job() full execution paths (non-dry-run)."""

    @pytest.mark.asyncio
    async def test_start_job_runs_and_returns_summary(
        self, job_service: JobService, sample_job_config: JobConfig
    ):
        """Test start_job runs the runner and returns a RunSummary."""
        from marianne.execution.runner.models import RunSummary

        name = sample_job_config.name
        sheets = sample_job_config.sheet.total_sheets

        expected_state = CheckpointState(
            job_id=name, job_name=name, total_sheets=sheets,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id=name, job_name=name, total_sheets=sheets,
            completed_sheets=sheets, final_status=JobStatus.COMPLETED,
        )

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=(expected_state, expected_summary))

        with (
            patch.object(Path, "mkdir"),
            patch.object(JobService, "_create_state_backend", return_value=MagicMock(close=AsyncMock())),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.start_job(sample_job_config)

        assert summary.job_id == name
        assert summary.final_status == JobStatus.COMPLETED
        assert summary.completed_sheets == sheets

    @pytest.mark.asyncio
    async def test_start_job_graceful_shutdown_returns_paused(
        self, job_service: JobService, sample_job_config: JobConfig
    ):
        """Test start_job returns PAUSED summary on GracefulShutdownError."""
        from marianne.execution.runner import GracefulShutdownError
        from marianne.execution.runner.models import RunSummary

        name = sample_job_config.name
        sheets = sample_job_config.sheet.total_sheets

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(side_effect=GracefulShutdownError())
        partial_summary = RunSummary(
            job_id=name, job_name=name, total_sheets=sheets,
            completed_sheets=3,
        )
        mock_runner.get_summary.return_value = partial_summary

        with (
            patch.object(Path, "mkdir"),
            patch.object(JobService, "_create_state_backend", return_value=MagicMock(close=AsyncMock())),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.start_job(sample_job_config)

        assert summary.final_status == JobStatus.PAUSED
        assert summary.completed_sheets == 3

    @pytest.mark.asyncio
    async def test_start_job_fatal_error_returns_failed(
        self, job_service: JobService, sample_job_config: JobConfig
    ):
        """Test start_job returns FAILED summary on FatalError."""
        from marianne.execution.runner import FatalError

        sheets = sample_job_config.sheet.total_sheets

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(side_effect=FatalError("bad thing"))
        mock_runner.get_summary.return_value = None

        with (
            patch.object(Path, "mkdir"),
            patch.object(JobService, "_create_state_backend", return_value=MagicMock(close=AsyncMock())),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.start_job(sample_job_config)

        assert summary.final_status == JobStatus.FAILED
        assert summary.total_sheets == sheets

    @pytest.mark.asyncio
    async def test_start_job_notifications_lifecycle(
        self, job_service: JobService, sample_job_config: JobConfig
    ):
        """Test start_job calls notification manager start, complete, and close."""
        from marianne.execution.runner.models import RunSummary

        name = sample_job_config.name
        sheets = sample_job_config.sheet.total_sheets

        expected_state = CheckpointState(
            job_id=name, job_name=name, total_sheets=sheets,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id=name, job_name=name, total_sheets=sheets,
            completed_sheets=sheets, final_status=JobStatus.COMPLETED,
        )

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=(expected_state, expected_summary))

        mock_notification_mgr = AsyncMock()

        with (
            patch.object(Path, "mkdir"),
            patch.object(JobService, "_create_state_backend", return_value=MagicMock(close=AsyncMock())),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": mock_notification_mgr,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            await job_service.start_job(sample_job_config)

        mock_notification_mgr.notify_job_start.assert_called_once()
        mock_notification_mgr.notify_job_complete.assert_called_once()
        # close() called twice: once in _execute_runner's finally (primary),
        # once in start_job's finally (belt-and-suspenders for setup failures).
        assert mock_notification_mgr.close.call_count >= 1


# ─── resume_job full execution ───────────────────────────────────────────


class TestResumeJobExecution:
    """Tests for resume_job() full execution paths."""

    @pytest.mark.asyncio
    async def test_resume_job_happy_path(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test resume_job runs from paused state and returns COMPLETED."""
        from marianne.execution.runner.models import RunSummary

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        paused_state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.PAUSED,
            config_snapshot={
                "name": "test-job",
                "backend": {"type": "claude_cli"},
                "sheet": {"size": 5, "total_items": 10},
                "prompt": {"template": "{{ sheet_num }}"},
            },
        )

        mock_backend = AsyncMock()
        completed_state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            completed_sheets=5,
            final_status=JobStatus.COMPLETED,
        )

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=(completed_state, expected_summary))

        with (
            patch.object(
                JobService, "_find_job_state", new_callable=AsyncMock,
                return_value=(paused_state, mock_backend),
            ),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.resume_job("test-job", workspace)

        assert summary.final_status == JobStatus.COMPLETED
        assert summary.completed_sheets == 5
        # Verify state was reset to RUNNING before execution
        mock_backend.save.assert_called_once()
        saved_state = mock_backend.save.call_args[0][0]
        assert saved_state.status == JobStatus.RUNNING
        assert saved_state.pid is None  # Stale PID must be cleared

    @pytest.mark.asyncio
    async def test_resume_clears_stale_pid(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test resume_job clears stale PID to prevent false-positive 'already running'."""
        from marianne.execution.runner.models import RunSummary

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Simulate state with a stale PID (e.g., from a previous conductor)
        paused_state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.PAUSED,
            pid=99999,  # Stale PID from previous run
            config_snapshot={
                "name": "test-job",
                "backend": {"type": "claude_cli"},
                "sheet": {"size": 5, "total_items": 10},
                "prompt": {"template": "{{ sheet_num }}"},
            },
        )

        mock_backend = AsyncMock()
        completed_state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            completed_sheets=5,
            final_status=JobStatus.COMPLETED,
        )

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=(completed_state, expected_summary))

        with (
            patch.object(
                JobService, "_find_job_state", new_callable=AsyncMock,
                return_value=(paused_state, mock_backend),
            ),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.resume_job("test-job", workspace)

        assert summary.final_status == JobStatus.COMPLETED
        # The critical assertion: PID must be None in the saved state
        saved_state = mock_backend.save.call_args[0][0]
        assert saved_state.pid is None

    @pytest.mark.asyncio
    async def test_resume_failed_job_runs(
        self, job_service: JobService, tmp_path: Path
    ):
        """Test resume_job can resume a FAILED job."""
        from marianne.execution.runner.models import RunSummary

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        failed_state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            last_completed_sheet=3,
            status=JobStatus.FAILED,
            config_snapshot={
                "name": "test-job",
                "backend": {"type": "claude_cli"},
                "sheet": {"size": 5, "total_items": 10},
                "prompt": {"template": "{{ sheet_num }}"},
            },
        )

        mock_backend = AsyncMock()
        completed_state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            completed_sheets=5,
            final_status=JobStatus.COMPLETED,
        )

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=(completed_state, expected_summary))

        with (
            patch.object(
                JobService, "_find_job_state", new_callable=AsyncMock,
                return_value=(failed_state, mock_backend),
            ),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.resume_job("test-job", workspace)

        assert summary.final_status == JobStatus.COMPLETED
        # Runner should be called with start_sheet=4 (last_completed+1)
        # config_path is None (not in state), so it's omitted from run_kwargs
        mock_runner.run.assert_called_once_with(start_sheet=4)


# ─── _setup_components ───────────────────────────────────────────────────


class TestSetupComponents:
    """Tests for JobService._setup_components()."""

    def test_basic_setup_no_learning_no_notifications(
        self, job_service: JobService, sample_job_config: JobConfig,
    ):
        """Test _setup_components with learning/notifications/grounding disabled."""
        components = job_service._setup_components(sample_job_config)

        assert components["backend"] is not None
        assert components["outcome_store"] is None
        assert components["global_learning_store"] is None
        assert components["notification_manager"] is None
        assert components["escalation_handler"] is None
        assert components["grounding_engine"] is None

    def test_setup_with_learning_enabled(
        self, job_service: JobService, sample_job_config: JobConfig,
    ):
        """Test _setup_components creates learning stores when enabled."""
        config = sample_job_config.model_copy(
            update={"learning": sample_job_config.learning.model_copy(
                update={"enabled": True},
            )},
        )
        components = job_service._setup_components(config)

        from marianne.learning.outcomes import JsonOutcomeStore
        assert isinstance(components["outcome_store"], JsonOutcomeStore)
        assert components["global_learning_store"] is not None


# ─── Real Component Wiring (D020) ────────────────────────────────────────


class TestRealComponentWiring:
    """D020: Verify _setup_components() with real imports, not mocks.

    Unlike TestSetupComponents which mocks ClaudeCliBackend.from_config(),
    these tests let the real import chain run: JobConfig → _create_backend() →
    ClaudeCliBackend.from_config() → real ClaudeCliBackend instance.

    Only runner.run() is mocked at the execution boundary — everything before
    that (config parsing, backend factory, state backend, learning store
    injection, runner creation) runs for real.
    """

    @pytest.fixture
    def real_config(self, tmp_path: Path) -> JobConfig:
        """Parse the fixture YAML into a real JobConfig with tmp_path workspace."""
        config = JobConfig.from_yaml(FIXTURE_CONFIG)
        return config.model_copy(update={"workspace": tmp_path / "workspace"})

    def test_setup_components_creates_real_backend(
        self, job_service: JobService, real_config: JobConfig,
    ):
        """_setup_components creates a real ClaudeCliBackend, not a mock."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        components = job_service._setup_components(real_config)

        assert isinstance(components["backend"], ClaudeCliBackend)
        # Verify the backend picked up config values
        assert components["backend"].skip_permissions is True
        assert components["backend"].disable_mcp is True

    def test_setup_components_learning_disabled_returns_none(
        self, job_service: JobService, real_config: JobConfig,
    ):
        """When learning is disabled in config, stores are None."""
        config = real_config.model_copy(
            update={"learning": real_config.learning.model_copy(update={"enabled": False})},
        )
        components = job_service._setup_components(config)

        assert components["outcome_store"] is None
        assert components["global_learning_store"] is None

    def test_setup_components_learning_enabled_creates_real_stores(
        self, real_config: JobConfig, tmp_path: Path,
    ):
        """When learning is enabled, real outcome store and global store are created."""
        from marianne.learning.global_store import GlobalLearningStore
        from marianne.learning.outcomes import JsonOutcomeStore

        config = real_config.model_copy(
            update={"learning": real_config.learning.model_copy(update={"enabled": True})},
        )

        # Inject a real global learning store (like the daemon does)
        db_path = tmp_path / "test-learning.db"
        global_store = GlobalLearningStore(db_path=db_path)
        service = JobService(global_learning_store=global_store)

        components = service._setup_components(config)

        assert isinstance(components["outcome_store"], JsonOutcomeStore)
        # The injected store should be used (daemon path), not a new singleton
        assert components["global_learning_store"] is global_store

    def test_create_runner_wires_real_components(
        self, job_service: JobService, real_config: JobConfig,
    ):
        """_create_runner creates a real JobRunner with real components."""
        from marianne.execution.runner import JobRunner
        from marianne.state import JsonStateBackend

        config = real_config
        config.workspace.mkdir(parents=True, exist_ok=True)

        components = job_service._setup_components(config)
        state_backend = job_service._create_state_backend(config.workspace, config.state_backend)

        runner = job_service._create_runner(
            config, components, state_backend, job_id="test-wiring",
        )

        assert isinstance(runner, JobRunner)
        assert isinstance(state_backend, JsonStateBackend)
        # Runner has the real backend attached
        assert runner.backend is components["backend"]

    @pytest.mark.asyncio
    async def test_start_job_real_wiring_with_mocked_run(
        self, real_config: JobConfig,
    ):
        """Full start_job path with real components, only runner.run() mocked.

        This is the key D020 test: the entire wiring chain runs for real —
        config parsing, workspace creation, backend factory, state backend,
        component setup, runner creation. Only runner.run() is patched so
        we don't need a real Claude CLI.
        """
        from marianne.execution.runner.models import RunSummary

        service = JobService()
        config = real_config

        # Create a mock that simulates successful execution
        expected_state = CheckpointState(
            job_id=config.name,
            job_name=config.name,
            total_sheets=config.sheet.total_sheets,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id=config.name,
            job_name=config.name,
            total_sheets=config.sheet.total_sheets,
            completed_sheets=config.sheet.total_sheets,
            final_status=JobStatus.COMPLETED,
        )

        # Patch only runner.run() — everything before this runs for real
        with patch(
            "marianne.execution.runner.LifecycleMixin.run",
            new_callable=AsyncMock,
            return_value=(expected_state, expected_summary),
        ):
            summary = await service.start_job(config)

        assert summary.job_id == config.name
        assert summary.final_status == JobStatus.COMPLETED
        assert summary.completed_sheets == config.sheet.total_sheets

        # Verify workspace was actually created on disk
        assert config.workspace.exists()

    @pytest.mark.asyncio
    async def test_start_job_real_wiring_with_injected_learning_store(
        self, real_config: JobConfig, tmp_path: Path,
    ):
        """Full start_job with daemon-injected learning store flows through to runner.

        When the daemon's LearningHub injects its store into JobService, that
        store should be passed through _setup_components → _create_runner →
        RunnerContext.global_learning_store. This test verifies that chain.
        """
        from marianne.execution.runner.models import RunSummary
        from marianne.learning.global_store import GlobalLearningStore

        db_path = tmp_path / "test-learning.db"
        global_store = GlobalLearningStore(db_path=db_path)
        service = JobService(global_learning_store=global_store)

        config = real_config.model_copy(
            update={"learning": real_config.learning.model_copy(update={"enabled": True})},
        )

        expected_state = CheckpointState(
            job_id=config.name,
            job_name=config.name,
            total_sheets=config.sheet.total_sheets,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id=config.name,
            job_name=config.name,
            total_sheets=config.sheet.total_sheets,
            completed_sheets=config.sheet.total_sheets,
            final_status=JobStatus.COMPLETED,
        )

        # Capture the runner that's created to inspect its context
        created_runners: list = []
        original_create_runner = service._create_runner

        def _spy_create_runner(*args, **kwargs):
            runner = original_create_runner(*args, **kwargs)
            created_runners.append(runner)
            return runner

        with (
            patch.object(service, "_create_runner", side_effect=_spy_create_runner),
            patch(
                "marianne.execution.runner.LifecycleMixin.run",
                new_callable=AsyncMock,
                return_value=(expected_state, expected_summary),
            ),
        ):
            summary = await service.start_job(config)

        assert summary.final_status == JobStatus.COMPLETED
        # Verify the runner received the injected store
        assert len(created_runners) == 1
        runner = created_runners[0]
        assert runner._global_learning_store is global_store


# ─── Q015: Tests for _execute_runner ─────────────────────────────────────


class TestExecuteRunner:
    """Tests for _execute_runner error handling and notification lifecycle (Q015)."""

    @pytest.fixture
    def mock_runner(self) -> MagicMock:
        """Create a mock runner that returns a successful run."""
        runner = MagicMock()
        state = CheckpointState(
            job_id="test-job", job_name="test-job", total_sheets=2,
        )
        state.status = JobStatus.COMPLETED
        summary = MagicMock()
        summary.completed_sheets = 2
        summary.failed_sheets = 0
        summary.total_duration_seconds = 10.0
        summary.final_status = JobStatus.COMPLETED
        runner.run = AsyncMock(return_value=(state, summary))
        runner.get_summary.return_value = summary
        return runner

    @pytest.mark.asyncio
    async def test_successful_run_returns_summary(
        self, job_service: JobService, mock_runner: MagicMock,
    ) -> None:
        """Successful run returns the runner's summary."""
        result = await job_service._execute_runner(
            runner=mock_runner,
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
            notification_manager=None,
        )
        assert result.completed_sheets == 2

    @pytest.mark.asyncio
    async def test_graceful_shutdown_returns_paused(
        self, job_service: JobService,
    ) -> None:
        """GracefulShutdownError → PAUSED status."""
        from marianne.execution.runner.models import GracefulShutdownError, RunSummary

        runner = MagicMock()
        runner.run = AsyncMock(side_effect=GracefulShutdownError("pausing"))
        runner.get_summary.return_value = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=2,
        )

        result = await job_service._execute_runner(
            runner=runner,
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
            notification_manager=None,
        )
        assert result.final_status == JobStatus.PAUSED

    @pytest.mark.asyncio
    async def test_fatal_error_returns_failed(
        self, job_service: JobService,
    ) -> None:
        """FatalError → FAILED status."""
        from marianne.execution.runner.models import FatalError, RunSummary

        runner = MagicMock()
        runner.run = AsyncMock(side_effect=FatalError("broken"))
        runner.get_summary.return_value = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=2,
        )

        result = await job_service._execute_runner(
            runner=runner,
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
            notification_manager=None,
        )
        assert result.final_status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_unexpected_error_propagates(
        self, job_service: JobService,
    ) -> None:
        """Unexpected exception propagates after notification."""
        runner = MagicMock()
        runner.run = AsyncMock(side_effect=RuntimeError("unexpected"))

        with pytest.raises(RuntimeError, match="unexpected"):
            await job_service._execute_runner(
                runner=runner,
                job_id="test-job",
                job_name="test-job",
                total_sheets=2,
                notification_manager=None,
            )

    @pytest.mark.asyncio
    async def test_notification_close_called_on_success(
        self, job_service: JobService, mock_runner: MagicMock,
    ) -> None:
        """Notification manager's close() is called even on success."""
        notification_mgr = AsyncMock()
        notification_mgr.notify_job_start = AsyncMock()
        notification_mgr.notify_job_complete = AsyncMock()
        notification_mgr.close = AsyncMock()

        await job_service._execute_runner(
            runner=mock_runner,
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
            notification_manager=notification_mgr,
        )
        notification_mgr.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_notification_close_called_on_fatal(
        self, job_service: JobService,
    ) -> None:
        """Notification manager's close() is called on FatalError."""
        from marianne.execution.runner.models import FatalError, RunSummary

        runner = MagicMock()
        runner.run = AsyncMock(side_effect=FatalError("fail"))
        runner.get_summary.return_value = RunSummary(
            job_id="test-job", job_name="test-job", total_sheets=2,
        )

        notification_mgr = AsyncMock()
        notification_mgr.notify_job_start = AsyncMock()
        notification_mgr.notify_job_failed = AsyncMock()
        notification_mgr.close = AsyncMock()

        await job_service._execute_runner(
            runner=runner,
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
            notification_manager=notification_mgr,
        )
        notification_mgr.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_sheet_passed_to_runner(
        self, job_service: JobService, mock_runner: MagicMock,
    ) -> None:
        """start_sheet kwarg is forwarded to runner.run()."""
        await job_service._execute_runner(
            runner=mock_runner,
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
            notification_manager=None,
            start_sheet=3,
        )
        mock_runner.run.assert_called_once_with(start_sheet=3)

    @pytest.mark.asyncio
    async def test_config_path_passed_to_runner(
        self, job_service: JobService, mock_runner: MagicMock,
    ) -> None:
        """config_path kwarg is forwarded to runner.run()."""
        await job_service._execute_runner(
            runner=mock_runner,
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
            notification_manager=None,
            config_path="/tmp/test-config.yaml",
        )
        mock_runner.run.assert_called_once_with(config_path="/tmp/test-config.yaml")


# ─── Phase 1: Config path wiring through daemon ─────────────────────────


class TestConfigPathWiring:
    """Tests for config_path being wired from daemon through start_job/resume_job."""

    @pytest.mark.asyncio
    async def test_start_job_passes_config_path_to_runner(
        self, job_service: JobService, sample_job_config: JobConfig,
    ) -> None:
        """start_job(config_path=...) passes it through to runner.run()."""
        from marianne.execution.runner.models import RunSummary

        name = sample_job_config.name
        sheets = sample_job_config.sheet.total_sheets

        expected_state = CheckpointState(
            job_id=name, job_name=name, total_sheets=sheets,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id=name, job_name=name, total_sheets=sheets,
            completed_sheets=sheets, final_status=JobStatus.COMPLETED,
        )

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=(expected_state, expected_summary))

        with (
            patch.object(Path, "mkdir"),
            patch.object(JobService, "_create_state_backend", return_value=MagicMock(close=AsyncMock())),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.start_job(
                sample_job_config,
                config_path="/tmp/my-score.yaml",
            )

        assert summary.final_status == JobStatus.COMPLETED
        mock_runner.run.assert_called_once_with(config_path="/tmp/my-score.yaml")

    @pytest.mark.asyncio
    async def test_start_job_without_config_path_omits_it(
        self, job_service: JobService, sample_job_config: JobConfig,
    ) -> None:
        """start_job() without config_path does not pass it to runner.run()."""
        from marianne.execution.runner.models import RunSummary

        name = sample_job_config.name
        sheets = sample_job_config.sheet.total_sheets

        expected_state = CheckpointState(
            job_id=name, job_name=name, total_sheets=sheets,
            status=JobStatus.COMPLETED,
        )
        expected_summary = RunSummary(
            job_id=name, job_name=name, total_sheets=sheets,
            completed_sheets=sheets, final_status=JobStatus.COMPLETED,
        )

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=(expected_state, expected_summary))

        with (
            patch.object(Path, "mkdir"),
            patch.object(JobService, "_create_state_backend", return_value=MagicMock(close=AsyncMock())),
            patch.object(JobService, "_setup_components", return_value={
                "backend": MagicMock(),
                "outcome_store": None,
                "global_learning_store": None,
                "notification_manager": None,
                "escalation_handler": None,
                "grounding_engine": None,
            }),
            patch.object(JobService, "_create_runner", return_value=mock_runner),
        ):
            summary = await job_service.start_job(sample_job_config)

        assert summary.final_status == JobStatus.COMPLETED
        # config_path not passed, so runner.run() called with no kwargs
        mock_runner.run.assert_called_once_with()
