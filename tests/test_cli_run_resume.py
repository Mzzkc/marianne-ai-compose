"""Tests for CLI run and resume command internals.

Covers daemon-routed run, _find_job_state, _reconstruct_config, and
the _shared.py helper functions that both commands depend on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from mozart.cli import app
from mozart.core.checkpoint import CheckpointState, JobStatus

runner = CliRunner()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_yaml_config(tmp_path: Path) -> Path:
    """Create minimal valid YAML config for run command tests."""
    import yaml

    config = {
        "name": "test-job",
        "description": "Test job for CLI tests",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": 30},
        "prompt": {"template": "Process sheet {{ sheet_num }}."},
        "retry": {"max_retries": 2},
        "validations": [
            {
                "type": "file_exists",
                "path": "{workspace}/output-{sheet_num}.txt",
                "description": "Output file exists",
            }
        ],
    }
    config_path = tmp_path / "test-config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return config_path


@pytest.fixture
def paused_state(tmp_path: Path) -> tuple[Path, CheckpointState]:
    """Create a paused job state file on disk and return (workspace, state)."""
    now = datetime.now(UTC)
    state = CheckpointState(
        job_id="paused-job",
        job_name="Paused Job",
        status=JobStatus.PAUSED,
        total_sheets=5,
        last_completed_sheet=2,
        current_sheet=3,
        created_at=now,
        updated_at=now,
    )
    workspace = tmp_path / "paused-workspace"
    workspace.mkdir()
    state_file = workspace / "paused-job.json"
    state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))
    return workspace, state


# =============================================================================
# Run command tests
# =============================================================================


class TestRunCommandExecution:
    """Tests for the run command's execution paths."""

    def test_run_dry_run_shows_sheet_plan(self, sample_yaml_config: Path) -> None:
        """Dry run mode should display sheet plan table without executing."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert "Sheet Plan" in result.stdout

    def test_run_dry_run_json_output(self, sample_yaml_config: Path) -> None:
        """Dry run with --json should output machine-parseable JSON."""
        result = runner.invoke(
            app, ["run", str(sample_yaml_config), "--dry-run", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["job_name"] == "test-job"
        assert data["total_sheets"] == 3

    def test_run_invalid_config_shows_error(self, tmp_path: Path) -> None:
        """Invalid YAML config should produce user-friendly error."""
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("name: test\nsheet:\n  size: -1")
        result = runner.invoke(app, ["run", str(bad_config)])
        assert result.exit_code != 0

    def test_run_invalid_config_json_error(self, tmp_path: Path) -> None:
        """Invalid config with --json should produce JSON error."""
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("not_valid: yaml: [broken")
        result = runner.invoke(app, ["run", str(bad_config), "--json"])
        assert result.exit_code != 0

    def test_run_escalation_rejected_in_daemon_mode(self, sample_yaml_config: Path) -> None:
        """--escalation should be rejected since daemon is non-interactive."""
        result = runner.invoke(
            app,
            ["run", str(sample_yaml_config), "--escalation"],
        )
        assert result.exit_code == 1
        assert "not available in daemon mode" in result.stdout or "not currently supported" in result.stdout

    def test_run_escalation_json_error(self, sample_yaml_config: Path) -> None:
        """--escalation with --json should produce JSON-shaped error."""
        result = runner.invoke(
            app,
            ["run", str(sample_yaml_config), "--json", "--escalation"],
        )
        assert result.exit_code == 1
        # Rich console.print may wrap long JSON lines, so we don't
        # parse strictly — just verify the error content is present.
        assert '"error"' in result.stdout
        assert "daemon mode" in result.stdout

    def test_run_shows_config_panel(self, sample_yaml_config: Path) -> None:
        """Run command should display configuration panel."""
        result = runner.invoke(
            app, ["run", str(sample_yaml_config), "--dry-run"]
        )
        assert result.exit_code == 0
        assert "Job Configuration" in result.stdout
        assert "test-job" in result.stdout

    def test_run_nonexistent_config(self, tmp_path: Path) -> None:
        """Nonexistent config file should fail with error."""
        result = runner.invoke(app, ["run", str(tmp_path / "missing.yaml")])
        assert result.exit_code != 0

    def test_run_workspace_override(
        self, sample_yaml_config: Path, tmp_path: Path,
    ) -> None:
        """--workspace should override config workspace throughout the job."""
        custom_ws = tmp_path / "custom-workspace"
        result = runner.invoke(
            app,
            [
                "run", str(sample_yaml_config),
                "--dry-run", "--json",
                "-w", str(custom_ws),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["workspace"] == str(custom_ws.resolve())


class TestRunDaemonRequired:
    """Tests for daemon-required run command behavior."""

    def test_run_without_daemon_shows_error(self, sample_yaml_config: Path) -> None:
        """Running without a daemon should show a clear error message."""
        with patch(
            "mozart.daemon.detect.is_daemon_available",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = runner.invoke(
                app,
                ["run", str(sample_yaml_config)],
            )
        assert result.exit_code == 1
        assert "conductor is not running" in result.stdout.lower()

    def test_run_without_daemon_json_error(self, sample_yaml_config: Path) -> None:
        """Running without daemon + --json should produce JSON error."""
        with patch(
            "mozart.daemon.detect.is_daemon_available",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = runner.invoke(
                app,
                ["run", str(sample_yaml_config), "--json"],
            )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert "conductor" in data["error"].lower()

    def test_run_routes_through_daemon(self, sample_yaml_config: Path) -> None:
        """Successful daemon submission should report acceptance."""
        with patch(
            "mozart.daemon.detect.is_daemon_available",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "mozart.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {
                "job_id": "test-job-abc123",
                "status": "accepted",
                "message": "Job queued",
            }),
        ):
            result = runner.invoke(
                app,
                ["run", str(sample_yaml_config)],
            )
        assert result.exit_code == 0
        assert "test-job-abc123" in result.stdout

    def test_run_passes_start_sheet_to_daemon(self, sample_yaml_config: Path) -> None:
        """--start-sheet should be forwarded to the daemon submission."""
        captured_params: dict = {}

        async def _mock_route(_method: str, params: dict, **_kw: object) -> object:
            captured_params.update(params)
            return (True, {"job_id": "x", "status": "accepted", "message": ""})

        with patch(
            "mozart.daemon.detect.is_daemon_available",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "mozart.daemon.detect.try_daemon_route",
            side_effect=_mock_route,
        ):
            result = runner.invoke(
                app,
                ["run", str(sample_yaml_config), "--start-sheet", "5"],
            )
        assert result.exit_code == 0
        assert captured_params.get("start_sheet") == 5

    def test_run_passes_fresh_to_daemon(self, sample_yaml_config: Path) -> None:
        """--fresh should be forwarded to the daemon submission."""
        captured_params: dict = {}

        async def _mock_route(_method: str, params: dict, **_kw: object) -> object:
            captured_params.update(params)
            return (True, {"job_id": "x", "status": "accepted", "message": ""})

        with patch(
            "mozart.daemon.detect.is_daemon_available",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "mozart.daemon.detect.try_daemon_route",
            side_effect=_mock_route,
        ):
            result = runner.invoke(
                app,
                ["run", str(sample_yaml_config), "--fresh"],
            )
        assert result.exit_code == 0
        assert captured_params.get("fresh") is True


# =============================================================================
# Resume command tests
# =============================================================================


class TestResumeCommand:
    """Tests for resume command entry point validation."""

    def test_resume_nonexistent_workspace(self, tmp_path: Path) -> None:
        """Resume with nonexistent workspace should fail."""
        result = runner.invoke(
            app,
            ["resume", "my-job", "--workspace", str(tmp_path / "does_not_exist")],
        )
        assert result.exit_code == 1
        assert "Workspace not found" in result.stdout or "not found" in result.stdout.lower()

    def test_resume_job_not_found(self, tmp_path: Path) -> None:
        """Resume with job ID that doesn't exist should fail."""
        workspace = tmp_path / "empty"
        workspace.mkdir()
        result = runner.invoke(
            app,
            ["resume", "nonexistent-job", "--workspace", str(workspace)],
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()


class TestFindJobState:
    """Tests for _find_job_state helper in resume command."""

    @pytest.mark.asyncio
    async def test_find_job_state_workspace_not_found(self) -> None:
        """Should raise typer.Exit when workspace doesn't exist."""
        import typer

        from mozart.cli.commands.resume import _find_job_state

        with pytest.raises(typer.Exit):
            await _find_job_state("job-1", Path("/nonexistent"), force=False)

    @pytest.mark.asyncio
    async def test_find_job_state_job_not_found(self, tmp_path: Path) -> None:
        """Should raise typer.Exit when job state not found."""
        import typer

        from mozart.cli.commands.resume import _find_job_state

        workspace = tmp_path / "ws"
        workspace.mkdir()

        with pytest.raises(typer.Exit):
            await _find_job_state("nonexistent", workspace, force=False)

    @pytest.mark.asyncio
    async def test_find_job_state_completed_without_force(self, tmp_path: Path) -> None:
        """Completed job without --force should raise typer.Exit."""
        import typer

        from mozart.cli.commands.resume import _find_job_state

        state = CheckpointState(
            job_id="completed-job",
            job_name="Done",
            status=JobStatus.COMPLETED,
            total_sheets=3,
            last_completed_sheet=3,
        )

        with patch(
            "mozart.cli.commands.resume.require_job_state",
            new_callable=AsyncMock,
            return_value=(state, AsyncMock()),
        ), pytest.raises(typer.Exit):
            await _find_job_state("completed-job", tmp_path, force=False)

    @pytest.mark.asyncio
    async def test_find_job_state_pending_job(self, tmp_path: Path) -> None:
        """Pending job should raise typer.Exit with hint to use 'run'."""
        import typer

        from mozart.cli.commands.resume import _find_job_state

        state = CheckpointState(
            job_id="pending-job",
            job_name="Not Started",
            status=JobStatus.PENDING,
            total_sheets=3,
        )

        with patch(
            "mozart.cli.commands.resume.require_job_state",
            new_callable=AsyncMock,
            return_value=(state, AsyncMock()),
        ), pytest.raises(typer.Exit):
            await _find_job_state("pending-job", tmp_path, force=False)

    @pytest.mark.asyncio
    async def test_find_job_state_paused_job_succeeds(self) -> None:
        """Paused job should return state and backend."""
        from mozart.cli.commands.resume import _find_job_state

        state = CheckpointState(
            job_id="paused-job",
            job_name="Paused",
            status=JobStatus.PAUSED,
            total_sheets=5,
            last_completed_sheet=2,
        )
        mock_backend = AsyncMock()

        with patch(
            "mozart.cli.commands.resume.require_job_state",
            new_callable=AsyncMock,
            return_value=(state, mock_backend),
        ):
            found_state, _ = await _find_job_state(
                "paused-job", None, force=False
            )
            assert found_state.job_id == "paused-job"
            assert found_state.status == JobStatus.PAUSED


class TestReconstructConfig:
    """Tests for _reconstruct_config auto-reload priority fallback."""

    def test_priority_1_explicit_config_file(self, sample_yaml_config: Path) -> None:
        """Provided --config file should take highest priority."""
        from mozart.cli.commands.resume import _reconstruct_config

        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3,
            config_snapshot={"name": "snapshot-config"},
            config_path="/some/other/path.yaml",
        )
        config, was_reloaded = _reconstruct_config(
            state, config_file=sample_yaml_config, no_reload=False,
        )
        assert config.name == "test-job"
        assert was_reloaded is True

    def test_priority_2_auto_reload_from_config_path(self, sample_yaml_config: Path) -> None:
        """Should auto-reload from stored config_path when file exists."""
        from mozart.cli.commands.resume import _reconstruct_config

        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3,
            config_snapshot={"name": "old-snapshot"},
            config_path=str(sample_yaml_config),
        )
        config, was_reloaded = _reconstruct_config(
            state, config_file=None, no_reload=False,
        )
        assert config.name == "test-job"  # from YAML, not snapshot
        assert was_reloaded is True

    def test_priority_3_snapshot_fallback_when_file_missing(self, tmp_path: Path) -> None:
        """Should fall back to snapshot when config_path file doesn't exist."""
        from mozart.cli.commands.resume import _reconstruct_config

        snapshot = {
            "name": "snapshot-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 5, "total_items": 15},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3,
            config_snapshot=snapshot,
            config_path=str(tmp_path / "deleted.yaml"),
        )
        config, was_reloaded = _reconstruct_config(
            state, config_file=None, no_reload=False,
        )
        assert config.name == "snapshot-job"
        assert was_reloaded is False

    def test_priority_3_snapshot_fallback_when_no_config_path(self) -> None:
        """Should fall back to snapshot when no config_path stored."""
        from mozart.cli.commands.resume import _reconstruct_config

        snapshot = {
            "name": "snapshot-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 5, "total_items": 15},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3,
            config_snapshot=snapshot,
            config_path=None,
        )
        config, was_reloaded = _reconstruct_config(
            state, config_file=None, no_reload=False,
        )
        assert config.name == "snapshot-job"
        assert was_reloaded is False

    def test_no_reload_flag_skips_auto_reload(self, sample_yaml_config: Path) -> None:
        """--no-reload should skip auto-reload and use snapshot."""
        from mozart.cli.commands.resume import _reconstruct_config

        snapshot = {
            "name": "snapshot-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 5, "total_items": 15},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }
        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3,
            config_snapshot=snapshot,
            config_path=str(sample_yaml_config),
        )
        config, was_reloaded = _reconstruct_config(
            state, config_file=None, no_reload=True,
        )
        assert config.name == "snapshot-job"  # snapshot, NOT yaml file
        assert was_reloaded is False

    def test_no_config_available_raises(self) -> None:
        """Should raise typer.Exit when no config source is available."""
        import typer

        from mozart.cli.commands.resume import _reconstruct_config

        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3,
            config_snapshot=None, config_path=None,
        )
        with pytest.raises(typer.Exit):
            _reconstruct_config(state, config_file=None, no_reload=False)

    def test_explicit_config_wins_over_no_reload(self, sample_yaml_config: Path) -> None:
        """--config should win even when --no-reload is set."""
        from mozart.cli.commands.resume import _reconstruct_config

        state = CheckpointState(
            job_id="test", job_name="Test", total_sheets=3,
            config_snapshot={"name": "snapshot"},
        )
        config, was_reloaded = _reconstruct_config(
            state, config_file=sample_yaml_config, no_reload=True,
        )
        assert config.name == "test-job"  # explicit file wins
        assert was_reloaded is True


# =============================================================================
# Shared helper function tests
# =============================================================================


class TestSharedHelpers:
    """Tests for _shared.py helper functions."""

    def test_create_backend_claude_cli(self) -> None:
        """create_backend should return ClaudeCliBackend for claude_cli type."""
        from mozart.cli.commands._shared import create_backend
        from mozart.core.config import BackendConfig, JobConfig

        config = MagicMock(spec=JobConfig)
        # Use real BackendConfig to ensure all fields are available
        config.backend = BackendConfig(type="claude_cli")

        backend = create_backend(config)
        from mozart.backends.claude_cli import ClaudeCliBackend
        assert isinstance(backend, ClaudeCliBackend)

    def test_create_progress_bar_default(self) -> None:
        """create_progress_bar should return a Progress instance."""
        from rich.progress import Progress

        from mozart.cli.commands._shared import create_progress_bar

        progress = create_progress_bar()
        assert isinstance(progress, Progress)

    def test_create_progress_bar_with_exec_status(self) -> None:
        """create_progress_bar with include_exec_status should add extra column."""
        from rich.progress import Progress

        from mozart.cli.commands._shared import create_progress_bar

        progress = create_progress_bar(include_exec_status=True)
        assert isinstance(progress, Progress)
        # Should have 2 more columns than default (bullet + exec_status text)
        default = create_progress_bar(include_exec_status=False)
        assert len(progress.columns) > len(default.columns)

    def test_setup_learning_disabled(self) -> None:
        """setup_learning should return (None, None) when learning is disabled."""
        from mozart.cli.commands._shared import setup_learning

        config = MagicMock()
        config.learning.enabled = False

        outcome_store, global_store = setup_learning(config)
        assert outcome_store is None
        assert global_store is None

    def test_setup_notifications_none_config(self) -> None:
        """setup_notifications should return None when no notifications configured."""
        from mozart.cli.commands._shared import setup_notifications

        config = MagicMock()
        config.notifications = None

        result = setup_notifications(config)
        assert result is None

    def test_setup_escalation_disabled(self) -> None:
        """setup_escalation should return None when not enabled."""
        from mozart.cli.commands._shared import setup_escalation

        config = MagicMock()
        result = setup_escalation(config, enabled=False)
        assert result is None

    def test_setup_grounding_disabled(self) -> None:
        """setup_grounding should return None when grounding is disabled."""
        from mozart.cli.commands._shared import setup_grounding

        config = MagicMock()
        config.grounding.enabled = False

        result = setup_grounding(config)
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_job_completion_completed(self) -> None:
        """handle_job_completion should display summary for completed jobs."""
        from mozart.cli.commands._shared import handle_job_completion

        state = MagicMock(status=JobStatus.COMPLETED)
        summary = MagicMock()
        summary.final_status = JobStatus.COMPLETED
        summary.completed_sheets = 5
        summary.failed_sheets = 0
        summary.total_duration_seconds = 120.0

        notification_manager = AsyncMock()

        with patch("mozart.cli.commands._shared.display_run_summary"):
            await handle_job_completion(
                state=state,
                summary=summary,
                notification_manager=notification_manager,
                job_id="test-job",
                job_name="Test",
            )

        notification_manager.notify_job_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_job_completion_failed(self) -> None:
        """handle_job_completion should send failure notification for failed jobs."""
        from mozart.cli.commands._shared import handle_job_completion

        state = MagicMock(status=JobStatus.FAILED, current_sheet=3)
        summary = MagicMock()
        summary.final_status = JobStatus.FAILED

        notification_manager = AsyncMock()

        with patch("mozart.cli.commands._shared.display_run_summary"):
            await handle_job_completion(
                state=state,
                summary=summary,
                notification_manager=notification_manager,
                job_id="test-job",
                job_name="Test",
            )

        notification_manager.notify_job_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_job_completion_no_notifications(self) -> None:
        """handle_job_completion with None notification_manager should not fail."""
        from mozart.cli.commands._shared import handle_job_completion

        state = MagicMock(status=JobStatus.COMPLETED)
        summary = MagicMock()
        summary.final_status = JobStatus.COMPLETED

        with patch("mozart.cli.commands._shared.display_run_summary") as mock_display:
            await handle_job_completion(
                state=state,
                summary=summary,
                notification_manager=None,
                job_id="test-job",
                job_name="Test",
            )
        # Verify it completed and called display_run_summary
        assert mock_display.called
