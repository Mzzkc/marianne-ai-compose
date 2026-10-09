"""Exercise local IPC denial without contacting the installed conductor."""
import asyncio
import errno
import json
import os
import socket

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.daemon import detect
from marianne.daemon.ipc.client import DaemonClient


@pytest.mark.parametrize("json_output", [True, False])
def test_status_health_then_denied_list_keeps_known_health(monkeypatch, json_output):
    from marianne.daemon.exceptions import DaemonAccessDeniedError

    calls = []

    async def route(method, params):
        calls.append(method)
        if method == "daemon.health":
            return True, {"status": "healthy"}
        raise DaemonAccessDeniedError()

    monkeypatch.setattr(detect, "try_daemon_route", route)
    result = CliRunner().invoke(app, ["status", *(["--json"] if json_output else [])])
    assert result.exit_code == 1
    assert calls == ["daemon.health", "job.list"]
    assert "mzt restart" not in result.output
    assert "mzt start" not in result.output
    if json_output:
        payload = json.loads(result.output)
        assert payload["conductor"] == "running"
        assert payload["jobs_known"] is False
        assert payload["active_count"] is None
        assert payload["recent_count"] is None
        assert payload["error_type"] == "DaemonAccessDeniedError"
    else:
        assert "conductor responded to health" in result.output.lower()
        assert "job status is unknown" in result.output.lower()
        assert "access denied" in result.output.lower()
        assert "running state is unknown" not in result.output.lower()


@pytest.mark.parametrize("command", ["list", "status", "run"])
@pytest.mark.parametrize("json_output", [True, False])
@pytest.mark.parametrize("failure", ["unresponsive", "protocol", "absent"])
def test_normal_cli_distinguishes_connection_outcomes(
    monkeypatch, tmp_path, command, json_output, failure,
):
    from marianne.daemon.exceptions import DaemonProtocolError, DaemonUnresponsiveError

    async def route(*args, **kwargs):
        if failure == "unresponsive":
            raise DaemonUnresponsiveError("Conductor did not respond")
        if failure == "protocol":
            raise DaemonProtocolError("Invalid conductor response")
        return False, None

    monkeypatch.setattr(detect, "try_daemon_route", route)
    args = [command]
    if command == "run":
        config = tmp_path / "public-score.yaml"
        config.write_text(
            "name: public-outcome-test\nsheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: Test only\n"
        )
        args.append(str(config))
    result = CliRunner().invoke(app, [*args, *(["--json"] if json_output else [])])
    assert result.exit_code == 1
    output = json.loads(result.output) if json_output else None
    if failure == "absent":
        assert "not running" in result.output.lower()
        if output is not None:
            assert output["error_type"] == "DaemonNotRunningError"
    else:
        assert "not running" not in result.output.lower()
        assert "mzt restart" not in result.output
        assert "mzt start" not in result.output
        if output is not None:
            assert output["error_type"] == (
                "DaemonUnresponsiveError" if failure == "unresponsive" else "DaemonProtocolError"
            )


@pytest.mark.parametrize("failure", ["denied", "unresponsive"])
def test_status_watch_stops_without_restart_advice(monkeypatch, failure):
    from marianne.daemon.exceptions import DaemonAccessDeniedError, DaemonUnresponsiveError

    async def route(*args, **kwargs):
        if failure == "denied":
            raise DaemonAccessDeniedError()
        raise DaemonUnresponsiveError("Conductor did not respond")

    monkeypatch.setattr(detect, "try_daemon_route", route)
    result = CliRunner().invoke(app, ["status", "public-job", "--watch"])
    assert result.exit_code == 1
    assert "mzt restart" not in result.output
    assert "mzt start" not in result.output
    assert "not running" not in result.output.lower()


def test_bound_mode_600_socket_foreign_uid_denial_is_not_absence(
    monkeypatch, tmp_path,
):
    """The bound socket is real; only foreign-UID connect denial is simulated.

    An unprivileged test runner cannot chown the socket to a different UID.
    The injected EACCES models the kernel result at the exact connector seam.
    """
    path = tmp_path / "conductor.sock"
    bound = socket.socket(socket.AF_UNIX)
    bound.bind(str(path))
    os.chmod(path, 0o600)
    assert path.is_socket() and path.stat().st_mode & 0o777 == 0o600
    original_connect = asyncio.open_unix_connection
    attempts = []

    async def foreign_uid_connect(endpoint, **kwargs):
        if endpoint == str(path):
            attempts.append(endpoint)
            raise PermissionError(errno.EACCES, "Simulated foreign UID")
        return await original_connect(endpoint, **kwargs)

    monkeypatch.setattr("asyncio.open_unix_connection", foreign_uid_connect)
    monkeypatch.setattr(detect, "_resolve_socket_path", lambda _: path)
    client = DaemonClient(path)
    try:
        with pytest.raises(Exception) as caught:
            asyncio.run(client.is_daemon_running())
        assert type(caught.value).__name__ == "DaemonAccessDeniedError"
        result = CliRunner().invoke(app, ["status", "--json"])
        assert result.exit_code == 1
        report = json.loads(result.output)
        assert report["error_type"] == "DaemonAccessDeniedError"
        assert report["running_state"] == "unknown"
        assert "not running" not in result.output.lower()
        assert attempts == [str(path), str(path)]
    finally:
        asyncio.run(client.close())
        bound.close()


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
    from marianne.daemon.exceptions import DaemonNotRunningError, DaemonUnresponsiveError
    path, _ = denied_connector(monkeypatch, tmp_path)

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("asyncio.open_unix_connection", fail)
    client = DaemonClient(path)
    expected = (
        DaemonUnresponsiveError
        if isinstance(failure, TimeoutError)
        else DaemonNotRunningError
    )
    with pytest.raises(expected):
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
