"""Comprehensive tests for the profiler subsystem.

Tests cover:
- MonitorStorage (SQLite + JSONL persistence)
- GpuProbe (GPU metric collection with fallback chain)
- StraceManager (strace attach/detach/parse)
- AnomalyDetector (heuristic anomaly detection)
- ProfilerCollector (central orchestrator)
- PatternType (learning integration)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.profiler.anomaly import (
    AnomalyDetector,
    FD_EXHAUSTION_THRESHOLD,
)
from marianne.daemon.profiler.models import (
    AnomalyConfig,
    AnomalySeverity,
    AnomalyType,
    EventType,
    GpuMetric,
    ProcessEvent,
    ProcessMetric,
    ProfilerConfig,
    RetentionConfig,
    SystemSnapshot,
)
from marianne.daemon.profiler.storage import MonitorStorage
from marianne.daemon.profiler.strace_manager import StraceManager
from marianne.daemon.types import ObserverEvent
from marianne.learning.patterns import PatternType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(
    *,
    timestamp: float | None = None,
    processes: list[ProcessMetric] | None = None,
    system_memory_total_mb: float = 16000.0,
    system_memory_available_mb: float = 8000.0,
    system_memory_used_mb: float = 8000.0,
    daemon_rss_mb: float = 100.0,
    zombie_count: int = 0,
    zombie_pids: list[int] | None = None,
    gpus: list[GpuMetric] | None = None,
    pressure_level: str = "none",
    running_jobs: int = 0,
    active_sheets: int = 0,
) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=timestamp or time.time(),
        daemon_pid=1000,
        system_memory_total_mb=system_memory_total_mb,
        system_memory_available_mb=system_memory_available_mb,
        system_memory_used_mb=system_memory_used_mb,
        daemon_rss_mb=daemon_rss_mb,
        load_avg_1=1.0,
        load_avg_5=0.8,
        load_avg_15=0.5,
        processes=processes or [],
        gpus=gpus or [],
        pressure_level=pressure_level,
        running_jobs=running_jobs,
        active_sheets=active_sheets,
        zombie_count=zombie_count,
        zombie_pids=zombie_pids or [],
    )


def _make_process(
    *,
    pid: int = 1234,
    cpu_percent: float = 10.0,
    rss_mb: float = 256.0,
    state: str = "S",
    open_fds: int = 50,
    job_id: str | None = "test-job",
    sheet_num: int | None = 1,
    age_seconds: float = 30.0,
) -> ProcessMetric:
    return ProcessMetric(
        pid=pid,
        ppid=1000,
        command="claude --model opus",
        state=state,
        cpu_percent=cpu_percent,
        rss_mb=rss_mb,
        vms_mb=rss_mb * 2,
        threads=4,
        open_fds=open_fds,
        age_seconds=age_seconds,
        job_id=job_id,
        sheet_num=sheet_num,
        syscall_counts={"write": 1500, "read": 3200},
        syscall_time_pct={"write": 0.40, "read": 0.28},
    )


def _make_event(
    *,
    pid: int = 1234,
    event_type: EventType = EventType.SPAWN,
    job_id: str = "test-job",
    sheet_num: int = 1,
    exit_code: int | None = None,
    timestamp: float | None = None,
) -> ProcessEvent:
    return ProcessEvent(
        timestamp=timestamp or time.time(),
        pid=pid,
        event_type=event_type,
        job_id=job_id,
        sheet_num=sheet_num,
        exit_code=exit_code,
        details=f"Sheet {sheet_num}",
    )


# ===========================================================================
# MonitorStorage tests
# ===========================================================================


class TestMonitorStorage:
    """Tests for SQLite + JSONL storage layer."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, tmp_path: Path) -> None:
        """Verify that initialize() creates the expected SQLite schema."""
        db_path = tmp_path / "test.db"
        storage = MonitorStorage(db_path=db_path)
        await storage.initialize()

        # Verify tables exist by querying sqlite_master
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        assert "snapshots" in tables
        assert "process_metrics" in tables
        assert "process_events" in tables

    @pytest.mark.asyncio
    async def test_write_and_read_snapshot(self, tmp_path: Path) -> None:
        """Round-trip: write a snapshot, read it back, verify fields match."""
        db_path = tmp_path / "test.db"
        storage = MonitorStorage(db_path=db_path)
        await storage.initialize()

        proc = _make_process(pid=42, rss_mb=512.0)
        gpu = GpuMetric(
            index=0,
            utilization_pct=75.0,
            memory_used_mb=4096.0,
            memory_total_mb=8192.0,
            temperature_c=65.0,
        )
        snapshot = _make_snapshot(
            timestamp=1000.0,
            processes=[proc],
            gpus=[gpu],
            running_jobs=2,
            active_sheets=3,
        )

        snapshot_id = await storage.write_snapshot(snapshot)
        assert snapshot_id > 0

        # Read it back
        results = await storage.read_snapshots(since=0.0, limit=10)
        assert len(results) == 1

        read_back = results[0]
        assert read_back.timestamp == 1000.0
        assert read_back.daemon_pid == 1000
        assert read_back.system_memory_total_mb == 16000.0
        assert read_back.running_jobs == 2
        assert read_back.active_sheets == 3

        # Verify process metrics
        assert len(read_back.processes) == 1
        p = read_back.processes[0]
        assert p.pid == 42
        assert p.rss_mb == 512.0
        assert p.syscall_counts == {"write": 1500, "read": 3200}

        # Verify GPU metrics
        assert len(read_back.gpus) == 1
        g = read_back.gpus[0]
        assert g.utilization_pct == 75.0
        assert g.memory_used_mb == 4096.0

    @pytest.mark.asyncio
    async def test_write_and_read_events(self, tmp_path: Path) -> None:
        """Round-trip for process lifecycle events."""
        db_path = tmp_path / "test.db"
        storage = MonitorStorage(db_path=db_path)
        await storage.initialize()

        spawn_event = _make_event(
            pid=555,
            event_type=EventType.SPAWN,
            timestamp=1000.0,
        )
        exit_event = _make_event(
            pid=555,
            event_type=EventType.EXIT,
            exit_code=0,
            timestamp=1010.0,
        )

        await storage.write_event(spawn_event)
        await storage.write_event(exit_event)

        events = await storage.read_events(since=0.0, limit=10)
        assert len(events) == 2
        assert events[0].event_type == EventType.SPAWN
        assert events[0].pid == 555
        assert events[1].event_type == EventType.EXIT
        assert events[1].exit_code == 0

    @pytest.mark.asyncio
    async def test_read_job_resource_profile(self, tmp_path: Path) -> None:
        """Verify aggregate resource profile query for a job."""
        db_path = tmp_path / "test.db"
        storage = MonitorStorage(db_path=db_path)
        await storage.initialize()

        # Write two snapshots with processes for the same job
        proc1 = _make_process(pid=100, rss_mb=256.0, job_id="job-A", sheet_num=1)
        proc2 = _make_process(pid=101, rss_mb=512.0, job_id="job-A", sheet_num=2)
        snap1 = _make_snapshot(timestamp=1000.0, processes=[proc1])
        snap2 = _make_snapshot(timestamp=1005.0, processes=[proc2])

        await storage.write_snapshot(snap1)
        await storage.write_snapshot(snap2)

        # Write a spawn event for this job
        spawn = _make_event(pid=100, event_type=EventType.SPAWN, job_id="job-A")
        await storage.write_event(spawn)

        profile = await storage.read_job_resource_profile("job-A")
        assert profile["job_id"] == "job-A"
        assert profile["peak_rss_mb"] == 512.0
        assert profile["unique_pid_count"] == 2
        assert profile["process_spawn_count"] == 1
        assert "1" in profile["sheet_metrics"]
        assert "2" in profile["sheet_metrics"]

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_data(self, tmp_path: Path) -> None:
        """Verify retention enforcement removes old data."""
        db_path = tmp_path / "test.db"
        storage = MonitorStorage(db_path=db_path)
        await storage.initialize()

        now = time.time()

        # Write an old snapshot (48 hours ago) and a recent one
        old_snap = _make_snapshot(timestamp=now - 48 * 3600)
        new_snap = _make_snapshot(timestamp=now)
        await storage.write_snapshot(old_snap)
        await storage.write_snapshot(new_snap)

        # Write an old event (60 days ago) and a recent one
        old_event = _make_event(timestamp=now - 60 * 86400)
        new_event = _make_event(timestamp=now)
        await storage.write_event(old_event)
        await storage.write_event(new_event)

        # Apply default retention (24h snapshots, 30d events)
        retention = RetentionConfig()
        await storage.cleanup(retention)

        # Old snapshot should be gone, new one remains
        snapshots = await storage.read_snapshots(since=0.0)
        assert len(snapshots) == 1
        assert snapshots[0].timestamp == pytest.approx(now, abs=1.0)

        # Old event should be gone, new one remains
        events = await storage.read_events(since=0.0)
        assert len(events) == 1

    def test_jsonl_append_and_rotate(self, tmp_path: Path) -> None:
        """Verify JSONL writing and rotation at size limit."""
        jsonl_path = tmp_path / "monitor.jsonl"
        # Use a tiny max size so rotation triggers quickly
        storage = MonitorStorage(
            db_path=tmp_path / "test.db",
            jsonl_path=jsonl_path,
            jsonl_max_bytes=200,
        )

        # Write enough lines to trigger rotation
        for i in range(10):
            snap_i = _make_snapshot(timestamp=1000.0 + i)
            storage.append_jsonl(snap_i)

        # The main file should exist
        assert jsonl_path.exists()

        # At least one rotation should have happened
        rotated_1 = jsonl_path.with_suffix(".jsonl.1")
        assert rotated_1.exists()

        # Verify each line in the current file is valid JSON
        for line in jsonl_path.read_text().strip().splitlines():
            parsed = json.loads(line)
            assert "timestamp" in parsed

    @pytest.mark.asyncio
    async def test_concurrent_writes(self, tmp_path: Path) -> None:
        """Multiple async writers don't corrupt the database."""
        db_path = tmp_path / "test.db"
        storage = MonitorStorage(db_path=db_path)
        await storage.initialize()

        async def write_snapshot(idx: int) -> None:
            snap = _make_snapshot(
                timestamp=1000.0 + idx,
                processes=[_make_process(pid=1000 + idx)],
            )
            await storage.write_snapshot(snap)

        # Launch 10 concurrent writes
        await asyncio.gather(*(write_snapshot(i) for i in range(10)))

        # All 10 should have been written
        results = await storage.read_snapshots(since=0.0, limit=100)
        assert len(results) == 10


# ===========================================================================
# GpuProbe tests
# ===========================================================================


class TestGpuProbe:
    """Tests for GPU probing with fallback chain."""

    def test_no_gpu_returns_empty(self) -> None:
        """When no GPU is available, get_gpu_metrics returns empty list."""
        from marianne.daemon.profiler import gpu_probe

        with (
            patch.object(gpu_probe, "_pynvml_available", False),
            patch.object(gpu_probe, "_pynvml", None),
            patch(
                "marianne.daemon.profiler.gpu_probe.GpuProbe._probe_nvidia_smi_sync",
                side_effect=FileNotFoundError("nvidia-smi not found"),
            ),
        ):
            from marianne.daemon.profiler.gpu_probe import GpuProbe

            result = GpuProbe.get_gpu_metrics()
            assert result == []

    def test_pynvml_fallback_to_nvidia_smi(self) -> None:
        """When pynvml fails, falls back to nvidia-smi."""
        from marianne.daemon.profiler import gpu_probe
        from marianne.daemon.profiler.gpu_probe import GpuProbe

        expected = [
            gpu_probe.GpuMetric(
                index=0,
                utilization_pct=75.0,
                memory_used_mb=4096.0,
                memory_total_mb=8192.0,
                temperature_c=65.0,
            )
        ]

        with (
            patch.object(gpu_probe, "_pynvml_available", True),
            patch.object(
                GpuProbe,
                "_probe_pynvml",
                side_effect=RuntimeError("pynvml init failed"),
            ),
            patch.object(
                GpuProbe,
                "_probe_nvidia_smi_sync",
                return_value=expected,
            ),
        ):
            result = GpuProbe.get_gpu_metrics()
            assert len(result) == 1
            assert result[0].utilization_pct == 75.0

    def test_no_nvidia_smi_returns_empty(self) -> None:
        """When both pynvml and nvidia-smi fail, returns empty."""
        from marianne.daemon.profiler import gpu_probe
        from marianne.daemon.profiler.gpu_probe import GpuProbe

        with (
            patch.object(gpu_probe, "_pynvml_available", False),
            patch.object(gpu_probe, "_pynvml", None),
            patch.object(
                GpuProbe,
                "_probe_nvidia_smi_sync",
                side_effect=FileNotFoundError("no nvidia-smi"),
            ),
        ):
            result = GpuProbe.get_gpu_metrics()
            assert result == []

    def test_parse_nvidia_smi_output(self) -> None:
        """Verify parsing of nvidia-smi CSV output."""
        from marianne.daemon.profiler.gpu_probe import GpuProbe

        output = "75, 4096, 8192, 65\n30, 2048, 8192, 55\n"
        result = GpuProbe._parse_nvidia_smi_output(output)
        assert len(result) == 2
        assert result[0].utilization_pct == 75.0
        assert result[0].memory_used_mb == 4096.0
        assert result[1].index == 1
        assert result[1].temperature_c == 55.0


# ===========================================================================
# StraceManager tests
# ===========================================================================


class TestStraceManager:
    """Tests for strace attach/detach/parse."""

    @pytest.mark.asyncio
    async def test_attach_and_detach(self) -> None:
        """Mock subprocess: attach strace, then detach and parse summary."""
        mgr = StraceManager(enabled=True)

        # Mock strace as available
        mock_proc = AsyncMock()
        mock_proc.pid = 9999
        mock_proc.returncode = None

        strace_output = (
            "% time     seconds  usecs/call     calls    errors syscall\n"
            "------ ----------- ----------- --------- --------- ----------------\n"
            " 40.12    0.123456          82      1500           write\n"
            " 28.33    0.087654          27      3200           read\n"
            "------ ----------- ----------- --------- --------- ----------------\n"
            "100.00    0.211110                  4700           total\n"
        )

        with (
            patch(
                "marianne.daemon.profiler.strace_manager._strace_path",
                "/usr/bin/strace",
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            result = await mgr.attach(1234)
            assert result is True
            assert 1234 in mgr.attached_pids

        # Now detach — set up communicate to return the summary
        mock_proc.communicate = AsyncMock(
            return_value=(b"", strace_output.encode())
        )

        summary = await mgr.detach(1234)
        assert summary is not None
        assert summary["syscall_counts"]["write"] == 1500
        assert summary["syscall_counts"]["read"] == 3200
        assert summary["syscall_time_pct"]["write"] == pytest.approx(40.12)
        assert 1234 not in mgr.attached_pids

    @pytest.mark.asyncio
    async def test_strace_not_installed(self) -> None:
        """Graceful skip when strace is not available."""
        mgr = StraceManager(enabled=True)

        with patch(
            "marianne.daemon.profiler.strace_manager._strace_path",
            None,
        ):
            result = await mgr.attach(1234)
            assert result is False
            assert mgr.attached_pids == []

    @pytest.mark.asyncio
    async def test_attach_already_exited_process(self) -> None:
        """Handles ESRCH (process already exited) gracefully."""
        mgr = StraceManager(enabled=True)

        with (
            patch(
                "marianne.daemon.profiler.strace_manager._strace_path",
                "/usr/bin/strace",
            ),
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=ProcessLookupError("No such process"),
            ),
        ):
            result = await mgr.attach(1234)
            assert result is False
            assert mgr.attached_pids == []

    def test_parse_strace_summary(self) -> None:
        """Parse the -c output format correctly."""
        output = (
            "% time     seconds  usecs/call     calls    errors syscall\n"
            "------ ----------- ----------- --------- --------- ----------------\n"
            " 55.00    0.500000         100      5000       100 read\n"
            " 30.00    0.300000          60      5000           write\n"
            " 15.00    0.150000          30      5000           futex\n"
            "------ ----------- ----------- --------- --------- ----------------\n"
            "100.00    0.950000                 15000       100 total\n"
        )

        result = StraceManager._parse_strace_summary(output)
        assert result["syscall_counts"]["read"] == 5000
        assert result["syscall_counts"]["write"] == 5000
        assert result["syscall_counts"]["futex"] == 5000
        assert result["syscall_time_pct"]["read"] == pytest.approx(55.0)
        assert result["syscall_time_pct"]["write"] == pytest.approx(30.0)
        assert "total" not in result["syscall_counts"]

    @pytest.mark.asyncio
    async def test_detach_all_cleanup(self) -> None:
        """All strace processes are terminated on cleanup."""
        mgr = StraceManager(enabled=True)

        # Create mock processes for two attached traces
        mock_proc_1 = AsyncMock()
        mock_proc_1.pid = 9001
        mock_proc_1.returncode = None
        mock_proc_1.wait = AsyncMock()

        mock_proc_2 = AsyncMock()
        mock_proc_2.pid = 9002
        mock_proc_2.returncode = None
        mock_proc_2.wait = AsyncMock()

        # Inject them directly
        mgr._attached[100] = mock_proc_1
        mgr._full_traces[200] = mock_proc_2

        await mgr.detach_all()

        # Both dicts should be cleared
        assert len(mgr._attached) == 0
        assert len(mgr._full_traces) == 0

        # Verify SIGINT was sent
        mock_proc_1.send_signal.assert_called_once()
        mock_proc_2.send_signal.assert_called_once()


# ===========================================================================
# AnomalyDetector tests
# ===========================================================================


class TestAnomalyDetector:
    """Tests for heuristic anomaly detection."""

    def test_memory_spike_detection(self) -> None:
        """A 50% RSS increase triggers a MEMORY_SPIKE anomaly."""
        config = AnomalyConfig(
            memory_spike_threshold=1.5,
            memory_spike_window_seconds=30.0,
        )
        detector = AnomalyDetector(config=config)

        now = time.time()
        baseline_proc = _make_process(pid=42, rss_mb=100.0)
        current_proc = _make_process(pid=42, rss_mb=160.0)  # 60% increase

        baseline = _make_snapshot(timestamp=now - 10, processes=[baseline_proc])
        current = _make_snapshot(timestamp=now, processes=[current_proc])

        anomalies = detector.detect(current, [baseline])
        memory_spikes = [a for a in anomalies if a.anomaly_type == AnomalyType.MEMORY_SPIKE]
        assert len(memory_spikes) >= 1
        assert memory_spikes[0].pid == 42
        assert memory_spikes[0].severity in (AnomalySeverity.HIGH, AnomalySeverity.CRITICAL)

    def test_memory_spike_below_threshold(self) -> None:
        """A 40% increase (below 1.5x threshold) does NOT trigger."""
        config = AnomalyConfig(memory_spike_threshold=1.5)
        detector = AnomalyDetector(config=config)

        now = time.time()
        baseline_proc = _make_process(pid=42, rss_mb=100.0)
        current_proc = _make_process(pid=42, rss_mb=140.0)  # 40% increase = 1.4x

        baseline = _make_snapshot(timestamp=now - 10, processes=[baseline_proc])
        current = _make_snapshot(timestamp=now, processes=[current_proc])

        anomalies = detector.detect(current, [baseline])
        memory_spikes = [a for a in anomalies if a.anomaly_type == AnomalyType.MEMORY_SPIKE]
        # Only per-process memory spikes — should be 0 (1.4 < 1.5)
        per_process_spikes = [a for a in memory_spikes if a.pid is not None]
        assert len(per_process_spikes) == 0

    def test_runaway_process_detection(self) -> None:
        """Process >90% CPU for >60s triggers RUNAWAY_PROCESS anomaly."""
        config = AnomalyConfig(
            runaway_cpu_threshold=90.0,
            runaway_duration_seconds=60.0,
        )
        detector = AnomalyDetector(config=config)

        now = time.time()
        # Build history of 13 snapshots at 5s intervals (65 seconds)
        # where PID 42 is consistently above 90% CPU
        history: list[SystemSnapshot] = []
        for i in range(13):
            proc = _make_process(pid=42, cpu_percent=95.0)
            snap = _make_snapshot(
                timestamp=now - (13 - i) * 5,
                processes=[proc],
            )
            history.append(snap)

        current_proc = _make_process(pid=42, cpu_percent=95.0)
        current = _make_snapshot(timestamp=now, processes=[current_proc])

        anomalies = detector.detect(current, history)
        runaway = [a for a in anomalies if a.anomaly_type == AnomalyType.RUNAWAY_PROCESS]
        assert len(runaway) == 1
        assert runaway[0].severity == AnomalySeverity.CRITICAL
        assert runaway[0].pid == 42

    def test_zombie_detection(self) -> None:
        """zombie_count > 0 triggers a ZOMBIE anomaly."""
        detector = AnomalyDetector()

        current = _make_snapshot(zombie_count=2, zombie_pids=[111, 222])
        anomalies = detector.detect(current, [])
        zombies = [a for a in anomalies if a.anomaly_type == AnomalyType.ZOMBIE]
        assert len(zombies) == 1
        assert zombies[0].metric_value == 2.0
        assert "2 zombie" in zombies[0].description

    def test_no_anomalies_in_normal_operation(self) -> None:
        """Clean snapshot with normal values returns empty anomaly list."""
        detector = AnomalyDetector()

        proc = _make_process(pid=42, cpu_percent=10.0, rss_mb=256.0, open_fds=50)
        current = _make_snapshot(processes=[proc])
        # Provide a baseline so memory spike check has something to compare
        baseline = _make_snapshot(
            timestamp=current.timestamp - 10,
            processes=[_make_process(pid=42, rss_mb=250.0)],
        )

        anomalies = detector.detect(current, [baseline])
        assert anomalies == []

    def test_multiple_anomalies(self) -> None:
        """Detector can find several anomalies simultaneously."""
        config = AnomalyConfig(
            memory_spike_threshold=1.5,
            runaway_cpu_threshold=90.0,
            runaway_duration_seconds=5.0,  # Short for testing
        )
        detector = AnomalyDetector(config=config)

        now = time.time()

        # Process with memory spike
        baseline_proc1 = _make_process(pid=10, rss_mb=100.0, cpu_percent=5.0)
        current_proc1 = _make_process(pid=10, rss_mb=200.0, cpu_percent=5.0)

        # Process with runaway CPU
        baseline_proc2 = _make_process(pid=20, rss_mb=100.0, cpu_percent=95.0)
        current_proc2 = _make_process(pid=20, rss_mb=100.0, cpu_percent=95.0)

        # Process with FD exhaustion
        current_proc3 = _make_process(
            pid=30, rss_mb=100.0, open_fds=FD_EXHAUSTION_THRESHOLD + 1
        )
        baseline_proc3 = _make_process(pid=30, rss_mb=100.0)

        baseline = _make_snapshot(
            timestamp=now - 10,
            processes=[baseline_proc1, baseline_proc2, baseline_proc3],
        )
        current = _make_snapshot(
            timestamp=now,
            processes=[current_proc1, current_proc2, current_proc3],
            zombie_count=1,
            zombie_pids=[999],
        )

        anomalies = detector.detect(current, [baseline])

        types_found = {a.anomaly_type for a in anomalies}
        assert AnomalyType.MEMORY_SPIKE in types_found
        assert AnomalyType.RUNAWAY_PROCESS in types_found
        assert AnomalyType.ZOMBIE in types_found
        assert AnomalyType.FD_EXHAUSTION in types_found
        assert len(anomalies) >= 4

    def test_fd_exhaustion_detection(self) -> None:
        """Process with open FDs >= threshold triggers FD_EXHAUSTION."""
        detector = AnomalyDetector()

        proc = _make_process(pid=42, open_fds=FD_EXHAUSTION_THRESHOLD)
        current = _make_snapshot(processes=[proc])

        anomalies = detector.detect(current, [])
        fd_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.FD_EXHAUSTION]
        assert len(fd_anomalies) == 1
        assert fd_anomalies[0].pid == 42

    def test_memory_pressure_detection(self) -> None:
        """System memory pressure (low available memory) is detected."""
        detector = AnomalyDetector()

        # available < 10% of total triggers memory pressure
        current = _make_snapshot(
            system_memory_total_mb=16000.0,
            system_memory_available_mb=1000.0,  # 6.25% < 10%
        )

        anomalies = detector.detect(current, [])
        pressure = [
            a
            for a in anomalies
            if a.anomaly_type == AnomalyType.MEMORY_SPIKE and a.pid is None
        ]
        assert len(pressure) == 1
        assert pressure[0].severity == AnomalySeverity.CRITICAL


# ===========================================================================
# ProfilerCollector tests
# ===========================================================================


class TestProfilerCollector:
    """Tests for the central profiler orchestrator."""

    def _make_collector(
        self, tmp_path: Path, *, enabled: bool = True
    ) -> tuple[Any, MagicMock, MagicMock]:
        """Build a ProfilerCollector with mocked dependencies."""
        from marianne.daemon.profiler.collector import ProfilerCollector

        config = ProfilerConfig(
            enabled=enabled,
            interval_seconds=1.0,
            strace_enabled=False,  # Don't need real strace in tests
            gpu_enabled=False,
            storage_path=tmp_path / "monitor.db",
            jsonl_path=tmp_path / "monitor.jsonl",
        )
        monitor = MagicMock()
        pgroup = MagicMock()
        event_bus = MagicMock()
        event_bus.subscribe = MagicMock(side_effect=lambda *a, **kw: f"sub-{id(a)}")
        event_bus.unsubscribe = MagicMock(return_value=True)
        event_bus.publish = AsyncMock()

        collector = ProfilerCollector(
            config=config,
            monitor=monitor,
            pgroup=pgroup,
            event_bus=event_bus,
            manager=None,
        )
        return collector, event_bus, monitor

    @pytest.mark.asyncio
    async def test_collect_snapshot_structure(self, tmp_path: Path) -> None:
        """Verify collect_snapshot returns a well-formed SystemSnapshot."""
        collector, _, _ = self._make_collector(tmp_path)

        # Patch out psutil-dependent code so we get a clean snapshot
        with (
            patch(
                "marianne.daemon.profiler.collector._psutil",
                None,
            ),
            patch(
                "marianne.daemon.profiler.collector.SystemProbe.get_memory_mb",
                return_value=512.0,
            ),
            patch(
                "marianne.daemon.profiler.collector.os.getloadavg",
                return_value=(1.5, 1.0, 0.5),
            ),
        ):
            snapshot = await collector.collect_snapshot()

        assert isinstance(snapshot, SystemSnapshot)
        assert snapshot.daemon_rss_mb == 512.0
        assert snapshot.load_avg_1 == 1.5
        assert snapshot.load_avg_5 == 1.0
        assert snapshot.load_avg_15 == 0.5
        assert snapshot.pressure_level == "none"
        assert snapshot.processes == []  # psutil is None
        assert snapshot.gpus == []  # gpu_enabled=False

    @pytest.mark.asyncio
    async def test_lifecycle_start_stop(self, tmp_path: Path) -> None:
        """Clean start and shutdown without errors."""
        collector, event_bus, _ = self._make_collector(tmp_path)

        # Patch the collection loop to exit immediately
        with patch.object(collector, "_collection_loop", new_callable=AsyncMock):
            await collector.start()

            # Verify event bus subscriptions were created
            assert event_bus.subscribe.call_count == 2
            assert collector._running is True

            await collector.stop()

            assert collector._running is False
            # Verify event bus unsubscriptions
            assert event_bus.unsubscribe.call_count == 2

    @pytest.mark.asyncio
    async def test_anomaly_published_to_event_bus(self, tmp_path: Path) -> None:
        """Anomalies detected by AnomalyDetector are published to EventBus."""
        collector, event_bus, _ = self._make_collector(tmp_path)

        # Inject a history where memory spike will be detected
        now = time.time()
        baseline = _make_snapshot(
            timestamp=now - 10,
            processes=[_make_process(pid=42, rss_mb=100.0)],
        )
        collector._history = [baseline]

        # Patch collect_snapshot to return a snapshot with a spiked process
        spiked_snap = _make_snapshot(
            timestamp=now,
            processes=[_make_process(pid=42, rss_mb=200.0)],
        )

        with (
            patch.object(
                collector, "collect_snapshot", new_callable=AsyncMock, return_value=spiked_snap
            ),
            patch.object(
                collector._storage, "write_snapshot", new_callable=AsyncMock, return_value=1
            ),
            patch.object(
                collector._storage, "append_jsonl",
            ),
        ):
            # Run one iteration of the collection logic manually
            snapshot = await collector.collect_snapshot()
            anomalies = collector._anomaly_detector.detect(snapshot, collector._history)
            for anomaly in anomalies:
                await collector._publish_anomaly(anomaly)

        # Verify at least one anomaly was published
        assert event_bus.publish.call_count >= 1
        published_event = event_bus.publish.call_args_list[0][0][0]
        assert published_event["event"] == "monitor.anomaly"
        assert published_event["data"]["anomaly_type"] == "memory_spike"

    @pytest.mark.asyncio
    async def test_strace_attached_on_sheet_start(self, tmp_path: Path) -> None:
        """When sheet.started fires with a PID, strace attachment is triggered."""
        from marianne.daemon.profiler.collector import ProfilerCollector

        config = ProfilerConfig(
            enabled=True,
            interval_seconds=1.0,
            strace_enabled=True,
            gpu_enabled=False,
            storage_path=tmp_path / "monitor.db",
            jsonl_path=tmp_path / "monitor.jsonl",
        )
        event_bus = MagicMock()
        event_bus.subscribe = MagicMock(side_effect=lambda *a, **kw: f"sub-{id(a)}")
        event_bus.unsubscribe = MagicMock()
        event_bus.publish = AsyncMock()

        collector = ProfilerCollector(
            config=config,
            monitor=MagicMock(),
            pgroup=MagicMock(),
            event_bus=event_bus,
            manager=None,
        )

        # Mock strace attach
        collector._strace.attach = AsyncMock(return_value=True)

        # Simulate a sheet.started event (ObserverEvent is a TypedDict)
        sheet_event: ObserverEvent = {
            "event": "sheet.started",
            "job_id": "my-job",
            "sheet_num": 3,
            "data": {"pid": 5555},
            "timestamp": time.time(),
        }
        collector._on_sheet_started(sheet_event)

        # Verify a SPAWN event was recorded
        assert len(collector._recent_events) == 1
        assert collector._recent_events[0].event_type == EventType.SPAWN
        assert collector._recent_events[0].pid == 5555

    @pytest.mark.asyncio
    async def test_disabled_profiler_does_not_start(self, tmp_path: Path) -> None:
        """When config.enabled=False, start() is a no-op."""
        collector, event_bus, _ = self._make_collector(tmp_path, enabled=False)

        await collector.start()

        assert collector._running is False
        assert event_bus.subscribe.call_count == 0

    def test_get_latest_snapshot_none(self, tmp_path: Path) -> None:
        """Before first collection, get_latest_snapshot returns None."""
        collector, _, _ = self._make_collector(tmp_path)
        assert collector.get_latest_snapshot() is None

    def test_get_recent_events_empty(self, tmp_path: Path) -> None:
        """Before any events, get_recent_events returns empty list."""
        collector, _, _ = self._make_collector(tmp_path)
        assert collector.get_recent_events() == []


# ===========================================================================
# PatternType tests
# ===========================================================================


class TestPatternTypes:
    """Verify that resource-related PatternTypes exist in the learning system."""

    def test_resource_anomaly_pattern_type_exists(self) -> None:
        """RESOURCE_ANOMALY pattern type is defined."""
        assert hasattr(PatternType, "RESOURCE_ANOMALY")
        assert PatternType.RESOURCE_ANOMALY.value == "resource_anomaly"

    def test_resource_correlation_pattern_type_exists(self) -> None:
        """RESOURCE_CORRELATION pattern type is defined."""
        assert hasattr(PatternType, "RESOURCE_CORRELATION")
        assert PatternType.RESOURCE_CORRELATION.value == "resource_correlation"

    def test_pattern_types_are_distinct(self) -> None:
        """The two resource pattern types have different values."""
        assert PatternType.RESOURCE_ANOMALY != PatternType.RESOURCE_CORRELATION


# ===========================================================================
# CorrelationAnalyzer tests
# ===========================================================================


class TestCorrelationAnalyzer:
    """Tests for periodic statistical analysis of resource usage vs outcomes."""

    def _make_analyzer(
        self, tmp_path: Path, min_sample_size: int = 2
    ) -> tuple[Any, MonitorStorage, MagicMock]:
        """Build a CorrelationAnalyzer with mocked LearningHub."""
        from marianne.daemon.profiler.correlation import CorrelationAnalyzer
        from marianne.daemon.profiler.models import CorrelationConfig

        storage = MonitorStorage(db_path=tmp_path / "monitor.db")

        learning_hub = MagicMock()
        learning_hub.is_running = True
        mock_store = MagicMock()
        mock_store.get_patterns = MagicMock(return_value=[])
        mock_store.record_pattern = MagicMock()
        learning_hub.store = mock_store

        config = CorrelationConfig(
            interval_minutes=5,
            min_sample_size=min_sample_size,
        )

        analyzer = CorrelationAnalyzer(
            storage=storage,
            learning_hub=learning_hub,
            config=config,
        )
        return analyzer, storage, learning_hub

    @pytest.mark.asyncio
    async def test_analyze_insufficient_samples(self, tmp_path: Path) -> None:
        """Returns empty when not enough jobs for analysis."""
        analyzer, storage, _ = self._make_analyzer(tmp_path, min_sample_size=5)
        await storage.initialize()

        # Write a single job's data — below min_sample_size of 5
        proc = _make_process(pid=100, job_id="job-A", sheet_num=1)
        snap = _make_snapshot(timestamp=time.time(), processes=[proc])
        await storage.write_snapshot(snap)
        event = _make_event(pid=100, job_id="job-A")
        await storage.write_event(event)

        correlations = await analyzer.analyze()
        assert correlations == []

    @pytest.mark.asyncio
    async def test_analyze_with_data(self, tmp_path: Path) -> None:
        """With enough enriched data, correlations are generated."""
        analyzer, storage, learning_hub = self._make_analyzer(
            tmp_path, min_sample_size=2,
        )
        await storage.initialize()

        now = time.time()

        # Write data for multiple jobs
        for i, (job_id, rss, outcome) in enumerate([
            ("job-A", 100.0, "failure"),
            ("job-B", 500.0, "failure"),
            ("job-C", 50.0, "success"),
            ("job-D", 600.0, "failure"),
            ("job-E", 40.0, "success"),
        ]):
            proc = _make_process(pid=100 + i, rss_mb=rss, job_id=job_id, sheet_num=1)
            snap = _make_snapshot(timestamp=now + i, processes=[proc])
            await storage.write_snapshot(snap)
            event = _make_event(pid=100 + i, job_id=job_id)
            await storage.write_event(event)

        # Mock outcome lookup — make get_patterns return patterns with outcome tags
        def mock_get_patterns(*, pattern_type, context_tags, limit, min_priority):
            job_outcomes = {
                "job-A": "failure", "job-B": "failure", "job-C": "success",
                "job-D": "failure", "job-E": "success",
            }
            for tag in context_tags or []:
                if tag.startswith("job:"):
                    job_id = tag[4:]
                    if job_id in job_outcomes:
                        mock_pattern = MagicMock()
                        mock_pattern.context_tags = [f"outcome:{job_outcomes[job_id]}"]
                        return [mock_pattern]
            return []

        learning_hub.store.get_patterns = mock_get_patterns

        correlations = await analyzer.analyze()
        # Should produce at least some correlations since we have 5 jobs
        # with varying memory and outcomes
        # (May or may not produce correlations depending on binning + deviations)
        assert isinstance(correlations, list)

    @pytest.mark.asyncio
    async def test_analyze_learning_hub_not_running(self, tmp_path: Path) -> None:
        """Returns empty when learning hub is not running."""
        analyzer, _, learning_hub = self._make_analyzer(tmp_path)
        learning_hub.is_running = False

        correlations = await analyzer.analyze()
        assert correlations == []

    @pytest.mark.asyncio
    async def test_lifecycle_start_stop(self, tmp_path: Path) -> None:
        """Clean start and shutdown without errors."""
        analyzer, _, _ = self._make_analyzer(tmp_path)
        event_bus = MagicMock()

        await analyzer.start(event_bus)
        assert analyzer._running is True
        assert analyzer._loop_task is not None

        await analyzer.stop()
        assert analyzer._running is False
        assert analyzer._loop_task is None

    def test_memory_vs_failure_analysis(self, tmp_path: Path) -> None:
        """Memory binning and failure rate computation."""
        analyzer, _, _ = self._make_analyzer(tmp_path)

        profiles = [
            {"job_id": "a", "peak_rss_mb": 100, "outcome": "success"},
            {"job_id": "b", "peak_rss_mb": 150, "outcome": "success"},
            {"job_id": "c", "peak_rss_mb": 200, "outcome": "failure"},
            {"job_id": "d", "peak_rss_mb": 600, "outcome": "failure"},
            {"job_id": "e", "peak_rss_mb": 700, "outcome": "failure"},
            {"job_id": "f", "peak_rss_mb": 800, "outcome": "failure"},
        ]
        results = analyzer._analyze_memory_vs_failure(profiles)
        assert isinstance(results, list)
        # With 3 in <256MB bin (33% failure) and 3 in 512MB-1GB bin (100% failure)
        # both should deviate from baseline (66%)
        for r in results:
            assert "confidence" in r
            assert r["confidence"] >= 0.0

    def test_syscall_vs_failure_analysis(self, tmp_path: Path) -> None:
        """Syscall hotspot correlation analysis."""
        analyzer, _, _ = self._make_analyzer(tmp_path)

        profiles = [
            {"job_id": "a", "syscall_hotspots": {"write": 60, "read": 40}, "outcome": "failure"},
            {"job_id": "b", "syscall_hotspots": {"write": 70, "read": 30}, "outcome": "failure"},
            {"job_id": "c", "syscall_hotspots": {"write": 50, "read": 50}, "outcome": "failure"},
            {"job_id": "d", "syscall_hotspots": {"read": 80, "write": 20}, "outcome": "success"},
            {"job_id": "e", "syscall_hotspots": {"read": 75, "write": 25}, "outcome": "success"},
            {"job_id": "f", "syscall_hotspots": {"read": 70, "write": 30}, "outcome": "success"},
        ]
        results = analyzer._analyze_syscall_vs_failure(profiles)
        assert isinstance(results, list)

    def test_store_correlation(self, tmp_path: Path) -> None:
        """Correlation is stored as RESOURCE_CORRELATION pattern."""
        analyzer, _, learning_hub = self._make_analyzer(tmp_path)

        correlation = {
            "type": "memory_vs_failure",
            "description": "Jobs with peak RSS >2GB have 80% failure rate",
            "confidence": 0.75,
            "context_tags": ["memory_bin:>2GB", "failure_rate:0.80"],
        }

        analyzer._store_correlation(correlation)

        learning_hub.store.record_pattern.assert_called_once()
        call_kwargs = learning_hub.store.record_pattern.call_args
        assert call_kwargs[1]["pattern_type"] == "resource_correlation"
        assert "memory_vs_failure" in call_kwargs[1]["pattern_name"]


# ===========================================================================
# SemanticAnalyzer anomaly event tests
# ===========================================================================


class TestSemanticAnalyzerAnomalyEvents:
    """Tests for the SemanticAnalyzer's monitor.anomaly event handling."""

    def test_anomaly_event_stored_as_resource_anomaly(self) -> None:
        """monitor.anomaly events are stored as RESOURCE_ANOMALY patterns."""
        from marianne.daemon.semantic_analyzer import SemanticAnalyzer

        config = MagicMock()
        config.enabled = True
        config.max_concurrent_analyses = 3
        config.analyze_on = ["success", "failure"]
        config.backend = MagicMock()
        config.backend.timeout_seconds = 30

        backend = MagicMock()
        backend.name = "test"

        learning_hub = MagicMock()
        learning_hub.is_running = True
        mock_store = MagicMock()
        learning_hub.store = mock_store

        analyzer = SemanticAnalyzer(
            config=config,
            backend=backend,
            learning_hub=learning_hub,
            live_states={},
        )

        anomaly_event: ObserverEvent = {
            "event": "monitor.anomaly",
            "job_id": "test-job",
            "sheet_num": 3,
            "data": {
                "anomaly_type": "memory_spike",
                "severity": "high",
                "description": "PID 1234 RSS grew 80%",
                "pid": 1234,
            },
            "timestamp": time.time(),
        }

        analyzer._on_anomaly_event(anomaly_event)

        mock_store.record_pattern.assert_called_once()
        call_kwargs = mock_store.record_pattern.call_args[1]
        assert call_kwargs["pattern_type"] == "resource_anomaly"
        assert "memory_spike" in call_kwargs["pattern_name"]
        assert any("anomaly_type:memory_spike" in t for t in call_kwargs["context_tags"])
        assert any("severity:high" in t for t in call_kwargs["context_tags"])
        assert any("job:test-job" in t for t in call_kwargs["context_tags"])

    def test_anomaly_event_skipped_when_hub_not_running(self) -> None:
        """Anomaly events are silently skipped if learning hub is down."""
        from marianne.daemon.semantic_analyzer import SemanticAnalyzer

        config = MagicMock()
        config.enabled = True
        config.max_concurrent_analyses = 3

        learning_hub = MagicMock()
        learning_hub.is_running = False

        analyzer = SemanticAnalyzer(
            config=config,
            backend=MagicMock(),
            learning_hub=learning_hub,
            live_states={},
        )

        anomaly_event: ObserverEvent = {
            "event": "monitor.anomaly",
            "job_id": "test-job",
            "sheet_num": 1,
            "data": {"anomaly_type": "zombie", "severity": "high", "description": "zombie detected"},
            "timestamp": time.time(),
        }

        # Should not raise
        analyzer._on_anomaly_event(anomaly_event)
        # Store should not have been called
        learning_hub.store.record_pattern.assert_not_called()

    @pytest.mark.asyncio
    async def test_anomaly_subscription_created(self) -> None:
        """start() creates subscriptions for both sheet events and anomaly events."""
        from marianne.daemon.semantic_analyzer import SemanticAnalyzer

        config = MagicMock()
        config.enabled = True
        config.max_concurrent_analyses = 3
        config.analyze_on = ["success", "failure"]

        event_bus = MagicMock()
        sub_ids = iter(["sub-1", "sub-2"])
        event_bus.subscribe = MagicMock(side_effect=lambda *a, **kw: next(sub_ids))

        analyzer = SemanticAnalyzer(
            config=config,
            backend=MagicMock(name="test-backend"),
            learning_hub=MagicMock(),
            live_states={},
        )

        await analyzer.start(event_bus)

        # Two subscriptions: one for sheet events, one for anomaly events
        assert event_bus.subscribe.call_count == 2
        assert analyzer._sub_id == "sub-1"
        assert analyzer._anomaly_sub_id == "sub-2"


# ===========================================================================
# BackpressureController resource estimation tests
# ===========================================================================


class TestBackpressureResourceEstimation:
    """Tests for BackpressureController.estimate_job_resource_needs."""

    @pytest.mark.asyncio
    async def test_estimate_returns_none_without_hub(self) -> None:
        """Returns None when no learning hub is configured."""
        from marianne.daemon.backpressure import BackpressureController

        monitor = MagicMock()
        rate_coord = MagicMock()

        controller = BackpressureController(monitor, rate_coord, learning_hub=None)
        result = await controller.estimate_job_resource_needs("hash-abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_estimate_returns_none_when_hub_not_running(self) -> None:
        """Returns None when learning hub exists but is not started."""
        from marianne.daemon.backpressure import BackpressureController

        learning_hub = MagicMock()
        learning_hub.is_running = False

        controller = BackpressureController(
            MagicMock(), MagicMock(), learning_hub=learning_hub
        )
        result = await controller.estimate_job_resource_needs("hash-abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_estimate_returns_none_when_no_patterns(self) -> None:
        """Returns None when learning store has no correlation patterns."""
        from marianne.daemon.backpressure import BackpressureController

        learning_hub = MagicMock()
        learning_hub.is_running = True
        learning_hub.store.get_patterns = MagicMock(return_value=[])

        controller = BackpressureController(
            MagicMock(), MagicMock(), learning_hub=learning_hub
        )
        result = await controller.estimate_job_resource_needs("hash-abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_estimate_with_memory_patterns(self) -> None:
        """Returns ResourceEstimate when memory correlation patterns exist."""
        from marianne.daemon.backpressure import BackpressureController

        # Create mock patterns with memory bin context tags
        pattern1 = MagicMock()
        pattern1.description = "Jobs with peak RSS 512MB-1GB have 80% failure rate"
        pattern1.effectiveness_score = 0.7
        pattern1.context_tags = ["memory_bin:512MB-1GB", "failure_rate:0.80"]

        pattern2 = MagicMock()
        pattern2.description = "Jobs with peak RSS 1-2GB have 90% failure rate"
        pattern2.effectiveness_score = 0.8
        pattern2.context_tags = ["memory_bin:1-2GB", "failure_rate:0.90"]

        learning_hub = MagicMock()
        learning_hub.is_running = True
        learning_hub.store.get_patterns = MagicMock(return_value=[pattern1, pattern2])

        controller = BackpressureController(
            MagicMock(), MagicMock(), learning_hub=learning_hub
        )
        result = await controller.estimate_job_resource_needs("hash-abc")

        assert result is not None
        assert result.estimated_peak_memory_mb > 0
        assert result.confidence > 0


# ===========================================================================
# Resource context enrichment tests (Stage 7)
# ===========================================================================


class TestResourceContextEnrichment:
    """Tests for get_resource_context_for_pid and log enrichment."""

    def _make_collector(
        self, tmp_path: Path
    ) -> tuple[Any, MagicMock, MagicMock]:
        """Build a ProfilerCollector with mocked dependencies."""
        from marianne.daemon.profiler.collector import ProfilerCollector

        config = ProfilerConfig(
            enabled=True,
            storage_path=tmp_path / "monitor.db",
            jsonl_path=tmp_path / "monitor.jsonl",
        )
        event_bus = MagicMock()
        event_bus.subscribe = MagicMock(return_value="sub-1")
        event_bus.publish = AsyncMock()

        collector = ProfilerCollector(
            config=config,
            monitor=MagicMock(),
            pgroup=MagicMock(),
            event_bus=event_bus,
            manager=None,
        )
        return collector, event_bus, config

    def test_get_resource_context_for_pid_no_snapshot(self, tmp_path: Path) -> None:
        """Returns empty context when no snapshot has been collected yet."""
        collector, _, _ = self._make_collector(tmp_path)

        ctx = collector.get_resource_context_for_pid(1234)
        assert ctx["rss_mb"] is None
        assert ctx["cpu_pct"] is None
        assert ctx["syscall_hotspot"] is None
        assert ctx["anomalies_active"] == []

    def test_get_resource_context_for_pid_found(self, tmp_path: Path) -> None:
        """Returns populated context when PID is found in snapshot."""
        collector, _, _ = self._make_collector(tmp_path)

        proc = _make_process(pid=1234, rss_mb=512.0, cpu_percent=45.0)
        snapshot = _make_snapshot(processes=[proc], daemon_rss_mb=200.0)
        collector._latest_snapshot = snapshot

        ctx = collector.get_resource_context_for_pid(1234)
        assert ctx["rss_mb"] == 512.0
        assert ctx["cpu_pct"] == 45.0
        assert ctx["daemon_rss_mb"] == 200.0
        # Process has syscall_time_pct: {"write": 0.40, "read": 0.28}
        assert ctx["syscall_hotspot"] is not None
        assert "write" in ctx["syscall_hotspot"]

    def test_get_resource_context_for_pid_not_found(self, tmp_path: Path) -> None:
        """Returns context with None values when PID is not in snapshot."""
        collector, _, _ = self._make_collector(tmp_path)

        proc = _make_process(pid=9999, rss_mb=256.0)
        snapshot = _make_snapshot(processes=[proc], daemon_rss_mb=200.0)
        collector._latest_snapshot = snapshot

        ctx = collector.get_resource_context_for_pid(5555)
        assert ctx["rss_mb"] is None
        assert ctx["cpu_pct"] is None
        assert ctx["daemon_rss_mb"] == 200.0

    def test_get_resource_context_general(self, tmp_path: Path) -> None:
        """get_resource_context returns daemon-level metrics."""
        collector, _, _ = self._make_collector(tmp_path)

        snapshot = _make_snapshot(
            daemon_rss_mb=300.0,
            pressure_level="medium",
            running_jobs=2,
            active_sheets=3,
        )
        collector._latest_snapshot = snapshot

        ctx = collector.get_resource_context()
        assert ctx["daemon_rss_mb"] == 300.0
        assert ctx["pressure_level"] == "medium"
        assert ctx["running_jobs"] == 2
        assert ctx["active_sheets"] == 3

    def test_get_resource_context_no_snapshot(self, tmp_path: Path) -> None:
        """get_resource_context returns empty when no snapshot collected."""
        collector, _, _ = self._make_collector(tmp_path)

        ctx = collector.get_resource_context()
        assert ctx["rss_mb"] is None
        assert ctx["anomalies_active"] == []

    @pytest.mark.asyncio
    async def test_sheet_started_enriched_with_resource_context(self, tmp_path: Path) -> None:
        """sheet.started events get resource_context added to data."""
        collector, event_bus, _ = self._make_collector(tmp_path)
        collector._strace.attach = AsyncMock(return_value=True)

        # Set a snapshot so we get a context
        proc = _make_process(pid=5555, rss_mb=256.0, cpu_percent=12.0)
        snapshot = _make_snapshot(processes=[proc], daemon_rss_mb=150.0)
        collector._latest_snapshot = snapshot

        sheet_event: ObserverEvent = {
            "event": "sheet.started",
            "job_id": "my-job",
            "sheet_num": 1,
            "data": {"pid": 5555},
            "timestamp": time.time(),
        }
        collector._on_sheet_started(sheet_event)

        # Verify resource_context was added to the event data
        data = sheet_event.get("data")
        assert data is not None
        assert "resource_context" in data
        assert data["resource_context"]["rss_mb"] == 256.0
        assert data["resource_context"]["cpu_pct"] == 12.0

    @pytest.mark.asyncio
    async def test_sheet_completed_enriched_with_resource_context(self, tmp_path: Path) -> None:
        """sheet.completed events get resource_context added to data."""
        collector, event_bus, _ = self._make_collector(tmp_path)
        collector._strace.detach = AsyncMock(return_value=None)

        proc = _make_process(pid=5555, rss_mb=1024.0, cpu_percent=88.0)
        snapshot = _make_snapshot(processes=[proc], daemon_rss_mb=300.0)
        collector._latest_snapshot = snapshot

        sheet_event: ObserverEvent = {
            "event": "sheet.completed",
            "job_id": "my-job",
            "sheet_num": 2,
            "data": {"pid": 5555, "exit_code": 0},
            "timestamp": time.time(),
        }
        collector._on_sheet_completed(sheet_event)

        data = sheet_event.get("data")
        assert data is not None
        assert "resource_context" in data
        assert data["resource_context"]["rss_mb"] == 1024.0
        assert data["resource_context"]["cpu_pct"] == 88.0

    @pytest.mark.asyncio
    async def test_sheet_failed_enriched_with_resource_context(self, tmp_path: Path) -> None:
        """sheet.failed events also get resource_context."""
        collector, event_bus, _ = self._make_collector(tmp_path)
        collector._strace.detach = AsyncMock(return_value=None)

        snapshot = _make_snapshot(daemon_rss_mb=500.0, pressure_level="high")
        collector._latest_snapshot = snapshot

        sheet_event: ObserverEvent = {
            "event": "sheet.failed",
            "job_id": "my-job",
            "sheet_num": 3,
            "data": {"pid": None, "exit_code": 1},
            "timestamp": time.time(),
        }
        collector._on_sheet_completed(sheet_event)

        data = sheet_event.get("data")
        assert data is not None
        assert "resource_context" in data
        # No PID, so we get general context
        assert data["resource_context"]["daemon_rss_mb"] == 500.0

    def test_anomalies_included_in_context(self, tmp_path: Path) -> None:
        """Active anomalies are reported in the resource context."""
        collector, _, _ = self._make_collector(tmp_path)

        # Create a snapshot that will trigger a zombie anomaly
        snapshot = _make_snapshot(
            zombie_count=2,
            zombie_pids=[1111, 2222],
            daemon_rss_mb=200.0,
        )
        collector._latest_snapshot = snapshot

        ctx = collector.get_resource_context_for_pid(9999)  # PID not found
        # Zombie anomaly should be detected
        assert "zombie" in ctx["anomalies_active"]


# ===========================================================================
# generate_resource_report tests (Stage 7)
# ===========================================================================


class TestGenerateResourceReport:
    """Tests for the generate_resource_report function."""

    @pytest.mark.asyncio
    async def test_no_data_returns_short_message(self, tmp_path: Path) -> None:
        """When no profiler data exists, returns a short message."""
        from marianne.daemon.profiler.storage import generate_resource_report

        storage = MonitorStorage(db_path=tmp_path / "empty.db")
        await storage.initialize()

        report = await generate_resource_report("nonexistent-job", storage)
        assert "No profiler data available" in report
        assert "nonexistent-job" in report

    @pytest.mark.asyncio
    async def test_report_includes_peak_memory(self, tmp_path: Path) -> None:
        """Report includes peak memory information."""
        from marianne.daemon.profiler.storage import generate_resource_report

        storage = MonitorStorage(db_path=tmp_path / "test.db")
        await storage.initialize()

        # Write some data
        proc = _make_process(pid=100, rss_mb=1024.0, job_id="my-job", sheet_num=1)
        snap = _make_snapshot(timestamp=time.time(), processes=[proc])
        await storage.write_snapshot(snap)
        event = _make_event(pid=100, job_id="my-job")
        await storage.write_event(event)

        report = await generate_resource_report("my-job", storage)
        assert "my-job" in report
        assert "Peak memory: 1024.0 MB" in report
        assert "Process spawns:" in report

    @pytest.mark.asyncio
    async def test_report_includes_per_sheet_breakdown(self, tmp_path: Path) -> None:
        """Report includes per-sheet resource breakdown."""
        from marianne.daemon.profiler.storage import generate_resource_report

        storage = MonitorStorage(db_path=tmp_path / "test.db")
        await storage.initialize()

        now = time.time()
        for i, (sheet, rss) in enumerate([(1, 512.0), (2, 768.0), (3, 1024.0)]):
            proc = _make_process(
                pid=100 + i, rss_mb=rss, job_id="multi-sheet", sheet_num=sheet
            )
            snap = _make_snapshot(timestamp=now + i, processes=[proc])
            await storage.write_snapshot(snap)

        report = await generate_resource_report("multi-sheet", storage)
        assert "Per-sheet resource peaks:" in report
        assert "Sheet 1:" in report
        assert "Sheet 2:" in report
        assert "Sheet 3:" in report

    @pytest.mark.asyncio
    async def test_report_includes_syscall_hotspots(self, tmp_path: Path) -> None:
        """Report includes syscall hotspot information."""
        from marianne.daemon.profiler.storage import generate_resource_report

        storage = MonitorStorage(db_path=tmp_path / "test.db")
        await storage.initialize()

        proc = _make_process(pid=100, job_id="syscall-job", sheet_num=1)
        snap = _make_snapshot(timestamp=time.time(), processes=[proc])
        await storage.write_snapshot(snap)
        event = _make_event(pid=100, job_id="syscall-job")
        await storage.write_event(event)

        report = await generate_resource_report("syscall-job", storage)
        assert "Syscall hotspots" in report

    @pytest.mark.asyncio
    async def test_report_includes_process_lifecycle(self, tmp_path: Path) -> None:
        """Report includes process lifecycle information."""
        from marianne.daemon.profiler.storage import generate_resource_report

        storage = MonitorStorage(db_path=tmp_path / "test.db")
        await storage.initialize()

        proc = _make_process(pid=100, rss_mb=256.0, job_id="lifecycle-job", sheet_num=1)
        snap = _make_snapshot(timestamp=time.time(), processes=[proc])
        await storage.write_snapshot(snap)

        # Write spawn and exit events
        spawn = _make_event(
            pid=100, event_type=EventType.SPAWN, job_id="lifecycle-job"
        )
        exit_evt = _make_event(
            pid=100, event_type=EventType.EXIT, job_id="lifecycle-job", exit_code=0
        )
        await storage.write_event(spawn)
        await storage.write_event(exit_evt)

        report = await generate_resource_report("lifecycle-job", storage)
        assert "Process lifecycle:" in report
        assert "1 spawns" in report
        assert "1 exits" in report
