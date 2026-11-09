"""The pool bridge survives a catalog-sized reply and fails loudly when its reader dies (GH #520).

REA answers ``tools/list`` with one 3.5 MB newline-framed line. The bridge used
to spawn its upstream with the default 64 KiB StreamReader limit: the reader
raised, logged once and exited, the process stayed alive, ``is_running`` stayed
true, and every later client request parked in ``_pending`` forever.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.config import McpPoolConfig, McpServerEntry
from marianne.daemon.mcp_pool import McpPoolManager, McpServerState

pytestmark = pytest.mark.asyncio

_SERVER = """
import json, sys
BIG = {BIG}
DIE_ON_CALL = {DIE}
def respond(payload):
    sys.stdout.write(json.dumps(payload) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    if "id" not in req:
        continue
    m = req.get("method")
    if m == "initialize":
        info = {{"name": "fake", "version": "1"}}
        result = {{"protocolVersion": "2025-11-25", "capabilities": {{"tools": {{}}}}}}
        result["serverInfo"] = info
        respond({{"jsonrpc": "2.0", "id": req["id"], "result": result}})
    elif m == "tools/list":
        schema = {{"type": "object"}}
        tools = [{{"name": f"tool_{{i}}", "description": "x" * 200, "inputSchema": schema}}
                 for i in range(BIG)]
        respond({{"jsonrpc": "2.0", "id": req["id"], "result": {{"tools": tools}}}})
    elif m == "tools/call" and DIE_ON_CALL:
        sys.stdout.close(); sys.exit(0)
    else:
        respond({{"jsonrpc": "2.0", "id": req["id"], "result": {{}}}})
"""


def _write(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    writer.write((json.dumps(message) + "\n").encode())


async def _read(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await asyncio.wait_for(reader.readline(), 20)
    assert line, "bridge closed the connection without a reply"
    return json.loads(line)  # type: ignore[no-any-return]


async def _pool(tmp_path: Path, *, big: int, die: bool) -> tuple[McpPoolManager, Path]:
    script = tmp_path / "fake_mcp.py"
    script.write_text(_SERVER.format(BIG=big, DIE=die))
    sock = tmp_path / "fake.sock"
    manager = McpPoolManager(McpPoolConfig(servers={
        "fake": McpServerEntry(
            command=f"{sys.executable} {script}", transport="stdio", socket=str(sock),
            restart_policy="never",
        ),
    }))
    await manager.start_all()
    assert manager.is_running("fake")
    return manager, sock


async def _session(sock: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(str(sock), limit=128 * 1024 * 1024)
    _write(writer, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert (await _read(reader))["id"] == 1
    return reader, writer


async def test_catalog_over_64kib_reaches_the_client(tmp_path: Path) -> None:
    """A tools/list reply far over the old StreamReader limit is delivered intact."""
    manager, sock = await _pool(tmp_path, big=3000, die=False)  # ~700 KB line
    try:
        reader, writer = await _session(sock)
        _write(writer, {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
        reply = await _read(reader)
        assert reply["id"] == 7
        assert len(reply["result"]["tools"]) == 3000
        assert manager.is_running("fake")
        writer.close()
        await writer.wait_closed()
    finally:
        await manager.stop_all()


async def test_dead_reader_fails_pending_requests_and_marks_bridge_down(tmp_path: Path) -> None:
    """When the upstream stops answering, the waiting client gets an error, not silence,
    and the pool stops handing out the socket."""
    manager, sock = await _pool(tmp_path, big=1, die=True)
    try:
        reader, writer = await _session(sock)
        _write(writer, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                        "params": {"name": "tool_0", "arguments": {}}})
        reply = await _read(reader)
        assert reply["id"] == 9
        assert reply["error"]["code"] == -32000
        assert "fake" in reply["error"]["message"]
        # The bridge is reported down: no new config file names this socket.
        deadline = asyncio.get_running_loop().time() + 5.0
        while manager.is_running("fake") and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert not manager.is_running("fake")
        assert manager.get_status()["fake"] is McpServerState.FAILED
        assert manager.generate_mcp_config_file(tmp_path, server_names=["fake"]) is None
        writer.close()
        await writer.wait_closed()
    finally:
        await manager.stop_all()
