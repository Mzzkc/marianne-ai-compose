"""Periodic correlation analysis — resource usage vs. job outcomes.

Runs on a configurable interval (default 30 min), queries completed jobs
from ``MonitorStorage``, cross-references with outcomes from the learning
store, and generates ``RESOURCE_CORRELATION`` patterns for statistical
relationships with confidence > 0.6.

No LLM calls — pure statistical analysis.  Minimum sample size of 5 jobs
before any correlations are generated.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from marianne.core.logging import get_logger
from marianne.daemon.profiler.models import CorrelationConfig
from marianne.learning.patterns import PatternType

if TYPE_CHECKING:
    from marianne.daemon.event_bus import EventBus
    from marianne.daemon.learning_hub import LearningHub
    from marianne.daemon.profiler.storage import MonitorStorage

_logger = get_logger("daemon.profiler.correlation")

# Minimum confidence to store a correlation pattern
_MIN_CONFIDENCE = 0.6

# Lookback window for completed jobs (7 days)
_LOOKBACK_SECONDS = 7 * 24 * 3600


class CorrelationAnalyzer:
    """Periodic statistical analysis of resource usage vs. job outcomes.

    Cross-references profiler snapshots (peak memory, CPU, syscall
    distributions, anomalies) with job success/failure outcomes from
    the learning store to identify predictive patterns.

    Lifecycle::

        analyzer = CorrelationAnalyzer(storage, learning_hub, config)
        await analyzer.start(event_bus)
        # ... periodic analysis runs automatically ...
        await analyzer.stop()
    """

    def __init__(
        self,
        storage: MonitorStorage,
        learning_hub: LearningHub,
        config: CorrelationConfig | None = None,
    ) -> None:
        self._storage = storage
        self._learning_hub = learning_hub
        self._config = config or CorrelationConfig()
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None

    async def start(self, event_bus: EventBus) -> None:
        """Start the periodic analysis loop.

        The event_bus parameter is accepted for interface consistency
        with other daemon components but is not currently used by the
        correlation analyzer (it reads from storage, not events).
        """
        if self._running:
            return

        self._running = True
        self._loop_task = asyncio.create_task(
            self._analysis_loop(), name="correlation-analysis-loop"
        )
        _logger.info(
            "correlation_analyzer.started",
            interval_minutes=self._config.interval_minutes,
            min_sample_size=self._config.min_sample_size,
        )

    async def stop(self) -> None:
        """Stop the periodic analysis loop."""
        self._running = False

        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        _logger.info("correlation_analyzer.stopped")

    async def analyze(self) -> list[dict[str, Any]]:
        """Run correlation analysis on completed jobs.

        Steps:
        1. Query completed jobs from storage (last 7 days)
        2. For each job: get peak memory, total CPU, syscall distribution
        3. Cross-reference with job outcomes from learning store
        4. Statistical analysis:
           - Memory vs failure rate (binned histogram)
           - Syscall hotspots vs failure rate
           - Anomaly presence vs failure rate
           - Execution duration vs failure rate
        5. Generate RESOURCE_CORRELATION patterns for confidence > 0.6
        6. Store in LearningHub

        Returns:
            List of generated correlation dicts (for testing/logging).
        """
        if not self._learning_hub.is_running:
            _logger.debug("correlation_analyzer.learning_hub_not_running")
            return []

        # 1. Get completed job profiles from storage
        since = time.time() - _LOOKBACK_SECONDS
        job_profiles = await self._get_job_profiles(since)

        if len(job_profiles) < self._config.min_sample_size:
            _logger.debug(
                "correlation_analyzer.insufficient_samples",
                sample_count=len(job_profiles),
                min_required=self._config.min_sample_size,
            )
            return []

        # 2. Cross-reference with outcomes from learning store
        enriched = self._enrich_with_outcomes(job_profiles)

        if not enriched:
            _logger.debug("correlation_analyzer.no_enriched_profiles")
            return []

        # 3. Run statistical analyses
        correlations: list[dict[str, Any]] = []
        correlations.extend(self._analyze_memory_vs_failure(enriched))
        correlations.extend(self._analyze_syscall_vs_failure(enriched))
        correlations.extend(self._analyze_duration_vs_failure(enriched))

        # 4. Filter by confidence and store
        stored = 0
        for corr in correlations:
            if corr["confidence"] >= _MIN_CONFIDENCE:
                self._store_correlation(corr)
                stored += 1

        if stored > 0:
            _logger.info(
                "correlation_analyzer.patterns_generated",
                total_analyzed=len(enriched),
                correlations_found=len(correlations),
                patterns_stored=stored,
            )

        return correlations

    # ------------------------------------------------------------------
    # Internal: data collection
    # ------------------------------------------------------------------

    async def _get_job_profiles(self, since: float) -> list[dict[str, Any]]:
        """Query resource profiles for all jobs with data since *since*.

        Returns a list of resource profile dicts (from MonitorStorage).
        """
        profiles: list[dict[str, Any]] = []

        # Read events to find unique job IDs
        events = await self._storage.read_events(since=since, limit=10000)
        job_ids: set[str] = set()
        for event in events:
            if event.job_id:
                job_ids.add(event.job_id)

        # Also scan snapshots for job IDs via process metrics
        snapshots = await self._storage.read_snapshots(since=since, limit=5000)
        for snap in snapshots:
            for proc in snap.processes:
                if proc.job_id:
                    job_ids.add(proc.job_id)

        # Get resource profile for each job
        for job_id in job_ids:
            try:
                profile = await self._storage.read_job_resource_profile(job_id)
                if profile.get("unique_pid_count", 0) > 0:
                    profiles.append(profile)
            except Exception:  # noqa: BLE001 — structured logger reports correlation_analyzer.profile_read_failed
                _logger.debug(
                    "correlation_analyzer.profile_read_failed",
                    job_id=job_id,
                    exc_info=True,
                )

        return profiles

    def _enrich_with_outcomes(
        self, profiles: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Cross-reference job profiles with outcomes from the learning store.

        For each job profile, queries the learning store for execution
        records to determine success/failure. Returns profiles enriched
        with an ``outcome`` field ('success' or 'failure').
        """
        store = self._learning_hub.store
        enriched: list[dict[str, Any]] = []

        for profile in profiles:
            job_id = profile.get("job_id", "")
            if not job_id:
                continue

            # Try to determine outcome from learning store patterns
            # Look for patterns tagged with this job's outcome
            outcome = self._infer_outcome(store, job_id)
            if outcome is not None:
                profile["outcome"] = outcome
                enriched.append(profile)

        return enriched

    def _infer_outcome(self, store: Any, job_id: str) -> str | None:
        """Infer job outcome (success/failure) from learning store patterns.

        Queries patterns tagged with this job_id to determine if the job
        succeeded or failed. Returns 'success', 'failure', or None if
        outcome cannot be determined.
        """
        try:
            # Query patterns that mention this job
            patterns = store.get_patterns(
                pattern_type=None,
                context_tags=[f"job:{job_id}"],
                limit=10,
                min_priority=0.0,
            )
            for pattern in patterns:
                tags = pattern.context_tags if hasattr(pattern, "context_tags") else ""
                if isinstance(tags, str):
                    tag_str = tags
                elif isinstance(tags, list):
                    tag_str = ",".join(tags)
                else:
                    tag_str = str(tags)
                if "outcome:success" in tag_str:
                    return "success"
                if "outcome:failure" in tag_str:
                    return "failure"
        except Exception:  # noqa: BLE001 — structured logger reports correlation_analyzer.outcome_inference_failed
            _logger.debug(
                "correlation_analyzer.outcome_inference_failed",
                job_id=job_id,
                exc_info=True,
            )

        return None

    # ------------------------------------------------------------------
    # Statistical analyses
    # ------------------------------------------------------------------

    def _analyze_memory_vs_failure(
        self, profiles: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Analyze correlation between peak memory and failure rate.

        Bins jobs by peak RSS and computes failure rate per bin.
        """
        correlations: list[dict[str, Any]] = []

        # Bin boundaries in MB
        bins = [0, 256, 512, 1024, 2048, float("inf")]
        bin_labels = ["<256MB", "256-512MB", "512MB-1GB", "1-2GB", ">2GB"]

        for i in range(len(bins) - 1):
            low, high = bins[i], bins[i + 1]
            in_bin = [
                p for p in profiles
                if low <= p.get("peak_rss_mb", 0) < high
            ]

            if len(in_bin) < 3:
                continue

            failures = sum(1 for p in in_bin if p.get("outcome") == "failure")
            failure_rate = failures / len(in_bin)
            total_failure_rate = sum(
                1 for p in profiles if p.get("outcome") == "failure"
            ) / len(profiles)

            # Only report if meaningfully different from baseline
            deviation = abs(failure_rate - total_failure_rate)
            if deviation < 0.1:
                continue

            # Confidence based on sample size and deviation
            confidence = min(
                0.95,
                0.5 + (len(in_bin) / len(profiles)) * 0.2 + deviation * 0.5,
            )

            correlations.append({
                "type": "memory_vs_failure",
                "description": (
                    f"Jobs with peak RSS {bin_labels[i]} have "
                    f"{failure_rate:.0%} failure rate "
                    f"(baseline: {total_failure_rate:.0%})"
                ),
                "confidence": confidence,
                "context_tags": [
                    f"memory_bin:{bin_labels[i]}",
                    f"failure_rate:{failure_rate:.2f}",
                    f"sample_size:{len(in_bin)}",
                ],
            })

        return correlations

    def _analyze_syscall_vs_failure(
        self, profiles: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Analyze correlation between dominant syscalls and failure rate.

        Identifies syscall hotspots (>35% of time) and their correlation
        with job outcomes.
        """
        correlations: list[dict[str, Any]] = []
        total_failure_rate = sum(
            1 for p in profiles if p.get("outcome") == "failure"
        ) / max(len(profiles), 1)

        # Collect syscall dominance per job
        syscall_groups: dict[str, list[dict[str, Any]]] = {}

        for profile in profiles:
            hotspots = profile.get("syscall_hotspots", {})
            if not hotspots:
                continue

            # Find dominant syscall (highest cumulative time %)
            total_pct = sum(hotspots.values())
            if total_pct <= 0:
                continue

            for syscall, pct in hotspots.items():
                relative_pct = pct / total_pct
                if relative_pct > 0.35:
                    if syscall not in syscall_groups:
                        syscall_groups[syscall] = []
                    syscall_groups[syscall].append(profile)

        for syscall, group in syscall_groups.items():
            if len(group) < 3:
                continue

            failures = sum(1 for p in group if p.get("outcome") == "failure")
            failure_rate = failures / len(group)

            deviation = abs(failure_rate - total_failure_rate)
            if deviation < 0.1:
                continue

            confidence = min(
                0.90,
                0.5 + (len(group) / len(profiles)) * 0.2 + deviation * 0.4,
            )

            direction = "higher" if failure_rate > total_failure_rate else "lower"
            correlations.append({
                "type": "syscall_vs_failure",
                "description": (
                    f"Jobs where {syscall}() dominates (>35% time) have "
                    f"{direction} failure rate ({failure_rate:.0%} vs "
                    f"{total_failure_rate:.0%} baseline)"
                ),
                "confidence": confidence,
                "context_tags": [
                    f"syscall_dominant:{syscall}",
                    f"failure_rate:{failure_rate:.2f}",
                    f"sample_size:{len(group)}",
                ],
            })

        return correlations

    def _analyze_duration_vs_failure(
        self, profiles: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Analyze correlation between execution duration and failure rate.

        Uses process age as a proxy for execution duration.
        """
        correlations: list[dict[str, Any]] = []

        total_failure_rate = sum(
            1 for p in profiles if p.get("outcome") == "failure"
        ) / max(len(profiles), 1)

        # Bin by duration (using peak RSS as a proxy — sheet_metrics
        # contain per-sheet data, but we don't have explicit duration).
        # Use process spawn count as a duration proxy: more spawns = longer
        long_jobs = [
            p for p in profiles if p.get("process_spawn_count", 0) > 3
        ]
        short_jobs = [
            p for p in profiles if p.get("process_spawn_count", 0) <= 3
        ]

        for label, group in [("long (>3 spawns)", long_jobs), ("short (<=3 spawns)", short_jobs)]:
            if len(group) < 3:
                continue

            failures = sum(1 for p in group if p.get("outcome") == "failure")
            failure_rate = failures / len(group)

            deviation = abs(failure_rate - total_failure_rate)
            if deviation < 0.1:
                continue

            confidence = min(
                0.85,
                0.5 + (len(group) / len(profiles)) * 0.2 + deviation * 0.4,
            )

            correlations.append({
                "type": "duration_vs_failure",
                "description": (
                    f"{label.capitalize()} jobs have {failure_rate:.0%} "
                    f"failure rate (baseline: {total_failure_rate:.0%})"
                ),
                "confidence": confidence,
                "context_tags": [
                    f"duration_class:{label}",
                    f"failure_rate:{failure_rate:.2f}",
                    f"sample_size:{len(group)}",
                ],
            })

        return correlations

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _store_correlation(self, correlation: dict[str, Any]) -> None:
        """Store a correlation as a RESOURCE_CORRELATION pattern."""
        store = self._learning_hub.store
        pattern_name = f"[{correlation['type']}] {correlation['description'][:100]}"

        store.record_pattern(
            pattern_type=PatternType.RESOURCE_CORRELATION.value,
            pattern_name=pattern_name,
            description=correlation["description"],
            context_tags=correlation.get("context_tags", []),
            suggested_action=correlation["description"],
        )

    # ------------------------------------------------------------------
    # Analysis loop
    # ------------------------------------------------------------------

    async def _analysis_loop(self) -> None:
        """Periodic loop running correlation analysis."""
        # Initial delay: wait one interval before first analysis
        # to allow data to accumulate
        try:
            await asyncio.sleep(self._config.interval_minutes * 60)
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                await self.analyze()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — structured logger reports correlation_analyzer.analysis_failed
                _logger.warning(
                    "correlation_analyzer.analysis_failed",
                    exc_info=True,
                )

            try:
                await asyncio.sleep(self._config.interval_minutes * 60)
            except asyncio.CancelledError:
                break


__all__ = ["CorrelationAnalyzer"]
