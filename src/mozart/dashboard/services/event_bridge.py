"""Daemon EventBus → SSE bridge for real-time event streaming.

Polls the daemon for observer and job events, deduplicates by timestamp,
and provides both streaming (SSE) and one-shot access patterns.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from mozart.core.logging import get_logger
from mozart.daemon.ipc.client import DaemonClient

_logger = get_logger("dashboard.event_bridge")


class DaemonEventBridge:
    """Bridge between daemon EventBus and browser SSE streams.

    Polls ``daemon.observer_events`` via IPC and transforms events into
    SSE-compatible dicts.  Deduplicates across polls by tracking the latest
    timestamp seen per job.

    Parameters
    ----------
    client:
        Connected ``DaemonClient`` for IPC calls.
    poll_interval:
        Seconds between successive polls (default 2.0).
    """

    def __init__(self, client: DaemonClient, poll_interval: float = 2.0) -> None:
        self._client = client
        self._poll_interval = poll_interval
        self._last_timestamps: dict[str, float] = {}  # job_id → last seen ts

    # ------------------------------------------------------------------
    # Streaming interfaces
    # ------------------------------------------------------------------

    async def job_events(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield SSE events for a specific job.

        Polls ``daemon.observer_events`` in a loop, deduplicating by
        timestamp so each event is emitted at most once.

        Yields
        ------
        dict
            ``{"event": <event_type>, "data": <json_string>}``
        """
        while True:
            try:
                raw_events = await self._fetch_observer_events(job_id, limit=50)
                new_events = self._deduplicate(job_id, raw_events)
                for evt in new_events:
                    yield self._to_sse(evt)
            except Exception:
                _logger.debug("job_events_poll_error", job_id=job_id, exc_info=True)
            await asyncio.sleep(self._poll_interval)

    async def all_events(self, limit: int = 50) -> AsyncIterator[dict[str, Any]]:
        """Yield SSE events across all active jobs.

        First lists all known jobs, then polls observer events for each.
        Intended for the global event timeline.

        Yields
        ------
        dict
            ``{"event": <event_type>, "data": <json_string>}``
        """
        while True:
            try:
                jobs = await self._client.list_jobs()
                all_new: list[dict[str, Any]] = []
                for job in jobs:
                    jid = job.get("job_id", job.get("id", ""))
                    if not jid:
                        continue
                    try:
                        raw = await self._fetch_observer_events(jid, limit=limit)
                        new = self._deduplicate(jid, raw)
                        all_new.extend(new)
                    except Exception:
                        _logger.debug(
                            "all_events_job_poll_error", job_id=jid, exc_info=True,
                        )

                # Sort by timestamp so events stream in chronological order
                all_new.sort(key=lambda e: e.get("timestamp", 0))
                for evt in all_new[:limit]:
                    yield self._to_sse(evt)
            except Exception:
                _logger.debug("all_events_poll_error", exc_info=True)
            await asyncio.sleep(self._poll_interval)

    # ------------------------------------------------------------------
    # One-shot interface
    # ------------------------------------------------------------------

    async def observer_events(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent observer events for a job (one-shot, not streaming).

        Returns
        -------
        list[dict]
            Raw event dicts from the daemon, most recent first.
        """
        try:
            events = await self._fetch_observer_events(job_id, limit=limit)
            return events
        except Exception:
            _logger.debug(
                "observer_events_fetch_error", job_id=job_id, exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_observer_events(
        self, job_id: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Call ``daemon.observer_events`` via IPC."""
        result = await self._client.call(
            "daemon.observer_events",
            {"job_id": job_id, "limit": limit},
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("events", [])
        return []

    def _deduplicate(
        self, job_id: str, events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Filter out events already seen based on timestamp tracking."""
        last_ts = self._last_timestamps.get(job_id, 0.0)
        new_events: list[dict[str, Any]] = []

        for evt in events:
            ts = evt.get("timestamp", 0.0)
            if isinstance(ts, str):
                # ISO-format fallback — use current time as proxy
                ts = time.time()
            if ts > last_ts:
                new_events.append(evt)

        if new_events:
            max_ts = max(e.get("timestamp", 0.0) for e in new_events)
            if isinstance(max_ts, (int, float)):
                self._last_timestamps[job_id] = max_ts

        return new_events

    @staticmethod
    def _to_sse(event: dict[str, Any]) -> dict[str, Any]:
        """Transform a raw daemon event into SSE format."""
        event_type = event.get("event_type", event.get("type", "daemon_event"))
        return {
            "event": event_type,
            "data": json.dumps(event),
        }


__all__ = ["DaemonEventBridge"]
