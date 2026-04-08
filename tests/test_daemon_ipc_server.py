"""Tests for marianne.daemon.ipc.server and handler — async Unix socket server.

Uses real Unix sockets (via tmp_path) instead of mocks to validate actual
I/O behavior. The RequestHandler is wired with simple test handlers.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.protocol import JsonRpcError, JsonRpcRequest, JsonRpcResponse
from marianne.daemon.ipc.server import DaemonServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler() -> RequestHandler:
    """Build a RequestHandler with simple test methods registered."""
    handler = RequestHandler()

    async def _echo(params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {"echo": params}

    async def _status(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        return {"running": True, "jobs": 0}

    async def _fail(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        raise JobSubmissionError("test failure")

    async def _crash(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        raise RuntimeError("unexpected boom")

    async def _slow(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
        await asyncio.sleep(5)
        return {"done": True}

    handler.register("test.echo", _echo)
    handler.register("daemon.status", _status)
    handler.register("test.fail", _fail)
    handler.register("test.crash", _crash)
    handler.register("test.slow", _slow)
    return handler


async def _send_request(
    socket_path: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Send a single JSON-RPC request and read the response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        return json.loads(line)
    finally:
        writer.close()
        await writer.wait_closed()


async def _make_dummy_writer() -> tuple[asyncio.StreamWriter, socket.socket, socket.socket]:
    """Create a dummy StreamWriter from a socket pair for handler unit tests.

    Returns (writer, rsock, wsock) — caller must close all three.
    """
    rsock, wsock = socket.socketpair()
    rsock.setblocking(False)
    wsock.setblocking(False)
    loop = asyncio.get_event_loop()
    transport, protocol = await loop.create_connection(
        lambda: asyncio.Protocol(), sock=wsock
    )
    # StreamWriter needs a reader protocol — create a minimal one
    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    writer = asyncio.StreamWriter(transport, reader_protocol, reader, loop)
    return writer, rsock, wsock


# ---------------------------------------------------------------------------
# Server lifecycle tests
# ---------------------------------------------------------------------------


class TestDaemonServerLifecycle:
    """Tests for server start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_socket(self, tmp_path: Path):
        """Starting the server creates the socket file."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            assert sock.exists()
            assert server.is_running
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stop_removes_socket(self, tmp_path: Path):
        """Stopping the server removes the socket file."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        await server.stop()
        assert not sock.exists()
        assert not server.is_running

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, tmp_path: Path):
        """Calling stop() multiple times is safe."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        assert server.is_running
        await server.stop()
        assert not server.is_running
        await server.stop()  # Should not raise
        assert not server.is_running

    @pytest.mark.asyncio
    async def test_start_removes_stale_socket(self, tmp_path: Path):
        """Starting cleans up a leftover socket from a previous run."""
        sock = tmp_path / "test.sock"
        sock.touch()  # Stale file
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            assert server.is_running
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_start_creates_parent_dirs(self, tmp_path: Path):
        """Starting creates parent directories if missing."""
        sock = tmp_path / "nested" / "dir" / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            assert sock.exists()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_socket_permissions(self, tmp_path: Path):
        """Socket has the configured permissions."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler(), permissions=0o600)
        await server.start()
        try:
            import stat

            mode = sock.stat().st_mode
            # Check only user bits (socket type flag varies by OS)
            assert mode & 0o777 == 0o600
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Request handling tests (via real server)
# ---------------------------------------------------------------------------


class TestDaemonServerRequestHandling:
    """Tests for request routing and response generation."""

    @pytest.mark.asyncio
    async def test_echo_method(self, tmp_path: Path):
        """A registered method receives params and returns a result."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            resp = await _send_request(sock, {
                "jsonrpc": "2.0",
                "method": "test.echo",
                "params": {"message": "hello"},
                "id": 1,
            })
            assert resp["result"] == {"echo": {"message": "hello"}}
            assert resp["id"] == 1
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_status_method(self, tmp_path: Path):
        """daemon.status returns the expected result structure."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            resp = await _send_request(sock, {
                "jsonrpc": "2.0",
                "method": "daemon.status",
                "id": 2,
            })
            assert resp["result"]["running"] is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_method_not_found(self, tmp_path: Path):
        """Unknown methods get a -32601 error."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            resp = await _send_request(sock, {
                "jsonrpc": "2.0",
                "method": "nonexistent.method",
                "id": 3,
            })
            assert "error" in resp
            assert resp["error"]["code"] == -32601
            assert "nonexistent.method" in resp["error"]["message"]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_daemon_error_mapped(self, tmp_path: Path):
        """DaemonError subclass is mapped to the appropriate error code."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            resp = await _send_request(sock, {
                "jsonrpc": "2.0",
                "method": "test.fail",
                "id": 4,
            })
            assert "error" in resp
            # JobSubmissionError maps to JOB_NOT_FOUND (-32000)
            assert resp["error"]["code"] == -32000
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_internal_error_on_crash(self, tmp_path: Path):
        """Unexpected exceptions become -32603 internal errors."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            resp = await _send_request(sock, {
                "jsonrpc": "2.0",
                "method": "test.crash",
                "id": 5,
            })
            assert "error" in resp
            assert resp["error"]["code"] == -32603
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Malformed request tests
# ---------------------------------------------------------------------------


class TestDaemonServerMalformedRequests:
    """Tests for malformed/invalid request handling."""

    @pytest.mark.asyncio
    async def test_malformed_json(self, tmp_path: Path):
        """Non-JSON input returns parse error (-32700)."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(sock))
            try:
                writer.write(b"this is not json\n")
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                resp = json.loads(line)
                assert resp["error"]["code"] == -32700
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_missing_method_field(self, tmp_path: Path):
        """JSON without 'method' returns invalid request (-32600)."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            resp = await _send_request(sock, {
                "jsonrpc": "2.0",
                "id": 6,
            })
            assert resp["error"]["code"] == -32600
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_non_dict_json(self, tmp_path: Path):
        """A JSON array instead of object returns invalid request."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(sock))
            try:
                writer.write(json.dumps([1, 2, 3]).encode() + b"\n")
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                resp = json.loads(line)
                assert resp["error"]["code"] == -32600
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_multiple_requests_on_same_connection(self, tmp_path: Path):
        """Multiple requests on one connection are handled sequentially."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(sock))
            try:
                for i in range(3):
                    req = {"jsonrpc": "2.0", "method": "test.echo", "params": {"n": i}, "id": i}
                    writer.write(json.dumps(req).encode() + b"\n")
                    await writer.drain()
                    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    resp = json.loads(line)
                    assert resp["result"]["echo"]["n"] == i
                    assert resp["id"] == i
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


class TestDaemonServerConcurrency:
    """Tests for concurrent client handling."""

    @pytest.mark.asyncio
    async def test_concurrent_clients(self, tmp_path: Path):
        """Multiple clients can connect and get responses simultaneously."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            async def _client(client_id: int) -> dict[str, Any]:
                return await _send_request(sock, {
                    "jsonrpc": "2.0",
                    "method": "test.echo",
                    "params": {"client": client_id},
                    "id": client_id,
                })

            results = await asyncio.gather(*[_client(i) for i in range(5)])
            for i, resp in enumerate(results):
                assert resp["result"]["echo"]["client"] == i
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_client_disconnect_doesnt_crash_server(self, tmp_path: Path):
        """A client disconnecting abruptly doesn't break the server."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()
        try:
            # Connect and immediately close
            _reader, writer = await asyncio.open_unix_connection(str(sock))
            writer.close()
            await writer.wait_closed()

            # Server should still work for new clients
            await asyncio.sleep(0.05)
            resp = await _send_request(sock, {
                "jsonrpc": "2.0",
                "method": "daemon.status",
                "id": 1,
            })
            assert resp["result"]["running"] is True
        finally:
            await server.stop()

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_stop_cancels_active_connections(self, tmp_path: Path):
        """Stopping the server cancels in-flight slow requests cleanly."""
        sock = tmp_path / "test.sock"
        server = DaemonServer(sock, _make_handler())
        await server.start()

        # Start a slow request in a background task
        reader, writer = await asyncio.open_unix_connection(str(sock))
        req = {"jsonrpc": "2.0", "method": "test.slow", "id": 1}
        writer.write(json.dumps(req).encode() + b"\n")
        await writer.drain()

        # Give the server a moment to start processing
        await asyncio.sleep(0.1)

        # Close client first so server-side writer.wait_closed() can complete
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        # Now stop the server — should complete quickly since client is gone
        await asyncio.wait_for(server.stop(), timeout=5.0)
        assert not server.is_running


# ---------------------------------------------------------------------------
# RequestHandler unit tests (with dummy writer)
# ---------------------------------------------------------------------------


class TestRequestHandler:
    """Tests for the RequestHandler routing logic using a dummy writer."""

    @pytest.mark.asyncio
    async def test_register_and_list_methods(self):
        """Registered methods appear in the methods list."""
        handler = RequestHandler()

        async def _noop(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
            return None

        handler.register("foo.bar", _noop)
        handler.register("baz.qux", _noop)
        assert "foo.bar" in handler.methods
        assert "baz.qux" in handler.methods

    @pytest.mark.asyncio
    async def test_handle_returns_response_for_known_method(self):
        """Handle returns JsonRpcResponse for a registered method."""
        handler = RequestHandler()

        async def _ping(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> str:
            return "pong"

        handler.register("test.ping", _ping)

        req = JsonRpcRequest(method="test.ping", id=1)
        writer, rsock, wsock = await _make_dummy_writer()
        try:
            result = await handler.handle(req, writer)
            assert isinstance(result, JsonRpcResponse)
            assert result.result == "pong"
            assert result.id == 1
        finally:
            writer.close()
            rsock.close()

    @pytest.mark.asyncio
    async def test_handle_unknown_method_returns_error(self):
        """Handle returns JsonRpcError for an unknown method."""
        handler = RequestHandler()

        req = JsonRpcRequest(method="unknown.method", id=1)
        writer, rsock, wsock = await _make_dummy_writer()
        try:
            result = await handler.handle(req, writer)
            assert isinstance(result, JsonRpcError)
            assert result.error.code == -32601
        finally:
            writer.close()
            rsock.close()

    @pytest.mark.asyncio
    async def test_handle_notification_for_unknown_method_returns_none(self):
        """Notifications to unknown methods are silently ignored."""
        handler = RequestHandler()

        req = JsonRpcRequest(method="unknown.method")  # id=None = notification
        writer, rsock, wsock = await _make_dummy_writer()
        try:
            result = await handler.handle(req, writer)
            assert result is None
        finally:
            writer.close()
            rsock.close()

    @pytest.mark.asyncio
    async def test_handle_daemon_error_returns_mapped_error(self):
        """DaemonError in handler maps to the correct RPC error code."""
        handler = RequestHandler()

        async def _fail(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
            raise JobSubmissionError("no such job")

        handler.register("test.fail", _fail)

        req = JsonRpcRequest(method="test.fail", id=99)
        writer, rsock, wsock = await _make_dummy_writer()
        try:
            result = await handler.handle(req, writer)
            assert isinstance(result, JsonRpcError)
            assert result.error.code == -32000  # JOB_NOT_FOUND
        finally:
            writer.close()
            rsock.close()

    @pytest.mark.asyncio
    async def test_handle_value_error_returns_invalid_params(self):
        """ValueError in handler maps to -32602."""
        handler = RequestHandler()

        async def _bad_params(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
            raise ValueError("missing required param")

        handler.register("test.bad", _bad_params)

        req = JsonRpcRequest(method="test.bad", id=10)
        writer, rsock, wsock = await _make_dummy_writer()
        try:
            result = await handler.handle(req, writer)
            assert isinstance(result, JsonRpcError)
            assert result.error.code == -32602
        finally:
            writer.close()
            rsock.close()

    @pytest.mark.asyncio
    async def test_handle_unexpected_exception_returns_internal_error(self):
        """Unexpected exceptions become -32603 internal errors."""
        handler = RequestHandler()

        async def _crash(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
            raise RuntimeError("unexpected boom")

        handler.register("test.crash", _crash)

        req = JsonRpcRequest(method="test.crash", id=77)
        writer, rsock, wsock = await _make_dummy_writer()
        try:
            result = await handler.handle(req, writer)
            assert isinstance(result, JsonRpcError)
            assert result.error.code == -32603
        finally:
            writer.close()
            rsock.close()

    @pytest.mark.asyncio
    async def test_handle_notification_suppresses_error_response(self):
        """Notifications don't return error responses even on handler failures."""
        handler = RequestHandler()

        async def _fail(_params: dict[str, Any], _writer: asyncio.StreamWriter) -> Any:
            raise JobSubmissionError("no such job")

        handler.register("test.fail", _fail)

        req = JsonRpcRequest(method="test.fail")  # id=None = notification
        writer, rsock, wsock = await _make_dummy_writer()
        try:
            result = await handler.handle(req, writer)
            assert result is None  # Notifications never get responses
        finally:
            writer.close()
            rsock.close()
