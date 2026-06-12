"""Live per-sheet output streaming (#352 increment 3).

Instrument drain chunks → line-buffered redaction → bounded per-sheet
ring → fan-out to IPC subscribers (``job.output.stream`` → ``mzt watch``).

Design (4-model lab, ~/lab-archives/2026-06-11-streaming-352):
- Redaction happens ONCE, at entry, on COMPLETE lines — chunk-boundary
  splits are the #1 redaction hazard, so partial lines are buffered
  until their newline arrives (or a size flush forces them out).
- Each (job, sheet) holds a ~256KB drop-oldest ring. Ephemeral by
  design: a conductor restart loses the ring; ``SheetState.stdout_tail``
  remains the persisted evidence.
- Subscribers get bounded queues with drop-on-backpressure — a slow
  ``mzt watch`` reader must NEVER block or freeze the conductor.
  Dropped output is surfaced as a ``[dropped ...]`` marker, not hidden.
- The writer callback is synchronous and runs on the daemon loop (the
  drain awaits between chunks), so no locking is needed.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable

from marianne.utils.credential_scanner import redact_credentials

_logger = logging.getLogger("daemon.output_hub")

# Ring capacity per sheet (bytes of line text). 15 chatty sheets ≈ 4MB.
DEFAULT_RING_CAPACITY = 256 * 1024

# A partial line longer than this is force-flushed — protects the line
# buffer against output that never emits a newline.
MAX_PARTIAL_LINE = 8 * 1024

# Per-subscriber queue depth. Overflow drops lines (marker surfaces it).
SUBSCRIBER_QUEUE_SIZE = 500


class OutputSubscriber:
    """One ``mzt watch`` client: a bounded queue + drop accounting."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE
        )
        self.dropped = 0

    def offer(self, line: str) -> None:
        """Enqueue without ever blocking; count drops on backpressure."""
        if self.dropped:
            # Try to surface the marker first so ordering reads naturally.
            try:
                self.queue.put_nowait(f"[dropped {self.dropped} lines]")
                self.dropped = 0
            except asyncio.QueueFull:
                self.dropped += 1
                return
        try:
            self.queue.put_nowait(line)
        except asyncio.QueueFull:
            self.dropped += 1


class SheetOutputBuffer:
    """Line assembly + redaction + drop-oldest ring for one (job, sheet)."""

    def __init__(self, capacity_bytes: int = DEFAULT_RING_CAPACITY) -> None:
        self._capacity = capacity_bytes
        self._lines: deque[str] = deque()
        self._size = 0
        self._dropped_lines = 0
        # Per-stream partial-line buffers (stdout and stderr interleave).
        self._partials: dict[str, bytes] = {}

    def feed(self, stream: str, chunk: bytes) -> list[str]:
        """Absorb a raw chunk; return the COMPLETE lines it produced.

        Lines are decoded (errors="replace"), redacted once, stderr-tagged,
        and appended to the ring before being returned for fan-out.
        """
        data = self._partials.get(stream, b"") + chunk
        *complete, rest = data.split(b"\n")
        if len(rest) > MAX_PARTIAL_LINE:
            complete.append(rest)
            rest = b""
        self._partials[stream] = rest

        out: list[str] = []
        for raw in complete:
            text = raw.decode("utf-8", errors="replace")
            text = redact_credentials(text)
            if stream == "stderr":
                text = f"[stderr] {text}"
            self._append(text)
            out.append(text)
        return out

    def flush(self) -> list[str]:
        """Flush any unterminated partial lines (end of execution)."""
        out: list[str] = []
        for stream, rest in list(self._partials.items()):
            if rest:
                out.extend(self.feed(stream, b"\n"))
        self._partials.clear()
        return out

    def _append(self, line: str) -> None:
        self._lines.append(line)
        self._size += len(line)
        while self._size > self._capacity and len(self._lines) > 1:
            evicted = self._lines.popleft()
            self._size -= len(evicted)
            self._dropped_lines += 1

    def snapshot(self) -> list[str]:
        """The retained lines, prefixed with an eviction marker if any."""
        lines = list(self._lines)
        if self._dropped_lines:
            lines.insert(0, f"[dropped {self._dropped_lines} earlier lines]")
        return lines


class OutputStreamHub:
    """Daemon-wide registry of per-sheet output buffers and subscribers."""

    def __init__(self, ring_capacity: int = DEFAULT_RING_CAPACITY) -> None:
        self._ring_capacity = ring_capacity
        self._buffers: dict[tuple[str, int], SheetOutputBuffer] = {}
        self._subscribers: dict[tuple[str, int], set[OutputSubscriber]] = {}
        # Job-wide subscribers receive every sheet's lines, sheet-tagged.
        self._job_subscribers: dict[str, set[OutputSubscriber]] = {}

    def make_writer(
        self, job_id: str, sheet_num: int
    ) -> Callable[[str, bytes], None]:
        """A per-dispatch chunk sink for the instrument's drain loop.

        Fail-open: streaming is observability — an error here must never
        break the execution it observes.
        """
        key = (job_id, sheet_num)

        def write(stream: str, chunk: bytes) -> None:
            try:
                buffer = self._buffers.get(key)
                if buffer is None:
                    buffer = SheetOutputBuffer(self._ring_capacity)
                    self._buffers[key] = buffer
                for line in buffer.feed(stream, chunk):
                    self._fan_out(key, line)
            except Exception:
                _logger.debug("output_hub.write_failed", exc_info=True)

        return write

    def flush(self, job_id: str, sheet_num: int) -> None:
        """Flush partial lines at end of execution. Fail-open."""
        try:
            buffer = self._buffers.get((job_id, sheet_num))
            if buffer is None:
                return
            for line in buffer.flush():
                self._fan_out((job_id, sheet_num), line)
        except Exception:
            _logger.debug("output_hub.flush_failed", exc_info=True)

    def _fan_out(self, key: tuple[str, int], line: str) -> None:
        for sub in self._subscribers.get(key, ()):  # copy not needed: no await
            sub.offer(line)
        job_subs = self._job_subscribers.get(key[0])
        if job_subs:
            tagged = f"[s{key[1]}] {line}"
            for sub in job_subs:
                sub.offer(tagged)

    def snapshot(self, job_id: str, sheet_num: int | None = None) -> list[str]:
        """Retained lines for one sheet, or all sheets (tagged) for a job."""
        if sheet_num is not None:
            buffer = self._buffers.get((job_id, sheet_num))
            return buffer.snapshot() if buffer else []
        lines: list[str] = []
        for num in self.sheets_for_job(job_id):
            buffer = self._buffers[(job_id, num)]
            lines.extend(f"[s{num}] {line}" for line in buffer.snapshot())
        return lines

    def sheets_for_job(self, job_id: str) -> list[int]:
        return sorted(s for (j, s) in self._buffers if j == job_id)

    def subscribe(
        self, job_id: str, sheet_num: int | None = None
    ) -> OutputSubscriber:
        """Subscribe to one sheet, or (sheet_num=None) the whole job —
        job-wide lines arrive tagged ``[s<sheet>] ...``."""
        sub = OutputSubscriber()
        if sheet_num is None:
            self._job_subscribers.setdefault(job_id, set()).add(sub)
        else:
            self._subscribers.setdefault((job_id, sheet_num), set()).add(sub)
        return sub

    def unsubscribe(
        self, job_id: str, sheet_num: int | None, sub: OutputSubscriber
    ) -> None:
        if sheet_num is None:
            subs = self._job_subscribers.get(job_id)
            if subs is not None:
                subs.discard(sub)
                if not subs:
                    del self._job_subscribers[job_id]
            return
        keyed = self._subscribers.get((job_id, sheet_num))
        if keyed is not None:
            keyed.discard(sub)
            if not keyed:
                del self._subscribers[(job_id, sheet_num)]

    def clear_job(self, job_id: str) -> None:
        """Drop a finished job's rings (memory bound). Subscribers keep
        their already-queued lines; new output cannot arrive anyway."""
        for key in [k for k in self._buffers if k[0] == job_id]:
            del self._buffers[key]
        for key in [k for k in self._subscribers if k[0] == job_id]:
            del self._subscribers[key]
        self._job_subscribers.pop(job_id, None)
