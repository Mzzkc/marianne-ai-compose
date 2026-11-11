"""GH #535: ``instrument_config.effort`` — a per-sheet reasoning-effort level.

The profile declares the mapping (``cli.command.effort_flag`` plus
``effort_levels``, and ``schema_effort_levels`` for schema sheets); a score
sets ``effort`` at any instrument_config scope. A sheet that does not set the
key builds exactly the command it built before. A sheet that sets it REPLACES
the profile's default effort pair, in headless and interactive launches, and
a level the profile does not accept is refused by ``mzt validate`` (V335) and
again before any spawn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from marianne.core.config import JobConfig
from marianne.core.config.instrument_settings import EFFORT_LEVELS, InstrumentSettings
from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    EffortMapping,
    InstrumentProfile,
    InteractiveCliConfig,
    ModelCapacity,
)
from marianne.core.sheet import Sheet, build_sheets
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.musician import sheet_task
from marianne.daemon.baton.state import AttemptContext, AttemptMode
from marianne.execution.base import Backend, ExecutionResult, SheetRequestState
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from marianne.instruments.classes import load_class_map
from marianne.instruments.loader import InstrumentProfileLoader
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks.capability_classes import _ClassContext
from tests.test_interactive_mode import READY_SCREEN, FakeTmux, make_interactive_config

LEVELS = ["low", "medium", "high", "xhigh", "max"]
SCHEMA_LEVELS = ["low", "medium", "high"]
VOCABULARY = ("minimal", "low", "medium", "high", "xhigh", "max")
SCHEMA_RF: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "x", "schema": {"type": "object"}},
}
BUILTINS = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"
# The claude-code shape without `minimal`, so a vocabulary word the profile
# does not map can be refused by name.
MAPPING: dict[str, Any] = {
    "delivery": "flag",
    "flag": "--effort",
    "levels": {level: level for level in LEVELS},
    "schema_levels": SCHEMA_LEVELS,
}
_UNSET: Any = object()


def _command(**overrides: Any) -> CliCommand:
    """The claude-code command: default max, schema sheets at high."""
    fields: dict[str, Any] = {
        "executable": "claude",
        "prompt_flag": "-p",
        "prompt_via_stdin": False,
        "model_flag": "--model",
        "output_format_flag": "--output-format",
        "output_format_value": "json",
        "json_schema_flag": "--json-schema",
        "extra_flags": ["--verbose", "--effort", "max"],
        "schema_extra_flags": ["--effort", "high"],
    }
    fields.update(overrides)
    return CliCommand(**fields)


def _profile(
    name: str = "claude-code",
    *,
    command: CliCommand | None = None,
    interactive: InteractiveCliConfig | None = None,
    effort: Any = _UNSET,
    models: list[dict[str, Any]] | None = None,
) -> InstrumentProfile:
    return InstrumentProfile.model_validate({
        "name": name, "display_name": name, "kind": "cli", "default_model": "m-1",
        "models": models or [],
        "effort": MAPPING if effort is _UNSET else effort,
        "cli": CliProfile(
            command=command or _command(),
            output=CliOutputConfig(format="json", result_path="result"),
            interactive=interactive,
        ),
    })


def _unmapped_profile(name: str = "plain-cli") -> InstrumentProfile:
    """A full-replacement copy made before the mapping existed: the pair, no mapping."""
    return _profile(name, effort=None)


def _row(name: str, **fields: Any) -> dict[str, Any]:
    return {"name": name, "context_window": 1000, "cost_per_1k_input": 0.0,
            "cost_per_1k_output": 0.0, **fields}


def _efforts(argv: list[str]) -> list[str]:
    """Every value that follows an ``--effort`` token."""
    return [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == "--effort"]


# ── Layer 1: one Marianne vocabulary; the profile maps it ────────────────


def test_marianne_vocabulary_is_one_ordered_list() -> None:
    assert EFFORT_LEVELS == VOCABULARY


@pytest.mark.parametrize("level", VOCABULARY)
def test_every_vocabulary_word_loads(level: str) -> None:
    assert InstrumentSettings(effort=level)["effort"] == level


@pytest.mark.parametrize("value", ["", 3, True, ["low"], "turbo", "LOW", "none", "ultra"])
def test_effort_must_be_a_vocabulary_word(value: Any) -> None:
    with pytest.raises(ValidationError):
        InstrumentSettings(effort=value)


def test_settings_key_is_described() -> None:
    field = InstrumentSettings.model_fields["effort"]
    assert field.description
    for word in ("minimal", "max", "effort:", "V335"):
        assert word in field.description


@pytest.mark.parametrize("name", sorted(EffortMapping.model_fields))
def test_mapping_fields_are_described(name: str) -> None:
    assert EffortMapping.model_fields[name].description


def test_profile_and_model_rows_describe_their_effort_fields() -> None:
    assert "effort" in (InstrumentProfile.model_fields["effort"].description or "")
    assert "effort" in (ModelCapacity.model_fields["effort_levels"].description or "")


def test_the_old_cli_command_keys_are_gone() -> None:
    """One spelling of the mapping: the profile's `effort:` block."""
    for old in ("effort_flag", "effort_levels", "schema_effort_levels"):
        assert old not in CliCommand.model_fields
        with pytest.raises(ValidationError):
            _command(**{old: None})


@pytest.mark.parametrize(
    "mapping, needle",
    [
        ({**MAPPING, "flag": None}, "flag"),
        ({**MAPPING, "key": "x"}, "key"),
        ({**MAPPING, "delivery": "config_override"}, "key"),
        ({**MAPPING, "delivery": "config_override", "key": "k", "flag": None}, "flag"),
        ({**MAPPING, "delivery": "slug"}, "delivery"),
        ({**MAPPING, "levels": {"low": ""}}, "empty"),
        ({**MAPPING, "levels": {"turbo": "turbo"}}, "turbo"),
        ({**MAPPING, "schema_levels": ["low", "minimal"]}, "minimal"),
        ({**MAPPING, "schema_levels": ["low", "low"]}, "duplicate"),
        ({**MAPPING, "schema_levels": []}, "empty"),
    ],
)
def test_inconsistent_mapping_is_refused(mapping: dict[str, Any], needle: str) -> None:
    with pytest.raises(ValidationError, match=needle):
        _profile(effort=mapping)


@pytest.mark.parametrize(
    "command, needle",
    [
        # The profile's own defaults must be native values it maps...
        ({"extra_flags": ["--effort", "ultra"]}, "ultra"),
        # ...and its schema default must be one a schema level maps to:
        # the hang protection cannot be undone by a typo in the profile.
        ({"schema_extra_flags": ["--effort", "max"]}, "max"),
        ({"schema_extra_flags": None}, "max"),
    ],
)
def test_profile_defaults_must_be_mapped_values(
    command: dict[str, Any], needle: str,
) -> None:
    with pytest.raises(ValidationError, match=needle):
        _profile(command=_command(**command))


def test_model_effort_levels_need_a_delivery_shape() -> None:
    with pytest.raises(ValidationError, match="effort"):
        _profile(effort=None, models=[_row("m-1", effort_levels={"low": "low"})])


def test_profile_without_mapping_still_loads() -> None:
    """Backward compatible: the effort pair in extra_flags needs no mapping."""
    assert _unmapped_profile().effort is None


def test_builtin_claude_code_declares_the_mapping() -> None:
    profiles = InstrumentProfileLoader.load_directory(BUILTINS)
    profile = profiles["claude-code"]
    assert profile.effort is not None
    assert profile.effort.delivery == "flag" and profile.effort.flag == "--effort"
    # Several Marianne levels may map to one native level.
    assert profile.effort.levels == {
        "minimal": "low", "low": "low", "medium": "medium",
        "high": "high", "xhigh": "xhigh", "max": "max",
    }
    # `--effort max` with `--json-schema` hangs on Claude Code; xhigh is unmeasured.
    assert profile.effort.schema_levels == ["minimal", "low", "medium", "high"]
    command = profile.cli.command  # type: ignore[union-attr]
    # The defaults a score that sets nothing still gets.
    assert command.extra_flags == ["--effort", "max"]
    assert command.schema_extra_flags == ["--effort", "high"]


def test_builtin_claude_code_ollama_declares_none() -> None:
    """Verified without reasoning flags; a score cannot set effort on it."""
    profiles = InstrumentProfileLoader.load_directory(BUILTINS)
    assert profiles["claude-code-ollama"].effort is None


# ── The key at every scope instrument_config supports ────────────────────

_SCOPES = {
    "score": "instrument_config: {effort: low}\n",
    "movement": "movements:\n  1:\n    instrument_config: {effort: low}\n",
    "sheet": "sheet:\n  size: 1\n  total_items: 1\n"
             "  per_sheet_instrument_config: {1: {effort: low}}\n",
    "alias": "instrument: seat\ninstruments:\n  seat:\n    profile: claude-code\n"
             "    config: {effort: low}\n",
    "alias-chain-entry": "instrument: seat\ninstruments:\n  seat:\n    chain:\n"
                         "      - {profile: claude-code, config: {effort: low}}\n",
}


@pytest.mark.parametrize("scope", sorted(_SCOPES))
def test_effort_survives_yaml_load_at_each_scope(tmp_path: Path, scope: str) -> None:
    body = _SCOPES[scope]
    head = "name: effort\nworkspace: ./output\nprompt: {template: hi}\n"
    if "sheet:" not in body:
        head += "sheet: {size: 1, total_items: 1}\n"
    if "instrument:" not in body:
        head += "instrument: claude-code\n"
    path = tmp_path / "score.yaml"
    path.write_text(head + body, encoding="utf-8")
    config = JobConfig.from_yaml(path)
    assert config.unknown_fields == (), scope
    sheet = build_sheets(config)[0]
    assert sheet.instrument_name == "claude-code"
    assert sheet.instrument_config["effort"] == "low"


def test_narrower_scope_wins_and_null_restores_the_profile_default(tmp_path: Path) -> None:
    path = tmp_path / "score.yaml"
    path.write_text(
        "name: effort\nworkspace: ./output\nprompt: {template: hi}\n"
        "instrument: claude-code\ninstrument_config: {effort: low}\n"
        "movements:\n  2:\n    instrument_config: {effort: medium}\n"
        "sheet:\n  size: 1\n  total_items: 3\n"
        "  per_sheet_instrument_config: {3: {effort: null}}\n",
        encoding="utf-8",
    )
    sheets = build_sheets(JobConfig.from_yaml(path))
    assert [s.instrument_config.get("effort") for s in sheets] == ["low", "medium", None]


# ── Headless command building ────────────────────────────────────────────


def test_unset_key_builds_exactly_todays_command() -> None:
    backend = PluginCliBackend(_profile())
    plain = backend._build_command("p", timeout_seconds=None)
    schema = backend._build_command("p", timeout_seconds=None, response_format=SCHEMA_RF)
    assert plain[-3:] == ["--verbose", "--effort", "max"]
    assert schema[-2:] == ["--effort", "high"] and "--verbose" not in schema
    assert plain == backend._build_command("p", timeout_seconds=None, effort=None)
    # Byte-identical to a profile that declares no mapping at all.
    unmapped = PluginCliBackend(_unmapped_profile("claude-code"))
    assert plain == unmapped._build_command("p", timeout_seconds=None)


@pytest.mark.parametrize("level", LEVELS)
def test_sheet_effort_replaces_the_default_pair(level: str) -> None:
    argv = PluginCliBackend(_profile())._build_command(
        "p", timeout_seconds=None, effort=level,
    )
    assert _efforts(argv) == [level]
    assert argv.count("--effort") == 1
    assert "--verbose" in argv  # every other fixed flag is kept


def test_several_marianne_levels_may_map_to_one_native_level() -> None:
    mapping = {**MAPPING, "levels": {"minimal": "low", "low": "low", "high": "high"},
               "schema_levels": None}
    backend = PluginCliBackend(_profile(effort=mapping, command=_command(
        extra_flags=["--effort", "high"], schema_extra_flags=None)))
    for level in ("minimal", "low"):
        assert _efforts(backend._build_command("p", timeout_seconds=None, effort=level)) == ["low"]


@pytest.mark.parametrize("level", SCHEMA_LEVELS)
def test_sheet_effort_on_a_schema_sheet_replaces_the_schema_pair(level: str) -> None:
    argv = PluginCliBackend(_profile())._build_command(
        "p", timeout_seconds=None, response_format=SCHEMA_RF, effort=level,
    )
    assert _efforts(argv) == [level]
    assert "--verbose" not in argv  # still the schema flag set, not extra_flags


@pytest.mark.parametrize("level", ["max", "xhigh"])
def test_schema_sheet_refuses_a_level_outside_schema_levels(level: str) -> None:
    """The hang protection is not negotiable from a score: refused, not downgraded."""
    backend = PluginCliBackend(_profile())
    with pytest.raises(ValueError, match="low, medium, high") as excinfo:
        backend._build_command(
            "p", timeout_seconds=None, response_format=SCHEMA_RF, effort=level,
        )
    assert "schema" in str(excinfo.value) and level in str(excinfo.value)


def test_unmapped_vocabulary_word_is_refused_naming_the_accepted_levels() -> None:
    backend = PluginCliBackend(_profile())
    with pytest.raises(ValueError, match="low, medium, high, xhigh, max") as excinfo:
        backend._build_command("p", timeout_seconds=None, effort="minimal")
    assert "'minimal'" in str(excinfo.value)


def test_profile_without_mapping_works_unset_and_refuses_the_key() -> None:
    """A full-replacement copy with the pair in extra_flags: unchanged, or loud."""
    backend = PluginCliBackend(_unmapped_profile())
    assert backend._build_command("p", timeout_seconds=None)[-2:] == ["--effort", "max"]
    with pytest.raises(ValueError, match="effort mapping") as excinfo:
        backend._build_command("p", timeout_seconds=None, effort="low")
    assert "accepted levels: none" in str(excinfo.value)


def test_mapping_without_a_default_pair_appends_one() -> None:
    command = _command(extra_flags=["--verbose"], schema_extra_flags=None)
    backend = PluginCliBackend(_profile(
        command=command, effort={**MAPPING, "schema_levels": None},
    ))
    assert "--effort" not in backend._build_command("p", timeout_seconds=None)
    argv = backend._build_command("p", timeout_seconds=None, effort="medium")
    assert argv[-3:] == ["--verbose", "--effort", "medium"]


def test_equals_form_of_the_default_is_replaced_too() -> None:
    command = _command(extra_flags=["--effort=max", "--verbose"])
    argv = PluginCliBackend(_profile(command=command))._build_command(
        "p", timeout_seconds=None, effort="low",
    )
    assert "--effort=max" not in argv and _efforts(argv) == ["low"]


# ── Delivery shape: config override (codex `-c model_reasoning_effort=<v>`) ─

CODEX_MAPPING: dict[str, Any] = {
    "delivery": "config_override",
    "flag": "-c",
    "key": "model_reasoning_effort",
    "levels": {"minimal": "low", "low": "low", "medium": "medium", "high": "high"},
}


def _codex_profile(extra_flags: list[str] | None = None) -> InstrumentProfile:
    return _profile("codex-cli", effort=CODEX_MAPPING, command=CliCommand(
        executable="codex", subcommand="exec", model_flag="--model",
        extra_flags=extra_flags if extra_flags is not None else ["--skip-git-repo-check"],
    ))


def test_config_override_unset_adds_nothing() -> None:
    argv = PluginCliBackend(_codex_profile())._build_command("p", timeout_seconds=None)
    assert "-c" not in argv and argv[-1] == "--skip-git-repo-check"


@pytest.mark.parametrize("level, native", [("minimal", "low"), ("high", "high")])
def test_config_override_appends_one_key_value_pair(level: str, native: str) -> None:
    argv = PluginCliBackend(_codex_profile())._build_command(
        "p", timeout_seconds=None, effort=level,
    )
    assert argv[-2:] == ["-c", f"model_reasoning_effort={native}"]
    assert argv.count("-c") == 1


def test_config_override_replaces_only_its_own_key() -> None:
    """Another `-c` override in the fixed flags is kept; the effort one is replaced."""
    backend = PluginCliBackend(_codex_profile([
        "-c", "model_verbosity=low", "-c", "model_reasoning_effort=high",
    ]))
    argv = backend._build_command("p", timeout_seconds=None, effort="low")
    assert argv[-4:] == ["-c", "model_verbosity=low", "-c", "model_reasoning_effort=low"]


def test_config_override_default_must_be_a_mapped_value() -> None:
    with pytest.raises(ValidationError, match="xhigh"):
        _codex_profile(["-c", "model_reasoning_effort=xhigh"])


# ── Model-dependent levels (ModelCapacity.effort_levels) ─────────────────


def _per_model_profile() -> InstrumentProfile:
    return _profile("opencode", effort={
        "delivery": "flag", "flag": "--variant", "levels": {}, "interactive": False,
    }, command=CliCommand(executable="opencode", subcommand="run", model_flag="--model"),
        models=[
            _row("m-1", effort_levels={"minimal": "low", "low": "low", "high": "high",
                                       "max": "max"}),
            _row("plain-model"),
        ])


def test_model_row_levels_replace_the_profile_levels() -> None:
    profile = _per_model_profile()
    assert profile.accepted_effort_levels(model="m-1", schema_mode=False) == [
        "minimal", "low", "high", "max",
    ]
    assert profile.accepted_effort_levels(model="plain-model", schema_mode=False) == []
    argv = PluginCliBackend(profile)._build_command("p", timeout_seconds=None, effort="minimal")
    assert argv[-2:] == ["--variant", "low"]


def test_a_model_without_a_control_refuses_naming_the_model() -> None:
    backend = PluginCliBackend(_per_model_profile())
    backend.apply_overrides({"model": "plain-model"})
    with pytest.raises(ValueError, match="plain-model") as excinfo:
        backend._build_command("p", timeout_seconds=None, effort="low")
    assert "accepted levels: none" in str(excinfo.value)
    backend.clear_overrides()
    with pytest.raises(ValueError, match="'medium'.*minimal, low, high, max"):
        backend._build_command("p", timeout_seconds=None, effort="medium")


def test_unlisted_model_uses_the_profile_levels() -> None:
    profile = _profile(models=[_row("m-1", effort_levels={"low": "low"})])
    assert profile.accepted_effort_levels(model="other", schema_mode=False) == LEVELS
    assert profile.accepted_effort_levels(model="m-1", schema_mode=False) == ["low"]


def _fake_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.pid = 4242
    proc.stdin = None
    proc.stdout = AsyncMock()
    proc.stdout.read = AsyncMock(side_effect=[b'{"result": "ok"}', b""])
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(side_effect=[b""])
    proc.wait = AsyncMock(return_value=0)
    proc.kill = MagicMock()
    return proc


async def _spawned(backend: PluginCliBackend, request: SheetRequestState | None) -> list[str]:
    commands: list[list[str]] = []

    def _spawn(*args: Any, **_kwargs: Any) -> MagicMock:
        commands.append([str(a) for a in args])
        return _fake_proc()

    with patch("asyncio.create_subprocess_exec", side_effect=_spawn):
        await backend.execute("do the task", request=request)
    assert len(commands) == 1
    return commands[0]


def _unsupervised_profile() -> InstrumentProfile:
    return _profile(command=_command(supervise=False))


@pytest.mark.asyncio
async def test_execute_carries_the_request_effort_and_nothing_sticks() -> None:
    backend = PluginCliBackend(_unsupervised_profile())
    lowered = await _spawned(backend, SheetRequestState(effort="low"))
    assert _efforts(lowered) == ["low"]
    # Request-local: the next sheet on the same pooled backend is at the default.
    assert _efforts(await _spawned(backend, SheetRequestState())) == ["max"]
    assert _efforts(await _spawned(backend, None)) == ["max"]


# ── Interactive (tmux) launch ────────────────────────────────────────────


def _interactive_backend(
    tmp_path: Path, *, effort: Any = _UNSET,
    extra_args: list[str] | None = None, tmux: FakeTmux | None = None,
) -> InteractiveCliBackend:
    profile = _profile(
        effort=effort,
        interactive=make_interactive_config(extra_args=extra_args or []),
    )
    return InteractiveCliBackend(
        profile, working_directory=tmp_path, tmux=tmux or FakeTmux(),  # type: ignore[arg-type]
    )


def test_interactive_launch_unset_is_unchanged(tmp_path: Path) -> None:
    """Interactive launch never carried extra_flags; without the key it still does not."""
    backend = _interactive_backend(tmp_path)
    assert backend._build_command() == ["claude", "--model", "m-1"]
    assert backend._build_command(effort=None) == ["claude", "--model", "m-1"]


def test_interactive_launch_carries_the_sheet_effort(tmp_path: Path) -> None:
    backend = _interactive_backend(tmp_path)
    assert backend._build_command(effort="low") == [
        "claude", "--model", "m-1", "--effort", "low",
    ]


def test_interactive_extra_args_effort_pair_is_replaced(tmp_path: Path) -> None:
    backend = _interactive_backend(tmp_path, extra_args=["--effort", "max", "--keep"])
    assert _efforts(backend._build_command()) == ["max"]
    argv = backend._build_command(effort="medium")
    assert _efforts(argv) == ["medium"] and "--keep" in argv


def test_interactive_launch_refuses_what_the_profile_does_not_accept(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="low, medium, high, xhigh, max"):
        _interactive_backend(tmp_path)._build_command(effort="minimal")
    unmapped = _interactive_backend(tmp_path, effort=None)
    with pytest.raises(ValueError, match="effort mapping"):
        unmapped._build_command(effort="low")


def test_interactive_launch_refuses_a_mapping_that_is_headless_only(tmp_path: Path) -> None:
    """opencode's `--variant` exists on `opencode run` only, not on the TUI."""
    backend = _interactive_backend(tmp_path, effort={**MAPPING, "interactive": False})
    assert backend._build_command() == ["claude", "--model", "m-1"]
    with pytest.raises(ValueError, match="interactive") as excinfo:
        backend._build_command(effort="low")
    assert "accepted levels: none" in str(excinfo.value)
    assert backend.effort_refusal("low", schema_mode=False) is not None


@pytest.mark.asyncio
async def test_interactive_execute_launches_with_the_request_effort(tmp_path: Path) -> None:
    def make_done() -> str:
        for marker in (tmp_path / ".marianne" / "interactive").glob("*.complete"):
            marker.touch()
        return READY_SCREEN

    fake = FakeTmux([READY_SCREEN, make_done])
    backend = _interactive_backend(tmp_path, tmux=fake)
    backend.configure_interactive(max_nudges=1)
    await backend.execute(
        "do it", timeout_seconds=5.0, request=SheetRequestState(effort="low"),
    )
    assert fake.created
    assert _efforts([str(a) for a in fake.created[0][1]]) == ["low"]


# ── Dispatch: the key reaches the backend request-locally ────────────────


def _dispatch_setup(
    tmp_path: Path, config: dict[str, Any],
) -> tuple[BatonAdapter, Sheet, Any]:
    sheet = Sheet(
        num=1, movement=1, voice=None, voice_count=1,
        workspace=tmp_path, instrument_name="claude-code",
        prompt_template="work", instrument_config=config, timeout_seconds=10,
    )
    adapter = BatonAdapter()
    adapter.register_job("j535", [sheet], {})
    return adapter, sheet, adapter.baton._jobs["j535"].sheets[1]


async def _dispatch(
    adapter: BatonAdapter, sheet: Sheet, state: Any, backend: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> AttemptContext:
    captured: list[AttemptContext] = []

    async def capture(**kwargs: Any) -> None:
        captured.append(kwargs["context"])

    monkeypatch.setattr(adapter, "_musician_wrapper", capture)
    task, _ = adapter._create_musician_task_after_acquire(
        job_id="j535", sheet_num=1, sheet=sheet, state=state, backend=backend,
        effective_instrument=state.instrument_name, technique_manifest=None,
        technique_skill_docs={}, mcp_config_path=None, output_router_active=False,
        mcp_code_bridge_active=False, mcp_bind_mounts=[], event_generation=0,
    )
    await task
    return captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("config, expected", [
    ({}, None),
    ({"effort": None}, None),
    ({"effort": "low"}, "low"),
    ({"effort": "max"}, "max"),
])
async def test_attempt_context_carries_the_sheet_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any], expected: str | None,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, config)
    context = await _dispatch(adapter, sheet, state, PluginCliBackend(_profile()), monkeypatch)
    assert context.effort == expected


@pytest.mark.asyncio
async def test_dispatch_carries_effort_to_an_interactive_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, {"effort": "medium"})
    backend = _interactive_backend(tmp_path)
    context = await _dispatch(adapter, sheet, state, backend, monkeypatch)
    assert context.effort == "medium"


@pytest.mark.asyncio
@pytest.mark.parametrize("config, needle", [
    ({"effort": "minimal"}, "low, medium, high, xhigh, max"),
    ({"effort": "turbo"}, "minimal, low, medium, high, xhigh, max"),
    ({"effort": 3}, "effort"),
    ({"effort": "max", "response_format": SCHEMA_RF}, "low, medium, high"),
])
async def test_dispatch_refuses_a_level_before_any_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any], needle: str,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, config)
    with pytest.raises(ValueError, match=needle):
        await _dispatch(adapter, sheet, state, PluginCliBackend(_profile()), monkeypatch)


@pytest.mark.asyncio
async def test_dispatch_refuses_effort_on_a_backend_that_cannot_apply_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never run a sheet at an effort the author did not ask for, silently."""
    adapter, sheet, state = _dispatch_setup(tmp_path, {"effort": "low"})
    with pytest.raises(ValueError, match="instrument_config.effort"):
        await _dispatch(adapter, sheet, state, object(), monkeypatch)


@pytest.mark.asyncio
async def test_fallback_entry_uses_its_own_effort_not_the_primarys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, {"effort": "low"})
    state.fallback_chain = ["other-cli", "third-cli"]
    state.fallback_configs = [{}, {"effort": "high"}]
    assert state.advance_fallback("test") == "other-cli"
    backend = PluginCliBackend(_profile())
    assert (await _dispatch(adapter, sheet, state, backend, monkeypatch)).effort is None
    assert state.advance_fallback("test") == "third-cli"
    assert (await _dispatch(adapter, sheet, state, backend, monkeypatch)).effort == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "low"])
async def test_musician_threads_effort_into_the_request(
    tmp_path: Path, value: str | None,
) -> None:
    _, sheet, _ = _dispatch_setup(tmp_path, {})
    backend = AsyncMock(spec=Backend)
    backend.name = "claude-code"
    backend.execute.return_value = ExecutionResult(
        success=True, stdout="ok", stderr="", duration_seconds=0.1, exit_code=0,
    )
    context = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL, effort=value)
    inbox: asyncio.Queue[Any] = asyncio.Queue()
    await sheet_task(
        job_id="j535", sheet=sheet, backend=backend, attempt_context=context, inbox=inbox,
    )
    assert inbox.get_nowait().execution_success
    assert backend.execute.call_args.kwargs["request"].effort == value


# ── Validator: V335 ──────────────────────────────────────────────────────


def _ctx(tmp_path: Path, **profiles: InstrumentProfile) -> _ClassContext:
    class_map = load_class_map(
        user_path=tmp_path / "no-user.yaml", venue_path=tmp_path / "no-venue.yaml",
        profile_names=set(profiles),
    )
    return _ClassContext(profiles=profiles, class_map=class_map)


def _score(tmp_path: Path, **fields: Any) -> JobConfig:
    base: dict[str, Any] = {
        "name": "effort",
        "workspace": str(tmp_path / "ws"),
        "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 2},
        "prompt": {"template": "x"},
    }
    base.update(fields)
    return JobConfig.model_validate(base)


def _v335(cfg: JobConfig, ctx: _ClassContext) -> list[ValidationIssue]:
    from marianne.validation.checks.instrument_effort import SheetEffortCheck

    with patch(
        "marianne.validation.checks.instrument_effort._load_context", return_value=ctx,
    ):
        return SheetEffortCheck().check(cfg, Path("s.yaml"), "instrument_config:\n  effort: x\n")


def _two_profiles(tmp_path: Path) -> _ClassContext:
    return _ctx(tmp_path, **{
        "claude-code": _profile("claude-code"),
        "plain-cli": _unmapped_profile(),
    })


def test_v335_is_registered_as_an_error() -> None:
    from marianne.validation.output_contract import _known_suppression_severities

    assert _known_suppression_severities()["V335"] == ValidationSeverity.ERROR


def test_v335_silent_without_the_key_and_on_accepted_levels(tmp_path: Path) -> None:
    ctx = _two_profiles(tmp_path)
    assert _v335(_score(tmp_path), ctx) == []
    assert _v335(_score(tmp_path, instrument="plain-cli"), ctx) == []
    assert _v335(_score(tmp_path, instrument_config={"effort": "low"}), ctx) == []
    assert _v335(_score(tmp_path, instrument_config={"effort": None}), ctx) == []
    schema_ok = {"effort": "high", "response_format": SCHEMA_RF, "interactive": False}
    assert _v335(_score(tmp_path, instrument_config=schema_ok), ctx) == []


def test_v335_refuses_the_key_on_a_profile_with_no_mapping(tmp_path: Path) -> None:
    cfg = _score(tmp_path, instrument="plain-cli", instrument_config={"effort": "low"})
    issues = _v335(cfg, _two_profiles(tmp_path))
    assert [i.check_id for i in issues] == ["V335"]
    issue = issues[0]
    assert issue.severity == ValidationSeverity.ERROR
    assert "plain-cli" in issue.message and "sheet(s) 1, 2" in issue.message
    assert "accepted levels: none" in issue.message
    assert "reasoning-effort mapping" in issue.message and "effort:" in issue.message
    # The hint names where the key does work on this machine, with its levels.
    assert issue.suggestion is not None
    assert "claude-code" in issue.suggestion
    assert "low, medium, high, xhigh, max" in issue.suggestion
    assert issue.line == 2


def test_v335_refuses_a_level_the_profile_does_not_map(tmp_path: Path) -> None:
    cfg = _score(tmp_path, instrument_config={"effort": "minimal"})
    issues = _v335(cfg, _two_profiles(tmp_path))
    assert [i.check_id for i in issues] == ["V335"]
    assert "'minimal'" in issues[0].message
    assert "accepted levels: low, medium, high, xhigh, max" in issues[0].message


def test_v335_names_the_model_when_the_model_row_decides(tmp_path: Path) -> None:
    profile = _profile("opencode", effort={
        "delivery": "flag", "flag": "--variant", "levels": {}, "interactive": False,
    }, command=CliCommand(executable="opencode", subcommand="run", model_flag="--model"),
        models=[_row("m-1", effort_levels={"low": "low", "high": "high"}), _row("m-2")])
    ctx = _ctx(tmp_path, opencode=profile)
    ok = _score(tmp_path, instrument="opencode", instrument_config={"effort": "high"})
    assert _v335(ok, ctx) == []
    cfg = _score(tmp_path, instrument="opencode",
                 instrument_config={"effort": "high", "model": "m-2"})
    issues = _v335(cfg, ctx)
    assert [i.check_id for i in issues] == ["V335"]
    assert "model 'm-2'" in issues[0].message
    assert "accepted levels: none" in issues[0].message


def test_v335_refuses_max_on_a_schema_sheet_and_names_the_schema_levels(
    tmp_path: Path,
) -> None:
    cfg = _score(
        tmp_path, instrument_config={"effort": "max"},
        sheet={
            "size": 1, "total_items": 2,
            "per_sheet_instrument_config": {
                2: {"response_format": SCHEMA_RF, "interactive": False},
            },
        },
    )
    issues = _v335(cfg, _two_profiles(tmp_path))
    assert [i.check_id for i in issues] == ["V335"]
    assert "sheet(s) 2" in issues[0].message  # sheet 1 at max is fine
    assert "schema" in issues[0].message
    assert "low, medium, high" in issues[0].message
    assert "xhigh" not in issues[0].message.split("accepted")[-1]


def test_v335_judges_each_alias_chain_entry_against_its_own_profile(
    tmp_path: Path,
) -> None:
    """Alias-level config reaches every chain entry; one of them has no mapping."""
    cfg = _score(
        tmp_path, instrument="seat",
        instruments={"seat": {
            "chain": ["claude-code", "plain-cli"], "config": {"effort": "low"},
        }},
    )
    issues = _v335(cfg, _two_profiles(tmp_path))
    assert [i.check_id for i in issues] == ["V335"]
    assert "plain-cli" in issues[0].message and "fallback" in issues[0].message


def test_v335_skips_an_unregistered_instrument(tmp_path: Path) -> None:
    """An unknown name is V210's finding, not a second one here."""
    cfg = _score(tmp_path, instrument="nobody", instrument_config={"effort": "low"})
    assert _v335(cfg, _two_profiles(tmp_path)) == []


def test_validator_and_runtime_refuse_in_the_same_words(tmp_path: Path) -> None:
    cfg = _score(tmp_path, instrument_config={"effort": "minimal"})
    message = _v335(cfg, _two_profiles(tmp_path))[0].message
    with pytest.raises(ValueError) as excinfo:
        PluginCliBackend(_profile())._build_command(
            "p", timeout_seconds=None, effort="minimal",
        )
    reason = _profile().effort_refusal("minimal", model="m-1", schema_mode=False)
    assert reason is not None and reason in message and reason in str(excinfo.value)


# ── Plugin profiles: a third-party YAML declares a mapping, no code change ─

_PLUGIN_FLAG = """\
name: acme-agent
display_name: Acme Agent
kind: cli
default_model: acme-1
models:
  - {name: acme-1, context_window: 1000, cost_per_1k_input: 0, cost_per_1k_output: 0}
  - name: acme-mini
    context_window: 1000
    cost_per_1k_input: 0
    cost_per_1k_output: 0
    effort_levels: {}
effort:
  delivery: flag
  flag: "--think"
  levels: {minimal: quick, low: quick, medium: normal, high: deep, max: deep}
cli:
  command: {executable: acme, prompt_flag: "-p", model_flag: "--model"}
  output: {format: text}
"""

_PLUGIN_OVERRIDE = """\
name: acme-config
display_name: Acme Config
kind: cli
effort:
  delivery: config_override
  flag: "--set"
  key: reasoning.level
  levels: {low: "1", medium: "2", high: "3"}
cli:
  command:
    executable: acme
    extra_flags: ["--set", "reasoning.level=2", "--set", "colour=off"]
  output: {format: text}
"""


def test_plugin_profiles_declare_each_cli_shape_from_yaml(tmp_path: Path) -> None:
    (tmp_path / "acme-agent.yaml").write_text(_PLUGIN_FLAG, encoding="utf-8")
    (tmp_path / "acme-config.yaml").write_text(_PLUGIN_OVERRIDE, encoding="utf-8")
    profiles = InstrumentProfileLoader.load_directory(tmp_path)

    flag = PluginCliBackend(profiles["acme-agent"])
    assert flag._build_command("p", timeout_seconds=None, effort="max")[-2:] == [
        "--think", "deep",
    ]
    assert "--think" not in flag._build_command("p", timeout_seconds=None)
    flag.apply_overrides({"model": "acme-mini"})
    assert "model 'acme-mini'" in (flag.effort_refusal("low", schema_mode=False) or "")

    override = PluginCliBackend(profiles["acme-config"])
    argv = override._build_command("p", timeout_seconds=None, effort="high")
    assert argv[-4:] == ["--set", "reasoning.level=3", "--set", "colour=off"]
    assert "'xhigh'" in (override.effort_refusal("xhigh", schema_mode=False) or "")


# ── Every builtin, as surveyed from `--help` and local catalogs (11 Nov 2026) ─


def _builtins() -> dict[str, InstrumentProfile]:
    return InstrumentProfileLoader.load_directory(BUILTINS)


def test_antigravity_declares_its_effort_flag() -> None:
    """agy 1.5.5 --help: --effort (low|medium|high|xhigh|max)."""
    profile = _builtins()["antigravity"]
    assert profile.effort is not None
    assert (profile.effort.delivery, profile.effort.flag) == ("flag", "--effort")
    assert profile.accepted_effort_levels(model=None, schema_mode=False) == list(VOCABULARY)
    assert profile.native_effort("minimal", model=None) == "low"
    argv = PluginCliBackend(profile)._build_command("p", timeout_seconds=None, effort="high")
    assert _efforts(argv) == ["high"]


def test_codex_declares_a_config_override() -> None:
    """codex exec --help: -c key=value; levels from codex's own model catalog."""
    profile = _builtins()["codex-cli"]
    assert profile.effort is not None
    assert profile.effort.delivery == "config_override"
    assert (profile.effort.flag, profile.effort.key) == ("-c", "model_reasoning_effort")
    assert profile.accepted_effort_levels(model="gpt-6-sol", schema_mode=False) == list(VOCABULARY)
    # o3 / o4-mini are not in codex 0.166.0's model catalog: no verified control.
    for model in ("o3", "o4-mini"):
        assert profile.accepted_effort_levels(model=model, schema_mode=False) == []
    plain = PluginCliBackend(profile)._build_command("p", timeout_seconds=None)
    assert "-c" not in plain
    argv = PluginCliBackend(profile)._build_command("p", timeout_seconds=None, effort="low")
    assert argv[-2:] == ["-c", "model_reasoning_effort=low"]


def test_opencode_levels_are_per_model_and_headless_only() -> None:
    """opencode run --help: --variant; `opencode models --verbose` variants."""
    profile = _builtins()["opencode"]
    assert profile.effort is not None
    assert (profile.effort.delivery, profile.effort.flag) == ("flag", "--variant")
    assert profile.effort.levels == {} and profile.effort.interactive is False
    glm = "zai-coding-plan/glm-5.3"
    assert profile.accepted_effort_levels(model=glm, schema_mode=False) == [
        "minimal", "low", "high", "max",
    ]
    assert profile.accepted_effort_levels(model="zai-coding-plan/glm-4.7", schema_mode=False) == []
    assert profile.accepted_effort_levels(model=None, schema_mode=False) == []
    backend = PluginCliBackend(profile)
    backend.apply_overrides({"model": glm})
    assert backend._build_command("p", timeout_seconds=None, effort="max")[-2:] == [
        "--variant", "max",
    ]


def test_crush_levels_are_per_model() -> None:
    """crush run --help: --reasoning-effort; levels from crush's provider catalog."""
    profile = _builtins()["crush"]
    assert profile.effort is not None
    assert (profile.effort.delivery, profile.effort.flag) == ("flag", "--reasoning-effort")
    assert profile.effort.levels == {}
    assert profile.accepted_effort_levels(
        model="google/gemma-4-31b-it:free", schema_mode=False,
    ) == ["minimal", "low", "medium", "high"]
    assert profile.accepted_effort_levels(model="qwen/qwen3-coder:free", schema_mode=False) == []


@pytest.mark.parametrize("name", [
    "claude-code-ollama", "gemini-cli", "aider", "goose", "cline-cli",
    "qwen-code", "openclaw", "cli", "ollama",
])
def test_builtins_without_a_verified_control_declare_none(name: str) -> None:
    assert _builtins()[name].effort is None


def test_every_builtin_is_accounted_for() -> None:
    declared = {n for n, p in _builtins().items() if p.effort is not None}
    assert declared == {"claude-code", "antigravity", "codex-cli", "opencode", "crush"}


# ── Delivery shape: HTTP request-body field ──────────────────────────────

_PLUGIN_HTTP = """\
name: acme-http
display_name: Acme HTTP
kind: http
default_model: acme-reasoner
models:
  - {name: acme-reasoner, context_window: 8000, cost_per_1k_input: 0, cost_per_1k_output: 0}
  - name: acme-small
    context_window: 8000
    cost_per_1k_input: 0
    cost_per_1k_output: 0
    effort_levels: {}
effort:
  delivery: body_field
  key: reasoning.effort
  levels: {minimal: minimal, low: low, medium: medium, high: high}
http:
  base_url: http://localhost:9/v1
  schema_family: openai
"""


def _http_backend(tmp_path: Path) -> Any:
    from marianne.daemon.baton.backend_pool import _create_backend_for_profile

    (tmp_path / "acme-http.yaml").write_text(_PLUGIN_HTTP, encoding="utf-8")
    profile = InstrumentProfileLoader.load_directory(tmp_path)["acme-http"]
    backend = _create_backend_for_profile(profile)
    import httpx

    response = httpx.Response(
        200, json={"model": "acme-reasoner", "choices": [{"message": {"content": "ok"}}]},
        request=httpx.Request("POST", "http://localhost:9/v1/chat/completions"),
    )
    client = AsyncMock()
    client.post.return_value = response
    backend._get_client = AsyncMock(return_value=client)
    return backend, client


@pytest.mark.parametrize("delivery, kind", [("body_field", "cli"), ("flag", "http")])
def test_body_field_is_for_http_and_flags_for_cli(delivery: str, kind: str) -> None:
    mapping = {"delivery": delivery, "levels": {"low": "low"}}
    mapping.update({"key": "reasoning_effort"} if delivery == "body_field" else {"flag": "-e"})
    profile: dict[str, Any] = {"name": "x", "display_name": "x", "kind": kind, "effort": mapping}
    if kind == "http":
        profile["http"] = {"base_url": "http://localhost:9", "schema_family": "openai"}
    else:
        profile["cli"] = {"command": {"executable": "x"}, "output": {"format": "text"}}
    with pytest.raises(ValidationError, match="does not fit kind"):
        InstrumentProfile.model_validate(profile)


@pytest.mark.asyncio
async def test_http_body_field_is_request_local(tmp_path: Path) -> None:
    backend, client = _http_backend(tmp_path)
    await backend.execute("p", request=SheetRequestState(effort="minimal"))
    sent = client.post.call_args.kwargs["json"]
    assert sent["reasoning"] == {"effort": "minimal"}
    # The next sheet on the pooled singleton sends the legacy payload.
    await backend.execute("p", request=SheetRequestState())
    assert set(client.post.call_args.kwargs["json"]) == {
        "model", "messages", "max_tokens", "temperature",
    }


@pytest.mark.asyncio
async def test_http_backend_refuses_before_sending(tmp_path: Path) -> None:
    backend, client = _http_backend(tmp_path)
    assert "'max'" in (backend.effort_refusal("max", schema_mode=False) or "")
    with pytest.raises(ValueError, match="accepted levels: minimal, low, medium, high"):
        await backend.execute("p", request=SheetRequestState(effort="max"))
    with pytest.raises(ValueError, match="model 'acme-small'"):
        await backend.execute(
            "p", request=SheetRequestState(effort="low", model="acme-small"),
        )
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_accepts_effort_on_an_http_profile_with_a_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = _http_backend(tmp_path)
    adapter, sheet, state = _dispatch_setup(tmp_path, {"effort": "high"})
    context = await _dispatch(adapter, sheet, state, backend, monkeypatch)
    assert context.effort == "high"


def test_builtin_ollama_http_profile_still_refuses_effort() -> None:
    from marianne.daemon.baton.backend_pool import _create_backend_for_profile

    backend = _create_backend_for_profile(_builtins()["ollama"])
    assert "accepted levels: none" in (backend.effort_refusal("low", schema_mode=False) or "")


# ── Capability classes: a class entry may carry effort ───────────────────


def _class_map(tmp_path: Path, body: str, profiles: set[str]) -> Any:
    user = tmp_path / "classes.yaml"
    user.write_text("version: 1\nclasses:\n" + body, encoding="utf-8")
    return load_class_map(user_path=user, venue_path=tmp_path / "none.yaml",
                          profile_names=profiles)


_EFFORT_CLASS = (
    "  thinker:\n"
    "    - {profile: claude-code, config: {model: claude-sonnet-5-5, effort: medium}}\n"
    "    - {profile: codex-cli, config: {effort: low}}\n"
    "    - antigravity\n"
)


def test_class_entry_config_takes_a_vocabulary_word() -> None:
    from marianne.core.config.classes import ClassEntry, ClassEntryConfig

    entry = ClassEntry.model_validate({"profile": "x", "config": {"effort": "low"}})
    assert entry.config.effort == "low"
    with pytest.raises(ValidationError):
        ClassEntry.model_validate({"profile": "x", "config": {"effort": "turbo"}})
    assert ClassEntryConfig.model_fields["effort"].description


def test_class_digest_is_unchanged_when_no_entry_sets_effort() -> None:
    """Existing class snapshots (and resumed jobs) keep their digest."""
    from marianne.core.config.classes import ClassEntry

    entry = ClassEntry.model_validate({"profile": "claude-code", "config": {"model": "m"}})
    assert entry.model_dump(mode="json") == {"profile": "claude-code", "config": {"model": "m"}}


def test_class_effort_reaches_the_primary_and_each_tail_entry(tmp_path: Path) -> None:
    names = {"claude-code", "codex-cli", "antigravity"}
    class_map = _class_map(tmp_path, _EFFORT_CLASS, names)
    cfg = _score(tmp_path, instrument="thinker")
    sheet = build_sheets(cfg, classes=class_map.snapshot({"thinker"}))[0]
    assert sheet.instrument_name == "claude-code"
    assert sheet.instrument_config["effort"] == "medium"
    assert sheet.instrument_fallback_configs == [{"effort": "low"}, {}]


def test_score_scope_effort_wins_over_the_class_on_the_primary(tmp_path: Path) -> None:
    names = {"claude-code", "codex-cli", "antigravity"}
    class_map = _class_map(tmp_path, _EFFORT_CLASS, names)
    cfg = _score(tmp_path, instrument="thinker", instrument_config={"effort": "high"})
    sheet = build_sheets(cfg, classes=class_map.snapshot({"thinker"}))[0]
    assert sheet.instrument_config["effort"] == "high"
    # The tail keeps its own entry config; score scope reaches the primary only.
    assert sheet.instrument_fallback_configs == [{"effort": "low"}, {}]


def test_v335_judges_class_entries_and_v325_shows_their_effort(tmp_path: Path) -> None:
    from marianne.validation.checks.capability_classes import ClassChainSummaryCheck

    profiles = {
        "claude-code": _profile("claude-code"),
        "codex-cli": _unmapped_profile("codex-cli"),
        "antigravity": _profile("antigravity"),
    }
    class_map = _class_map(tmp_path, _EFFORT_CLASS, set(profiles))
    ctx = _ClassContext(profiles=profiles, class_map=class_map)
    cfg = _score(tmp_path, instrument="thinker")
    issues = _v335(cfg, ctx)
    assert [i.check_id for i in issues] == ["V335"]
    assert "fallback instrument 'codex-cli'" in issues[0].message
    with patch(
        "marianne.validation.checks.capability_classes._load_context", return_value=ctx,
    ):
        summary = ClassChainSummaryCheck().check(cfg, Path("s.yaml"), "")
    assert "claude-code/claude-sonnet-5-5 (effort medium)" in summary[0].message
    assert "codex-cli (effort low)" in summary[0].message
