"""Tests for DaemonEventBridge and event stream routes."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from marianne.dashboard.services.event_bridge import DaemonEventBridge

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def mock_client() -> AsyncMock:
    """Create a mocked DaemonClient."""
    client = AsyncMock()
    client.call = AsyncMock(return_value=[])
    client.list_jobs = AsyncMock(return_value=[])
    return client


@pytest.fixture()
def bridge(mock_client: AsyncMock) -> DaemonEventBridge:
    """Create a DaemonEventBridge with mocked client."""
    return DaemonEventBridge(client=mock_client)


# ============================================================================
# DaemonEventBridge — observer_events (one-shot)
# ============================================================================


@pytest.mark.asyncio
async def test_observer_events_returns_list(
    bridge: DaemonEventBridge,
    mock_client: AsyncMock,
) -> None:
    """observer_events returns the raw event list from the daemon."""
    events = [
        {"event": "sheet.completed", "timestamp": 1000.0, "sheet": 1},
        {"event": "sheet.started", "timestamp": 1001.0, "sheet": 2},
    ]
    mock_client.call.return_value = events

    result = await bridge.observer_events("job-1", limit=10)
    assert result == events
    mock_client.call.assert_awaited_once_with(
        "daemon.observer_events",
        {"job_id": "job-1", "limit": 10},
    )


@pytest.mark.asyncio
async def test_observer_events_returns_empty_on_error(
    bridge: DaemonEventBridge,
    mock_client: AsyncMock,
) -> None:
    """observer_events returns [] when the IPC call fails."""
    mock_client.call.side_effect = ConnectionError("no daemon")

    result = await bridge.observer_events("job-1")
    assert result == []


@pytest.mark.asyncio
async def test_observer_events_handles_dict_response(
    bridge: DaemonEventBridge,
    mock_client: AsyncMock,
) -> None:
    """observer_events handles dict-wrapped response with 'events' key."""
    events = [{"event": "sheet.started", "timestamp": 500.0}]
    mock_client.call.return_value = {"events": events}

    result = await bridge.observer_events("job-1")
    assert result == events


@pytest.mark.asyncio
async def test_observer_events_handles_unexpected_type(
    bridge: DaemonEventBridge,
    mock_client: AsyncMock,
) -> None:
    """observer_events returns [] for unexpected response types."""
    mock_client.call.return_value = "unexpected"

    result = await bridge.observer_events("job-1")
    assert result == []


# ============================================================================
# DaemonEventBridge — _to_sse
# ============================================================================


def test_to_sse_format() -> None:
    """_to_sse produces correct event/data structure."""
    raw = {"event": "sheet.completed", "sheet": 3, "timestamp": 100.0}
    sse = DaemonEventBridge._to_sse(raw)

    assert sse["event"] == "sheet.completed"
    parsed = json.loads(sse["data"])
    assert parsed["sheet"] == 3


def test_to_sse_fallback_event_type() -> None:
    """_to_sse uses 'daemon_event' when no event key present."""
    raw = {"foo": "bar"}
    sse = DaemonEventBridge._to_sse(raw)
    assert sse["event"] == "daemon_event"


def test_to_sse_uses_event_type_key() -> None:
    """_to_sse falls back to 'event_type' key when 'event' is absent."""
    raw = {"event_type": "custom.event", "value": 42}
    sse = DaemonEventBridge._to_sse(raw)
    assert sse["event"] == "custom.event"


# ============================================================================
# DaemonEventBridge — queue-based streaming
# ============================================================================


@pytest.mark.asyncio
async def test_job_events_delivers_to_queue(
    bridge: DaemonEventBridge,
) -> None:
    """job_events yields events pushed to the per-job queue."""
    bridge._running = True

    async def _inject():
        for queues in bridge._job_queues.get("job-1", []):
            await queues.put({"event": "sheet.completed", "data": "{}"})
        await asyncio.sleep(0.1)
        bridge._running = False

    task = asyncio.create_task(_inject())
    collected: list[dict[str, Any]] = []
    async for evt in bridge.job_events("job-1"):
        collected.append(evt)
        break

    await task
    assert len(collected) == 1
    assert collected[0]["event"] == "sheet.completed"


@pytest.mark.asyncio
async def test_all_events_delivers_to_global_queue(
    bridge: DaemonEventBridge,
) -> None:
    """all_events yields events pushed to the global queue."""
    bridge._running = True

    async def _inject():
        for q in bridge._global_queues:
            await q.put({"event": "global.test", "data": "{}"})
        await asyncio.sleep(0.1)
        bridge._running = False

    task = asyncio.create_task(_inject())
    collected: list[dict[str, Any]] = []
    async for evt in bridge.all_events():
        collected.append(evt)
        break

    await task
    assert len(collected) == 1
    assert collected[0]["event"] == "global.test"


# ============================================================================
# Events route module
# ============================================================================


def test_format_sse_produces_valid_wire_format() -> None:
    """_format_sse creates proper SSE wire format."""
    from marianne.dashboard.routes.events import _format_sse

    sse = {"event": "test.event", "data": '{"key": "value"}'}
    formatted = _format_sse(sse)

    assert "event: test.event\n" in formatted
    assert 'data: {"key": "value"}\n' in formatted
    assert formatted.endswith("\n\n")


def test_format_sse_default_event() -> None:
    """_format_sse uses 'message' as default event type."""
    from marianne.dashboard.routes.events import _format_sse

    formatted = _format_sse({"data": "{}"})
    assert "event: message\n" in formatted


def test_set_and_get_event_bridge() -> None:
    """set_event_bridge/get_event_bridge round-trips correctly."""
    from marianne.dashboard.routes.events import get_event_bridge, set_event_bridge

    client = AsyncMock()
    bridge = DaemonEventBridge(client=client)
    set_event_bridge(bridge)
    try:
        assert get_event_bridge() is bridge
    finally:
        set_event_bridge(None)  # type: ignore[arg-type]


def test_get_event_bridge_raises_when_not_configured() -> None:
    """get_event_bridge raises RuntimeError before set_event_bridge."""
    from marianne.dashboard.routes.events import get_event_bridge, set_event_bridge

    set_event_bridge(None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="DaemonEventBridge not configured"):
        get_event_bridge()
