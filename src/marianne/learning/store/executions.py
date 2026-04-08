"""Execution-related mixin for GlobalLearningStore.

This module provides the ExecutionMixin class that handles all execution-related
operations including:
- Recording sheet execution outcomes
- Querying execution statistics
- Finding similar historical executions
- Workspace clustering for cross-workspace learning
- Optimal execution window analysis

Extracted from global_store.py as part of the modularization effort.
"""

import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any

from marianne.core.logging import MarianneLogger
from marianne.learning.outcomes import SheetOutcome
from marianne.learning.store.base import WhereBuilder, _logger
from marianne.learning.store.models import ExecutionRecord


class ExecutionMixin:
    """Mixin providing execution-related methods for GlobalLearningStore.

    This mixin requires that the composed class provides:
    - _get_connection(): Context manager yielding sqlite3.Connection
    - _logger: Logger instance for logging
    - hash_workspace(workspace_path): Static method to hash workspace paths
    - hash_job(job_name, config_hash): Static method to hash job identifiers

    Execution Recording Methods:
    - record_outcome: Record a sheet execution outcome
    - _extract_sheet_num: Helper to parse sheet numbers from IDs
    - _calculate_confidence: Calculate confidence score for an outcome

    Execution Statistics Methods:
    - get_execution_stats: Get aggregate statistics from the global store
    - get_recent_executions: Get recent execution records

    Similar Executions Methods (Learning Activation):
    - get_similar_executions: Find similar historical executions
    - get_optimal_execution_window: Analyze optimal times for execution

    Workspace Clustering Methods:
    - get_workspace_cluster: Get cluster ID for a workspace
    - assign_workspace_cluster: Assign a workspace to a cluster
    - get_similar_workspaces: Get workspaces in the same cluster
    """

    # Type hints for attributes provided by GlobalLearningStoreBase
    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]
    hash_workspace: Callable[..., str]  # (Path) -> str
    hash_job: Callable[..., str]  # (str, str | None) -> str

    def record_outcome(
        self,
        outcome: SheetOutcome,
        workspace_path: Path,
        model: str | None = None,
        error_codes: list[str] | None = None,
    ) -> str:
        """Record a sheet outcome to the global store.

        Args:
            outcome: The SheetOutcome to record.
            workspace_path: Path to the workspace for hashing.
            model: Optional model name used for execution.
            error_codes: Optional list of error codes encountered.

        Returns:
            The execution record ID.
        """
        execution_id = str(uuid.uuid4())
        workspace_hash = self.hash_workspace(workspace_path)
        job_hash = self.hash_job(outcome.job_id)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO executions (
                    id, workspace_hash, job_hash, sheet_num,
                    started_at, completed_at, duration_seconds,
                    status, retry_count, success_without_retry,
                    validation_pass_rate, confidence_score, model, error_codes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    workspace_hash,
                    job_hash,
                    self._extract_sheet_num(outcome.sheet_id),
                    outcome.timestamp.isoformat(),
                    datetime.now().isoformat(),
                    outcome.execution_duration,
                    outcome.final_status.value,
                    outcome.retry_count,
                    outcome.success_without_retry,
                    outcome.validation_pass_rate,
                    self._calculate_confidence(outcome),
                    model,
                    json.dumps(error_codes or []),
                ),
            )

        _logger.debug(
            f"Recorded outcome {execution_id} for sheet {outcome.sheet_id}"
        )
        return execution_id

    def _extract_sheet_num(self, sheet_id: str) -> int:
        """Extract sheet number from sheet ID.

        Handles various formats:
        - "job-sheet1" -> 1
        - "job-1" -> 1
        - "sheet1" -> 1
        - "1" -> 1
        """
        # Try to find a number at the end of the string
        match = re.search(r"(\d+)$", sheet_id)
        if match:
            return int(match.group(1))
        return 0

    def _calculate_confidence(self, outcome: SheetOutcome) -> float:
        """Calculate a confidence score for an outcome.

        Uses validation results and retry count to estimate confidence.
        """
        base_confidence = outcome.validation_pass_rate

        # Penalize high retry counts
        retry_penalty = 0.1 * min(outcome.retry_count, 5)
        base_confidence -= retry_penalty

        # Boost success without retry
        if outcome.success_without_retry:
            base_confidence = min(1.0, base_confidence + 0.1)

        return max(0.0, min(1.0, base_confidence))

    def get_execution_stats(self) -> dict[str, Any]:
        """Get aggregate statistics from the global store.

        Returns:
            Dictionary with stats like total_executions, success_rate, etc.
        """
        with self._get_connection() as conn:
            stats: dict[str, Any] = {}

            # Total executions
            cursor = conn.execute("SELECT COUNT(*) as count FROM executions")
            stats["total_executions"] = cursor.fetchone()["count"]

            # First-attempt success rate
            cursor = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN success_without_retry THEN 1 ELSE 0 END) as successes,
                    COUNT(*) as total
                FROM executions
                """
            )
            row = cursor.fetchone()
            if row["total"] > 0:
                stats["success_without_retry_rate"] = row["successes"] / row["total"]
            else:
                stats["success_without_retry_rate"] = 0.0

            # Total patterns
            cursor = conn.execute("SELECT COUNT(*) as count FROM patterns")
            stats["total_patterns"] = cursor.fetchone()["count"]

            # Average pattern effectiveness
            cursor = conn.execute(
                "SELECT AVG(effectiveness_score) as avg FROM patterns"
            )
            row = cursor.fetchone()
            stats["avg_pattern_effectiveness"] = row["avg"] or 0.0

            # Total error recoveries
            cursor = conn.execute("SELECT COUNT(*) as count FROM error_recoveries")
            stats["total_error_recoveries"] = cursor.fetchone()["count"]

            # Unique workspaces
            cursor = conn.execute(
                "SELECT COUNT(DISTINCT workspace_hash) as count FROM executions"
            )
            stats["unique_workspaces"] = cursor.fetchone()["count"]

            return stats

    def get_recent_executions(
        self,
        limit: int = 20,
        workspace_hash: str | None = None,
    ) -> list[ExecutionRecord]:
        """Get recent execution records.

        Args:
            limit: Maximum number of records to return.
            workspace_hash: Optional filter by workspace.

        Returns:
            List of ExecutionRecord objects.
        """
        with self._get_connection() as conn:
            if workspace_hash:
                cursor = conn.execute(
                    """
                    SELECT * FROM executions
                    WHERE workspace_hash = ?
                    ORDER BY completed_at DESC
                    LIMIT ?
                    """,
                    (workspace_hash, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM executions
                    ORDER BY completed_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )

            records = []
            for row in cursor.fetchall():
                records.append(
                    ExecutionRecord(
                        id=row["id"],
                        workspace_hash=row["workspace_hash"],
                        job_hash=row["job_hash"],
                        sheet_num=row["sheet_num"],
                        started_at=datetime.fromisoformat(row["started_at"])
                        if row["started_at"]
                        else None,
                        completed_at=datetime.fromisoformat(row["completed_at"])
                        if row["completed_at"]
                        else None,
                        duration_seconds=row["duration_seconds"] or 0.0,
                        status=row["status"] or "",
                        retry_count=row["retry_count"] or 0,
                        success_without_retry=bool(row["success_without_retry"]),
                        validation_pass_rate=row["validation_pass_rate"] or 0.0,
                        confidence_score=row["confidence_score"] or 0.0,
                        model=row["model"],
                        error_codes=json.loads(row["error_codes"] or "[]"),
                    )
                )

            return records

    # =========================================================================
    # Learning Activation: Similar Executions and Optimal Timing
    # =========================================================================

    def get_similar_executions(
        self,
        job_hash: str | None = None,
        workspace_hash: str | None = None,
        sheet_num: int | None = None,
        limit: int = 10,
    ) -> list[ExecutionRecord]:
        """Get similar historical executions for learning.

        Learning Activation: Enables querying executions that are similar to
        the current context, supporting pattern-based decision making.

        Args:
            job_hash: Optional job hash to filter by similar jobs.
            workspace_hash: Optional workspace hash to filter by.
            sheet_num: Optional sheet number to filter by.
            limit: Maximum number of records to return.

        Returns:
            List of ExecutionRecord objects matching the criteria.
        """
        with self._get_connection() as conn:
            wb = WhereBuilder()
            if job_hash is not None:
                wb.add("job_hash = ?", job_hash)
            if workspace_hash is not None:
                wb.add("workspace_hash = ?", workspace_hash)
            if sheet_num is not None:
                wb.add("sheet_num = ?", sheet_num)

            where_sql, params = wb.build()
            cursor = conn.execute(
                f"""
                SELECT * FROM executions
                WHERE {where_sql}
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                (*params, limit),
            )

            records = []
            for row in cursor.fetchall():
                records.append(
                    ExecutionRecord(
                        id=row["id"],
                        workspace_hash=row["workspace_hash"],
                        job_hash=row["job_hash"],
                        sheet_num=row["sheet_num"],
                        started_at=datetime.fromisoformat(row["started_at"])
                        if row["started_at"]
                        else None,
                        completed_at=datetime.fromisoformat(row["completed_at"])
                        if row["completed_at"]
                        else None,
                        duration_seconds=row["duration_seconds"] or 0.0,
                        status=row["status"] or "",
                        retry_count=row["retry_count"] or 0,
                        success_without_retry=bool(row["success_without_retry"]),
                        validation_pass_rate=row["validation_pass_rate"] or 0.0,
                        confidence_score=row["confidence_score"] or 0.0,
                        model=row["model"],
                        error_codes=json.loads(row["error_codes"] or "[]"),
                    )
                )

            return records

    def get_optimal_execution_window(
        self,
        error_code: str | None = None,
        model: str | None = None,  # noqa: ARG002 - reserved for future model filtering
    ) -> dict[str, Any]:
        """Analyze historical data to find optimal execution windows.

        Learning Activation: Identifies times of day when executions are most
        likely to succeed, enabling time-aware scheduling recommendations.

        Args:
            error_code: Optional error code to analyze (e.g., for rate limits).
            model: Optional model to filter by.

        Returns:
            Dict with optimal window analysis:
            - optimal_hours: List of hours (0-23) with best success rates
            - avoid_hours: List of hours with high failure/rate limit rates
            - confidence: Confidence in the recommendation (0.0-1.0)
            - sample_count: Number of samples analyzed
        """
        with self._get_connection() as conn:
            # Query success rate by hour of day from error_recoveries
            if error_code:
                cursor = conn.execute(
                    """
                    SELECT
                        time_of_day,
                        COUNT(*) as total,
                        SUM(CASE WHEN recovery_success THEN 1 ELSE 0 END) as successes
                    FROM error_recoveries
                    WHERE error_code = ?
                    GROUP BY time_of_day
                    ORDER BY time_of_day
                    """,
                    (error_code,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT
                        time_of_day,
                        COUNT(*) as total,
                        SUM(CASE WHEN recovery_success THEN 1 ELSE 0 END) as successes
                    FROM error_recoveries
                    GROUP BY time_of_day
                    ORDER BY time_of_day
                    """
                )

            rows = cursor.fetchall()

            if not rows:
                return {
                    "optimal_hours": [],
                    "avoid_hours": [],
                    "confidence": 0.0,
                    "sample_count": 0,
                }

            # Analyze success rates by hour
            hour_stats: dict[int, tuple[int, int]] = {}  # hour -> (successes, total)
            total_samples = 0

            for row in rows:
                hour = row["time_of_day"]
                total = row["total"]
                successes = row["successes"]
                hour_stats[hour] = (successes, total)
                total_samples += total

            # Find optimal and avoid hours
            optimal_hours: list[int] = []
            avoid_hours: list[int] = []

            for hour, (successes, total) in hour_stats.items():
                if total >= 3:  # Minimum samples for confidence
                    success_rate = successes / total
                    if success_rate >= 0.7:
                        optimal_hours.append(hour)
                    elif success_rate <= 0.3:
                        avoid_hours.append(hour)

            # Calculate confidence based on sample size
            confidence = min(total_samples / 50.0, 1.0)

            return {
                "optimal_hours": sorted(optimal_hours),
                "avoid_hours": sorted(avoid_hours),
                "confidence": confidence,
                "sample_count": total_samples,
            }

    # =========================================================================
    # Workspace Clustering Methods
    # =========================================================================

    def get_workspace_cluster(
        self,
        workspace_hash: str,
    ) -> str | None:
        """Get the cluster ID for a workspace.

        Learning Activation: Supports workspace similarity by grouping
        workspaces with similar patterns into clusters.

        Args:
            workspace_hash: Hash of the workspace to query.

        Returns:
            Cluster ID if assigned, None otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT cluster_id FROM workspace_clusters WHERE workspace_hash = ?",
                (workspace_hash,),
            )
            row = cursor.fetchone()
            return row["cluster_id"] if row else None

    def assign_workspace_cluster(
        self,
        workspace_hash: str,
        cluster_id: str,
    ) -> None:
        """Assign a workspace to a cluster.

        Learning Activation: Groups workspaces with similar execution
        patterns for targeted pattern recommendations.

        Args:
            workspace_hash: Hash of the workspace.
            cluster_id: ID of the cluster to assign to.
        """
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO workspace_clusters
                (workspace_hash, cluster_id, assigned_at)
                VALUES (?, ?, ?)
                """,
                (workspace_hash, cluster_id, now),
            )

    def get_similar_workspaces(
        self,
        cluster_id: str,
        limit: int = 10,
    ) -> list[str]:
        """Get workspace hashes in the same cluster.

        Learning Activation: Enables cross-workspace learning by identifying
        workspaces with similar patterns.

        Args:
            cluster_id: Cluster ID to query.
            limit: Maximum number of workspace hashes to return.

        Returns:
            List of workspace hashes in the cluster.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT workspace_hash FROM workspace_clusters
                WHERE cluster_id = ?
                ORDER BY assigned_at DESC
                LIMIT ?
                """,
                (cluster_id, limit),
            )
            return [row["workspace_hash"] for row in cursor.fetchall()]

    # =========================================================================
    # Error Recovery Methods (Adaptive Wait Time Learning)
    # =========================================================================

    def record_error_recovery(
        self,
        error_code: str,
        suggested_wait: float,
        actual_wait: float,
        recovery_success: bool,
        model: str | None = None,
    ) -> str:
        """Record an error recovery for learning adaptive wait times.

        Args:
            error_code: The error code (e.g., 'E103').
            suggested_wait: The initially suggested wait time in seconds.
            actual_wait: The actual wait time used in seconds.
            recovery_success: Whether recovery after waiting succeeded.
            model: Optional model name.

        Returns:
            The recovery record ID.
        """
        record_id = str(uuid.uuid4())
        now = datetime.now()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO error_recoveries (
                    id, error_code, suggested_wait, actual_wait,
                    recovery_success, recorded_at, model, time_of_day
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    error_code,
                    suggested_wait,
                    actual_wait,
                    recovery_success,
                    now.isoformat(),
                    model,
                    now.hour,
                ),
            )

        return record_id

    def get_learned_wait_time(
        self,
        error_code: str,
        model: str | None = None,
        min_samples: int = 3,
    ) -> float | None:
        """Get the learned optimal wait time for an error code.

        Analyzes past error recoveries to suggest an adaptive wait time.

        Args:
            error_code: The error code to look up.
            model: Optional model to filter by.
            min_samples: Minimum samples required before learning.

        Returns:
            Suggested wait time in seconds, or None if not enough data.
        """
        with self._get_connection() as conn:
            if model:
                cursor = conn.execute(
                    """
                    SELECT actual_wait, recovery_success
                    FROM error_recoveries
                    WHERE error_code = ? AND model = ? AND recovery_success = 1
                    ORDER BY recorded_at DESC
                    LIMIT 20
                    """,
                    (error_code, model),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT actual_wait, recovery_success
                    FROM error_recoveries
                    WHERE error_code = ? AND recovery_success = 1
                    ORDER BY recorded_at DESC
                    LIMIT 20
                    """,
                    (error_code,),
                )

            rows = cursor.fetchall()
            if len(rows) < min_samples:
                return None

            # Use weighted average favoring shorter successful waits
            waits: list[float] = [float(row["actual_wait"]) for row in rows]
            # Lower waits are better, so we use harmonic mean-like weighting
            avg_wait = sum(waits) / len(waits)
            min_successful = min(waits)

            # Blend toward shorter waits that still work
            return (avg_wait + min_successful) / 2

    def get_learned_wait_time_with_fallback(
        self,
        error_code: str,
        static_delay: float,
        model: str | None = None,
        min_samples: int = 3,
        min_confidence: float = 0.7,
    ) -> tuple[float, float, str]:
        """Get learned wait time with fallback to static delay and confidence.

        This method bridges the global learning store with retry strategies.
        It returns a delay value along with a confidence score indicating
        how much to trust the learned value.

        Evolution #3: Learned Wait Time Injection - provides the bridge between
        global_store's cross-workspace learned delays and retry_strategy's
        blend_historical_delay() method.

        Args:
            error_code: The error code to look up (e.g., 'E101').
            static_delay: Fallback static delay if no learned data available.
            model: Optional model to filter by.
            min_samples: Minimum samples required for learning.
            min_confidence: Minimum confidence threshold for using learned delay.

        Returns:
            Tuple of (delay_seconds, confidence, strategy_name).
            - delay_seconds: The recommended delay (learned or static).
            - confidence: Confidence in the recommendation (0.0-1.0).
              High confidence (>=0.7) means learned delay is reliable.
              Low confidence (<0.7) means learned delay should be blended with static.
            - strategy_name: "global_learned" | "global_learned_blend" | "static_fallback"
        """
        # Query learned wait time from global store
        learned_wait = self.get_learned_wait_time(
            error_code=error_code,
            model=model,
            min_samples=min_samples,
        )

        if learned_wait is None:
            # No learned data - fall back to static
            return static_delay, 0.0, "static_fallback"

        # Calculate confidence based on sample count
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count
                FROM error_recoveries
                WHERE error_code = ? AND recovery_success = 1
                """,
                (error_code,),
            )
            sample_count = cursor.fetchone()["count"]

        # Confidence scales with sample count: 10 samples = 1.0 confidence
        confidence = min(sample_count / 10.0, 1.0)

        # Apply floor: learned delay shouldn't be less than 50% of static
        # This prevents overly aggressive waits that might fail
        delay_floor = static_delay * 0.5

        if confidence >= min_confidence:
            # High confidence: use learned delay (with floor)
            final_delay = max(learned_wait, delay_floor)
            return final_delay, confidence, "global_learned"
        else:
            # Low confidence: blend learned with static
            # weight = confidence / min_confidence (0.0 to 1.0)
            weight = confidence / min_confidence
            blended = weight * max(learned_wait, delay_floor) + (1 - weight) * static_delay
            return blended, confidence, "global_learned_blend"

    def get_error_recovery_sample_count(self, error_code: str) -> int:
        """Get the number of successful recovery samples for an error code.

        Args:
            error_code: The error code to query.

        Returns:
            Number of successful recovery samples.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count
                FROM error_recoveries
                WHERE error_code = ? AND recovery_success = 1
                """,
                (error_code,),
            )
            result: int = cursor.fetchone()["count"]
            return result


# Export public API
__all__ = [
    "ExecutionMixin",
]
