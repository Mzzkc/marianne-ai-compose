"""Session validity is the owner's liveness, read at call time (no lease, no renewal, no clock).

Every gate here has its reaper off unless a test says otherwise, and every clock is injected.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.memory_gate import (
    MemoryDispatchGate,
    SheetIdentity,
)
from marianne.memory.errors import MemoryBackendError
from marianne.memory.shim import SESSION_ENDED_MESSAGE, _error_payload

SHEET = SheetIdentity(num=1, movement=1, phase="work")
T = 20.0


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def two() -> MemoryConfig:
    return MemoryConfig.model_validate({"backends": {
        "notes": {"system": "files", "scope": "project/a"},
        "ledger": {"system": "files", "scope": "project/b"},
    }})


def make(
    root: Path, *, wall: Clock | None = None, mono: Clock | None = None,
    reaper: bool = False, **kw: object,
) -> MemoryDispatchGate:
    run = root / "run"
    run.mkdir(mode=0o700, exist_ok=True)
    extra: dict[str, object] = dict(kw)
    if wall is not None:
        extra["clock"] = wall
    if mono is not None:
        extra["monotonic"] = mono
    gate = MemoryDispatchGate(
        run_dir=run, memory_root=root / "files", audit_dir=root / "audit", user="ada",
        start_reaper=reaper, **extra,  # type: ignore[arg-type]
    )
    gate.register_job("j", two())
    return gate


def run(body: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(asyncio.wait_for(body(), T))


def sockets(root: Path) -> list[str]:
    d = root / "run" / "s"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


async def deliver(gate: MemoryDispatchGate, root: Path, num: int = 1) -> tuple[str, str]:
    p = await gate.deliver(
        "j", SheetIdentity(num, 1, "work"), timeout_seconds=60, existing_mcp_config=None,
        workspace=root / "ws",
    )
    assert p is not None and gate._gateway is not None
    return p.dispatch_id, gate._open[("j", p.dispatch_id)].session_ids[0]


def refused(gate: MemoryDispatchGate, sid: str) -> MemoryBackendError | None:
    assert gate._gateway is not None
    try:
        gate._gateway.sessions.admit_call(sid, "read")
    except MemoryBackendError as exc:
        return exc
    return None


def shim_message(exc: MemoryBackendError) -> str:
    return str(_error_payload(exc)["message"])


async def block_worker(gate: MemoryDispatchGate) -> threading.Event:
    """Park the single worker on a job that only ends when the test says so."""
    release = threading.Event()
    gate._submit(release.wait, 30)
    await asyncio.sleep(0)
    return release


def test_live_owner_stays_usable_while_a_worker_job_blocks_and_clocks_jump(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        wall, mono = Clock(), Clock(5000.0)
        gate = make(short_tmp, wall=wall, mono=mono)
        release = threading.Event()
        try:
            dispatch, sid = await deliver(gate, short_tmp)
            stop = asyncio.Event()
            owner = asyncio.create_task(stop.wait())
            gate.set_owner("j", dispatch, owner)
            gate._submit(release.wait, 30)  # one worker job blocked "for an arbitrary time"
            for wall_step, mono_step in ((+400, +400), (-100_000, +10**6), (+10**7, +10**7)):
                wall.now += wall_step  # suspend/resume, NTP steps in both directions
                mono.now += mono_step
                await gate.tick()
                assert refused(gate, sid) is None
                assert gate.is_open("j", dispatch)
            stop.set()
        finally:
            release.set()
            await gate.shutdown()

    run(main)


def test_the_moment_the_owner_finishes_a_call_is_refused_even_with_the_worker_blocked(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        release = threading.Event()
        try:
            dispatch, sid = await deliver(gate, short_tmp)
            owner = asyncio.create_task(asyncio.sleep(0))
            gate.set_owner("j", dispatch, owner)
            release = await block_worker(gate)  # teardown cannot run: the worker is busy
            await owner
            await asyncio.sleep(0)
            assert gate.is_open("j", dispatch)  # teardown has not run
            exc = refused(gate, sid)
            assert exc is not None and exc.subtype == "session_closed"
            assert SESSION_ENDED_MESSAGE in shim_message(exc)
        finally:
            release.set()
            await gate.shutdown()

    run(main)


def test_a_cancelled_owner_ends_the_session_the_same_way(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        try:
            dispatch, sid = await deliver(gate, short_tmp)
            owner = asyncio.create_task(asyncio.sleep(3600))
            gate.set_owner("j", dispatch, owner)
            assert refused(gate, sid) is None
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert refused(gate, sid) is not None
        finally:
            await gate.shutdown()

    run(main)


@pytest.mark.parametrize("how", ["teardown", "deregister", "shutdown"])
def test_teardown_deregister_and_shutdown_end_sessions_before_any_io_runs(
    short_tmp: Path, how: str,
) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        release = threading.Event()
        try:
            dispatch, sid = await deliver(gate, short_tmp)
            release = await block_worker(gate)
            assert refused(gate, sid) is None
            if how == "teardown":
                task = asyncio.ensure_future(gate.teardown("j", dispatch))
                await asyncio.sleep(0)
            elif how == "deregister":
                gate.deregister_job("j")  # synchronous: the flag is set before it returns
            else:
                task = asyncio.ensure_future(gate.shutdown())
                await asyncio.sleep(0)
            exc = refused(gate, sid)
            assert exc is not None and SESSION_ENDED_MESSAGE in shim_message(exc)
            assert gate._gateway is not None
            assert sockets(short_tmp)  # nothing has been closed yet: the worker is blocked
            release.set()
            if how != "deregister":
                await task
        finally:
            release.set()
            await gate.shutdown()

    run(main)


def test_with_the_reaper_never_started_a_flagged_session_still_refuses(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp, reaper=False)
        try:
            dispatch, sid = await deliver(gate, short_tmp)
            assert gate._reaper is None
            owner = asyncio.create_task(asyncio.sleep(0))
            gate.set_owner("j", dispatch, owner)
            await owner
            await asyncio.sleep(0)
            assert gate._reaper is None and refused(gate, sid) is not None
            assert sockets(short_tmp)  # cleanup is delayed, correctness is not
        finally:
            await gate.shutdown()

    run(main)


def test_reaper_pass_cleans_up_a_missed_teardown(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        try:
            dispatch, sid = await deliver(gate, short_tmp)
            owner = asyncio.create_task(asyncio.sleep(0))
            gate.set_owner("j", dispatch, owner)
            await owner  # finished; nobody called teardown
            await asyncio.sleep(0)
            assert len(sockets(short_tmp)) == 2
            await gate.tick()
            await gate.drain()
            assert not gate.is_open("j", dispatch) and sockets(short_tmp) == []
            exc = refused(gate, sid)
            assert exc is not None and SESSION_ENDED_MESSAGE in shim_message(exc)
        finally:
            await gate.shutdown()

    run(main)


def test_ownerless_dispatch_is_ended_after_the_grace(short_tmp: Path) -> None:
    async def main() -> None:
        mono = Clock(100.0)
        gate = make(short_tmp, mono=mono, ownerless_grace_seconds=50.0)
        try:
            dispatch, sid = await deliver(gate, short_tmp)
            mono.now += 49
            await gate.tick()
            assert gate.is_open("j", dispatch) and refused(gate, sid) is None
            mono.now += 2
            await gate.tick()
            await gate.drain()
            assert not gate.is_open("j", dispatch) and sockets(short_tmp) == []
            assert refused(gate, sid) is not None
        finally:
            await gate.shutdown()

    run(main)


def test_real_reaper_task_cleans_up_a_missed_teardown(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp, reaper=True, reap_interval_seconds=0.01)
        try:
            dispatch, _sid = await deliver(gate, short_tmp)
            reaper = gate._reaper
            assert reaper is not None and not reaper.done()
            owner = asyncio.create_task(asyncio.sleep(0))
            gate.set_owner("j", dispatch, owner)
            await owner
            while gate.is_open("j", dispatch) or sockets(short_tmp):  # bounded by T
                await asyncio.sleep(0.005)
            assert gate._reaper is None or gate._reaper.done() or gate.open_dispatches == 0
        finally:
            await gate.shutdown()

    run(main)


def test_shutdown_stops_the_reaper_and_does_not_count_it_as_pending(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp, reaper=True)
        await deliver(gate, short_tmp)
        reaper = gate._reaper
        assert reaper is not None and not reaper.done()
        report = await asyncio.wait_for(gate.shutdown(), 5)
        assert reaper.done() and gate._reaper is None and report.clean

    run(main)


def test_a_slow_worker_job_is_logged_with_its_kind(
    short_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marianne.daemon.baton.memory_gate as mg

    # The worker logs the warning AFTER it has published the job's result (``_Worker._loop``:
    # ``set_result`` first, then the timing read, then the log), and the gate never joins the
    # worker thread. So the awaiter can resume, the test can return and pytest can undo this
    # patch before the worker has logged anything: the warning then goes to the real logger
    # and ``seen`` is still empty (measured: 14 in 20 000 runs under no load at all). Wait for
    # the log call itself instead of racing it.
    logged = threading.Event()
    seen: list[tuple[str, dict[str, object]]] = []

    def record(event: str, **kw: object) -> None:
        seen.append((event, kw.get("extra", {})))  # type: ignore[arg-type]
        if event == "memory_gate.slow_job":
            logged.set()

    monkeypatch.setattr(mg._logger, "warning", record)

    async def main() -> None:
        gate = make(short_tmp, slow_job_seconds=0.0)  # every job counts as slow
        try:

            def marker_job() -> int:
                return 1

            await gate._run(marker_job)
            # Wait for the worker's log call; the loop must stay free for the handoff.
            deadline = asyncio.get_running_loop().time() + 5.0
            while not logged.is_set() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.001)
        finally:
            await gate.shutdown()

    run(main)
    assert logged.is_set(), "the worker never logged memory_gate.slow_job"
    assert any(e == "memory_gate.slow_job" and x.get("kind") == "marker_job" for e, x in seen)
