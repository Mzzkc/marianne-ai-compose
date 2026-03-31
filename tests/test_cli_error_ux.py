"""Tests for CLI error UX improvements.

F-031: Malformed YAML in `mozart run` should show "YAML syntax error",
not a misleading Pydantic "Schema validation failed" message.

F-110 (partial): Backpressure rejection should NOT show "conductor is
not running" — the conductor IS running, it's just refusing work.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from mozart.cli import app

runner = CliRunner()


# =============================================================================
# F-031: Malformed YAML produces clear YAML syntax error
# =============================================================================


class TestF031YamlSyntaxError:
    """YAML syntax errors in `mozart run` must say 'YAML syntax error',
    not 'Schema validation failed'."""

    def test_malformed_yaml_in_run_shows_yaml_error(self, tmp_path: Path) -> None:
        """Malformed YAML produces a clear 'YAML syntax error' message."""
        bad = tmp_path / "bad.yaml"
        # Unclosed flow sequence — guaranteed ScannerError
        bad.write_text("key: [unclosed, list\n")
        result = runner.invoke(app, ["run", str(bad)])
        assert result.exit_code != 0
        output = result.output.lower()
        assert "yaml syntax error" in output
        # Must NOT show misleading Pydantic schema error
        assert "schema validation failed" not in output

    def test_malformed_yaml_in_run_json_mode(self, tmp_path: Path) -> None:
        """JSON output also shows YAML error, not schema error."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: test\ninvalid: {{{broken")
        result = runner.invoke(app, ["run", str(bad), "--json"])
        assert result.exit_code != 0
        # The JSON output should contain error info
        output = result.output.lower()
        assert "yaml" in output or "parse" in output
        assert "schema validation failed" not in output

    def test_empty_yaml_in_run_shows_clear_error(self, tmp_path: Path) -> None:
        """Empty YAML file produces a clear message, not 'NoneType is not iterable'."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        result = runner.invoke(app, ["run", str(empty)])
        assert result.exit_code != 0
        output = result.output.lower()
        assert "nonetype" not in output
        # Should mention empty or invalid score
        assert "empty" in output or "invalid" in output

    def test_list_yaml_in_run_shows_clear_error(self, tmp_path: Path) -> None:
        """YAML that parses to a list (not dict) produces a clear message."""
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2\n")
        result = runner.invoke(app, ["run", str(list_yaml)])
        assert result.exit_code != 0
        output = result.output.lower()
        # Should say it needs to be a mapping/dict, not show internal Python error
        assert "mapping" in output or "key-value" in output or "empty" in output or "invalid" in output

    def test_yaml_error_includes_hint(self, tmp_path: Path) -> None:
        """YAML syntax errors should include a helpful hint."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(":\n  - {{{bad")
        result = runner.invoke(app, ["run", str(bad)])
        assert result.exit_code != 0
        output = result.output.lower()
        # Should suggest using validate command or checking syntax
        assert "validate" in output or "syntax" in output or "indentation" in output


# =============================================================================
# F-110 (partial): Backpressure rejection should not say "not running"
# =============================================================================


class TestF110BackpressureUX:
    """When the conductor rejects a job due to backpressure, the error
    message must NOT say 'conductor is not running'."""

    @staticmethod
    def _make_config(tmp_path: Path) -> Path:
        import yaml

        config = {
            "name": "test-job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 10, "total_items": 10},
            "prompt": {"template": "test"},
        }
        config_path = tmp_path / "test.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        return config_path

    def test_backpressure_rejection_no_not_running_message(
        self, tmp_path: Path
    ) -> None:
        """Backpressure rejection shows rejection reason, not 'not running'."""
        config_path = self._make_config(tmp_path)

        # Mock daemon as available but rejecting due to backpressure
        with (
            patch(
                "mozart.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "mozart.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(
                    True,
                    {
                        "job_id": "",
                        "status": "rejected",
                        "message": "System under high pressure — try again later",
                    },
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output.lower()
        # Must NOT say conductor is not running
        assert "not running" not in output
        # Should show the actual rejection reason
        assert "rejected" in output or "pressure" in output or "try again" in output

    def test_shutdown_rejection_no_not_running_message(
        self, tmp_path: Path
    ) -> None:
        """Shutdown rejection shows shutdown reason, not 'not running'."""
        config_path = self._make_config(tmp_path)

        with (
            patch(
                "mozart.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "mozart.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(
                    True,
                    {
                        "job_id": "",
                        "status": "rejected",
                        "message": "Daemon is shutting down",
                    },
                ),
            ),
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        output = result.output.lower()
        assert "not running" not in output
        assert "shutting down" in output or "rejected" in output

    def test_genuine_not_running_still_shows_not_running(
        self, tmp_path: Path
    ) -> None:
        """When daemon is genuinely not reachable, 'not running' IS shown."""
        config_path = self._make_config(tmp_path)

        # Daemon genuinely not available
        with patch(
            "mozart.daemon.detect.is_daemon_available",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = runner.invoke(app, ["run", str(config_path)])

        assert result.exit_code != 0
        assert "not running" in result.output.lower()
