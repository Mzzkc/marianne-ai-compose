"""GH #534: ``instrument_config.tools`` — a per-sheet harness toolset.

A profile's top-level ``tools:`` block names the flag that limits which tool
definitions the harness gives the model. A sheet that sets nothing builds the
argv it always built; a sheet that sets the key delivers it request-locally in
the headless and the interactive builders, and a profile that cannot apply it
refuses in one shared sentence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from marianne.core.config import JobConfig
from marianne.core.config.instrument_settings import InstrumentSettings
from marianne.core.config.instruments import InstrumentProfile, ToolsMapping
from marianne.core.sheet import build_sheets
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.musician import sheet_task
from marianne.daemon.baton.state import AttemptContext, AttemptMode
from marianne.execution.base import Backend, ExecutionResult, SheetRequestState
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from tests.test_interactive_mode import READY_SCREEN, FakeTmux, make_interactive_config
from tests.test_sheet_effort_535 import (
    _command,
    _dispatch,
    _dispatch_setup,
    _profile,
    _spawned,
    _unsupervised_profile,
)

TOOLS: dict[str, Any] = {
    "delivery": "flag",
    "flag": "--tools",
    "join": "comma",
    "known": ["Bash", "Read", "Edit", "Write", "Grep", "Glob"],
}


def _tprofile(tools: Any = TOOLS, **kw: Any) -> InstrumentProfile:
    base = _profile(**kw)
    return InstrumentProfile.model_validate({**base.model_dump(), "tools": tools})


def _build(profile: InstrumentProfile, **kw: Any) -> list[str]:
    return PluginCliBackend(profile)._build_command("p", timeout_seconds=10.0, **kw)


def _flag_value(argv: list[str], flag: str = "--tools") -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


# ── Schema ───────────────────────────────────────────────────────────────


def test_settings_key_is_described_and_validated() -> None:
    assert InstrumentSettings.model_fields["tools"].description
    assert InstrumentSettings(tools=["Read"]).tools == ["Read"]
    assert InstrumentSettings(tools=[]).tools == []
    for bad in (["Bash", "Bash"], [""], ["a,b"], "Read", [1]):
        with pytest.raises(ValidationError):
            InstrumentSettings(tools=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", list(ToolsMapping.model_fields))
def test_mapping_fields_are_described(name: str) -> None:
    assert ToolsMapping.model_fields[name].description


def test_profile_tools_field_is_described() -> None:
    assert InstrumentProfile.model_fields["tools"].description


@pytest.mark.parametrize("data", [
    {"delivery": "flag"},
    {"delivery": "body_field", "flag": "--tools"},
    {"delivery": "flag", "flag": "--tools", "known": []},
    {"delivery": "flag", "flag": "--tools", "known": ["A", "A"]},
    {"delivery": "flag", "flag": "--tools", "bogus": 1},
])
def test_inconsistent_mapping_is_refused(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ToolsMapping.model_validate(data)


def test_old_allowed_tools_flag_still_loads_and_is_ignored() -> None:
    from marianne.core.config.instruments import CliCommand

    cmd = CliCommand(executable="x", allowed_tools_flag="--allowedTools")
    assert cmd.allowed_tools_flag == "--allowedTools"
    assert "DEPRECATED" in (CliCommand.model_fields["allowed_tools_flag"].description or "")


def test_tools_block_needs_a_cli_profile() -> None:
    with pytest.raises(ValidationError, match="kind 'cli'"):
        InstrumentProfile.model_validate({
            "name": "h", "display_name": "h", "kind": "http", "default_model": "m",
            "tools": TOOLS,
            "http": {"base_url": "http://x", "schema_family": "openai"},
        })


@pytest.mark.parametrize("scope", ["score", "movement", "sheet"])
def test_tools_survive_yaml_load_at_each_scope(tmp_path: Path, scope: str) -> None:
    body = {
        "score": "instrument_config:\n  tools: [Read, Grep]\n",
        "movement": "movements:\n  1:\n    instrument_config:\n      tools: [Read, Grep]\n",
        "sheet": "sheet:\n  per_sheet_instrument_config:\n    1:\n      tools: [Read, Grep]\n",
    }[scope]
    yaml_text = (
        "name: t534\nworkspace: ws\ninstrument: claude-code\n"
        "sheet:\n  size: 1\n  total_items: 2\n" if scope != "sheet" else
        "name: t534\nworkspace: ws\ninstrument: claude-code\n"
    )
    if scope == "sheet":
        yaml_text += (
            "sheet:\n  size: 1\n  total_items: 2\n  per_sheet_instrument_config:\n"
            "    1:\n      tools: [Read, Grep]\n"
        )
    else:
        yaml_text += body
    yaml_text += "prompt:\n  template: hi\n"
    path = tmp_path / "s.yaml"
    path.write_text(yaml_text)
    cfg = JobConfig.from_yaml(path)
    sheets = build_sheets(cfg)
    assert sheets[0].instrument_config["tools"] == ["Read", "Grep"]


def test_class_entry_carries_tools_into_the_sheet_config() -> None:
    from marianne.core.config.classes import ClassEntryConfig
    from marianne.core.sheet import _class_entry_config

    class _E:
        config = ClassEntryConfig(tools=["Read"])

    assert _class_entry_config(_E()) == {"tools": ["Read"]}  # type: ignore[arg-type]
    assert ClassEntryConfig().model_dump() == {"model": None}


# ── Headless builder ─────────────────────────────────────────────────────


def test_unset_key_builds_exactly_todays_argv() -> None:
    plain = _profile()
    with_block = _tprofile()
    before = PluginCliBackend(plain)._build_command("p", timeout_seconds=10.0)
    assert PluginCliBackend(with_block)._build_command("p", timeout_seconds=10.0) == before
    assert _build(with_block, tools=None) == before
    assert "--tools" not in before


def test_tools_are_comma_joined_and_appended() -> None:
    argv = _build(_tprofile(), tools=("Bash", "Read"))
    assert _flag_value(argv) == ["Bash,Read"]


def test_empty_list_delivers_one_empty_argument() -> None:
    argv = _build(_tprofile(), tools=())
    assert argv[argv.index("--tools") + 1] == ""


def test_space_join() -> None:
    profile = _tprofile({**TOOLS, "join": "space", "known": None})
    argv = _build(profile, tools=("a", "b"))
    assert _flag_value(argv) == ["a b"]


def test_default_in_extra_flags_is_replaced_never_doubled() -> None:
    profile = _tprofile(command=_command(extra_flags=["--tools", "default", "--keep"]))
    backend = PluginCliBackend(profile)
    assert _flag_value(backend._build_command("p", timeout_seconds=10.0)) == ["default"]
    argv = backend._build_command("p", timeout_seconds=10.0, tools=("Read",))
    assert _flag_value(argv) == ["Read"] and "--keep" in argv


def test_tools_apply_on_a_schema_sheet_too() -> None:
    rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
    argv = PluginCliBackend(_tprofile())._build_command(
        "p", timeout_seconds=10.0, response_format=rf, tools=("Read",),
    )
    assert _flag_value(argv) == ["Read"]


def test_unmapped_profile_refuses_in_the_shared_words() -> None:
    backend = PluginCliBackend(_profile())
    with pytest.raises(ValueError, match="declares no tools mapping") as exc:
        backend._build_command("p", timeout_seconds=10.0, tools=("Read",))
    assert "known tools: none declared" in str(exc.value)
    assert backend.tools_refusal(["Read"]) in str(exc.value)


def test_unknown_name_is_refused_naming_the_known_list() -> None:
    with pytest.raises(ValueError, match="does not know tool") as exc:
        _build(_tprofile(), tools=("Raed",))
    assert "known tools: Bash, Read, Edit, Write, Grep, Glob" in str(exc.value)


def test_without_known_any_name_passes_through() -> None:
    profile = _tprofile({**TOOLS, "known": None})
    argv = _build(profile, tools=("Whatever",))
    assert _flag_value(argv) == ["Whatever"]


@pytest.mark.asyncio
async def test_execute_carries_request_tools_and_nothing_sticks() -> None:
    profile = _tprofile(command=_command(supervise=False))
    backend = PluginCliBackend(profile)
    assert _flag_value(await _spawned(backend, SheetRequestState(tools=("Read",)))) == ["Read"]
    assert _flag_value(await _spawned(backend, SheetRequestState())) == []
    assert _flag_value(await _spawned(backend, None)) == []
    assert _unsupervised_profile() is not None


# ── Interactive builder ──────────────────────────────────────────────────


def _interactive(tmp_path: Path, tools: Any = TOOLS, tmux: FakeTmux | None = None,
                 extra_args: list[str] | None = None) -> InteractiveCliBackend:
    profile = _tprofile(
        tools, interactive=make_interactive_config(extra_args=extra_args or []),
    )
    return InteractiveCliBackend(
        profile, working_directory=tmp_path, tmux=tmux or FakeTmux(),  # type: ignore[arg-type]
    )


def test_interactive_unset_is_unchanged(tmp_path: Path) -> None:
    backend = _interactive(tmp_path)
    assert backend._build_command() == ["claude", "--model", "m-1"]
    assert backend._build_command(tools=None) == ["claude", "--model", "m-1"]


def test_interactive_carries_tools_and_replaces_a_default(tmp_path: Path) -> None:
    assert _interactive(tmp_path)._build_command(tools=("Read", "Edit")) == [
        "claude", "--model", "m-1", "--tools", "Read,Edit",
    ]
    argv = _interactive(tmp_path, extra_args=["--tools", "default", "--keep"])._build_command(
        tools=("Read",),
    )
    assert _flag_value(argv) == ["Read"] and "--keep" in argv


def test_interactive_refuses_unmapped_unknown_and_headless_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not know tool"):
        _interactive(tmp_path)._build_command(tools=("Nope",))
    with pytest.raises(ValueError, match="declares no tools mapping"):
        _interactive(tmp_path, tools=None)._build_command(tools=("Read",))
    backend = _interactive(tmp_path, tools={**TOOLS, "interactive": False})
    assert backend._build_command() == ["claude", "--model", "m-1"]
    with pytest.raises(ValueError, match="tools.interactive is false"):
        backend._build_command(tools=("Read",))


@pytest.mark.asyncio
async def test_interactive_execute_launches_with_the_request_tools(tmp_path: Path) -> None:
    def make_done() -> str:
        for marker in (tmp_path / ".marianne" / "interactive").glob("*.complete"):
            marker.touch()
        return READY_SCREEN

    fake = FakeTmux([READY_SCREEN, make_done])
    backend = _interactive(tmp_path, tmux=fake)
    backend.configure_interactive(max_nudges=1)
    await backend.execute(
        "do it", timeout_seconds=5.0, request=SheetRequestState(tools=("Read",)),
    )
    assert fake.created
    assert "--tools" in str(fake.created[0]) or "Read" in str(fake.created[0])


# ── Dispatch and musician ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("config, expected", [
    ({}, None),
    ({"tools": None}, None),
    ({"tools": []}, ()),
    ({"tools": ["Read", "Grep"]}, ("Read", "Grep")),
])
async def test_attempt_context_carries_the_sheet_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any], expected: tuple[str, ...] | None,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, config)
    context = await _dispatch(
        adapter, sheet, state, PluginCliBackend(_tprofile()), monkeypatch,
    )
    assert context.tools == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("config, backend_profile, needle", [
    ({"tools": ["Nope"]}, TOOLS, "known tools: Bash"),
    ({"tools": ["Read"]}, None, "declares no tools mapping"),
    ({"tools": "Read"}, TOOLS, "list of tool names"),
])
async def test_dispatch_refuses_before_any_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any], backend_profile: Any, needle: str,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, config)
    with pytest.raises(ValueError, match=needle):
        await _dispatch(
            adapter, sheet, state, PluginCliBackend(_tprofile(backend_profile)), monkeypatch,
        )


@pytest.mark.asyncio
async def test_dispatch_refuses_tools_on_a_backend_that_cannot_apply_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, {"tools": ["Read"]})
    with pytest.raises(ValueError, match="instrument_config.tools"):
        await _dispatch(adapter, sheet, state, object(), monkeypatch)


@pytest.mark.asyncio
async def test_fallback_entry_uses_its_own_tools_not_the_primarys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, sheet, state = _dispatch_setup(tmp_path, {"tools": ["Read"]})
    state.fallback_chain = ["other-cli", "third-cli"]
    state.fallback_configs = [{}, {"tools": ["Grep"]}]
    assert state.advance_fallback("test") == "other-cli"
    backend = PluginCliBackend(_tprofile())
    assert (await _dispatch(adapter, sheet, state, backend, monkeypatch)).tools is None
    assert state.advance_fallback("test") == "third-cli"
    assert (await _dispatch(adapter, sheet, state, backend, monkeypatch)).tools == ("Grep",)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, (), ("Read",)])
async def test_musician_threads_tools_into_the_request(
    tmp_path: Path, value: tuple[str, ...] | None,
) -> None:
    import asyncio

    _, sheet, _ = _dispatch_setup(tmp_path, {})
    backend = AsyncMock_backend()
    context = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL, tools=value)
    inbox: asyncio.Queue[Any] = asyncio.Queue()
    await sheet_task(
        job_id="j535", sheet=sheet, backend=backend, attempt_context=context, inbox=inbox,
    )
    assert inbox.get_nowait().execution_success
    assert backend.execute.call_args.kwargs["request"].tools == value


def AsyncMock_backend() -> Any:
    from unittest.mock import AsyncMock

    backend = AsyncMock(spec=Backend)
    backend.name = "claude-code"
    backend.execute.return_value = ExecutionResult(
        success=True, stdout="ok", stderr="", duration_seconds=0.1, exit_code=0,
    )
    return backend


def test_baton_adapter_is_importable() -> None:
    assert BatonAdapter


# ── Validator: V337 ──────────────────────────────────────────────────────


def _ctx(tmp_path: Path, **profiles: InstrumentProfile) -> Any:
    from marianne.instruments.classes import load_class_map
    from marianne.validation.checks.capability_classes import _ClassContext

    class_map = load_class_map(
        user_path=tmp_path / "no-user.yaml", venue_path=tmp_path / "no-venue.yaml",
        profile_names=set(profiles),
    )
    return _ClassContext(profiles=profiles, class_map=class_map)


def _score(tmp_path: Path, **fields: Any) -> JobConfig:
    base: dict[str, Any] = {
        "name": "tools", "workspace": str(tmp_path / "ws"), "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 2}, "prompt": {"template": "x"},
    }
    base.update(fields)
    return JobConfig.model_validate(base)


def _v336(cfg: JobConfig, ctx: Any) -> list[Any]:
    from unittest.mock import patch

    from marianne.validation.checks.instrument_tools import SheetToolsCheck

    with patch(
        "marianne.validation.checks.instrument_tools._load_context", return_value=ctx,
    ):
        return SheetToolsCheck().check(cfg, Path("s.yaml"), "instrument_config:\n  tools: x\n")


def test_v336_is_registered_as_an_error() -> None:
    from marianne.validation.base import ValidationSeverity
    from marianne.validation.output_contract import _known_suppression_severities

    assert _known_suppression_severities()["V337"] == ValidationSeverity.ERROR


def test_v336_silent_without_the_key_and_on_known_names(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _tprofile()})
    assert _v336(_score(tmp_path), ctx) == []
    assert _v336(_score(tmp_path, instrument_config={"tools": ["Read"]}), ctx) == []
    assert _v336(_score(tmp_path, instrument_config={"tools": []}), ctx) == []


def test_v336_refuses_a_profile_with_no_mapping(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _profile()})
    issues = _v336(_score(tmp_path, instrument_config={"tools": ["Read"]}), ctx)
    assert len(issues) == 1 and issues[0].check_id == "V337"
    assert "declares no tools mapping" in issues[0].message


def test_v336_refuses_an_unknown_name_naming_the_list(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _tprofile()})
    issues = _v336(_score(tmp_path, instrument_config={"tools": ["Raed"]}), ctx)
    assert len(issues) == 1 and "known tools: Bash, Read" in issues[0].message


def test_validator_and_runtime_refuse_in_the_same_words(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _tprofile()})
    message = _v336(_score(tmp_path, instrument_config={"tools": ["Raed"]}), ctx)[0].message
    with pytest.raises(ValueError) as excinfo:
        _build(_tprofile(), tools=("Raed",))
    reason = _tprofile().tools_refusal(["Raed"])
    assert reason is not None and reason in message and reason in str(excinfo.value)


def test_v336_judges_the_per_sheet_scope(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _tprofile()})
    cfg = _score(
        tmp_path,
        sheet={"size": 1, "total_items": 2,
               "per_sheet_instrument_config": {2: {"tools": ["Nope"]}}},
    )
    issues = _v336(cfg, ctx)
    assert len(issues) == 1 and issues[0].metadata["sheets"] == "2"


# ── Builtins ─────────────────────────────────────────────────────────────

BUILTINS = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"
DECLARED = {"claude-code", "claude-code-ollama"}


def _builtin(name: str) -> InstrumentProfile:
    import yaml

    return InstrumentProfile.model_validate(yaml.safe_load((BUILTINS / f"{name}.yaml").read_text()))


@pytest.mark.parametrize("name", sorted(DECLARED))
def test_claude_code_builtins_declare_the_removing_flag(name: str) -> None:
    profile = _builtin(name)
    assert profile.tools is not None
    assert profile.tools.flag == "--tools" and profile.tools.join == "comma"
    flags = profile.cli.command.extra_flags if profile.cli else []
    assert "--allowedTools" not in flags and profile.cli.command.allowed_tools_flag is None
    argv = PluginCliBackend(profile)._build_command(
        "p", timeout_seconds=10.0, tools=("Bash", "Read", "Edit", "Write"),
    )
    assert _flag_value(argv) == ["Bash,Read,Edit,Write"]


def test_every_other_builtin_declares_none() -> None:
    names = {p.stem for p in BUILTINS.glob("*.yaml")}
    for name in sorted(names - DECLARED):
        assert _builtin(name).tools is None, name


def test_claude_code_without_the_key_builds_no_tools_flag() -> None:
    argv = PluginCliBackend(_builtin("claude-code"))._build_command("p", timeout_seconds=10.0)
    assert "--tools" not in argv
