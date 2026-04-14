"""Daemon EventBus → SSE bridge for real-time event streaming.

Subscribes to ``daemon.monitor.stream`` via ``DaemonClient.stream()`` and
multiplexes events to per-job and global consumers.  Replaces the previous
polling approach with a single long-lived subscription to the conductor's
EventBus.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any, cast

from marianne.core.logging import get_logger
from marianne.daemon.ipc.client import DaemonClient

_logger = get_logger("dashboard.event_bridge")

_QUEUE_MAX = 200
_RECONNECT_BASE = 1.0
_RECONNECT_MAX = 30.0
_INITIAL_STATUS_POLL_INTERVAL = 5.0


class DaemonEventBridge:
    """Bridge between daemon EventBus and browser SSE streams.

    Maintains a single ``daemon.monitor.stream`` subscription that feeds
    per-job queues and a global queue.  Consumers (SSE routes) await
    their queue — no polling needed.

    Parameters
    ----------
    client:
        Connected ``DaemonClient`` for IPC calls.
    """

    def __init__(self, client: DaemonClient) -> None:
        self._client = client
        self._job_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._global_queues: list[asyncio.Queue[dict[str, Any]]] = []
        self._lock = asyncio.Lock()
        self._subscriber_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background subscription to the conductor event stream."""
        if self._running:
            return
        self._running = True
        self._subscriber_task = asyncio.create_task(
            self._subscribe_loop(),
            name="event-bridge-subscriber",
        )
        _logger.info("event_bridge.started")

    async def stop(self) -> None:
        """Stop the background subscription and clean up."""
        self._running = False
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
            self._subscriber_task = None
        async with self._lock:
            for queues in self._job_queues.values():
                for q in queues:
                    q.put_nowait({"event": "bridge_stopped", "data": "{}"})
            for q in self._global_queues:
                q.put_nowait({"event": "bridge_stopped", "data": "{}"})
        _logger.info("event_bridge.stopped")

    # ------------------------------------------------------------------
    # Streaming interfaces (queue-based, zero polling)
    # ------------------------------------------------------------------

    async def job_events(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield real-time SSE events for a specific job.

        Creates a per-job queue, subscribes to the conductor event stream,
        and yields events as they arrive.
        """
        queue = await self._register_job_queue(job_id)
        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    queue.task_done()
                    yield event
                except TimeoutError:
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({"timestamp": time.time()}),
                    }
        finally:
            await self._unregister_job_queue(job_id, queue)

    async def all_events(self, limit: int = 50) -> AsyncIterator[dict[str, Any]]:
        """Yield real-time SSE events across all jobs.

        Used by the global event timeline on the dashboard index page.
        """
        queue = await self._register_global_queue()
        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    queue.task_done()
                    yield event
                except TimeoutError:
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({"timestamp": time.time()}),
                    }
        finally:
            await self._unregister_global_queue(queue)

    # ------------------------------------------------------------------
    # One-shot interface (backward compat)
    # ------------------------------------------------------------------

    async def observer_events(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent observer events for a job (one-shot, not streaming).

        Falls back to IPC call for historical events.
        """
        try:
            events = await self._fetch_observer_events(job_id, limit=limit)
            return events
        except Exception:
            _logger.debug(
                "observer_events_fetch_error",
                job_id=job_id,
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # Queue registration
    # ------------------------------------------------------------------

    async def _register_job_queue(
        self,
        job_id: str,
    ) -> asyncio.Queue[dict[str, Any]]:
        """Register a queue for a specific job's events."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        async with self._lock:
            if job_id not in self._job_queues:
                self._job_queues[job_id] = []
            self._job_queues[job_id].append(queue)
        return queue

    async def _unregister_job_queue(
        self,
        job_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Remove a per-job queue."""
        async with self._lock:
            queues = self._job_queues.get(job_id)
            if queues is not None:
                try:
                    queues.remove(queue)
                except ValueError:
                    pass
                if not queues:
                    self._job_queues.pop(job_id, None)

    async def _register_global_queue(
        self,
    ) -> asyncio.Queue[dict[str, Any]]:
        """Register a queue for all events."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        async with self._lock:
            self._global_queues.append(queue)
        return queue

    async def _unregister_global_queue(
        self,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Remove a global queue."""
        async with self._lock:
            try:
                self._global_queues.remove(queue)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Background subscription loop
    # ------------------------------------------------------------------

    async def _subscribe_loop(self) -> None:
        """Maintain a persistent subscription with reconnection backoff."""
        delay = _RECONNECT_BASE
        while self._running:
            try:
                async for params in self._client.stream("daemon.monitor.stream"):
                    if not self._running:
                        break
                    await self._dispatch(params)
                    delay = _RECONNECT_BASE
            except asyncio.CancelledError:
                break
            except Exception:
                _logger.warning(
                    "event_bridge.stream_disconnected",
                    reconnect_in=delay,
                    exc_info=True,
                )

            if not self._running:
                break

            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)

    async def _dispatch(self, raw_event: dict[str, Any]) -> None:
        """Route an event to matching queues."""
        sse_event = self._to_sse(raw_event)
        job_id = raw_event.get("job_id")

        async with self._lock:
            targets: list[asyncio.Queue[dict[str, Any]]] = []
            if job_id and job_id in self._job_queues:
                targets.extend(self._job_queues[job_id])
            targets.extend(self._global_queues)

        for q in targets:
            try:
                q.put_nowait(sse_event)
            except asyncio.QueueFull:
                _logger.debug(
                    "event_bridge.queue_full",
                    job_id=job_id,
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_observer_events(
        self,
        job_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Call ``daemon.observer_events`` via IPC."""
        result = await self._client.call(
            "daemon.observer_events",
            {"job_id": job_id, "limit": limit},
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return cast("list[dict[str, Any]]", result.get("events", []))
        return []

    @staticmethod
    def _to_sse(event: dict[str, Any]) -> dict[str, Any]:
        """Transform a raw daemon event into SSE format."""
        event_type = event.get("event_type", event.get("event", "daemon_event"))
        return {
            "event": event_type,
            "data": json.dumps(event),
        }


__all__ = ["DaemonEventBridge"]
