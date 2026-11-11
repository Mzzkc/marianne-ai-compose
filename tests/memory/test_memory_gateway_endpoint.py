"""Slice 3 (UDS wave): the Unix-socket endpoint, against the real files backend."""

from __future__ import annotations

import asyncio
import json
import stat
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from marianne.memory.backends.files import files_backend_factory, resolve_files_location
from marianne.memory.gateway import AuditLog, Gateway, verify_audit_file
from marianne.memory.gateway.endpoint import EndpointLimits, MemoryEndpoint

P = "agent:musician-1"
SCOPE = "project/demo"
EV = {"evidence_locator": "test:evidence"}
ALL = ("read", "write", "verify", "bind", "forget")
T = 10.0


class Rig:
    def __init__(self, root: Path, **ep: Any) -> None:
        self.root = root
        loc = resolve_files_location(SCOPE, root / "files")
        self.backend = files_backend_factory(
            {"attachment_id": "att-1", "scope": SCOPE, "recall_cap_bytes": 4096, **loc.as_config()}
        )
        self.gw = Gateway(audit=AuditLog(root / "audit"))
        self.gw.register_backend("att-1", self.backend)
        (root / "run").mkdir(mode=0o700)
        self.ep = MemoryEndpoint(self.gw, run_dir=root / "run", **ep)

    async def open(self, **over: Any) -> tuple[str, Path]:
        kw: dict[str, Any] = {
            "principal": P, "attachment_id": "att-1", "scope": SCOPE, "permissions": ALL,
            "dispatch_id": "d1", "deadline_at": time.time() + 60,
        }
        kw.update(over)
        s, path = await self.ep.open_session(**kw)
        return s.session_id, path

    def audit(self, sid: str, dispatch: str = "d1") -> list[dict[str, Any]]:
        p = self.root / "audit" / dispatch / f"{sid}.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()]


def run(root: Path, body: Callable[[Rig], Awaitable[None]], **ep: Any) -> None:
    async def main() -> None:
        rig = Rig(root, **ep)
        try:
            await asyncio.wait_for(body(rig), timeout=T)
        finally:
            await asyncio.wait_for(rig.ep.close(), timeout=T)

    asyncio.run(main())


class Conn:
    def __init__(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        self.r, self.w = r, w

    @classmethod
    async def to(cls, path: Path) -> Conn:
        r, w = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), T)
        return cls(r, w)

    async def raw(self, data: bytes) -> dict[str, Any] | None:
        self.w.write(data)
        await self.w.drain()
        return await self.read()

    async def read(self) -> dict[str, Any] | None:
        line = await asyncio.wait_for(self.r.readline(), T)
        return json.loads(line) if line else None

    async def call(
        self, tool: str, args: dict[str, Any] | None = None, **top: Any,
    ) -> dict[str, Any]:
        req = {"id": 1, "tool": tool, "args": args or {}, **top}
        out = await self.raw((json.dumps(req) + "\n").encode())
        assert out is not None
        return out

    async def eof(self) -> bool:
        return (await asyncio.wait_for(self.r.read(), T)) == b""

    async def close(self) -> None:
        self.w.close()
        await self.w.wait_closed()


def test_bind_modes_and_unlink_on_close(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        sid, path = await rig.open()
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        st = path.lstat()
        assert stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600
        c = await Conn.to(path)
        await c.close()
        await rig.ep.close_session(sid)
        assert not path.exists()
        with pytest.raises((FileNotFoundError, ConnectionRefusedError)):
            await asyncio.wait_for(asyncio.open_unix_connection(str(path)), T)
        kinds = [r["event"] for r in rig.audit(sid)]
        assert kinds[0] == "session_opened" and kinds[-1] == "session_closed"
        assert verify_audit_file(rig.root / "audit" / "d1" / f"{sid}.jsonl", require_sealed=True).ok

    run(short_tmp, body)


def test_round_trip_of_all_six_tools(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        _, path = await rig.open()
        c = await Conn.to(path)
        d = await c.call("declare", {"fact": "the build uses ruff", "provenance": EV})
        assert d["ok"] and d["id"] == 1
        fid = d["result"]["fact_id"]
        r = await c.call("recall", {"query": "ruff"})
        assert [i["fact_id"] for i in r["result"]["results"]] == [fid]
        v = await c.call("verify", {"fact_id_or_hash": fid})
        assert v["ok"]
        f = await c.call("fetch", {"unit_id": fid, "offset": 0, "length": 8})
        assert f["ok"] and f["result"]["bytes"] == "the buil"
        b = await c.call("bind", {"fact_id": fid, "target_symbol": "ci.lint"})
        assert b["ok"], b
        g = await c.call("forget", {"fact_id": fid, "reason": "done"}, budget_seconds=5)
        assert g["ok"], g
        gone = await c.call("recall", {"query": "ruff"})
        assert gone["ok"] and gone["result"]["results"] == []
        await c.close()

    run(short_tmp, body)


def test_oversize_line_is_refused_and_closed(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        _, path = await rig.open()
        c = await Conn.to(path)
        big = b'{"id":1,"tool":"recall","args":{"query":"' + b"x" * 5000 + b'"}}\n'
        out = await c.raw(big)
        assert out is not None and out["error"]["subtype"] == "line_too_long"
        assert out["error"]["code"] == "E7"
        assert await c.eof()

    run(short_tmp, body, limits=EndpointLimits(max_line_bytes=1024))


def test_malformed_json_is_typed_and_connection_stays_open(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        _, path = await rig.open()
        c = await Conn.to(path)
        bad = await c.raw(b"{not json\n")
        assert bad is not None and bad["ok"] is False
        assert bad["error"]["subtype"] == "malformed_json" and bad["error"]["code"] == "E7"
        assert set(bad["error"]) == {"code", "name", "subtype", "message", "remedy"}
        arr = await c.raw(b"[1]\n")
        assert arr is not None and arr["error"]["subtype"] == "malformed_request"
        unk = await c.call("nope")
        assert unk["error"]["subtype"] == "unknown_tool"
        args = await c.call("recall", {"bogus": 1})
        assert args["error"]["subtype"] == "bad_arguments"
        ok = await c.call("recall", {"query": "x"})
        assert ok["ok"]
        await c.close()

    run(short_tmp, body)


def test_client_supplied_session_principal_scope_are_ignored_and_audited(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        sid, path = await rig.open()
        other, _ = await rig.open(dispatch_id="d2", principal="agent:other")
        c = await Conn.to(path)
        await c.call("declare", {"fact": "mine", "provenance": EV})
        r = await c.call(
            "recall", {"query": "mine", "scope": "project/elsewhere", "session_id": other},
            principal="agent:evil", session=other,
        )
        assert r["ok"], r
        assert [i["content"] for i in r["result"]["results"]] == ["mine"]
        assert r["ignored_fields"] == ["principal", "scope", "session", "session_id"]
        refused = [x for x in rig.audit(sid) if x.get("result_status") == "refused"]
        assert refused and refused[0]["ignored_fields"] == r["ignored_fields"]
        assert refused[0]["principal"] == P
        assert not [x for x in rig.audit(other, "d2") if x["event"] == "operation"]
        await c.close()

    run(short_tmp, body)


def test_uid_mismatch_gets_one_e1_frame_then_close(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        _, path = await rig.open()
        c = await Conn.to(path)
        first = await c.read()
        assert first is not None and first["error"]["code"] == "E1"
        assert first["error"]["name"] == "unauthenticated"
        assert await c.eof()

    run(short_tmp, body, peer_uid_reader=lambda s: 424242, uid=1)


def test_unreadable_peer_credentials_are_refused(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        _, path = await rig.open()
        c = await Conn.to(path)
        first = await c.read()
        assert first is not None and first["error"]["code"] == "E1"
        assert await c.eof()

    run(short_tmp, body, peer_uid_reader=lambda s: None)


def test_expired_session_refuses_over_the_wire(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        _, path = await rig.open(deadline_at=time.time() + 0.3)
        c = await Conn.to(path)
        assert (await c.call("recall", {"query": "x"}))["ok"]
        await asyncio.sleep(0.5)
        out = await c.call("recall", {"query": "x"})
        assert out["ok"] is False and out["error"]["code"] == "E10"
        later = await c.call("recall", {"query": "x"})
        assert later["ok"] is False and later["error"]["code"] == "E1"  # session_closed
        await c.close()

    run(short_tmp, body)


def test_connection_limit_and_idle_timeout(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        _, path = await rig.open()
        a = await Conn.to(path)
        assert (await a.call("recall", {"query": "x"}))["ok"]
        b = await Conn.to(path)
        assert (await b.call("recall", {"query": "x"}))["ok"]
        c = await Conn.to(path)
        refused = await c.read()
        assert refused is not None and refused["error"]["subtype"] == "connection_limit"
        assert await c.eof()
        assert await a.eof()  # idle timeout closes the quiet connections
        assert await b.eof()

    run(short_tmp, body, limits=EndpointLimits(max_connections_per_session=2,
                                                idle_timeout_seconds=0.5))


def test_disconnect_during_a_slow_call_is_audited_cancelled(short_tmp: Path) -> None:
    async def body(rig: Rig) -> None:
        sid, path = await rig.open()
        real = rig.backend.recall

        def slow(*a: Any, **k: Any) -> Any:
            time.sleep(0.6)
            return real(*a, **k)

        rig.backend.recall = slow  # type: ignore[method-assign]
        c = await Conn.to(path)
        c.w.write(b'{"id":1,"tool":"recall","args":{"query":"x"}}\n')
        await c.w.drain()
        await asyncio.sleep(0.15)
        await c.close()
        for _ in range(100):
            if any(r.get("result_status") == "cancelled" for r in rig.audit(sid)):
                break
            await asyncio.sleep(0.05)
        assert any(r.get("result_status") == "cancelled" for r in rig.audit(sid))
        await asyncio.sleep(0.7)  # let the abandoned worker finish before teardown

    run(short_tmp, body)
