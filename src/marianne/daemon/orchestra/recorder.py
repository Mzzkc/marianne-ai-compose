"""Orchestra timeline recorder (GH #468).

Subscribes to the EventBus, projects conductor events into the orchestra
vocabulary, maintains the live fleet snapshot, appends each projected event
to ``<orchestra_dir>/<YYYY-MM-DD>.jsonl`` and serves a bounded ring buffer
to IPC readers.

The projection is deliberately lossy. A viewer sees *which* score is
playing *which* sheet on *which* instrument and how it ended; never where
the workspace is, what the prompt said, or why a sheet failed. The
``job_id`` is shown as given (operators name their scores), because the
whole point is to watch the fleet by name.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from marianne.core.logging import get_logger
from marianne.daemon.config import OrchestraConfig

if TYPE_CHECKING:
    from marianne.daemon.event_bus import EventBus
    from marianne.daemon.types import ObserverEvent

_logger = get_logger("daemon.orchestra")


class OrchestraEvent(TypedDict):
    """One projected event. Every field is public-safe by construction."""

    ts: float
    job: str
    sheet: int | None
    kind: str
    instrument: str | None
    outcome: str | None
    note: str | None


ORCHESTRA_EVENTS: frozenset[str] = frozenset({
    "job.started",
    "job.ended",
    "job.paused",
    "job.resumed",
    "job.chained",
    "sheet.dispatched",
    "sheet.ended",
    "sheet.fallback",
    "sheet.retry",
    "sheet.skipped",
})

# Source event -> (kind, outcome). Events not listed are not projected.
_DIRECT: dict[str, tuple[str, str | None]] = {
    "job.started": ("job.started", None),
    "job.resuming": ("job.resumed", None),
    "job.completed": ("job.ended", "completed"),
    "job.failed": ("job.ended", "failed"),
    "job.cancelled": ("job.ended", "cancelled"),
    "job.timeout": ("job.ended", "timeout"),
    "job.paused": ("job.paused", None),
    "baton.job.paused": ("job.paused", None),
    "baton.job.resumed": ("job.resumed", None),
    "baton.job.cancelled": ("job.ended", "cancelled"),
    "baton.job.timeout": ("job.ended", "timeout"),
    "sheet.dispatched": ("sheet.dispatched", None),
    "sheet.completed": ("sheet.ended", "completed"),
    "sheet.partial": ("sheet.ended", "partial"),
    "sheet.failed": ("sheet.ended", "failed"),
    "sheet.skipped": ("sheet.skipped", "skipped"),
    "baton.sheet.skipped": ("sheet.skipped", "skipped"),
    "baton.instrument.fallback": ("sheet.fallback", None),
    "baton.sheet.retry_scheduled": ("sheet.retry", None),
    "rate_limit.active": ("sheet.retry", "rate_limited"),
    "baton.flow.trigger_fired": ("job.chained", None),
}


def _instrument_of(data: dict[str, Any]) -> str | None:
    for key in ("instrument", "to_instrument", "instrument_name"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def project_event(event: ObserverEvent) -> OrchestraEvent | None:
    """Project a bus event into the orchestra vocabulary, or None to drop it.

    Only whitelisted source events project, and only whitelisted fields are
    read from their payloads, so a new internal field can never leak.
    """
    source = str(event.get("event", ""))
    mapping = _DIRECT.get(source)
    if mapping is None:
        return None
    kind, outcome = mapping
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    note: str | None = None
    if kind == "sheet.fallback":
        src = data.get("from_instrument")
        dst = data.get("to_instrument")
        if isinstance(src, str) and isinstance(dst, str):
            note = f"{src} -> {dst}"
    elif kind == "job.chained":
        # Only a concert action is a chain; other trigger actions are not shown.
        actions = data.get("actions")
        if not isinstance(actions, list) or not any(
            isinstance(a, dict) and "concert" in a for a in actions
        ):
            return None
    sheet_raw = event.get("sheet_num")
    sheet = int(sheet_raw) if isinstance(sheet_raw, int) and sheet_raw > 0 else None
    attempt = data.get("attempt")
    if kind in {"sheet.dispatched", "sheet.ended"} and isinstance(attempt, int) and attempt > 1:
        note = f"attempt {attempt}"
    return {
        "ts": float(event.get("timestamp") or time.time()),
        "job": str(event.get("job_id", "")),
        "sheet": sheet,
        "kind": kind,
        "instrument": _instrument_of(data),
        "outcome": outcome,
        "note": note,
    }


class _JobView:
    __slots__ = ("job", "started_at", "status", "sheets", "total_sheets", "ended_at", "chain_depth")

    def __init__(self, job: str, started_at: float) -> None:
        self.job = job
        self.started_at = started_at
        self.status = "running"
        self.sheets: dict[int, dict[str, Any]] = {}
        self.total_sheets: int | None = None
        self.ended_at: float | None = None
        self.chain_depth: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "total_sheets": self.total_sheets,
            "sheets": [
                {"sheet": num, **state} for num, state in sorted(self.sheets.items())
            ],
        }


class OrchestraRecorder:
    """EventBus subscriber projecting the fleet into the orchestra timeline.

    Not thread-safe; runs on the conductor's event loop like the observer
    recorder. File writes are append-only and flushed per event: a viewer
    tailing today's file sees each beat within one write.
    """

    def __init__(self, config: OrchestraConfig) -> None:
        self._config = config
        self._event_bus: EventBus | None = None
        self._sub_id: str | None = None
        self._recent: deque[OrchestraEvent] = deque(maxlen=config.ring_size)
        self._jobs: dict[str, _JobView] = {}
        self._handle: Any | None = None
        self._handle_day: str | None = None
        self._watchers: set[asyncio.Queue[OrchestraEvent]] = set()

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self, event_bus: EventBus) -> None:
        if not self._config.enabled:
            _logger.info("orchestra.disabled")
            return
        self._event_bus = event_bus
        self._sub_id = event_bus.subscribe(self._handle_event)
        _logger.info("orchestra.started", directory=str(self._config.directory))

    async def stop(self, event_bus: EventBus | None = None) -> None:
        bus = event_bus or self._event_bus
        if self._sub_id is not None and bus is not None:
            bus.unsubscribe(self._sub_id)
            self._sub_id = None
        self._close()
        self._event_bus = None
        _logger.info("orchestra.stopped")

    # ── seeding from the manager (jobs that predate this recorder) ───────

    def seed_job(self, job: str, *, total_sheets: int | None, started_at: float | None,
                 status: str = "running") -> None:
        """Register a job the recorder did not see start (conductor restart)."""
        view = self._jobs.get(job)
        if view is None:
            view = _JobView(job, started_at or time.time())
            self._jobs[job] = view
        view.total_sheets = total_sheets
        view.status = status

    def forget_job(self, job: str) -> None:
        self._jobs.pop(job, None)

    # ── readers ───────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """The fleet as it stands: every job the recorder knows, newest first."""
        jobs = sorted(self._jobs.values(), key=lambda v: v.started_at, reverse=True)
        return {
            "ts": time.time(),
            "jobs": [v.to_dict() for v in jobs],
        }

    def recent(self, limit: int = 200) -> list[OrchestraEvent]:
        events = list(self._recent)
        return events[-limit:]

    def watch(self) -> asyncio.Queue[OrchestraEvent]:
        """A bounded queue that receives every projected event from now on."""
        queue: asyncio.Queue[OrchestraEvent] = asyncio.Queue(maxsize=self._config.ring_size)
        self._watchers.add(queue)
        return queue

    def unwatch(self, queue: asyncio.Queue[OrchestraEvent]) -> None:
        self._watchers.discard(queue)

    def day_path(self, day: str | None = None) -> Path:
        stamp = day or datetime.now(UTC).strftime("%Y-%m-%d")
        return self._config.directory.expanduser() / f"{stamp}.jsonl"

    # ── internals ─────────────────────────────────────────────────────────

    def _handle_event(self, event: ObserverEvent) -> None:
        projected = project_event(event)
        if projected is None:
            return
        self._apply(projected)
        self._recent.append(projected)
        self._write(projected)
        for queue in list(self._watchers):
            try:
                queue.put_nowait(projected)
            except asyncio.QueueFull:
                pass

    def _apply(self, ev: OrchestraEvent) -> None:
        job = ev["job"]
        if not job:
            return
        view = self._jobs.get(job)
        if view is None:
            view = _JobView(job, ev["ts"])
            self._jobs[job] = view
        kind = ev["kind"]
        if kind == "job.started":
            view.status = "running"
            view.started_at = ev["ts"]
        elif kind == "job.ended":
            view.status = ev["outcome"] or "ended"
            view.ended_at = ev["ts"]
            self._prune_ended()
        elif kind == "job.paused":
            view.status = "paused"
        elif kind == "job.resumed":
            view.status = "running"
        sheet = ev["sheet"]
        if sheet is None:
            return
        state = view.sheets.setdefault(sheet, {"status": "pending", "instrument": None})
        if kind == "sheet.dispatched":
            state["status"] = "playing"
            state["instrument"] = ev["instrument"] or state["instrument"]
            state["since"] = ev["ts"]
        elif kind == "sheet.ended":
            state["status"] = ev["outcome"] or "ended"
            state["instrument"] = ev["instrument"] or state["instrument"]
            state["ended"] = ev["ts"]
        elif kind == "sheet.skipped":
            state["status"] = "skipped"
        elif kind == "sheet.fallback":
            state["instrument"] = ev["instrument"] or state["instrument"]
            state["status"] = "playing"
        elif kind == "sheet.retry":
            state["status"] = "retrying"

    def _prune_ended(self) -> None:
        """Keep ended jobs visible for a while, then drop them from the snapshot."""
        cutoff = time.time() - self._config.keep_ended_seconds
        for job, view in list(self._jobs.items()):
            if view.ended_at is not None and view.ended_at < cutoff:
                del self._jobs[job]

    def _write(self, ev: OrchestraEvent) -> None:
        if not self._config.persist:
            return
        day = datetime.fromtimestamp(ev["ts"], tz=UTC).strftime("%Y-%m-%d")
        if self._handle is None or self._handle_day != day:
            self._close()
            path = self.day_path(day)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = open(path, "a", encoding="utf-8")  # noqa: SIM115
                self._handle_day = day
            except OSError:
                _logger.warning("orchestra.open_failed", path=str(path), exc_info=True)
                self._handle = None
                return
        try:
            self._handle.write(json.dumps(ev, separators=(",", ":")) + "\n")
            self._handle.flush()
        except OSError:
            _logger.warning("orchestra.write_failed", exc_info=True)

    def _close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
            self._handle_day = None


def read_day(path: Path, *, limit: int | None = None) -> list[OrchestraEvent]:
    """Read one day's JSONL for replay; malformed lines are skipped."""
    events: list[OrchestraEvent] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                if isinstance(raw, dict) and "kind" in raw and "job" in raw:
                    events.append(raw)  # type: ignore[arg-type]
    except OSError:
        return []
    return events[-limit:] if limit else events
