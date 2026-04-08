"""Profiler package — system resource collection, storage, and anomaly detection.

Collects per-process metrics, GPU stats, strace summaries, and stores
time-series data in SQLite for consumption by ``mzt top`` and the
anomaly/correlation analyzers.
"""

from marianne.daemon.profiler.anomaly import AnomalyDetector
from marianne.daemon.profiler.collector import ProfilerCollector
from marianne.daemon.profiler.correlation import CorrelationAnalyzer
from marianne.daemon.profiler.gpu_probe import GpuMetric, GpuProbe
from marianne.daemon.profiler.models import (
    Anomaly,
    AnomalyConfig,
    AnomalySeverity,
    AnomalyType,
    CorrelationConfig,
    EventType,
    ProcessEvent,
    ProcessMetric,
    ProfilerConfig,
    ResourceEstimate,
    RetentionConfig,
    SystemSnapshot,
)
from marianne.daemon.profiler.storage import MonitorStorage, generate_resource_report
from marianne.daemon.profiler.strace_manager import StraceManager

__all__ = [
    "Anomaly",
    "AnomalyConfig",
    "AnomalyDetector",
    "CorrelationAnalyzer",
    "AnomalySeverity",
    "AnomalyType",
    "CorrelationConfig",
    "EventType",
    "GpuMetric",
    "GpuProbe",
    "MonitorStorage",
    "generate_resource_report",
    "ProcessEvent",
    "ProcessMetric",
    "ProfilerCollector",
    "ProfilerConfig",
    "ResourceEstimate",
    "RetentionConfig",
    "StraceManager",
    "SystemSnapshot",
]
