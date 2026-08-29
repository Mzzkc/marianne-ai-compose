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

    async def test_stop_is_idempotent(self) -> None:
        reg = _FakeRegistry()
        writer = CheckpointWriter(reg)  # type: ignore[arg-type]
        writer.start()
        await writer.stop()
        await writer.stop()  # no error
        assert not writer.running
