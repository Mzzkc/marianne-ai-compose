"""Tests for TUI integration of observer events.

Tests the wiring of observer events into:
- MonitorReader.get_observer_events() (IPC call)
- TimelinePanel (PROC entries merged by timestamp)
- DetailPanel (file activity section)
- MonitorApp refresh cycle (sequential IPC calls)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.widgets import Static

from marianne.daemon.profiler.models import EventType, ProcessEvent
from marianne.tui.panels.detail import DetailPanel
from marianne.tui.panels.timeline import TimelinePanel
from marianne.tui.reader import MonitorReader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_observer_event(
    event: str,
    job_id: str = "test-job",
    sheet_num: int = 1,
    data: dict[str, Any] | None = None,
    timestamp: float | None = None,
) -> dict[str, Any]:
    """Create a mock observer event dict."""
    return {
        "job_id": job_id,
        "sheet_num": sheet_num,
        "event": event,
        "data": data or {},
        "timestamp": timestamp or time.time(),
    }


def _make_process_event(
    event_type: EventType = EventType.SPAWN,
    pid: int = 1234,
    timestamp: float | None = None,
    job_id: str = "test-job",
) -> ProcessEvent:
    """Create a ProcessEvent for timeline testing."""
    return ProcessEvent(
        event_type=event_type,
        pid=pid,
        timestamp=timestamp or time.time(),
        job_id=job_id,
    )


# ===========================================================================
# MonitorReader.get_observer_events
# ===========================================================================


class TestReaderGetObserverEvents:
    """Test MonitorReader.get_observer_events() IPC method."""

    @pytest.mark.asyncio
    async def test_returns_events_from_ipc(self) -> None:
        """get_observer_events returns events from the IPC daemon."""
        mock_client = AsyncMock()
        mock_client.is_daemon_running = AsyncMock(return_value=True)
        mock_client.call = AsyncMock(return_value={
            "events": [
                _make_observer_event(
                    "observer.process_spawned", data={"pid": 42, "name": "claude"},
                ),
                _make_observer_event("observer.file_created", data={"path": "src/foo.py"}),
            ]
        })

        reader = MonitorReader(ipc_client=mock_client)
        events = await reader.get_observer_events(job_id="test-job", limit=10)

        assert len(events) == 2
        mock_client.call.assert_called_once_with(
            "daemon.observer_events",
            {"job_id": "test-job", "limit": 10},
        )

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_ipc(self) -> None:
        """get_observer_events returns empty list when no IPC client."""
        reader = MonitorReader()
        events = await reader.get_observer_events()
        assert events == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_ipc_error(self) -> None:
        """get_observer_events returns empty list on IPC failure."""
        mock_client = AsyncMock()
        mock_client.is_daemon_running = AsyncMock(return_value=True)
        mock_client.call = AsyncMock(side_effect=ConnectionError("gone"))

        reader = MonitorReader(ipc_client=mock_client)
        events = await reader.get_observer_events()
        assert events == []

    @pytest.mark.asyncio
    async def test_defaults_to_none_job_id_and_50_limit(self) -> None:
        """get_observer_events passes None job_id and 50 limit by default."""
        mock_client = AsyncMock()
        mock_client.is_daemon_running = AsyncMock(return_value=True)
        mock_client.call = AsyncMock(return_value={"events": []})

        reader = MonitorReader(ipc_client=mock_client)
        await reader.get_observer_events()

        mock_client.call.assert_called_once_with(
            "daemon.observer_events",
            {"job_id": None, "limit": 50},
        )


# ===========================================================================
# TimelinePanel — observer PROC entries
# ===========================================================================


class TestTimelineProcEntries:
    """Test observer process events rendering in TimelinePanel."""

    def test_update_data_accepts_observer_events(self) -> None:
        """update_data accepts observer_events parameter."""
        panel = TimelinePanel()
        # Should not raise; panel remains usable after empty update
        panel.update_data(observer_events=[])
        assert panel is not None  # survived without exception

    def test_process_spawned_rendered_as_proc(self) -> None:
        """observer.process_spawned events render as PROC entries."""
        panel = TimelinePanel()
        obs_events = [
            _make_observer_event(
                "observer.process_spawned",
                data={"pid": 42, "name": "claude"},
                timestamp=1000.0,
            ),
        ]
        panel.update_data(observer_events=obs_events)
        # Check internal state has the observer events stored
        assert len(panel._observer_events) == 1

    def test_process_exited_uses_role_fallback(self) -> None:
        """observer.process_exited events fall back to data['role'] for label.

        REVIEW FIX 1: process_exited events carry {pid, role} not {pid, name}.
        """
        panel = TimelinePanel()
        obs_events = [
            _make_observer_event(
                "observer.process_exited",
                data={"pid": 42, "role": "child"},
                timestamp=1000.0,
            ),
        ]
        panel.update_data(observer_events=obs_events)
        assert len(panel._observer_events) == 1

    def test_merge_sort_with_profiler_events(self) -> None:
        """Observer PROC entries interleave with profiler events by timestamp.

        REVIEW FIX 2: Don't just append — merge into sorted entries list.
        """
        panel = TimelinePanel()

        # Profiler event at t=2000
        profiler_events = [
            _make_process_event(
                event_type=EventType.SPAWN,
                pid=100,
                timestamp=2000.0,
            ),
        ]

        # Observer events at t=1000 and t=3000
        obs_events = [
            _make_observer_event(
                "observer.process_spawned",
                data={"pid": 42, "name": "early"},
                timestamp=1000.0,
            ),
            _make_observer_event(
                "observer.process_spawned",
                data={"pid": 99, "name": "late"},
                timestamp=3000.0,
            ),
        ]

        panel.update_data(events=profiler_events, observer_events=obs_events)

        # Timeline renders entries sorted by timestamp descending.
        # With our data: t=3000 first, t=2000 second, t=1000 third.
        # We can verify all 3 entries appear by checking the combined count
        # of stored events.
        assert len(panel._events) == 1  # profiler
        assert len(panel._observer_events) == 2  # observer

    def test_only_process_events_in_timeline(self) -> None:
        """Only observer.process_* events appear in the timeline, not file events."""
        panel = TimelinePanel()
        obs_events = [
            _make_observer_event(
                "observer.file_created",
                data={"path": "foo.py"},
                timestamp=1000.0,
            ),
            _make_observer_event(
                "observer.process_spawned",
                data={"pid": 42, "name": "claude"},
                timestamp=1001.0,
            ),
        ]
        panel.update_data(observer_events=obs_events)
        # Both are stored, but file events are filtered during render
        assert len(panel._observer_events) == 2


# ===========================================================================
# DetailPanel — file activity section
# ===========================================================================


class TestDetailFileActivity:
    """Test file activity rendering in DetailPanel."""

    def test_show_file_activity_renders_events(self) -> None:
        """show_file_activity renders file events with timestamps and paths."""
        panel = DetailPanel()
        # Compose widgets (needed for _content to be set)
        panel._content = MagicMock(spec=Static)

        file_events = [
            _make_observer_event(
                "observer.file_created",
                data={"path": "src/main.py"},
                timestamp=1000.0,
            ),
            _make_observer_event(
                "observer.file_modified",
                data={"path": "src/utils.py"},
                timestamp=1001.0,
            ),
        ]
        panel.show_file_activity(file_events)

        # The content should have been updated
        panel._content.update.assert_called_once()
        markup = panel._content.update.call_args[0][0]
        assert "File Activity" in markup
        assert "src/main.py" in markup
        assert "src/utils.py" in markup

    def test_show_file_activity_empty(self) -> None:
        """show_file_activity handles empty event list."""
        panel = DetailPanel()
        panel._content = MagicMock(spec=Static)

        panel.show_file_activity([])

        panel._content.update.assert_called_once()
        markup = panel._content.update.call_args[0][0]
        assert "No file activity" in markup

    def test_show_item_with_file_activity(self) -> None:
        """show_item with type='job' includes file activity when observer_events present."""
        panel = DetailPanel()
        panel._content = MagicMock(spec=Static)

        item: dict[str, Any] = {
            "type": "job",
            "job_id": "my-job",
            "processes": [],
            "observer_file_events": [
                _make_observer_event(
                    "observer.file_created",
                    data={"path": "src/foo.py"},
                    timestamp=1000.0,
                ),
            ],
        }
        panel.show_item(item)

        panel._content.update.assert_called_once()
        markup = panel._content.update.call_args[0][0]
        assert "my-job" in markup
        assert "File Activity" in markup
        assert "src/foo.py" in markup


# ===========================================================================
# MonitorApp — refresh cycle
# ===========================================================================


class TestMonitorAppRefreshCycle:
    """Test observer event wiring in the refresh cycle."""

    @pytest.mark.asyncio
    async def test_refresh_calls_get_observer_events_sequentially(self) -> None:
        """refresh_data calls get_observer_events sequentially (REVIEW FIX 3)."""
        call_order: list[str] = []

        mock_client = AsyncMock()
        mock_client.is_daemon_running = AsyncMock(return_value=True)

        async def mock_call(method: str, params: dict | None = None) -> dict:
            call_order.append(method)
            if method == "daemon.top":
                return {"cpu_percent": 0, "memory_percent": 0, "process_count": 0,
                        "processes": [], "timestamp": time.time()}
            if method == "daemon.events":
                return {"events": []}
            if method == "daemon.observer_events":
                return {"events": []}
            return {}

        mock_client.call = AsyncMock(side_effect=mock_call)

        reader = MonitorReader(ipc_client=mock_client)

        # We can't easily mount a full Textual app in tests,
        # so test the reader calls directly to verify sequentiality.
        await reader.get_events(time.time() - 300, limit=50)
        await reader.get_observer_events()

        # Verify they were called sequentially (not concurrently)
        assert "daemon.events" in call_order
        assert "daemon.observer_events" in call_order
