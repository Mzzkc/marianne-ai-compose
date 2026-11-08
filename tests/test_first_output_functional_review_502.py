"""Functional boundary checks for per-sheet first-output overrides (#502)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from marianne.core.config import JobConfig
from marianne.core.sheet import build_sheets
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.musician import sheet_task
from marianne.execution.base import SheetRequestState
from marianne.execution.instruments import cli_backend
from marianne.execution.instruments.cli_backend import PluginCliBackend
from tests.test_first_output_override_502 import _dispatch
from tests.test_post_exit_drain_406 import _profile


@pytest.mark.parametrize("yaml_bound, expected", [("null", None), ("2.5", 2.5)])
async def test_yaml_override_reaches_real_cli_and_next_request_inherits(
    tmp_path, monkeypatch, yaml_bound, expected,
):
    """Cross loader, dispatch, musician and subprocess; then reuse the same backend."""
    config = JobConfig.from_yaml_string(
        "name: functional-502\nworkspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\n"
        "prompt: {template: \"print('completed')\"}\n"
        f"instrument_config: {{first_output_timeout_seconds: {yaml_bound}}}\n"
    )
    sheet = build_sheets(config)[0]
    sheet.workspace = tmp_path
    sheet.timeout_seconds = 10
    adapter = BatonAdapter()
    adapter.register_job("j502", [sheet], {})
    state = adapter.baton._jobs["j502"].sheets[1]
    backend = PluginCliBackend(_profile(first_output=5.0))
    bounds = []
    original_wait = backend._wait_bounded_by_first_output

    async def observe_wait(proc, descendants, born, ticks, timeout, bound, out, err):
        bounds.append(bound)
        return await original_wait(proc, descendants, born, ticks, timeout, bound, out, err)

    monkeypatch.setattr(backend, "_wait_bounded_by_first_output", observe_wait)
    context = await _dispatch(adapter, sheet, state, backend, monkeypatch)
    inbox = asyncio.Queue()
    await sheet_task(
        job_id="j502", sheet=sheet, backend=backend,
        attempt_context=context, inbox=inbox,
        rendered_prompt="print('completed')",
    )
    assert inbox.get_nowait().execution_success
    inherited = await backend.execute(
        "print('next sheet')", timeout_seconds=10, request=SheetRequestState(),
    )
    legacy = await backend.execute("print('legacy caller')", timeout_seconds=10)
    assert inherited.success and inherited.stdout == "next sheet\n"
    assert legacy.success and legacy.stdout == "legacy caller\n"
    assert bounds == [expected, 5.0, 5.0]
    assert backend._cli.first_output_timeout_seconds == 5.0


async def test_stderr_first_byte_disarms_first_output_deadline(monkeypatch):
    """A byte on stderr must grant the remaining sheet budget, even without stdout."""
    parent_wait = AsyncMock(return_value=0)
    monkeypatch.setattr(cli_backend, "_wait_for_parent_exit", parent_wait)
    original_wait_for = asyncio.wait_for
    timeouts = []

    async def observe_timeout(awaitable, timeout):
        timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(cli_backend.asyncio, "wait_for", observe_timeout)
    backend = PluginCliBackend(_profile(first_output=0.001))
    result = await backend._wait_bounded_by_first_output(
        object(), {}, None, None, 10.0, 0.001, [], [b"startup on stderr"],
    )
    assert result == 0
    parent_wait.assert_awaited_once()
    assert len(timeouts) == 1 and timeouts[0] > 5.0


@pytest.mark.parametrize("sheet_bound, expected", [("null", None), ("45", 45.0)])
def test_scope_precedence_and_explicit_null_survive_json_roundtrip(sheet_bound, expected):
    """All four scopes compete; explicit null must win over three numeric defaults."""
    config = JobConfig.from_yaml_string(
        "name: precedence-502\nworkspace: ./output\ninstrument: named\n"
        "prompt: {template: hi}\n"
        "instrument_config: {first_output_timeout_seconds: 240}\n"
        "movements:\n  1:\n    instrument_config: {first_output_timeout_seconds: 180}\n"
        "instruments:\n  named:\n    profile: cli\n"
        "    config: {first_output_timeout_seconds: 120}\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "  per_sheet_instrument_config:\n"
        f"    1: {{first_output_timeout_seconds: {sheet_bound}}}\n"
    )
    assert config.unknown_fields == ()
    for candidate in (config, JobConfig.model_validate_json(config.model_dump_json())):
        settings = build_sheets(candidate)[0].instrument_config
        assert settings["first_output_timeout_seconds"] == expected


async def test_nonzero_child_exit_preserves_error_without_stale_marker():
    backend = PluginCliBackend(_profile(first_output=5.0))
    result = await backend.execute(
        "import sys; sys.stderr.write('startup failed\\n'); sys.exit(7)",
        timeout_seconds=10, request=SheetRequestState(first_output_timeout_seconds=2.0),
    )
    assert not result.success
    assert result.exit_code == 7
    assert "startup failed" in result.stderr
    assert "Stale execution" not in result.stderr
