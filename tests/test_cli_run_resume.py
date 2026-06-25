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

from marianne.cli import app
from marianne.core.checkpoint import CheckpointState, JobStatus

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
def sample_fleet_config(tmp_path: Path) -> Path:
    """Create a minimal valid fleet config for run command tests."""
    fleet_path = tmp_path / "fleet.yaml"
    fleet_path.write_text(
        "name: test-fleet\n"
        "type: fleet\n"
        "scores:\n"
        "  - path: a.yaml\n"
        "    group: root\n"
        "  - path: b.yaml\n"
        "    group: workers\n"
        "groups:\n"
        "  root:\n"
        "    depends_on: []\n"
        "  workers:\n"
        "    depends_on: [root]\n"
    )
    return fleet_path


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
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["job_name"] == "test-job"
        assert data["total_sheets"] == 3

    def test_run_fleet_dry_run_shows_fleet_plan(
        self,
        sample_fleet_config: Path,
    ) -> None:
        """Fleet dry-run should not parse the fleet as a score."""
        result = runner.invoke(app, ["run", str(sample_fleet_config), "--dry-run"])

        assert result.exit_code == 0
        assert "Fleet Configuration" in result.stdout
        assert "Fleet Plan" in result.stdout
        assert "test-fleet" in result.stdout

    def test_run_fleet_dry_run_json_output(
        self,
        sample_fleet_config: Path,
    ) -> None:
        """Fleet dry-run JSON should output fleet metadata."""
        result = runner.invoke(
            app,
            ["run", str(sample_fleet_config), "--dry-run", "--json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["type"] == "fleet"
        assert data["fleet_name"] == "test-fleet"
        assert data["scores"] == 2

    def test_run_fleet_submits_to_daemon(
        self,
        sample_fleet_config: Path,
    ) -> None:
        """Fleet run should route the fleet config to job.submit."""
        submitted: dict = {}

        async def fake_route(method: str, params: dict) -> tuple[bool, dict]:
            submitted["method"] = method
            submitted["params"] = params
            return True, {
                "status": "accepted",
                "job_id": "test-fleet",
                "message": "fleet queued",
            }

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                side_effect=fake_route,
            ),
        ):
            result = runner.invoke(
                app,
                ["run", str(sample_fleet_config), "--json"],
            )

        assert result.exit_code == 0
        assert submitted["method"] == "job.submit"
        assert submitted["params"]["config_path"].endswith("fleet.yaml")

    def test_run_fleet_rejects_score_specific_options(
        self,
        sample_fleet_config: Path,
    ) -> None:
        """Fleet run should be honest about unsupported score options."""
        result = runner.invoke(
            app,
            ["run", str(sample_fleet_config), "--fresh"],
        )

        assert result.exit_code == 1
        assert "do not yet support score-specific options" in result.stdout

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

    def test_run_escalation_accepted_and_forwarded(
        self, sample_yaml_config: Path
    ) -> None:
        """#361: --escalation is no longer rejected — it reaches job.submit.

        The old hard rejection ("requires interactive console prompts")
        predates marker-file resolution; escalation is non-interactive now.
        """
        from unittest.mock import AsyncMock, patch

        submitted: dict = {}

        async def fake_route(method: str, params: dict) -> tuple[bool, dict]:
            submitted["method"] = method
            submitted["params"] = params
            return True, {
                "status": "accepted",
                "job_id": "j-esc",
                "message": "queued",
            }

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                side_effect=fake_route,
            ),
        ):
            result = runner.invoke(
                app,
                ["run", str(sample_yaml_config), "--escalation", "--json"],
            )

        assert result.exit_code == 0
        assert submitted["method"] == "job.submit"
        assert submitted["params"]["escalation"] is True
        assert submitted["params"]["self_healing"] is False

    def test_run_shows_config_panel(self, sample_yaml_config: Path) -> None:
        """Run command should display configuration panel."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        assert "Score Configuration" in result.stdout
        assert "test-job" in result.stdout

    def test_run_nonexistent_config(self, tmp_path: Path) -> None:
        """Nonexistent config file should fail with error."""
        result = runner.invoke(app, ["run", str(tmp_path / "missing.yaml")])
        assert result.exit_code != 0

    def test_run_workspace_override(
        self,
        sample_yaml_config: Path,
        tmp_path: Path,
    ) -> None:
        """--workspace should override config workspace throughout the job."""
        custom_ws = tmp_path / "custom-workspace"
        result = runner.invoke(
            app,
            [
                "run",
                str(sample_yaml_config),
                "--dry-run",
                "--json",
                "-w",
                str(custom_ws),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["workspace"] == str(custom_ws.resolve())

    def test_run_shows_cost_warning_when_disabled(
        self,
        sample_yaml_config: Path,
    ) -> None:
        """When cost_limits.enabled is false, run shows a cost warning."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        assert "Cost tracking is disabled" in result.stdout

    def test_run_no_cost_warning_when_enabled(self, tmp_path: Path) -> None:
        """When cost_limits.enabled is true, no warning is shown."""
        import yaml

        config = {
            "name": "cost-enabled-job",
            "sheet": {"size": 10, "total_items": 10},
            "prompt": {"template": "Test {{ sheet_num }}."},
            "cost_limits": {
                "enabled": True,
                "max_cost_per_job": 10.0,
            },
        }
        config_path = tmp_path / "cost-enabled.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        result = runner.invoke(app, ["run", str(config_path), "--dry-run"])
        assert result.exit_code == 0
        assert "Cost tracking is disabled" not in result.stdout

    def test_run_no_cost_warning_in_json_mode(
        self,
        sample_yaml_config: Path,
    ) -> None:
        """JSON output mode suppresses the cost warning."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run", "--json"])
        assert result.exit_code == 0
        assert "Cost tracking is disabled" not in result.stdout

    def test_run_config_panel_shows_instrument(
        self,
        sample_yaml_config: Path,
    ) -> None:
        """Config panel shows instrument (not just 'Backend')."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        assert "Instrument:" in result.stdout


class TestRunDaemonRequired:
    """Tests for daemon-required run command behavior."""

    def test_run_without_daemon_shows_error(self, sample_yaml_config: Path) -> None:
        """Running without a daemon should show a clear error message."""
        with patch(
            "marianne.daemon.detect.is_daemon_available",
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
            "marianne.daemon.detect.is_daemon_available",
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
        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(
                    True,
                    {
                        "job_id": "test-job-abc123",
                        "status": "accepted",
                        "message": "Job queued",
                    },
                ),
            ),
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

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                side_effect=_mock_route,
            ),
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

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                side_effect=_mock_route,
            ),
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

    def test_resume_job_not_found(self, tmp_path: Path) -> None:
        """Resume with job ID that doesn't exist should fail."""
        # Note: --workspace flag removed in F-502
        result = runner.invoke(
            app,
            ["resume", "nonexistent-job"],
        )
        assert result.exit_code == 1
        # Either "not found" (conductor running but job missing) or
        # "not running" (conductor not available) are valid failures.
        output = result.stdout.lower()
        assert "not found" in output or "not running" in output


class TestSharedHelpers:
    """Tests for _shared.py helper functions."""

    # test_create_backend_claude_cli removed in Phase 1 — create_backend() was
    # part of the dead _shared.py cluster. Backend creation now flows through
    # the instrument registry (marianne.instruments.native_factory).

    def test_create_progress_bar_default(self) -> None:
        """create_progress_bar should return a Progress instance."""
        from rich.progress import Progress

        from marianne.cli.commands._shared import create_progress_bar

        progress = create_progress_bar()
        assert isinstance(progress, Progress)

    def test_create_progress_bar_with_exec_status(self) -> None:
        """create_progress_bar with include_exec_status should add extra column."""
        from rich.progress import Progress

        from marianne.cli.commands._shared import create_progress_bar

        progress = create_progress_bar(include_exec_status=True)
        assert isinstance(progress, Progress)
        # Should have 2 more columns than default (bullet + exec_status text)
        default = create_progress_bar(include_exec_status=False)
        assert len(progress.columns) > len(default.columns)

    # setup_learning / setup_notifications / setup_escalation / setup_grounding
    # tests removed in Phase 1 — all four helpers were part of the dead
    # _shared.py cluster that the daemon replaced via the baton adapter.
    # Equivalent logic now lives in marianne.execution.setup (setup_learning,
    # setup_notifications) and the baton's native event handling (grounding,
    # escalation).

    @pytest.mark.asyncio
    async def test_handle_job_completion_completed(self) -> None:
        """handle_job_completion should display summary for completed jobs."""
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock(status=JobStatus.COMPLETED)
        summary = MagicMock()
        summary.final_status = JobStatus.COMPLETED
        summary.completed_sheets = 5
        summary.failed_sheets = 0
        summary.total_duration_seconds = 120.0

        notification_manager = AsyncMock()

        with patch("marianne.cli.commands._shared.display_run_summary"):
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
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock(status=JobStatus.FAILED, current_sheet=3)
        summary = MagicMock()
        summary.final_status = JobStatus.FAILED

        notification_manager = AsyncMock()

        with patch("marianne.cli.commands._shared.display_run_summary"):
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
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock(status=JobStatus.COMPLETED)
        summary = MagicMock()
        summary.final_status = JobStatus.COMPLETED

        with patch("marianne.cli.commands._shared.display_run_summary") as mock_display:
            await handle_job_completion(
                state=state,
                summary=summary,
                notification_manager=None,
                job_id="test-job",
                job_name="Test",
            )
        # Verify it completed and called display_run_summary
        assert mock_display.called


# =============================================================================
# Resume error message standardization (M3 step 35)
# =============================================================================


class TestResumeErrorMessages:
    """#50: resume routes ENTIRELY through the conductor — the local
    state-gating helpers (_find_job_state / require_job_state) were dead
    code and are deleted. Resumability validation is the conductor's
    (JobManager.resume_job rejects non-resumable states); the CLI just
    surfaces the rejection (covered by TestResumeRejectedHints)."""

    def test_dead_state_helpers_removed(self) -> None:
        from marianne.cli.commands import resume as resume_mod

        assert not hasattr(resume_mod, "_find_job_state")
        assert not hasattr(resume_mod, "_reconstruct_config")
        assert not hasattr(resume_mod, "require_job_state")


class TestResumeRejectedHints:
    """Resume command should provide appropriate hints based on failure reason.

    F-073: 'not found' errors should suggest 'mzt list', not 'diagnose'.
    Known-but-unresumable scores should suggest 'diagnose'.
    """

    async def test_not_found_suggests_list(self) -> None:
        """When resume is rejected with 'not found', suggest 'mzt list'."""
        from marianne.cli.commands.resume import _resume_job

        result_dict = {
            "status": "rejected",
            "message": "Score 'nonexistent' not found",
            "job_id": "nonexistent",
        }

        import typer

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, result_dict),
            ),
            patch("marianne.cli.commands.resume.output_error") as mock_err,
            pytest.raises(typer.Exit),
        ):
            await _resume_job("nonexistent", None, False, False, False, False, False)

        mock_err.assert_called_once()
        call_args = mock_err.call_args
        hints = call_args[1].get("hints", [])
        hint_text = " ".join(str(h) for h in hints)
        # Should suggest 'mzt list'
        assert "mzt list" in hint_text
        # "not found" should NOT have 'diagnose' as a hint
        assert "diagnose" not in hint_text

    async def test_not_resumable_suggests_diagnose(self) -> None:
        """When resume is rejected for a known score, suggest 'diagnose'."""
        from marianne.cli.commands.resume import _resume_job

        result_dict = {
            "status": "rejected",
            "message": (
                "Score 'my-job' is completed, "
                "only PAUSED, FAILED, or CANCELLED scores can be resumed"
            ),
            "job_id": "my-job",
        }

        import typer

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, result_dict),
            ),
            patch("marianne.cli.commands.resume.output_error") as mock_err,
            pytest.raises(typer.Exit),
        ):
            await _resume_job("my-job", None, False, False, False, False, False)

        mock_err.assert_called_once()
        call_args = mock_err.call_args
        hints = call_args[1].get("hints", [])
        hint_text = " ".join(str(h) for h in hints)
        # Known score should suggest diagnose
        assert "diagnose" in hint_text
        # Should also suggest list as secondary
        assert "list" in hint_text


class TestResumeScoreTerminology:
    """Resume command should use 'Score' terminology, not 'Job' (F-072)."""

    async def test_success_message_uses_score(self) -> None:
        """Accepted resume should say 'score', not 'job'."""
        from marianne.cli.commands.resume import _resume_job

        result_dict = {
            "status": "accepted",
            "message": "Resumed from sheet 3",
            "job_id": "my-score",
        }

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, result_dict),
            ),
            patch("marianne.cli.commands.resume.console") as mock_console,
        ):
            await _resume_job("my-score", None, False, False, False, False, False)

        # Check that the success message uses "score" not "job"
        all_prints = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "score" in all_prints.lower() or "my-score" in all_prints
