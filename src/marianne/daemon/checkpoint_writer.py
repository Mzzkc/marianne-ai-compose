"""Ordered, acknowledged checkpoint writer (#111).

The conductor persists job state by serializing the full ``CheckpointState`` to
the daemon registry. The previous path fired each save as an unawaited
``asyncio.create_task(registry.save_checkpoint(...))`` — **unordered and
unacknowledged**. Two rapid transitions for the same job could commit out of
order, overwriting a newer full-state blob with an older one (a silent state
regression). Today that is masked (the in-memory copy serves live reads and the
shutdown flush writes the latest), but as the daemon DB becomes the sole source
of truth it is a data-loss bug.

``CheckpointWriter`` is a single-consumer, in-order writer:

- One FIFO ``asyncio.Queue`` drained by one task ⇒ writes never reorder.
- Per-job **coalescing**: only the latest snapshot for a ``job_id`` is written
  (each ``CheckpointState`` blob is a full snapshot, so a newer one supersedes
  any older queued one). This both fixes reordering and cuts write amplification
  under bursts.
- Best-effort write errors are logged, never raised — a failed persist must not
  crash the conductor loop (the next snapshot or shutdown flush re-persists).
- Explicit acknowledged writes report the exact registry save result to their
  caller; terminal transitions use this path when stale bytes are unacceptable.

It does NOT own durability-at-shutdown: the manager's existing synchronous
final-flush writes the latest in-memory state for every job before closing the
registry, which supersedes anything still queued. The writer is simply stopped
first so the final-flush is the last write.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from marianne.core.logging import get_logger

if TYPE_CHECKING:
    from marianne.daemon.registry import JobRegistry

_logger = get_logger("daemon.checkpoint_writer")

# Generous bound so the consumer keeps up in practice, while still surfacing
# backpressure (rather than letting memory run unbounded ahead of disk).
_DEFAULT_MAX_QUEUE = 512


@dataclass(frozen=True)
class _AcknowledgedWrite:
    job_id: str
    payload: str
    completed: asyncio.Future[None]


class CheckpointWriter:
    """Serialized, order-preserving writer for registry checkpoint saves."""

    def __init__(self, registry: JobRegistry, *, max_queue: int = _DEFAULT_MAX_QUEUE) -> None:
        self._registry = registry
        self._queue: asyncio.Queue[str | _AcknowledgedWrite] = asyncio.Queue(
            maxsize=max_queue,
        )
        self._latest: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._accepting = False
        self._stopping = False
        self._stop_requested = asyncio.Event()
        self._admissions_idle = asyncio.Event()
        self._admissions_idle.set()
        self._active_ack_admissions = 0
        self._stopped = asyncio.Event()
        self._stopped.set()

    def start(self) -> None:
        """Start the consumer task (idempotent). Must run inside the event loop."""
        if self._stopping:
            raise RuntimeError("checkpoint writer is stopping")
        if self._task is None:
            self._stop_requested.clear()
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="checkpoint-writer")
            self._accepting = True

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def enqueue(self, job_id: str, checkpoint_json: str) -> None:
        """Record the latest snapshot for ``job_id`` and queue it for writing.

        Non-blocking and safe to call from the conductor's event-loop callbacks.
        The latest snapshot is always recorded; a full queue logs backpressure
        (the snapshot is still written by the next enqueue for the job or the
        shutdown flush).
        """
        self._latest[job_id] = checkpoint_json
        try:
            self._queue.put_nowait(job_id)
        except asyncio.QueueFull:
            _logger.warning("checkpoint_writer.queue_full", job_id=job_id)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if isinstance(item, _AcknowledgedWrite):
                acknowledged: _AcknowledgedWrite | None = item
                job_id = item.job_id
            else:
                acknowledged = None
                job_id = item
            try:
                payload = (
                    acknowledged.payload
                    if acknowledged is not None
                    else (
                        self._latest.pop(job_id)
                        if job_id in self._latest
                        else None
                    )
                )
                if payload is not None:
                    await self._registry.save_checkpoint(job_id, payload)
                if acknowledged is not None and not acknowledged.completed.done():
                    acknowledged.completed.set_result(None)
            except asyncio.CancelledError:
                if acknowledged is not None and not acknowledged.completed.done():
                    acknowledged.completed.cancel()
                raise
            except Exception as exc:
                if acknowledged is not None:
                    if not acknowledged.completed.done():
                        acknowledged.completed.set_exception(exc)
                    _logger.error(
                        "checkpoint_writer.acknowledged_write_failed",
                        job_id=job_id,
                        exc_info=True,
                    )
                else:
                    _logger.warning(
                        "checkpoint_writer.write_failed",
                        job_id=job_id,
                        exc_info=True,
                    )
            finally:
                self._queue.task_done()

    async def write_and_wait(self, job_id: str, checkpoint_json: str) -> None:
        """Write one exact snapshot in FIFO order and report its save result."""
        if not self._accepting or not self.running:
            raise RuntimeError("checkpoint writer is not running")
        completed = asyncio.get_running_loop().create_future()
        request = _AcknowledgedWrite(
            job_id=job_id,
            payload=checkpoint_json,
            completed=completed,
        )
        self._active_ack_admissions += 1
        self._admissions_idle.clear()
        put_task = asyncio.create_task(self._queue.put(request))
        stop_task = asyncio.create_task(self._stop_requested.wait())
        try:
            done, _pending = await asyncio.wait(
                (put_task, stop_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                if not put_task.done():
                    put_task.cancel()
                if not completed.done():
                    completed.cancel()
            else:
                await put_task
        except asyncio.CancelledError:
            if not put_task.done():
                put_task.cancel()
            if not completed.done():
                completed.cancel()
            raise
        finally:
            for task in (put_task, stop_task):
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._active_ack_admissions -= 1
            if self._active_ack_admissions == 0:
                self._admissions_idle.set()
        await completed

    async def drain(self) -> None:
        """Wait until all queued writes have been processed."""
        await self._queue.join()

    async def stop(self) -> None:
        """Cancel the consumer task cleanly (idempotent).

        Pending coalesced snapshots are intentionally NOT flushed here — the
        caller's final synchronous flush of live state supersedes them.
        """
        if self._stopping:
            await self._stopped.wait()
            return
        self._stopping = True
        self._accepting = False
        self._stop_requested.set()
        try:
            # Producers that passed the accepting check join shutdown before
            # the consumer is cancelled and the visible queue is drained.
            # Therefore no acknowledged item can appear after this drain.
            await self._admissions_idle.wait()
            if self._task is not None:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
                self._task = None
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if (
                    isinstance(item, _AcknowledgedWrite)
                    and not item.completed.done()
                ):
                    item.completed.cancel()
                self._queue.task_done()
        finally:
            self._stopping = False
            self._stopped.set()
