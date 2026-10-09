"""Caller deadlines close pooled sockets and unavailable discovery stays visible."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from marianne.cli.helpers import await_early_failure
from marianne.daemon.exceptions import DaemonError
from marianne.dashboard.app import create_app
from tests.conftest import MockStateBackend


@pytest.mark.parametrize('result', [{'status': 'completed'}, ConnectionError('gone')])
async def test_early_failure_always_closes_client(monkeypatch, result):
    client = AsyncMock()
    if isinstance(result, Exception):
        client.call.side_effect = result
    else:
        client.call.return_value = result
    monkeypatch.setattr('marianne.daemon.ipc.client.DaemonClient', lambda *_: client)
    await await_early_failure('job', timeout=0.1, poll_interval=0.001)
    client.close.assert_awaited_once()


async def test_early_failure_deadline_includes_rpc(monkeypatch):
    client = AsyncMock()
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    client.call.side_effect = blocked
    monkeypatch.setattr('marianne.daemon.ipc.client.DaemonClient', lambda *_: client)
    task = asyncio.create_task(await_early_failure('job', timeout=0.03, poll_interval=0.001))
    await asyncio.wait_for(started.wait(), 0.2)
    done, _ = await asyncio.wait([task], timeout=0.2)
    if not done:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert done, 'polling deadline did not bound a stalled RPC'
    assert task.result() is None
    client.close.assert_awaited_once()


async def test_early_failure_cancellation_closes_client(monkeypatch):
    client = AsyncMock()
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    client.call.side_effect = blocked
    monkeypatch.setattr('marianne.daemon.ipc.client.DaemonClient', lambda *_: client)
    task = asyncio.create_task(await_early_failure('job', poll_interval=0.001))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client.close.assert_awaited_once()


def test_dashboard_reports_unavailable_roster_as_503():
    backend = MockStateBackend()
    backend.list_jobs = AsyncMock(side_effect=DaemonError('Conductor roster is unavailable'))
    app = create_app(state_backend=backend)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/api/jobs')
        assert response.status_code == 503
        assert 'unavailable' in response.json()['detail']


def test_normal_stop_refuses_unknown_work(tmp_path, monkeypatch):
    from unittest.mock import Mock

    import typer

    from marianne.daemon import process

    pid = tmp_path / 'pid'
    pid.write_text('12345')
    kill = Mock()
    monkeypatch.setattr(process, '_pid_alive', lambda _: True)
    monkeypatch.setattr(process, '_check_running_jobs', lambda _: None)
    monkeypatch.setattr(process.os, 'kill', kill)
    with pytest.raises(typer.Exit):
        process.stop_conductor(pid_file=pid)
    kill.assert_not_called()


def test_conductor_status_bounds_probes_and_closes(tmp_path, monkeypatch):
    from marianne.daemon import process

    client = AsyncMock()

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    client.call.side_effect = blocked
    monkeypatch.setattr('marianne.daemon.ipc.client.DaemonClient', lambda *args, **kw: client)
    monkeypatch.setattr(process, '_pid_alive', lambda _: True)
    monkeypatch.setattr(process, 'CONDUCTOR_PROBE_TIMEOUT', 0.02, raising=False)
    pid = tmp_path / 'pid'
    pid.write_text('12345')
    process.get_conductor_status(pid_file=pid, socket_path=tmp_path / 's')
    client.close.assert_awaited_once()


async def test_mcp_roster_unavailable_is_tool_error(tmp_path):
    from marianne.daemon.exceptions import DaemonUnresponsiveError
    from marianne.mcp.tools import JobTools

    tools = JobTools(AsyncMock(), tmp_path)
    tools._daemon_client = AsyncMock()
    tools._daemon_client.is_daemon_running.side_effect = DaemonUnresponsiveError('unresponsive')
    result = await tools.call_tool('list_jobs', {})
    assert result['isError'] is True
    assert 'unresponsive' in result['content'][0]['text']
    tools._daemon_client.list_jobs.assert_not_awaited()


@pytest.mark.parametrize('tool_class', ['JobTools', 'ControlTools'])
async def test_mcp_tool_shutdown_closes_owned_client(tmp_path, monkeypatch, tool_class):
    from marianne.mcp import tools as module

    client = AsyncMock()
    monkeypatch.setattr(module, 'DaemonClient', lambda *_: client)
    tools = getattr(module, tool_class)(AsyncMock(), tmp_path)
    await tools.shutdown()
    client.close.assert_awaited_once()


def test_dashboard_shutdown_closes_owned_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr('marianne.dashboard.app._create_daemon_client', lambda: client)
    app = create_app(state_backend=MockStateBackend(), connect_daemon=True)
    app.state.event_bridge = None
    with TestClient(app):
        pass
    client.close.assert_awaited_once()


async def test_mcp_uncertain_control_is_tool_error(tmp_path):
    from marianne.daemon.exceptions import DaemonUnresponsiveError
    from marianne.mcp.tools import ControlTools

    tools = ControlTools(AsyncMock(), tmp_path)
    tools.job_control.pause_job = AsyncMock(side_effect=DaemonUnresponsiveError('outcome unknown'))
    result = await tools.call_tool('pause_job', {'job_id': 'test'})
    assert result['isError'] is True
    assert 'outcome unknown' in result['content'][0]['text']
    tools.job_control.pause_job.assert_awaited_once()


@pytest.mark.parametrize('error', [TimeoutError('roster timed out'), OSError('roster read failed')])
async def test_mcp_failed_roster_after_health_remains_error(tmp_path, error):
    from marianne.mcp.tools import JobTools

    tools = JobTools(AsyncMock(), tmp_path)
    tools._daemon_client = AsyncMock()
    tools._daemon_client.is_daemon_running.return_value = True
    tools._daemon_client.list_jobs.side_effect = error
    result = await tools.call_tool('list_jobs', {})
    assert result['isError'] is True
    assert str(error) in result['content'][0]['text']
    assert 'mzt start' not in result['content'][0]['text']
