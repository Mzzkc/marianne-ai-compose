"""Tests for DaemonEventBridge — queue-based event multiplexing.

Tests the real-time streaming bridge that replaces polling with
daemon.monitor.stream subscriptions.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.dashboard.services.event_bridge import DaemonEventBridge


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock DaemonClient."""
    client = AsyncMock()
    client.call = AsyncMock(return_value={"events": []})
    return client


@pytest.fixture
def bridge(mock_client: AsyncMock) -> DaemonEventBridge:
    """Create a DaemonEventBridge with mock client."""
    return DaemonEventBridge(mock_client)


class TestDaemonEventBridgeLifecycle:
    """Test start/stop lifecycle."""

    async def test_start_creates_subscriber_task(self, bridge: DaemonEventBridge) -> None:
        """start() creates a background subscriber task."""
        mock_stream: AsyncIterator[dict[str, Any]] = AsyncMock()
        mock_stream.__aiter__ = MagicMock(return_value=mock_stream)
        bridge._client.stream = AsyncMock(return_value=mock_stream)

        await bridge.start()
        assert bridge._running is True
        assert bridge._subscriber_task is not None

        await bridge.stop()
        assert bridge._running is False
        assert bridge._subscriber_task is None

    async def test_start_idempotent(self, bridge: DaemonEventBridge) -> None:
        """Calling start() twice does not create duplicate tasks."""
        mock_stream: AsyncIterator[dict[str, Any]] = AsyncMock()
        mock_stream.__aiter__ = MagicMock(return_value=mock_stream)
        bridge._client.stream = AsyncMock(return_value=mock_stream)

        await bridge.start()
        task1 = bridge._subscriber_task
        await bridge.start()
        task2 = bridge._subscriber_task

        assert task1 is task2

        await bridge.stop()

    async def test_stop_sends_bridge_stopped(self, bridge: DaemonEventBridge) -> None:
        """stop() pushes bridge_stopped to all registered queues."""
        mock_stream: AsyncIterator[dict[str, Any]] = AsyncMock()
        mock_stream.__aiter__ = MagicMock(return_value=mock_stream)
        bridge._client.stream = AsyncMock(return_value=mock_stream)

        await bridge.start()

        queue = await bridge._register_global_queue()

        await bridge.stop()

        event = queue.get_nowait()
        assert event["event"] == "bridge_stopped"


class TestDaemonEventBridgeDispatch:
    """Test event routing to queues."""

    async def test_dispatch_routes_to_job_queue(self, bridge: DaemonEventBridge) -> None:
        """Events with matching job_id are delivered to per-job queues."""
        queue = await bridge._register_job_queue("job-1")

        await bridge._dispatch(
            {
                "job_id": "job-1",
                "event": "sheet.completed",
                "data": {"sheet_num": 1},
                "timestamp": 1234.5,
            }
        )

        event = queue.get_nowait()
        assert event["event"] == "sheet.completed"
        assert "job-1" in event["data"]

    async def test_dispatch_routes_to_global_queue(self, bridge: DaemonEventBridge) -> None:
        """All events are delivered to global queues."""
        queue = await bridge._register_global_queue()

        await bridge._dispatch(
            {
                "job_id": "job-2",
                "event": "sheet.started",
                "data": {},
                "timestamp": 1234.5,
            }
        )

        event = queue.get_nowait()
        assert event["event"] == "sheet.started"

    async def test_dispatch_ignores_full_queues(self, bridge: DaemonEventBridge) -> None:
        """Full queues are silently skipped."""
        queue = await bridge._register_job_queue("job-1")
        for i in range(200):
            queue.put_nowait({"event": f"fill-{i}", "data": "{}"})

        await bridge._dispatch(
            {
                "job_id": "job-1",
                "event": "sheet.completed",
                "data": {},
                "timestamp": 1234.5,
            }
        )

    async def test_dispatch_no_job_id(self, bridge: DaemonEventBridge) -> None:
        """Events without job_id only go to global queues."""
        global_queue = await bridge._register_global_queue()
        job_queue = await bridge._register_job_queue("job-1")

        await bridge._dispatch(
            {
                "event": "daemon.info",
                "data": {},
                "timestamp": 1234.5,
            }
        )

        event = global_queue.get_nowait()
        assert event["event"] == "daemon.info"
        assert job_queue.empty()


class TestDaemonEventBridgeStreaming:
    """Test the streaming interfaces."""

    async def test_job_events_yields_events(self, bridge: DaemonEventBridge) -> None:
        """job_events() yields events from the per-job queue."""
        events_received: list[dict[str, Any]] = []

        async def _produce():
            queue = bridge._job_queues.get("job-1", [])
            if queue:
                await queue[0].put({"event": "test", "data": '{"key": "val"}'})
            await asyncio.sleep(0.1)
            await bridge.stop()

        bridge._running = True
        producer = asyncio.create_task(_produce())
        collected: list[dict[str, Any]] = []

        async for event in bridge.job_events("job-1"):
            collected.append(event)
            if len(collected) >= 1:
                await bridge.stop()
                break

        await producer
        assert len(collected) >= 1
        assert collected[0]["event"] == "test"

    async def test_all_events_yields_events(self, bridge: DaemonEventBridge) -> None:
        """all_events() yields from the global queue."""
        bridge._running = True

        async def _produce():
            for q in bridge._global_queues:
                await q.put({"event": "global_test", "data": "{}"})
            await asyncio.sleep(0.1)
            await bridge.stop()

        producer = asyncio.create_task(_produce())
        collected: list[dict[str, Any]] = []

        async for event in bridge.all_events():
            collected.append(event)
            if len(collected) >= 1:
                await bridge.stop()
                break

        await producer
        assert len(collected) >= 1
        assert collected[0]["event"] == "global_test"


class TestDaemonEventBridgeReconnect:
    """Test reconnection behavior."""

    async def test_subscribe_loop_reconnects_on_error(self, bridge: DaemonEventBridge) -> None:
        """_subscribe_loop reconnects after a connection error."""
        call_count = 0

        async def _mock_stream(
            method: str, params: dict[str, Any] | None = None
        ) -> AsyncIterator[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("socket closed")
            yield {"job_id": "j1", "event": "test", "data": {}, "timestamp": 1.0}
            await asyncio.sleep(10)

        bridge._running = True
        bridge._client.stream = _mock_stream

        task = asyncio.create_task(bridge._subscribe_loop())
        await asyncio.sleep(2.5)
        bridge._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert call_count >= 2


class TestDaemonEventBridgeOneShot:
    """Test the one-shot observer_events interface."""

    async def test_observer_events_returns_events(self, bridge: DaemonEventBridge) -> None:
        """observer_events() calls IPC and returns event list."""
        bridge._client.call = AsyncMock(
            return_value={
                "events": [
                    {"event": "sheet.completed", "job_id": "j1", "timestamp": 1.0},
                ],
            }
        )

        result = await bridge.observer_events("j1", limit=10)
        assert len(result) == 1
        assert result[0]["event"] == "sheet.completed"

    async def test_observer_events_handles_error(self, bridge: DaemonEventBridge) -> None:
        """observer_events() returns empty list on IPC failure."""
        bridge._client.call = AsyncMock(side_effect=ConnectionError("fail"))

        result = await bridge.observer_events("j1")
        assert result == []


class TestDaemonEventBridgeSSEFormat:
    """Test SSE event formatting."""

    def test_to_sse_uses_event_field(self) -> None:
        """_to_sse extracts event type from 'event' key."""
        raw = {"event": "sheet.completed", "job_id": "j1", "data": {}, "timestamp": 1.0}
        sse = DaemonEventBridge._to_sse(raw)
        assert sse["event"] == "sheet.completed"
        assert json.loads(sse["data"]) == raw

    def test_to_sse_fallback_to_event_type(self) -> None:
        """_to_sse falls back to 'event_type' key."""
        raw = {"event_type": "observer.file_created", "job_id": "j1", "timestamp": 1.0}
        sse = DaemonEventBridge._to_sse(raw)
        assert sse["event"] == "observer.file_created"

    def test_to_sse_default(self) -> None:
        """_to_sse defaults to 'daemon_event'."""
        raw = {"job_id": "j1", "timestamp": 1.0}
        sse = DaemonEventBridge._to_sse(raw)
        assert sse["event"] == "daemon_event"
