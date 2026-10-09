"""
F-502: CLI Conductor-Only Enforcement Tests

Tests verify that pause/resume/recover commands:
1. Do NOT accept --workspace parameter (removed)
2. Require conductor to be running (no filesystem fallback)
3. Route through conductor IPC (daemon-only architecture)

These tests define the success criteria for F-502 implementation.
Expected to FAIL (RED) until implementation is complete.
"""

from typer.testing import CliRunner

from marianne.cli import app


class TestPauseCommand:
    """Test pause command conductor-only enforcement."""

    def test_pause_no_workspace_parameter(self):
        """Pause command should not accept --workspace parameter."""
        runner = CliRunner()
        result = runner.invoke(app, ["pause", "test-job", "--workspace", "/tmp/test"])

        # Should fail with "no such option" error, not E502 job not found
        assert result.exit_code != 0
        assert (
            "no such option" in result.output.lower() or "unrecognized" in result.output.lower()
        ), f"Expected parameter rejection, got: {result.output}"

    def test_pause_requires_conductor(self, monkeypatch):
        """Pause should fail cleanly when conductor unavailable (no fallback)."""
        runner = CliRunner()

        # Mock try_daemon_route to raise connection error
        from marianne.daemon.exceptions import DaemonError

        async def _no_conductor(method: str, params: dict, *, socket_path=None):
            raise DaemonError("Conductor not running")

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route",
            _no_conductor,
        )

        result = runner.invoke(app, ["pause", "test-job"])

        # Should fail with conductor error, not attempt filesystem fallback
        assert result.exit_code != 0
        assert "conductor" in result.output.lower() or "daemon" in result.output.lower(), (
            f"Expected conductor error, got: {result.output}"
        )


class TestResumeCommand:
    """Test resume command conductor-only enforcement."""

    def test_resume_no_workspace_parameter(self):
        """Resume command should not accept --workspace parameter."""
        runner = CliRunner()
        result = runner.invoke(app, ["resume", "test-job", "--workspace", "/tmp/test"])

        # Should fail with "no such option" error
        assert result.exit_code != 0
        assert (
            "no such option" in result.output.lower() or "unrecognized" in result.output.lower()
        ), f"Expected parameter rejection, got: {result.output}"

    def test_resume_requires_conductor(self, monkeypatch):
        """Resume should fail cleanly when conductor unavailable (no fallback)."""
        runner = CliRunner()

        # Mock try_daemon_route to raise connection error
        from marianne.daemon.exceptions import DaemonError

        async def _no_conductor(method: str, params: dict, *, socket_path=None):
            raise DaemonError("Conductor not running")

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route",
            _no_conductor,
        )

        result = runner.invoke(app, ["resume", "test-job"])

        # Should fail with conductor error, not attempt filesystem fallback
        assert result.exit_code != 0
        assert "conductor" in result.output.lower() or "daemon" in result.output.lower(), (
            f"Expected conductor error, got: {result.output}"
        )


class TestRecoverCommand:
    """Test recover command conductor-only enforcement."""

    def test_recover_no_workspace_parameter(self):
        """Recover command should not accept --workspace parameter."""
        runner = CliRunner()
        result = runner.invoke(app, ["recover", "test-job", "--workspace", "/tmp/test"])

        # Should fail with "no such option" error
        assert result.exit_code != 0
        assert (
            "no such option" in result.output.lower() or "unrecognized" in result.output.lower()
        ), f"Expected parameter rejection, got: {result.output}"

    def test_recover_fails_cleanly_without_job(self, monkeypatch):
        """Recover fails cleanly when job not found in DB.

        Note: recover now reads the DB directly (GH#170), so it no longer
        requires the conductor to be running.
        """
        runner = CliRunner()

        result = runner.invoke(app, ["recover", "test-job"])

        # Should fail cleanly with "not found" error
        assert result.exit_code != 0
        assert "not found" in result.output.lower(), (
            f"Expected 'not found' error, got: {result.output}"
        )


class TestStatusCommand:
    """Test status command conductor-only enforcement.

    The status command retains --workspace as a hidden debug override,
    but still routes through the conductor by default (F-502).
    """

    def test_status_workspace_is_hidden_debug_override(self, monkeypatch):
        """Status --workspace is accepted but only as a hidden debug override.

        Unlike pause/resume/recover (which fully removed --workspace),
        the status command keeps it as a hidden option for debug use.
        The command still routes through the conductor first.
        """
        async def absent_conductor(*args, **kwargs):
            return False, None

        monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", absent_conductor)
        runner = CliRunner()
        result = runner.invoke(app, ["status", "test-job", "--workspace", "/tmp/test"])

        # --workspace is accepted (hidden debug override), so we should NOT
        # see "no such option". The command proceeds and fails because the
        # job doesn't exist — that's the expected behavior.
        assert result.exit_code != 0
        assert "no such option" not in result.output.lower(), (
            f"--workspace should be accepted as hidden debug override, got: {result.output}"
        )
        assert "not found" in result.output.lower(), (
            f"Expected job-not-found error, got: {result.output}"
        )
