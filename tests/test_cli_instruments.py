"""Tests for ``mzt instruments`` CLI commands.

Tests cover:
- ``mzt instruments list`` — table output, JSON output, empty state, mixed readiness
- ``mzt instruments check <name>`` — binary found, not found, unknown instrument
"""

from __future__ import annotations

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
        method: str,
        params: dict,
        *,
        socket_path=None,  # noqa: ANN001
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        _fake_route,
    )


# ---------------------------------------------------------------------------
# mzt instruments list
# ---------------------------------------------------------------------------


class TestInstrumentsResolve:
    """``mzt instruments resolve SCORE``: what each sheet's binding resolves to."""

    def test_resolve_reports_alias_profile_model_per_sheet(self, tmp_path) -> None:
        score = tmp_path / "s.yaml"
        score.write_text(
            "name: s\n"
            f"workspace: {tmp_path / 'ws'}\n"
            "instruments:\n"
            "  seat-a: {profile: codex-cli, config: {model: m-a}}\n"
            "  seat-b: {profile: claude-code, config: {model: m-b}}\n"
            "instrument: claude-code\n"
            "sheet: {size: 1, total_items: 3, per_sheet_instruments: {2: seat-a, 3: seat-b}}\n"
            "prompt: {template: hi}\n"
        )
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
            "codex-cli": _make_cli_profile("codex-cli", "Codex", "codex"),
        }
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles", return_value=profiles
        ):
            result = runner.invoke(app, ["instruments", "resolve", str(score), "--json"])
        assert result.exit_code == 0, result.output
        import json as _json

        start = result.output.find("{")
        rows = _json.loads(result.output[start:])["sheets"]
        assert [(r["sheet"], r["bound_through"], r["profile"], r["model"]) for r in rows] == [
            (1, "", "claude-code", None),
            (2, "seat-a", "codex-cli", "m-a"),
            (3, "seat-b", "claude-code", "m-b"),
        ]

    def test_resolve_table_names_distinct_bindings(self, tmp_path) -> None:
        score = tmp_path / "s.yaml"
        score.write_text(
            "name: s\n"
            f"workspace: {tmp_path / 'ws'}\n"
            "instrument: claude-code\n"
            "sheet: {size: 1, total_items: 2}\n"
            "prompt: {template: hi}\n"
        )
        profiles = {"claude-code": _make_cli_profile("claude-code", "Claude Code", "claude")}
        with patch(
            "marianne.cli.commands.instruments._load_all_profiles", return_value=profiles
        ):
            result = runner.invoke(app, ["instruments", "resolve", str(score)])
        assert result.exit_code == 0, result.output
        assert "2 sheets, 1 distinct profile+model bindings" in result.output


class TestInstrumentsList:
    """Tests for the ``mzt instruments list`` command."""

    def test_list_shows_table_header(self) -> None:
        """Output contains the expected table columns."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
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
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value=None),
        ):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "claude-code" in result.stdout
        assert "gemini-cli" in result.stdout

    def test_list_shows_ready_status_when_binary_found(self) -> None:
        """CLI instruments with found binaries show ready status."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "ready" in result.stdout.lower()

    def test_list_shows_not_found_when_binary_missing(self) -> None:
        """CLI instruments with missing binaries show not found."""
        profiles = {
            "codex-cli": _make_cli_profile("codex-cli", "Codex CLI", "codex"),
        }
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value=None),
        ):
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
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/gemini"),
        ):
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
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
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

        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", side_effect=_selective_which),
        ):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "2" in result.stdout  # total count
        assert "1" in result.stdout  # ready count

    def test_list_json_output(self) -> None:
        """--json flag produces JSON output."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
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
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            result = runner.invoke(app, ["instruments", "list"])

        assert result.exit_code == 0
        assert "cli" in result.stdout.lower()


# ---------------------------------------------------------------------------
# mzt instruments check
# ---------------------------------------------------------------------------


class TestInstrumentsCheck:
    """Tests for the ``mzt instruments check <name>`` command."""

    def test_check_binary_found(self) -> None:
        """Check passes when binary is found on PATH."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            result = runner.invoke(app, ["instruments", "check", "claude-code"])

        assert result.exit_code == 0
        assert "claude-code" in result.stdout.lower()
        assert "/usr/bin/claude" in result.stdout

    def test_check_binary_not_found(self) -> None:
        """Check reports failure when binary is missing."""
        profiles = {
            "codex-cli": _make_cli_profile("codex-cli", "Codex CLI", "codex"),
        }
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value=None),
        ):
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
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
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
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/gemini"),
        ):
            result = runner.invoke(app, ["instruments", "check", "gemini-cli"])

        assert result.exit_code == 0
        assert "gemini-2.5-pro" in result.stdout

    def test_check_json_output(self) -> None:
        """--json flag produces structured JSON for check."""
        profiles = {
            "claude-code": _make_cli_profile("claude-code", "Claude Code", "claude"),
        }
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            result = runner.invoke(app, ["instruments", "check", "claude-code", "--json"])

        assert result.exit_code == 0
        import json

        data = json.loads(result.stdout)
        assert data["name"] == "claude-code"
        assert data["binary_found"] is True
        assert data["binary_path"] == "/usr/bin/claude"

    def test_check_http_instrument_skips_binary(self) -> None:
        """HTTP instruments don't have a binary check — just show info.

        GH #447: a LOCAL server is probed for its served models by the one
        availability probe, so the test pins that call (nothing listens on
        port 9000) instead of depending on the machine.
        """
        from marianne.core.config.instruments import HttpProfile

        http_profile = InstrumentProfile(
            name="local-http",
            display_name="Local HTTP",
            description="OpenAI-compatible API access",
            kind="http",
            http=HttpProfile(
                base_url="http://localhost:9000/v1",
                endpoint="/chat/completions",
                schema_family="openai",
            ),
        )
        profiles = {"local-http": http_profile}
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value=profiles,
            ),
            patch(
                "marianne.instruments.availability.served_local_models",
                return_value=["any-model"],
            ),
        ):
            result = runner.invoke(app, ["instruments", "check", "local-http"])

        assert result.exit_code == 0
        assert "local-http" in result.stdout.lower()
        # HTTP instruments should show endpoint info
        assert "localhost:9000/v1/chat/completions" in result.stdout
        assert "Binary:" not in result.stdout

    def test_check_local_http_server_not_answering_is_not_ready(self) -> None:
        """GH #447: the same probe doctor uses — a loopback server that does
        not answer is `unreachable`, and `check` says so instead of 'ready'."""
        from marianne.core.config.instruments import HttpProfile

        http_profile = InstrumentProfile(
            name="local-http",
            display_name="Local HTTP",
            description="OpenAI-compatible API access",
            kind="http",
            http=HttpProfile(
                base_url="http://localhost:9000/v1",
                endpoint="/chat/completions",
                schema_family="openai",
            ),
        )
        with (
            patch(
                "marianne.cli.commands.instruments._load_all_profiles",
                return_value={"local-http": http_profile},
            ),
            patch(
                "marianne.instruments.availability.served_local_models",
                return_value=None,
            ),
        ):
            result = runner.invoke(app, ["instruments", "check", "local-http"])

        assert result.exit_code == 1
        assert "no server answered" in result.output


# ---------------------------------------------------------------------------
# mzt instruments check --live: the profile against the installed binary
# ---------------------------------------------------------------------------


def _script_profile(tmp_path, name: str, body: str, *, schema_flag: str | None = None,
                    fmt: str = "text") -> InstrumentProfile:
    script = tmp_path / f"{name}.sh"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    return InstrumentProfile(
        name=name, display_name=name, kind="cli", capabilities={"tool_use"},
        cli=CliProfile(
            command=CliCommand(
                executable=str(script), prompt_flag="-p", json_schema_flag=schema_flag,
            ),
            output=CliOutputConfig(format=fmt, result_path="result" if fmt == "json" else None),
        ),
    )


class TestInstrumentsCheckLive:
    def _run(self, profile: InstrumentProfile):
        import json as _json

        with patch(
            "marianne.cli.commands.instruments._load_all_profiles",
            return_value={profile.name: profile},
        ):
            result = runner.invoke(
                app, ["instruments", "check", profile.name, "--live", "--json", "--timeout", "30"],
            )
        out = result.output
        start = out.find("{\n")
        assert start >= 0, f"no JSON object in output: {out[:400]!r}"
        report, _ = _json.JSONDecoder().raw_decode(out[start:])
        return result, report

    def test_working_cli_passes(self, tmp_path) -> None:
        result, report = self._run(_script_profile(tmp_path, "good", 'cat >/dev/null; echo ok'))
        assert result.exit_code == 0, result.output
        assert report["ok"] is True and report["plain"]["ok"] is True
        assert report["schema"] is None

    def test_rejected_flag_fails_with_the_cli_words(self, tmp_path) -> None:
        """The crush --yolo shape: the binary is on PATH, the profile validates,
        and every call dies before reaching a provider."""
        body = 'echo "ERROR Unknown flag: --yolo." >&2; exit 1'
        result, report = self._run(_script_profile(tmp_path, "badflag", body))
        assert result.exit_code == 1
        assert report["ok"] is False and report["plain"]["ok"] is False
        assert report["plain"]["exit_code"] == 1

    def test_schema_mode_proves_structured_output(self, tmp_path) -> None:
        body = (
            'cat >/dev/null; echo \'{"type":"result","result":"x",'
            '"structured_output":{"n":7,"shape":{"kind":"int","value":7}}}\''
        )
        profile = _script_profile(tmp_path, "schema", body, schema_flag="--json-schema", fmt="json")
        result, report = self._run(profile)
        assert result.exit_code == 0, result.output
        assert report["schema"]["ok"] is True and report["schema"]["structured_ok"] is True

    def test_schema_mode_fails_when_the_nested_union_is_ignored(self, tmp_path) -> None:
        """A backend that answers the flat part and drops the nested
        discriminated union is the GH #486 shape: the old probe passed it."""
        body = 'cat >/dev/null; echo \'{"type":"result","result":"x","structured_output":{"n":7}}\''
        profile = _script_profile(tmp_path, "flat", body, schema_flag="--json-schema", fmt="json")
        result, report = self._run(profile)
        assert result.exit_code == 1
        # The backend validates structured_output against the schema, so the
        # missing required `shape` fails the call before the probe's own
        # branch check runs; either way the probe reports the schema mode red.
        assert report["schema"]["ok"] is False
        assert report["schema"].get("structured_ok") is not True

    def test_schema_mode_catches_unread_output_shape(self, tmp_path) -> None:
        """The Claude Code verbose shape before the fix: the flag is declared
        but the parser cannot find structured_output."""
        body = 'cat >/dev/null; echo \'{"type":"result","result":"x"}\''
        profile = _script_profile(
            tmp_path, "noshape", body, schema_flag="--json-schema", fmt="json",
        )
        result, report = self._run(profile)
        assert result.exit_code == 1
        assert report["plain"]["ok"] is True and report["schema"]["ok"] is False


def test_live_failure_reports_the_first_failed_mode() -> None:
    from marianne.cli.commands.instruments import LiveReport, live_failure

    base: LiveReport = {
        "name": "x", "model": None, "availability": "ready",
        "plain": {"ok": True}, "schema": None, "ok": True,
    }
    assert live_failure(base) is None
    assert live_failure({**base, "plain": {"ok": False, "classification": "TRANSIENT"}}) == (
        "plain", "TRANSIENT",
    )
    assert live_failure({**base, "schema": {"ok": False, "exit_code": 1}}) == ("schema", "exit 1")


def test_live_record_writes_replayable_fixture(tmp_path) -> None:
    """--record keeps the raw bytes, the exit code and the CLI version."""
    import json as _json

    profile = _script_profile(tmp_path, "rec", 'cat >/dev/null; echo ok; echo note >&2')
    out = tmp_path / "caps"
    with patch(
        "marianne.cli.commands.instruments._load_all_profiles",
        return_value={profile.name: profile},
    ):
        result = runner.invoke(
            app, ["instruments", "check", "rec", "--live", "--record", str(out), "--timeout", "30"],
        )
    assert result.exit_code == 0, result.output
    assert (out / "rec" / "plain.stdout").read_bytes() == b"ok\n"
    assert (out / "rec" / "plain.stderr").read_bytes() == b"note\n"
    meta = _json.loads((out / "rec" / "plain.json").read_text())
    assert meta["exit_code"] == 0 and meta["mode"] == "plain" and meta["cli_version"]
