"""#157: profiler memory_spike false-positives on normal Claude CLI startup.

The anomaly detector flags any process whose RSS grows past the ratio threshold
(default 1.5x) in the window — but Claude CLI subprocesses legitimately grow
8-10x (≈25 MB → 200-400 MB) in their first seconds as they load Python, init
the runtime, and parse the prompt. This produced `severity=critical` alerts on
every sheet, burying real anomalies and polluting the CorrelationAnalyzer /
learning store with non-events.

Per the issue's preference-ordered fix (combine options 1+2):
- **Grace period** (`memory_spike_grace_seconds`, default 60.0): don't evaluate
  memory_spike for processes younger than this. Startup growth is over by then;
  a real leak persists for minutes (process is >60s old) so it is STILL caught.
- **Absolute RSS floor** (`memory_spike_min_rss_mb`, default 0.0 = off): optional
  guard so a small-but-high-ratio old process can be ignored. Default keeps the
  current behavior for processes past the grace period.

The reliability guard (don't suppress real leaks) is verified explicitly below:
an old process with sustained large growth is still flagged.
"""

from __future__ import annotations

from marianne.daemon.profiler.anomaly import AnomalyDetector
from marianne.daemon.profiler.models import (
    AnomalyConfig,
    AnomalyType,
    ProcessMetric,
    SystemSnapshot,
)


def _proc(*, rss_mb: float, age_seconds: float, pid: int = 42) -> ProcessMetric:
    return ProcessMetric(
        pid=pid, ppid=1000, command="claude --model opus", state="S",
        cpu_percent=10.0, rss_mb=rss_mb, vms_mb=rss_mb * 2, threads=4,
        open_fds=50, age_seconds=age_seconds, job_id="j", sheet_num=1,
    )


def _snap(*, ts: float, procs: list[ProcessMetric]) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=ts, daemon_pid=1000,
        system_memory_total_mb=48000.0, system_memory_available_mb=44000.0,
        system_memory_used_mb=4000.0, daemon_rss_mb=136.0,
        load_avg_1=1.0, load_avg_5=0.8, load_avg_15=0.5,
        processes=procs, gpus=[], pressure_level="none",
        running_jobs=1, active_sheets=1, zombie_count=0, zombie_pids=[],
    )


def _detector(*, grace: float = 60.0, min_rss: float = 0.0) -> AnomalyDetector:
    return AnomalyDetector(
        config=AnomalyConfig(
            memory_spike_threshold=1.5,
            memory_spike_window_seconds=60.0,
            memory_spike_grace_seconds=grace,
            memory_spike_min_rss_mb=min_rss,
        )
    )


def _spikes(detector: AnomalyDetector, *, baseline_rss: float, current_rss: float,
            age: float) -> list:
    base = _snap(ts=1000.0, procs=[_proc(rss_mb=baseline_rss, age_seconds=age - 30)])
    cur = _snap(ts=1030.0, procs=[_proc(rss_mb=current_rss, age_seconds=age)])
    return [
        a for a in detector.detect(cur, [base])
        if a.anomaly_type == AnomalyType.MEMORY_SPIKE
    ]


class TestMemorySpikeGracePeriod:
    def test_config_defaults(self) -> None:
        cfg = AnomalyConfig()
        assert cfg.memory_spike_grace_seconds == 60.0
        assert cfg.memory_spike_min_rss_mb == 0.0

    def test_young_startup_growth_suppressed(self) -> None:
        # The documented false positive: 26→222 MB on a young (30s) process.
        spikes = _spikes(_detector(), baseline_rss=26.0, current_rss=222.0, age=30.0)
        assert spikes == []

    def test_old_process_real_leak_still_flagged(self) -> None:
        # Reliability guard: a process past the grace window with sustained
        # large growth MUST still be flagged (a real leak grows over minutes).
        spikes = _spikes(_detector(), baseline_rss=400.0, current_rss=1600.0, age=600.0)
        assert len(spikes) == 1
        assert spikes[0].anomaly_type == AnomalyType.MEMORY_SPIKE

    def test_floor_suppresses_small_old_process(self) -> None:
        # With a 500 MB floor, an OLD process that only reached 222 MB is ignored.
        spikes = _spikes(
            _detector(min_rss=500.0), baseline_rss=26.0, current_rss=222.0, age=600.0
        )
        assert spikes == []

    def test_floor_default_off_keeps_old_high_ratio(self) -> None:
        # Default floor (0) → an old high-ratio process is still flagged
        # regardless of absolute size (preserves behavior past the grace period).
        spikes = _spikes(_detector(), baseline_rss=26.0, current_rss=222.0, age=600.0)
        assert len(spikes) == 1
