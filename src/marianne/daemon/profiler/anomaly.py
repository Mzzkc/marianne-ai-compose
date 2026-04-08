"""Heuristic anomaly detection for the Marianne system profiler.

Compares the current ``SystemSnapshot`` against recent history to detect
resource anomalies: memory spikes, runaway processes, zombies, and FD
exhaustion.  No LLM calls — pure threshold-based detection.

Detected anomalies are published to the ``EventBus`` as ``monitor.anomaly``
events and stored as ``RESOURCE_ANOMALY`` patterns in the learning system.
"""

from __future__ import annotations

from marianne.daemon.profiler.models import (
    Anomaly,
    AnomalyConfig,
    AnomalySeverity,
    AnomalyType,
    SystemSnapshot,
)

# Default thresholds for checks not covered by AnomalyConfig
FD_EXHAUSTION_THRESHOLD: int = 1000
"""Open FD count that triggers an FD exhaustion anomaly."""

MEMORY_PRESSURE_FRACTION: float = 0.10
"""Available/total memory ratio below which memory pressure is flagged."""


class AnomalyDetector:
    """Detects resource anomalies by comparing snapshots against thresholds.

    Runs on each new snapshot collected by ``ProfilerCollector``.  Stateless
    except for the configuration — all history is passed in via the
    ``detect`` method.
    """

    def __init__(self, config: AnomalyConfig | None = None) -> None:
        self.config = config or AnomalyConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        current: SystemSnapshot,
        history: list[SystemSnapshot],
    ) -> list[Anomaly]:
        """Run all anomaly checks against *current* snapshot and *history*.

        Args:
            current: The most recent system snapshot.
            history: Recent snapshots (oldest-first) for trend analysis.
                     Should cover at least the configured spike window.

        Returns:
            List of detected ``Anomaly`` objects (may be empty).
        """
        anomalies: list[Anomaly] = []
        anomalies.extend(self._check_memory_spikes(current, history))
        anomalies.extend(self._check_runaway_processes(current, history))
        anomalies.extend(self._check_zombies(current))
        anomalies.extend(self._check_fd_exhaustion(current))
        anomalies.extend(self._check_memory_pressure(current))
        return anomalies

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_memory_spikes(
        self,
        current: SystemSnapshot,
        history: list[SystemSnapshot],
    ) -> list[Anomaly]:
        """Detect per-process RSS increases exceeding the threshold.

        Compares each process in *current* against the earliest snapshot
        within the configured time window.  A spike is flagged when
        ``current_rss / baseline_rss >= memory_spike_threshold``.
        """
        anomalies: list[Anomaly] = []
        if not history:
            return anomalies

        window_start = current.timestamp - self.config.memory_spike_window_seconds

        # Find the earliest snapshot still inside the window
        baseline: SystemSnapshot | None = None
        for snap in history:
            if snap.timestamp >= window_start:
                baseline = snap
                break
        if baseline is None:
            # All history is older than the window — use the most recent
            baseline = history[-1]

        # Build PID → RSS lookup from baseline
        baseline_rss: dict[int, float] = {
            p.pid: p.rss_mb for p in baseline.processes if p.rss_mb > 0
        }

        for proc in current.processes:
            if proc.rss_mb <= 0:
                continue
            prev_rss = baseline_rss.get(proc.pid)
            if prev_rss is None or prev_rss <= 0:
                continue
            ratio = proc.rss_mb / prev_rss
            if ratio >= self.config.memory_spike_threshold:
                increase_pct = (ratio - 1.0) * 100.0
                severity = (
                    AnomalySeverity.CRITICAL
                    if ratio >= self.config.memory_spike_threshold * 2
                    else AnomalySeverity.HIGH
                )
                anomalies.append(
                    Anomaly(
                        anomaly_type=AnomalyType.MEMORY_SPIKE,
                        severity=severity,
                        description=(
                            f"PID {proc.pid} RSS grew {increase_pct:.0f}% "
                            f"({prev_rss:.0f}\u2192{proc.rss_mb:.0f} MB) "
                            f"in {self.config.memory_spike_window_seconds:.0f}s"
                        ),
                        pid=proc.pid,
                        job_id=proc.job_id,
                        timestamp=current.timestamp,
                        metric_value=proc.rss_mb,
                        threshold=prev_rss * self.config.memory_spike_threshold,
                    )
                )

        return anomalies

    def _check_runaway_processes(
        self,
        current: SystemSnapshot,
        history: list[SystemSnapshot],
    ) -> list[Anomaly]:
        """Detect processes exceeding CPU threshold for extended duration.

        A process is "runaway" if it has been above
        ``runaway_cpu_threshold`` for at least ``runaway_duration_seconds``
        of consecutive history snapshots.
        """
        anomalies: list[Anomaly] = []
        threshold = self.config.runaway_cpu_threshold
        required_duration = self.config.runaway_duration_seconds

        for proc in current.processes:
            if proc.cpu_percent < threshold:
                continue

            # Walk backward through history to see how long this PID
            # has been above threshold
            hot_since: float = current.timestamp
            for snap in reversed(history):
                match = next(
                    (p for p in snap.processes if p.pid == proc.pid),
                    None,
                )
                if match is None or match.cpu_percent < threshold:
                    break
                hot_since = snap.timestamp

            duration = current.timestamp - hot_since
            if duration >= required_duration:
                anomalies.append(
                    Anomaly(
                        anomaly_type=AnomalyType.RUNAWAY_PROCESS,
                        severity=AnomalySeverity.CRITICAL,
                        description=(
                            f"PID {proc.pid} at {proc.cpu_percent:.0f}% CPU "
                            f"for {duration:.0f}s (threshold: "
                            f"{threshold:.0f}% for {required_duration:.0f}s)"
                        ),
                        pid=proc.pid,
                        job_id=proc.job_id,
                        timestamp=current.timestamp,
                        metric_value=proc.cpu_percent,
                        threshold=threshold,
                    )
                )

        return anomalies

    def _check_zombies(self, current: SystemSnapshot) -> list[Anomaly]:
        """Flag any zombie processes in the current snapshot."""
        anomalies: list[Anomaly] = []
        if current.zombie_count > 0:
            # Use zombie_pids from snapshot if available, otherwise scan processes
            zombie_pids = current.zombie_pids or [
                p.pid for p in current.processes if p.state == "Z"
            ]
            first_pid = zombie_pids[0] if zombie_pids else None
            anomalies.append(
                Anomaly(
                    anomaly_type=AnomalyType.ZOMBIE,
                    severity=AnomalySeverity.HIGH,
                    description=(
                        f"{current.zombie_count} zombie process(es) detected"
                        + (f": PIDs {zombie_pids}" if zombie_pids else "")
                    ),
                    pid=first_pid,
                    timestamp=current.timestamp,
                    metric_value=float(current.zombie_count),
                    threshold=0.0,
                )
            )
        return anomalies

    def _check_fd_exhaustion(self, current: SystemSnapshot) -> list[Anomaly]:
        """Flag processes with open FDs exceeding the threshold."""
        anomalies: list[Anomaly] = []
        threshold = FD_EXHAUSTION_THRESHOLD

        for proc in current.processes:
            if proc.open_fds >= threshold:
                anomalies.append(
                    Anomaly(
                        anomaly_type=AnomalyType.FD_EXHAUSTION,
                        severity=AnomalySeverity.HIGH,
                        description=(
                            f"PID {proc.pid} has {proc.open_fds} open FDs "
                            f"(threshold: {threshold})"
                        ),
                        pid=proc.pid,
                        job_id=proc.job_id,
                        timestamp=current.timestamp,
                        metric_value=float(proc.open_fds),
                        threshold=float(threshold),
                    )
                )

        return anomalies

    def _check_memory_pressure(
        self, current: SystemSnapshot,
    ) -> list[Anomaly]:
        """Flag when system available memory drops below threshold.

        Uses the module-level MEMORY_PRESSURE_FRACTION constant as the
        threshold (fraction of total memory that must remain available).

        NOTE: This produces an anomaly with ``AnomalyType.MEMORY_SPIKE``
        since there is no dedicated memory-pressure type — the spike type
        is the closest semantic match for system-wide memory issues.
        """
        anomalies: list[Anomaly] = []
        total = current.system_memory_total_mb
        available = current.system_memory_available_mb

        if total <= 0:
            return anomalies

        available_fraction = available / total
        if available_fraction < MEMORY_PRESSURE_FRACTION:
            anomalies.append(
                Anomaly(
                    anomaly_type=AnomalyType.MEMORY_SPIKE,
                    severity=AnomalySeverity.CRITICAL,
                    description=(
                        f"System memory pressure: {available:.0f} MB available "
                        f"({available_fraction:.1%} of {total:.0f} MB total)"
                    ),
                    timestamp=current.timestamp,
                    metric_value=available,
                    threshold=total * MEMORY_PRESSURE_FRACTION,
                )
            )

        return anomalies
