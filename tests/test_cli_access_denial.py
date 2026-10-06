"""Inject kernel denial at connector; no actual IPC or lifecycle operations."""
import errno
import json

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.daemon import detect
from marianne.daemon.ipc.client import DaemonClient


def denied_connector(monkeypatch, tmp_path, code=errno.EACCES):
    path = tmp_path / "inert-endpoint-placeholder"
    path.touch()
    calls = []

    async def denied(*args, **kwargs):
        calls.append((args, kwargs))
        raise PermissionError(code, "Injected public access denial")

    monkeypatch.setattr("asyncio.open_unix_connection", denied)
    monkeypatch.setattr(detect, "_resolve_socket_path", lambda _: path)
    return path, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("pooled", [True, False])
@pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM])
async def test_connector_denial_is_not_absence(monkeypatch, tmp_path, pooled, code):
    # Catching OSError as not-running destroys the meaningful denied state.
    path, calls = denied_connector(monkeypatch, tmp_path, code)
    client = DaemonClient(path)
    with pytest.raises(Exception) as caught:
        if pooled:
            await client.call("job.list")
        else:
            async with client._connect():
                raise AssertionError("denied connector unexpectedly yielded")
    assert type(caught.value).__name__ == "DaemonAccessDeniedError"
    assert len(calls) == 1
    if pooled:
        assert client._pool._semaphore._value == client._pool._max_size
    await client.close()


@pytest.mark.parametrize("json_output", [True, False])
def test_run_denial_does_not_fall_back_or_suggest_restart(monkeypatch, tmp_path, json_output):
    import asyncio

    import typer

    from marianne.cli.commands.run import _try_daemon_submit

    _, calls = denied_connector(monkeypatch, tmp_path)
    with pytest.raises(typer.Exit) as caught:
        asyncio.run(_try_daemon_submit(
            tmp_path / "unused.yaml", None, False, False, False, json_output,
        ))
    assert caught.value.exit_code == 1
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["pooled", "unpooled", "health"])
async def test_socket_stat_denial_is_typed_and_releases_slot(monkeypatch, tmp_path, consumer):
    from pathlib import Path

    path, calls = denied_connector(monkeypatch, tmp_path)
    original = Path.exists

    def denied_exists(self):
        if self == path:
            raise PermissionError(errno.EACCES, "Injected stat denial")
        return original(self)

    monkeypatch.setattr(Path, "exists", denied_exists)
    client = DaemonClient(path)
    with pytest.raises(Exception) as caught:
        if consumer == "pooled":
            await client.call("job.list")
        elif consumer == "health":
            await client.is_daemon_running()
        else:
            async with client._connect():
                raise AssertionError("unexpected connection")
    assert type(caught.value).__name__ == "DaemonAccessDeniedError"
    assert not calls
    if consumer == "pooled":
        assert client._pool._semaphore._value == client._pool._max_size
    await client.close()
@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["health", "available", "route"])
async def test_denial_survives_health_and_detection(monkeypatch, tmp_path, consumer):
    path, calls = denied_connector(monkeypatch, tmp_path)
    client = DaemonClient(path)
    with pytest.raises(Exception) as caught:
        if consumer == "health":
            await client.is_daemon_running()
        elif consumer == "available":
            await detect.is_daemon_available(path)
        else:
            await detect.try_daemon_route("job.list", {}, socket_path=path)
    assert type(caught.value).__name__ == "DaemonAccessDeniedError"
    assert len(calls) == 1
    await client.close()


@pytest.mark.parametrize("command", ["list", "status", "run"])
@pytest.mark.parametrize("json_output", [True, False])
def test_normal_cli_denial_never_says_start_or_absent(monkeypatch, tmp_path, command, json_output):
    # Shared denial diagnostic must survive overview's broad catch as well.
    _, calls = denied_connector(monkeypatch, tmp_path)
    args = [command]
    if command == "run":
        config = tmp_path / "public-score.yaml"
        config.write_text(
            "name: public-denial-test\nsheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: Test only\n"
        )
        args.append(str(config))
    result = CliRunner().invoke(app, [*args, *(["--json"] if json_output else [])])
    assert result.exit_code == 1
    assert "access denied" in result.output.lower(), result.output
    assert "unknown" in result.output.lower()
    assert "not running" not in result.output.lower()
    assert "mzt start" not in result.output and "mzt restart" not in result.output
    if json_output:
        output = json.loads(result.output)
        assert output["running_state"] == "unknown"
        assert output["error_type"] == "DaemonAccessDeniedError"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pooled", [True, False])
@pytest.mark.parametrize("failure", [FileNotFoundError(), ConnectionRefusedError(), TimeoutError()])
async def test_connectivity_neighbors_retain_not_running(monkeypatch, tmp_path, pooled, failure):
    from marianne.daemon.exceptions import DaemonNotRunningError
    path, _ = denied_connector(monkeypatch, tmp_path)

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("asyncio.open_unix_connection", fail)
    client = DaemonClient(path)
    with pytest.raises(DaemonNotRunningError):
        if pooled:
            await client.call("job.list")
        else:
            async with client._connect():
                raise AssertionError("unexpected connection")
    if pooled:
        assert client._pool._semaphore._value == client._pool._max_size
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pooled", [True, False])
async def test_connector_cancellation_is_not_denial_or_absence(monkeypatch, tmp_path, pooled):
    import asyncio
    path, _ = denied_connector(monkeypatch, tmp_path)

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("asyncio.open_unix_connection", cancel)
    client = DaemonClient(path)
    with pytest.raises(asyncio.CancelledError):
        if pooled:
            await client.call("job.list")
        else:
            async with client._connect():
                raise AssertionError("unexpected connection")
    # Existing pooled pending-connector slot leak is outside this repair.
    await client.close()
