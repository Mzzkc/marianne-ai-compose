"""#111: the ordered, acknowledged CheckpointWriter.

Pins the properties that make the daemon DB safe as the sole source of truth:
writes for a job never reorder (no older full-snapshot overwrites a newer one),
bursts coalesce to the latest snapshot, a failing write doesn't kill the writer,
and stop() cancels cleanly.
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.daemon.checkpoint_writer import CheckpointWriter


class _FakeRegistry:
    def __init__(self) -> None:
        self.saves: list[tuple[str, str]] = []

    async def save_checkpoint(self, job_id: str, payload: str) -> None:
        self.saves.append((job_id, payload))


class _GatedRegistry:
    """Blocks the FIRST save until released, so a burst can queue behind it."""

    def __init__(self) -> None:
        self.saves: list[tuple[str, str]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._gated_first = True

    async def save_checkpoint(self, job_id: str, payload: str) -> None:
        if self._gated_first:
            self._gated_first = False
            self.entered.set()
            await self.release.wait()
        self.saves.append((job_id, payload))


class _FailingOnceRegistry:
    def __init__(self) -> None:
        self.saves: list[tuple[str, str]] = []
        self._failed = False

    async def save_checkpoint(self, job_id: str, payload: str) -> None:
        if not self._failed:
            self._failed = True
            raise RuntimeError("disk go boom")
        self.saves.append((job_id, payload))


class _HeldRegistry:
    """Hold selected saves behind explicit events for lifecycle interleavings."""

    def __init__(self, *, hold_job_id: str) -> None:
        self.hold_job_id = hold_job_id
        self.saves: list[tuple[str, str]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def save_checkpoint(self, job_id: str, payload: str) -> None:
        self.saves.append((job_id, payload))
        if job_id == self.hold_job_id:
            self.entered.set()
            await self.release.wait()


class TestCheckpointWriter:
    async def test_write_happens(self) -> None:
        reg = _FakeRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        writer.enqueue("j1", "v1")
        await writer.drain()
        await writer.stop()
        assert reg.saves == [("j1", "v1")]

    async def test_final_state_is_latest(self) -> None:
        reg = _FakeRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        for v in ("v1", "v2", "v3"):
            writer.enqueue("j1", v)
        await writer.drain()
        await writer.stop()
        # Whatever coalescing happened, the DB ends at the newest snapshot and
        # never regresses to an older one.
        assert reg.saves[-1] == ("j1", "v3")
        assert reg.saves == sorted(reg.saves, key=lambda s: int(s[1][1:]))

    async def test_burst_coalesces_to_latest(self) -> None:
        reg = _GatedRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        writer.enqueue("j1", "v1")  # consumer picks this up and blocks in save
        await asyncio.wait_for(reg.entered.wait(), timeout=2.0)
        writer.enqueue("j1", "v2")  # queue behind the blocked write
        writer.enqueue("j1", "v3")
        reg.release.set()
        await writer.drain()
        await writer.stop()
        # v1 (already in-flight) then the coalesced latest v3; v2 dropped.
        assert reg.saves == [("j1", "v1"), ("j1", "v3")]

    async def test_cross_job_fifo_order(self) -> None:
        reg = _FakeRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        writer.enqueue("a", "1")
        writer.enqueue("b", "1")
        writer.enqueue("c", "1")
        await writer.drain()
        await writer.stop()
        assert [j for j, _ in reg.saves] == ["a", "b", "c"]

    async def test_write_failure_does_not_kill_writer(self) -> None:
        reg = _FailingOnceRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        writer.enqueue("j1", "v1")  # this save raises
        await writer.drain()
        assert writer.running  # survived the failure
        writer.enqueue("j2", "v2")  # subsequent writes still work
        await writer.drain()
        await writer.stop()
        assert ("j2", "v2") in reg.saves

    async def test_acknowledged_write_surfaces_registry_failure(self) -> None:
        reg = _FailingOnceRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()

        try:
            with pytest.raises(RuntimeError, match="disk go boom"):
                await writer.write_and_wait("j1", "terminal")
            assert writer.running
        finally:
            await writer.stop()

    async def test_stop_cancels_acknowledged_producer_blocked_by_full_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = _HeldRegistry(hold_job_id="first")
        writer = CheckpointWriter(reg, max_queue=1)  # type: ignore[arg-type]
        writer.start()
        writer.enqueue("first", "one")
        await reg.entered.wait()
        writer.enqueue("queued", "two")
        assert writer._queue.full()

        admission_entered = asyncio.Event()
        original_put = writer._queue.put

        async def observed_put(item: object) -> None:
            admission_entered.set()
            await original_put(item)  # type: ignore[arg-type]

        monkeypatch.setattr(writer._queue, "put", observed_put)
        acknowledged = asyncio.create_task(
            writer.write_and_wait("terminal", "three"),
        )
        await admission_entered.wait()

        await writer.stop()
        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(0.5):
                await acknowledged
        assert writer._queue.empty()
        assert not writer.running
        reg.release.set()

    async def test_stop_cancels_acknowledged_save_already_in_flight(self) -> None:
        reg = _HeldRegistry(hold_job_id="terminal")
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        acknowledged = asyncio.create_task(
            writer.write_and_wait("terminal", "payload"),
        )
        await reg.entered.wait()

        await writer.stop()
        with pytest.raises(asyncio.CancelledError):
            await acknowledged
        assert writer._queue.empty()
        assert not writer.running
        reg.release.set()

    async def test_simultaneous_put_and_stop_readiness_settles_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = _HeldRegistry(hold_job_id="first")
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        writer.enqueue("first", "one")
        await reg.entered.wait()
        original_put = writer._queue.put
        stop_tasks: list[asyncio.Task[None]] = []

        async def put_then_begin_stop(item: object) -> None:
            await original_put(item)  # type: ignore[arg-type]
            stop_tasks.append(asyncio.create_task(writer.stop()))
            await writer._stop_requested.wait()

        monkeypatch.setattr(writer._queue, "put", put_then_begin_stop)
        acknowledged = asyncio.create_task(
            writer.write_and_wait("terminal", "two"),
        )

        with pytest.raises(asyncio.CancelledError):
            await acknowledged
        assert len(stop_tasks) == 1
        await stop_tasks[0]
        assert writer._queue.empty()
        assert not writer.running
        reg.release.set()

    async def test_caller_cancellation_before_acknowledged_admission(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = _HeldRegistry(hold_job_id="first")
        writer = CheckpointWriter(reg, max_queue=1)  # type: ignore[arg-type]
        writer.start()
        writer.enqueue("first", "one")
        await reg.entered.wait()
        writer.enqueue("queued", "two")

        admission_entered = asyncio.Event()
        original_put = writer._queue.put

        async def observed_put(item: object) -> None:
            admission_entered.set()
            await original_put(item)  # type: ignore[arg-type]

        monkeypatch.setattr(writer._queue, "put", observed_put)
        acknowledged = asyncio.create_task(
            writer.write_and_wait("cancelled", "three"),
        )
        await admission_entered.wait()
        acknowledged.cancel()
        with pytest.raises(asyncio.CancelledError):
            await acknowledged

        reg.release.set()
        await writer.drain()
        await writer.stop()
        assert ("cancelled", "three") not in reg.saves
        assert writer._queue.empty()

    async def test_caller_cancellation_after_acknowledged_admission(self) -> None:
        reg = _HeldRegistry(hold_job_id="terminal")
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        acknowledged = asyncio.create_task(
            writer.write_and_wait("terminal", "payload"),
        )
        await reg.entered.wait()

        acknowledged.cancel()
        with pytest.raises(asyncio.CancelledError):
            await acknowledged
        reg.release.set()
        await writer.drain()
        assert writer.running
        assert reg.saves == [("terminal", "payload")]
        await writer.stop()

    async def test_acknowledged_writer_restarts_after_stop(self) -> None:
        reg = _FakeRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        await writer.write_and_wait("first", "one")
        await writer.stop()

        writer.start()
        await writer.write_and_wait("second", "two")
        await writer.stop()

        assert reg.saves == [("first", "one"), ("second", "two")]

    async def test_stop_is_idempotent(self) -> None:
        reg = _FakeRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        await writer.stop()
        await writer.stop()  # no error
        assert not writer.running
