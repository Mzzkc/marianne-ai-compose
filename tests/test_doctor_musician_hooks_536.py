"""GH #536: ``mzt doctor`` lists the user-level harness hooks a sheet would run."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands.doctor import (
    _hook_command_name,
    _musician_hook_report,
    _user_harness_hooks,
)

runner = CliRunner()
doctor_module = importlib.import_module("marianne.cli.commands.doctor")
TOKEN = "sk-FAKE-TOKEN-123"


def _settings(**events: str) -> dict[str, object]:
    return {
        "env": {"ANTHROPIC_AUTH_TOKEN": TOKEN},
        "hooks": {
            event: [{"matcher": "", "hooks": [{"type": "command", "command": command}]}]
            for event, command in events.items()
        },
    }


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data))
    return path


# ── Name derivation: never print arguments, env or inline code ───────────

@pytest.mark.parametrize(("command", "expected"), [
    ("/home/op/bin/read-memory.sh --all", "read-memory.sh"),
    ("python3 /x/y/notify.py arg", "notify.py"),
    ("python3.12 -u /x/notify.py", "notify.py"),
    ("/usr/bin/env bash /x/guard.sh", "guard.sh"),
    ("node /x/scan.js", "scan.js"),
    (f"FOO={TOKEN} /x/hook.sh", "hook.sh"),
    (f"A=1 B={TOKEN} python3 /x/notify.py", "notify.py"),
    # prefix collision: not an interpreter, so it is the command itself
    ("bashful-secret-token --flag", "bashful-secret-token"),
    ("pythonista run", "pythonista"),
])
def test_command_name_is_the_program_basename(command: str, expected: str) -> None:
    assert _hook_command_name(command) == expected


@pytest.mark.parametrize("command", [
    f"bash -c 'curl -H \"Authorization: Bearer {TOKEN}\" https://x'",
    f"python -c \"print('{TOKEN}')\"",
    f"python3.12 -c 'import os; {TOKEN}'",
    f"sh -ec 'echo {TOKEN}'",
    f"node -e \"{TOKEN}\"",
    f"node --eval '{TOKEN}'",
    f"env FOO=bar bash -lc '{TOKEN}'",
])
def test_inline_scripts_are_a_placeholder(command: str) -> None:
    assert _hook_command_name(command) == "<inline script>"


@pytest.mark.parametrize("command", [
    "x" * 65,
    "$weird;chars",
    "",
    "FOO=bar",
    "bash",
    "'unterminated",
])
def test_unsafe_or_empty_names_are_unrecognised(command: str) -> None:
    assert _hook_command_name(command) == "<unrecognised>"


def test_a_quoted_path_with_spaces_names_only_the_basename() -> None:
    assert _hook_command_name("'/home/op/My Tools/guard.sh' --secret") == "guard.sh"
    assert _hook_command_name("/home/op/My\\ Tools/guard.sh") == "guard.sh"


def test_an_unquoted_path_with_spaces_prints_no_argument() -> None:
    """Split at the space: the name is the first word's basename, nothing after it."""
    name = _hook_command_name("/home/op/My Tools/guard.sh --secret")
    assert name == "My" and "secret" not in name


def test_listing_never_carries_arguments_env_or_paths(tmp_path: Path) -> None:
    path = _write(tmp_path, _settings(
        SessionStart=f"FOO={TOKEN} /home/op/bin/read-memory.sh --all",
        Stop=f"bash -c 'echo {TOKEN}'",
    ))
    rows = _user_harness_hooks(path)
    assert rows == [
        {"event": "SessionStart", "command": "read-memory.sh"},
        {"event": "Stop", "command": "<inline script>"},
    ]
    assert TOKEN not in json.dumps(rows) and "/home/op" not in json.dumps(rows)


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"hooks": "x"}', '{"hooks": {}}'])
def test_absent_or_unusable_settings_yield_no_hooks(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "settings.json"
    if content is not None:
        path.write_text(content)
    assert _user_harness_hooks(path) == []


# ── Classification ───────────────────────────────────────────────────────

def _report(tmp_path: Path, *, disabled: bool, **events: str) -> dict[str, object]:
    return _musician_hook_report(_write(tmp_path, _settings(**events)), disabled=disabled)


def test_context_events_warn_that_they_add_to_every_sheet(tmp_path: Path) -> None:
    report = _report(tmp_path, disabled=False, SessionStart="/b/mem.sh", UserPromptSubmit="/b/n.sh")
    assert report["status"] == "warning"
    detail = str(report["detail"])
    assert "context-injecting" in detail and "MARIANNE_SHEET" in detail
    effects = {h["event"]: h["effect"] for h in report["hooks"]}  # type: ignore[attr-defined]
    assert "adds to every sheet's context unless the hook checks MARIANNE_SHEET" in (
        effects["SessionStart"]
    )


def test_guard_events_are_intended_to_run_and_do_not_warn(tmp_path: Path) -> None:
    report = _report(tmp_path, disabled=False, PreToolUse="/b/guard.sh", PostToolUse="/b/log.sh")
    assert report["status"] == "ok"
    assert all(
        "intended for safety hooks" in h["effect"] for h in report["hooks"]  # type: ignore[attr-defined]
    )


def test_disabling_all_hooks_warns_when_guards_will_not_run(tmp_path: Path) -> None:
    report = _report(tmp_path, disabled=True, PreToolUse="/b/guard.sh", SessionStart="/b/m.sh")
    assert report["status"] == "warning"
    assert "will NOT run" in str(report["detail"]) and "auto-approval" in str(report["detail"])


def test_disabling_all_hooks_without_guards_is_ok(tmp_path: Path) -> None:
    assert _report(tmp_path, disabled=True, SessionStart="/b/m.sh")["status"] == "ok"


def test_no_hooks_is_quiet(tmp_path: Path) -> None:
    report = _musician_hook_report(tmp_path / "none.json", disabled=False)
    assert report["status"] == "ok" and report["hooks"] == []


# ── The command, with the host neutralised ───────────────────────────────

def test_default_settings_path_is_neutralised_in_tests() -> None:
    assert not doctor_module._claude_user_settings_path().exists()


def _invoke(tmp_path: Path, *args: str, disabled: bool, **events: str) -> str:
    path = _write(tmp_path, _settings(**events))
    with (
        patch("marianne.cli.commands.doctor._check_conductor_status",
              return_value=("not running", None)),
        patch("marianne.cli.commands.doctor._claude_user_settings_path", return_value=path),
        patch("marianne.cli.commands.doctor._claude_code_hooks_disabled", return_value=disabled),
    ):
        return runner.invoke(app, ["doctor", *args]).stdout


def test_builtin_profile_does_not_disable_hooks() -> None:
    assert doctor_module._claude_code_hooks_disabled() is False


def test_doctor_json_carries_the_report_and_no_token(tmp_path: Path) -> None:
    out = _invoke(
        tmp_path, "--json", disabled=False,
        SessionStart=f"FOO={TOKEN} /h/read-memory.sh", Stop=f"bash -c 'echo {TOKEN}'",
    )
    assert TOKEN not in out
    report = json.loads(out)["musician_hooks"]
    assert report["status"] == "warning"
    assert report["hooks"][0]["command"] == "read-memory.sh"


def test_doctor_text_never_prints_the_token(tmp_path: Path) -> None:
    out = _invoke(
        tmp_path, disabled=False,
        SessionStart=f"FOO={TOKEN} /h/read-memory.sh --all",
        UserPromptSubmit=f"python -c '{TOKEN}'",
        PreToolUse="/h/guard.sh",
    )
    assert "Musician hooks" in out and "read-memory.sh" in out and "<inline script>" in out
    assert TOKEN not in out and "--all" not in out and "/h/" not in out


def test_doctor_text_warns_about_guards_when_hooks_are_disabled(tmp_path: Path) -> None:
    out = _invoke(tmp_path, disabled=True, PreToolUse="/h/guard.sh")
    assert "will NOT run" in out


# --- conductor review, 2026-11-11: adversarial inputs for the display name ---------------

_PLANTED = "sk-FAKE-TOKEN-123"


@pytest.mark.parametrize(
    "command",
    [
        f"API_KEY={_PLANTED} /opt/hook.sh --flag {_PLANTED}",
        f"bash -c 'curl -H \"Authorization: Bearer {_PLANTED}\" x'",
        f'bash -lc "echo {_PLANTED}"',
        f"python3.12 -c \"print('{_PLANTED}')\"",
        f"env FOO={_PLANTED} python3 /x/guard.py {_PLANTED}",
        f"sh -x /tmp/{_PLANTED}/run.sh",
        f'node --eval "{_PLANTED}"',
        f"/usr/bin/env -S bash -c '{_PLANTED}'",
        f"python -m {_PLANTED}",
        f"sh -o {_PLANTED} /x/run.sh",
        f"node --require {_PLANTED} /x/hook.js",
        "'unterminated " + _PLANTED,
    ],
)
def test_no_argument_value_or_inline_text_is_ever_the_display_name(command: str) -> None:
    """A token in an env prefix, an argument, an option value or an inline script never shows."""
    from marianne.cli.commands.doctor import _hook_command_name

    assert _PLANTED not in _hook_command_name(command)


def test_an_option_value_is_not_mistaken_for_the_script() -> None:
    from marianne.cli.commands.doctor import _hook_command_name

    assert _hook_command_name("python -m some_module") == "<unrecognised>"
    assert _hook_command_name("sh -o pipefail /x/run.sh") == "<unrecognised>"
    assert _hook_command_name("python3 /x/guard.py") == "guard.py"
