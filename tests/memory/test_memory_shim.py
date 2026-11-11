"""The memory shim: stdio MCP in front of a real gateway endpoint (subprocess, end to end)."""

from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marianne.memory.shim import MAX_LINE_BYTES, MemoryShim, main, serve
from tests.memory.test_memory_gateway_endpoint import EV, Rig

T = 15.0
SRC = str(Path(__file__).resolve().parents[2] / "src")
ROOT = str(Path(__file__).resolve().parents[2])


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([SRC, ROOT])
    return env


class ShimProc:
    def __init__(self, path: Path, budget: float | None = None) -> None:
        argv = [sys.executable, "-m", "marianne.memory.shim", "--socket", str(path)]
        if budget is not None:
            argv += ["--memory-budget-seconds", str(budget)]
        self.p = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=child_env(),
        )
        self._lines: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._id = 0

    def _pump(self) -> None:
        assert self.p.stdout is not None
        for line in self.p.stdout:
            self._lines.put(line)
        self._lines.put(b"")

    def send_raw(self, data: bytes) -> None:
        assert self.p.stdin is not None
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def recv(self) -> dict[str, Any]:
        line = self._lines.get(timeout=T)
        assert line, "shim closed stdout"
        out: dict[str, Any] = json.loads(line)
        return out

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        self.send_raw(json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        ).encode() + b"\n")
        return self.recv()

    def call(self, name: str, **args: Any) -> dict[str, Any]:
        return self.rpc("tools/call", {"name": name, "arguments": args})

    def close(self) -> None:
        if self.p.stdin:
            self.p.stdin.close()
        try:
            self.p.wait(timeout=T)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        for f in (self.p.stdout, self.p.stderr):
            if f:
                f.close()


def with_shim(
    root: Path, body: Callable[[ShimProc, Rig, str, Path], None], *,
    budget: float | None = None, **open_kw: Any,
) -> None:
    async def main_() -> None:
        rig = Rig(root)
        sid, path = await rig.open(**open_kw)

        def work() -> None:
            sp = ShimProc(path, budget)
            try:
                sp.rpc("initialize", {"protocolVersion": "2025-06-18"})
                sp.send_raw(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                body(sp, rig, sid, path)
            finally:
                sp.close()

        try:
            await asyncio.wait_for(asyncio.to_thread(work), 60)
        finally:
            await asyncio.wait_for(rig.ep.close(), 10)

    asyncio.run(main_())


def payload(resp: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = resp["result"]["structuredContent"]
    return out


@pytest.mark.timeout(60)
def test_initialize_and_exactly_six_tools(short_tmp: Path) -> None:
    def body(sp: ShimProc, rig: Rig, sid: str, path: Path) -> None:
        assert sp.rpc("ping")["result"] == {}
        tools = sp.rpc("tools/list")["result"]["tools"]
        assert [t["name"] for t in tools] == [
            "declare", "recall", "verify", "bind", "forget", "fetch",
        ]
        text = json.dumps(tools).lower()
        for term in ("session", "principal", "scope", "credential", "grant", "token", "permission"):
            assert term not in text, term

    with_shim(short_tmp, body)


def test_initialize_reply_shape(short_tmp: Path) -> None:
    out = io.BytesIO()
    serve(MemoryShim(short_tmp / "x.sock"), io.BytesIO(
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}\n'
    ), out)
    res = json.loads(out.getvalue())["result"]
    assert res["protocolVersion"] == "2024-11-05" and "tools" in res["capabilities"]


@pytest.mark.timeout(60)
def test_declare_then_recall_and_no_identity_in_argv_or_env(short_tmp: Path) -> None:
    def body(sp: ShimProc, rig: Rig, sid: str, path: Path) -> None:
        r = sp.call("declare", fact="the shim works", provenance=EV)
        assert r["result"]["isError"] is False
        fid = payload(r)["fact_id"]
        got = payload(sp.call("recall", query="shim"))
        assert [i["fact_id"] for i in got["results"]] == [fid]
        cmdline = (Path("/proc") / str(sp.p.pid) / "cmdline").read_bytes().decode()
        environ = (Path("/proc") / str(sp.p.pid) / "environ").read_bytes().decode()
        assert sid not in cmdline and sid not in environ
        assert "agent:musician-1" not in cmdline + environ

    with_shim(short_tmp, body)


@pytest.mark.timeout(60)
def test_refusal_is_a_tool_error_with_code_subtype_message_remedy(short_tmp: Path) -> None:
    def body(sp: ShimProc, rig: Rig, sid: str, path: Path) -> None:
        r = sp.call("declare", fact="no write", provenance=EV)
        assert "error" not in r and r["result"]["isError"] is True
        err = payload(r)
        assert err["code"] == "E2" and err["subtype"] and "message" in err and "remedy" in err
        assert payload(sp.call("recall", query="x"))["results"] == []  # still usable

    with_shim(short_tmp, body, permissions=("read",))


@pytest.mark.timeout(60)
def test_protocol_faults_are_jsonrpc_errors(short_tmp: Path) -> None:
    def body(sp: ShimProc, rig: Rig, sid: str, path: Path) -> None:
        assert sp.rpc("nope")["error"]["code"] == -32601
        assert sp.call("not_a_tool")["error"]["code"] == -32602
        assert sp.call("recall")["error"]["code"] == -32602  # missing query
        # identity smuggled as an argument is a protocol fault, not forwarded
        assert sp.call("recall", query="x", session_id="s")["error"]["code"] == -32602
        assert sp.call("recall", query="x", scope="project/other")["error"]["code"] == -32602
        sp.send_raw(b"{not json\n")
        assert sp.recv()["error"]["code"] == -32700
        sp.send_raw(b"[1]\n")
        assert sp.recv()["error"]["code"] == -32600
        assert sp.rpc("ping")["result"] == {}

    with_shim(short_tmp, body)


@pytest.mark.timeout(60)
def test_oversized_line_refused_and_stream_recovers(short_tmp: Path) -> None:
    def body(sp: ShimProc, rig: Rig, sid: str, path: Path) -> None:
        sp.send_raw(b"x" * (MAX_LINE_BYTES * 3) + b"\n")
        assert sp.recv()["error"]["code"] == -32600
        assert sp.rpc("ping")["result"] == {}

    with_shim(short_tmp, body)


def test_oversized_line_bounded_in_process() -> None:
    out = io.BytesIO()
    data = b"y" * 5000 + b"\n" + b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    serve(MemoryShim("/nonexistent"), io.BytesIO(data), out, max_line_bytes=100)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32600 and lines[1]["result"] == {}


@pytest.mark.timeout(60)
def test_socket_closed_mid_session_gives_typed_error_per_call(short_tmp: Path) -> None:
    async def main_() -> None:
        rig = Rig(short_tmp)
        sid, path = await rig.open()

        def first() -> ShimProc:
            sp = ShimProc(path)
            sp.rpc("initialize")
            assert payload(sp.call("recall", query="x"))["results"] == []
            return sp

        sp = await asyncio.wait_for(asyncio.to_thread(first), 30)
        try:
            await asyncio.wait_for(rig.ep.close_session(sid), 10)

            def after() -> None:
                for _ in range(2):
                    r = sp.call("recall", query="x")
                    assert r["result"]["isError"] is True
                    assert payload(r)["code"] in {"E1", "E5"}
                assert sp.p.poll() is None  # stays up, per-call typed errors

            await asyncio.wait_for(asyncio.to_thread(after), 30)
        finally:
            await asyncio.to_thread(sp.close)
            await asyncio.wait_for(rig.ep.close(), 10)

    asyncio.run(main_())


@pytest.mark.timeout(60)
def test_socket_gone_at_startup_exits_nonzero_one_stderr_line(short_tmp: Path) -> None:
    p = subprocess.run(
        [sys.executable, "-m", "marianne.memory.shim", "--socket", str(short_tmp / "none.sock")],
        input=b"", capture_output=True, env=child_env(), timeout=T,
    )
    assert p.returncode != 0 and p.stdout == b""
    assert len(p.stderr.decode().strip().splitlines()) == 1


@pytest.mark.timeout(60)
def test_unanswering_gateway_times_out_within_budget(short_tmp: Path) -> None:
    path = short_tmp / "hang.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(8)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])
            except OSError:
                continue

    threading.Thread(target=accept, daemon=True).start()
    sp = ShimProc(path, budget=0.5)
    try:
        sp.rpc("initialize")
        import time
        t0 = time.monotonic()
        r = sp.call("recall", query="x")
        elapsed = time.monotonic() - t0
        assert r["result"]["isError"] is True and payload(r)["code"] == "E5"
        assert elapsed < 5.0
    finally:
        sp.close()
        stop.set()
        srv.close()
        for c in held:
            c.close()


def test_bad_budget_is_refused() -> None:
    errs: list[str] = []
    assert main(["--socket", "/x", "--memory-budget-seconds", "0"], stderr=errs.append) == 2
    assert main(["--socket", "/x", "--memory-budget-seconds", "nan"], stderr=errs.append) == 2
