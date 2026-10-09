"""Integration tests for DaemonServer concurrency controls.

Tests verify that connection limits and request concurrency limits
operate independently, using real Unix sockets (no mocks).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.server import DaemonServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(method: str, req_id: int = 1) -> bytes:
    """Build a JSON-RPC request as NDJSON bytes."""
    return json.dumps({"jsonrpc": "2.0", "method": method, "id": req_id}).encode() + b"\n"


async def _read_response(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one NDJSON line and parse it."""
    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    assert line, "Server closed connection unexpectedly"
    return json.loads(line)


def _build_slow_handler(delay: float, barrier: asyncio.Event | None = None) -> RequestHandler:
    """Build a handler where 'test.slow' sleeps for *delay* seconds.

    If *barrier* is provided, the handler sets it when processing starts.
    """
    handler = RequestHandler()

    async def _slow(params: dict[str, Any], writer: asyncio.StreamWriter) -> dict[str, Any]:
        if barrier is not None:
            barrier.set()
        await asyncio.sleep(delay)
        return {"ok": True}

    handler.register("test.slow", _slow)
    return handler


def _build_echo_handler() -> RequestHandler:
    """Build a handler where 'test.echo' returns the params."""
    handler = RequestHandler()

    async def _echo(params: dict[str, Any], writer: asyncio.StreamWriter) -> dict[str, Any]:
        return params

    handler.register("test.echo", _echo)
    return handler


def _build_counting_handler(
    counter: dict[str, int],
    hold_event: asyncio.Event | None = None,
) -> RequestHandler:
    """Build a handler that counts concurrent in-flight requests.

    ``counter["current"]`` tracks live requests, ``counter["peak"]``
    records the maximum seen.  If *hold_event* is provided, the handler
    waits for it before returning (so callers can control concurrency).
    """
    handler = RequestHandler()

    async def _counted(params: dict[str, Any], writer: asyncio.StreamWriter) -> dict[str, Any]:
        counter["current"] += 1
        counter["peak"] = max(counter["peak"], counter["current"])
        try:
            if hold_event is not None:
                await asyncio.wait_for(hold_event.wait(), timeout=10.0)
            else:
                await asyncio.sleep(0.05)
        finally:
            counter["current"] -= 1
        return {"peak": counter["peak"]}

    handler.register("test.counted", _counted)
    return handler


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def socket_path(tmp_path: Path) -> Path:
    return tmp_path / "test.sock"


async def test_request_semaphore_limits_concurrency(
    socket_path: Path,
) -> None:
    """Request semaphore limits concurrent handler invocations."""
    max_requests = 2
    counter: dict[str, int] = {"current": 0, "peak": 0}
    hold = asyncio.Event()

    handler = _build_counting_handler(counter, hold_event=hold)
    server = DaemonServer(
        socket_path,
        handler,
        max_connections=50,
        max_concurrent_requests=max_requests,
    )

    await server.start()
    try:
        # Open 4 connections, each sending one request
        conns: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        for _ in range(4):
            r, w = await asyncio.open_unix_connection(str(socket_path))
            conns.append((r, w))

        # Send all requests without waiting for responses
        for i, (_, w) in enumerate(conns):
            w.write(_make_request("test.counted", req_id=i + 1))
            await w.drain()

        # Give the server time to start processing
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if counter["current"] >= max_requests:
                break
            await asyncio.sleep(0.02)

        # Peak should be capped at max_concurrent_requests
        assert counter["peak"] <= max_requests
        assert counter["current"] <= max_requests

        # Release the hold so all requests finish
        hold.set()

        # Read all responses
        for r, w in conns:
            resp = await _read_response(r)
            assert "result" in resp
            w.close()
            await w.wait_closed()
    finally:
        await server.stop()


async def test_connection_limit_rejects_excess(socket_path: Path) -> None:
    """Already accepted sockets are bounded errors instead of indefinite queues."""
    server = DaemonServer(socket_path, _build_echo_handler(), max_connections=2)
    await server.start()
    held = []
    try:
        for _ in range(2):
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            held.append(writer)
            writer.write(_make_request("test.echo"))
            await writer.drain()
            assert (await _read_response(reader))["result"] == {}
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        held.append(writer)
        response = await asyncio.wait_for(_read_response(reader), 1)
        assert response["id"] is None  # rejection happens before parsing a request
        assert response["error"]["code"] == -32001
        assert await asyncio.wait_for(reader.read(), 1) == b""
    finally:
        for writer in held:
            writer.close()
        await server.stop()


async def test_persistent_connection_multiple_requests(
    socket_path: Path,
) -> None:
    """A single connection can send multiple sequential requests."""
    handler = _build_echo_handler()
    server = DaemonServer(socket_path, handler, max_connections=10, max_concurrent_requests=10)

    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        for i in range(5):
            writer.write(_make_request("test.echo", req_id=i + 1))
            await writer.drain()
            resp = await _read_response(reader)
            assert resp["id"] == i + 1
            assert "result" in resp

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


async def test_mixed_load_independent_limits(
    socket_path: Path,
) -> None:
    """Connection limit and request limit operate independently.

    With max_connections=4 and max_concurrent_requests=2, four clients
    can be connected but only two requests process simultaneously.
    """
    max_conns = 4
    max_requests = 2
    counter: dict[str, int] = {"current": 0, "peak": 0}
    hold = asyncio.Event()

    handler = _build_counting_handler(counter, hold_event=hold)
    server = DaemonServer(
        socket_path,
        handler,
        max_connections=max_conns,
        max_concurrent_requests=max_requests,
    )

    await server.start()
    try:
        # Open max_conns connections — all should succeed
        conns: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        for _ in range(max_conns):
            r, w = await asyncio.open_unix_connection(str(socket_path))
            conns.append((r, w))

        # Send a request on each connection
        for i, (_, w) in enumerate(conns):
            w.write(_make_request("test.counted", req_id=i + 1))
            await w.drain()

        # Wait for requests to start processing
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if counter["current"] >= max_requests:
                break
            await asyncio.sleep(0.02)

        # All 4 connections are open, but only 2 requests at a time
        assert len(conns) == max_conns
        assert counter["peak"] <= max_requests

        # Release and collect responses
        hold.set()
        for r, w in conns:
            resp = await _read_response(r)
            assert "result" in resp
            w.close()
            await w.wait_closed()
    finally:
        await server.stop()


async def test_server_constructor_validation() -> None:
    """Constructor rejects invalid concurrency parameters."""
    handler = RequestHandler()
    sock = Path("/tmp/test-validation.sock")

    with pytest.raises(ValueError, match="max_connections must be >= 1"):
        DaemonServer(sock, handler, max_connections=0)

    with pytest.raises(ValueError, match="max_concurrent_requests must be >= 1"):
        DaemonServer(sock, handler, max_concurrent_requests=0)


async def test_graceful_shutdown_cleans_up(
    socket_path: Path,
) -> None:
    """Server.stop() removes the socket and marks the server as stopped."""
    handler = _build_echo_handler()
    server = DaemonServer(socket_path, handler, max_connections=10, max_concurrent_requests=10)

    await server.start()
    assert server.is_running
    assert socket_path.exists()

    # Make a request then close connection cleanly
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.write(_make_request("test.echo", req_id=1))
    await writer.drain()
    resp = await _read_response(reader)
    assert "result" in resp
    writer.close()
    await writer.wait_closed()

    # Stop should remove socket and update state
    await server.stop()
    assert not server.is_running
    assert not socket_path.exists()
