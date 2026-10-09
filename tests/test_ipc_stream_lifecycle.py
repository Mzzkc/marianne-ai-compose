"""Real control-channel regression tests for quiet/departed watchers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from marianne.daemon.config import DaemonConfig
from marianne.daemon.event_bus import EventBus
from marianne.daemon.health import HealthChecker
from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.server import DaemonServer
from marianne.daemon.output_hub import OutputStreamHub
from marianne.daemon.process import DaemonProcess


async def _until(predicate: Any) -> bool:
    try:
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(0)
    except TimeoutError:
        return False
    return True


def _request(method: str, params: dict[str, Any] | None = None) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "method": method,
                       "params": params or {}, "id": 1}).encode() + b"\n"


def _production_handler() -> tuple[RequestHandler, Any]:
    manager = SimpleNamespace(output_hub=OutputStreamHub(), event_bus=EventBus(),
                              shutting_down=False)
    handler = RequestHandler()
    DaemonProcess(DaemonConfig())._register_methods(handler, manager, HealthChecker(manager, None))
    return handler, manager


def _subscriber_count(manager: Any, method: str) -> int:
    if method == "job.output.stream":
        return sum(len(subs) for subs in manager.output_hub._job_subscribers.values())
    return manager.event_bus.subscriber_count


@pytest.mark.parametrize("method", ["job.output.stream", "daemon.monitor.stream"])
async def test_quiet_departed_watchers_release_capacity(tmp_path: Path, method: str) -> None:
    handler, manager = _production_handler()
    server = DaemonServer(tmp_path / "s", handler, max_connections=5)
    await server.start()
    clients = []
    try:
        for index in range(5):
            _, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
            clients.append(writer)
            writer.write(_request(method, {"job_id": f"quiet-{index}"}))
            await writer.drain()
        assert await _until(lambda: _subscriber_count(manager, method) == 5)
        for writer in clients:
            writer.close()
            await writer.wait_closed()
        assert await _until(lambda: _subscriber_count(manager, method) == 0), (
            "departed quiet watchers retained their subscriptions"
        )
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
        clients.append(writer)
        writer.write(_request("daemon.health"))
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), 1))
        assert reply["result"]["status"] == "ok"
    finally:
        for writer in clients:
            writer.close()
        await server.stop()
        await manager.event_bus.shutdown()


@pytest.mark.parametrize("method", ["job.output.stream", "daemon.monitor.stream"])
async def test_live_watchers_do_not_occupy_short_rpc_budget(tmp_path: Path, method: str) -> None:
    handler, manager = _production_handler()
    server = DaemonServer(tmp_path / "s", handler, max_concurrent_requests=1)
    await server.start()
    clients = []
    try:
        for index in range(2):
            _, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
            clients.append(writer)
            writer.write(_request(method, {"job_id": f"live-{index}"}))
            await writer.drain()
        assert await _until(lambda: _subscriber_count(manager, method) == 2), (
            "live watchers consumed the one short-RPC processing slot"
        )
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
        clients.append(writer)
        writer.write(_request("daemon.health"))
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), 1))
        assert reply["result"]["status"] == "ok"
        assert _subscriber_count(manager, method) == 2
    finally:
        for writer in clients:
            writer.close()
        await server.stop()
        await manager.event_bus.shutdown()


async def test_over_capacity_connection_gets_bounded_refusal(tmp_path: Path) -> None:
    handler, manager = _production_handler()
    server = DaemonServer(tmp_path / "s", handler, max_connections=1)
    await server.start()
    clients = []
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
        clients.append(writer)
        writer.write(_request("daemon.health"))
        await writer.drain()
        assert json.loads(await asyncio.wait_for(reader.readline(), 1))["result"]["status"] == "ok"
        overflow, extra = await asyncio.open_unix_connection(str(tmp_path / "s"))
        clients.append(extra)
        extra.write(_request("daemon.health"))
        await extra.drain()
        try:
            response = await asyncio.wait_for(overflow.readline(), 1)
        except TimeoutError:
            response = b""
        assert response, "over-capacity connection waited indefinitely instead of being refused"
        assert json.loads(response)["error"]["code"] == -32001
    finally:
        for writer in clients:
            writer.close()
        await server.stop()
        await manager.event_bus.shutdown()


async def test_client_disconnect_does_not_cancel_accepted_mutation(tmp_path: Path) -> None:
    handler = RequestHandler()
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def mutate(params: dict[str, Any], writer: asyncio.StreamWriter) -> dict[str, bool]:
        entered.set()
        await release.wait()
        completed.set()
        return {"accepted": True}

    handler.register("job.submit", mutate)
    server = DaemonServer(tmp_path / "s", handler)
    await server.start()
    try:
        _, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
        writer.write(_request("job.submit"))
        await writer.drain()
        await asyncio.wait_for(entered.wait(), 1)
        writer.close()
        await writer.wait_closed()
        release.set()
        await asyncio.wait_for(completed.wait(), 1)
    finally:
        release.set()
        await server.stop()


async def test_nonreading_peer_releases_transport_after_write_deadline(tmp_path: Path) -> None:
    handler = RequestHandler()
    handler.register('test.large', _large_result)
    server = DaemonServer(tmp_path / 's', handler, write_timeout=0.03)
    captured = []
    accept = server._accept_connection

    async def capture(reader, writer):
        captured.append(writer)
        await accept(reader, writer)

    server._accept_connection = capture
    await server.start()
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / 's'))
    try:
        writer.write(_request('test.large'))
        await writer.drain()
        assert await _until(lambda: captured and not server._connections)
        assert await _until(lambda: captured[0].get_extra_info('socket').fileno() == -1), (
            'write timeout left a nonreading peer transport open'
        )
    finally:
        writer.close()
        await server.stop()


async def _large_result(params: Any, writer: Any) -> dict[str, str]:
    return {'payload': 'x' * (4 * 1024 * 1024)}


async def test_request_deadline_releases_dispatch_capacity(tmp_path: Path) -> None:
    handler = RequestHandler()
    cancelled = asyncio.Event()

    async def blocked(params, writer):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def ping(params, writer):
        return {'ok': True}

    handler.register('blocked', blocked)
    handler.register('ping', ping)
    server = DaemonServer(tmp_path / 's', handler, request_timeout=0.03,
                          max_concurrent_requests=1)
    await server.start()
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / 's'))
    try:
        writer.write(_request('blocked'))
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 1))
        assert 'deadline exceeded' in response['error']['message']
        assert cancelled.is_set()
        writer.write(_request('ping'))
        await writer.drain()
        assert json.loads(await asyncio.wait_for(reader.readline(), 1))['result']['ok']
    finally:
        writer.close()
        await server.stop()


async def test_stream_budget_rejects_excess_without_blocking_health(tmp_path: Path) -> None:
    handler, manager = _production_handler()
    server = DaemonServer(tmp_path / 's', handler, max_streams=1)
    await server.start()
    clients = []
    try:
        _, first = await asyncio.open_unix_connection(str(tmp_path / 's'))
        clients.append(first)
        first.write(_request('daemon.monitor.stream'))
        await first.drain()
        assert await _until(lambda: manager.event_bus.subscriber_count == 1)
        reader, second = await asyncio.open_unix_connection(str(tmp_path / 's'))
        clients.append(second)
        second.write(_request('daemon.monitor.stream'))
        await second.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 1))
        assert response['error']['code'] == -32001
        second.write(_request('daemon.health'))
        await second.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 1))
        assert response['result']['status'] == 'ok'
    finally:
        for client in clients:
            client.close()
        await server.stop()
        await manager.event_bus.shutdown()


async def test_timed_out_notification_does_not_contaminate_next_response(tmp_path: Path) -> None:
    handler = RequestHandler()

    async def blocked(params, writer):
        await asyncio.Event().wait()

    async def ping(params, writer):
        return {'ok': True}

    handler.register('blocked', blocked)
    handler.register('ping', ping)
    server = DaemonServer(tmp_path / 's', handler, request_timeout=0.03)
    await server.start()
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / 's'))
    try:
        writer.write(b'{"jsonrpc":"2.0","method":"blocked"}\n' + _request('ping'))
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), 1))
        assert response['id'] == 1
        assert response['result']['ok']
    finally:
        writer.close()
        await server.stop()


@pytest.mark.parametrize("cancel_count", [1, 3])
async def test_cancelled_stop_finishes_handlers_and_releases_socket_ownership(
    tmp_path: Path, cancel_count: int,
) -> None:
    from marianne.daemon.socket_ownership import OwnedUnixSocket

    entered, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleaned = False

    async def blocked(params, writer):
        nonlocal cleaned
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish.wait()
            cleaned = True

    handler = RequestHandler()
    handler.register("blocked", blocked)
    path = tmp_path / "s"
    server = DaemonServer(path, handler)
    await server.start()
    _, writer = await asyncio.open_unix_connection(str(path))
    stop = None
    try:
        writer.write(_request("blocked"))
        await writer.drain()
        await asyncio.wait_for(entered.wait(), 1)
        stop = asyncio.create_task(server.stop())
        await asyncio.wait_for(cleaning.wait(), 1)
        for _ in range(cancel_count):
            stop.cancel()
            await asyncio.sleep(0)
        assert not stop.done(), "stop returned while owned handler cleanup was pending"
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stop, 1)
        assert cleaned
        assert not path.exists()
        assert server._socket_owner._lock_fd is None
        contender = OwnedUnixSocket(path)
        await contender.prepare()
        contender.release()
        # A stopped instance can be reused after the shielded cleanup settles.
        await server.start()
    finally:
        finish.set()
        if stop is not None:
            await asyncio.gather(stop, return_exceptions=True)
        writer.close()
        await server.stop()
