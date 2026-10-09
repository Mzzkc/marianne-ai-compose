"""Actual transport controls for uncertain RPC outcomes and lease ownership."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer

from marianne.cli.commands.status import _status_overview
from marianne.daemon.detect import try_daemon_route
from marianne.daemon.exceptions import DaemonError, DaemonNotRunningError
from marianne.daemon.ipc.client import ConnectionPool, DaemonClient


@asynccontextmanager
async def wire_server(path, reply, *, close_after_response=False):
    tasks = set()
    writers = set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        try:
            while line := await reader.readline():
                message = json.loads(line)
                response = await reply(message, writer)
                if response is not None:
                    writer.write(json.dumps(response).encode() + b"\n")
                    await writer.drain()
                    if close_after_response:
                        return
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)
            tasks.discard(task)

    server = await asyncio.start_unix_server(serve, path=str(path))
    try:
        yield
    finally:
        server.close()
        await server.wait_closed()
        for writer in writers:
            writer.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)


def result(message):
    return {"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}}


@pytest.mark.parametrize("method", ["job.submit", "job.cancel", "future.mutation"])
async def test_timeout_never_repeats_uncertain_mutation(tmp_path, method):
    calls = []

    async def reply(message, writer):
        calls.append(message["method"])
        return None

    async with (
        wire_server(tmp_path / "s.sock", reply),
        DaemonClient(tmp_path / "s.sock", timeout=0.03) as client,
    ):
        with pytest.raises(TimeoutError):
            await client.call(method)
    assert calls == [method]


@pytest.mark.parametrize("method,expected", [("job.submit", 1), ("job.list", 2)])
async def test_disconnect_retries_only_known_safe_reads(tmp_path, method, expected):
    calls = []

    async def reply(message, writer):
        calls.append(message["method"])
        if len(calls) == 1:
            writer.close()
            return None
        return result(message)

    async with (
        wire_server(tmp_path / "s.sock", reply),
        DaemonClient(tmp_path / "s.sock", timeout=0.1) as client,
    ):
        if method == "job.submit":
            with pytest.raises(DaemonNotRunningError):
                await client.call(method)
        else:
            assert await client.call(method) == {"ok": True}
    assert len(calls) == expected


async def test_cancelled_response_releases_one_slot_without_reusing_late_reply(tmp_path):
    received = asyncio.Event()

    async def reply(message, writer):
        if message["method"] == "job.submit":
            received.set()
            return None
        return result(message)

    async with (
        wire_server(tmp_path / "s.sock", reply),
        DaemonClient(tmp_path / "s.sock", pool_size=1, timeout=0.05) as client,
    ):
        task = asyncio.create_task(client.call("job.submit"))
        await received.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await client.call("job.list") == {"ok": True}


async def test_connect_cancellation_releases_pool_capacity(tmp_path):
    socket = tmp_path / "s.sock"
    socket.touch()
    pool = ConnectionPool(socket, max_size=1, acquire_timeout=0.05)
    connecting = asyncio.Event()

    async def stalled_connect(*args, **kwargs):
        connecting.set()
        await asyncio.Event().wait()

    with patch("asyncio.open_unix_connection", side_effect=stalled_connect):
        task = asyncio.create_task(pool.acquire())
        await connecting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def reply(message, writer):
        return result(message)

    socket.unlink()
    async with wire_server(socket, reply):
        reader, writer = await pool.acquire()
        pool.release(reader, writer)
        await pool.close()


async def test_close_closes_checked_out_writer_and_refuses_waiting_acquirer(tmp_path):
    async def reply(message, writer):
        return result(message)

    async with wire_server(tmp_path / "s.sock", reply):
        pool = ConnectionPool(tmp_path / "s.sock", max_size=1, acquire_timeout=0.1)
        reader, writer = await pool.acquire()
        waiter = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0)
        await pool.close()
        assert writer.is_closing()
        with pytest.raises(DaemonNotRunningError, match="closed"):
            await waiter
        pool.release(reader, writer)


@pytest.mark.parametrize(
    "bad",
    [
        {"jsonrpc": "2.0", "id": 42, "result": "wrong request"},
        {"jsonrpc": "2.0", "id": True, "result": "wrong type"},
        {"jsonrpc": "1.0", "id": 1, "result": None},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "result": None, "error": {}},
        {"jsonrpc": "2.0", "id": 1, "error": "broken"},
        [],
    ],
)
async def test_corrupt_envelope_fails_and_connection_is_discarded(tmp_path, bad):
    connections = set()

    async def reply(message, writer):
        connections.add(writer)
        return bad if message["id"] == 1 else result(message)

    async with (
        wire_server(tmp_path / "s.sock", reply),
        DaemonClient(tmp_path / "s.sock", pool_size=1) as client,
    ):
        with pytest.raises(DaemonError, match="response"):
            await client.call("job.list")
        assert await client.call("job.list") == {"ok": True}
    assert len(connections) == 2


async def test_health_probe_is_bounded_and_timeout_is_not_absence(tmp_path):
    async def reply(message, writer):
        return None

    with patch("marianne.daemon.ipc.client._HEALTH_TIMEOUT_SECONDS", 0.03, create=True):
        async with (
            wire_server(tmp_path / "s.sock", reply),
            DaemonClient(tmp_path / "s.sock") as client,
        ):
            with pytest.raises(DaemonError, match="respond"):
                await asyncio.wait_for(client.is_daemon_running(), timeout=0.3)


async def test_route_preserves_unresponsive_probe_and_closes_client():
    with patch("marianne.daemon.ipc.client.DaemonClient") as cls:
        client = cls.return_value
        client.close = AsyncMock()
        client.is_daemon_running = AsyncMock(side_effect=DaemonError("did not respond"))
        with pytest.raises(DaemonError, match="respond"):
            await try_daemon_route("job.submit", {}, socket_path=Path("/fake"))
        client.call.assert_not_called()
        client.close.assert_awaited_once()


async def test_route_uncertain_write_failure_cannot_fall_back_to_direct():
    with patch("marianne.daemon.ipc.client.DaemonClient") as cls:
        client = cls.return_value
        client.close = AsyncMock()
        client.is_daemon_running = AsyncMock(return_value=True)
        client.call = AsyncMock(side_effect=BrokenPipeError("lost response"))
        with pytest.raises(DaemonError, match="outcome.*unknown"):
            await try_daemon_route("job.submit", {})
        client.close.assert_awaited_once()


@pytest.mark.parametrize("list_failure", ["exception", "not_routed", "invalid"])
async def test_overview_failed_discovery_is_error_not_empty_success(list_failure):
    async def route(method, params):
        if method == "daemon.health":
            return True, {"status": "healthy"}
        if list_failure == "exception":
            raise TimeoutError("lost job list")
        if list_failure == "not_routed":
            return False, None
        return True, {}

    with (
        patch("marianne.daemon.detect.try_daemon_route", side_effect=route),
        patch("marianne.cli.commands.status.output_json") as output,
    ):
        with pytest.raises(typer.Exit) as exc:
            await _status_overview(True)
        assert exc.value.exit_code == 1
        payload = output.call_args.args[0]
        assert payload["active_count"] is None
        assert payload["jobs_known"] is False


async def test_overview_unresponsive_health_reports_real_failure():
    with (
        patch(
            "marianne.daemon.detect.try_daemon_route", side_effect=DaemonError("did not respond")
        ),
        patch("marianne.cli.commands.status.output_error") as output,
    ):
        with pytest.raises(typer.Exit):
            await _status_overview(True)
        assert "did not respond" in output.call_args.args[0]
        assert "not running" not in output.call_args.args[0]


@pytest.mark.parametrize("operation", ["call", "stream", "health"])
async def test_admission_overload_with_null_id_preserves_resource_error(tmp_path, operation):
    from marianne.daemon.exceptions import ResourceExhaustedError

    async def reply(message, writer):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "capacity"}}

    async with (
        wire_server(tmp_path / "s.sock", reply, close_after_response=True),
        DaemonClient(tmp_path / "s.sock") as client,
    ):
        with pytest.raises(ResourceExhaustedError, match="capacity"):
            if operation == "stream":
                async for _ in client.stream("job.monitor"):
                    pass
            elif operation == "health":
                await client.is_daemon_running()
            else:
                await client.call("job.submit")


async def test_stream_unrelated_final_response_is_not_success(tmp_path):
    async def reply(message, writer):
        return {"jsonrpc": "2.0", "id": message["id"] + 1, "result": None}

    async with (
        wire_server(tmp_path / "s.sock", reply),
        DaemonClient(tmp_path / "s.sock") as client,
    ):
        with pytest.raises(DaemonError, match="response"):
            async for _ in client.stream("job.monitor"):
                pass


async def test_explicit_health_request_has_short_total_deadline(tmp_path):
    async def reply(message, writer):
        return None

    with patch("marianne.daemon.ipc.client._HEALTH_TIMEOUT_SECONDS", 0.03):
        async with (
            wire_server(tmp_path / "s.sock", reply),
            DaemonClient(tmp_path / "s.sock") as client,
        ):
            started = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(client.health(), timeout=0.3)
            assert asyncio.get_running_loop().time() - started < 0.15


async def test_routed_health_stays_bounded_after_healthy_probe(tmp_path):
    calls = 0

    async def reply(message, writer):
        nonlocal calls
        calls += 1
        return result(message) if calls == 1 else None

    with patch("marianne.daemon.ipc.client._HEALTH_TIMEOUT_SECONDS", 0.03):
        async with wire_server(tmp_path / "s.sock", reply):
            with pytest.raises(DaemonError, match="respond"):
                await asyncio.wait_for(
                    try_daemon_route("daemon.health", {}, socket_path=tmp_path / "s.sock"),
                    timeout=0.3,
                )


@pytest.mark.parametrize("operation", ["call", "stream"])
async def test_write_backpressure_is_part_of_request_deadline(tmp_path, operation):
    connected = 0
    tasks = set()

    async def do_not_read(reader, writer):
        nonlocal connected
        connected += 1
        task = asyncio.current_task()
        tasks.add(task)
        try:
            await asyncio.Event().wait()
        finally:
            writer.close()
            tasks.discard(task)

    socket = tmp_path / "s.sock"
    server = await asyncio.start_unix_server(do_not_read, path=str(socket), limit=1024)
    try:
        async with DaemonClient(socket, timeout=0.03) as client:
            started = asyncio.get_running_loop().time()

            async def request():
                params = {"payload": "x" * 4_000_000}
                if operation == "call":
                    await client.call("job.submit", params)
                else:
                    async for _ in client.stream("job.monitor", params):
                        pass

            with pytest.raises(TimeoutError):
                await asyncio.wait_for(request(), timeout=0.3)
            assert asyncio.get_running_loop().time() - started < 0.15
        assert connected == 1
    finally:
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)


async def test_health_cleanup_cannot_outlive_its_probe_deadline(tmp_path):
    from unittest.mock import MagicMock

    socket = tmp_path / "s.sock"
    socket.touch()
    reader = asyncio.StreamReader()
    writer = MagicMock()
    writer.drain = AsyncMock()
    cleanup = asyncio.Event()
    writer.wait_closed = AsyncMock(side_effect=cleanup.wait)
    with (
        patch("asyncio.open_unix_connection", AsyncMock(return_value=(reader, writer))),
        patch("marianne.daemon.ipc.client._HEALTH_TIMEOUT_SECONDS", 0.03),
    ):
        client = DaemonClient(socket)
        task = asyncio.create_task(client.is_daemon_running())
        try:
            await asyncio.sleep(0.15)
            assert task.done(), "Health cleanup left the bounded probe hanging"
            with pytest.raises(DaemonError, match="respond"):
                await task
        finally:
            cleanup.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.close()


async def test_close_keeps_new_pool_created_during_old_pool_cleanup(tmp_path):
    async def reply(message, writer):
        return result(message)

    async with wire_server(tmp_path / "s.sock", reply):
        client = DaemonClient(tmp_path / "s.sock")
        await client.call("job.list")
        old_pool = client._get_pool()
        old_writer = old_pool._idle[0][1]
        started = asyncio.Event()
        gate = asyncio.Event()

        async def close_wait():
            started.set()
            await gate.wait()

        with patch.object(old_writer, "wait_closed", side_effect=close_wait):
            close_task = asyncio.create_task(client.close())
            await started.wait()
            await client.call("job.list")
            new_pool = client._get_pool()
            gate.set()
            await close_task
        try:
            assert client._pool is new_pool
        finally:
            await new_pool.close()
            await client.close()


async def test_availability_and_run_preserve_unresponsive_health_error(tmp_path):
    from marianne.cli.commands.run import _try_daemon_submit
    from marianne.daemon.exceptions import DaemonUnresponsiveError

    with (
        patch("marianne.daemon.ipc.client.DaemonClient") as cls,
        patch("marianne.cli.commands.run.output_error") as output,
    ):
        client = cls.return_value
        client.close = AsyncMock()
        client.is_daemon_running = AsyncMock(side_effect=DaemonUnresponsiveError("did not respond"))
        with pytest.raises(typer.Exit):
            await _try_daemon_submit(tmp_path / "score.yaml", None, False, False, False, True)
        assert "did not respond" in output.call_args.args[0]
        assert "not running" not in output.call_args.args[0]
        client.call.assert_not_called()


async def test_accepted_health_eof_is_unresponsive_not_absence(tmp_path):
    async def reply(message, writer):
        writer.close()
        return None

    async with (
        wire_server(tmp_path / "s.sock", reply),
        DaemonClient(tmp_path / "s.sock") as client,
    ):
        with pytest.raises(DaemonError, match="health"):
            await client.is_daemon_running()


async def test_health_is_independent_of_busy_client_pool(tmp_path):
    from marianne.daemon.ipc.handler import RequestHandler
    from marianne.daemon.ipc.server import DaemonServer

    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(params, writer):
        entered.set()
        await release.wait()
        return {"ok": True}
    async def health(params, writer):
        return {"status": "ok"}
    handler = RequestHandler()
    handler.register("blocked", blocked)
    handler.register("daemon.health", health)
    path = tmp_path / "s"
    server = DaemonServer(path, handler)
    await server.start()
    client = DaemonClient(path, pool_size=1, timeout=0.1)
    query = asyncio.create_task(client.call("blocked"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert await client.is_daemon_running()
        assert (await client.health())["status"] == "ok"
    finally:
        release.set()
        await asyncio.gather(query, return_exceptions=True)
        await client.close()
        await server.stop()
