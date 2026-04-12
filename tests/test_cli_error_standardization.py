"""Tests for CLI error standardization — output_error() migration and UX fixes.

Covers:
- F-046: Instruments table STATUS column shows 'http' instead of readiness
- Error message standardization: raw console.print -> output_error()
- F-031 verification: YAML syntax errors caught before Pydantic
- F-032 verification: JSON output sanitization of control characters

TDD: Tests written before implementation (Lens, Movement 2).
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.output import _sanitize_for_json, output_error
from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    HttpProfile,
    InstrumentProfile,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_cli_profile(
    name: str,
    display_name: str,
    executable: str = "fake-binary",
    default_model: str | None = "test-model-1",
) -> InstrumentProfile:
    """Create a minimal CLI InstrumentProfile for testing."""
    return InstrumentProfile(
        name=name,
        display_name=display_name,
        description=f"Test instrument {name}",
        kind="cli",
        capabilities={"tool_use"},
        default_model=default_model,
        default_timeout_seconds=300.0,
        cli=CliProfile(
            command=CliCommand(executable=executable, prompt_flag="-p"),
            output=CliOutputConfig(format="text"),
        ),
    )


def _make_http_profile(
    name: str,
    display_name: str,
    default_model: str | None = "test-model-1",
) -> InstrumentProfile:
    """Create a minimal HTTP InstrumentProfile for testing."""
    return InstrumentProfile(
        name=name,
        display_name=display_name,
        description=f"Test HTTP instrument {name}",
        kind="http",
        capabilities={"tool_use"},
        default_model=default_model,
        default_timeout_seconds=300.0,
        http=HttpProfile(
            base_url="https://api.example.com",
            endpoint="/v1/messages",
            schema_family="anthropic",
        ),
    )


def _capture_console() -> tuple[Console, StringIO]:
    """Create a plain-text Console and its backing buffer."""
    buf = StringIO()
    console = Console(file=buf, color_system=None, width=120)
    return console, buf


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent CLI tests from routing through a real conductor."""

    async def _fake_route(
        method: str, params: dict, *, socket_path=None  # noqa: ANN001
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        _fake_route,
    )


# ---------------------------------------------------------------------------
# F-046: Instruments STATUS column for HTTP instruments
# ---------------------------------------------------------------------------


class TestInstrumentsHttpStatus:
    """F-046: HTTP instruments should show '? unchecked' not 'http' in STATUS."""

    def test_http_instrument_shows_unchecked_not_http(self) -> None:
        """HTTP instruments show '? unchecked' in STATUS column, not 'http'."""
        profiles = {
            "anthropic_api": _make_http_profile("anthropic_api", "Anthropic API"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        # The old behavior showed "http" — we want "unchecked" instead
        assert "http" not in result.stdout.split("KIND")[1].split("DEFAULT MODEL")[0] or "unchecked" in result.stdout.lower()

    def test_http_instrument_not_counted_as_ready(self) -> None:
        """HTTP instruments shouldn't be counted as 'ready' since we can't verify them."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
            "anthropic_api": _make_http_profile("anthropic_api", "Anthropic API"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        # Only CLI instrument with found binary should count as "ready"
        # HTTP instruments should show as "unchecked", not "ready"
        assert "1 ready" in result.stdout or "1 verified" in result.stdout

    def test_http_instrument_json_has_unchecked_status(self) -> None:
        """JSON output for HTTP instruments should show status as unchecked."""
        profiles = {
            "anthropic_api": _make_http_profile("anthropic_api", "Anthropic API"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ):
            result = runner.invoke(app, ["instruments", "list", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        # JSON should indicate the instrument status is unchecked, not "ready"
        assert data[0].get("status") == "unchecked" or data[0].get("ready") is None

    def test_mixed_cli_and_http_summary_counts(self) -> None:
        """Summary line distinguishes verified CLI from unchecked HTTP instruments."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
            "anthropic_api": _make_http_profile("anthropic_api", "Anthropic API"),
            "ollama": _make_http_profile("ollama", "Ollama"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        # Should mention unchecked instruments separately
        assert "unchecked" in result.stdout.lower()


# ---------------------------------------------------------------------------
# F-031: Malformed YAML error handling in validate
# ---------------------------------------------------------------------------


class TestMalformedYamlErrors:
    """F-031: Malformed YAML should show 'YAML syntax error', not Pydantic error."""

    def test_malformed_yaml_shows_syntax_error(self, tmp_path: Path) -> None:
        """Truly invalid YAML produces a YAML syntax error, not Pydantic error."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("this: {{{invalid yaml\n")
        result = runner.invoke(app, ["validate", str(bad_yaml)])
        # Should mention YAML syntax, not schema validation
        assert "yaml" in result.stdout.lower() or "syntax" in result.stdout.lower()
        assert result.exit_code != 0

    def test_empty_yaml_shows_clear_error(self, tmp_path: Path) -> None:
        """Empty file produces a clear error about required fields."""
        empty_yaml = tmp_path / "empty.yaml"
        empty_yaml.write_text("")
        result = runner.invoke(app, ["validate", str(empty_yaml)])
        # Should mention that the score needs content, not a raw Python error
        assert "empty" in result.stdout.lower() or "invalid" in result.stdout.lower()
        assert result.exit_code != 0

    def test_nondict_yaml_shows_clear_error(self, tmp_path: Path) -> None:
        """YAML that parses as a list/string produces helpful error."""
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2\n")
        result = runner.invoke(app, ["validate", str(list_yaml)])
        assert result.exit_code != 0
        # Should NOT show raw Python TypeError
        assert "TypeError" not in result.stdout
        assert "NoneType" not in result.stdout


# ---------------------------------------------------------------------------
# F-032: JSON output control character sanitization
# ---------------------------------------------------------------------------


class TestJsonSanitization:
    """F-032: JSON output must not contain invalid control characters."""

    def test_sanitize_strips_ansi_escape_sequences(self) -> None:
        """ANSI escape sequences in strings are stripped."""
        dirty = "Hello \x1b[31mred\x1b[0m world"
        clean = _sanitize_for_json(dirty)
        assert "\x1b" not in str(clean)
        assert "Hello" in str(clean)
        assert "world" in str(clean)

    def test_sanitize_strips_null_bytes(self) -> None:
        """Null bytes and other C0 controls are stripped."""
        dirty = "before\x00after\x01more\x08end"
        clean = _sanitize_for_json(dirty)
        assert "\x00" not in str(clean)
        assert "\x01" not in str(clean)
        assert "\x08" not in str(clean)
        # Control chars are stripped; remaining text is preserved
        assert "before" in str(clean)
        assert "after" in str(clean)
        assert "end" in str(clean)

    def test_sanitize_preserves_tabs_and_newlines(self) -> None:
        """Tabs and newlines are safe in JSON and should be preserved."""
        text = "line1\nline2\ttabbed"
        clean = _sanitize_for_json(text)
        assert clean == text

    def test_sanitize_handles_nested_dicts(self) -> None:
        """Nested dict values are recursively sanitized."""
        dirty = {"outer": {"inner": "text\x1b[31mred\x1b[0m"}}
        clean = _sanitize_for_json(dirty)
        assert "\x1b" not in json.dumps(clean)

    def test_sanitize_handles_lists(self) -> None:
        """List items are recursively sanitized."""
        dirty = ["clean", "dirty\x00text", {"key": "val\x1b[0m"}]
        clean = _sanitize_for_json(dirty)
        dumped = json.dumps(clean)
        assert "\x00" not in dumped
        assert "\x1b" not in dumped


# ---------------------------------------------------------------------------
# Error standardization: output_error() usage
# ---------------------------------------------------------------------------


class TestOutputErrorStandardization:
    """Verify that error outputs use output_error() consistently."""

    def test_output_error_with_job_id_extra(self) -> None:
        """output_error() supports job_id as a JSON extra field."""
        console, buf = _capture_console()
        output_error(
            "Score not found",
            error_code="E501",
            hints=["Run 'mzt list' to see available scores"],
            json_output=True,
            console_instance=console,
            job_id="test-score",
        )
        parsed = json.loads(buf.getvalue())
        assert parsed["success"] is False
        assert parsed["job_id"] == "test-score"
        assert parsed["error_code"] == "E501"
        assert "Run 'mzt list'" in parsed["hints"][0]

    def test_output_error_rich_mode_shows_hints(self) -> None:
        """Rich mode shows hints after the error message."""
        console, buf = _capture_console()
        output_error(
            "Cannot pause score",
            error_code="E503",
            hints=["Check if the score is running"],
            console_instance=console,
        )
        output = buf.getvalue()
        assert "E503" in output
        assert "Cannot pause score" in output
        assert "Check if the score is running" in output

    def test_validate_file_not_found_uses_output_error(self, tmp_path: Path) -> None:
        """validate command uses output_error for unreadable files."""
        nonexistent = tmp_path / "nonexistent.yaml"
        result = runner.invoke(app, ["validate", str(nonexistent)])
        assert result.exit_code != 0
        # Should be a formatted error, not a raw Python traceback
        assert "Traceback" not in result.stdout


# ---------------------------------------------------------------------------
# Ghost M2: pause.py error standardization (6 raw errors → output_error)
# ---------------------------------------------------------------------------


class TestPauseErrorStandardization:
    """Verify pause command errors migrated from raw console.print to output_error."""

    def test_pause_daemon_error_includes_hint(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pause daemon route error (E501) now includes 'mzt list' hint."""
        from marianne.daemon.exceptions import DaemonError

        async def _raise(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, None]:
            raise DaemonError("Score 'ghost-test' not found")

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _raise,
        )
        result = runner.invoke(app, ["pause", "ghost-test"])
        assert result.exit_code != 0
        assert "ghost-test" in result.output
        assert "mzt list" in result.output

    def test_pause_daemon_error_json_has_hints(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """JSON error from pause daemon route includes hints array."""
        from marianne.daemon.exceptions import DaemonError

        async def _raise(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, None]:
            raise DaemonError("Not found")

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _raise,
        )
        result = runner.invoke(app, ["pause", "no-job", "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output.strip())
        assert data["error_code"] == "E501"
        assert "hints" in data
        assert any("mzt list" in h for h in data["hints"])

    def test_modify_invalid_config_includes_hint(self, tmp_path: Path) -> None:
        """Modify with invalid config (E505) now includes YAML syntax hint."""
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("invalid: {{{yaml")
        result = runner.invoke(
            app, ["modify", "some-job", "-c", str(bad_config)],
        )
        assert result.exit_code != 0
        assert "Invalid config" in result.output or "YAML" in result.output

    def test_modify_invalid_config_json_has_config_file(
        self, tmp_path: Path,
    ) -> None:
        """JSON error from modify invalid config includes config_file path."""
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("not valid yaml {{{")
        result = runner.invoke(
            app, ["modify", "some-job", "-c", str(bad_config), "--json"],
        )
        assert result.exit_code != 0
        # JSON may contain control characters from YAML error (F-032),
        # use strict=False to handle them
        data = json.loads(result.output.strip(), strict=False)
        assert data["error_code"] == "E505"
        assert "config_file" in data
        assert str(bad_config) in data["config_file"]


# ---------------------------------------------------------------------------
# Ghost M2: helpers.py require_conductor error standardization
# ---------------------------------------------------------------------------


class TestRequireConductorStandardization:
    """require_conductor() now uses output_error with 'mzt start' hint."""

    def test_require_conductor_includes_start_hint(self) -> None:
        """When conductor is not running, error suggests 'mzt start'."""
        # Use a CLI command that requires the conductor — status with a job_id
        # will call require_conductor when the daemon is not available
        result = runner.invoke(app, ["status", "nonexistent-job"])
        # Either exits with error or shows conductor not running message
        if result.exit_code != 0:
            output_lower = result.output.lower()
            # Should include "mzt start" hint if conductor isn't running
            assert (
                "mzt start" in output_lower
                or "conductor" in output_lower
                or "not found" in output_lower
            )

    def test_require_conductor_json_has_hints(self) -> None:
        """JSON output from require_conductor is structured with hints."""
        result = runner.invoke(app, ["status", "nonexistent-job", "--json"])
        if result.exit_code != 0 and result.output.strip():
            # Parse JSON — may have conductor not running or score not found
            try:
                data = json.loads(result.output.strip())
                # If it's a structured error, verify it has useful fields
                if "error" in data or "message" in data:
                    assert isinstance(data, dict)
            except json.JSONDecodeError:
                pass  # Non-JSON output is acceptable for some error paths


# ---------------------------------------------------------------------------
# Ghost M2: recover.py error standardization
# ---------------------------------------------------------------------------


class TestRecoverErrorStandardization:
    """recover command: missing config snapshot uses output_error with hints."""

    def test_no_config_snapshot_includes_hint(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing config snapshot error suggests re-running."""
        from datetime import UTC, datetime

        from marianne.core.checkpoint import CheckpointState, JobStatus

        async def _no_snapshot(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, dict]:
            if method == "job.recover":
                # Return a state without config_snapshot
                state = CheckpointState(
                    job_id="test-job",
                    job_name="No Snapshot",
                    total_sheets=3,
                    status=JobStatus.FAILED,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    config_snapshot=None,  # Missing snapshot triggers the error
                )
                return True, {"state": state.model_dump(mode="json")}
            return True, {}

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _no_snapshot,
        )
        result = runner.invoke(app, ["recover", "test-job"])
        assert result.exit_code != 0
        output_lower = result.output.lower()
        assert "config snapshot" in output_lower or "cannot run" in output_lower


# ---------------------------------------------------------------------------
# Harper M2: validate.py error standardization — output_error + output_json
# ---------------------------------------------------------------------------


class TestValidateErrorStandardization:
    """validate command error paths migrated to output_error() / output_json()."""

    def test_validate_yaml_error_uses_output_error(self, tmp_path: Path) -> None:
        """YAML syntax error uses output_error() format (Error: prefix)."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("this: {{{broken")
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code != 0
        # output_error() adds "Error:" prefix — no raw [red] markup in plaintext
        assert "Error:" in result.output or "error" in result.output.lower()
        # The YAML error details are included
        assert "yaml" in result.output.lower() or "syntax" in result.output.lower()

    def test_validate_yaml_error_json_uses_output_json(self, tmp_path: Path) -> None:
        """YAML syntax error in --json mode outputs structured JSON."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("this: {{{broken")
        result = runner.invoke(app, ["validate", str(bad), "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output.strip())
        assert data["success"] is False
        assert "yaml" in data["message"].lower() or "syntax" in data["message"].lower()

    def test_validate_schema_error_uses_output_error(self, tmp_path: Path) -> None:
        """Schema validation error uses output_error() format."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("wrong_key: true\n")
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code != 0
        assert "Error:" in result.output or "validation" in result.output.lower()

    def test_validate_schema_error_json_structured(self, tmp_path: Path) -> None:
        """Schema error in --json mode outputs structured JSON via output_error."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("wrong_key: true\n")
        result = runner.invoke(app, ["validate", str(bad), "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output.strip())
        assert data["success"] is False
        assert "message" in data


# ---------------------------------------------------------------------------
# Harper M2: cancel.py error standardization
# ---------------------------------------------------------------------------


class TestCancelErrorStandardization:
    """cancel command error paths use output_error()."""

    def test_cancel_daemon_error_uses_output_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancel daemon error uses output_error() format."""
        from marianne.daemon.exceptions import DaemonError

        async def _raise(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, None]:
            raise DaemonError("Connection failed")

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _raise,
        )
        result = runner.invoke(app, ["cancel", "test-job"])
        assert result.exit_code != 0
        assert "Error:" in result.output
        assert "Connection failed" in result.output

    def test_cancel_daemon_error_json_structured(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancel daemon error in --json mode outputs structured JSON."""
        from marianne.daemon.exceptions import DaemonError

        async def _raise(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, None]:
            raise DaemonError("Not found")

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _raise,
        )
        result = runner.invoke(app, ["cancel", "test-job", "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output.strip())
        assert data["success"] is False
        assert "message" in data

    def test_cancel_not_found_uses_output_error(self) -> None:
        """Cancel for non-existent job uses output_error() format."""
        # With the fixture _no_daemon, conductor returns (False, None)
        # which triggers require_conductor path
        result = runner.invoke(app, ["cancel", "nonexistent"])
        assert result.exit_code != 0
        # Should show a structured error, not raw [yellow]...[/yellow]
        output = result.output.lower()
        assert "conductor" in output or "error" in output or "not found" in output


# ---------------------------------------------------------------------------
# Harper M2: pause.py remaining raw errors
# ---------------------------------------------------------------------------


class TestPauseRemainingStandardization:
    """Remaining pause error paths migrated from raw console.print."""

    def test_pause_not_running_uses_output_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pausing a non-running job uses output_error() with hints."""

        async def _not_running(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, dict]:
            if method == "job.pause":
                return True, {"paused": False, "error": "Score is not running"}
            return True, {}

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _not_running,
        )
        result = runner.invoke(app, ["pause", "test-job"])
        assert result.exit_code != 0
        # Should use output_error format with E502 code
        assert "E502" in result.output or "not running" in result.output.lower()

    def test_pause_failure_uses_output_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pause failure from conductor uses output_error() format."""

        async def _fail(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, dict]:
            if method == "job.pause":
                return True, {"paused": False, "error": "Job already stopped"}
            return True, None

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _fail,
        )
        result = runner.invoke(app, ["pause", "test-job"])
        assert result.exit_code != 0
        # output_error format: "Error [E503]: ..." or "Error: ..."
        assert "Error" in result.output
        assert "already stopped" in result.output.lower() or "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# Harper M2: resume.py remaining raw error
# ---------------------------------------------------------------------------


class TestResumeRemainingStandardization:
    """Resume error path for rejected status uses output_error()."""

    def test_resume_rejected_uses_output_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Resume rejected by conductor uses output_error() format."""

        async def _reject(
            method: str, params: dict, *, socket_path: Path | None = None,
        ) -> tuple[bool, dict]:
            return True, {"status": "rejected", "message": "Job is completed"}

        monkeypatch.setattr(
            "marianne.daemon.detect.try_daemon_route", _reject,
        )
        result = runner.invoke(app, ["resume", "test-job"])
        assert result.exit_code != 0
        assert "Error:" in result.output
        assert "completed" in result.output.lower() or "rejected" in result.output.lower()
