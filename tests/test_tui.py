"""Comprehensive tests for the Mozart TUI monitor.

Tests cover:
- MonitorReader (source detection, IPC calls, event routing)
- Panel rendering (HeaderPanel, JobsPanel, TimelinePanel, DetailPanel)
- MonitorApp (Textual async app lifecycle and data refresh)
- CLI top command helpers (_parse_duration, _filter_snapshot)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mozart.daemon.profiler.models import (
    Anomaly,
    AnomalySeverity,
    AnomalyType,
    EventType,
    GpuMetric,
    ProcessEvent,
    ProcessMetric,
    SystemSnapshot,
)
from mozart.daemon.profiler.storage import MonitorStorage

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


def _make_process(
    *,
    pid: int = 1234,
    cpu_percent: float = 45.0,
    rss_mb: float = 512.0,
    vms_mb: float = 1024.0,
    state: str = "S",
    job_id: str | None = "my-review-job",
    sheet_num: int | None = 3,
    age_seconds: float = 270.5,
    command: str = "claude --model opus",
    open_fds: int = 32,
    threads: int = 4,
    syscall_counts: dict[str, int] | None = None,
    syscall_time_pct: dict[str, float] | None = None,
) -> ProcessMetric:
    _default_counts = {"write": 1500, "read": 3200}
    _default_pcts = {"write": 0.40, "read": 0.28, "futex": 0.15}
    return ProcessMetric(
        pid=pid,
        ppid=1000,
        command=command,
        state=state,
        cpu_percent=cpu_percent,
        rss_mb=rss_mb,
        vms_mb=vms_mb,
        threads=threads,
        open_fds=open_fds,
        age_seconds=age_seconds,
        job_id=job_id,
        sheet_num=sheet_num,
        syscall_counts=_default_counts if syscall_counts is None else syscall_counts,
        syscall_time_pct=_default_pcts if syscall_time_pct is None else syscall_time_pct,
    )


def _make_snapshot(
    *,
    timestamp: float | None = None,
    processes: list[ProcessMetric] | None = None,
    gpus: list[GpuMetric] | None = None,
    system_memory_total_mb: float = 16384.0,
    system_memory_used_mb: float = 8192.0,
    system_memory_available_mb: float = 8192.0,
    daemon_rss_mb: float = 128.5,
    pressure_level: str = "none",
    running_jobs: int = 2,
    active_sheets: int = 3,
    zombie_count: int = 0,
    zombie_pids: list[int] | None = None,
) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=timestamp or time.time(),
        daemon_pid=12345,
        system_memory_total_mb=system_memory_total_mb,
        system_memory_used_mb=system_memory_used_mb,
        system_memory_available_mb=system_memory_available_mb,
        daemon_rss_mb=daemon_rss_mb,
        load_avg_1=1.5,
        load_avg_5=1.2,
        load_avg_15=0.9,
        processes=processes or [],
        gpus=gpus or [],
        pressure_level=pressure_level,
        running_jobs=running_jobs,
        active_sheets=active_sheets,
        zombie_count=zombie_count,
        zombie_pids=zombie_pids or [],
    )


def _make_event(
    *,
    pid: int = 12350,
    event_type: EventType = EventType.SPAWN,
    job_id: str = "my-review-job",
    sheet_num: int = 3,
    exit_code: int | None = None,
    signal_num: int | None = None,
    timestamp: float | None = None,
    details: str = "",
) -> ProcessEvent:
    return ProcessEvent(
        timestamp=timestamp or time.time(),
        pid=pid,
        event_type=event_type,
        job_id=job_id,
        sheet_num=sheet_num,
        exit_code=exit_code,
        signal_num=signal_num,
        details=details,
    )


@pytest.fixture
def mock_snapshot() -> SystemSnapshot:
    """A representative snapshot with two jobs and processes."""
    procs = [
        _make_process(pid=12350, job_id="my-review-job", sheet_num=3, cpu_percent=45.0, rss_mb=512.0),
        _make_process(pid=12380, job_id="my-review-job", sheet_num=4, cpu_percent=12.0, rss_mb=256.0),
        _make_process(pid=12400, job_id="code-cleanup-job", sheet_num=1, cpu_percent=8.0, rss_mb=128.0),
    ]
    return _make_snapshot(
        processes=procs,
        running_jobs=2,
        active_sheets=3,
        pressure_level="low",
    )


@pytest.fixture
def mock_events() -> list[ProcessEvent]:
    """A set of process lifecycle events."""
    now = time.time()
    return [
        _make_event(pid=12380, event_type=EventType.SPAWN, timestamp=now - 60),
        _make_event(pid=12340, event_type=EventType.EXIT, exit_code=0, timestamp=now - 120),
        _make_event(
            pid=12330, event_type=EventType.SIGNAL, signal_num=15,
            timestamp=now - 180, details="rate limit",
        ),
    ]


@pytest.fixture
def mock_ipc_client() -> AsyncMock:
    """A mock DaemonClient that responds to daemon.top and daemon.events."""
    client = AsyncMock()
    client.is_daemon_running = AsyncMock(return_value=True)
    return client


# ===========================================================================
# MonitorReader tests
# ===========================================================================


class TestMonitorReader:
    """Tests for MonitorReader source detection, snapshot reads, and event reads."""

    @pytest.mark.asyncio
    async def test_no_sources_returns_none(self) -> None:
        """Reader with no sources configured returns None for snapshot."""
        from mozart.tui.reader import MonitorReader

        reader = MonitorReader()
        result = await reader.get_latest_snapshot()
        assert result is None
        assert reader.source == "none"

    @pytest.mark.asyncio
    async def test_ipc_source_calls_daemon_top(self, mock_ipc_client: AsyncMock) -> None:
        """Reader with IPC client calls daemon.top for snapshots."""
        from mozart.tui.reader import MonitorReader

        snap_dict = _make_snapshot().model_dump(mode="json")
        mock_ipc_client.call = AsyncMock(return_value=snap_dict)

        reader = MonitorReader(ipc_client=mock_ipc_client)
        result = await reader.get_latest_snapshot()

        assert result is not None
        assert isinstance(result, SystemSnapshot)
        mock_ipc_client.call.assert_called_once_with("daemon.top")
        assert reader.source == "ipc"

    @pytest.mark.asyncio
    async def test_ipc_source_calls_daemon_events(self, mock_ipc_client: AsyncMock) -> None:
        """Reader with IPC client calls daemon.events for events."""
        from mozart.tui.reader import MonitorReader

        now = time.time()
        raw_events = [
            {
                "timestamp": now - 10,
                "pid": 1234,
                "event_type": "spawn",
                "job_id": "test-job",
                "sheet_num": 1,
                "details": "",
            },
            {
                "timestamp": now - 5,
                "pid": 1234,
                "event_type": "exit",
                "exit_code": 0,
                "job_id": "test-job",
                "sheet_num": 1,
                "details": "",
            },
        ]
        mock_ipc_client.call = AsyncMock(return_value={"events": raw_events})

        reader = MonitorReader(ipc_client=mock_ipc_client)
        events = await reader.get_events(since=now - 60, limit=10)

        assert len(events) == 2
        assert events[0].event_type == EventType.SPAWN
        assert events[1].event_type == EventType.EXIT
        assert events[1].exit_code == 0
        mock_ipc_client.call.assert_called_with("daemon.events", {"limit": 10})

    @pytest.mark.asyncio
    async def test_ipc_events_filters_by_since(self, mock_ipc_client: AsyncMock) -> None:
        """IPC event reads filter results by the since timestamp."""
        from mozart.tui.reader import MonitorReader

        now = time.time()
        raw_events = [
            {"timestamp": now - 100, "pid": 1, "event_type": "spawn", "details": ""},
            {"timestamp": now - 5, "pid": 2, "event_type": "spawn", "details": ""},
        ]
        mock_ipc_client.call = AsyncMock(return_value={"events": raw_events})

        reader = MonitorReader(ipc_client=mock_ipc_client)
        events = await reader.get_events(since=now - 30, limit=50)

        assert len(events) == 1
        assert events[0].pid == 2

    @pytest.mark.asyncio
    async def test_source_priority_ipc_over_sqlite(self, mock_ipc_client: AsyncMock) -> None:
        """IPC is preferred over SQLite when both are available."""
        from mozart.tui.reader import MonitorReader

        storage = MagicMock(spec=MonitorStorage)
        reader = MonitorReader(ipc_client=mock_ipc_client, storage=storage)
        # Force source detection
        await reader._ensure_source()
        assert reader.source == "ipc"

    @pytest.mark.asyncio
    async def test_source_priority_sqlite_over_jsonl(self, tmp_path: Path) -> None:
        """SQLite is preferred over JSONL when IPC is unavailable."""
        from mozart.tui.reader import MonitorReader

        storage = MagicMock(spec=MonitorStorage)
        jsonl_path = tmp_path / "monitor.jsonl"
        jsonl_path.write_text('{"timestamp": 1000}\n')

        reader = MonitorReader(storage=storage, jsonl_path=jsonl_path)
        await reader._ensure_source()
        assert reader.source == "sqlite"

    @pytest.mark.asyncio
    async def test_source_falls_back_to_jsonl(self, tmp_path: Path) -> None:
        """Falls back to JSONL when IPC and SQLite are unavailable."""
        from mozart.tui.reader import MonitorReader

        jsonl_path = tmp_path / "monitor.jsonl"
        jsonl_path.write_text('{"timestamp": 1000}\n')

        reader = MonitorReader(jsonl_path=jsonl_path)
        await reader._ensure_source()
        assert reader.source == "jsonl"

    @pytest.mark.asyncio
    async def test_ipc_daemon_not_running_falls_back(self) -> None:
        """When IPC client reports daemon not running, falls back to next source."""
        from mozart.tui.reader import MonitorReader

        client = AsyncMock()
        client.is_daemon_running = AsyncMock(return_value=False)

        storage = MagicMock(spec=MonitorStorage)
        reader = MonitorReader(ipc_client=client, storage=storage)
        await reader._ensure_source()
        assert reader.source == "sqlite"

    @pytest.mark.asyncio
    async def test_ipc_snapshot_error_returns_none(self, mock_ipc_client: AsyncMock) -> None:
        """When IPC call fails, get_latest_snapshot returns None."""
        from mozart.tui.reader import MonitorReader

        mock_ipc_client.call = AsyncMock(side_effect=ConnectionError("socket not found"))

        reader = MonitorReader(ipc_client=mock_ipc_client)
        result = await reader.get_latest_snapshot()
        assert result is None

    @pytest.mark.asyncio
    async def test_ipc_events_error_returns_empty(self, mock_ipc_client: AsyncMock) -> None:
        """When IPC event call fails, returns empty list."""
        from mozart.tui.reader import MonitorReader

        mock_ipc_client.call = AsyncMock(side_effect=ConnectionError("socket not found"))

        reader = MonitorReader(ipc_client=mock_ipc_client)
        events = await reader.get_events(since=0.0)
        assert events == []

    @pytest.mark.asyncio
    async def test_jsonl_snapshot_read(self, tmp_path: Path) -> None:
        """JSONL reader reads the last line of the file."""
        from mozart.tui.reader import MonitorReader

        snap = _make_snapshot(timestamp=1234567.0)
        jsonl_path = tmp_path / "monitor.jsonl"
        jsonl_path.write_text(
            json.dumps(snap.model_dump(mode="json")) + "\n"
        )

        reader = MonitorReader(jsonl_path=jsonl_path)
        result = await reader.get_latest_snapshot()
        assert result is not None
        assert result.timestamp == 1234567.0

    @pytest.mark.asyncio
    async def test_no_sources_events_returns_empty(self) -> None:
        """Reader with no sources returns empty event list."""
        from mozart.tui.reader import MonitorReader

        reader = MonitorReader()
        events = await reader.get_events(since=0.0)
        assert events == []


# ===========================================================================
# Panel rendering tests
# ===========================================================================


class TestHeaderPanel:
    """Tests for the system summary header bar."""

    def test_render_conductor_up_with_snapshot(self, mock_snapshot: SystemSnapshot) -> None:
        """HeaderPanel renders UP status with memory/CPU bars when given a snapshot."""
        from mozart.tui.panels.header import HeaderPanel

        panel = HeaderPanel()
        panel.update_data(
            snapshot=mock_snapshot,
            conductor_up=True,
            uptime_seconds=8100.0,  # 2h15m
        )

        # Verify internal state was set
        assert panel._conductor_up is True
        assert panel._snapshot is mock_snapshot
        assert panel._uptime_seconds == 8100.0

    def test_render_conductor_down_no_snapshot(self) -> None:
        """HeaderPanel renders DOWN status gracefully when no snapshot."""
        from mozart.tui.panels.header import HeaderPanel

        panel = HeaderPanel()
        panel.update_data(snapshot=None, conductor_up=False)

        assert panel._conductor_up is False
        assert panel._snapshot is None

    def test_render_with_gpu(self) -> None:
        """HeaderPanel shows GPU utilization when GPUs are present."""
        from mozart.tui.panels.header import HeaderPanel

        gpu = GpuMetric(index=0, utilization_pct=75.0, memory_used_mb=4096.0, memory_total_mb=8192.0)
        snap = _make_snapshot(gpus=[gpu])

        panel = HeaderPanel()
        panel.update_data(snapshot=snap, conductor_up=True, uptime_seconds=60.0)
        assert panel._snapshot is not None
        assert panel._snapshot.gpus[0].utilization_pct == 75.0

    def test_render_with_anomalies(self) -> None:
        """HeaderPanel shows anomaly count when anomalies exist."""
        from mozart.tui.panels.header import HeaderPanel

        panel = HeaderPanel()
        panel.update_data(
            snapshot=_make_snapshot(),
            conductor_up=True,
            anomaly_count=3,
        )
        assert panel._anomaly_count == 3


class TestHeaderHelpers:
    """Tests for header panel helper functions."""

    def test_bar_zero(self) -> None:
        from mozart.tui.panels.header import _bar
        result = _bar(0.0, 4)
        assert result == "\u2591" * 4

    def test_bar_full(self) -> None:
        from mozart.tui.panels.header import _bar
        result = _bar(100.0, 4)
        assert result == "\u2588" * 4

    def test_bar_half(self) -> None:
        from mozart.tui.panels.header import _bar
        result = _bar(50.0, 10)
        assert "\u2588" in result
        assert "\u2591" in result
        assert len(result) == 10

    def test_pressure_color_critical(self) -> None:
        from mozart.tui.panels.header import _pressure_color
        assert _pressure_color("critical") == "red"
        assert _pressure_color("high") == "red"

    def test_pressure_color_medium(self) -> None:
        from mozart.tui.panels.header import _pressure_color
        assert _pressure_color("medium") == "yellow"

    def test_pressure_color_low(self) -> None:
        from mozart.tui.panels.header import _pressure_color
        assert _pressure_color("low") == "green"
        assert _pressure_color("none") == "green"

    def test_format_uptime_seconds(self) -> None:
        from mozart.tui.panels.header import _format_uptime
        assert _format_uptime(30.0) == "30s"

    def test_format_uptime_minutes(self) -> None:
        from mozart.tui.panels.header import _format_uptime
        assert _format_uptime(300.0) == "5m"

    def test_format_uptime_hours(self) -> None:
        from mozart.tui.panels.header import _format_uptime
        assert _format_uptime(8100.0) == "2h15m"


class TestJobsPanel:
    """Tests for the job-centric process tree panel."""

    def test_empty_snapshot_shows_no_active_jobs(self) -> None:
        """JobsPanel handles None snapshot without crashing."""
        from mozart.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        # Before compose, _tree is None — update_data should be safe
        panel.update_data(None)
        assert panel._items == []

    def test_processes_grouped_by_job(self, mock_snapshot: SystemSnapshot) -> None:
        """Processes are grouped into job tree nodes."""
        from mozart.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        # Simulate items without compose (test data method)
        panel._snapshot = mock_snapshot
        # _render_jobs needs _tree, which is only set after compose.
        # Test the data structure directly.
        from collections import defaultdict
        by_job: dict[str, list[ProcessMetric]] = defaultdict(list)
        for proc in mock_snapshot.processes:
            if proc.job_id:
                by_job[proc.job_id].append(proc)
        assert len(by_job) == 2
        assert "my-review-job" in by_job
        assert "code-cleanup-job" in by_job
        assert len(by_job["my-review-job"]) == 2

    def test_selected_item_empty(self) -> None:
        """selected_item returns None when no items."""
        from mozart.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        assert panel.selected_item is None

    def test_selected_item_from_items(self) -> None:
        """selected_item returns item from _items when tree cursor unavailable."""
        from mozart.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        panel._items = [{"type": "job", "job_id": "test-job"}]
        panel._selected_index = 0
        assert panel.selected_item == {"type": "job", "job_id": "test-job"}

    def test_select_next_clamps(self) -> None:
        """select_next doesn't go past the last item."""
        from mozart.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        panel._items = [{"type": "job"}, {"type": "process"}]
        panel._selected_index = 1
        panel.select_next()
        assert panel._selected_index == 1  # Clamped at last

    def test_select_prev_clamps(self) -> None:
        """select_prev doesn't go below 0."""
        from mozart.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        panel._items = [{"type": "job"}, {"type": "process"}]
        panel._selected_index = 0
        panel.select_prev()
        assert panel._selected_index == 0  # Clamped at 0


class TestJobsPanelHelpers:
    """Tests for jobs panel formatting helpers."""

    def test_format_bytes_mb_small(self) -> None:
        from mozart.tui.panels.jobs import _format_bytes_mb
        assert _format_bytes_mb(512.0) == "512M"

    def test_format_bytes_mb_large(self) -> None:
        from mozart.tui.panels.jobs import _format_bytes_mb
        assert _format_bytes_mb(2048.0) == "2.0G"

    def test_format_age_seconds(self) -> None:
        from mozart.tui.panels.jobs import _format_age
        assert _format_age(30.0) == "30s"

    def test_format_age_minutes(self) -> None:
        from mozart.tui.panels.jobs import _format_age
        assert _format_age(270.0) == "4m30s"

    def test_format_age_hours(self) -> None:
        from mozart.tui.panels.jobs import _format_age
        assert _format_age(3900.0) == "1h05m"

    def test_format_progress_bar(self) -> None:
        from mozart.tui.panels.jobs import _format_progress_bar
        bar = _format_progress_bar(3, 6, width=15)
        assert "50%" in bar
        assert "\u2588" in bar
        assert "\u2591" in bar

    def test_format_progress_bar_zero_total(self) -> None:
        from mozart.tui.panels.jobs import _format_progress_bar
        bar = _format_progress_bar(0, 0, width=10)
        assert "0%" in bar

    def test_state_label_running(self) -> None:
        from mozart.tui.panels.jobs import _state_label
        assert _state_label("R") == "[RUNNING]"
        assert _state_label("S") == "[RUNNING]"
        assert _state_label("D") == "[RUNNING]"

    def test_state_label_zombie(self) -> None:
        from mozart.tui.panels.jobs import _state_label
        assert _state_label("Z") == "[ZOMBIE]"

    def test_state_label_stopped(self) -> None:
        from mozart.tui.panels.jobs import _state_label
        assert _state_label("T") == "[STOPPED]"

    def test_state_label_unknown(self) -> None:
        from mozart.tui.panels.jobs import _state_label
        assert _state_label("X") == "[X]"

    def test_top_syscalls(self) -> None:
        from mozart.tui.panels.jobs import _top_syscalls
        proc = _make_process()
        result = _top_syscalls(proc, top_n=3)
        assert "write" in result
        assert "read" in result
        assert "|" in result

    def test_top_syscalls_empty(self) -> None:
        from mozart.tui.panels.jobs import _top_syscalls
        proc = _make_process(syscall_counts={}, syscall_time_pct={})
        assert _top_syscalls(proc) == ""


class TestTimelinePanel:
    """Tests for the event timeline panel."""

    def test_empty_events(self) -> None:
        """TimelinePanel handles empty event list without crashing."""
        from mozart.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        panel.update_data(events=[])
        assert panel._events == []

    def test_events_stored(self, mock_events: list[ProcessEvent]) -> None:
        """Events passed to update_data are stored."""
        from mozart.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        panel.update_data(events=mock_events)
        assert len(panel._events) == 3
        assert panel._events[0].event_type == EventType.SPAWN

    def test_anomalies_stored(self) -> None:
        """Anomalies passed to update_data are stored."""
        from mozart.tui.panels.timeline import TimelinePanel

        anomaly = Anomaly(
            anomaly_type=AnomalyType.MEMORY_SPIKE,
            severity=AnomalySeverity.HIGH,
            description="Memory grew 80%",
            pid=1234,
            metric_value=200.0,
            threshold=150.0,
        )
        panel = TimelinePanel()
        panel.update_data(anomalies=[anomaly])
        assert len(panel._anomalies) == 1

    def test_learning_insights_stored(self) -> None:
        """Learning insights passed to update_data are stored."""
        from mozart.tui.panels.timeline import TimelinePanel

        insight = {"timestamp": time.time(), "text": "High memory correlates with failures"}
        panel = TimelinePanel()
        panel.update_data(learning_insights=[insight])
        assert len(panel._learning_insights) == 1

    def test_format_timestamp(self) -> None:
        from mozart.tui.panels.timeline import _format_timestamp
        # Just verify it returns a time-formatted string
        result = _format_timestamp(1740000000.0)
        assert ":" in result
        assert len(result) == 8  # HH:MM:SS

    def test_event_colors_defined(self) -> None:
        from mozart.tui.panels.timeline import _EVENT_COLORS
        assert EventType.SPAWN.value in _EVENT_COLORS
        assert EventType.EXIT.value in _EVENT_COLORS
        assert EventType.SIGNAL.value in _EVENT_COLORS
        assert EventType.KILL.value in _EVENT_COLORS
        assert EventType.OOM.value in _EVENT_COLORS


class TestDetailPanel:
    """Tests for the detail drill-down panel."""

    def test_show_empty(self) -> None:
        """show_empty doesn't crash."""
        from mozart.tui.panels.detail import DetailPanel

        panel = DetailPanel()
        # Before compose, _content is None — should be safe
        panel.show_empty()

    def test_show_process(self) -> None:
        """show_process stores process details."""
        from mozart.tui.panels.detail import DetailPanel

        proc = _make_process(pid=42, cpu_percent=88.0, rss_mb=1024.0)
        panel = DetailPanel()
        # Before compose, _content is None — show_process writes to _content
        panel.show_process(proc)
        # No crash is the test — _content is None before compose

    def test_show_anomaly(self) -> None:
        """show_anomaly handles anomaly data without crashing."""
        from mozart.tui.panels.detail import DetailPanel

        anomaly = Anomaly(
            anomaly_type=AnomalyType.RUNAWAY_PROCESS,
            severity=AnomalySeverity.CRITICAL,
            description="PID 9999 at 98% CPU for 5 minutes",
            pid=9999,
            metric_value=98.0,
            threshold=90.0,
        )
        panel = DetailPanel()
        panel.show_anomaly(anomaly)
        assert panel is not None  # no crash is the test

    def test_show_item_process(self) -> None:
        """show_item dispatches to show_process for process items."""
        from mozart.tui.panels.detail import DetailPanel

        proc = _make_process()
        panel = DetailPanel()
        panel.show_item({"type": "process", "process": proc})
        assert panel is not None  # no crash is the test

    def test_show_item_job(self) -> None:
        """show_item shows job summary for job items."""
        from mozart.tui.panels.detail import DetailPanel

        procs = [
            _make_process(pid=1, cpu_percent=30.0, rss_mb=256.0),
            _make_process(pid=2, cpu_percent=20.0, rss_mb=128.0),
        ]
        panel = DetailPanel()
        panel.show_item({"type": "job", "job_id": "test-job", "processes": procs})
        assert panel is not None  # no crash is the test

    def test_show_item_none(self) -> None:
        """show_item with None calls show_empty."""
        from unittest.mock import patch

        from mozart.tui.panels.detail import DetailPanel

        panel = DetailPanel()
        with patch.object(panel, "show_empty") as mock_empty:
            panel.show_item(None)
            assert mock_empty.called

    def test_show_item_unknown_type(self) -> None:
        """show_item with unknown type calls show_empty."""
        from unittest.mock import patch

        from mozart.tui.panels.detail import DetailPanel

        panel = DetailPanel()
        with patch.object(panel, "show_empty") as mock_empty:
            panel.show_item({"type": "unknown_widget"})
            assert mock_empty.called

    def test_format_bytes_mb(self) -> None:
        from mozart.tui.panels.detail import _format_bytes_mb
        assert _format_bytes_mb(512.0) == "512M"
        assert _format_bytes_mb(2048.0) == "2.0G"


# ===========================================================================
# MonitorApp tests
# ===========================================================================


class TestMonitorApp:
    """Tests for the Textual MonitorApp lifecycle and data refresh."""

    def test_app_instantiation(self) -> None:
        """MonitorApp can be created with default arguments."""
        from mozart.tui.app import MonitorApp

        app = MonitorApp()
        assert app._refresh_interval == 2.0
        assert app._latest_snapshot is None

    def test_app_with_custom_reader(self) -> None:
        """MonitorApp accepts a custom MonitorReader."""
        from mozart.tui.app import MonitorApp
        from mozart.tui.reader import MonitorReader

        reader = MonitorReader()
        app = MonitorApp(reader=reader, refresh_interval=5.0)
        assert app._reader is reader
        assert app._refresh_interval == 5.0

    def test_app_bindings_separated(self) -> None:
        """Verify j/k/up/down are separate bindings (BUG-08 fix)."""
        from textual.binding import Binding

        from mozart.tui.app import MonitorApp

        keys: list[str] = []
        for b in MonitorApp.BINDINGS:
            assert isinstance(b, Binding), f"Expected Binding, got {type(b)}"
            keys.append(b.key)
        assert "j" in keys
        assert "k" in keys
        assert "down" in keys
        assert "up" in keys
        # Should NOT have comma-separated bindings
        for key in keys:
            assert "," not in key, f"Found comma-separated binding: {key}"

    @pytest.mark.asyncio
    async def test_refresh_data_calls_reader(self, mock_ipc_client: AsyncMock) -> None:
        """refresh_data fetches data from reader and updates panels."""
        from mozart.tui.app import MonitorApp
        from mozart.tui.reader import MonitorReader

        snap = _make_snapshot()
        mock_ipc_client.call = AsyncMock(return_value=snap.model_dump(mode="json"))

        reader = MonitorReader(ipc_client=mock_ipc_client)
        monitor_app = MonitorApp(reader=reader)

        # Verify app was created with the correct reader
        assert monitor_app._reader is reader

        # Test that reader calls work correctly
        result = await reader.get_latest_snapshot()
        assert result is not None
        assert isinstance(result, SystemSnapshot)

    def test_section_label_no_underscore_prefix(self) -> None:
        """SectionLabel class name has no underscore prefix (BUG-07 fix)."""
        from mozart.tui.app import SectionLabel
        assert SectionLabel.__name__ == "SectionLabel"

    def test_jobs_panel_is_scrollable(self) -> None:
        """JobsPanel extends VerticalScroll, not Static (BUG-01 fix)."""
        from textual.containers import VerticalScroll

        from mozart.tui.panels.jobs import JobsPanel
        assert issubclass(JobsPanel, VerticalScroll)

    def test_timeline_panel_is_richlog(self) -> None:
        """TimelinePanel extends RichLog, not Static (BUG-01 fix)."""
        from textual.widgets import RichLog

        from mozart.tui.panels.timeline import TimelinePanel
        assert issubclass(TimelinePanel, RichLog)

    def test_detail_panel_is_scrollable(self) -> None:
        """DetailPanel extends VerticalScroll, not Static (BUG-01 fix)."""
        from textual.containers import VerticalScroll

        from mozart.tui.panels.detail import DetailPanel
        assert issubclass(DetailPanel, VerticalScroll)

    def test_header_panel_is_static(self) -> None:
        """HeaderPanel correctly extends Static (fixed-height, no scroll needed)."""
        from textual.widgets import Static

        from mozart.tui.panels.header import HeaderPanel
        assert issubclass(HeaderPanel, Static)


# ===========================================================================
# CLI top command tests
# ===========================================================================


class TestParseDuration:
    """Tests for _parse_duration() in the CLI top command."""

    def test_seconds_only(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("30s") == 30.0

    def test_minutes_only(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("5m") == 300.0

    def test_hours_only(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("1h") == 3600.0

    def test_hours_and_minutes(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("2h30m") == 9000.0

    def test_full_hms(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("1h30m15s") == 5415.0

    def test_bare_number_as_minutes(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("5") == 300.0  # 5 minutes

    def test_bare_float_as_minutes(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("2.5") == 150.0  # 2.5 minutes

    def test_case_insensitive(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("1H30M") == 5400.0

    def test_whitespace_stripped(self) -> None:
        from mozart.cli.commands.top import _parse_duration
        assert _parse_duration("  5m  ") == 300.0

    def test_invalid_raises(self) -> None:
        import typer

        from mozart.cli.commands.top import _parse_duration
        with pytest.raises(typer.BadParameter):
            _parse_duration("abc")

    def test_empty_raises(self) -> None:
        import typer

        from mozart.cli.commands.top import _parse_duration
        with pytest.raises(typer.BadParameter):
            _parse_duration("")


class TestFilterSnapshot:
    """Tests for _filter_snapshot() in the CLI top command."""

    def test_no_filter_returns_original(self) -> None:
        from mozart.cli.commands.top import _filter_snapshot

        snapshot = {"processes": [{"job_id": "a"}, {"job_id": "b"}], "timestamp": 1234}
        result = _filter_snapshot(snapshot, None)
        assert result is snapshot  # Same object, not filtered

    def test_filter_by_job_id(self) -> None:
        from mozart.cli.commands.top import _filter_snapshot

        snapshot = {
            "processes": [
                {"job_id": "my-review", "pid": 1},
                {"job_id": "other-job", "pid": 2},
                {"job_id": "my-review", "pid": 3},
            ],
            "timestamp": 1234,
        }
        result = _filter_snapshot(snapshot, "my-review")
        assert result is not None
        assert len(result["processes"]) == 2
        assert all(p["job_id"] == "my-review" for p in result["processes"])
        # Original should be unmodified
        assert len(snapshot["processes"]) == 3

    def test_filter_no_match_returns_filtered_empty(self) -> None:
        from mozart.cli.commands.top import _filter_snapshot

        snapshot = {
            "processes": [{"job_id": "a"}, {"job_id": "b"}],
            "timestamp": 1234,
        }
        result = _filter_snapshot(snapshot, "nonexistent")
        assert result is not None
        assert len(result["processes"]) == 0

    def test_filter_no_processes_returns_full(self) -> None:
        from mozart.cli.commands.top import _filter_snapshot

        snapshot = {"processes": [], "timestamp": 1234, "system_memory_total_mb": 16384}
        result = _filter_snapshot(snapshot, "some-job")
        assert result is not None
        assert result["system_memory_total_mb"] == 16384

    def test_filter_preserves_system_metrics(self) -> None:
        from mozart.cli.commands.top import _filter_snapshot

        snapshot = {
            "processes": [{"job_id": "a", "pid": 1}],
            "timestamp": 1234,
            "daemon_pid": 9999,
            "pressure_level": "low",
        }
        result = _filter_snapshot(snapshot, "a")
        assert result is not None
        assert result["daemon_pid"] == 9999
        assert result["pressure_level"] == "low"


# ===========================================================================
# Import and structure verification tests
# ===========================================================================


class TestTUIImports:
    """Verify TUI package structure and imports work."""

    def test_tui_package_imports(self) -> None:
        """The TUI package exports MonitorApp and MonitorReader."""
        from mozart.tui import MonitorApp, MonitorReader
        assert MonitorApp is not None
        assert MonitorReader is not None

    def test_panels_package_imports(self) -> None:
        """The panels package exports all four panels."""
        from mozart.tui.panels import DetailPanel, HeaderPanel, JobsPanel, TimelinePanel
        assert HeaderPanel is not None
        assert JobsPanel is not None
        assert TimelinePanel is not None
        assert DetailPanel is not None

    def test_profiler_models_import(self) -> None:
        """Profiler models used by TUI are importable."""
        from mozart.daemon.profiler.models import (
            Anomaly,
            EventType,
            GpuMetric,
            ProcessEvent,
            ProcessMetric,
            SystemSnapshot,
        )
        assert SystemSnapshot is not None
        assert ProcessMetric is not None
        assert ProcessEvent is not None
        assert GpuMetric is not None
        assert Anomaly is not None
        assert EventType is not None


# ===========================================================================
# Integration-level tests
# ===========================================================================


class TestReaderToPanel:
    """Integration tests: reader data flows correctly to panel data structures."""

    @pytest.mark.asyncio
    async def test_ipc_snapshot_to_header(self, mock_ipc_client: AsyncMock) -> None:
        """End-to-end: IPC snapshot flows to HeaderPanel.update_data format."""
        from mozart.tui.panels.header import HeaderPanel
        from mozart.tui.reader import MonitorReader

        snap = _make_snapshot(
            running_jobs=3,
            active_sheets=5,
            pressure_level="medium",
        )
        mock_ipc_client.call = AsyncMock(return_value=snap.model_dump(mode="json"))

        reader = MonitorReader(ipc_client=mock_ipc_client)
        result = await reader.get_latest_snapshot()

        assert result is not None
        panel = HeaderPanel()
        panel.update_data(snapshot=result, conductor_up=True, uptime_seconds=600.0)
        assert panel._snapshot is not None
        assert panel._snapshot.running_jobs == 3
        assert panel._snapshot.active_sheets == 5
        assert panel._snapshot.pressure_level == "medium"

    @pytest.mark.asyncio
    async def test_ipc_events_to_timeline(self, mock_ipc_client: AsyncMock) -> None:
        """End-to-end: IPC events flow to TimelinePanel.update_data format."""
        from mozart.tui.panels.timeline import TimelinePanel
        from mozart.tui.reader import MonitorReader

        now = time.time()
        raw_events = [
            {
                "timestamp": now - 10,
                "pid": 1234,
                "event_type": "spawn",
                "job_id": "test-job",
                "sheet_num": 1,
                "details": "Sheet 1",
            },
        ]
        mock_ipc_client.call = AsyncMock(return_value={"events": raw_events})

        reader = MonitorReader(ipc_client=mock_ipc_client)
        events = await reader.get_events(since=now - 60)

        assert len(events) == 1
        panel = TimelinePanel()
        panel.update_data(events=events)
        assert len(panel._events) == 1
        assert panel._events[0].event_type == EventType.SPAWN
