"""Submitting after shutdown, and a job that is stuck when shutdown gives up (S3)."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.baton.memory_gate import MemoryGateError

from .test_memory_gate_liveness import deliver, make, refused, run


def _no_unretrieved(loop_errors: list[dict[str, Any]]) -> asyncio.AbstractEventLoop:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _l, ctx: loop_errors.append(ctx))
    return loop


def test_submit_after_shutdown_is_a_failed_future_not_a_raise_and_leaks_nothing(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        errors: list[dict[str, Any]] = []
        _no_unretrieved(errors)
        gate = make(short_tmp)
        await deliver(gate, short_tmp)
        await gate.shutdown()
        fut = gate._submit(lambda: 1)  # must not raise into a callback
        assert fut.done() and isinstance(fut.exception(), MemoryGateError)
        assert "shut down" in str(fut.exception())
        with pytest.raises(MemoryGateError):
            await gate._run(lambda: 1)
        assert not gate._pending
        await asyncio.wait_for(gate.drain(), 2)
        del fut
        await asyncio.sleep(0)
        assert errors == []

    run(main)


def test_stuck_job_at_shutdown_then_teardown_and_deregister_clean_up_inline(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        errors: list[dict[str, Any]] = []
        _no_unretrieved(errors)
        gate = make(short_tmp, shutdown_bound_seconds=0.05)
        release = threading.Event()
        try:
            d1, s1 = await deliver(gate, short_tmp, 1)
            d2, s2 = await deliver(gate, short_tmp, 2)
            dirs = [gate._open[("j", d)].dir for d in (d1, d2)]
            assert all(d is not None and d.exists() for d in dirs)
            gate._submit(release.wait, 30)  # stuck
            report = await gate.shutdown()
            assert report.errors  # shutdown said what it could not finish
            assert all(d is not None and not d.exists() for d in dirs)  # removed inline
            # Late callers: neither hangs nor raises, and nothing is left to retrieve.
            assert await asyncio.wait_for(gate.teardown("j", d1), 2) is not None
            gate.deregister_job("j")
            await asyncio.wait_for(gate.drain(), 2)
            assert gate.open_dispatches == 0 and not gate._pending
            assert refused(gate, s1) is not None and refused(gate, s2) is not None
        finally:
            release.set()
        await asyncio.sleep(0.05)
        assert errors == []

    run(main)


def test_worker_thread_is_a_daemon_so_a_stuck_job_cannot_hold_the_interpreter(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        release = threading.Event()
        try:
            await deliver(gate, short_tmp)
            gate._submit(release.wait, 30)
            names = {t.name: t.daemon for t in threading.enumerate() if "memory-gate" in t.name}
            assert names and all(names.values()), names
        finally:
            release.set()
            await gate.shutdown()

    run(main)


def test_a_job_finishing_after_shutdown_raises_nothing_in_the_worker(short_tmp: Path) -> None:
    async def main() -> None:
        seen: list[Any] = []
        old = threading.excepthook
        threading.excepthook = lambda args: seen.append(args)
        gate = make(short_tmp, shutdown_bound_seconds=0.05)
        release = threading.Event()
        try:
            await deliver(gate, short_tmp)
            gate._submit(release.wait, 30)
            await gate.shutdown()
            release.set()
            await asyncio.sleep(0.2)  # the worker unblocks and finishes the queue
            assert seen == []
        finally:
            release.set()
            threading.excepthook = old

    run(main)
