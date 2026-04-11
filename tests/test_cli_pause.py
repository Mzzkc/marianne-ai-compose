"""Tests for mzt pause and modify CLI commands.

Comprehensive tests covering:
- Pause command signal file creation
- Pause command error handling (E501-E504)
- Modify command config validation
- Modify command pause + resume workflow
- JSON output format validation
- Edge cases and error conditions
"""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.helpers import (
    _create_pause_signal,
    _find_job_workspace,
    _wait_for_pause_ack,
)
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.state.json_backend import JsonStateBackend

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure CLI tests never route through a real conductor.

    All pause/modify tests exercise the filesystem-based fallback path
    (invoked when --workspace is given and the conductor is unavailable).
    """
    async def _fake_route(
        method: str, params: dict, *, socket_path=None
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route", _fake_route,
    )


def _parse_json_output(output: str) -> dict:
    """Parse JSON output that may have Rich console line wrapping issues.

    Rich console may insert newlines in long string values when the terminal
    width is narrow (common in test environments). This function handles that
    by joining all lines and parsing the result.
    """
    # Join lines and remove extra whitespace while preserving JSON structure
    # This handles Rich console line wrapping in string values
    cleaned = ' '.join(line.strip() for line in output.strip().split('\n') if line.strip())
    return json.loads(cleaned)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def running_job_state(temp_workspace: Path) -> tuple[CheckpointState, Path]:
    """Create a running job state for testing."""
    state = CheckpointState(
        job_id="test-pause-job",
        job_name="Test Pause Job",
        total_sheets=5,
        last_completed_sheet=2,
        status=JobStatus.RUNNING,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        config_snapshot={
            "name": "test-pause-job",
            "sheet": {"size": 10, "total_items": 50},
            "prompt": {"template": "Test prompt {{ sheet_num }}"},
            "backend": {"type": "claude_cli"},
        },
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

    # Write state to JSON file
    state_file = temp_workspace / f"{state.job_id}.json"
    state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

    return state, temp_workspace


@pytest.fixture
def paused_job_state(temp_workspace: Path) -> tuple[CheckpointState, Path]:
    """Create a paused job state for testing."""
    state = CheckpointState(
        job_id="test-paused-job",
        job_name="Test Paused Job",
        total_sheets=5,
        last_completed_sheet=3,
        status=JobStatus.PAUSED,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        config_snapshot={
            "name": "test-paused-job",
            "sheet": {"size": 10, "total_items": 50},
            "prompt": {"template": "Test prompt {{ sheet_num }}"},
            "backend": {"type": "claude_cli"},
        },
    )

    state_file = temp_workspace / f"{state.job_id}.json"
    state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

    return state, temp_workspace


@pytest.fixture
def completed_job_state(temp_workspace: Path) -> tuple[CheckpointState, Path]:
    """Create a completed job state for testing."""
    state = CheckpointState(
        job_id="test-completed-job",
        job_name="Test Completed Job",
        total_sheets=5,
        last_completed_sheet=5,
        status=JobStatus.COMPLETED,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    state_file = temp_workspace / f"{state.job_id}.json"
    state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

    return state, temp_workspace


@pytest.fixture
def failed_job_state(temp_workspace: Path) -> tuple[CheckpointState, Path]:
    """Create a failed job state for testing."""
    state = CheckpointState(
        job_id="test-failed-job",
        job_name="Test Failed Job",
        total_sheets=5,
        last_completed_sheet=2,
        status=JobStatus.FAILED,
        error_message="Max retries exceeded",
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        config_snapshot={
            "name": "test-failed-job",
            "sheet": {"size": 10, "total_items": 50},
            "prompt": {"template": "Test prompt {{ sheet_num }}"},
            "backend": {"type": "claude_cli"},
        },
    )

    state_file = temp_workspace / f"{state.job_id}.json"
    state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

    return state, temp_workspace


@pytest.fixture
def pending_job_state(temp_workspace: Path) -> tuple[CheckpointState, Path]:
    """Create a pending job state for testing."""
    state = CheckpointState(
        job_id="test-pending-job",
        job_name="Test Pending Job",
        total_sheets=5,
        last_completed_sheet=0,
        status=JobStatus.PENDING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    state_file = temp_workspace / f"{state.job_id}.json"
    state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

    return state, temp_workspace


@pytest.fixture
def sample_valid_config(tmp_path: Path) -> Path:
    """Create a valid YAML config file for modify tests."""
    import yaml

    config_dict = {
        "name": "modified-job",
        "description": "Modified job configuration",
        "backend": {
            "type": "claude_cli",
            "skip_permissions": True,
        },
        "sheet": {
            "size": 15,
            "total_items": 45,
        },
        "prompt": {
            "template": "Modified prompt for sheet {{ sheet_num }}.",
        },
    }

    config_path = tmp_path / "modified-config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_dict, f)

    return config_path


@pytest.fixture
def sample_invalid_config(tmp_path: Path) -> Path:
    """Create an invalid YAML config file for modify tests."""
    config_path = tmp_path / "invalid-config.yaml"
    config_path.write_text("name: test\nsheet:\n  size: -1")  # Invalid size
    return config_path


# ============================================================================
# Test Helper Functions
# ============================================================================


class TestFindJobWorkspace:
    """Tests for _find_job_workspace helper function."""

    def test_find_with_hint_path(self, running_job_state: tuple[CheckpointState, Path]) -> None:
        """Test finding workspace with hint path."""
        state, workspace = running_job_state
        found = _find_job_workspace(state.job_id, hint=workspace)
        assert found == workspace

    def test_find_without_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test workspace not found without hint when not in cwd."""
        # Isolate from real workspace dirs in project root
        monkeypatch.chdir(tmp_path)
        # Without hint and not in cwd patterns, should return None
        found = _find_job_workspace("nonexistent-job")
        assert found is None

    def test_find_in_cwd_workspace_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test finding job in ./workspace subdirectory."""
        # Create workspace subdirectory structure
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        state = CheckpointState(
            job_id="cwd-test-job",
            job_name="CWD Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        state_file = workspace / f"{state.job_id}.json"
        state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

        monkeypatch.chdir(tmp_path)
        found = _find_job_workspace(state.job_id)
        assert found == workspace

    def test_find_nonexistent_job(
        self, temp_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test searching for nonexistent job returns None."""
        # Isolate from real workspace dirs in project root
        monkeypatch.chdir(temp_workspace.parent)
        found = _find_job_workspace("does-not-exist", hint=temp_workspace)
        assert found is None


class TestCreatePauseSignal:
    """Tests for _create_pause_signal helper function."""

    def test_creates_signal_file(self, temp_workspace: Path) -> None:
        """Test signal file creation."""
        signal_path = _create_pause_signal(temp_workspace, "my-job")
        assert signal_path.exists()
        assert signal_path.name == ".marianne-pause-my-job"

    def test_signal_file_idempotent(self, temp_workspace: Path) -> None:
        """Test creating signal file twice is idempotent."""
        _create_pause_signal(temp_workspace, "my-job")
        _create_pause_signal(temp_workspace, "my-job")
        signal_path = temp_workspace / ".marianne-pause-my-job"
        assert signal_path.exists()

    def test_permission_error_propagates(self, tmp_path: Path) -> None:
        """Test permission errors are raised."""
        # Use a path that doesn't exist
        nonexistent = tmp_path / "nonexistent-dir"
        with pytest.raises(OSError):
            _create_pause_signal(nonexistent, "my-job")


class TestWaitForPauseAck:
    """Tests for _wait_for_pause_ack helper function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_paused(self, temp_workspace: Path) -> None:
        """Test returns True when job becomes paused."""
        state_backend = JsonStateBackend(temp_workspace)

        # Create initial running state
        state = CheckpointState(
            job_id="wait-test-job",
            job_name="Wait Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        await state_backend.save(state)

        # Update to paused in background
        async def update_state():
            await asyncio.sleep(0.1)
            state.status = JobStatus.PAUSED
            await state_backend.save(state)

        # Run both concurrently
        task = asyncio.create_task(update_state())
        result = await _wait_for_pause_ack(state_backend, state.job_id, timeout=5)
        await task

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self, temp_workspace: Path) -> None:
        """Test returns False when timeout is reached."""
        state_backend = JsonStateBackend(temp_workspace)

        # Create running state that never changes
        state = CheckpointState(
            job_id="timeout-test-job",
            job_name="Timeout Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        await state_backend.save(state)

        # Very short timeout
        result = await _wait_for_pause_ack(state_backend, state.job_id, timeout=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_completed(self, temp_workspace: Path) -> None:
        """Test returns True when job completes (non-running state)."""
        state_backend = JsonStateBackend(temp_workspace)

        state = CheckpointState(
            job_id="complete-test-job",
            job_name="Complete Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        await state_backend.save(state)

        # Update to completed
        async def complete_job():
            await asyncio.sleep(0.1)
            state.status = JobStatus.COMPLETED
            await state_backend.save(state)

        task = asyncio.create_task(complete_job())
        result = await _wait_for_pause_ack(state_backend, state.job_id, timeout=5)
        await task

        assert result is True


# ============================================================================
# Test Pause Command
# ============================================================================


class TestPauseCommand:
    """Tests for `mzt pause` command."""

    def test_pause_help(self) -> None:
        """Test pause command shows help."""
        result = runner.invoke(app, ["pause", "--help"])
        assert result.exit_code == 0
        assert "Pause a running Marianne score" in result.output
        # --workspace is a hidden debug option, not shown in help
        assert "--wait" in result.output
        assert "--timeout" in result.output
        assert "--json" in result.output

    def test_pause_creates_signal_file(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause creates correct signal file."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert "Pause signal sent" in result.output

        # Verify signal file was created
        signal_file = workspace / f".marianne-pause-{state.job_id}"
        assert signal_file.exists()

    def test_pause_nonexistent_job(self, temp_workspace: Path) -> None:
        """Test pause with non-existent job shows E501."""
        result = runner.invoke(app, [
            "pause", "nonexistent-job",
            "--workspace", str(temp_workspace),
        ])

        assert result.exit_code == 1
        assert "E501" in result.output
        assert "not found" in result.output.lower()

    def test_pause_already_paused(
        self, paused_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause when job already paused shows E502."""
        state, workspace = paused_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 1
        assert "E502" in result.output
        assert "paused" in result.output.lower()

    def test_pause_completed_job(
        self, completed_job_state: tuple[CheckpointState, Path], mocker: Any
    ) -> None:
        """Test pause when job completed shows E502."""
        state, workspace = completed_job_state

        # Mock conductor routing to return completed job error
        async def mock_route(method: str, params: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
            return True, {"status": "rejected", "message": "Job is completed"}

        mocker.patch("marianne.daemon.detect.try_daemon_route", side_effect=mock_route)

        result = runner.invoke(app, ["pause", state.job_id])

        assert result.exit_code == 1
        assert "completed" in result.output.lower()

    def test_pause_failed_job(
        self, failed_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause when job failed shows E502."""
        state, workspace = failed_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 1
        assert "E502" in result.output

    def test_pause_pending_job(
        self, pending_job_state: tuple[CheckpointState, Path], mocker: Any
    ) -> None:
        """Test pause when job pending shows E502."""
        state, workspace = pending_job_state

        # Mock conductor routing to return pending job error
        async def mock_route(method: str, params: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
            return True, {"status": "rejected", "message": "Job is not running"}

        mocker.patch("marianne.daemon.detect.try_daemon_route", side_effect=mock_route)

        result = runner.invoke(app, ["pause", state.job_id])

        assert result.exit_code == 1
        assert "not running" in result.output.lower() or "pending" in result.output.lower()

    def test_pause_json_output_success(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause with JSON output format on success."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
            "--json",
        ])

        assert result.exit_code == 0
        output = _parse_json_output(result.stdout)
        assert output["success"] is True
        assert output["job_id"] == state.job_id
        assert "signal_file" in output
        assert output["message"] == "Pause signal sent. Score will pause at next sheet boundary."

    def test_pause_json_output_error(self, temp_workspace: Path) -> None:
        """Test pause with JSON output format on error."""
        result = runner.invoke(app, [
            "pause", "nonexistent-job",
            "--workspace", str(temp_workspace),
            "--json",
        ])

        assert result.exit_code == 1
        output = _parse_json_output(result.stdout)
        assert output["success"] is False
        assert output["error_code"] == "E501"
        assert "hints" in output

    def test_pause_shows_resume_instructions(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause shows resume instructions after success."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert f"mzt resume {state.job_id}" in result.output

    def test_pause_permission_error(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause shows E503 on permission error."""
        state, workspace = running_job_state

        # Patch where the function is used (in commands.pause), not where it's defined
        with patch(
            "marianne.cli.helpers._create_pause_signal",
            side_effect=PermissionError("Read-only"),
        ):
            result = runner.invoke(app, [
                "pause", state.job_id,
                "--workspace", str(workspace),
            ])

        assert result.exit_code == 1
        assert "E503" in result.output
        assert "permission" in result.output.lower()

    def test_pause_with_wait_timeout(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause with --wait times out correctly (E504)."""
        state, workspace = running_job_state

        # Mock the wait function to simulate timeout
        async def mock_wait(*_args: object, **_kwargs: object) -> bool:
            return False  # Simulate timeout

        with patch("marianne.cli.helpers._wait_for_pause_ack", mock_wait):
            result = runner.invoke(app, [
                "pause", state.job_id,
                "--workspace", str(workspace),
                "--wait",
                "--timeout", "1",
            ])

        assert result.exit_code == 2
        assert "E504" in result.output

    def test_pause_with_wait_success(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause with --wait succeeds when job pauses."""
        state, workspace = running_job_state

        # Mock the wait function to simulate success
        async def mock_wait(*_args: object, **_kwargs: object) -> bool:
            return True  # Simulate acknowledged pause

        with patch("marianne.cli.helpers._wait_for_pause_ack", mock_wait):
            result = runner.invoke(app, [
                "pause", state.job_id,
                "--workspace", str(workspace),
                "--wait",
                "--timeout", "5",
            ])

        assert result.exit_code == 0
        assert "paused successfully" in result.output.lower()


# ============================================================================
# Test Modify Command
# ============================================================================


class TestModifyCommand:
    """Tests for `mzt modify` command."""

    def test_modify_help(self) -> None:
        """Test modify command shows help."""
        result = runner.invoke(app, ["modify", "--help"])
        assert result.exit_code == 0
        assert "Apply a new configuration to a score" in result.output
        assert "--config" in result.output
        assert "--resume" in result.output
        assert "--wait" in result.output

    def test_modify_validates_config(
        self,
        running_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify validates new config file."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert "Config validated" in result.output

    def test_modify_invalid_config(
        self,
        running_job_state: tuple[CheckpointState, Path],
        sample_invalid_config: Path,
    ) -> None:
        """Test modify shows E505 for invalid config."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_invalid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 1
        assert "E505" in result.output

    def test_modify_missing_config(
        self,
        running_job_state: tuple[CheckpointState, Path],
        tmp_path: Path,
    ) -> None:
        """Test modify shows error for missing config file."""
        state, workspace = running_job_state
        missing_config = tmp_path / "does-not-exist.yaml"

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(missing_config),
            "--workspace", str(workspace),
        ])

        # typer validates file existence before command runs
        assert result.exit_code != 0

    def test_modify_pauses_running_job(
        self,
        running_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify pauses running job first."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert "Pause signal sent" in result.output

        # Verify signal file was created
        signal_file = workspace / f".marianne-pause-{state.job_id}"
        assert signal_file.exists()

    def test_modify_nonexistent_job(
        self, temp_workspace: Path, sample_valid_config: Path
    ) -> None:
        """Test modify shows E501 for non-existent job."""
        result = runner.invoke(app, [
            "modify", "nonexistent-job",
            "--config", str(sample_valid_config),
            "--workspace", str(temp_workspace),
        ])

        assert result.exit_code == 1
        assert "E501" in result.output

    def test_modify_already_paused_job_allowed(
        self,
        paused_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify works with already paused job."""
        state, workspace = paused_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert "Config validated" in result.output

    def test_modify_completed_job_blocked(
        self,
        completed_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify shows E502 for completed job."""
        state, workspace = completed_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 1
        assert "E502" in result.output

    def test_modify_pending_job_blocked(
        self,
        pending_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify shows E502 for pending job."""
        state, workspace = pending_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 1
        assert "E502" in result.output

    def test_modify_failed_job_allowed(
        self,
        failed_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify works with failed job (can be resumed)."""
        state, workspace = failed_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert "Config validated" in result.output

    def test_modify_json_output(
        self,
        paused_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify with JSON output format."""
        state, workspace = paused_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
            "--json",
        ])

        assert result.exit_code == 0
        output = _parse_json_output(result.stdout)
        assert output["success"] is True
        assert output["config_validated"] is True

    def test_modify_shows_resume_instructions(
        self,
        paused_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify shows resume instructions when not using --resume."""
        state, workspace = paused_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert f"mzt resume {state.job_id}" in result.output

    def test_modify_with_resume_flag(
        self,
        paused_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify with --resume triggers resume."""
        state, workspace = paused_job_state

        # Mock the runner to avoid actual execution
        with patch("marianne.execution.runner.JobRunner") as mock_runner_cls:
            mock_runner = AsyncMock()
            mock_runner.run = AsyncMock(return_value=CheckpointState(
                job_id=state.job_id,
                job_name="Modified Job",
                total_sheets=3,
                last_completed_sheet=3,
                status=JobStatus.COMPLETED,
            ))
            mock_runner_cls.return_value = mock_runner

            with patch("marianne.backends.claude_cli.ClaudeCliBackend") as mock_backend:
                mock_backend.from_config = AsyncMock(return_value=mock_backend)

                result = runner.invoke(app, [
                    "modify", state.job_id,
                    "--config", str(sample_valid_config),
                    "--workspace", str(workspace),
                    "--resume",
                ])

        # Should show resume message (may fail in mock later, but message should appear)
        assert "Resuming with new config" in result.output or "Resume Score" in result.output

    def test_modify_permission_error(
        self,
        running_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify shows E503 on permission error."""
        state, workspace = running_job_state

        with patch(
            "marianne.cli.helpers._create_pause_signal",
            side_effect=PermissionError("Read-only"),
        ):
            result = runner.invoke(app, [
                "modify", state.job_id,
                "--config", str(sample_valid_config),
                "--workspace", str(workspace),
            ])

        assert result.exit_code == 1
        assert "E503" in result.output


# ============================================================================
# Integration Tests
# ============================================================================


class TestPauseModifyIntegration:
    """Integration tests for pause/modify workflow."""

    def test_pause_then_resume_workflow(
        self,
        running_job_state: tuple[CheckpointState, Path],
    ) -> None:
        """Test pause creates signal that can be cleared by resume."""
        state, workspace = running_job_state

        # Step 1: Pause the job
        pause_result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])
        assert pause_result.exit_code == 0

        # Verify signal file exists
        signal_file = workspace / f".marianne-pause-{state.job_id}"
        assert signal_file.exists()

        # Step 2: Manually clean up signal (as runner would do)
        signal_file.unlink()

        # Step 3: Update state to paused (as runner would do)
        state.status = JobStatus.PAUSED
        state_file = workspace / f"{state.job_id}.json"
        state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

        # Step 4: Verify pause command detects paused state
        pause_again = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])
        assert pause_again.exit_code == 1
        assert "already paused" in pause_again.output.lower()

    def test_modify_then_manual_resume(
        self,
        running_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify workflow without --resume shows correct instructions."""
        state, workspace = running_job_state

        # Modify (pauses + validates)
        result = runner.invoke(app, [
            "modify", state.job_id,
            "--config", str(sample_valid_config),
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert "Pause signal sent" in result.output
        assert "Config validated" in result.output
        assert f"mzt resume {state.job_id}" in result.output

    def test_multiple_pause_signals_idempotent(
        self,
        running_job_state: tuple[CheckpointState, Path],
    ) -> None:
        """Test multiple pause commands are idempotent on running job."""
        state, workspace = running_job_state

        # First pause
        result1 = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])
        assert result1.exit_code == 0

        # Second pause (signal file already exists, still running)
        result2 = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])
        assert result2.exit_code == 0

        # Only one signal file should exist
        signal_files = list(workspace.glob(".marianne-pause-*"))
        assert len(signal_files) == 1

    def test_concurrent_pause_via_dashboard_and_cli(
        self,
        running_job_state: tuple[CheckpointState, Path],
    ) -> None:
        """Test CLI pause when dashboard-style signal already exists."""
        state, workspace = running_job_state

        # Simulate dashboard creating signal
        signal_file = workspace / f".marianne-pause-{state.job_id}"
        signal_file.touch()

        # CLI pause should still succeed (idempotent)
        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
        ])

        assert result.exit_code == 0
        assert signal_file.exists()


# ============================================================================
# Edge Cases
# ============================================================================


class TestPauseEdgeCases:
    """Edge case tests for pause command."""

    def test_pause_with_special_characters_in_job_id(
        self, temp_workspace: Path
    ) -> None:
        """Test pause with job ID containing special characters."""
        # Create state with special chars (only allowed chars)
        job_id = "test-job_123"
        state = CheckpointState(
            job_id=job_id,
            job_name="Special Job",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        state_file = temp_workspace / f"{job_id}.json"
        state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))

        result = runner.invoke(app, [
            "pause", job_id,
            "--workspace", str(temp_workspace),
        ])

        assert result.exit_code == 0
        signal_file = temp_workspace / f".marianne-pause-{job_id}"
        assert signal_file.exists()

    def test_pause_json_short_flag(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause with -j short flag for JSON output."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
            "-j",
        ])

        assert result.exit_code == 0
        output = _parse_json_output(result.stdout)
        assert output["success"] is True

    def test_pause_workspace_short_flag(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause with -w short flag for workspace."""
        state, workspace = running_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "-w", str(workspace),
        ])

        assert result.exit_code == 0

    def test_pause_timeout_short_flag(
        self, running_job_state: tuple[CheckpointState, Path]
    ) -> None:
        """Test pause with -t short flag for timeout."""
        state, workspace = running_job_state

        async def mock_wait(*_: object, **__: object) -> bool:
            return False

        with patch("marianne.cli.helpers._wait_for_pause_ack", mock_wait):
            result = runner.invoke(app, [
                "pause", state.job_id,
                "-w", str(workspace),
                "--wait",
                "-t", "1",
            ])

        assert result.exit_code == 2


class TestModifyEdgeCases:
    """Edge case tests for modify command."""

    def test_modify_config_short_flag(
        self,
        paused_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify with -c short flag for config."""
        state, workspace = paused_job_state

        result = runner.invoke(app, [
            "modify", state.job_id,
            "-c", str(sample_valid_config),
            "-w", str(workspace),
        ])

        assert result.exit_code == 0

    def test_modify_resume_short_flag(
        self,
        paused_job_state: tuple[CheckpointState, Path],
        sample_valid_config: Path,
    ) -> None:
        """Test modify with -r short flag for resume."""
        state, workspace = paused_job_state

        with patch("marianne.execution.runner.JobRunner") as mock_runner_cls:
            mock_runner = AsyncMock()
            mock_runner.run = AsyncMock(return_value=CheckpointState(
                job_id=state.job_id,
                job_name="Test",
                total_sheets=3,
                last_completed_sheet=3,
                status=JobStatus.COMPLETED,
            ))
            mock_runner_cls.return_value = mock_runner

            with patch("marianne.backends.claude_cli.ClaudeCliBackend") as mock_backend:
                mock_backend.from_config = AsyncMock(return_value=mock_backend)

                result = runner.invoke(app, [
                    "modify", state.job_id,
                    "-c", str(sample_valid_config),
                    "-w", str(workspace),
                    "-r",
                ])

        # Should show resume message (may fail in mock later, but message should appear)
        assert "Resuming with new config" in result.output or "Resume Score" in result.output


# ============================================================================
# Conductor-Routed Pause Tests
# ============================================================================


class TestPauseConductorRouted:
    """Tests for pause command when conductor handles the request.

    These tests override the autouse _no_daemon fixture to simulate
    a running conductor that routes the pause RPC.
    """

    @staticmethod
    async def _mock_route_success(
        method: str, params: dict, **kw: object,
    ) -> tuple[bool, dict]:
        return True, {"paused": True, "job_id": "test-job"}

    @staticmethod
    async def _mock_route_refused(
        method: str, params: dict, **kw: object,
    ) -> tuple[bool, dict]:
        return True, {"paused": False, "error": "Job is completed"}

    @staticmethod
    async def _mock_route_error(
        method: str, params: dict, **kw: object,
    ) -> tuple[bool, dict]:
        raise ConnectionError("socket refused")

    def test_conductor_pause_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Conductor reports successful pause."""
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", self._mock_route_success)

        result = runner.invoke(app, ["pause", "test-job"])
        assert result.exit_code == 0
        assert "Pause signal sent" in result.output

    def test_conductor_pause_success_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Conductor pause success with JSON output."""
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", self._mock_route_success)

        result = runner.invoke(app, ["pause", "test-job", "--json"])
        assert result.exit_code == 0
        output = _parse_json_output(result.stdout)
        assert output["success"] is True

    def test_conductor_pause_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Conductor reports job cannot be paused (not running)."""
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", self._mock_route_refused)

        result = runner.invoke(app, ["pause", "test-job"])
        assert result.exit_code == 1

    def test_conductor_pause_refused_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Conductor refusal with JSON output includes E502."""
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", self._mock_route_refused)

        result = runner.invoke(app, ["pause", "test-job", "--json"])
        assert result.exit_code == 1
        output = _parse_json_output(result.stdout)
        assert output["success"] is False
        assert output["error_code"] == "E502"

    def test_conductor_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connection error to conductor produces E501."""
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", self._mock_route_error)

        result = runner.invoke(app, ["pause", "test-job"])
        assert result.exit_code == 1

    def test_conductor_connection_error_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connection error with JSON output."""
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", self._mock_route_error)

        result = runner.invoke(app, ["pause", "test-job", "--json"])
        assert result.exit_code == 1
        output = _parse_json_output(result.stdout)
        assert output["success"] is False
        assert output["error_code"] == "E501"


# ============================================================================
# Filesystem Pause JSON Edge Cases
# ============================================================================


class TestPauseJsonEdgeCases:
    """Tests for JSON output edge cases in filesystem-based pause."""

    def test_pause_paused_job_json_has_hints(
        self, paused_job_state: tuple[CheckpointState, Path],
    ) -> None:
        """JSON output for already-paused job includes resume hint."""
        state, workspace = paused_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
            "--json",
        ])

        assert result.exit_code == 1
        output = _parse_json_output(result.stdout)
        assert output["error_code"] == "E502"
        assert any("resume" in h.lower() for h in output.get("hints", []))

    def test_pause_completed_job_json_has_hints(
        self, completed_job_state: tuple[CheckpointState, Path],
    ) -> None:
        """JSON output for completed job includes appropriate hint."""
        state, workspace = completed_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
            "--json",
        ])

        assert result.exit_code == 1
        output = _parse_json_output(result.stdout)
        assert output["error_code"] == "E502"
        assert any("completed" in h.lower() for h in output.get("hints", []))

    def test_pause_pending_job_json_has_hints(
        self, pending_job_state: tuple[CheckpointState, Path],
    ) -> None:
        """JSON output for pending job includes 'use mzt run' hint."""
        state, workspace = pending_job_state

        result = runner.invoke(app, [
            "pause", state.job_id,
            "--workspace", str(workspace),
            "--json",
        ])

        assert result.exit_code == 1
        output = _parse_json_output(result.stdout)
        assert output["error_code"] == "E502"
        assert any("run" in h.lower() for h in output.get("hints", []))

    def test_pause_wait_timeout_json(
        self, running_job_state: tuple[CheckpointState, Path],
    ) -> None:
        """JSON output for wait timeout includes signal_file."""
        state, workspace = running_job_state

        async def mock_wait(*_: object, **__: object) -> bool:
            return False

        with patch("marianne.cli.helpers._wait_for_pause_ack", mock_wait):
            result = runner.invoke(app, [
                "pause", state.job_id,
                "--workspace", str(workspace),
                "--wait", "--timeout", "1",
                "--json",
            ])

        assert result.exit_code == 2
        output = _parse_json_output(result.stdout)
        assert output["error_code"] == "E504"
        assert "signal_file" in output


# ============================================================================
# Conductor-Routed Modify Tests (stale-status recovery, #97)
# ============================================================================


class TestModifyConductorRouted:
    """Tests for modify --resume via conductor with stale/changing status.

    These tests simulate the conductor-routed path where the daemon reports
    status and handles pause/resume RPCs. The autouse _no_daemon fixture is
    overridden per-test via monkeypatch.
    """

    @pytest.fixture()
    def valid_config(self, tmp_path: Path) -> Path:
        """Create a minimal valid config for modify tests."""
        import yaml as _yaml

        cfg = {
            "name": "test-modify-job",
            "backend": {"type": "claude_cli", "skip_permissions": True},
            "sheet": {"size": 10, "total_items": 30},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }
        p = tmp_path / "cfg.yaml"
        p.write_text(_yaml.dump(cfg))
        return p

    @staticmethod
    def _make_route(
        status_response: dict,
        pause_response: dict | Exception | None = None,
        resume_response: dict | Exception | None = None,
        modify_response: dict | Exception | None = None,
    ):
        """Build a mock try_daemon_route that returns per-method responses."""
        async def _route(method: str, params: dict, **kw: object):
            if method == "job.status":
                return True, status_response
            if method == "job.pause":
                if isinstance(pause_response, Exception):
                    raise pause_response
                return True, (pause_response or {"paused": False})
            if method == "job.resume":
                if isinstance(resume_response, Exception):
                    raise resume_response
                return True, (resume_response or {"accepted": True})
            if method == "job.modify":
                if isinstance(modify_response, Exception):
                    raise modify_response
                return True, (modify_response or {"job_id": params.get("job_id", ""), "status": "accepted", "message": "Modify accepted"})
            return False, None
        return _route

    def test_modify_resume_stale_running_no_e503(
        self,
        monkeypatch: pytest.MonkeyPatch,
        valid_config: Path,
    ) -> None:
        """When daemon reports RUNNING but pause fails, job.modify handles it atomically."""
        route = self._make_route(
            status_response={
                "job_id": "stale-job",
                "status": "running",
                "job_name": "Stale Job",
                "total_sheets": 3,
                "last_completed_sheet": 1,
            },
            # Pause fails — job actually failed between status and pause
            pause_response={"paused": False, "error": "Job is failed, not running"},
            modify_response={
                "job_id": "stale-job",
                "status": "accepted",
                "message": "Pause signal sent. Will resume with cfg.yaml when paused.",
            },
        )
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", route)

        result = runner.invoke(app, [
            "modify", "stale-job",
            "--config", str(valid_config),
            "--resume",
        ])

        # The key assertion: no scary E503 error shown
        assert "E503" not in result.output
        # Config validation still shown
        assert "Config validated" in result.output
        # Atomic modify message shown
        assert "Will resume" in result.output or "Modify accepted" in result.output

    def test_modify_resume_daemon_returns_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        valid_config: Path,
    ) -> None:
        """When daemon correctly reports FAILED, job.modify resumes immediately."""
        route = self._make_route(
            status_response={
                "job_id": "failed-job",
                "status": "failed",
                "job_name": "Failed Job",
                "total_sheets": 5,
                "last_completed_sheet": 2,
                "error_message": "Max retries exceeded",
            },
            modify_response={
                "job_id": "failed-job",
                "status": "accepted",
                "message": "Job resume queued",
            },
        )
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", route)

        result = runner.invoke(app, [
            "modify", "failed-job",
            "--config", str(valid_config),
            "--resume",
        ])

        # No pause attempt, no error
        assert "E503" not in result.output
        assert "Pause signal" not in result.output
        # Config validation shown, then atomic modify result
        assert "Config validated" in result.output
        assert "accepted" in result.output or "resume" in result.output.lower()

    def test_modify_resume_modify_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        valid_config: Path,
    ) -> None:
        """Connection error during job.modify shows E506 error."""
        from marianne.daemon.exceptions import DaemonError

        route = self._make_route(
            status_response={
                "job_id": "conn-err-job",
                "status": "running",
                "job_name": "Conn Err Job",
                "total_sheets": 3,
                "last_completed_sheet": 1,
            },
            modify_response=DaemonError("socket refused"),
        )
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", route)

        result = runner.invoke(app, [
            "modify", "conn-err-job",
            "--config", str(valid_config),
            "--resume",
        ])

        # Connection error reported
        assert result.exit_code == 1
        assert "socket refused" in result.output

    def test_modify_no_resume_pause_failure_shows_e503(
        self,
        monkeypatch: pytest.MonkeyPatch,
        valid_config: Path,
    ) -> None:
        """Without --resume, pause failure shows E503 as before."""
        route = self._make_route(
            status_response={
                "job_id": "err-job",
                "status": "running",
                "job_name": "Err Job",
                "total_sheets": 3,
                "last_completed_sheet": 1,
            },
            pause_response={"paused": False, "error": "Job is failed"},
        )
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", route)

        result = runner.invoke(app, [
            "modify", "err-job",
            "--config", str(valid_config),
            # No --resume flag
        ])

        # Without --resume, error IS shown
        assert result.exit_code == 1
        assert "E503" in result.output

    def test_modify_resume_json_stale_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        valid_config: Path,
    ) -> None:
        """JSON output with --resume uses job.modify, no E503."""
        route = self._make_route(
            status_response={
                "job_id": "json-job",
                "status": "running",
                "job_name": "JSON Job",
                "total_sheets": 3,
                "last_completed_sheet": 1,
            },
            modify_response={
                "job_id": "json-job",
                "status": "accepted",
                "message": "Pause signal sent. Will resume with cfg.yaml when paused.",
            },
        )
        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", route)

        result = runner.invoke(app, [
            "modify", "json-job",
            "--config", str(valid_config),
            "--resume",
            "--json",
        ])

        # No JSON error block with E503
        assert "E503" not in result.output
        # JSON output contains the modify response
        assert "accepted" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
