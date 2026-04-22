"""Smoke tests for CLI entry points and module imports.

These tests verify that:
1. All public modules can be imported without errors
2. All CLI commands respond to --help without crashing
3. CLI commands fail gracefully with missing/invalid arguments

No mocking, no fixtures, no complex setup — just basic sanity checks.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent smoke tests from reaching a real conductor."""

    async def _fake_route(method: str, params: dict, *, socket_path=None) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        _fake_route,
    )


# =============================================================================
# Module import smoke tests
# =============================================================================

# Every top-level package under marianne should be importable.
# Catching circular imports, missing deps, and syntax errors.
IMPORTABLE_MODULES = [
    # Core
    "marianne",
    "marianne.core",
    "marianne.core.checkpoint",
    "marianne.core.constants",
    "marianne.core.fan_out",
    "marianne.core.logging",
    # Config
    "marianne.core.config",
    "marianne.core.config.backend",
    "marianne.core.config.execution",
    "marianne.core.config.job",
    "marianne.core.config.learning",
    "marianne.core.config.orchestration",
    "marianne.core.config.workspace",
    # Errors
    "marianne.core.errors",
    "marianne.core.errors.classifier",
    "marianne.core.errors.codes",
    "marianne.core.errors.models",
    "marianne.core.errors.parsers",
    "marianne.core.errors.signals",
    # CLI
    "marianne.cli",
    "marianne.cli.helpers",
    "marianne.cli.output",
    "marianne.cli.commands",
    "marianne.cli.commands.run",
    "marianne.cli.commands.resume",
    "marianne.cli.commands.pause",
    "marianne.cli.commands.status",
    "marianne.cli.commands.validate",
    "marianne.cli.commands.recover",
    "marianne.cli.commands.diagnose",
    "marianne.cli.commands.dashboard",
    "marianne.cli.commands.config_cmd",
    # Backends
    "marianne.backends",
    "marianne.backends.base",
    "marianne.backends.claude_cli",
    # State
    "marianne.state",
    "marianne.state.base",
    "marianne.state.json_backend",
    "marianne.state.memory",
    "marianne.state.sqlite_backend",
    # Execution
    "marianne.execution",
    # marianne.execution.hooks removed in Phase 6b — HookExecutor was dead
    # code; the live expand_hook_variables() was relocated to daemon/manager.py.
    "marianne.execution.validation",
    "marianne.execution.validation.engine",
    # Learning
    "marianne.learning",
    "marianne.learning.patterns",
    "marianne.learning.migration",
    "marianne.learning.store",
    "marianne.learning.store.base",
    "marianne.learning.store.models",
    # Validation
    "marianne.validation",
    "marianne.validation.base",
    "marianne.validation.runner",
    "marianne.validation.reporter",
    "marianne.validation.checks",
    "marianne.validation.checks.jinja",
    "marianne.validation.checks.paths",
    "marianne.validation.checks.config",
    # Daemon
    "marianne.daemon",
    "marianne.daemon.config",
    "marianne.daemon.detect",
    "marianne.daemon.exceptions",
    "marianne.daemon.health",
    "marianne.daemon.backpressure",
    "marianne.daemon.ipc",
    "marianne.daemon.ipc.protocol",
    "marianne.daemon.ipc.errors",
    "marianne.daemon.registry",
    "marianne.daemon.types",
    "marianne.daemon.output",
    "marianne.daemon.task_utils",
    "marianne.daemon.rate_coordinator",
    "marianne.daemon.scheduler",
    "marianne.daemon.monitor",
    "marianne.daemon.system_probe",
    "marianne.daemon.pgroup",
    # Healing
    "marianne.healing",
    "marianne.healing.context",
    "marianne.healing.coordinator",
    "marianne.healing.diagnosis",
    "marianne.healing.registry",
    "marianne.healing.remedies",
    "marianne.healing.remedies.base",
    # Isolation
    "marianne.isolation",
    "marianne.isolation.worktree",
    # MCP
    "marianne.mcp",
    "marianne.mcp.resources",
    "marianne.mcp.tools",
    # Notifications
    "marianne.notifications",
    "marianne.notifications.base",
    "marianne.notifications.factory",
    # Prompts
    "marianne.prompts",
    "marianne.prompts.templating",
    # Utils
    "marianne.utils",
    "marianne.utils.time",
    # Workspace
    "marianne.workspace",
    "marianne.workspace.lifecycle",
]


class TestModuleImports:
    """Verify all public modules import without errors."""

    @pytest.mark.parametrize("module_name", IMPORTABLE_MODULES)
    def test_module_imports(self, module_name: str) -> None:
        """Each module should import cleanly without side effects."""
        mod = importlib.import_module(module_name)
        assert mod is not None


# =============================================================================
# CLI --help smoke tests (commands missing from existing test suite)
# =============================================================================


class TestCoreCommandsSmoke:
    """Smoke tests for core CLI commands missing help tests."""

    def test_run_help(self) -> None:
        """Test run --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "run" in result.stdout.lower()

    def test_resume_help(self) -> None:
        """Test resume --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["resume", "--help"])
        assert result.exit_code == 0
        assert "resume" in result.stdout.lower()

    def test_status_help(self) -> None:
        """Test status --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        assert "status" in result.stdout.lower()

    def test_list_help(self) -> None:
        """Test list --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["list", "--help"])
        assert result.exit_code == 0

    def test_validate_help(self) -> None:
        """Test validate --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0
        assert "validate" in result.stdout.lower()

    def test_errors_help(self) -> None:
        """Test errors --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["errors", "--help"])
        assert result.exit_code == 0

    def test_history_help(self) -> None:
        """Test history --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["history", "--help"])
        assert result.exit_code == 0

    def test_recover_help(self) -> None:
        """Test recover --help exits cleanly (hidden command)."""
        result = runner.invoke(app, ["recover", "--help"])
        assert result.exit_code == 0

    def test_mcp_help(self) -> None:
        """Test mcp --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["mcp", "--help"])
        assert result.exit_code == 0

    def test_modify_help(self) -> None:
        """Test modify --help exits cleanly and shows usage."""
        result = runner.invoke(app, ["modify", "--help"])
        assert result.exit_code == 0
        assert "modify" in result.stdout.lower()


class TestConfigCommandsSmoke:
    """Smoke tests for config subcommands."""

    def test_config_help(self) -> None:
        """Test config --help exits cleanly."""
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "config" in result.stdout.lower()

    def test_config_show_help(self) -> None:
        """Test config show --help exits cleanly."""
        result = runner.invoke(app, ["config", "show", "--help"])
        assert result.exit_code == 0

    def test_config_set_help(self) -> None:
        """Test config set --help exits cleanly."""
        result = runner.invoke(app, ["config", "set", "--help"])
        assert result.exit_code == 0

    def test_config_path_help(self) -> None:
        """Test config path --help exits cleanly."""
        result = runner.invoke(app, ["config", "path", "--help"])
        assert result.exit_code == 0

    def test_config_init_help(self) -> None:
        """Test config init --help exits cleanly."""
        result = runner.invoke(app, ["config", "init", "--help"])
        assert result.exit_code == 0


# =============================================================================
# CLI error handling smoke tests
# =============================================================================


class TestCommandErrorHandling:
    """Verify commands fail gracefully with missing/invalid arguments."""

    def test_run_no_args(self) -> None:
        """Test run without config file exits with error."""
        result = runner.invoke(app, ["run"])
        assert result.exit_code != 0

    def test_validate_no_args(self) -> None:
        """Test validate without config file exits with error."""
        result = runner.invoke(app, ["validate"])
        assert result.exit_code != 0

    def test_resume_no_args(self) -> None:
        """Test resume without job_id exits with error."""
        result = runner.invoke(app, ["resume"])
        assert result.exit_code != 0

    def test_status_nonexistent_job(self, tmp_path: Path) -> None:
        """Test status with nonexistent job returns error."""
        workspace = tmp_path / "empty_ws"
        workspace.mkdir()
        result = runner.invoke(app, ["status", "nonexistent-job", "--workspace", str(workspace)])
        assert result.exit_code != 0

    def test_errors_no_args(self) -> None:
        """Test errors without job_id exits with error."""
        result = runner.invoke(app, ["errors"])
        assert result.exit_code != 0

    def test_history_no_args(self) -> None:
        """Test history without job_id exits with error."""
        result = runner.invoke(app, ["history"])
        assert result.exit_code != 0

    def test_recover_no_args(self) -> None:
        """Test recover without job_id exits with error."""
        result = runner.invoke(app, ["recover"])
        assert result.exit_code != 0

    def test_pause_no_args(self) -> None:
        """Test pause without job_id exits with error."""
        result = runner.invoke(app, ["pause"])
        assert result.exit_code != 0

    def test_unknown_command(self) -> None:
        """Test invoking a nonexistent command exits with error."""
        result = runner.invoke(app, ["nonexistent-command-xyz"])
        assert result.exit_code != 0


# =============================================================================
# Global flags smoke tests
# =============================================================================


class TestGlobalFlags:
    """Verify global flags work with various commands."""

    def test_verbose_flag(self) -> None:
        """Test --verbose flag doesn't crash."""
        result = runner.invoke(app, ["--verbose", "validate", "--help"])
        assert result.exit_code == 0

    def test_quiet_flag(self) -> None:
        """Test --quiet flag doesn't crash."""
        result = runner.invoke(app, ["--quiet", "validate", "--help"])
        assert result.exit_code == 0

    def test_version_flag(self) -> None:
        """Test --version flag shows version and exits."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "Marianne AI Compose" in result.stdout


# =============================================================================
# LogFollower unit tests
# =============================================================================


class TestLogFollower:
    """Unit tests for the extracted LogFollower class."""

    def test_parse_line_json(self, tmp_path: Path) -> None:
        """JSON lines are parsed into dicts."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log")
        entry = follower.parse_line('{"event": "started", "level": "INFO"}')
        assert entry is not None
        assert entry["event"] == "started"
        assert entry["level"] == "INFO"

    def test_parse_line_plain_text(self, tmp_path: Path) -> None:
        """Non-JSON lines are wrapped in a dict with _raw flag."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log")
        entry = follower.parse_line("plain text log line")
        assert entry is not None
        assert entry["_raw"] is True
        assert entry["event"] == "plain text log line"

    def test_parse_line_empty(self, tmp_path: Path) -> None:
        """Empty lines return None."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log")
        assert follower.parse_line("") is None
        assert follower.parse_line("   ") is None

    def test_should_include_no_filters(self, tmp_path: Path) -> None:
        """With no filters, all entries pass."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log")
        assert follower.should_include({"event": "x", "level": "DEBUG"}) is True
        assert follower.should_include({"event": "x", "level": "ERROR"}) is True

    def test_should_include_level_filter(self, tmp_path: Path) -> None:
        """Level filter excludes entries below threshold."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log", min_level=2)  # WARNING
        assert follower.should_include({"event": "x", "level": "DEBUG"}) is False
        assert follower.should_include({"event": "x", "level": "INFO"}) is False
        assert follower.should_include({"event": "x", "level": "WARNING"}) is True
        assert follower.should_include({"event": "x", "level": "ERROR"}) is True

    def test_should_include_job_filter(self, tmp_path: Path) -> None:
        """Job ID filter excludes non-matching entries."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log", job_id="my-job")
        assert follower.should_include({"event": "x", "job_id": "my-job"}) is True
        assert follower.should_include({"event": "x", "job_id": "other"}) is False
        assert follower.should_include({"event": "x"}) is False

    def test_format_entry_json_mode(self, tmp_path: Path) -> None:
        """JSON output mode returns raw JSON string."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log", json_output=True)
        import json

        entry = {"event": "test", "level": "INFO"}
        result = follower.format_entry(entry)
        assert json.loads(result) == entry

    def test_format_entry_raw_line(self, tmp_path: Path) -> None:
        """Raw lines are returned as-is."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log")
        result = follower.format_entry({"event": "plain text", "_raw": True})
        assert result == "plain text"

    def test_format_entry_structured(self, tmp_path: Path) -> None:
        """Structured entries include level, component, and event."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "test.log")
        entry = {
            "timestamp": "2026-01-01T10:30:00+00:00",
            "level": "WARNING",
            "event": "rate_limit_detected",
            "component": "runner",
            "job_id": "test-job",
            "sheet_num": 3,
        }
        result = follower.format_entry(entry)
        assert "WARNING" in result
        assert "runner" in result
        assert "rate_limit_detected" in result
        assert "sheet:3" in result

    def test_read_lines_from_file(self, tmp_path: Path) -> None:
        """Reading lines from a real file works."""
        from marianne.cli.commands.diagnose import LogFollower

        log_file = tmp_path / "test.log"
        log_file.write_text("line1\nline2\nline3\n")
        follower = LogFollower(log_path=log_file)
        lines = follower.read_lines()
        assert len(lines) == 3

    def test_read_lines_with_limit(self, tmp_path: Path) -> None:
        """Line limit returns only the last N lines."""
        from marianne.cli.commands.diagnose import LogFollower

        log_file = tmp_path / "test.log"
        log_file.write_text("line1\nline2\nline3\nline4\nline5\n")
        follower = LogFollower(log_path=log_file)
        lines = follower.read_lines(num_lines=2)
        assert len(lines) == 2
        assert "line4" in lines[0]
        assert "line5" in lines[1]

    def test_read_lines_nonexistent_file(self, tmp_path: Path) -> None:
        """Reading from nonexistent file returns empty list."""
        from marianne.cli.commands.diagnose import LogFollower

        follower = LogFollower(log_path=tmp_path / "missing.log")
        lines = follower.read_lines()
        assert lines == []
