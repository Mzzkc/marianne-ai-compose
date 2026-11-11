"""GH #536: an opt-in switch keeps the operator's user-level harness hooks out of a musician.

The profile declares ``cli.command.disable_hooks_args``; both the headless and the
interactive builders carry it. A profile without the field builds the argv it
always built.
"""

from __future__ import annotations

import json
from pathlib import Path

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from marianne.instruments.loader import InstrumentProfileLoader
from tests.test_interactive_mode import FakeTmux, make_interactive_config

BUILTINS = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"
ISOLATION = ["--settings", '{"disableAllHooks":true}']


def _profile(isolation: list[str] | None, *, interactive: bool = False) -> InstrumentProfile:
    fields: dict[str, object] = {
        "executable": "claude", "prompt_flag": "-p", "model_flag": "--model",
        "extra_flags": ["--effort", "max"],
    }
    if isolation is not None:
        fields["disable_hooks_args"] = isolation
    return InstrumentProfile.model_validate({
        "name": "claude-code", "display_name": "c", "kind": "cli", "default_model": "m-1",
        "models": [],
        "cli": CliProfile(
            command=CliCommand(**fields),
            output=CliOutputConfig(format="json", result_path="result"),
            interactive=make_interactive_config(extra_args=["--keep"]) if interactive else None,
        ),
    })


def test_headless_carries_disable_hooks_args_before_extra_flags() -> None:
    argv = PluginCliBackend(_profile(ISOLATION))._build_command("p", timeout_seconds=None)
    i = argv.index("--settings")
    assert argv[i:i + 2] == ISOLATION
    assert argv[-2:] == ["--effort", "max"]


def test_headless_without_the_field_is_todays_argv() -> None:
    with_none = PluginCliBackend(_profile(None))._build_command("p", timeout_seconds=None)
    empty = PluginCliBackend(_profile([]))._build_command("p", timeout_seconds=None)
    assert with_none == empty
    assert "--settings" not in with_none


def test_headless_is_isolated_exactly_once() -> None:
    argv = PluginCliBackend(_profile(ISOLATION))._build_command("p", timeout_seconds=None)
    assert argv.count("--settings") == 1


def test_interactive_carries_disable_hooks_args(tmp_path: Path) -> None:
    backend = InteractiveCliBackend(
        _profile(ISOLATION, interactive=True), working_directory=tmp_path,
        tmux=FakeTmux(),  # type: ignore[arg-type]
    )
    argv = backend._build_command()
    assert argv[argv.index("--settings"):][:2] == ISOLATION
    assert argv[-1] == "--keep"


def test_interactive_without_the_field_is_todays_argv(tmp_path: Path) -> None:
    backend = InteractiveCliBackend(
        _profile(None, interactive=True), working_directory=tmp_path,
        tmux=FakeTmux(),  # type: ignore[arg-type]
    )
    assert backend._build_command() == ["claude", "--model", "m-1", "--keep"]


def test_builtin_claude_code_does_not_disable_hooks_by_default() -> None:
    """Musicians run auto-approved; operator PreToolUse guards may be the only guardrail."""
    profiles = InstrumentProfileLoader.load_directory(BUILTINS)
    profile = profiles["claude-code"]
    assert profile.cli.command.disable_hooks_args == []  # type: ignore[union-attr]
    argv = PluginCliBackend(profile)._build_command("p", timeout_seconds=None)
    assert "disableAllHooks" not in " ".join(argv)
    assert "--settings" not in argv and "--bare" not in argv
    assert "--setting-sources" not in argv


def test_the_field_description_states_the_cost() -> None:
    description = CliCommand.model_fields["disable_hooks_args"].description or ""
    assert "DISABLE EVERY OPERATOR HOOK" in description and "safety hooks" in description


def test_opt_in_form_is_valid_settings_json() -> None:
    assert json.loads(ISOLATION[1]) == {"disableAllHooks": True}
