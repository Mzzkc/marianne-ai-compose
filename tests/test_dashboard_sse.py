"""Tests for SSE manager functionality."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime

import pytest

from marianne.dashboard.services.sse_manager import ClientConnection, SSEEvent, SSEManager


class TestSSEEvent:
    """Test SSE event formatting."""

    def test_simple_event_format(self):
        """Test basic SSE event formatting."""
        event = SSEEvent(event="test", data="hello world")
        formatted = event.format()

        expected = "event: test\ndata: hello world\n\n"
        assert formatted == expected

    def test_event_with_id_and_retry(self):
        """Test SSE event with ID and retry fields."""
        event = SSEEvent(
            event="status",
            data="job started",
            id="test-123",
            retry=5000
        )
        formatted = event.format()

        expected = "id: test-123\nretry: 5000\nevent: status\ndata: job started\n\n"
        assert formatted == expected

    def test_multiline_data(self):
        """Test SSE event with multiline data."""
        event = SSEEvent(event="log", data="line 1\nline 2\nline 3")
        formatted = event.format()

        expected = "event: log\ndata: line 1\ndata: line 2\ndata: line 3\n\n"
        assert formatted == expected

    def test_json_data(self):
        """Test SSE event with JSON data."""
        data = {"status": "running", "progress": 50}
        event = SSEEvent(event="update", data=json.dumps(data))
        formatted = event.format()

        expected = 'event: update\ndata: {"status": "running", "progress": 50}\n\n'
        assert formatted == expected


class TestClientConnection:
    """Test client connection management."""

    def test_client_connection_creation(self):
        """Test creating a client connection."""
        conn = ClientConnection(client_id="test-123", job_id="job-456")

        assert conn.client_id == "test-123"
        assert conn.job_id == "job-456"
        assert isinstance(conn.queue, asyncio.Queue)
        assert conn.queue.maxsize == 100
        assert isinstance(conn.connected_at, datetime)

    def test_client_connection_queue_operations(self):
        """Test queue operations on client connection."""
        conn = ClientConnection(client_id="test-123", job_id="job-456")
        event = SSEEvent(event="test", data="hello")

        # Should be able to put and get events
        conn.queue.put_nowait(event)
        assert conn.queue.qsize() == 1

        retrieved_event = conn.queue.get_nowait()
        assert retrieved_event == event
        assert conn.queue.qsize() == 0


@pytest.mark.asyncio
class TestSSEManager:
    """Test SSE manager operations."""

    @pytest.fixture
    async def sse_manager(self):
        """Create an SSE manager for testing."""
        return SSEManager()

    # ── Deterministic helpers (replace asyncio.sleep timing) ──────────

    @staticmethod
    async def _wait_for_connections(
        manager: SSEManager,
        expected: int,
        *,
        job_id: str | None = None,
        timeout: float = 2.0,
    ) -> None:
        """Poll until connection count reaches *expected* or timeout."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if manager.get_connection_count(job_id) == expected:
                return
            await asyncio.sleep(0.01)
        raise TimeoutError(
            f"Expected {expected} connections (job_id={job_id}), "
            f"got {manager.get_connection_count(job_id)} after {timeout}s"
        )

    @staticmethod
    async def _wait_for_events(
        events_list: list[str],
        expected_count: int,
        *,
        timeout: float = 2.0,
    ) -> None:
        """Poll until events_list has at least *expected_count* entries."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if len(events_list) >= expected_count:
                return
            await asyncio.sleep(0.01)
        raise TimeoutError(
            f"Expected {expected_count} events, got {len(events_list)} "
            f"after {timeout}s: {events_list}"
        )

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None]) -> None:
        """Cancel a task and suppress CancelledError."""
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # ── Tests ─────────────────────────────────────────────────────────

    async def test_connection_count_empty(self, sse_manager):
        """Test connection count when no clients connected."""
        assert sse_manager.get_connection_count() == 0
        assert sse_manager.get_connection_count("job-123") == 0

    async def test_broadcast_to_no_clients(self, sse_manager):
        """Test broadcasting when no clients are connected."""
        event = SSEEvent(event="test", data="hello")
        sent_count = await sse_manager.broadcast("job-123", event)
        assert sent_count == 0

    async def test_disconnect_nonexistent_client(self, sse_manager):
        """Test disconnecting a client that doesn't exist."""
        # Should not raise an error
        await sse_manager.disconnect("job-123", "client-456")
        assert sse_manager.get_connection_count() == 0

    async def test_basic_connection_lifecycle(self, sse_manager):
        """Test basic connect/disconnect lifecycle."""
        connect_task = asyncio.create_task(
            self._consume_events(sse_manager.connect("job-123", "client-456"))
        )

        await self._wait_for_connections(sse_manager, 1)
        assert sse_manager.get_connection_count("job-123") == 1

        await self._cancel_task(connect_task)
        await self._wait_for_connections(sse_manager, 0)

    async def test_broadcast_to_single_client(self, sse_manager):
        """Test broadcasting to a single client."""
        events_received: list[str] = []

        connect_task = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-123", "client-456"), events_received
            )
        )

        await self._wait_for_connections(sse_manager, 1)

        event = SSEEvent(event="test", data="hello world")
        sent_count = await sse_manager.broadcast("job-123", event)
        assert sent_count == 1

        # Wait for connection event + broadcast event
        await self._wait_for_events(events_received, 2)

        await self._cancel_task(connect_task)

        first_event = events_received[0]
        assert "event: connected" in first_event
        assert "client-456" in first_event

        second_event = events_received[1]
        assert "event: test" in second_event
        assert "data: hello world" in second_event

    async def test_broadcast_to_multiple_clients(self, sse_manager):
        """Test broadcasting to multiple clients."""
        events_received_1: list[str] = []
        events_received_2: list[str] = []

        connect_task_1 = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-123", "client-1"), events_received_1
            )
        )
        connect_task_2 = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-123", "client-2"), events_received_2
            )
        )

        await self._wait_for_connections(sse_manager, 2, job_id="job-123")

        event = SSEEvent(event="test", data="broadcast to all")
        sent_count = await sse_manager.broadcast("job-123", event)
        assert sent_count == 2

        await self._wait_for_events(events_received_1, 2)
        await self._wait_for_events(events_received_2, 2)

        await self._cancel_task(connect_task_1)
        await self._cancel_task(connect_task_2)

        assert any("broadcast to all" in event for event in events_received_1)
        assert any("broadcast to all" in event for event in events_received_2)

    async def test_send_job_update(self, sse_manager):
        """Test convenience method for job status updates."""
        events_received: list[str] = []

        connect_task = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-123", "client-456"), events_received
            )
        )

        await self._wait_for_connections(sse_manager, 1)

        data = {"sheet": 2, "progress": 50}
        sent_count = await sse_manager.send_job_update("job-123", "running", data)
        assert sent_count == 1

        await self._wait_for_events(events_received, 2)

        await self._cancel_task(connect_task)

        job_update_event = None
        for event in events_received:
            if "event: job_status" in event:
                job_update_event = event
                break

        assert job_update_event is not None
        assert "running" in job_update_event
        assert "sheet" in job_update_event
        assert "progress" in job_update_event

    async def test_send_log_line(self, sse_manager):
        """Test convenience method for log streaming."""
        events_received: list[str] = []

        connect_task = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-123", "client-456"), events_received
            )
        )

        await self._wait_for_connections(sse_manager, 1)

        sent_count = await sse_manager.send_log_line(
            "job-123", "Processing sheet 2\n", "info"
        )
        assert sent_count == 1

        await self._wait_for_events(events_received, 2)

        await self._cancel_task(connect_task)

        log_event = None
        for event in events_received:
            if "event: log" in event:
                log_event = event
                break

        assert log_event is not None
        assert "Processing sheet 2" in log_event
        assert "info" in log_event

    async def test_connection_isolation_by_job(self, sse_manager):
        """Test that broadcasts are isolated by job ID."""
        events_job1: list[str] = []
        events_job2: list[str] = []

        connect_task_1 = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-1", "client-1"), events_job1
            )
        )
        connect_task_2 = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-2", "client-2"), events_job2
            )
        )

        await self._wait_for_connections(sse_manager, 2)

        event = SSEEvent(event="test", data="only for job-1")
        sent_count = await sse_manager.broadcast("job-1", event)
        assert sent_count == 1

        await self._wait_for_events(events_job1, 2)

        await self._cancel_task(connect_task_1)
        await self._cancel_task(connect_task_2)

        assert any("only for job-1" in event for event in events_job1)
        assert not any("only for job-1" in event for event in events_job2)

    async def test_get_connected_jobs(self, sse_manager):
        """Test getting list of jobs with active connections."""
        jobs = await sse_manager.get_connected_jobs()
        assert jobs == []

        connect_task_1 = asyncio.create_task(
            self._consume_events(sse_manager.connect("job-1", "client-1"))
        )
        connect_task_2 = asyncio.create_task(
            self._consume_events(sse_manager.connect("job-2", "client-2"))
        )

        await self._wait_for_connections(sse_manager, 2)

        jobs = await sse_manager.get_connected_jobs()
        assert set(jobs) == {"job-1", "job-2"}

        await self._cancel_task(connect_task_1)
        await self._cancel_task(connect_task_2)

    async def test_close_all_connections(self, sse_manager):
        """Test closing all connections for shutdown."""
        events_received: list[str] = []

        connect_task = asyncio.create_task(
            self._collect_events(
                sse_manager.connect("job-123", "client-456"), events_received
            )
        )

        await self._wait_for_connections(sse_manager, 1)

        await sse_manager.close_all_connections()

        # Wait for the close event to propagate through the queue
        await self._wait_for_events(events_received, 2)  # connected + close

        assert sse_manager.get_connection_count() == 0

        await self._cancel_task(connect_task)

        assert any("event: close" in event for event in events_received)

    # ── Private helpers ───────────────────────────────────────────────

    async def _consume_events(self, event_stream: AsyncIterator[str]) -> None:
        """Consume events from a stream without storing them."""
        async for _ in event_stream:
            pass

    async def _collect_events(
        self, event_stream: AsyncIterator[str], events_list: list[str]
    ) -> None:
        """Collect events from a stream into a list."""
        async for event in event_stream:
            events_list.append(event)
