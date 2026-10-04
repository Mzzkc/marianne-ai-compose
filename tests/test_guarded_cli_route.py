"""A reviewed CLI route is bound before any subprocess is spawned.

This is configured-command evidence, not server-side model attestation.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marianne.execution.base import SheetRequestState
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.instruments.loader import capture_resolved_instrument_route, load_all_profiles


@pytest.fixture
def guarded_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    venue = tmp_path / ".marianne" / "instruments"
    venue.mkdir(parents=True)
    file = venue / "fiction.yaml"
    file.write_text(
        "name: fiction-cli\ndisplay_name: Fiction CLI\nkind: cli\n"
        "default_model: fiction-model\ncli:\n  command:\n"
        "    executable: echo\n    model_flag: --model\n"
        "  output:\n    format: text\n"
    )
    profile = load_all_profiles()["fiction-cli"]
    binding = capture_resolved_instrument_route(
        "fiction-cli", {"provider": "Fiction Provider"},
        now=datetime.now(UTC), arm="remote",
    )
    return PluginCliBackend(profile), binding, file


@pytest.mark.parametrize("change", ["provider", "model", "arm", "profile", "stale"])
async def test_guarded_cli_rival_refuses_before_spawn(guarded_cli, change):
    backend, binding, file = guarded_cli
    if change == "profile":
        file.write_text(file.read_text() + "# changed profile bytes\n")
    elif change == "stale":
        binding = binding.model_copy(update={
            "resolved_at": datetime.now(UTC) - timedelta(seconds=61)
        })
    else:
        field, value = {
            "provider": ("effective_provider", "Rival Provider"),
            "model": ("effective_model", "rival-model"),
            "arm": ("arm", "local"),
        }[change]
        binding = binding.model_copy(update={field: value})
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
        result = await backend.execute(
            "PUBLIC FICTION", request=SheetRequestState(
                expected_route=binding, route_provider_override="Fiction Provider"
            ),
        )
    assert not result.success and result.error_type == "attempt_route_drift"
    spawn.assert_not_awaited()


@pytest.mark.parametrize("change", ["profile", "stale"])
async def test_guarded_cli_rechecks_after_awaited_mcp_lock_and_restores_config(
    guarded_cli, monkeypatch, tmp_path, change,
):
    import marianne.execution.instruments.cli_backend as module

    backend, binding, file = guarded_cli
    now = datetime.now(UTC)
    binding = binding.model_copy(update={"resolved_at": now})
    clock = {"now": now}
    monkeypatch.setattr(module, "utc_now", lambda: clock["now"])
    source = tmp_path / "generated.json"
    source.write_text("{}")
    target = tmp_path / "workspace.json"
    target.write_text("original workspace bytes")
    backend.set_mcp_config(source)
    monkeypatch.setattr(backend, "_workspace_mcp_config_target", lambda: target)

    class LateLock:
        released = False

        async def acquire(self):
            await asyncio.sleep(0)
            if change == "profile":
                file.write_text(file.read_text() + "# changed while waiting for MCP\n")
            else:
                clock["now"] = now + timedelta(seconds=61)
            return True

        def release(self):
            self.released = True

    lock = LateLock()
    monkeypatch.setitem(module._MCP_WORKSPACE_CONFIG_LOCKS, target.resolve(), lock)
    with patch("asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec) as spawn:
        result = await backend.execute(
            "PUBLIC FICTION", request=SheetRequestState(
                expected_route=binding, route_provider_override="Fiction Provider"
            ),
        )
    assert not result.success and result.error_type == "attempt_route_drift"
    spawn.assert_not_awaited()
    assert target.read_text() == "original workspace bytes"
    assert lock.released


async def test_guarded_cli_matching_profile_executes_without_claiming_model_echo(guarded_cli):
    backend, binding, _ = guarded_cli
    result = await backend.execute(
        "PUBLIC FICTION", request=SheetRequestState(
            expected_route=binding, route_provider_override="Fiction Provider"
        ),
    )
    assert result.success
    assert result.model_requested == "fiction-model"
    assert result.model_echo_status is None and result.model_observed is None


@pytest.mark.parametrize("change", ["profile", "stale"])
async def test_guarded_cli_rechecks_after_command_preparation_before_spawn(
    guarded_cli, monkeypatch, change,
):
    """Command/MCP preparation can outlive or change an earlier route check."""
    import marianne.execution.instruments.cli_backend as module

    backend, binding, file = guarded_cli
    now = datetime.now(UTC)
    binding = binding.model_copy(update={"resolved_at": now})
    clock = {"now": now}
    monkeypatch.setattr(module, "utc_now", lambda: clock["now"])
    build_command = backend._build_command

    def prepare_then_change(*args, **kwargs):
        command = build_command(*args, **kwargs)
        if change == "profile":
            file.write_text(file.read_text() + "# changed during command preparation\n")
        else:
            clock["now"] = now + timedelta(seconds=61)
        return command

    monkeypatch.setattr(backend, "_build_command", prepare_then_change)
    with patch("asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec) as spawn:
        result = await backend.execute(
            "PUBLIC FICTION", request=SheetRequestState(
                expected_route=binding, route_provider_override="Fiction Provider"
            ),
        )
    assert not result.success and result.error_type == "attempt_route_drift"
    spawn.assert_not_awaited()
