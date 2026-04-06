"""Tests for ``mozart instruments`` CLI commands.

Tests cover:
- ``mozart instruments list`` — table output, JSON output, empty state, mixed readiness
- ``mozart instruments check <name>`` — binary found, not found, unknown instrument
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
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
        capabilities={"tool_use", "structured_output"},
        default_model=default_model,
        default_timeout_seconds=300.0,
        cli=CliProfile(
            command=CliCommand(executable=executable, prompt_flag="-p"),
            output=CliOutputConfig(format="text"),
        ),
    )


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
# mozart instruments list
# ---------------------------------------------------------------------------


class TestInstrumentsList:
    """Tests for the ``mozart instruments list`` command."""

    def test_list_shows_table_header(self) -> None:
        """Output contains the expected table columns."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "NAME" in result.stdout
        assert "KIND" in result.stdout
        assert "STATUS" in result.stdout

    def test_list_shows_instrument_name(self) -> None:
        """Each instrument's name appears in the output."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
            "gemini-cli": _make_cli_profile("gemini-cli", "Gemini CLI", "gemini"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value=None):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "claude-code" in result.stdout
        assert "gemini-cli" in result.stdout

    def test_list_shows_ready_status_when_binary_found(self) -> None:
        """CLI instruments with found binaries show ready status."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "ready" in result.stdout.lower()

    def test_list_shows_not_found_when_binary_missing(self) -> None:
        """CLI instruments with missing binaries show not found."""
        profiles = {
            "codex-cli": _make_cli_profile("codex-cli", "Codex CLI", "codex"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value=None):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "not found" in result.stdout.lower()

    def test_list_shows_default_model(self) -> None:
        """Default model is displayed in the table."""
        profiles = {
            "gemini-cli": _make_cli_profile(
                "gemini-cli", "Gemini CLI", "gemini", default_model="gemini-2.5-pro"
            ),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/gemini"):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "gemini-2.5-pro" in result.stdout

    def test_list_shows_instrument_default_when_no_model(self) -> None:
        """Instruments with no default_model show a fallback label."""
        profiles = {
            "claude-code": _make_cli_profile(
                "claude-code", "Claude Code", "claude", default_model=None
            ),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        # Should show a fallback like "(instrument default)"
        assert "instrument default" in result.stdout.lower()

    def test_list_empty_registry(self) -> None:
        """Empty registry shows a helpful message."""
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value={},
        ):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "no instruments" in result.stdout.lower()

    def test_list_shows_summary_count(self) -> None:
        """Summary line shows instrument count and ready count."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
            "codex-cli": _make_cli_profile("codex-cli", "Codex CLI", "codex"),
        }

        def _selective_which(exe: str) -> str | None:
            return "/usr/bin/claude" if exe == "claude" else None

        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", side_effect=_selective_which):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "2" in result.stdout  # total count
        assert "1" in result.stdout  # ready count

    def test_list_json_output(self) -> None:
        """--json flag produces JSON output."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "list", "--json"])

        assert result.exit_code == 0
        import json

        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "claude-code"
        assert data[0]["ready"] is True

    def test_list_shows_kind(self) -> None:
        """Each instrument's kind (cli/http) is shown."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "cli" in result.stdout.lower()


# ---------------------------------------------------------------------------
# mozart instruments check
# ---------------------------------------------------------------------------


class TestInstrumentsCheck:
    """Tests for the ``mozart instruments check <name>`` command."""

    def test_check_binary_found(self) -> None:
        """Check passes when binary is found on PATH."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "check", "claude-code"])

        assert result.exit_code == 0
        assert "claude-code" in result.stdout.lower()
        assert "/usr/bin/claude" in result.stdout

    def test_check_binary_not_found(self) -> None:
        """Check reports failure when binary is missing."""
        profiles = {
            "codex-cli": _make_cli_profile("codex-cli", "Codex CLI", "codex"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value=None):
            result = runner.invoke(app, ["instruments", "check", "codex-cli"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_check_unknown_instrument(self) -> None:
        """Check errors for instruments not in the registry."""
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value={},
        ):
            result = runner.invoke(app, ["instruments", "check", "nonexistent"])

        assert result.exit_code == 1
        assert "nonexistent" in result.stdout.lower()

    def test_check_shows_capabilities(self) -> None:
        """Check output includes capability list."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["instruments", "check", "claude-code"])

        assert result.exit_code == 0
        assert "tool_use" in result.stdout

    def test_check_shows_model_info(self) -> None:
        """Check output includes model information."""
        profiles = {
            "gemini-cli": _make_cli_profile(
                "gemini-cli", "Gemini CLI", "gemini", default_model="gemini-2.5-pro"
            ),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/gemini"):
            result = runner.invoke(app, ["instruments", "check", "gemini-cli"])

        assert result.exit_code == 0
        assert "gemini-2.5-pro" in result.stdout

    def test_check_json_output(self) -> None:
        """--json flag produces structured JSON for check."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ), patch("shutil.which", return_value="/usr/bin/claude"):
            result = runner.invoke(
                app, ["instruments", "check", "claude-code", "--json"]
            )

        assert result.exit_code == 0
        import json

        data = json.loads(result.stdout)
        assert data["name"] == "claude-code"
        assert data["binary_found"] is True
        assert data["binary_path"] == "/usr/bin/claude"

    def test_check_http_instrument_skips_binary(self) -> None:
        """HTTP instruments don't have a binary check — just show info."""
        from marianne.core.config.instruments import HttpProfile

        http_profile = InstrumentProfile(
            name="anthropic-api",
            display_name="Anthropic API",
            description="Direct API access",
            kind="http",
            http=HttpProfile(
                base_url="https://api.anthropic.com",
                endpoint="/v1/messages",
                schema_family="anthropic",
                auth_env_var="ANTHROPIC_API_KEY",
            ),
        )
        profiles = {"anthropic-api": http_profile}
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value=profiles,
        ):
            result = runner.invoke(app, ["instruments", "check", "anthropic-api"])

        assert result.exit_code == 0
        assert "anthropic-api" in result.stdout.lower()
        # HTTP instruments should show endpoint info
        assert "api.anthropic.com" in result.stdout
