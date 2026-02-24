"""Tests for DaemonEventBridge and event stream routes."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mozart.dashboard.services.event_bridge import DaemonEventBridge

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
    return DaemonEventBridge(client=mock_client, poll_interval=0.05)


# ============================================================================
# DaemonEventBridge — observer_events (one-shot)
# ============================================================================


@pytest.mark.asyncio
async def test_observer_events_returns_list(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """observer_events returns the raw event list from the daemon."""
    events = [
        {"event_type": "sheet.completed", "timestamp": 1000.0, "sheet": 1},
        {"event_type": "sheet.started", "timestamp": 1001.0, "sheet": 2},
    ]
    mock_client.call.return_value = events

    result = await bridge.observer_events("job-1", limit=10)
    assert result == events
    mock_client.call.assert_awaited_once_with(
        "daemon.observer_events", {"job_id": "job-1", "limit": 10},
    )


@pytest.mark.asyncio
async def test_observer_events_returns_empty_on_error(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """observer_events returns [] when the IPC call fails."""
    mock_client.call.side_effect = ConnectionError("no daemon")

    result = await bridge.observer_events("job-1")
    assert result == []


@pytest.mark.asyncio
async def test_observer_events_handles_dict_response(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """observer_events handles dict-wrapped response with 'events' key."""
    events = [{"event_type": "sheet.started", "timestamp": 500.0}]
    mock_client.call.return_value = {"events": events}

    result = await bridge.observer_events("job-1")
    assert result == events


@pytest.mark.asyncio
async def test_observer_events_handles_unexpected_type(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """observer_events returns [] for unexpected response types."""
    mock_client.call.return_value = "unexpected"

    result = await bridge.observer_events("job-1")
    assert result == []


# ============================================================================
# DaemonEventBridge — deduplication
# ============================================================================


@pytest.mark.asyncio
async def test_deduplication_filters_seen_events(
    bridge: DaemonEventBridge,
) -> None:
    """Events with timestamps <= the last-seen are filtered out."""
    events = [
        {"event_type": "a", "timestamp": 10.0},
        {"event_type": "b", "timestamp": 20.0},
    ]

    # First call: both events are new
    new = bridge._deduplicate("job-x", events)
    assert len(new) == 2

    # Second call with same events: all filtered
    new = bridge._deduplicate("job-x", events)
    assert len(new) == 0


@pytest.mark.asyncio
async def test_deduplication_passes_newer_events(
    bridge: DaemonEventBridge,
) -> None:
    """Only events with timestamps > last-seen are returned."""
    bridge._last_timestamps["job-y"] = 15.0

    events = [
        {"event_type": "old", "timestamp": 10.0},
        {"event_type": "new", "timestamp": 20.0},
    ]
    new = bridge._deduplicate("job-y", events)
    assert len(new) == 1
    assert new[0]["event_type"] == "new"


@pytest.mark.asyncio
async def test_deduplication_handles_string_timestamps(
    bridge: DaemonEventBridge,
) -> None:
    """String timestamps fall back to current time (always considered new)."""
    events = [
        {"event_type": "iso", "timestamp": "2026-01-01T00:00:00Z"},
    ]
    new = bridge._deduplicate("job-z", events)
    assert len(new) == 1


# ============================================================================
# DaemonEventBridge — _to_sse
# ============================================================================


def test_to_sse_format() -> None:
    """_to_sse produces correct event/data structure."""
    raw = {"event_type": "sheet.completed", "sheet": 3, "timestamp": 100.0}
    sse = DaemonEventBridge._to_sse(raw)

    assert sse["event"] == "sheet.completed"
    parsed = json.loads(sse["data"])
    assert parsed["sheet"] == 3


def test_to_sse_fallback_event_type() -> None:
    """_to_sse uses 'daemon_event' when no event_type/type key present."""
    raw = {"foo": "bar"}
    sse = DaemonEventBridge._to_sse(raw)
    assert sse["event"] == "daemon_event"


def test_to_sse_uses_type_key() -> None:
    """_to_sse falls back to 'type' key when 'event_type' is absent."""
    raw = {"type": "custom.event", "value": 42}
    sse = DaemonEventBridge._to_sse(raw)
    assert sse["event"] == "custom.event"


# ============================================================================
# DaemonEventBridge — job_events streaming
# ============================================================================


@pytest.mark.asyncio
async def test_job_events_yields_new_events(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """job_events yields SSE dicts for new events from the daemon."""
    call_count = 0

    async def mock_call(method: str, params: dict[str, Any] | None = None) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                {"event_type": "sheet.started", "timestamp": 100.0, "sheet": 1},
            ]
        # Second poll: raise to stop the test
        raise asyncio.CancelledError

    mock_client.call = mock_call  # type: ignore[assignment]

    events_collected: list[dict[str, Any]] = []
    try:
        async for evt in bridge.job_events("job-1"):
            events_collected.append(evt)
            break  # Just collect the first event
    except asyncio.CancelledError:
        pass

    assert len(events_collected) == 1
    assert events_collected[0]["event"] == "sheet.started"


@pytest.mark.asyncio
async def test_job_events_handles_poll_error(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """job_events continues polling after IPC errors."""
    call_count = 0

    async def mock_call(method: str, params: dict[str, Any] | None = None) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("daemon down")
        if call_count == 2:
            return [{"event_type": "recovered", "timestamp": 200.0}]
        raise asyncio.CancelledError

    mock_client.call = mock_call  # type: ignore[assignment]

    events_collected: list[dict[str, Any]] = []
    try:
        async for evt in bridge.job_events("job-err"):
            events_collected.append(evt)
            break
    except asyncio.CancelledError:
        pass

    assert len(events_collected) == 1
    assert events_collected[0]["event"] == "recovered"


# ============================================================================
# DaemonEventBridge — all_events streaming
# ============================================================================


@pytest.mark.asyncio
async def test_all_events_polls_all_jobs(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """all_events iterates over jobs from list_jobs and polls each."""
    mock_client.list_jobs.return_value = [
        {"job_id": "j1"},
        {"job_id": "j2"},
    ]

    call_results = {
        "j1": [{"event_type": "sheet.completed", "timestamp": 100.0, "job_id": "j1"}],
        "j2": [{"event_type": "sheet.started", "timestamp": 200.0, "job_id": "j2"}],
    }
    call_count = 0

    async def mock_call(method: str, params: dict[str, Any] | None = None) -> Any:
        nonlocal call_count
        call_count += 1
        if method == "daemon.observer_events" and params:
            jid = params.get("job_id", "")
            return call_results.get(jid, [])
        return []

    mock_client.call = mock_call  # type: ignore[assignment]

    events_collected: list[dict[str, Any]] = []
    count = 0
    async for evt in bridge.all_events(limit=50):
        events_collected.append(evt)
        count += 1
        if count >= 2:
            break

    assert len(events_collected) == 2
    # Events are sorted by timestamp
    assert events_collected[0]["event"] == "sheet.completed"
    assert events_collected[1]["event"] == "sheet.started"


@pytest.mark.asyncio
async def test_all_events_handles_job_id_key_variants(
    bridge: DaemonEventBridge, mock_client: AsyncMock,
) -> None:
    """all_events handles both 'job_id' and 'id' keys in job listings."""
    mock_client.list_jobs.return_value = [
        {"id": "alt-id"},
    ]

    async def mock_call(method: str, params: dict[str, Any] | None = None) -> Any:
        if method == "daemon.observer_events" and params:
            if params.get("job_id") == "alt-id":
                return [{"event_type": "found", "timestamp": 300.0}]
        return []

    mock_client.call = mock_call  # type: ignore[assignment]

    events_collected: list[dict[str, Any]] = []
    async for evt in bridge.all_events():
        events_collected.append(evt)
        break

    assert len(events_collected) == 1
    assert events_collected[0]["event"] == "found"


# ============================================================================
# Events route module
# ============================================================================


def test_format_sse_produces_valid_wire_format() -> None:
    """_format_sse creates proper SSE wire format."""
    from mozart.dashboard.routes.events import _format_sse

    sse = {"event": "test.event", "data": '{"key": "value"}'}
    formatted = _format_sse(sse)

    assert "event: test.event\n" in formatted
    assert 'data: {"key": "value"}\n' in formatted
    assert formatted.endswith("\n\n")


def test_format_sse_default_event() -> None:
    """_format_sse uses 'message' as default event type."""
    from mozart.dashboard.routes.events import _format_sse

    formatted = _format_sse({"data": "{}"})
    assert "event: message\n" in formatted


def test_set_and_get_event_bridge() -> None:
    """set_event_bridge/get_event_bridge round-trips correctly."""
    from mozart.dashboard.routes.events import get_event_bridge, set_event_bridge

    client = AsyncMock()
    bridge = DaemonEventBridge(client=client)
    set_event_bridge(bridge)
    try:
        assert get_event_bridge() is bridge
    finally:
        set_event_bridge(None)  # type: ignore[arg-type]


def test_get_event_bridge_raises_when_not_configured() -> None:
    """get_event_bridge raises RuntimeError before set_event_bridge."""
    from mozart.dashboard.routes.events import get_event_bridge, set_event_bridge

    set_event_bridge(None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="DaemonEventBridge not configured"):
        get_event_bridge()
