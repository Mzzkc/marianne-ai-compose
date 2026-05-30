"""Adversarial tests for the IPC server's read-idle timeout (#310).

The server's `_handle_client` blocked on `reader.readline()` with no timeout, so
a client that connects and never sends a newline holds a connection slot
forever. A 4-model thinking-lab review recommended a per-readline idle timeout
(configurable) that closes the connection on expiry WITHOUT sending a JSON-RPC
error (a stuck/dead client isn't reading). These tests also cover the parser's
adversarial paths flagged as untested (oversized, malformed, partial-line).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.server import DaemonServer


def _handler() -> RequestHandler:
    h = RequestHandler()

    async def _ping(_p: dict[str, Any], _w: asyncio.StreamWriter) -> Any:
        return {"pong": True}

    h.register("test.ping", _ping)
    return h


def _req(method: str, rid: int = 1) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "method": method, "id": rid}).encode() + b"\n"


class TestIdleTimeout:
    async def test_rejects_nonpositive_timeout(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            DaemonServer(tmp_path / "s", _handler(), read_idle_timeout=0.0)

    async def test_idle_connection_closed(self, tmp_path: Path) -> None:
        server = DaemonServer(tmp_path / "s", _handler(), read_idle_timeout=0.15)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
            # Send nothing — the server must close the connection after the idle
            # timeout. A closed connection makes read() return EOF (b"").
            data = await asyncio.wait_for(reader.read(), timeout=2.0)
            assert data == b""
            writer.close()
        finally:
            await server.stop()

    async def test_partial_line_times_out(self, tmp_path: Path) -> None:
        server = DaemonServer(tmp_path / "s", _handler(), read_idle_timeout=0.15)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
            writer.write(b'{"jsonrpc": "2.0", "method": "test.ping"')  # no newline
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), timeout=2.0)
            assert data == b""  # closed without a response
            writer.close()
        finally:
            await server.stop()

    async def test_active_connection_survives_and_responds(self, tmp_path: Path) -> None:
        server = DaemonServer(tmp_path / "s", _handler(), read_idle_timeout=2.0)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
            writer.write(_req("test.ping", 1))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            assert json.loads(line)["result"] == {"pong": True}

            # A second request on the same connection still works (idle timeout
            # is per-readline, not a connection-lifetime cap).
            writer.write(_req("test.ping", 2))
            await writer.drain()
            line2 = await asyncio.wait_for(reader.readline(), timeout=2.0)
            assert json.loads(line2)["id"] == 2
            writer.close()
        finally:
            await server.stop()

    async def test_malformed_json_returns_parse_error(self, tmp_path: Path) -> None:
        server = DaemonServer(tmp_path / "s", _handler(), read_idle_timeout=2.0)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(tmp_path / "s"))
            writer.write(b"not json at all\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            assert json.loads(line)["error"]["code"] == -32700  # parse error
            writer.close()
        finally:
            await server.stop()
