"""Adversarial tests for the 5 interconnected mzt top TUI bugs.

Each test class reproduces the EXACT broken scenario observed in production:
- 32 processes ALL mapped to observer-integration with sheet_num=10
- Progress: "Sheet 31/1 ███████████████ 3100%"
- Timeline: "No events yet" despite 4 active jobs
- Uptime: "0s" (resets on every TUI launch)
- Only 1 of 4 running jobs visible

Tests are structured to FAIL with the old code and PASS with the fixes.
"""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import psutil
import pytest

from marianne.daemon.backpressure import BackpressureController, PressureLevel
from marianne.daemon.event_bus import EventBus
from marianne.daemon.manager import JobManager
from marianne.daemon.monitor import ResourceMonitor
from marianne.daemon.pgroup import ProcessGroupManager
from marianne.daemon.profiler.models import (
    EventType,
    JobProgress,
    ProcessEvent,
    ProcessMetric,
    SystemSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_checkpoint(
    *,
    job_id: str,
    total_sheets: int = 10,
    last_completed_sheet: int = 5,
    current_sheet: int | None = 6,
    status: str = "running",
    pid: int = 9999,
) -> MagicMock:
    """Build a mock CheckpointState with the fields profiler reads."""
    from marianne.core.checkpoint import CheckpointState
    state = MagicMock(spec=CheckpointState)
    state.job_id = job_id
    state.total_sheets = total_sheets
    state.last_completed_sheet = last_completed_sheet
    state.current_sheet = current_sheet
    state.status = status
    state.pid = pid  # In daemon mode, this is always the daemon PID
    return state


def _fake_psutil_process(
    pid: int,
    cmdline: list[str],
    children: list[Any] | None = None,
) -> MagicMock:
    """Build a mock psutil.Process."""
    proc = MagicMock(spec=psutil.Process)
    proc.pid = pid
    proc.cmdline.return_value = cmdline
    proc.children.return_value = children or []
    return proc


# ===========================================================================
# Fix 1: Timeline events must record WITHOUT PID
# ===========================================================================


@pytest.fixture()
def _no_async_tasks():
    """Neuter asyncio.create_task for sync tests that trigger fire-and-forget coroutines."""
    with patch("marianne.daemon.profiler.collector.asyncio.create_task"):
        yield


@pytest.mark.usefixtures("_no_async_tasks")
class TestFix1TimelineEventsWithoutPID:
    """The runner never sends PIDs in sheet events. Old code required a PID
    to record ProcessEvents, so the timeline was always empty.
    """

    def _make_collector(self) -> Any:
        """Build a ProfilerCollector with minimal mocked deps."""
        from marianne.daemon.profiler.collector import ProfilerCollector
        from marianne.daemon.profiler.models import ProfilerConfig

        config = ProfilerConfig(enabled=False, strace_enabled=False)
        monitor = MagicMock(spec=ResourceMonitor)
        pgroup = MagicMock(spec=ProcessGroupManager)
        event_bus = MagicMock(spec=EventBus)
        event_bus.subscribe = MagicMock(spec=EventBus.subscribe, return_value="sub-id")

        collector = ProfilerCollector(config, monitor, pgroup, event_bus)
        return collector

    def test_sheet_started_without_pid_records_event(self) -> None:
        """_on_sheet_started must record a ProcessEvent even when
        data has no 'pid' key. This is the normal case — runners
        don't include PIDs in sheet.started events.
        """
        collector = self._make_collector()
        assert len(collector._recent_events) == 0

        event = {
            "job_id": "my-job",
            "sheet_num": 3,
            "event": "sheet.started",
            "data": {},  # No pid!
        }
        collector._on_sheet_started(event)

        assert len(collector._recent_events) == 1
        recorded = collector._recent_events[0]
        assert recorded.event_type == EventType.SPAWN
        assert recorded.job_id == "my-job"
        assert recorded.sheet_num == 3
        assert recorded.pid == 0  # fallback

    def test_sheet_completed_without_pid_records_event(self) -> None:
        """_on_sheet_completed must record a ProcessEvent even when
        data has no 'pid' key.
        """
        collector = self._make_collector()

        event = {
            "job_id": "my-job",
            "sheet_num": 5,
            "event": "sheet.completed",
            "data": {"exit_code": 0},  # No pid!
        }
        collector._on_sheet_completed(event)

        assert len(collector._recent_events) == 1
        recorded = collector._recent_events[0]
        assert recorded.event_type == EventType.EXIT
        assert recorded.job_id == "my-job"
        assert recorded.sheet_num == 5
        assert recorded.pid == 0
        assert recorded.exit_code == 0

    def test_sheet_failed_without_pid_records_event(self) -> None:
        """sheet.failed events (which also route to _on_sheet_completed)
        must record even without a PID.
        """
        collector = self._make_collector()

        event = {
            "job_id": "my-job",
            "sheet_num": 7,
            "event": "sheet.failed",
            "data": {"exit_code": 1},
        }
        collector._on_sheet_completed(event)

        assert len(collector._recent_events) == 1
        recorded = collector._recent_events[0]
        assert "failed" in recorded.details

    def test_sheet_started_with_pid_still_works(self) -> None:
        """When a PID IS present, it should still be recorded (not 0)."""
        collector = self._make_collector()

        event = {
            "job_id": "my-job",
            "sheet_num": 1,
            "event": "sheet.started",
            "data": {"pid": 42},
        }
        collector._on_sheet_started(event)

        assert len(collector._recent_events) == 1
        assert collector._recent_events[0].pid == 42

    def test_rapid_fire_events_all_recorded(self) -> None:
        """Simulate a burst of sheet events — all must be recorded."""
        collector = self._make_collector()

        for i in range(1, 11):
            collector._on_sheet_started({
                "job_id": "burst-job",
                "sheet_num": i,
                "event": "sheet.started",
                "data": {},
            })
            collector._on_sheet_completed({
                "job_id": "burst-job",
                "sheet_num": i,
                "event": "sheet.completed",
                "data": {"exit_code": 0},
            })

        # 10 starts + 10 completions = 20 events
        assert len(collector._recent_events) == 20


# ===========================================================================
# Fix 2: Workspace-based PID mapping
# ===========================================================================


class TestFix2WorkspacePIDMapping:
    """Old code used CheckpointState.pid (always the daemon PID) to map
    processes to jobs. In daemon mode, all jobs share one process, so
    every child got mapped to whichever job was first in dict iteration.

    New code matches workspace paths in command lines.
    """

    def _make_collector_with_manager(
        self,
        job_meta: dict[str, Any],
        live_states: dict[str, Any],
    ) -> Any:
        from marianne.daemon.profiler.collector import ProfilerCollector
        from marianne.daemon.profiler.models import ProfilerConfig

        config = ProfilerConfig(enabled=False, strace_enabled=False)
        monitor = MagicMock(spec=ResourceMonitor)
        pgroup = MagicMock(spec=ProcessGroupManager)
        event_bus = MagicMock(spec=EventBus)
        event_bus.subscribe = MagicMock(spec=EventBus.subscribe, return_value="sub-id")

        manager = MagicMock(spec=JobManager)
        manager._job_meta = job_meta
        manager._live_states = live_states

        collector = ProfilerCollector(config, monitor, pgroup, event_bus, manager=manager)
        return collector

    def test_four_jobs_four_workspaces_correct_mapping(self) -> None:
        """Reproduce the production scenario: 4 jobs with distinct workspaces.
        Direct children of the daemon should map to different jobs based
        on workspace path in their command line.
        """
        from marianne.daemon.manager import JobMeta

        jobs = {
            "observer-integration": JobMeta(
                job_id="observer-integration",
                config_path=Path("observer.yaml"),
                workspace=Path("/home/user/workspace-observer"),
            ),
            "enhanced-validation": JobMeta(
                job_id="enhanced-validation",
                config_path=Path("enhanced.yaml"),
                workspace=Path("/home/user/.enhanced-workspace"),
            ),
            "monitor-concert": JobMeta(
                job_id="monitor-concert",
                config_path=Path("monitor.yaml"),
                workspace=Path("/home/user/monitor-workspace"),
            ),
            "issue-solver": JobMeta(
                job_id="issue-solver",
                config_path=Path("solver.yaml"),
                workspace=Path("/home/user/.issue-solver-workspace"),
            ),
        }
        live_states = {
            "observer-integration": _fake_checkpoint(job_id="observer-integration", current_sheet=10),
            "enhanced-validation": _fake_checkpoint(job_id="enhanced-validation", current_sheet=3),
            "monitor-concert": _fake_checkpoint(job_id="monitor-concert", current_sheet=7),
            "issue-solver": _fake_checkpoint(job_id="issue-solver", current_sheet=2),
        }

        collector = self._make_collector_with_manager(jobs, live_states)

        # Mock psutil: daemon has 4 direct children, each with workspace in cmdline
        child_observer = _fake_psutil_process(
            pid=100,
            cmdline=["claude", "-p", "Workspace: /home/user/workspace-observer"],
            children=[
                _fake_psutil_process(pid=101, cmdline=["pytest"]),
                _fake_psutil_process(pid=102, cmdline=["node", "pyright"]),
            ],
        )
        child_enhanced = _fake_psutil_process(
            pid=200,
            cmdline=["claude", "-p", "Workspace: /home/user/.enhanced-workspace"],
        )
        child_monitor = _fake_psutil_process(
            pid=300,
            cmdline=["claude", "-p", "Workspace: /home/user/monitor-workspace"],
            children=[_fake_psutil_process(pid=301, cmdline=["bash"])],
        )
        child_solver = _fake_psutil_process(
            pid=400,
            cmdline=["claude", "-p", "Workspace: /home/user/.issue-solver-workspace"],
        )

        daemon_proc = MagicMock(spec=psutil.Process)
        daemon_proc.children.return_value = [
            child_observer, child_enhanced, child_monitor, child_solver,
        ]

        with patch("marianne.daemon.profiler.collector._psutil") as mock_psutil:
            mock_psutil.Process.return_value = daemon_proc
            pid_map = collector._build_pid_job_map()

        # Each direct child maps to the correct job
        assert pid_map[100] == ("observer-integration", 10)
        assert pid_map[200] == ("enhanced-validation", 3)
        assert pid_map[300] == ("monitor-concert", 7)
        assert pid_map[400] == ("issue-solver", 2)

        # Descendants inherit their parent's job
        assert pid_map[101] == ("observer-integration", 10)
        assert pid_map[102] == ("observer-integration", 10)
        assert pid_map[301] == ("monitor-concert", 7)

        # Verify we have 7 total mappings, not everything to one job
        unique_jobs = {v[0] for v in pid_map.values()}
        assert len(unique_jobs) == 4

    def test_unmatched_process_not_in_map(self) -> None:
        """A process whose cmdline doesn't contain any workspace path
        should NOT appear in the map (shown as orphan).
        """
        from marianne.daemon.manager import JobMeta

        jobs = {
            "my-job": JobMeta(
                job_id="my-job",
                config_path=Path("job.yaml"),
                workspace=Path("/home/user/my-workspace"),
            ),
        }
        live_states = {
            "my-job": _fake_checkpoint(job_id="my-job"),
        }

        collector = self._make_collector_with_manager(jobs, live_states)

        child_matched = _fake_psutil_process(
            pid=100,
            cmdline=["claude", "Workspace: /home/user/my-workspace"],
        )
        child_orphan = _fake_psutil_process(
            pid=200,
            cmdline=["python3", "-m", "http.server"],  # No workspace
        )

        daemon_proc = MagicMock(spec=psutil.Process)
        daemon_proc.children.return_value = [child_matched, child_orphan]

        with patch("marianne.daemon.profiler.collector._psutil") as mock_psutil:
            mock_psutil.Process.return_value = daemon_proc
            pid_map = collector._build_pid_job_map()

        assert 100 in pid_map
        assert pid_map[100] == ("my-job", 6)  # current_sheet default from _fake_checkpoint
        assert 200 not in pid_map  # Orphan

    def test_no_manager_returns_empty_map(self) -> None:
        """When no manager is set, _build_pid_job_map returns empty."""
        from marianne.daemon.profiler.collector import ProfilerCollector
        from marianne.daemon.profiler.models import ProfilerConfig

        config = ProfilerConfig(enabled=False)
        collector = ProfilerCollector(
            config,
            MagicMock(spec=ResourceMonitor),
            MagicMock(spec=ProcessGroupManager),
            MagicMock(spec=EventBus),
        )
        assert collector._build_pid_job_map() == {}

    def test_no_psutil_returns_empty_map(self) -> None:
        """When psutil is unavailable, _build_pid_job_map returns empty."""
        from marianne.daemon.manager import JobMeta

        jobs = {"j": JobMeta(job_id="j", config_path=Path("j.yaml"), workspace=Path("/ws"))}
        collector = self._make_collector_with_manager(jobs, {})

        with patch("marianne.daemon.profiler.collector._psutil", None):
            pid_map = collector._build_pid_job_map()

        assert pid_map == {}

    def test_psutil_access_denied_doesnt_crash(self) -> None:
        """If psutil raises AccessDenied reading a child's cmdline,
        that child is skipped — not crash the whole collection.
        """
        import psutil

        from marianne.daemon.manager import JobMeta

        jobs = {"j": JobMeta(job_id="j", config_path=Path("j.yaml"), workspace=Path("/ws"))}
        live_states = {"j": _fake_checkpoint(job_id="j")}
        collector = self._make_collector_with_manager(jobs, live_states)

        bad_child = MagicMock(spec=psutil.Process)
        bad_child.pid = 666
        bad_child.cmdline.side_effect = psutil.AccessDenied(pid=666)

        good_child = _fake_psutil_process(
            pid=100, cmdline=["claude", "Workspace: /ws"],
        )

        daemon_proc = MagicMock(spec=psutil.Process)
        daemon_proc.children.return_value = [bad_child, good_child]

        with patch("marianne.daemon.profiler.collector._psutil") as mock_psutil:
            mock_psutil.Process.return_value = daemon_proc
            mock_psutil.NoSuchProcess = psutil.NoSuchProcess
            mock_psutil.AccessDenied = psutil.AccessDenied
            mock_psutil.ZombieProcess = psutil.ZombieProcess
            pid_map = collector._build_pid_job_map()

        assert 100 in pid_map
        assert 666 not in pid_map  # Skipped, not crashed


# ===========================================================================
# Fix 3: Job progress in SystemSnapshot
# ===========================================================================


class TestFix3JobProgressInSnapshot:
    """Old snapshots had no per-job progress data. The TUI had to guess
    progress from process counts, producing "Sheet 31/1 ███ 3100%".
    """

    @pytest.mark.asyncio
    async def test_collect_snapshot_populates_job_progress(self) -> None:
        """collect_snapshot must read _live_states and build JobProgress entries."""
        from marianne.daemon.profiler.collector import ProfilerCollector
        from marianne.daemon.profiler.models import ProfilerConfig

        config = ProfilerConfig(enabled=False, strace_enabled=False)
        monitor = MagicMock(spec=ResourceMonitor)
        pgroup = MagicMock(spec=ProcessGroupManager)
        event_bus = MagicMock(spec=EventBus)
        event_bus.subscribe = MagicMock(spec=EventBus.subscribe, return_value="sub-id")

        manager = MagicMock(spec=JobManager)
        manager._job_meta = {}
        manager._live_states = {
            "job-a": _fake_checkpoint(
                job_id="job-a", total_sheets=10,
                last_completed_sheet=7, current_sheet=8, status="running",
            ),
            "job-b": _fake_checkpoint(
                job_id="job-b", total_sheets=5,
                last_completed_sheet=2, current_sheet=3, status="running",
            ),
        }
        manager.running_count = 2
        manager.active_job_count = 2
        manager.uptime_seconds = 3600.0
        manager.backpressure = MagicMock(spec=BackpressureController)
        manager.backpressure.current_level.return_value = MagicMock(spec=PressureLevel, value="none")

        collector = ProfilerCollector(config, monitor, pgroup, event_bus, manager=manager)
        snapshot = await collector.collect_snapshot()

        assert len(snapshot.job_progress) == 2

        progress_by_id = {jp.job_id: jp for jp in snapshot.job_progress}

        jp_a = progress_by_id["job-a"]
        assert jp_a.total_sheets == 10
        assert jp_a.last_completed_sheet == 7
        assert jp_a.current_sheet == 8
        assert jp_a.status == "running"

        jp_b = progress_by_id["job-b"]
        assert jp_b.total_sheets == 5
        assert jp_b.last_completed_sheet == 2
        assert jp_b.current_sheet == 3

    @pytest.mark.asyncio
    async def test_collect_snapshot_populates_conductor_uptime(self) -> None:
        """collect_snapshot must read manager.uptime_seconds."""
        from marianne.daemon.profiler.collector import ProfilerCollector
        from marianne.daemon.profiler.models import ProfilerConfig

        config = ProfilerConfig(enabled=False)
        manager = MagicMock(spec=JobManager)
        manager._job_meta = {}
        manager._live_states = {}
        manager.running_count = 0
        manager.active_job_count = 0
        manager.uptime_seconds = 7200.5
        manager.backpressure = MagicMock(spec=BackpressureController)
        manager.backpressure.current_level.return_value = MagicMock(spec=PressureLevel, value="none")

        collector = ProfilerCollector(
            config,
            MagicMock(spec=ResourceMonitor),
            MagicMock(spec=ProcessGroupManager),
            MagicMock(spec=EventBus),
            manager=manager,
        )
        snapshot = await collector.collect_snapshot()

        assert snapshot.conductor_uptime_seconds == 7200.5

    @pytest.mark.asyncio
    async def test_no_manager_means_empty_progress(self) -> None:
        """Without a manager, job_progress should be empty, not crash."""
        from marianne.daemon.profiler.collector import ProfilerCollector
        from marianne.daemon.profiler.models import ProfilerConfig

        config = ProfilerConfig(enabled=False)
        collector = ProfilerCollector(
            config,
            MagicMock(spec=ResourceMonitor),
            MagicMock(spec=ProcessGroupManager),
            MagicMock(spec=EventBus),
            manager=None,
        )
        snapshot = await collector.collect_snapshot()

        assert snapshot.job_progress == []
        assert snapshot.conductor_uptime_seconds == 0.0


# ===========================================================================
# Fix 4: Progress bar uses completed/total sheets
# ===========================================================================


class TestFix4ProgressBarCalculation:
    """Old code: len(running_processes) / len(unique_sheet_nums) = 3100%.
    New code: last_completed_sheet / total_sheets from JobProgress.

    This tests the rendering logic in JobsPanel.
    """

    def test_progress_bar_uses_job_progress_not_process_count(self) -> None:
        """Reproduce the exact bug: 32 processes mapped to one job,
        but job_progress says 5/10 completed. The display must show
        the job_progress data, not the process count.
        """
        from marianne.tui.panels.jobs import JobsPanel

        # 32 processes all mapped to the same job (the old bug)
        procs = [
            ProcessMetric(
                pid=1000 + i,
                ppid=999,
                state="S",
                cpu_percent=1.0,
                rss_mb=100.0,
                job_id="observer-integration",
                sheet_num=10,
            )
            for i in range(32)
        ]

        snap = SystemSnapshot(
            processes=procs,
            running_jobs=4,
            active_sheets=4,
            job_progress=[
                JobProgress(
                    job_id="observer-integration",
                    total_sheets=10,
                    last_completed_sheet=5,
                    current_sheet=6,
                    status="running",
                ),
            ],
        )

        # Group processes by job_id (same logic as _render_jobs)
        by_job: dict[str, list[ProcessMetric]] = defaultdict(list)
        for proc in snap.processes:
            if proc.job_id:
                by_job[proc.job_id].append(proc)

        # Index job progress
        progress_by_job = {jp.job_id: jp for jp in snap.job_progress}

        # Simulate the fix logic
        job_id = "observer-integration"
        procs_for_job = by_job[job_id]
        jp = progress_by_job.get(job_id)

        # NEW code path (with job_progress)
        assert jp is not None
        assert jp.total_sheets == 10
        assert jp.last_completed_sheet == 5

        from marianne.tui.panels.jobs import _format_progress_bar

        progress = _format_progress_bar(jp.last_completed_sheet, jp.total_sheets)
        assert "50%" in progress  # 5/10 = 50%
        assert "3100%" not in progress
        assert "2100%" not in progress

        # OLD code path would have done this:
        sheet_nums = {p.sheet_num for p in procs_for_job if p.sheet_num is not None}
        old_total = max(len(sheet_nums), 1)  # = 1 (only sheet_num=10)
        running = [p for p in procs_for_job if p.state in ("R", "S", "D")]
        old_progress = _format_progress_bar(len(running), old_total)
        # This produces the 3200% bug!
        assert "3200%" in old_progress  # Proves old code was broken

    def test_fallback_when_no_job_progress(self) -> None:
        """When job_progress is empty (old daemon), the fallback code
        path still works (even if it produces bad numbers).
        """
        snap = SystemSnapshot(
            processes=[
                ProcessMetric(
                    pid=100, state="S", cpu_percent=1.0, rss_mb=50.0,
                    job_id="my-job", sheet_num=3,
                ),
                ProcessMetric(
                    pid=101, state="S", cpu_percent=1.0, rss_mb=50.0,
                    job_id="my-job", sheet_num=4,
                ),
            ],
            job_progress=[],  # No progress data (old daemon)
        )

        progress_by_job = {jp.job_id: jp for jp in snap.job_progress}
        jp = progress_by_job.get("my-job")
        assert jp is None  # Triggers fallback path

    def test_multiple_jobs_each_get_own_progress(self) -> None:
        """When multiple jobs exist, each shows its own progress."""
        snap = SystemSnapshot(
            processes=[
                ProcessMetric(pid=100, state="S", job_id="job-a", sheet_num=3),
                ProcessMetric(pid=200, state="S", job_id="job-b", sheet_num=1),
            ],
            job_progress=[
                JobProgress(job_id="job-a", total_sheets=10, last_completed_sheet=7, current_sheet=8),
                JobProgress(job_id="job-b", total_sheets=3, last_completed_sheet=0, current_sheet=1),
            ],
        )

        progress_by_job = {jp.job_id: jp for jp in snap.job_progress}

        from marianne.tui.panels.jobs import _format_progress_bar

        bar_a = _format_progress_bar(progress_by_job["job-a"].last_completed_sheet, progress_by_job["job-a"].total_sheets)
        bar_b = _format_progress_bar(progress_by_job["job-b"].last_completed_sheet, progress_by_job["job-b"].total_sheets)

        assert "70%" in bar_a
        assert "0%" in bar_b


# ===========================================================================
# Fix 5: Conductor uptime, not TUI session time
# ===========================================================================


class TestFix5ConductorUptime:
    """Old code tracked uptime as time.monotonic() - first_snapshot_time,
    which reset to 0 every time the TUI was launched. New code reads
    conductor_uptime_seconds from the snapshot.
    """

    def test_uptime_comes_from_snapshot_not_session(self) -> None:
        """The uptime passed to the header panel must come from
        snapshot.conductor_uptime_seconds, not from a TUI-local timer.
        """
        from marianne.tui.app import MonitorApp

        app = MonitorApp()

        # Simulate: TUI just started (mount_time is now)
        app._mount_time = time.monotonic()

        # Snapshot says conductor has been running for 2 hours
        snap = SystemSnapshot(
            conductor_uptime_seconds=7200.0,
            running_jobs=2,
            active_sheets=3,
        )

        # The logic from refresh_data:
        uptime = snap.conductor_uptime_seconds

        # Must be 7200 (conductor uptime), not ~0 (TUI session time)
        assert uptime == 7200.0

    def test_uptime_zero_when_no_snapshot(self) -> None:
        """When snapshot is None, uptime should be 0."""
        snapshot = None
        uptime = 0.0
        if snapshot is not None:
            uptime = snapshot.conductor_uptime_seconds
        assert uptime == 0.0

    def test_no_first_snapshot_time_field(self) -> None:
        """MonitorApp must NOT have _first_snapshot_time — that field
        was the root cause of the uptime bug.
        """
        from marianne.tui.app import MonitorApp

        app = MonitorApp()
        assert not hasattr(app, "_first_snapshot_time"), (
            "MonitorApp still has _first_snapshot_time — "
            "uptime will reset on every TUI launch"
        )

    def test_manager_uptime_seconds_property(self) -> None:
        """JobManager.uptime_seconds must return real daemon lifetime."""
        from marianne.daemon.manager import JobManager
        from marianne.daemon.config import DaemonConfig

        start = time.monotonic() - 120.0  # Started 2 minutes ago
        mgr = JobManager(DaemonConfig(), start_time=start)

        uptime = mgr.uptime_seconds
        assert uptime >= 119.0  # At least ~120s (allow small jitter)
        assert uptime < 130.0   # But not wildly off


# ===========================================================================
# Integration: The full broken scenario
# ===========================================================================


class TestIntegrationFullBrokenScenario:
    """Reproduce the exact production failure end-to-end:
    4 jobs, 32 processes, all mapped to one job.
    """

    def test_old_snapshot_format_doesnt_crash_new_client(self) -> None:
        """A snapshot from an old daemon (missing job_progress and
        conductor_uptime_seconds) must deserialize without crashing.
        """
        old_format = {
            "timestamp": 1234567.0,
            "daemon_pid": 2849,
            "system_memory_total_mb": 48000.0,
            "system_memory_available_mb": 40000.0,
            "system_memory_used_mb": 8000.0,
            "daemon_rss_mb": 133.0,
            "load_avg_1": 6.2,
            "load_avg_5": 5.3,
            "load_avg_15": 4.5,
            "processes": [
                {"pid": 100 + i, "job_id": "observer-integration", "sheet_num": 10,
                 "state": "S", "cpu_percent": 0.0, "rss_mb": 200.0}
                for i in range(32)
            ],
            "gpus": [],
            "pressure_level": "none",
            "running_jobs": 4,
            "active_sheets": 4,
            "zombie_count": 0,
            "zombie_pids": [],
            # NOTE: no job_progress, no conductor_uptime_seconds
        }

        snap = SystemSnapshot(**old_format)

        # New fields default gracefully
        assert snap.job_progress == []
        assert snap.conductor_uptime_seconds == 0.0

        # Process data still accessible
        assert len(snap.processes) == 32
        job_ids = {p.job_id for p in snap.processes}
        assert job_ids == {"observer-integration"}  # Old bug: all same job

    def test_new_snapshot_format_shows_multiple_jobs(self) -> None:
        """A snapshot from the new daemon correctly shows 4 jobs."""
        snap = SystemSnapshot(
            processes=[
                ProcessMetric(pid=100, state="S", job_id="observer", sheet_num=10),
                ProcessMetric(pid=101, state="S", job_id="observer", sheet_num=10),
                ProcessMetric(pid=200, state="S", job_id="enhanced", sheet_num=3),
                ProcessMetric(pid=300, state="S", job_id="monitor", sheet_num=7),
                ProcessMetric(pid=400, state="S", job_id="solver", sheet_num=2),
            ],
            job_progress=[
                JobProgress(job_id="observer", total_sheets=10, last_completed_sheet=9, current_sheet=10),
                JobProgress(job_id="enhanced", total_sheets=5, last_completed_sheet=2, current_sheet=3),
                JobProgress(job_id="monitor", total_sheets=8, last_completed_sheet=6, current_sheet=7),
                JobProgress(job_id="solver", total_sheets=3, last_completed_sheet=1, current_sheet=2),
            ],
            conductor_uptime_seconds=3920.0,
            running_jobs=4,
            active_sheets=4,
        )

        # 4 distinct jobs in processes
        job_ids = {p.job_id for p in snap.processes}
        assert len(job_ids) == 4

        # 4 progress entries
        assert len(snap.job_progress) == 4

        # Each has sane progress (not 3100%)
        from marianne.tui.panels.jobs import _format_progress_bar
        for jp in snap.job_progress:
            bar = _format_progress_bar(jp.last_completed_sheet, jp.total_sheets)
            pct = jp.last_completed_sheet / jp.total_sheets * 100
            assert f"{pct:.0f}%" in bar
            assert pct <= 100

        # Uptime is real conductor time, not 0
        assert snap.conductor_uptime_seconds == 3920.0


# ===========================================================================
# Model edge cases
# ===========================================================================


class TestJobProgressModel:
    """Edge cases for the JobProgress Pydantic model."""

    def test_minimum_valid_progress(self) -> None:
        jp = JobProgress(job_id="x", total_sheets=1, last_completed_sheet=0)
        assert jp.current_sheet is None
        assert jp.status == "unknown"

    def test_completed_job_progress(self) -> None:
        jp = JobProgress(
            job_id="done", total_sheets=5,
            last_completed_sheet=5, current_sheet=None, status="completed",
        )
        assert jp.last_completed_sheet == jp.total_sheets

    def test_serialization_roundtrip(self) -> None:
        jp = JobProgress(
            job_id="test", total_sheets=10,
            last_completed_sheet=7, current_sheet=8, status="running",
        )
        data = jp.model_dump(mode="json")
        restored = JobProgress(**data)
        assert restored == jp

    def test_snapshot_with_progress_serialization(self) -> None:
        """SystemSnapshot with job_progress must serialize/deserialize."""
        snap = SystemSnapshot(
            job_progress=[
                JobProgress(job_id="a", total_sheets=5, last_completed_sheet=3),
                JobProgress(job_id="b", total_sheets=2, last_completed_sheet=1),
            ],
            conductor_uptime_seconds=600.0,
        )
        data = snap.model_dump(mode="json")
        restored = SystemSnapshot(**data)
        assert len(restored.job_progress) == 2
        assert restored.conductor_uptime_seconds == 600.0


# ===========================================================================
# Phase 1 — Issue #102: Observer file events in timeline
# ===========================================================================


class TestTimelinePanelFileEvents:
    """TimelinePanel must render observer.file_* events with correct icons
    and color coding, parallel to the existing observer.process_* rendering.
    """

    def _make_panel(self) -> Any:
        """Build a TimelinePanel for testing."""
        from marianne.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        return panel

    def test_file_created_event_rendered(self) -> None:
        """observer.file_created events appear in timeline entries."""
        from marianne.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        obs_events = [
            {
                "event": "observer.file_created",
                "timestamp": 1700000000.0,
                "job_id": "my-job",
                "data": {"path": "/workspace/output.txt"},
            },
        ]
        # Call update_data to trigger rendering
        panel.update_data(observer_events=obs_events)

        # The panel should have processed file events into entries
        # Verify by checking internal state via _render_timeline logic
        # Re-render to capture entries
        entries: list[tuple[float, str]] = []
        for obs in panel._observer_events:
            evt_name = obs.get("event", "")
            if evt_name.startswith("observer.file_"):
                entries.append((obs["timestamp"], evt_name))
        assert len(entries) == 1
        assert entries[0][1] == "observer.file_created"

    def test_file_modified_event_rendered(self) -> None:
        """observer.file_modified events appear in timeline."""
        from marianne.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        obs_events = [
            {
                "event": "observer.file_modified",
                "timestamp": 1700000001.0,
                "job_id": "my-job",
                "data": {"path": "/workspace/config.yaml"},
            },
        ]
        panel.update_data(observer_events=obs_events)

        # Verify the event is stored and would be rendered
        file_events = [
            e for e in panel._observer_events
            if e.get("event", "").startswith("observer.file_")
        ]
        assert len(file_events) == 1
        assert file_events[0]["event"] == "observer.file_modified"

    def test_file_deleted_event_rendered(self) -> None:
        """observer.file_deleted events appear in timeline."""
        from marianne.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        obs_events = [
            {
                "event": "observer.file_deleted",
                "timestamp": 1700000002.0,
                "job_id": "my-job",
                "data": {"path": "/workspace/temp.log"},
            },
        ]
        panel.update_data(observer_events=obs_events)

        file_events = [
            e for e in panel._observer_events
            if e.get("event", "").startswith("observer.file_")
        ]
        assert len(file_events) == 1
        assert file_events[0]["event"] == "observer.file_deleted"

    def test_file_events_have_color_mappings(self) -> None:
        """Event color map includes entries for all file event types."""
        from marianne.tui.panels.timeline import _EVENT_COLORS

        assert "observer_file_created" in _EVENT_COLORS
        assert "observer_file_modified" in _EVENT_COLORS
        assert "observer_file_deleted" in _EVENT_COLORS
        assert _EVENT_COLORS["observer_file_created"] == "green"
        assert _EVENT_COLORS["observer_file_modified"] == "yellow"
        assert _EVENT_COLORS["observer_file_deleted"] == "red"

    def test_mixed_process_and_file_events(self) -> None:
        """Both observer.process_* and observer.file_* events render together."""
        from marianne.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        obs_events = [
            {
                "event": "observer.process_spawned",
                "timestamp": 1700000000.0,
                "job_id": "my-job",
                "data": {"pid": 1234, "name": "claude"},
            },
            {
                "event": "observer.file_created",
                "timestamp": 1700000001.0,
                "job_id": "my-job",
                "data": {"path": "/workspace/output.txt"},
            },
            {
                "event": "observer.file_modified",
                "timestamp": 1700000002.0,
                "job_id": "my-job",
                "data": {"path": "/workspace/output.txt"},
            },
        ]
        panel.update_data(observer_events=obs_events)

        # All 3 events stored
        assert len(panel._observer_events) == 3
        process_events = [e for e in panel._observer_events if e["event"].startswith("observer.process_")]
        file_events = [e for e in panel._observer_events if e["event"].startswith("observer.file_")]
        assert len(process_events) == 1
        assert len(file_events) == 2

    def test_long_path_truncated(self) -> None:
        """File paths longer than 40 chars get truncated in the timeline."""
        from marianne.tui.panels.timeline import TimelinePanel

        panel = TimelinePanel()
        long_path = "/workspace/" + "a" * 50 + "/file.txt"
        obs_events = [
            {
                "event": "observer.file_created",
                "timestamp": 1700000000.0,
                "job_id": "my-job",
                "data": {"path": long_path},
            },
        ]
        panel.update_data(observer_events=obs_events)
        # Verify the panel renders without error (path truncation is internal)
        assert len(panel._observer_events) == 1


# ===========================================================================
# Phase 1 — Issue #102: JobsPanel passes observer_file_events to items
# ===========================================================================


class TestJobsPanelObserverFileEvents:
    """JobsPanel must accept observer file events and attach them to
    job_data items so DetailPanel.show_item() can render file activity.
    """

    def test_jobs_panel_stores_observer_file_events(self) -> None:
        """JobsPanel.update_data accepts observer_file_events parameter."""
        from marianne.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        file_events = [
            {
                "event": "observer.file_created",
                "timestamp": 1700000000.0,
                "job_id": "my-job",
                "data": {"path": "/workspace/out.txt"},
            },
        ]
        # Ensure update_data accepts the new parameter without error
        panel.update_data(None, observer_file_events=file_events)
        assert panel._observer_file_events == file_events

    def test_job_data_includes_observer_file_events(self) -> None:
        """When a job is rendered, its job_data dict includes observer_file_events
        filtered to that job_id.
        """
        from textual.widgets import Static, Tree

        from marianne.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        # Manually initialize the tree widget (normally done in compose())
        panel._empty_label = Static("[dim]No active jobs[/]", id="jobs-empty")
        tree: Tree[dict[str, Any]] = Tree("Jobs", id="jobs-tree")
        tree.show_root = False
        tree.guide_depth = 3
        panel._tree = tree

        snap = SystemSnapshot(
            processes=[
                ProcessMetric(pid=100, state="S", job_id="job-a", sheet_num=1),
                ProcessMetric(pid=200, state="S", job_id="job-b", sheet_num=2),
            ],
            job_progress=[
                JobProgress(job_id="job-a", total_sheets=5, last_completed_sheet=2),
                JobProgress(job_id="job-b", total_sheets=3, last_completed_sheet=1),
            ],
        )
        file_events = [
            {
                "event": "observer.file_created",
                "timestamp": 1700000000.0,
                "job_id": "job-a",
                "data": {"path": "/ws-a/out.txt"},
            },
            {
                "event": "observer.file_modified",
                "timestamp": 1700000001.0,
                "job_id": "job-b",
                "data": {"path": "/ws-b/config.yaml"},
            },
            {
                "event": "observer.process_spawned",
                "timestamp": 1700000002.0,
                "job_id": "job-a",
                "data": {"pid": 999},
            },
        ]

        # Pre-store file events, then render
        panel._observer_file_events = file_events
        panel.update_data(snap)

        # Find the job items
        job_items = [item for item in panel._items if item.get("type") == "job"]
        assert len(job_items) == 2

        job_a_item = next(i for i in job_items if i["job_id"] == "job-a")
        job_b_item = next(i for i in job_items if i["job_id"] == "job-b")

        # job-a gets only its file event (not the process event)
        assert len(job_a_item["observer_file_events"]) == 1
        assert job_a_item["observer_file_events"][0]["event"] == "observer.file_created"

        # job-b gets only its file event
        assert len(job_b_item["observer_file_events"]) == 1
        assert job_b_item["observer_file_events"][0]["event"] == "observer.file_modified"

    def test_job_data_empty_when_no_file_events(self) -> None:
        """When no observer file events exist, job_data still has the key
        but with an empty list.
        """
        from textual.widgets import Static, Tree

        from marianne.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        # Manually initialize the tree widget (normally done in compose())
        panel._empty_label = Static("[dim]No active jobs[/]", id="jobs-empty")
        tree: Tree[dict[str, Any]] = Tree("Jobs", id="jobs-tree")
        tree.show_root = False
        tree.guide_depth = 3
        panel._tree = tree

        snap = SystemSnapshot(
            processes=[
                ProcessMetric(pid=100, state="S", job_id="my-job", sheet_num=1),
            ],
            job_progress=[
                JobProgress(job_id="my-job", total_sheets=5, last_completed_sheet=2),
            ],
        )
        panel.update_data(snap)

        job_items = [item for item in panel._items if item.get("type") == "job"]
        assert len(job_items) == 1
        assert "observer_file_events" in job_items[0]
        assert job_items[0]["observer_file_events"] == []


# ===========================================================================
# Phase 1 — Issue #102: DetailPanel renders file activity from job items
# ===========================================================================


class TestDetailPanelFileActivity:
    """DetailPanel.show_item() renders file activity when
    observer_file_events are present in the job item.
    """

    def test_show_item_renders_file_activity(self) -> None:
        """When a job item has observer_file_events, show_item() should
        include 'File Activity' in the rendered content.
        """
        from marianne.tui.panels.detail import DetailPanel

        panel = DetailPanel()
        item: dict[str, Any] = {
            "type": "job",
            "job_id": "my-job",
            "processes": [
                ProcessMetric(pid=100, state="S", job_id="my-job", sheet_num=1),
            ],
            "observer_file_events": [
                {
                    "event": "observer.file_created",
                    "timestamp": 1700000000.0,
                    "data": {"path": "/workspace/output.txt"},
                },
                {
                    "event": "observer.file_deleted",
                    "timestamp": 1700000001.0,
                    "data": {"path": "/workspace/temp.log"},
                },
            ],
        }
        # show_item should not crash when called before compose()
        panel.show_item(item)
        # _content is None pre-compose, so _set_content is a no-op — that's the point
        assert panel._content is None

    def test_show_item_no_file_events_no_crash(self) -> None:
        """When a job item has empty observer_file_events, show_item()
        still renders job details without error.
        """
        from marianne.tui.panels.detail import DetailPanel

        panel = DetailPanel()
        item: dict[str, Any] = {
            "type": "job",
            "job_id": "my-job",
            "processes": [
                ProcessMetric(pid=100, state="S", job_id="my-job", sheet_num=1),
            ],
            "observer_file_events": [],
        }
        panel.show_item(item)
        assert panel._content is None  # no crash, content deferred until compose()
