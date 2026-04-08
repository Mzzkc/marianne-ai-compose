"""Pydantic v2 models for the Marianne system profiler.

Defines configuration, per-process metrics, GPU metrics, system snapshots,
process lifecycle events, anomalies, and resource estimates used by the
profiler collector, storage layer, anomaly detector, and correlation analyzer.
"""

from __future__ import annotations

import time
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AnomalySeverity(str, Enum):
    """Severity level for detected anomalies."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyType(str, Enum):
    """Types of resource anomalies the detector can identify."""

    MEMORY_SPIKE = "memory_spike"
    """RSS increased >threshold in recent window."""

    RUNAWAY_PROCESS = "runaway_process"
    """Child process consuming >threshold CPU for extended duration."""

    ZOMBIE = "zombie"
    """One or more zombie child processes found."""

    FD_EXHAUSTION = "fd_exhaustion"
    """Process approaching file descriptor limits."""


class EventType(str, Enum):
    """Process lifecycle event types."""

    SPAWN = "spawn"
    EXIT = "exit"
    SIGNAL = "signal"
    KILL = "kill"
    OOM = "oom"


# ---------------------------------------------------------------------------
# Per-process metric model
# ---------------------------------------------------------------------------


class ProcessMetric(BaseModel):
    """Resource metrics for a single process in a snapshot."""

    pid: int = Field(description="Process ID")
    ppid: int = Field(default=0, description="Parent PID")
    command: str = Field(default="", description="Process command line")
    state: str = Field(default="S", description="Process state (R/S/D/Z/T)")
    cpu_percent: float = Field(default=0.0, ge=0.0, description="CPU usage %")
    rss_mb: float = Field(default=0.0, ge=0.0, description="RSS memory in MB")
    vms_mb: float = Field(default=0.0, ge=0.0, description="Virtual memory in MB")
    threads: int = Field(default=1, ge=0, description="Thread count")
    open_fds: int = Field(default=0, ge=0, description="Open file descriptors")
    age_seconds: float = Field(default=0.0, ge=0.0, description="Process age in seconds")
    job_id: str | None = Field(default=None, description="Associated job ID")
    sheet_num: int | None = Field(default=None, description="Associated sheet number")
    syscall_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Syscall name → invocation count from strace -c",
    )
    syscall_time_pct: dict[str, float] = Field(
        default_factory=dict,
        description="Syscall name → time percentage from strace -c",
    )


# ---------------------------------------------------------------------------
# GPU metric model
# ---------------------------------------------------------------------------


class GpuMetric(BaseModel):
    """Per-GPU resource snapshot at a point in time."""

    index: int = Field(ge=0, description="GPU device index")
    utilization_pct: float = Field(
        default=0.0, ge=0.0, le=100.0, description="GPU utilization %"
    )
    memory_used_mb: float = Field(
        default=0.0, ge=0.0, description="GPU memory used in MB"
    )
    memory_total_mb: float = Field(
        default=0.0, ge=0.0, description="GPU total memory in MB"
    )
    temperature_c: float = Field(
        default=0.0, ge=0.0, description="GPU temperature in Celsius"
    )


# ---------------------------------------------------------------------------
# Per-job progress model
# ---------------------------------------------------------------------------


class JobProgress(BaseModel):
    """Progress snapshot for a single running job.

    Populated from CheckpointState in ``_live_states`` during
    ``collect_snapshot()``.  Consumed by the TUI jobs panel for
    accurate progress bars.
    """

    job_id: str = Field(description="Job identifier")
    total_sheets: int = Field(default=1, ge=1, description="Total sheets in the job")
    last_completed_sheet: int = Field(
        default=0, ge=0, description="Last fully completed sheet number"
    )
    current_sheet: int | None = Field(
        default=None, description="Currently executing sheet number"
    )
    status: str = Field(default="unknown", description="Job status string")


# ---------------------------------------------------------------------------
# System snapshot model
# ---------------------------------------------------------------------------


class SystemSnapshot(BaseModel):
    """Point-in-time system resource snapshot.

    Collected periodically by ProfilerCollector, stored in SQLite + JSONL,
    and consumed by AnomalyDetector and CorrelationAnalyzer.
    """

    timestamp: float = Field(
        default_factory=time.time, description="Unix timestamp of this snapshot"
    )
    daemon_pid: int = Field(default=0, description="PID of the daemon process")

    # System memory
    system_memory_total_mb: float = Field(default=0.0, ge=0.0)
    system_memory_available_mb: float = Field(default=0.0, ge=0.0)
    system_memory_used_mb: float = Field(default=0.0, ge=0.0)
    daemon_rss_mb: float = Field(default=0.0, ge=0.0)

    # Load average
    load_avg_1: float = Field(default=0.0, ge=0.0)
    load_avg_5: float = Field(default=0.0, ge=0.0)
    load_avg_15: float = Field(default=0.0, ge=0.0)

    # Per-process and GPU data
    processes: list[ProcessMetric] = Field(default_factory=list)
    gpus: list[GpuMetric] = Field(default_factory=list)

    # Backpressure
    pressure_level: str = Field(default="none")

    # Job state
    running_jobs: int = Field(default=0, ge=0)
    active_sheets: int = Field(default=0, ge=0)

    # Zombies
    zombie_count: int = Field(default=0, ge=0)
    zombie_pids: list[int] = Field(default_factory=list)

    # Per-job progress (populated from CheckpointState live states)
    job_progress: list[JobProgress] = Field(default_factory=list)

    # Conductor uptime (seconds since daemon started)
    conductor_uptime_seconds: float = Field(default=0.0, ge=0.0)


# ---------------------------------------------------------------------------
# Process lifecycle event model
# ---------------------------------------------------------------------------


class ProcessEvent(BaseModel):
    """Lifecycle event for a child process (spawn, exit, signal, kill, oom)."""

    timestamp: float = Field(
        default_factory=time.time, description="Unix timestamp"
    )
    pid: int = Field(description="Process ID")
    event_type: EventType = Field(description="Event type")
    exit_code: int | None = Field(default=None)
    signal_num: int | None = Field(default=None)
    job_id: str | None = Field(default=None)
    sheet_num: int | None = Field(default=None)
    details: str = Field(default="", description="Additional context (JSON or text)")


# ---------------------------------------------------------------------------
# Anomaly model
# ---------------------------------------------------------------------------


class Anomaly(BaseModel):
    """A detected resource anomaly.

    Produced by AnomalyDetector when heuristic thresholds are exceeded.
    Published to EventBus as ``monitor.anomaly`` events and stored as
    RESOURCE_ANOMALY patterns in the learning system.
    """

    timestamp: float = Field(
        default_factory=time.time, description="Unix timestamp of detection"
    )
    anomaly_type: AnomalyType = Field(description="Type of anomaly")
    pid: int | None = Field(
        default=None, description="PID of the affected process, if applicable"
    )
    job_id: str | None = Field(
        default=None, description="Job ID, if applicable"
    )
    sheet_num: int | None = Field(
        default=None, description="Sheet number, if applicable"
    )
    description: str = Field(default="", description="Human-readable description")
    severity: AnomalySeverity = Field(
        default=AnomalySeverity.MEDIUM, description="Severity level"
    )
    metric_value: float = Field(
        default=0.0, description="Actual metric value that triggered the anomaly"
    )
    threshold: float = Field(
        default=0.0, description="Threshold that was exceeded"
    )


# ---------------------------------------------------------------------------
# Resource estimate model
# ---------------------------------------------------------------------------


class ResourceEstimate(BaseModel):
    """Scheduling hint based on learned resource correlations.

    Returned by BackpressureController.estimate_job_resource_needs()
    to inform job admission and scheduling decisions.
    """

    estimated_peak_memory_mb: float = Field(
        default=0.0, ge=0.0, description="Predicted peak RSS for the job in MB"
    )
    estimated_cpu_seconds: float = Field(
        default=0.0, ge=0.0, description="Predicted total CPU-time in seconds"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Confidence score (0.0 = no data, 1.0 = high confidence)",
    )


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class RetentionConfig(BaseModel):
    """Data retention policy for profiler storage."""

    model_config = ConfigDict(extra="forbid")

    full_resolution_hours: int = Field(
        default=24, ge=1, description="Hours to keep full-resolution snapshots"
    )
    downsampled_days: int = Field(
        default=7, ge=1, description="Days to keep downsampled (1-min avg) data"
    )
    events_days: int = Field(
        default=30, ge=1, description="Days to keep process lifecycle events"
    )


class AnomalyConfig(BaseModel):
    """Thresholds for anomaly detection."""

    model_config = ConfigDict(extra="forbid")

    memory_spike_threshold: float = Field(
        default=1.5,
        gt=1.0,
        description="RSS increase ratio that triggers a memory spike anomaly "
        "(e.g. 1.5 = 50% increase)",
    )
    memory_spike_window_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Time window (seconds) over which to measure memory spikes",
    )
    runaway_cpu_threshold: float = Field(
        default=90.0,
        ge=50.0,
        le=100.0,
        description="CPU percentage above which a process is considered runaway",
    )
    runaway_duration_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description="Seconds a process must exceed CPU threshold to be flagged",
    )


class CorrelationConfig(BaseModel):
    """Configuration for the periodic correlation analyzer."""

    model_config = ConfigDict(extra="forbid")

    interval_minutes: int = Field(
        default=30,
        ge=5,
        description="How often (minutes) to run correlation analysis",
    )
    min_sample_size: int = Field(
        default=5,
        ge=2,
        description="Minimum completed jobs before generating correlations",
    )


class ProfilerConfig(BaseModel):
    """Top-level profiler configuration for ``DaemonConfig.profiler``.

    Controls data collection, storage, anomaly detection thresholds,
    and correlation analysis frequency.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True, description="Master switch for the profiler subsystem"
    )
    interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        description="Seconds between snapshot collections",
    )
    strace_enabled: bool = Field(
        default=False,
        description="Attach strace to child processes for syscall profiling",
    )
    strace_full_on_demand: bool = Field(
        default=True,
        description="Allow full strace (-f) via mzt top --trace PID",
    )
    gpu_enabled: bool = Field(
        default=False,
        description="Attempt GPU probing (graceful skip if unavailable)",
    )
    storage_path: Path = Field(
        default=Path("~/.marianne/monitor.db"),
        description="SQLite database path for time-series storage",
    )
    jsonl_path: Path = Field(
        default=Path("~/.marianne/monitor.jsonl"),
        description="JSONL streaming log path",
    )
    jsonl_max_bytes: int = Field(
        default=52_428_800,
        ge=1_048_576,
        description="JSONL file rotation size in bytes (default 50MB)",
    )
    retention: RetentionConfig = Field(
        default_factory=RetentionConfig,
        description="Data retention policy",
    )
    anomaly: AnomalyConfig = Field(
        default_factory=AnomalyConfig,
        description="Anomaly detection thresholds",
    )
    correlation: CorrelationConfig = Field(
        default_factory=CorrelationConfig,
        description="Correlation analyzer settings",
    )


__all__ = [
    "Anomaly",
    "AnomalyConfig",
    "AnomalySeverity",
    "AnomalyType",
    "CorrelationConfig",
    "EventType",
    "GpuMetric",
    "JobProgress",
    "ProcessEvent",
    "ProcessMetric",
    "ProfilerConfig",
    "ResourceEstimate",
    "RetentionConfig",
    "SystemSnapshot",
]
