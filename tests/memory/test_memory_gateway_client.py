"""Slice 4 (UDS wave): the synchronous client against the real endpoint."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marianne.memory import (
    BackendUnreachable,
    PermissionDenied,
    SchemaViolation,
)
from marianne.memory.gateway.client import MemoryGatewayClient
from tests.memory.test_memory_gateway_endpoint import EV, Rig


def with_client(
    root: Path, body: Callable[[MemoryGatewayClient, Rig], None], **open_kw: Any,
) -> None:
    """Run the endpoint on a loop in this thread; the sync client in a worker thread."""

    async def main() -> None:
        rig = Rig(root)
        _, path = await rig.open(**open_kw)
        try:
            def work() -> None:
                with MemoryGatewayClient(path, timeout=5) as c:
                    body(c, rig)

            await asyncio.wait_for(asyncio.to_thread(work), 20)
        finally:
            await asyncio.wait_for(rig.ep.close(), 10)

    asyncio.run(main())


def test_round_trip_through_the_sync_client(short_tmp: Path) -> None:
    def body(c: MemoryGatewayClient, rig: Rig) -> None:
        fid = c.declare("client fact", provenance=EV)["fact_id"]
        assert [i["fact_id"] for i in c.recall("client")["results"]] == [fid]
        assert c.verify(fid)["fact_id"] == fid
        assert c.fetch(fid, 0, 6)["bytes"] == "client"
        c.bind(fid, "ci.lint")
        c.forget(fid, reason="done")
        assert c.call("recall", query="client", budget_seconds=3)["results"] == []

    with_client(short_tmp, body)


def test_refusals_raise_the_typed_error(short_tmp: Path) -> None:
    def body(c: MemoryGatewayClient, rig: Rig) -> None:
        with pytest.raises(PermissionDenied) as info:
            c.declare("nope", provenance=EV)
        assert info.value.subtype and info.value.code.name == "E2"
        with pytest.raises(SchemaViolation) as bad:
            c.call("unknown_tool")
        assert bad.value.subtype == "unknown_tool"
        with pytest.raises(SchemaViolation):
            c.call("recall", bogus=1)
        assert c.recall("x")["results"] == []  # still usable

    with_client(short_tmp, body, permissions=("read",))


def test_connect_to_a_missing_socket_is_typed(short_tmp: Path) -> None:
    with pytest.raises(BackendUnreachable) as info:
        MemoryGatewayClient(short_tmp / "none.sock")
    assert info.value.subtype == "connect_failed"


def test_closed_session_and_dropped_connection_are_typed(short_tmp: Path) -> None:
    async def main() -> None:
        rig = Rig(short_tmp)
        sid, path = await rig.open()

        def work() -> None:
            c = MemoryGatewayClient(path, timeout=5)
            c.recall("x")
            fut = asyncio.run_coroutine_threadsafe(rig.ep.close_session(sid), loop)
            fut.result(10)
            with pytest.raises(BackendUnreachable):
                c.recall("x")
            c.close()

        loop = asyncio.get_running_loop()
        await asyncio.wait_for(asyncio.to_thread(work), 20)
        await rig.ep.close()

    asyncio.run(main())


def test_client_times_out_on_a_silent_server(short_tmp: Path) -> None:
    path = short_tmp / "q.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    held: list[socket.socket] = []
    t = threading.Thread(target=lambda: held.append(srv.accept()[0]), daemon=True)
    t.start()
    try:
        with MemoryGatewayClient(path, timeout=0.3) as c:
            t0 = time.time()
            with pytest.raises(BackendUnreachable) as info:
                c.recall("x")
            assert info.value.subtype == "transport_failed" and time.time() - t0 < 3
    finally:
        for h in held:
            h.close()
        srv.close()
