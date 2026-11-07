"""Per-attempt CLI first-output overrides reach the consumer without shared state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.musician import sheet_task
from marianne.daemon.baton.state import AttemptContext, AttemptMode
from marianne.execution.base import FIRST_OUTPUT_TIMEOUT_UNSET, Backend, ExecutionResult
from marianne.execution.instruments.cli_backend import PluginCliBackend
from tests.test_post_exit_drain_406 import _profile


def _setup(tmp_path, config):
    sheet = Sheet(
        num=1, movement=1, voice=None, voice_count=1,
        workspace=tmp_path, instrument_name="drain-406",
        prompt_template="work", instrument_config=config, timeout_seconds=10,
    )
    adapter = BatonAdapter()
    adapter.register_job("j502", [sheet], {})
    return adapter, sheet, adapter.baton._jobs["j502"].sheets[1]


async def _dispatch(adapter, sheet, state, backend, monkeypatch):
    captured = []

    async def capture(**kwargs):
        captured.append(kwargs["context"])

    monkeypatch.setattr(adapter, "_musician_wrapper", capture)
    task, _ = adapter._create_musician_task_after_acquire(
        job_id="j502", sheet_num=1, sheet=sheet, state=state, backend=backend,
        effective_instrument=state.instrument_name, technique_manifest=None,
        technique_skill_docs={}, mcp_config_path=None, output_router_active=False,
        mcp_code_bridge_active=False, mcp_bind_mounts=[], event_generation=0,
    )
    await task
    return captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("config, expected", [
    ({}, FIRST_OUTPUT_TIMEOUT_UNSET),
    ({"first_output_timeout_seconds": None}, None),
    ({"first_output_timeout_seconds": 180}, 180.0),
    ({"first_output_timeout_seconds": "2.5"}, 2.5),
])
async def test_attempt_context_carries_first_output_override(
    tmp_path, monkeypatch, config, expected,
):
    adapter, sheet, state = _setup(tmp_path, config)
    context = await _dispatch(adapter, sheet, state, PluginCliBackend(_profile()), monkeypatch)
    assert context.first_output_timeout_seconds == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, "oops", "nan", "inf", True, False, [], {}])
async def test_first_output_override_invalid_value_refused(tmp_path, monkeypatch, value):
    adapter, sheet, state = _setup(tmp_path, {"first_output_timeout_seconds": value})
    with pytest.raises(ValueError, match="first_output_timeout_seconds"):
        await _dispatch(adapter, sheet, state, PluginCliBackend(_profile()), monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_config, expected", [
    ({"first_output_timeout_seconds": None}, None),
    ({"first_output_timeout_seconds": 240}, 240.0),
    ({}, FIRST_OUTPUT_TIMEOUT_UNSET),
])
async def test_first_output_fallback_entry_config_honored(
    tmp_path, monkeypatch, fallback_config, expected,
):
    adapter, sheet, state = _setup(tmp_path, {"first_output_timeout_seconds": 90})
    state.fallback_chain = ["other-cli"]
    state.fallback_configs = [fallback_config]
    assert state.advance_fallback("test") == "other-cli"
    context = await _dispatch(adapter, sheet, state, PluginCliBackend(_profile()), monkeypatch)
    assert context.first_output_timeout_seconds == expected


@pytest.mark.asyncio
async def test_first_output_override_non_cli_warns_and_ignores(tmp_path, monkeypatch, capsys):
    adapter, sheet, state = _setup(tmp_path, {"first_output_timeout_seconds": "unused"})
    context = await _dispatch(adapter, sheet, state, object(), monkeypatch)
    assert context.first_output_timeout_seconds is FIRST_OUTPUT_TIMEOUT_UNSET
    assert "first_output_timeout_unsupported" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [FIRST_OUTPUT_TIMEOUT_UNSET, None, 180.0])
async def test_musician_threads_first_output_into_request_state(tmp_path: Path, value):
    _, sheet, _ = _setup(tmp_path, {})
    backend = AsyncMock(spec=Backend)
    backend.name = "drain-406"
    backend.execute.return_value = ExecutionResult(
        success=True, stdout="ok", stderr="", duration_seconds=0.1, exit_code=0,
    )
    context = AttemptContext(
        attempt_number=1, mode=AttemptMode.NORMAL, first_output_timeout_seconds=value,
    )
    inbox = asyncio.Queue()
    await sheet_task(
        job_id="j502", sheet=sheet, backend=backend, attempt_context=context, inbox=inbox,
    )
    assert inbox.get_nowait().execution_success
    assert backend.execute.call_args.kwargs["request"].first_output_timeout_seconds == value


@pytest.mark.parametrize("value", ["240", "null", None])
@pytest.mark.parametrize(
    "scope", ["score", "movement", "alias"],
)
def test_yaml_first_output_override_survives_scope_merge(scope, value):
    from marianne.core.config import JobConfig
    from marianne.core.sheet import build_sheets

    bodies = {
        "score": f"instrument_config: {{first_output_timeout_seconds: {value}}}\n",
        "movement": "movements:\n  1:\n    instrument_config: "
        f"{{first_output_timeout_seconds: {value}}}\n",
        "alias": "instrument: named\ninstruments:\n  named:\n"
        "    profile: cli\n"
        f"    config: {{first_output_timeout_seconds: {value}}}\n",
    }
    base = (
        "name: first-output-502\nworkspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n"
    )
    # Omission must stay omitted, including through typed defaults/serialization.
    body = bodies[scope] if value is not None else ""
    config = JobConfig.from_yaml_string(base + body)
    assert config.unknown_fields == ()
    for candidate in [config, JobConfig.model_validate(config.model_dump())]:
        settings = build_sheets(candidate)[0].instrument_config
        if value is None:
            assert "first_output_timeout_seconds" not in settings
        else:
            assert settings["first_output_timeout_seconds"] == (None if value == "null" else 240)


@pytest.mark.parametrize("value", ["240", "null"])
def test_yaml_first_output_override_survives_per_sheet_scope(value):
    """The per-sheet scope carries its own sheet block, so the base config
    drops the default one — kept separate from the shared-scope test to
    make that substitution explicit."""
    from marianne.core.config import JobConfig
    from marianne.core.sheet import build_sheets

    config = JobConfig.from_yaml_string(
        "name: first-output-502\nworkspace: ./output\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "  per_sheet_instrument_config:\n    1: "
        f"{{first_output_timeout_seconds: {value}}}\n"
        "prompt: {template: hi}\n"
    )
    assert config.unknown_fields == ()
    for candidate in [config, JobConfig.model_validate(config.model_dump())]:
        settings = build_sheets(candidate)[0].instrument_config
        assert settings["first_output_timeout_seconds"] == (None if value == "null" else 240)


@pytest.mark.parametrize("value", ["0", "-1", "oops", ".nan", ".inf", "true", "false"])
def test_yaml_invalid_first_output_override_refused(value):
    from marianne.core.config import JobConfig

    with pytest.raises(ValueError, match="first_output_timeout_seconds"):
        JobConfig.from_yaml_string(
            "name: invalid-502\nworkspace: ./output\n"
            "sheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n"
            f"instrument_config: {{first_output_timeout_seconds: {value}}}\n"
        )
