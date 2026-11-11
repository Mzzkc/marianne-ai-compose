"""Second security review: in-flight accounting, atomic quota reservation, bounded executor."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from marianne.memory import BackendUnreachable, RecallCapExceeded
from marianne.memory.gateway import Gateway, MemoryCaps
from marianne.memory.gateway.endpoint import MAX_GATEWAY_WORKERS, EndpointLimits
from tests.memory.test_memory_gateway_endpoint import SCOPE, Conn, Rig, T, run

REQ = b'{"id":1,"tool":"recall","args":{"query":"x"}}\n'


def blocker(rig: Rig, gate: threading.Event) -> dict[str, int]:
    state = {"in": 0, "max": 0, "entered": 0}
    lock = threading.Lock()
    real = rig.backend.recall

    def slow(*a: Any, **k: Any) -> Any:
        with lock:
            state["in"] += 1
            state["entered"] += 1
            state["max"] = max(state["max"], state["in"])
        try:
            gate.wait(10)
            return real(*a, **k)
        finally:
            with lock:
                state["in"] -= 1

    rig.backend.recall = slow  # type: ignore[method-assign]
    return state


async def until(pred: Callable[[], bool], timeout: float = T) -> None:
    end = time.time() + timeout
    while not pred():
        assert time.time() < end, "condition not reached"
        await asyncio.sleep(0.02)


def test_abandoned_calls_keep_counting_against_the_in_flight_limit(short_tmp: Any) -> None:
    async def body(rig: Rig) -> None:
        gate = threading.Event()
        state = blocker(rig, gate)
        try:
            _, path = await rig.open()
            for _ in range(6):  # connect, fire, disconnect, in a loop
                c = await Conn.to(path)
                c.w.write(REQ)
                await c.w.drain()
                await asyncio.sleep(0.05)
                await c.close()
            await until(lambda: state["entered"] >= 2)
            await asyncio.sleep(0.2)
            assert state["entered"] == 2 and state["max"] == 2
            c = await Conn.to(path)
            out = await c.call("recall", {"query": "x"})
            assert out["ok"] is False and out["error"]["code"] == "E5"
            assert out["error"]["subtype"] == "in_flight_limit"
            assert state["entered"] == 2
            gate.set()
            ok: dict[str, Any] = {}
            end = time.time() + T
            while not ok.get("ok") and time.time() < end:
                ok = await c.call("recall", {"query": "x"})
                await asyncio.sleep(0.02)
            assert ok.get("ok") is True
            assert state["max"] <= 2
            await c.close()
        finally:
            gate.set()

    run(short_tmp, body, limits=EndpointLimits(max_in_flight_per_session=2))


def test_disconnect_audits_cancelled_and_real_outcome_without_double_count(
    short_tmp: Any,
) -> None:
    async def body(rig: Rig) -> None:
        gate = threading.Event()
        state = blocker(rig, gate)
        try:
            sid, path = await rig.open()
            c = await Conn.to(path)
            c.w.write(REQ)
            await c.w.drain()
            await until(lambda: state["entered"] == 1)
            await c.close()
            await until(lambda: any(r.get("result_status") == "cancelled" for r in rig.audit(sid)))
            gate.set()
            await until(lambda: any(
                r["event"] == "operation" and r.get("result_status") == "ok"
                for r in rig.audit(sid)
            ))
            statuses = [r.get("result_status") for r in rig.audit(sid) if r["event"] == "operation"]
            assert sorted(statuses) == ["cancelled", "ok"]
            assert rig.gw.sessions.get(sid).call_count == 1
        finally:
            gate.set()

    run(short_tmp, body)


def test_close_waits_for_in_flight_then_reports(short_tmp: Any) -> None:
    async def body(rig: Rig) -> None:
        gate = threading.Event()
        state = blocker(rig, gate)
        try:
            sid, path = await rig.open()
            c = await Conn.to(path)
            c.w.write(REQ)
            await c.w.drain()
            await until(lambda: state["entered"] == 1)
            await c.close()
            left = await rig.ep.close_session(sid)
            assert left == 1 and not path.exists()
            assert any(r.get("result_status") == "in_flight_at_close" for r in rig.audit(sid))
        finally:
            gate.set()
        await until(lambda: state["in"] == 0)

    run(short_tmp, body, limits=EndpointLimits(close_wait_seconds=0.3))


def test_close_returns_clean_when_the_call_finishes_inside_the_wait(short_tmp: Any) -> None:
    async def body(rig: Rig) -> None:
        gate = threading.Event()
        state = blocker(rig, gate)
        try:
            sid, path = await rig.open()
            c = await Conn.to(path)
            c.w.write(REQ)
            await c.w.drain()
            await until(lambda: state["entered"] == 1)
            await c.close()
            threading.Timer(0.2, gate.set).start()
            t0 = time.time()
            assert await rig.ep.close_session(sid) == 0
            assert time.time() - t0 >= 0.15 and not path.exists()
        finally:
            gate.set()

    run(short_tmp, body, limits=EndpointLimits(close_wait_seconds=5.0))


def test_endpoint_owns_a_bounded_executor_shut_down_on_close(short_tmp: Any) -> None:
    async def body(rig: Rig) -> None:
        assert rig.ep.max_workers == MAX_GATEWAY_WORKERS
        await rig.open()
        await rig.ep.close()
        with pytest.raises(RuntimeError):
            rig.ep._executor.submit(lambda: 0)

    run(short_tmp, body)


# ------------------------------------------------------------ atomic quota (in-process)


class Sized:
    """Returns 40-byte facts up to the cap it is given; optionally rendezvous first."""

    def __init__(self, barrier: threading.Barrier | None = None, fail: bool = False) -> None:
        self.barrier, self.fail = barrier, fail

    def recall(self, ctx: Any, query: str, scope: str, limit: int = 100,
               cursor: str | None = None, cap_bytes: int | None = None) -> Any:
        if self.fail:
            raise RuntimeError("boom")
        if self.barrier is not None:
            try:
                self.barrier.wait(3)
            except threading.BrokenBarrierError:
                pass
        room = 10**9 if cap_bytes is None else cap_bytes
        items: list[Any] = []
        while (len(items) + 1) * 40 <= room and len(items) < 5:
            items.append({
                "fact_id": f"f{len(items)}", "content": "x" * 40, "score": 1.0, "tier": "recent",
                "asserted_at": 0.0, "evidence_locator": "e", "status": "live",
            })
        return {"results": items, "cursor": None, "oversized": [], "explaining_path": None}


def open_quota(backend: Any) -> tuple[Gateway, str]:
    g = Gateway(require_audit=False)
    g.register_backend("att-1", backend)
    s = g.open_session(
        principal="agent:musician-1", attachment_id="att-1", scope=SCOPE, permissions=("read",),
        dispatch_id="d1", deadline_at=time.time() + 60,
        caps=MemoryCaps(recall_cap_bytes=100, dispatch_recall_quota_bytes=150),
    )
    return g, s.session_id


def test_concurrent_recalls_never_exceed_the_quota() -> None:
    g, sid = open_quota(Sized(threading.Barrier(2)))
    got: list[int] = []

    def go() -> None:
        try:
            r = g.recall(sid, "x")
            got.append(sum(len(i["content"]) for i in r["results"]))
        except RecallCapExceeded:
            got.append(0)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
        assert not t.is_alive()
    s = g.sessions.get(sid)
    assert sum(got) <= 150 and s.recall_bytes_used == sum(got) > 0
    assert s.recall_reserved == 0


def test_failed_call_returns_its_whole_reservation() -> None:
    g, sid = open_quota(Sized(fail=True))
    with pytest.raises(BackendUnreachable):
        g.recall(sid, "x")
    s = g.sessions.get(sid)
    assert s.recall_reserved == 0 and s.recall_bytes_used == 0
    g.register_backend("att-1", Sized())
    assert len(g.recall(sid, "x")["results"]) == 2  # the full 100-byte cap is available again


def test_unused_part_of_the_reservation_is_returned() -> None:
    g, sid = open_quota(Sized())
    g.recall(sid, "x")  # reserved 100, used 80
    s = g.sessions.get(sid)
    assert s.recall_reserved == 0 and s.recall_bytes_used == 80
    assert len(g.recall(sid, "x")["results"]) == 1  # 70 left, one 40-byte fact
    assert g.recall(sid, "x")["results"] == []  # 30 left, nothing fits: an empty page
    assert g.sessions.get(sid).recall_bytes_used == 120
