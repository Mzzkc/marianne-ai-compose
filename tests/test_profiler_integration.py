"""Integration tests for the profiler subsystem.

Tests cover end-to-end flows:
- ProfilerCollector lifecycle (start → collect → stop → verify storage)
- Anomaly detection → learning store flow
- EventBus monitor.anomaly event publishing
- SystemSnapshot NDJSON serialization round-trip
- `mzt diagnose --resources` output format
- DaemonConfig loading with profiler section
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from marianne.daemon.event_bus import EventBus
from marianne.daemon.profiler.collector import ProfilerCollector
from marianne.daemon.profiler.models import (
    EventType,
    GpuMetric,
    ProcessEvent,
    ProcessMetric,
    ProfilerConfig,
    SystemSnapshot,
)
from marianne.daemon.profiler.storage import MonitorStorage, generate_resource_report
from marianne.daemon.types import ObserverEvent


# ---------------------------------------------------------------------------
# Helpers (reused from test_profiler.py for consistency)
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


def _make_collector(
    tmp_path: Path,
    event_bus: EventBus | None = None,
) -> tuple[ProfilerCollector, EventBus]:
    """Build a ProfilerCollector with a real EventBus."""
    config = ProfilerConfig(
        enabled=True,
        interval_seconds=1.0,
        strace_enabled=False,
        gpu_enabled=False,
        storage_path=tmp_path / "monitor.db",
        jsonl_path=tmp_path / "monitor.jsonl",
    )

    if event_bus is None:
        event_bus = EventBus(max_queue_size=100)

    monitor = MagicMock()
    pgroup = MagicMock()

    collector = ProfilerCollector(
        config=config,
        monitor=monitor,
        pgroup=pgroup,
        event_bus=event_bus,
        manager=None,
    )
    return collector, event_bus


# ===========================================================================
# Test 1: ProfilerCollector lifecycle
# ===========================================================================


class TestProfilerCollectorLifecycle:
    """Start, collect N snapshots, stop, verify storage."""

    @pytest.mark.asyncio
    async def test_profiler_collector_lifecycle(self, tmp_path: Path) -> None:
        """Full lifecycle: start → collect N snapshots → stop → verify persistence."""
        collector, event_bus = _make_collector(tmp_path)

        await event_bus.start()

        # Initialize storage (normally done by start(), but we skip the loop)
        await collector._storage.initialize()

        # Manually collect and persist snapshots (simulates what the loop does)
        for i in range(5):
            with (
                patch("marianne.daemon.profiler.collector._psutil", None),
                patch(
                    "marianne.daemon.profiler.collector.SystemProbe.get_memory_mb",
                    return_value=100.0 + i * 50,
                ),
                patch(
                    "marianne.daemon.profiler.collector.os.getloadavg",
                    return_value=(1.0, 0.8, 0.5),
                ),
            ):
                snapshot = await collector.collect_snapshot()

            # Persist (same as collection loop)
            await collector._storage.write_snapshot(snapshot)
            collector._storage.append_jsonl(snapshot)

            # Run anomaly detection (same as collection loop)
            anomalies = collector._anomaly_detector.detect(snapshot, collector._history)
            for anomaly in anomalies:
                await collector._publish_anomaly(anomaly)

            # Update history
            collector._history.append(snapshot)

        await event_bus.shutdown()

        # Verify snapshots were persisted to SQLite
        storage = MonitorStorage(db_path=tmp_path / "monitor.db")
        await storage.initialize()
        snapshots = await storage.read_snapshots(since=0.0, limit=100)

        assert len(snapshots) == 5, f"Expected 5 snapshots, got {len(snapshots)}"

        # Verify JSONL file was written
        jsonl_path = tmp_path / "monitor.jsonl"
        assert jsonl_path.exists(), "JSONL file should exist"
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) >= 2, f"Expected at least 2 JSONL lines, got {len(lines)}"

        # Verify each JSONL line is valid JSON with expected fields
        for line in lines:
            parsed = json.loads(line)
            assert "timestamp" in parsed
            assert "daemon_pid" in parsed
            assert "processes" in parsed


# ===========================================================================
# Test 2: Anomaly → Learning flow
# ===========================================================================


class TestAnomalyToLearningFlow:
    """Inject anomaly, verify RESOURCE_ANOMALY pattern stored."""

    @pytest.mark.asyncio
    async def test_anomaly_to_learning_flow(self) -> None:
        """When AnomalyDetector finds an anomaly, it flows through EventBus
        to SemanticAnalyzer which stores a RESOURCE_ANOMALY pattern."""
        from marianne.daemon.semantic_analyzer import SemanticAnalyzer
        from marianne.learning.patterns import PatternType

        event_bus = EventBus(max_queue_size=100)
        await event_bus.start()

        # Set up a mock learning hub and store
        learning_hub = MagicMock()
        learning_hub.is_running = True
        mock_store = MagicMock()
        mock_store.record_pattern = MagicMock()
        learning_hub.store = mock_store

        # Set up SemanticAnalyzer to subscribe to anomaly events
        sa_config = MagicMock()
        sa_config.enabled = True
        sa_config.max_concurrent_analyses = 3
        sa_config.analyze_on = ["success", "failure"]
        sa_config.backend = MagicMock()
        sa_config.backend.timeout_seconds = 30

        from unittest.mock import AsyncMock

        mock_backend = MagicMock(name="test-backend")
        mock_backend.close = AsyncMock()

        analyzer = SemanticAnalyzer(
            config=sa_config,
            backend=mock_backend,
            learning_hub=learning_hub,
            live_states={},
        )
        await analyzer.start(event_bus)

        # Now publish a monitor.anomaly event (simulating what ProfilerCollector does)
        anomaly_event: ObserverEvent = {
            "event": "monitor.anomaly",
            "job_id": "integration-test-job",
            "sheet_num": 2,
            "data": {
                "anomaly_type": "memory_spike",
                "severity": "high",
                "description": "PID 5555 RSS grew 80% (100→180 MB) in 30s",
                "pid": 5555,
                "metric_value": 180.0,
                "threshold": 150.0,
            },
            "timestamp": time.time(),
        }
        await event_bus.publish(anomaly_event)

        # Give the event bus time to distribute
        await asyncio.sleep(0.3)

        await analyzer.stop(event_bus)
        await event_bus.shutdown()

        # Verify the pattern was stored
        assert mock_store.record_pattern.call_count >= 1
        call_kwargs = mock_store.record_pattern.call_args[1]
        assert call_kwargs["pattern_type"] == PatternType.RESOURCE_ANOMALY.value
        assert "memory_spike" in call_kwargs["pattern_name"]


# ===========================================================================
# Test 3: EventBus monitor.anomaly events
# ===========================================================================


class TestEventBusMonitorEvents:
    """Verify monitor.anomaly events are published on the EventBus."""

    @pytest.mark.asyncio
    async def test_event_bus_monitor_events(self, tmp_path: Path) -> None:
        """Anomalies detected by the collector are published to EventBus."""
        event_bus = EventBus(max_queue_size=100)
        await event_bus.start()

        received_events: list[ObserverEvent] = []

        def on_anomaly(event: ObserverEvent) -> None:
            received_events.append(event)

        event_bus.subscribe(
            callback=on_anomaly,
            event_filter=lambda e: e.get("event") == "monitor.anomaly",
        )

        collector, _ = _make_collector(tmp_path, event_bus=event_bus)

        # Set up history with a baseline snapshot
        now = time.time()
        baseline = _make_snapshot(
            timestamp=now - 10,
            processes=[_make_process(pid=42, rss_mb=100.0)],
        )
        collector._history = [baseline]

        # Create a snapshot that triggers a memory spike anomaly
        spiked_snapshot = _make_snapshot(
            timestamp=now,
            processes=[_make_process(pid=42, rss_mb=200.0)],
        )

        # Detect and publish anomalies
        anomalies = collector._anomaly_detector.detect(spiked_snapshot, collector._history)
        assert len(anomalies) > 0, "Should detect at least one anomaly"

        for anomaly in anomalies:
            await collector._publish_anomaly(anomaly)

        # Give EventBus time to distribute
        await asyncio.sleep(0.3)

        await event_bus.shutdown()

        # Verify we received monitor.anomaly events
        anomaly_events = [e for e in received_events if e.get("event") == "monitor.anomaly"]
        assert len(anomaly_events) >= 1
        data = anomaly_events[0]["data"]
        assert data is not None
        assert data["anomaly_type"] == "memory_spike"
        assert data["severity"] in ("high", "critical")


# ===========================================================================
# Test 4: Snapshot JSON serialization
# ===========================================================================


class TestSnapshotJsonSerialization:
    """Verify NDJSON output is valid JSON that round-trips correctly."""

    def test_snapshot_json_serialization(self) -> None:
        """SystemSnapshot serializes to valid JSON and deserializes back."""
        proc = _make_process(pid=42, rss_mb=512.0, job_id="ser-test", sheet_num=3)
        gpu = GpuMetric(
            index=0,
            utilization_pct=75.0,
            memory_used_mb=4096.0,
            memory_total_mb=8192.0,
            temperature_c=65.0,
        )
        snapshot = _make_snapshot(
            timestamp=1700000000.0,
            processes=[proc],
            gpus=[gpu],
            running_jobs=2,
            active_sheets=3,
            pressure_level="medium",
            zombie_count=1,
            zombie_pids=[999],
        )

        # Serialize to JSON (NDJSON format)
        json_str = json.dumps(snapshot.model_dump(mode="json"), separators=(",", ":"))

        # Validate it's proper JSON
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

        # Verify key fields survive round-trip
        assert parsed["timestamp"] == 1700000000.0
        assert parsed["daemon_pid"] == 1000
        assert parsed["system_memory_total_mb"] == 16000.0
        assert parsed["running_jobs"] == 2
        assert parsed["active_sheets"] == 3
        assert parsed["pressure_level"] == "medium"
        assert parsed["zombie_count"] == 1
        assert parsed["zombie_pids"] == [999]

        # Verify nested structures
        assert len(parsed["processes"]) == 1
        assert parsed["processes"][0]["pid"] == 42
        assert parsed["processes"][0]["rss_mb"] == 512.0
        assert parsed["processes"][0]["job_id"] == "ser-test"
        assert parsed["processes"][0]["syscall_counts"] == {"write": 1500, "read": 3200}

        assert len(parsed["gpus"]) == 1
        assert parsed["gpus"][0]["utilization_pct"] == 75.0

        # Verify it can be reconstructed into a SystemSnapshot
        reconstructed = SystemSnapshot(**parsed)
        assert reconstructed.timestamp == snapshot.timestamp
        assert reconstructed.running_jobs == snapshot.running_jobs
        assert len(reconstructed.processes) == 1
        assert reconstructed.processes[0].pid == 42

    def test_snapshot_ndjson_multiline(self, tmp_path: Path) -> None:
        """Multiple snapshots written as NDJSON produce valid per-line JSON."""
        jsonl_path = tmp_path / "test.jsonl"
        storage = MonitorStorage(
            db_path=tmp_path / "test.db",
            jsonl_path=jsonl_path,
        )

        # Write several snapshots
        for i in range(5):
            snap = _make_snapshot(
                timestamp=1700000000.0 + i * 5,
                processes=[_make_process(pid=100 + i)],
            )
            storage.append_jsonl(snap)

        # Verify each line is valid JSON
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 5

        for i, line in enumerate(lines):
            parsed = json.loads(line)
            assert parsed["timestamp"] == 1700000000.0 + i * 5
            assert len(parsed["processes"]) == 1
            assert parsed["processes"][0]["pid"] == 100 + i


# ===========================================================================
# Test 5: Diagnose --resources output
# ===========================================================================


class TestDiagnoseResourcesOutput:
    """Verify resource report format from generate_resource_report."""

    @pytest.mark.asyncio
    async def test_diagnose_resources_output(self, tmp_path: Path) -> None:
        """Full resource report includes all expected sections."""
        storage = MonitorStorage(db_path=tmp_path / "test.db")
        await storage.initialize()

        now = time.time()

        # Write data simulating a multi-sheet job
        for i, (sheet, rss, cpu) in enumerate([
            (1, 256.0, 30.0),
            (2, 512.0, 65.0),
            (3, 1024.0, 90.0),
        ]):
            proc = _make_process(
                pid=200 + i,
                rss_mb=rss,
                cpu_percent=cpu,
                job_id="diagnose-test",
                sheet_num=sheet,
            )
            snap = _make_snapshot(timestamp=now + i * 5, processes=[proc])
            await storage.write_snapshot(snap)

        # Write lifecycle events
        for i in range(3):
            spawn = ProcessEvent(
                timestamp=now + i * 5,
                pid=200 + i,
                event_type=EventType.SPAWN,
                job_id="diagnose-test",
                sheet_num=i + 1,
                details=f"Sheet {i + 1} started",
            )
            await storage.write_event(spawn)

            exit_evt = ProcessEvent(
                timestamp=now + i * 5 + 60,
                pid=200 + i,
                event_type=EventType.EXIT,
                exit_code=0,
                job_id="diagnose-test",
                sheet_num=i + 1,
                details=f"Sheet {i + 1} completed",
            )
            await storage.write_event(exit_evt)

        report = await generate_resource_report("diagnose-test", storage)

        # Verify report contains all expected sections
        assert "diagnose-test" in report
        assert "Peak memory: 1024.0 MB" in report
        assert "Process spawns: 3" in report
        assert "Unique PIDs observed: 3" in report
        assert "Per-sheet resource peaks:" in report
        assert "Sheet 1:" in report
        assert "Sheet 2:" in report
        assert "Sheet 3:" in report
        assert "Syscall hotspots" in report
        assert "Process lifecycle:" in report
        assert "3 spawns" in report
        assert "3 exits" in report

    @pytest.mark.asyncio
    async def test_diagnose_no_data(self, tmp_path: Path) -> None:
        """Resource report for non-existent job returns short message."""
        storage = MonitorStorage(db_path=tmp_path / "empty.db")
        await storage.initialize()

        report = await generate_resource_report("no-such-job", storage)
        assert "No profiler data available" in report
        assert "no-such-job" in report


# ===========================================================================
# Test 6: DaemonConfig loads with profiler section
# ===========================================================================


class TestProfilerConfigInDaemonConfig:
    """Verify DaemonConfig correctly loads profiler configuration."""

    def test_profiler_config_in_daemon_config(self) -> None:
        """DaemonConfig includes ProfilerConfig with correct defaults."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig()
        assert hasattr(config, "profiler")
        assert isinstance(config.profiler, ProfilerConfig)

        # Verify defaults match the design doc
        assert config.profiler.enabled is True
        assert config.profiler.interval_seconds == 5.0
        assert config.profiler.strace_enabled is False
        assert config.profiler.gpu_enabled is False
        assert config.profiler.jsonl_max_bytes == 52_428_800
        assert config.profiler.storage_path == Path("~/.marianne/monitor.db")
        assert config.profiler.jsonl_path == Path("~/.marianne/monitor.jsonl")

    def test_profiler_config_custom_values(self) -> None:
        """DaemonConfig accepts custom profiler settings."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig(
            profiler=ProfilerConfig(
                enabled=False,
                interval_seconds=10.0,
                strace_enabled=False,
                gpu_enabled=False,
            )
        )
        assert config.profiler.enabled is False
        assert config.profiler.interval_seconds == 10.0
        assert config.profiler.strace_enabled is False

    def test_profiler_config_nested_sections(self) -> None:
        """Profiler retention, anomaly, and correlation configs are nested."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig()

        # Retention config
        assert config.profiler.retention.full_resolution_hours == 24
        assert config.profiler.retention.downsampled_days == 7
        assert config.profiler.retention.events_days == 30

        # Anomaly config
        assert config.profiler.anomaly.memory_spike_threshold == 1.5
        assert config.profiler.anomaly.memory_spike_window_seconds == 30.0
        assert config.profiler.anomaly.runaway_cpu_threshold == 90.0
        assert config.profiler.anomaly.runaway_duration_seconds == 60.0

        # Correlation config
        assert config.profiler.correlation.interval_minutes == 30
        assert config.profiler.correlation.min_sample_size == 5
