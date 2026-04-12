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
        result = runner.invoke(app,["pause", "test-job", "--workspace", "/tmp/test"])

        # Should fail with "no such option" error, not E502 job not found
        assert result.exit_code != 0
        assert ("no such option" in result.output.lower() or
                "unrecognized" in result.output.lower()), \
            f"Expected parameter rejection, got: {result.output}"

    def test_pause_requires_conductor(self, monkeypatch):
        """Pause should fail cleanly when conductor unavailable (no fallback)."""
        runner = CliRunner()

        # Simulate conductor unavailable by pointing to non-existent socket
        monkeypatch.setenv("MARIANNE_SOCKET", "/tmp/nonexistent-conductor.sock")

        result = runner.invoke(app, ["pause", "test-job"])

        # Should fail with conductor error, not attempt filesystem fallback
        assert result.exit_code != 0
        assert ("conductor" in result.output.lower() or
                "daemon" in result.output.lower()), \
            f"Expected conductor error, got: {result.output}"


class TestResumeCommand:
    """Test resume command conductor-only enforcement."""

    def test_resume_no_workspace_parameter(self):
        """Resume command should not accept --workspace parameter."""
        runner = CliRunner()
        result = runner.invoke(app, ["resume", "test-job", "--workspace", "/tmp/test"])

        # Should fail with "no such option" error
        assert result.exit_code != 0
        assert ("no such option" in result.output.lower() or
                "unrecognized" in result.output.lower()), \
            f"Expected parameter rejection, got: {result.output}"

    def test_resume_requires_conductor(self, monkeypatch):
        """Resume should fail cleanly when conductor unavailable (no fallback)."""
        runner = CliRunner()

        # Simulate conductor unavailable
        monkeypatch.setenv("MARIANNE_SOCKET", "/tmp/nonexistent-conductor.sock")

        result = runner.invoke(app, ["resume", "test-job"])

        # Should fail with conductor error, not attempt filesystem fallback
        assert result.exit_code != 0
        assert ("conductor" in result.output.lower() or
                "daemon" in result.output.lower()), \
            f"Expected conductor error, got: {result.output}"


class TestRecoverCommand:
    """Test recover command conductor-only enforcement."""

    def test_recover_no_workspace_parameter(self):
        """Recover command should not accept --workspace parameter."""
        runner = CliRunner()
        result = runner.invoke(app, ["recover", "test-job", "--workspace", "/tmp/test"])

        # Should fail with "no such option" error
        assert result.exit_code != 0
        assert ("no such option" in result.output.lower() or
                "unrecognized" in result.output.lower()), \
            f"Expected parameter rejection, got: {result.output}"

    def test_recover_requires_conductor(self, monkeypatch):
        """Recover should fail cleanly when conductor unavailable (no fallback)."""
        runner = CliRunner()

        # Simulate conductor unavailable
        monkeypatch.setenv("MARIANNE_SOCKET", "/tmp/nonexistent-conductor.sock")

        result = runner.invoke(app, ["recover", "test-job"])

        # Should fail with conductor error, not attempt filesystem fallback
        assert result.exit_code != 0
        assert ("conductor" in result.output.lower() or
                "daemon" in result.output.lower()), \
            f"Expected conductor error, got: {result.output}"


class TestStatusCommand:
    """Test status command workspace cleanup (P2)."""

    def test_status_no_workspace_parameter(self):
        """Status command should not accept --workspace parameter."""
        runner = CliRunner()
        result = runner.invoke(app, ["status", "test-job", "--workspace", "/tmp/test"])

        # Should fail with "no such option" error
        assert result.exit_code != 0
        assert ("no such option" in result.output.lower() or
                "unrecognized" in result.output.lower()), \
            f"Expected parameter rejection, got: {result.output}"
