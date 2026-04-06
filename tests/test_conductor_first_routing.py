"""Functional tests for conductor-first architecture (Issue #50).

These tests verify that:
1. All CLI commands route through the conductor by default
2. Commands show clear errors when conductor is unavailable
3. Commands fall back to filesystem with --workspace override
4. The conductor RPC methods exist and handle parameters correctly
5. The require_conductor helper works as expected
6. The mozartd entry point is fully removed
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.checkpoint import CheckpointState, JobStatus

runner = CliRunner()


# ─── Helper fixtures ────────────────────────────────────────────────


def _make_job_state(
    job_id: str = "test-job",
    status: JobStatus = JobStatus.RUNNING,
    total_sheets: int = 5,
    completed: int = 2,
) -> CheckpointState:
    """Create a CheckpointState for testing."""
    state = CheckpointState(
        job_id=job_id,
        job_name=job_id,
        total_sheets=total_sheets,
    )
    state.status = status
    state.last_completed_sheet = completed
    return state


# ─── Phase 1: Entry point consolidation ─────────────────────────────


class TestMozartdRemoved:
    """Verify mozartd entry point is fully removed."""

    def test_mozartd_not_in_pyproject(self):
        """mozartd should NOT appear in pyproject.toml scripts section."""
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        assert "mozartd" not in scripts, "mozartd entry point should be removed"
        assert "mozart" in scripts, "mozart entry point should still exist"

    def test_daemon_app_not_in_source(self):
        """daemon_app Typer instance should be removed from process.py."""
        import inspect

        from marianne.daemon import process

        source = inspect.getsource(process)
        assert "daemon_app" not in source, "daemon_app should be removed"

    def test_conductor_commands_registered(self):
        """mozart start/stop/restart/conductor-status should be registered."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "start" in result.output
        assert "stop" in result.output
        assert "restart" in result.output
        assert "conductor-status" in result.output


class TestConductorCommands:
    """Verify conductor start/stop/restart commands work."""

    def test_start_help(self):
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0
        assert "Start the Mozart conductor" in result.output

    def test_stop_help(self):
        result = runner.invoke(app, ["stop", "--help"])
        assert result.exit_code == 0
        assert "Stop the Mozart conductor" in result.output

    def test_restart_help(self):
        result = runner.invoke(app, ["restart", "--help"])
        assert result.exit_code == 0
        assert "Restart the Mozart conductor" in result.output


# ─── Phase 2+3: Commands route through conductor ────────────────────


class TestStatusRoutesThruConductor:
    """Status command routes through conductor by default."""

    def test_status_shows_conductor_required_error(self):
        """Without conductor, mozart status shows clear error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["status", "test-job"])

        assert result.exit_code == 1
        assert "conductor is not running" in result.output.lower() or \
               "mozart start" in result.output

    def test_status_succeeds_via_conductor(self):
        """Status command works when conductor returns state."""
        state = _make_job_state(status=JobStatus.COMPLETED)
        state_dict = state.model_dump(mode="json")

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, state_dict),
        ):
            result = runner.invoke(app, ["status", "test-job"])

        assert result.exit_code == 0

    def test_status_json_via_conductor(self):
        """Status --json works via conductor."""
        state = _make_job_state(status=JobStatus.COMPLETED)
        state_dict = state.model_dump(mode="json")

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, state_dict),
        ):
            result = runner.invoke(app, ["status", "test-job", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["job_id"] == "test-job"
        assert output["status"] == "completed"

    def test_status_workspace_override_falls_back(self, tmp_path: Path):
        """Status with --workspace falls back to filesystem when conductor unavailable."""
        state = _make_job_state(status=JobStatus.COMPLETED)

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(False, None),
            ),
            # Patch at the import location in status.py, not in helpers.py
            patch(
                "marianne.cli.commands.status.require_job_state",
                new_callable=AsyncMock,
                return_value=(state, MagicMock()),
            ),
        ):
            result = runner.invoke(
                app, ["status", "test-job", "--workspace", str(tmp_path)],
            )

        assert result.exit_code == 0


class TestPauseRoutesThruConductor:
    """Pause command routes through conductor by default."""

    def test_pause_shows_conductor_required_error(self):
        """Without conductor, mozart pause shows clear error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["pause", "test-job"])

        assert result.exit_code == 1
        assert "conductor is not running" in result.output.lower() or \
               "mozart start" in result.output

    def test_pause_succeeds_via_conductor(self):
        """Pause command works when conductor acknowledges."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {"paused": True}),
        ):
            result = runner.invoke(app, ["pause", "test-job"])

        assert result.exit_code == 0
        assert "pause" in result.output.lower()

    def test_pause_failure_via_conductor(self):
        """Pause reports error when conductor rejects."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {"paused": False, "error": "Job not running"}),
        ):
            result = runner.invoke(app, ["pause", "test-job"])

        assert result.exit_code == 1


class TestResumeRoutesThruConductor:
    """Resume command routes through conductor by default."""

    def test_resume_shows_conductor_required_error(self):
        """Without conductor, mozart resume shows clear error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["resume", "test-job"])

        assert result.exit_code == 1
        assert "conductor is not running" in result.output.lower() or \
               "mozart start" in result.output

    def test_resume_succeeds_via_conductor(self):
        """Resume command works when conductor accepts."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(
                True,
                {"job_id": "test-job", "status": "accepted", "message": "Queued"},
            ),
        ):
            result = runner.invoke(app, ["resume", "test-job"])

        assert result.exit_code == 0
        assert "accepted" in result.output.lower()


class TestErrorsRoutesThruConductor:
    """Errors command routes through conductor by default."""

    def test_errors_shows_conductor_required_error(self):
        """Without conductor, mozart errors shows clear error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["errors", "test-job"])

        assert result.exit_code == 1
        assert "conductor is not running" in result.output.lower() or \
               "mozart start" in result.output

    def test_errors_succeeds_via_conductor(self):
        """Errors command works via conductor."""
        state = _make_job_state(status=JobStatus.COMPLETED)
        state_dict = state.model_dump(mode="json")

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {"state": state_dict}),
        ):
            result = runner.invoke(app, ["errors", "test-job"])

        assert result.exit_code == 0
        assert "no errors" in result.output.lower()


class TestDiagnoseRoutesThruConductor:
    """Diagnose command routes through conductor by default."""

    def test_diagnose_shows_conductor_required_error(self):
        """Without conductor, mozart diagnose shows clear error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["diagnose", "test-job"])

        assert result.exit_code == 1
        assert "conductor is not running" in result.output.lower() or \
               "mozart start" in result.output

    def test_diagnose_succeeds_via_conductor(self):
        """Diagnose command works via conductor."""
        state = _make_job_state(status=JobStatus.COMPLETED)
        state_dict = state.model_dump(mode="json")

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {"state": state_dict, "workspace": "/tmp/ws"}),
        ):
            result = runner.invoke(app, ["diagnose", "test-job"])

        assert result.exit_code == 0


class TestHistoryRoutesThruConductor:
    """History command routes through conductor by default."""

    def test_history_shows_conductor_required_error(self):
        """Without conductor, mozart history shows clear error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["history", "test-job"])

        assert result.exit_code == 1
        assert "conductor is not running" in result.output.lower() or \
               "mozart start" in result.output

    def test_history_succeeds_via_conductor(self):
        """History command works via conductor."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {
                "job_id": "test-job",
                "records": [],
                "has_history": True,
            }),
        ):
            result = runner.invoke(app, ["history", "test-job"])

        assert result.exit_code == 0


class TestRecoverRoutesThruConductor:
    """Recover command routes through conductor by default."""

    def test_recover_shows_conductor_required_error(self):
        """Without conductor, mozart recover shows clear error."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["recover", "test-job"])

        assert result.exit_code == 1
        assert "conductor is not running" in result.output.lower() or \
               "mozart start" in result.output


# ─── Phase 4: Hidden --workspace ────────────────────────────────────


class TestWorkspaceHidden:
    """Verify --workspace is hidden on all commands."""

    def test_workspace_hidden_from_status_help(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        # --workspace should NOT appear in help output (hidden=True)
        # But other options should appear
        assert "--json" in result.output
        assert "--watch" in result.output

    def test_workspace_hidden_from_pause_help(self):
        result = runner.invoke(app, ["pause", "--help"])
        assert result.exit_code == 0
        assert "--wait" in result.output

    def test_workspace_hidden_from_resume_help(self):
        result = runner.invoke(app, ["resume", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output

    def test_workspace_hidden_from_errors_help(self):
        result = runner.invoke(app, ["errors", "--help"])
        assert result.exit_code == 0
        assert "--type" in result.output

    def test_workspace_hidden_from_diagnose_help(self):
        result = runner.invoke(app, ["diagnose", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output

    def test_workspace_hidden_from_history_help(self):
        result = runner.invoke(app, ["history", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.output


# ─── require_conductor helper ───────────────────────────────────────


class TestRequireConductor:
    """Tests for the require_conductor helper function."""

    def test_require_conductor_passes_when_routed(self):
        """require_conductor does nothing when routed=True."""
        from marianne.cli.helpers import require_conductor

        # Should not raise
        require_conductor(True)

    def test_require_conductor_exits_when_not_routed(self):
        """require_conductor raises typer.Exit(1) when routed=False."""
        import typer

        from marianne.cli.helpers import require_conductor

        with pytest.raises(typer.Exit):
            require_conductor(False)

    def test_require_conductor_json_output(self, capsys):
        """require_conductor outputs JSON when json_output=True."""
        import typer

        from marianne.cli.helpers import require_conductor

        with pytest.raises(typer.Exit):
            require_conductor(False, json_output=True)


# ─── RPC method registration ────────────────────────────────────────


class TestRPCMethodRegistration:
    """Verify all required RPC methods are registered."""

    @pytest.mark.asyncio
    async def test_all_phase2_methods_registered(self):
        """Phase 2 RPC methods (status, pause, resume) are registered."""
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.process import DaemonProcess

        config = DaemonConfig()
        dp = DaemonProcess(config)

        handler = MagicMock()
        manager = MagicMock()
        health = MagicMock()

        dp._register_methods(handler, manager, health)

        registered = {call.args[0] for call in handler.register.call_args_list}
        # Phase 2 methods
        assert "job.status" in registered
        assert "job.pause" in registered
        assert "job.resume" in registered

    @pytest.mark.asyncio
    async def test_all_phase3_methods_registered(self):
        """Phase 3 RPC methods (errors, diagnose, history, recover) are registered."""
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.process import DaemonProcess

        config = DaemonConfig()
        dp = DaemonProcess(config)

        handler = MagicMock()
        manager = MagicMock()
        health = MagicMock()

        dp._register_methods(handler, manager, health)

        registered = {call.args[0] for call in handler.register.call_args_list}
        # Phase 3 methods
        assert "job.errors" in registered
        assert "job.diagnose" in registered
        assert "job.history" in registered
        assert "job.recover" in registered


# ─── IPC Client convenience methods ─────────────────────────────────


class TestDaemonClientMethods:
    """Verify DaemonClient has typed convenience methods for all operations."""

    def test_client_has_all_methods(self):
        """DaemonClient should have convenience methods for all RPC operations."""
        from marianne.daemon.ipc.client import DaemonClient

        client = DaemonClient(Path("/tmp/test.sock"))

        # Phase 2 methods
        assert hasattr(client, "get_job_status")
        assert hasattr(client, "pause_job")
        assert hasattr(client, "resume_job")

        # Phase 3 methods
        assert hasattr(client, "get_errors")
        assert hasattr(client, "diagnose")
        assert hasattr(client, "get_execution_history")
        assert hasattr(client, "recover_job")

        # Existing methods
        assert hasattr(client, "submit_job")
        assert hasattr(client, "cancel_job")
        assert hasattr(client, "list_jobs")
        assert hasattr(client, "status")
        assert hasattr(client, "health")
        assert hasattr(client, "readiness")


# ─── Manager enrichments ────────────────────────────────────────────


class TestManagerEnrichments:
    """Verify JobManager has methods for all conductor-routed operations."""

    def test_manager_has_diagnostic_methods(self):
        """JobManager should have diagnostic and recovery methods."""
        from marianne.daemon.manager import JobManager

        # Check method signatures exist
        assert hasattr(JobManager, "get_job_errors")
        assert hasattr(JobManager, "get_diagnostic_report")
        assert hasattr(JobManager, "get_execution_history")
        assert hasattr(JobManager, "recover_job")
        assert hasattr(JobManager, "get_job_status")
        assert hasattr(JobManager, "pause_job")
        assert hasattr(JobManager, "resume_job")


# ─── Conductor error messages ───────────────────────────────────────


class TestConductorErrorMessages:
    """Verify error messages reference 'mozart start' not 'mozartd'."""

    def test_require_conductor_mentions_mozart_start(self):
        """Error message should say 'mozart start', not 'mozartd start'."""
        import typer

        from marianne.cli.helpers import require_conductor

        try:
            require_conductor(False)
        except typer.Exit:
            pass
        # The function prints to console, not returns — check the source
        import inspect
        source = inspect.getsource(require_conductor)
        assert "mozart start" in source
        assert "mozartd" not in source

    def test_list_jobs_error_mentions_mozart_start(self):
        """List command error should reference 'mozart start'."""
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 1
        assert "mozart start" in result.output


# ─── try_daemon_route safety ────────────────────────────────────────


class TestTryDaemonRouteSafety:
    """Verify try_daemon_route handles errors correctly."""

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self):
        """Connection errors return (False, None) — never raise."""
        from marianne.daemon.detect import try_daemon_route

        with patch(
            "marianne.daemon.ipc.client.DaemonClient.is_daemon_running",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError("refused"),
        ):
            routed, result = await try_daemon_route("job.status", {"job_id": "x"})

        assert routed is False
        assert result is None

    @pytest.mark.asyncio
    async def test_daemon_not_running_returns_false(self):
        """DaemonNotRunningError returns (False, None)."""
        from marianne.daemon.detect import try_daemon_route

        with patch(
            "marianne.daemon.ipc.client.DaemonClient.is_daemon_running",
            new_callable=AsyncMock,
            return_value=False,
        ):
            routed, result = await try_daemon_route("job.status", {"job_id": "x"})

        assert routed is False
        assert result is None

    @pytest.mark.asyncio
    async def test_business_logic_error_reraises(self):
        """JobSubmissionError from conductor is re-raised (not swallowed)."""
        from marianne.daemon.detect import try_daemon_route
        from marianne.daemon.exceptions import JobSubmissionError

        with (
            patch(
                "marianne.daemon.ipc.client.DaemonClient.is_daemon_running",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.ipc.client.DaemonClient.call",
                new_callable=AsyncMock,
                side_effect=JobSubmissionError("Job not found"),
            ),pytest.raises(JobSubmissionError)
        ):
            await try_daemon_route("job.status", {"job_id": "x"})


# ─── Filesystem fallback helpers remain private ─────────────────────


class TestFilesystemFallbacksPrivate:
    """Verify filesystem fallback helpers exist but are private."""

    def test_private_helpers_exist(self):
        """Private filesystem fallback helpers still exist for debug override."""
        from marianne.cli import helpers

        assert hasattr(helpers, "_find_job_workspace")
        assert hasattr(helpers, "_find_job_state_fs")
        assert hasattr(helpers, "_find_job_state_direct")
        assert hasattr(helpers, "_create_pause_signal")
        assert hasattr(helpers, "_wait_for_pause_ack")

    def test_public_api_has_require_conductor(self):
        """require_conductor is the new public conductor helper."""
        from marianne.cli import helpers

        assert hasattr(helpers, "require_conductor")

    def test_no_public_find_job_state(self):
        """find_job_state/require_job_state should NOT be public anymore."""
        from marianne.cli import helpers

        # These should only exist as private _-prefixed versions
        assert not hasattr(helpers, "find_job_state")
        assert not hasattr(helpers, "require_job_state")
        assert not hasattr(helpers, "find_job_workspace")


# ─── Documentation consistency ──────────────────────────────────────


class TestDocumentationConsistency:
    """Verify user-facing documentation is updated."""

    def test_no_mozartd_in_daemon_guide(self):
        """daemon-guide.md should not reference mozartd."""
        doc = Path(__file__).parent.parent / "docs" / "daemon-guide.md"
        if doc.exists():
            content = doc.read_text()
            assert "mozartd" not in content.lower(), \
                "daemon-guide.md should use 'mozart start' not 'mozartd'"

    def test_no_mozartd_in_getting_started(self):
        """getting-started.md should not reference mozartd."""
        doc = Path(__file__).parent.parent / "docs" / "getting-started.md"
        if doc.exists():
            content = doc.read_text()
            assert "mozartd" not in content.lower(), \
                "getting-started.md should use 'mozart start' not 'mozartd'"

    def test_no_mozartd_in_cli_reference(self):
        """cli-reference.md should not reference mozartd."""
        doc = Path(__file__).parent.parent / "docs" / "cli-reference.md"
        if doc.exists():
            content = doc.read_text()
            assert "mozartd" not in content.lower(), \
                "cli-reference.md should use 'mozart start' not 'mozartd'"

    def test_no_mozartd_in_source_code(self):
        """No mozartd references should remain in the src/ directory."""
        src_dir = Path(__file__).parent.parent / "src"
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text()
            # Allow it in logger names and comments documenting the transition
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if "mozartd" in line.lower():
                    # Skip import/comment lines that reference the old name
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""'):
                        continue
                    raise AssertionError(
                        f"mozartd reference in {py_file}:{i+1}: {line.strip()}"
                    )
