"""Integration tests for Mozart end-to-end workflows.

These tests verify that all components work together correctly:
- Run -> Status -> Resume flow
- List with multiple jobs
- Dashboard API CRUD operations
- Complete lifecycle tests

All tests use mocked backends to avoid actual Claude/API calls.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from marianne.backends.base import ExecutionResult
from marianne.cli import app
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.dashboard import create_app
from marianne.execution.runner import RunSummary
from marianne.state.json_backend import JsonStateBackend

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure integration tests never route through a real conductor.

    Individual tests that need conductor behavior override with
    unittest.mock.patch (which takes precedence during its context).
    """
    async def _fake_route(
        method: str, params: dict, *, socket_path=None
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route", _fake_route,
    )


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_successful_backend() -> MagicMock:
    """Create a mock backend that always succeeds."""
    backend = AsyncMock()
    backend.execute = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            stdout="Task completed successfully",
            stderr="",
            exit_code=0,
            duration_seconds=1.5,
        )
    )
    backend.health_check = AsyncMock(return_value=True)
    return backend


@pytest.fixture
def mock_failing_backend() -> MagicMock:
    """Create a mock backend that fails on sheet 2."""
    backend = AsyncMock()
    call_count = 0

    async def execute_with_failure(*args: Any, **kwargs: Any) -> ExecutionResult:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:  # Fail on second sheet
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Error processing sheet",
                exit_code=1,
                duration_seconds=0.5,
            )
        return ExecutionResult(
            success=True,
            stdout="Task completed",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
        )

    backend.execute = execute_with_failure
    backend.health_check = AsyncMock(return_value=True)
    return backend


@pytest.fixture
def workspace_with_config(tmp_path: Path, sample_config_dict: dict) -> tuple[Path, Path]:
    """Create workspace with config file and return (workspace, config_path)."""
    import yaml

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create output directory that validations expect
    output_dir = workspace / "output"
    output_dir.mkdir()

    config_path = tmp_path / "test-job.yaml"
    # Modify config for integration test
    config_dict = sample_config_dict.copy()
    config_dict["sheet"]["total_items"] = 20  # 2 sheets of size 10
    config_dict["validations"] = []  # No validations for simpler tests

    with open(config_path, "w") as f:
        yaml.dump(config_dict, f)

    return workspace, config_path


@pytest.fixture
def multi_job_workspace(tmp_path: Path) -> Path:
    """Create workspace with file-based job states.

    Used by status/resume tests that still read from JSON state files.
    """
    workspace = tmp_path / "multi-jobs"
    workspace.mkdir()

    jobs = [
        CheckpointState(
            job_id="job-completed-1",
            job_name="Completed Job 1",
            total_sheets=5,
            last_completed_sheet=5,
            status=JobStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        CheckpointState(
            job_id="job-running-2",
            job_name="Running Job 2",
            total_sheets=10,
            last_completed_sheet=5,
            status=JobStatus.RUNNING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        CheckpointState(
            job_id="job-failed-3",
            job_name="Failed Job 3",
            total_sheets=8,
            last_completed_sheet=3,
            status=JobStatus.FAILED,
            error_message="Max retries exceeded",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        CheckpointState(
            job_id="job-paused-4",
            job_name="Paused Job 4",
            total_sheets=6,
            last_completed_sheet=2,
            status=JobStatus.PAUSED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            config_snapshot={
                "name": "job-paused-4",
                "sheet": {"size": 5, "total_items": 30},
                "prompt": {"template": "Test"},
            },
        ),
    ]

    for job in jobs:
        state_file = workspace / f"{job.job_id}.json"
        state_file.write_text(json.dumps(job.model_dump(mode="json"), default=str))

    return workspace


def _multi_job_daemon_data() -> list[dict[str, Any]]:
    """Daemon-format job data for list command tests."""
    import time

    now = time.time()
    return [
        {
            "job_id": "job-completed-1", "status": "completed",
            "workspace": "/ws/1", "submitted_at": now - 400,
            "started_at": now - 390, "completed_at": now - 300, "pid": 1001,
        },
        {
            "job_id": "job-running-2", "status": "running",
            "workspace": "/ws/2", "submitted_at": now - 200,
            "started_at": now - 190, "pid": 1002,
        },
        {
            "job_id": "job-failed-3", "status": "failed",
            "workspace": "/ws/3", "submitted_at": now - 100,
            "started_at": now - 90, "completed_at": now - 50,
            "error_message": "Max retries exceeded", "pid": 1003,
        },
        {
            "job_id": "job-paused-4", "status": "paused",
            "workspace": "/ws/4", "submitted_at": now - 50,
            "started_at": now - 40, "pid": 1004,
        },
    ]


# ============================================================================
# Run -> Status -> Resume Flow Tests
# ============================================================================


class TestRunStatusResumeWorkflow:
    """Tests for the complete run -> status -> resume workflow."""

    def test_run_dry_run_shows_plan(
        self, workspace_with_config: tuple[Path, Path]
    ) -> None:
        """Dry run shows job plan without execution."""
        _, config_path = workspace_with_config

        result = runner.invoke(
            app,
            ["run", str(config_path), "--dry-run"],
        )

        assert result.exit_code == 0, f"Exit code: {result.exit_code}\nOutput: {result.stdout}"
        assert "Dry run" in result.stdout
        assert "Sheet Plan" in result.stdout

    def test_status_shows_job_after_run(self, tmp_path: Path) -> None:
        """Status command shows job details after running."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # Create a state file manually (simulating post-run state)
        state = CheckpointState(
            job_id="integration-test-job",
            job_name="Integration Test Job",
            total_sheets=3,
            last_completed_sheet=2,
            status=JobStatus.RUNNING,
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
            },
        )
        state_file = workspace / "integration-test-job.json"
        state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

        # Check status
        result = runner.invoke(
            app,
            ["status", "integration-test-job", "--workspace", str(workspace)],
        )

        assert result.exit_code == 0
        assert "Integration Test Job" in result.stdout
        assert "RUNNING" in result.stdout
        assert "2" in result.stdout  # completed sheets
        assert "3" in result.stdout  # total sheets

    def test_resume_continues_from_checkpoint(self, tmp_path: Path) -> None:
        """Resume continues a paused job from the last checkpoint."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # Create a paused state with config snapshot
        state = CheckpointState(
            job_id="resume-test-job",
            job_name="Resume Test Job",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.PAUSED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            config_snapshot={
                "name": "resume-test-job",
                "sheet": {"size": 10, "total_items": 50},
                "prompt": {"template": "Process sheet {{ sheet_num }}"},
                "backend": {"type": "claude_cli", "skip_permissions": True},
                "retry": {"max_retries": 2},
                "validations": [],
            },
            sheets={
                1: SheetState(
                    sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1
                ),
                2: SheetState(
                    sheet_num=2, status=SheetStatus.COMPLETED, attempt_count=1
                ),
            },
        )
        state_file = workspace / "resume-test-job.json"
        state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

        with patch("marianne.execution.runner.JobRunner") as mock_runner_cls:
            # Create mock runner that returns completed state and summary
            mock_runner = AsyncMock()
            completed_state = CheckpointState(
                job_id="resume-test-job",
                job_name="Resume Test Job",
                total_sheets=5,
                last_completed_sheet=5,
                status=JobStatus.COMPLETED,
            )
            mock_summary = RunSummary(
                job_id="resume-test-job",
                job_name="Resume Test Job",
                total_sheets=5,
                completed_sheets=3,
                failed_sheets=0,
                skipped_sheets=0,
            )
            mock_runner.run = AsyncMock(return_value=(completed_state, mock_summary))
            mock_runner_cls.return_value = mock_runner

            with patch("marianne.backends.claude_cli.ClaudeCliBackend") as mock_backend:
                mock_backend.from_config = AsyncMock(return_value=AsyncMock())

                result = runner.invoke(
                    app,
                    ["resume", "resume-test-job", "--workspace", str(workspace)],
                )

        # Should show resume info
        assert "Resume Score" in result.stdout
        assert "2/5" in result.stdout  # Starting from sheet 2

    def test_complete_workflow_end_to_end(self, tmp_path: Path) -> None:
        """Test complete workflow: run -> status -> (fail) -> resume."""
        import yaml

        workspace = tmp_path / "e2e-workspace"
        workspace.mkdir()

        # Create config
        config_dict = {
            "name": "e2e-test-job",
            "description": "End-to-end test",
            "backend": {"type": "claude_cli", "skip_permissions": True},
            "sheet": {"size": 5, "total_items": 15},  # 3 sheets
            "prompt": {"template": "Process sheet {{ sheet_num }}"},
            "retry": {"max_retries": 1},
            "validations": [],
        }
        config_path = tmp_path / "e2e-config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config_dict, f)

        # Step 1: Start run (will be interrupted)
        # Simulate a paused state after 1 sheet
        paused_state = CheckpointState(
            job_id="e2e-test-job",
            job_name="End-to-end test",
            total_sheets=3,
            last_completed_sheet=1,
            status=JobStatus.PAUSED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            config_snapshot=config_dict,
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    attempt_count=1,
                    validation_passed=True,
                ),
            },
        )
        state_file = workspace / "e2e-test-job.json"
        state_file.write_text(
            json.dumps(paused_state.model_dump(mode="json"), default=str)
        )

        # Step 2: Check status
        result = runner.invoke(
            app, ["status", "e2e-test-job", "--workspace", str(workspace)]
        )
        assert result.exit_code == 0
        assert "PAUSED" in result.stdout
        assert "1" in result.stdout and "3" in result.stdout  # 1/3 sheets

        # Step 3: Resume and complete
        with patch("marianne.execution.runner.JobRunner") as mock_runner_cls:
            completed_state = CheckpointState(
                job_id="e2e-test-job",
                job_name="End-to-end test",
                total_sheets=3,
                last_completed_sheet=3,
                status=JobStatus.COMPLETED,
            )
            mock_summary = RunSummary(
                job_id="e2e-test-job",
                job_name="End-to-end test",
                total_sheets=3,
                completed_sheets=2,  # Resumed from sheet 2
                failed_sheets=0,
                skipped_sheets=0,
            )
            mock_runner = AsyncMock()
            mock_runner.run = AsyncMock(return_value=(completed_state, mock_summary))
            mock_runner_cls.return_value = mock_runner

            with patch("marianne.backends.claude_cli.ClaudeCliBackend") as mock_backend:
                mock_backend.from_config = AsyncMock(return_value=AsyncMock())

                result = runner.invoke(
                    app, ["resume", "e2e-test-job", "--workspace", str(workspace)]
                )

        assert "Resume Score" in result.stdout


# ============================================================================
# List Command with Multiple Jobs Tests
# ============================================================================


class TestListMultipleJobs:
    """Tests for listing and filtering multiple jobs via daemon."""

    @staticmethod
    def _mock_route(jobs: list[dict]):
        """Return a patch that makes try_daemon_route return *jobs*."""
        async def _fake_route(method, params):
            return (True, jobs)
        return patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route)

    def test_list_default_shows_active_only(self) -> None:
        """Default list shows only active jobs (running, queued, paused)."""
        jobs = _multi_job_daemon_data()
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "job-running-2" in result.stdout
        assert "job-paused-4" in result.stdout
        assert "job-completed-1" not in result.stdout
        assert "job-failed-3" not in result.stdout
        assert "2 score(s)" in result.stdout

    def test_list_all_shows_everything(self) -> None:
        """--all flag shows all jobs including completed/failed."""
        jobs = _multi_job_daemon_data()
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--all"])

        assert result.exit_code == 0
        assert "job-completed-1" in result.stdout
        assert "job-running-2" in result.stdout
        assert "job-failed-3" in result.stdout
        assert "job-paused-4" in result.stdout
        assert "4 score(s)" in result.stdout

    def test_list_filters_by_completed(self) -> None:
        """List with --status=completed shows only completed jobs."""
        jobs = _multi_job_daemon_data()
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--status", "completed"])

        assert result.exit_code == 0
        assert "job-completed-1" in result.stdout
        assert "job-running-2" not in result.stdout
        assert "job-failed-3" not in result.stdout
        assert "job-paused-4" not in result.stdout
        assert "1 score(s)" in result.stdout

    def test_list_filters_by_failed(self) -> None:
        """List with --status=failed shows only failed jobs."""
        jobs = _multi_job_daemon_data()
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--status", "failed"])

        assert result.exit_code == 0
        assert "job-failed-3" in result.stdout
        assert "1 score(s)" in result.stdout

    def test_list_filters_by_paused(self) -> None:
        """List with --status=paused shows only paused jobs."""
        jobs = _multi_job_daemon_data()
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--status", "paused"])

        assert result.exit_code == 0
        assert "job-paused-4" in result.stdout
        assert "1 score(s)" in result.stdout

    def test_list_with_limit(self) -> None:
        """List respects --limit option."""
        jobs = _multi_job_daemon_data()
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--all", "--limit", "2"])

        assert result.exit_code == 0
        assert "2 score(s)" in result.stdout

    def test_list_shows_workspace_info(self) -> None:
        """List shows workspace path for each job."""
        jobs = _multi_job_daemon_data()
        with self._mock_route(jobs):
            result = runner.invoke(app, ["list", "--all"])

        assert result.exit_code == 0
        # Should show workspace paths
        assert "/ws/" in result.stdout


# ============================================================================
# Dashboard API Integration Tests
# ============================================================================


class TestDashboardAPIIntegration:
    """Tests for Dashboard API with real state backend."""

    @pytest.fixture
    def dashboard_workspace(self, tmp_path: Path) -> Path:
        """Create workspace for dashboard tests."""
        workspace = tmp_path / "dashboard-workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def dashboard_client(self, dashboard_workspace: Path) -> TestClient:
        """Create test client for dashboard."""
        state_backend = JsonStateBackend(dashboard_workspace)
        test_app = create_app(state_backend=state_backend)
        return TestClient(test_app)

    def test_dashboard_initially_empty(
        self, dashboard_client: TestClient
    ) -> None:
        """Dashboard shows empty list initially."""
        response = dashboard_client.get("/api/jobs")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_dashboard_list_and_get_job(
        self, dashboard_workspace: Path, dashboard_client: TestClient
    ) -> None:
        """Dashboard can list and get jobs from state files."""
        # Create a job state file
        job = CheckpointState(
            job_id="dashboard-test-job",
            job_name="Dashboard Test Job",
            total_sheets=5,
            last_completed_sheet=3,
            status=JobStatus.RUNNING,
        )
        state_file = dashboard_workspace / f"{job.job_id}.json"
        state_file.write_text(
            json.dumps(job.model_dump(mode="json"), default=str)
        )

        # List should show the job
        response = dashboard_client.get("/api/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["job_id"] == "dashboard-test-job"

        # Get specific job
        response = dashboard_client.get("/api/jobs/dashboard-test-job")
        assert response.status_code == 200
        job_data = response.json()
        assert job_data["job_id"] == "dashboard-test-job"
        assert job_data["status"] == "running"
        assert job_data["total_sheets"] == 5

    def test_dashboard_404_for_missing_job(
        self, dashboard_client: TestClient
    ) -> None:
        """Dashboard returns 404 for non-existent job."""
        response = dashboard_client.get("/api/jobs/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_dashboard_health_check(self, dashboard_client: TestClient) -> None:
        """Dashboard health endpoint works."""
        response = dashboard_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "mozart-dashboard"


# ============================================================================
# All CLI Commands Functional Tests
# ============================================================================


class TestAllCLICommandsFunctional:
    """Verify all 6 CLI commands are functional."""

    def test_validate_command_works(self, sample_yaml_config: Path) -> None:
        """Validate command works with valid config."""
        result = runner.invoke(app, ["validate", str(sample_yaml_config)])
        assert result.exit_code == 0
        assert "Valid" in result.stdout

    def test_run_command_works(self, sample_yaml_config: Path) -> None:
        """Run command works with --dry-run."""
        result = runner.invoke(
            app,
            ["run", str(sample_yaml_config), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert "Sheet Plan" in result.stdout

    def test_list_command_works(self) -> None:
        """List command works via daemon."""
        jobs = _multi_job_daemon_data()
        async def _fake_route(method, params):
            return (True, jobs)
        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["list", "--all"])
        assert result.exit_code == 0
        assert "score(s)" in result.stdout

    def test_status_command_works(self, multi_job_workspace: Path) -> None:
        """Status command works."""
        result = runner.invoke(
            app,
            ["status", "job-completed-1", "--workspace", str(multi_job_workspace)],
        )
        assert result.exit_code == 0
        assert "Completed Job 1" in result.stdout

    def test_resume_command_works(self, multi_job_workspace: Path) -> None:
        """Resume command works (shows error for completed job)."""
        result = runner.invoke(
            app,
            ["resume", "job-completed-1", "--workspace", str(multi_job_workspace)],
        )
        # Completed jobs can't be resumed without --force
        assert result.exit_code == 1
        assert "already completed" in result.stdout

    def test_dashboard_command_works(self, tmp_path: Path) -> None:
        """Dashboard command starts (with mocked uvicorn)."""
        import sys

        mock_uvicorn = MagicMock()
        mock_uvicorn.run = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            result = runner.invoke(
                app, ["dashboard", "--workspace", str(tmp_path)]
            )
            assert result.exit_code == 0
            assert "Mozart Dashboard" in result.stdout


# ============================================================================
# State Backend Integration Tests
# ============================================================================


class TestStateBackendIntegration:
    """Tests for state backend integration across components."""

    @pytest.mark.asyncio
    async def test_json_backend_persistence(self, tmp_path: Path) -> None:
        """JSON backend persists and loads state correctly."""
        backend = JsonStateBackend(tmp_path)

        # Save a state
        state = CheckpointState(
            job_id="persistence-test",
            job_name="Persistence Test",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.RUNNING,
        )
        await backend.save(state)

        # Load it back
        loaded = await backend.load("persistence-test")
        assert loaded is not None
        assert loaded.job_id == "persistence-test"
        assert loaded.status == JobStatus.RUNNING
        assert loaded.last_completed_sheet == 2

    @pytest.mark.asyncio
    async def test_json_backend_list_jobs(self, tmp_path: Path) -> None:
        """JSON backend lists all jobs correctly."""
        backend = JsonStateBackend(tmp_path)

        # Save multiple states
        for i in range(3):
            state = CheckpointState(
                job_id=f"list-test-{i}",
                job_name=f"List Test {i}",
                total_sheets=5,
                last_completed_sheet=i,
                status=JobStatus.COMPLETED if i == 2 else JobStatus.RUNNING,
            )
            await backend.save(state)

        # List all
        jobs = await backend.list_jobs()
        assert len(jobs) == 3

        # Verify we can filter in memory
        completed = [j for j in jobs if j.status == JobStatus.COMPLETED]
        assert len(completed) == 1
        assert completed[0].job_id == "list-test-2"

    @pytest.mark.asyncio
    async def test_state_updates_persist(self, tmp_path: Path) -> None:
        """State updates are persisted correctly."""
        backend = JsonStateBackend(tmp_path)

        # Create initial state
        state = CheckpointState(
            job_id="update-test",
            job_name="Update Test",
            total_sheets=5,
            last_completed_sheet=0,
            status=JobStatus.PENDING,
        )
        await backend.save(state)

        # Update state
        state.status = JobStatus.RUNNING
        state.last_completed_sheet = 3
        state.sheets[1] = SheetState(
            sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1
        )
        await backend.save(state)

        # Load and verify
        loaded = await backend.load("update-test")
        assert loaded is not None
        assert loaded.status == JobStatus.RUNNING
        assert loaded.last_completed_sheet == 3
        assert 1 in loaded.sheets
        assert loaded.sheets[1].status == SheetStatus.COMPLETED


# ============================================================================
# Error Handling Integration Tests
# ============================================================================


class TestErrorHandlingIntegration:
    """Tests for error handling across the system."""

    def test_invalid_config_file_error(self, tmp_path: Path) -> None:
        """Invalid config file produces helpful error."""
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("not: valid: yaml: config")

        result = runner.invoke(app, ["validate", str(bad_config)])
        # Exit code 1 = invalid config, exit code 2 = cannot validate (parse error)
        assert result.exit_code in (1, 2)

    def test_list_without_daemon_error(self) -> None:
        """List without running daemon produces helpful error."""
        async def _fake_route(method, params):
            return (False, None)
        with patch("marianne.daemon.detect.try_daemon_route", side_effect=_fake_route):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 1
        assert "conductor is not running" in result.stdout.lower()

    def test_missing_job_error(self, tmp_path: Path) -> None:
        """Missing job produces helpful error."""
        workspace = tmp_path / "empty"
        workspace.mkdir()

        result = runner.invoke(
            app, ["status", "nonexistent-job", "--workspace", str(workspace)]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_resume_missing_config_error(self, tmp_path: Path) -> None:
        """Resume without config snapshot shows helpful error."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # Create state without config_snapshot
        state = CheckpointState(
            job_id="no-config",
            job_name="No Config Job",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.PAUSED,
            config_snapshot=None,
        )
        state_file = workspace / "no-config.json"
        state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

        result = runner.invoke(
            app, ["resume", "no-config", "--workspace", str(workspace)]
        )
        assert result.exit_code == 1
        assert "config" in result.stdout.lower()
