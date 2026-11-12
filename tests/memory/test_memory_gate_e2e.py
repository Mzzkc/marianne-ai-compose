"""Slice 2: the gate's generated MCP entry drives the real shim subprocess end to end."""

from __future__ import annotations

import asyncio
import queue
import subprocess
import threading
import time
from pathlib import Path

import pytest

from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.memory_gate import MemoryDispatchGate, SheetIdentity
from tests.memory.test_memory_gate import make
from tests.memory.test_memory_shim import ShimProc, child_env, payload

EV = {"evidence_locator": "test:evidence"}


class _EntryShim(ShimProc):
    """The real shim, launched from the gate's generated command line (not a hand-built one)."""

    def __init__(self, command: str, args: list[str]) -> None:
        self.p = subprocess.Popen(
            [command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=child_env(),
        )
        self._lines: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._id = 0


@pytest.mark.timeout(90)
def test_shim_from_generated_entry_declares_recalls_then_fails_after_teardown(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        gate: MemoryDispatchGate = make(short_tmp)
        try:
            gate.register_job("job", MemoryConfig.model_validate({"backends": {
                "notes": {"system": "files", "scope": "project/demo"}}}))
            delivery = await asyncio.wait_for(
                gate.prepare("job", SheetIdentity(1, 1, "work"), "d1", time.time() + 60), 20,
            )
            assert delivery is not None
            entry = delivery.mcp_entries["memory-notes"]
            sock = Path(entry["args"][entry["args"].index("--socket") + 1])
            assert sock.exists()

            def work() -> tuple[str, ShimProc]:
                sp = _EntryShim(entry["command"], entry["args"])
                try:
                    sp.rpc("initialize", {"protocolVersion": "2025-06-18"})
                    sp.send_raw(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                    first = sp.call("declare", fact="gate works end to end", provenance=EV)
                    assert first["result"]["isError"] is False
                    fid = payload(first)["fact_id"]
                    got = payload(sp.call("recall", query="gate"))
                    assert [i["fact_id"] for i in got["results"]] == [fid]
                    return fid, sp
                except BaseException:
                    sp.close()
                    raise

            fid, sp = await asyncio.wait_for(asyncio.to_thread(work), 60)
            try:
                report = await asyncio.wait_for(gate.teardown("job", "d1"), 20)
                assert report.clean
                assert not sock.exists()

                def after() -> dict[str, object]:
                    return sp.call("recall", query="gate")

                resp = await asyncio.wait_for(asyncio.to_thread(after), 30)
                # A clean failure: a structured error, not a hang and not a crash.
                failed = "error" in resp or resp["result"]["isError"] is True
                assert failed, resp
            finally:
                await asyncio.to_thread(sp.close)
        finally:
            await asyncio.wait_for(gate.shutdown(), 20)

    asyncio.run(main())
