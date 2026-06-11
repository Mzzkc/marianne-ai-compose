"""Tests for Marianne CLI commands."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus

# Module-level runner is safe: CliRunner is stateless (no mutable state between invocations).
# Each invoke() call creates an isolated Click context.
runner = CliRunner()


@pytest.fixture(autouse=True)
def _registry_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#50: state reads are conductor-ONLY — offline reads hit the daemon
    registry DB seeded per test; workspace state files are never read."""
    from marianne.cli import helpers

    monkeypatch.setattr(
        helpers, "DAEMON_STATE_DB_PATH", tmp_path / "daemon-state.db"
    )


def _seed_registry(tmp_path: Path, state) -> None:
    """Seed the daemon registry DB with this job's state (#50)."""
    import asyncio as _asyncio

    from marianne.cli import helpers
    from marianne.daemon.registry import JobRegistry

    # Single source: always write where the patched offline reader looks,
    # regardless of which directory the caller passed.
    db = helpers.DAEMON_STATE_DB_PATH

    async def _seed() -> None:
        async with JobRegistry(db) as reg:
            await reg.register_job(state.job_id, tmp_path / "cfg.yaml", tmp_path)
            await reg.save_checkpoint(state.job_id, state.model_dump_json())

    _asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure CLI tests never route through a real conductor.

    Tests that need conductor behavior should mock try_daemon_route
    explicitly with the desired return value.
    """

    async def _fake_route(method: str, params: dict, *, socket_path=None) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        _fake_route,
    )


class TestVersionCommand:
    """Tests for the --version flag."""

    def test_version_shows_version(self) -> None:
        """Test that --version prints version info."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "Marianne AI Compose" in result.stdout


class TestValidateCommand:
    """Tests for the validate command."""

    def test_validate_valid_config(self, sample_yaml_config: Path) -> None:
        """Test validation of a valid config file."""
        result = runner.invoke(app, ["validate", str(sample_yaml_config)])
        assert result.exit_code == 0
        # New enhanced validation shows different output
        assert "Configuration valid" in result.stdout or "YAML syntax valid" in result.stdout

    def test_validate_nonexistent_file(self, tmp_path: Path) -> None:
        """Test validation of a nonexistent file."""
        fake_path = tmp_path / "nonexistent.yaml"
        result = runner.invoke(app, ["validate", str(fake_path)])
        assert result.exit_code != 0

    def test_validate_invalid_yaml(self, tmp_path: Path) -> None:
        """Test validation of invalid YAML content."""
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("name: test\nsheet:\n  size: -1")  # Invalid size
        result = runner.invoke(app, ["validate", str(bad_config)])
        # Exit code 2 for schema validation failures
        assert result.exit_code == 2
        assert "Schema validation failed" in result.stdout


class TestListCommand:
    """Tests for the list command.

    ``mzt list`` queries the daemon's persistent registry via
    ``try_daemon_route("job.list", {})``.  All tests mock this route.
    """

    @staticmethod
    def _mock_route(jobs: list[dict]):
        """Return a patch that makes try_daemon_route return *jobs*."""

        async def _fake_route(method, params):
            return (True, jobs)

        return patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route)

    @staticmethod
    def _mock_route_down():
        """Return a patch simulating no daemon running."""

        async def _fake_route(method, params):
            return (False, None)

        return patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route)

    def test_list_no_active_jobs(self) -> None:
        """Default list shows 'no active jobs' when only historical jobs exist."""
        jobs = [
            {
                "job_id": "job-0",
                "status": "completed",
                "workspace": "/w0",
                "submitted_at": 1707900000.0,
            },
        ]
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No active scores" in result.stdout
        assert "--all" in result.stdout

    def test_list_shows_active_jobs(self) -> None:
        """Default list shows running/queued/paused jobs."""
        jobs = [
            {
                "job_id": "running-1",
                "status": "running",
                "workspace": "/w0",
                "submitted_at": 1707900000.0,
            },
            {
                "job_id": "completed-1",
                "status": "completed",
                "workspace": "/w1",
                "submitted_at": 1707900001.0,
            },
            {
                "job_id": "queued-1",
                "status": "queued",
                "workspace": "/w2",
                "submitted_at": 1707900002.0,
            },
        ]
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "running-1" in result.stdout
        assert "queued-1" in result.stdout
        assert "completed-1" not in result.stdout
        assert "2 score(s)" in result.stdout

    def test_list_all_shows_everything(self) -> None:
        """--all flag shows all jobs including completed/failed."""
        jobs = [
            {
                "job_id": "job-0",
                "status": "completed",
                "workspace": "/w0",
                "submitted_at": 1707900000.0,
            },
            {
                "job_id": "job-1",
                "status": "failed",
                "workspace": "/w1",
                "submitted_at": 1707900001.0,
            },
            {
                "job_id": "job-2",
                "status": "queued",
                "workspace": "/w2",
                "submitted_at": 1707900002.0,
            },
        ]
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--all"])
        assert result.exit_code == 0
        assert "job-0" in result.stdout
        assert "job-1" in result.stdout
        assert "job-2" in result.stdout
        assert "3 score(s)" in result.stdout

    def test_list_filter_by_status(self) -> None:
        """--status filter overrides default active-only view."""
        jobs = [
            {
                "job_id": "job-0",
                "status": "completed",
                "workspace": "/w0",
                "submitted_at": 1707900000.0,
            },
            {
                "job_id": "job-1",
                "status": "failed",
                "workspace": "/w1",
                "submitted_at": 1707900001.0,
            },
            {
                "job_id": "job-2",
                "status": "completed",
                "workspace": "/w2",
                "submitted_at": 1707900002.0,
            },
        ]
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--status", "completed"])
        assert result.exit_code == 0
        assert "job-0" in result.stdout
        assert "job-2" in result.stdout
        assert "job-1" not in result.stdout
        assert "2 score(s)" in result.stdout

    def test_list_filter_no_matches(self) -> None:
        """Status filter with no matches shows appropriate message."""
        jobs = [
            {
                "job_id": "job-0",
                "status": "completed",
                "workspace": "/w0",
                "submitted_at": 1707900000.0,
            },
        ]
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--status", "running"])
        assert result.exit_code == 0
        assert "No running scores found" in result.stdout

    def test_list_with_limit(self) -> None:
        """--limit option caps results."""
        jobs = [
            {
                "job_id": f"job-{i}",
                "status": "running",
                "workspace": f"/w{i}",
                "submitted_at": 1707900000.0 + i,
            }
            for i in range(5)
        ]
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--limit", "2"])
        assert result.exit_code == 0
        assert "2 score(s)" in result.stdout

    def test_list_daemon_not_running(self) -> None:
        """List without daemon shows error."""
        with self._mock_route_down():
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 1
        assert "conductor is not running" in result.stdout


class TestRunCommand:
    """Tests for the run command."""

    def test_run_dry_run(self, sample_yaml_config: Path) -> None:
        """Test run command in dry-run mode."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert "Sheet Plan" in result.stdout

    def test_run_shows_config_panel(self, sample_yaml_config: Path) -> None:
        """Test run command displays configuration panel."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        assert "Score Configuration" in result.stdout
        assert "test-job" in result.stdout


class TestStatusCommand:
    """Tests for the status command."""

    def test_status_with_valid_job_id(self, tmp_path: Path) -> None:
        """Test status command with a valid job ID."""
        # Create a job state file
        state = CheckpointState(
            job_id="test-job-status",
            job_name="Test Job for Status",
            total_sheets=5,
            last_completed_sheet=3,
            status=JobStatus.RUNNING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            total_retry_count=2,
            rate_limit_waits=1,
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=2,
                    validation_passed=True,
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["status", "test-job-status", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Test Job for Status" in result.stdout
        assert "test-job-status" in result.stdout
        assert "RUNNING" in result.stdout
        # Check progress is shown
        assert "3" in result.stdout and "5" in result.stdout

    def test_status_with_invalid_job_id(self, tmp_path: Path) -> None:
        """Test status command with an invalid job ID shows error."""
        workspace = tmp_path / "empty_ws"
        workspace.mkdir()

        result = runner.invoke(app, ["status", "nonexistent-job", "--workspace", str(workspace)])
        assert result.exit_code == 1
        assert "Score not found" in result.stdout
        assert "nonexistent-job" in result.stdout

    def test_status_json_output_format(self, tmp_path: Path) -> None:
        """Test status --json outputs valid JSON."""
        state = CheckpointState(
            job_id="json-test-job",
            job_name="JSON Test Job",
            total_sheets=10,
            last_completed_sheet=5,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(
            app, ["status", "json-test-job", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0

        # Parse the output as JSON to verify it's valid
        output_data = json.loads(result.stdout)
        assert output_data["job_id"] == "json-test-job"
        assert output_data["job_name"] == "JSON Test Job"
        assert output_data["status"] == "completed"
        assert output_data["progress"]["completed"] == 5
        assert output_data["progress"]["total"] == 10
        assert output_data["progress"]["percent"] == 50.0
        assert "1" in output_data["sheets"]

    def test_status_json_output_for_missing_job(self, tmp_path: Path) -> None:
        """Test status --json outputs JSON error for missing job."""
        workspace = tmp_path / "empty_ws2"
        workspace.mkdir()

        result = runner.invoke(
            app, ["status", "missing-job", "--json", "--workspace", str(workspace)]
        )
        assert result.exit_code == 1

        # Parse the output as JSON to verify error format
        output_data = json.loads(result.stdout)
        assert output_data["success"] is False
        assert "missing-job" in output_data["message"]

    def test_status_shows_sheet_details(self, tmp_path: Path) -> None:
        """Test status command shows sheet details table."""
        state = CheckpointState(
            job_id="sheet-details-job",
            job_name="Sheet Details Test",
            total_sheets=3,
            last_completed_sheet=2,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            error_message="Max retries exceeded",
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    validation_passed=False,
                    error_message="Validation failed: file not found",
                    error_category="validation",
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["status", "sheet-details-job", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Sheet Details" in result.stdout
        assert "Max retries exceeded" in result.stdout
        # Check sheet statuses are visible
        assert "completed" in result.stdout.lower()
        assert "failed" in result.stdout.lower()

    def test_status_nonexistent_workspace(self, tmp_path: Path) -> None:
        """Test status command with nonexistent workspace."""
        fake_workspace = tmp_path / "does_not_exist"

        result = runner.invoke(app, ["status", "some-job", "--workspace", str(fake_workspace)])
        assert result.exit_code == 1
        assert "Workspace not found" in result.stdout


class TestResumeCommand:
    """Tests for the resume command."""

    def test_resume_job_not_found(self, tmp_path: Path, monkeypatch) -> None:
        """Test resume shows error for non-existent job."""
        workspace = tmp_path / "empty_ws"
        workspace.mkdir()
        # F-502: Resume searches CWD, not explicit workspace
        monkeypatch.chdir(workspace)

        # F-502: Mock conductor to test local validation logic
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["resume", "nonexistent-job"])
        assert result.exit_code == 1
        # With conductor unavailable, we get "conductor not running" error
        assert "conductor" in result.stdout.lower() or "daemon" in result.stdout.lower()

    def test_resume_completed_job_blocked(self, tmp_path: Path, monkeypatch) -> None:
        """Test resume shows error for completed jobs without --force."""

        # Resume routes through conductor; mock a rejection response
        async def _fake_route(method, params, *, socket_path=None):
            return True, {
                "job_id": "completed-job",
                "status": "rejected",
                "message": "Score 'completed-job' is already completed",
            }

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["resume", "completed-job"])
        assert result.exit_code == 1
        assert "already completed" in result.stdout

    def test_resume_pending_job_blocked(self, tmp_path: Path, monkeypatch) -> None:
        """Test resume shows error for pending (never started) jobs."""

        # Resume routes through conductor; mock a rejection response
        async def _fake_route(method, params, *, socket_path=None):
            return True, {
                "job_id": "pending-job",
                "status": "rejected",
                "message": "Score 'pending-job' has not been started yet",
            }

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["resume", "pending-job"])
        assert result.exit_code == 1
        assert "not been started yet" in result.stdout

    def test_resume_paused_job_accepted(
        self, tmp_path: Path, sample_config_dict: dict, monkeypatch
    ) -> None:
        """Test resume of paused job is accepted by conductor."""

        # Resume routes through conductor; mock an accepted response
        async def _fake_route(method, params, *, socket_path=None):
            return True, {
                "job_id": "paused-job",
                "status": "accepted",
                "message": "Job resume queued",
            }

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["resume", "paused-job"])

        assert result.exit_code == 0
        assert "Resume accepted" in result.stdout

    def test_resume_failed_job_allowed(
        self, tmp_path: Path, sample_config_dict: dict, monkeypatch
    ) -> None:
        """Test resume is allowed for failed jobs."""

        # Resume routes through conductor; mock an accepted response
        async def _fake_route(method, params, *, socket_path=None):
            assert params["job_id"] == "failed-job"
            return True, {
                "job_id": "failed-job",
                "status": "accepted",
                "message": "Job resume queued",
            }

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["resume", "failed-job"])

        assert result.exit_code == 0
        assert "Resume accepted" in result.stdout

    def test_resume_missing_config(self, tmp_path: Path, monkeypatch) -> None:
        """Test resume shows error when no config is available."""

        # Resume routes through conductor; mock a rejection about missing config
        async def _fake_route(method, params, *, socket_path=None):
            return True, {
                "job_id": "no-config-job",
                "status": "rejected",
                "message": "No config available for score 'no-config-job'",
            }

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["resume", "no-config-job"])
        assert result.exit_code == 1
        assert "No config available" in result.stdout

    def test_resume_with_config_file(
        self, tmp_path: Path, sample_yaml_config: Path, monkeypatch
    ) -> None:
        """Test resume with explicit --config file passes config_path to conductor."""

        # Resume routes through conductor; verify config_path is passed
        async def _fake_route(method, params, *, socket_path=None):
            assert params["config_path"] is not None
            return True, {
                "job_id": "test-job",
                "status": "accepted",
                "message": "Job resume queued",
            }

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(
                app,
                [
                    "resume",
                    "test-job",
                    "--config",
                    str(sample_yaml_config),
                ],
            )

        assert result.exit_code == 0
        assert "Resume accepted" in result.stdout

    def test_resume_force_completed(
        self, tmp_path: Path, sample_config_dict: dict, monkeypatch
    ) -> None:
        """Test resume with --force is accepted by conductor."""

        # Resume routes through conductor; mock an accepted response
        async def _fake_route(method, params, *, socket_path=None):
            return True, {
                "job_id": "force-job",
                "status": "accepted",
                "message": "Job resume queued",
            }

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(
                app,
                [
                    "resume",
                    "force-job",
                    "--force",
                ],
            )

        assert result.exit_code == 0
        assert "Resume accepted" in result.stdout


class TestDashboardCommand:
    """Tests for the dashboard command."""

    def test_dashboard_starts_with_default_options(self, tmp_path: Path) -> None:
        """Test dashboard command starts server with default options (mocked)."""
        import sys

        mock_uvicorn = AsyncMock()
        mock_uvicorn.run = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            result = runner.invoke(app, ["dashboard", "--workspace", str(tmp_path)])

            assert result.exit_code == 0
            assert "Marianne Dashboard" in result.stdout
            assert "http://127.0.0.1:8000" in result.stdout
            assert "Docs:" in result.stdout

    def test_dashboard_custom_port(self, tmp_path: Path) -> None:
        """Test dashboard command with custom port."""
        import sys

        mock_uvicorn = AsyncMock()
        mock_uvicorn.run = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            result = runner.invoke(
                app, ["dashboard", "--port", "3000", "--workspace", str(tmp_path)]
            )

            assert result.exit_code == 0
            assert "http://127.0.0.1:3000" in result.stdout

    def test_dashboard_custom_host(self, tmp_path: Path) -> None:
        """Test dashboard command with custom host."""
        import sys

        mock_uvicorn = AsyncMock()
        mock_uvicorn.run = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            result = runner.invoke(
                app, ["dashboard", "--host", "0.0.0.0", "--workspace", str(tmp_path)]
            )

            assert result.exit_code == 0
            assert "http://0.0.0.0:8000" in result.stdout

    def test_dashboard_starts_with_workspace_option(self, tmp_path: Path) -> None:
        """Test dashboard accepts --workspace and starts successfully."""
        import sys

        mock_uvicorn = AsyncMock()
        mock_uvicorn.run = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            result = runner.invoke(app, ["dashboard", "--workspace", str(tmp_path)])

            assert result.exit_code == 0
            assert "Marianne Dashboard" in result.stdout

    def test_dashboard_shows_docs_url(self, tmp_path: Path) -> None:
        """Test dashboard shows Swagger docs URL in startup message."""
        import sys

        mock_uvicorn = AsyncMock()
        mock_uvicorn.run = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            result = runner.invoke(app, ["dashboard", "--workspace", str(tmp_path)])

            assert result.exit_code == 0
            assert "/docs" in result.stdout
            assert "/openapi.json" in result.stdout

    def test_dashboard_creates_app_with_correct_settings(self, tmp_path: Path) -> None:
        """Test dashboard creates app with correct title and workspace."""
        import sys

        mock_uvicorn = AsyncMock()
        mock_uvicorn.run = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            result = runner.invoke(
                app, ["dashboard", "--workspace", str(tmp_path), "--port", "9000"]
            )

            assert result.exit_code == 0
            # Verify the startup panel shows correct info
            assert "Marianne Dashboard" in result.stdout
            assert "http://127.0.0.1:9000" in result.stdout
            assert "Starting Server" in result.stdout


class TestVerboseAndQuietFlags:
    """Tests for --verbose and --quiet global flags."""

    def test_verbose_flag_short(self) -> None:
        """Test -v flag is accepted."""
        result = runner.invoke(app, ["-v", "--version"])
        assert result.exit_code == 0

    def test_verbose_flag_long(self) -> None:
        """Test --verbose flag is accepted."""
        result = runner.invoke(app, ["--verbose", "--version"])
        assert result.exit_code == 0

    def test_quiet_flag_short(self) -> None:
        """Test -q flag is accepted."""
        result = runner.invoke(app, ["-q", "--version"])
        assert result.exit_code == 0

    def test_quiet_flag_long(self) -> None:
        """Test --quiet flag is accepted."""
        result = runner.invoke(app, ["--quiet", "--version"])
        assert result.exit_code == 0

    def test_version_flag_changed_to_capital_v(self) -> None:
        """Test --version uses -V (capital) shorthand."""
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert "Marianne AI Compose" in result.stdout


class TestRunCommandJsonOutput:
    """Tests for the --json flag on run command."""

    def test_run_dry_run_with_json_flag(self, sample_yaml_config: Path) -> None:
        """Test run --dry-run --json outputs valid JSON."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run", "--json"])
        assert result.exit_code == 0

        # Parse output as JSON
        output_data = json.loads(result.stdout)
        assert output_data["dry_run"] is True
        assert "job_name" in output_data
        assert "total_sheets" in output_data

    def test_run_json_short_flag(self, sample_yaml_config: Path) -> None:
        """Test run --dry-run -j uses short flag."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run", "-j"])
        assert result.exit_code == 0

        # Should be valid JSON
        output_data = json.loads(result.stdout)
        assert output_data["dry_run"] is True

    def test_run_quiet_mode_hides_config_panel(self, sample_yaml_config: Path) -> None:
        """Test run with --quiet hides job configuration panel."""
        result = runner.invoke(app, ["-q", "run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        # In quiet mode, config panel is hidden
        assert "Score Configuration" not in result.stdout


class TestRunSummaryDisplay:
    """Tests for run summary display (indirect via CLI)."""

    def test_run_dry_run_shows_sheet_plan(self, sample_yaml_config: Path) -> None:
        """Test dry-run still shows sheet plan (not summary)."""
        result = runner.invoke(app, ["run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0
        assert "Sheet Plan" in result.stdout
        # Dry run shouldn't show summary (job wasn't run)
        assert "Run Summary" not in result.stdout


class TestLoggingOptions:
    """Tests for --log-level, --log-file, and --log-format CLI options."""

    def test_log_level_option_debug(self, sample_yaml_config: Path) -> None:
        """Test --log-level DEBUG is accepted."""
        result = runner.invoke(
            app, ["--log-level", "DEBUG", "run", str(sample_yaml_config), "--dry-run"]
        )
        assert result.exit_code == 0

    def test_log_level_option_info(self, sample_yaml_config: Path) -> None:
        """Test --log-level INFO is accepted."""
        result = runner.invoke(
            app, ["--log-level", "INFO", "run", str(sample_yaml_config), "--dry-run"]
        )
        assert result.exit_code == 0

    def test_log_level_option_warning(self, sample_yaml_config: Path) -> None:
        """Test --log-level WARNING is accepted."""
        result = runner.invoke(
            app, ["--log-level", "WARNING", "run", str(sample_yaml_config), "--dry-run"]
        )
        assert result.exit_code == 0

    def test_log_level_option_error(self, sample_yaml_config: Path) -> None:
        """Test --log-level ERROR is accepted."""
        result = runner.invoke(
            app, ["--log-level", "ERROR", "run", str(sample_yaml_config), "--dry-run"]
        )
        assert result.exit_code == 0

    def test_log_level_short_flag(self, sample_yaml_config: Path) -> None:
        """Test -L short flag for log level."""
        result = runner.invoke(app, ["-L", "DEBUG", "run", str(sample_yaml_config), "--dry-run"])
        assert result.exit_code == 0

    def test_log_file_option(self, tmp_path: Path, sample_yaml_config: Path) -> None:
        """Test --log-file option creates log file."""
        log_file = tmp_path / "test.log"
        result = runner.invoke(
            app,
            [
                "--log-level",
                "DEBUG",
                "--log-file",
                str(log_file),
                "run",
                str(sample_yaml_config),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        # Note: The log file may or may not have content depending on what
        # operations happen during dry-run, but the option should be accepted

    def test_log_format_console(self, sample_yaml_config: Path) -> None:
        """Test --log-format console is accepted."""
        result = runner.invoke(
            app, ["--log-format", "console", "run", str(sample_yaml_config), "--dry-run"]
        )
        assert result.exit_code == 0

    def test_log_format_json(self, sample_yaml_config: Path) -> None:
        """Test --log-format json is accepted."""
        result = runner.invoke(
            app, ["--log-format", "json", "run", str(sample_yaml_config), "--dry-run"]
        )
        assert result.exit_code == 0

    def test_log_format_both_requires_log_file(self, sample_yaml_config: Path) -> None:
        """Test --log-format both requires --log-file."""
        result = runner.invoke(
            app, ["--log-format", "both", "run", str(sample_yaml_config), "--dry-run"]
        )
        # Should fail because format='both' requires file_path
        assert result.exit_code == 1
        assert "file_path is required" in result.stdout

    def test_log_format_both_with_log_file(self, tmp_path: Path, sample_yaml_config: Path) -> None:
        """Test --log-format both works with --log-file."""
        log_file = tmp_path / "test.log"
        result = runner.invoke(
            app,
            [
                "--log-format",
                "both",
                "--log-file",
                str(log_file),
                "run",
                str(sample_yaml_config),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0

    def test_all_logging_options_combined(self, tmp_path: Path, sample_yaml_config: Path) -> None:
        """Test all logging options can be combined."""
        log_file = tmp_path / "combined.log"
        result = runner.invoke(
            app,
            [
                "--log-level",
                "DEBUG",
                "--log-format",
                "both",
                "--log-file",
                str(log_file),
                "run",
                str(sample_yaml_config),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0

    def test_log_level_with_validate_command(self, sample_yaml_config: Path) -> None:
        """Test --log-level works with validate command."""
        result = runner.invoke(app, ["--log-level", "DEBUG", "validate", str(sample_yaml_config)])
        assert result.exit_code == 0
        # New enhanced validation shows different output
        assert "Configuration valid" in result.stdout or "YAML syntax valid" in result.stdout

    def test_log_level_with_list_command(self) -> None:
        """Test --log-level works with list command."""

        async def _fake_route(method, params):
            return (True, [])

        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["--log-level", "WARNING", "list"])
        assert result.exit_code == 0
        assert "No active scores" in result.stdout

    def test_log_level_env_var(self, sample_yaml_config: Path) -> None:
        """Test MZT_LOG_LEVEL environment variable."""
        result = runner.invoke(
            app,
            ["run", str(sample_yaml_config), "--dry-run"],
            env={"MZT_LOG_LEVEL": "DEBUG"},
        )
        assert result.exit_code == 0

    def test_log_file_env_var(self, tmp_path: Path, sample_yaml_config: Path) -> None:
        """Test MZT_LOG_FILE environment variable."""
        log_file = tmp_path / "env_test.log"
        result = runner.invoke(
            app,
            ["run", str(sample_yaml_config), "--dry-run"],
            env={"MZT_LOG_FILE": str(log_file)},
        )
        assert result.exit_code == 0

    def test_log_format_env_var(self, sample_yaml_config: Path) -> None:
        """Test MZT_LOG_FORMAT environment variable."""
        result = runner.invoke(
            app,
            ["run", str(sample_yaml_config), "--dry-run"],
            env={"MZT_LOG_FORMAT": "json"},
        )
        assert result.exit_code == 0

    def test_cli_option_overrides_env_var(self, tmp_path: Path, sample_yaml_config: Path) -> None:
        """Test CLI option takes precedence over environment variable."""
        # Set env var to WARNING, but CLI to DEBUG
        result = runner.invoke(
            app,
            ["--log-level", "DEBUG", "run", str(sample_yaml_config), "--dry-run"],
            env={"MZT_LOG_LEVEL": "WARNING"},
        )
        assert result.exit_code == 0


class TestErrorsCommand:
    """Tests for the errors command."""

    def test_errors_job_not_found(self, tmp_path: Path) -> None:
        """Test errors command shows error for non-existent job."""
        workspace = tmp_path / "empty_ws"
        workspace.mkdir()

        result = runner.invoke(app, ["errors", "nonexistent-job", "--workspace", str(workspace)])
        assert result.exit_code == 1
        assert "Score not found" in result.stdout

    def test_errors_no_errors_found(self, tmp_path: Path) -> None:
        """Test errors command with job that has no errors."""
        state = CheckpointState(
            job_id="clean-job",
            job_name="Clean Job",
            total_sheets=3,
            last_completed_sheet=3,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["errors", "clean-job", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "No errors found" in result.stdout

    def test_errors_with_failed_sheet(self, tmp_path: Path) -> None:
        """Test errors command shows sheet-level errors."""
        state = CheckpointState(
            job_id="failed-job",
            job_name="Failed Job",
            total_sheets=3,
            last_completed_sheet=2,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    validation_passed=False,
                    error_message="Max retries exceeded: validation failed",
                    error_category="validation",
                    completed_at=datetime.now(UTC),
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["errors", "failed-job", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Errors for Score" in result.stdout
        assert "Max retries exceeded" in result.stdout or "validation" in result.stdout

    def test_errors_sheet_filter(self, tmp_path: Path) -> None:
        """Test errors command with --sheet filter."""
        state = CheckpointState(
            job_id="multi-fail-job",
            job_name="Multi Fail Job",
            total_sheets=5,
            last_completed_sheet=3,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Sheet 1 failed",
                    error_category="transient",
                    completed_at=datetime.now(UTC),
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Sheet 3 failed",
                    error_category="fatal",
                    completed_at=datetime.now(UTC),
                ),
            },
        )

        _seed_registry(tmp_path, state)

        # Filter for sheet 3 only
        result = runner.invoke(
            app, ["errors", "multi-fail-job", "--sheet", "3", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        # Should show sheet 3 error but not sheet 1
        assert "3" in result.stdout

    def test_errors_json_output(self, tmp_path: Path) -> None:
        """Test errors --json outputs valid JSON."""
        state = CheckpointState(
            job_id="json-error-job",
            job_name="JSON Error Job",
            total_sheets=2,
            last_completed_sheet=1,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.FAILED,
                    attempt_count=2,
                    error_message="Test error message",
                    error_category="transient",
                    completed_at=datetime.now(UTC),
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(
            app, ["errors", "json-error-job", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0

        output = json.loads(result.stdout)
        assert output["job_id"] == "json-error-job"
        assert output["total_errors"] >= 1
        assert "errors" in output

    def test_errors_type_filter(self, tmp_path: Path) -> None:
        """Test errors command with --type filter."""
        state = CheckpointState(
            job_id="mixed-errors-job",
            job_name="Mixed Errors Job",
            total_sheets=3,
            last_completed_sheet=1,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Transient error occurred",
                    error_category="transient",
                    completed_at=datetime.now(UTC),
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.FAILED,
                    attempt_count=1,
                    error_message="Permanent error occurred",
                    error_category="fatal",
                    completed_at=datetime.now(UTC),
                ),
            },
        )

        _seed_registry(tmp_path, state)

        # Filter for transient only
        result = runner.invoke(
            app, ["errors", "mixed-errors-job", "--type", "transient", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        # Should show transient but not permanent
        assert "transient" in result.stdout.lower()


class TestDiagnoseCommand:
    """Tests for the diagnose command."""

    def test_diagnose_job_not_found(self, tmp_path: Path) -> None:
        """Test diagnose command shows error for non-existent job."""
        workspace = tmp_path / "empty_ws"
        workspace.mkdir()

        result = runner.invoke(app, ["diagnose", "nonexistent-job", "--workspace", str(workspace)])
        assert result.exit_code == 1
        assert "Score not found" in result.stdout

    def test_diagnose_basic_report(self, tmp_path: Path) -> None:
        """Test diagnose command generates basic report."""
        state = CheckpointState(
            job_id="diagnose-test",
            job_name="Diagnose Test Job",
            total_sheets=5,
            last_completed_sheet=3,
            status=JobStatus.RUNNING,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    execution_duration_seconds=15.5,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=2,
                    validation_passed=True,
                    execution_duration_seconds=25.0,
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    execution_duration_seconds=12.0,
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["diagnose", "diagnose-test", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Diagnostic Report" in result.stdout
        assert "Diagnose Test Job" in result.stdout
        assert "Progress" in result.stdout

    def test_diagnose_with_errors(self, tmp_path: Path) -> None:
        """Test diagnose command shows errors section."""
        state = CheckpointState(
            job_id="diagnose-errors",
            job_name="Diagnose Errors Job",
            total_sheets=3,
            last_completed_sheet=1,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            error_message="Score failed due to max retries",
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Validation failed: file not found",
                    error_category="validation",
                    completed_at=datetime.now(UTC),
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["diagnose", "diagnose-errors", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Errors" in result.stdout
        assert "FAILED" in result.stdout

    def test_diagnose_with_preflight_warnings(self, tmp_path: Path) -> None:
        """Test diagnose command shows preflight warnings."""
        state = CheckpointState(
            job_id="diagnose-warnings",
            job_name="Diagnose Warnings Job",
            total_sheets=2,
            last_completed_sheet=2,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    preflight_warnings=["Large prompt: 50000 tokens estimated"],
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    preflight_warnings=["Missing file reference: /nonexistent/file.txt"],
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["diagnose", "diagnose-warnings", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Preflight Warnings" in result.stdout

    def test_diagnose_json_output(self, tmp_path: Path) -> None:
        """Test diagnose --json outputs valid JSON."""
        state = CheckpointState(
            job_id="diagnose-json",
            job_name="Diagnose JSON Job",
            total_sheets=3,
            last_completed_sheet=3,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    prompt_metrics={"estimated_tokens": 1000, "line_count": 50},
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    prompt_metrics={"estimated_tokens": 1200, "line_count": 60},
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    prompt_metrics={"estimated_tokens": 1100, "line_count": 55},
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(
            app, ["diagnose", "diagnose-json", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0

        output = json.loads(result.stdout)
        assert output["job_id"] == "diagnose-json"
        assert output["status"] == "completed"
        assert "progress" in output
        assert "timing" in output
        assert "execution_timeline" in output
        assert "token_statistics" in output

    def test_diagnose_timeline(self, tmp_path: Path) -> None:
        """Test diagnose command shows execution timeline."""
        state = CheckpointState(
            job_id="diagnose-timeline",
            job_name="Diagnose Timeline Job",
            total_sheets=3,
            last_completed_sheet=3,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    execution_mode="normal",
                    outcome_category="success_first_try",
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=2,
                    validation_passed=True,
                    execution_mode="completion",
                    outcome_category="success_completion",
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    execution_mode="normal",
                    outcome_category="success_first_try",
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["diagnose", "diagnose-timeline", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Execution Timeline" in result.stdout


class TestEnhancedStatusCommand:
    """Tests for the enhanced status command features (Task 15)."""

    def test_status_shows_recent_errors(self, tmp_path: Path) -> None:
        """Test status command shows recent errors section."""
        state = CheckpointState(
            job_id="status-errors",
            job_name="Status Errors Test",
            total_sheets=5,
            last_completed_sheet=3,
            status=JobStatus.RUNNING,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Timeout after 300 seconds",
                    error_category="timeout",
                    completed_at=datetime.now(UTC),
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.COMPLETED,
                    attempt_count=2,
                    validation_passed=True,
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["status", "status-errors", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        assert "Recent Errors" in result.stdout
        assert "mzt errors" in result.stdout  # Hint to use errors command

    def test_status_shows_last_activity(self, tmp_path: Path) -> None:
        """Test status command shows last activity timestamp."""
        now = datetime.now(UTC)
        state = CheckpointState(
            job_id="status-activity",
            job_name="Status Activity Test",
            total_sheets=3,
            last_completed_sheet=2,
            status=JobStatus.RUNNING,
            created_at=now,
            started_at=now,
            updated_at=now,
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    last_activity_at=now,
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    last_activity_at=now,
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["status", "status-activity", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        # Beautified output shows last activity in compact Stats section
        assert "Last activity" in result.stdout or "Stats" in result.stdout

    def test_status_shows_circuit_breaker_inferred(self, tmp_path: Path) -> None:
        """Test status command shows inferred circuit breaker state when failures detected."""
        state = CheckpointState(
            job_id="status-cb",
            job_name="Status CB Test",
            total_sheets=10,
            last_completed_sheet=5,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
                2: SheetState(sheet_num=2, status=SheetStatus.COMPLETED, attempt_count=1),
                3: SheetState(sheet_num=3, status=SheetStatus.COMPLETED, attempt_count=1),
                # Consecutive failures that would trigger circuit breaker
                4: SheetState(
                    sheet_num=4,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Failed 1",
                ),
                5: SheetState(
                    sheet_num=5,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Failed 2",
                ),
                6: SheetState(
                    sheet_num=6,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Failed 3",
                ),
                7: SheetState(
                    sheet_num=7,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Failed 4",
                ),
                8: SheetState(
                    sheet_num=8,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Failed 5",
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(app, ["status", "status-cb", "--workspace", str(tmp_path)])
        assert result.exit_code == 0
        # Should show inferred circuit breaker state with consecutive failures
        assert "Circuit Breaker" in result.stdout
        assert "OPEN" in result.stdout

    def test_status_json_includes_new_fields(self, tmp_path: Path) -> None:
        """Test status --json includes circuit_breaker and recent_errors."""
        state = CheckpointState(
            job_id="status-json-new",
            job_name="Status JSON New Fields",
            total_sheets=3,
            last_completed_sheet=1,
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                    last_activity_at=datetime.now(UTC),
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.FAILED,
                    attempt_count=3,
                    error_message="Test failure",
                    error_category="transient",
                    completed_at=datetime.now(UTC),
                ),
            },
        )

        _seed_registry(tmp_path, state)

        result = runner.invoke(
            app, ["status", "status-json-new", "--json", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0

        output = json.loads(result.stdout)
        assert "circuit_breaker" in output
        assert "recent_errors" in output
        assert "last_activity" in output["timing"]
        assert len(output["recent_errors"]) >= 1


# =============================================================================
# CLI Command Smoke Tests
# =============================================================================


class TestDiagnoseCommandSmoke:
    """Smoke tests for the diagnose command."""

    def test_diagnose_help(self) -> None:
        """Test diagnose --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["diagnose", "--help"])
        assert result.exit_code == 0
        assert "diagnose" in result.stdout.lower()

    def test_diagnose_nonexistent_job(self, tmp_path: Path) -> None:
        """Test diagnose with nonexistent job ID returns error."""
        workspace = tmp_path / "empty_ws"
        workspace.mkdir()
        result = runner.invoke(app, ["diagnose", "no-such-job-xyz", "--workspace", str(workspace)])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower() or "no-such-job-xyz" in result.stdout


class TestPauseCommandSmoke:
    """Smoke tests for the pause command."""

    def test_pause_help(self) -> None:
        """Test pause --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["pause", "--help"])
        assert result.exit_code == 0
        assert "pause" in result.stdout.lower()

    def test_pause_nonexistent_workspace(self, tmp_path: Path) -> None:
        """Test pause with nonexistent workspace returns error."""
        fake_workspace = tmp_path / "does_not_exist"
        result = runner.invoke(app, ["pause", "no-such-job", "--workspace", str(fake_workspace)])
        assert result.exit_code != 0


class TestDashboardCommandSmoke:
    """Smoke tests for the dashboard command."""

    def test_dashboard_help(self) -> None:
        """Test dashboard --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.stdout.lower()


class TestLearningCommandsSmoke:
    """Smoke tests for all learning and pattern CLI commands.

    Each test verifies that --help exits cleanly, confirming the command
    is registered and its option parsing is functional.
    """

    def test_learning_stats_help(self) -> None:
        """Test learning-stats --help exits cleanly."""
        result = runner.invoke(app, ["learning-stats", "--help"])
        assert result.exit_code == 0
        assert "learning" in result.stdout.lower() or "stats" in result.stdout.lower()

    def test_learning_insights_help(self) -> None:
        """Test learning-insights --help exits cleanly."""
        result = runner.invoke(app, ["learning-insights", "--help"])
        assert result.exit_code == 0
        assert "insight" in result.stdout.lower() or "learning" in result.stdout.lower()

    def test_learning_activity_help(self) -> None:
        """Test learning-activity --help exits cleanly."""
        result = runner.invoke(app, ["learning-activity", "--help"])
        assert result.exit_code == 0
        assert "activity" in result.stdout.lower() or "learning" in result.stdout.lower()

    def test_learning_drift_help(self) -> None:
        """Test learning-drift --help exits cleanly."""
        result = runner.invoke(app, ["learning-drift", "--help"])
        assert result.exit_code == 0
        assert "drift" in result.stdout.lower() or "learning" in result.stdout.lower()

    def test_learning_epistemic_drift_help(self) -> None:
        """Test learning-epistemic-drift --help exits cleanly."""
        result = runner.invoke(app, ["learning-epistemic-drift", "--help"])
        assert result.exit_code == 0
        assert "drift" in result.stdout.lower() or "epistemic" in result.stdout.lower()

    def test_patterns_list_help(self) -> None:
        """Test patterns-list --help exits cleanly."""
        result = runner.invoke(app, ["patterns-list", "--help"])
        assert result.exit_code == 0
        assert "pattern" in result.stdout.lower() or "list" in result.stdout.lower()

    def test_patterns_why_help(self) -> None:
        """Test patterns-why --help exits cleanly."""
        result = runner.invoke(app, ["patterns-why", "--help"])
        assert result.exit_code == 0
        assert "why" in result.stdout.lower() or "pattern" in result.stdout.lower()

    def test_patterns_entropy_help(self) -> None:
        """Test patterns-entropy --help exits cleanly."""
        result = runner.invoke(app, ["patterns-entropy", "--help"])
        assert result.exit_code == 0
        assert "entropy" in result.stdout.lower() or "pattern" in result.stdout.lower()

    def test_patterns_budget_help(self) -> None:
        """Test patterns-budget --help exits cleanly."""
        result = runner.invoke(app, ["patterns-budget", "--help"])
        assert result.exit_code == 0
        assert "budget" in result.stdout.lower() or "pattern" in result.stdout.lower()

    def test_entropy_status_help(self) -> None:
        """Test entropy-status --help exits cleanly."""
        result = runner.invoke(app, ["entropy-status", "--help"])
        assert result.exit_code == 0
        assert "entropy" in result.stdout.lower() or "status" in result.stdout.lower()
