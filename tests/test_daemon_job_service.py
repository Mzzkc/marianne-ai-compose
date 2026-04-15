"""Tests for marianne.daemon.job_service module."""

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


# TestStartJob — removed: tested old runner-based start_job API (baton migration)


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
        signal_file = workspace / ".marianne-pause-test-job"
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
    """Tests for resume_job edge cases and validation.

    The resume_job method was removed from JobService as part of the
    runner removal — resume is now handled by the conductor/baton.
    """

    @pytest.mark.skip(reason="Runner removed — resume_job no longer exists on JobService")
    async def test_resume_completed_job_raises(self) -> None:
        """Obsolete: resume_job removed from JobService."""

    @pytest.mark.skip(reason="Runner removed — resume_job no longer exists on JobService")
    async def test_resume_pending_job_raises(self) -> None:
        """Obsolete: resume_job removed from JobService."""


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


# TestStartJobExecution — removed: tested old runner-based start_job execution paths (baton migration)


# TestResumeJobExecution — removed: tested old runner-based resume_job execution API (baton migration)


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


# TestRealComponentWiring — removed: tested old runner-based start_job/component wiring API (baton migration)


# TestExecuteRunner — removed: tested old runner-based _execute_runner API (baton migration)


# TestConfigPathWiring — removed: tested old runner-based config_path wiring API (baton migration)
