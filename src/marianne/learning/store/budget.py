"""Budget and entropy response mixin for the global learning store.

This module contains the BudgetMixin class that provides exploration budget
management and automatic entropy response functionality. The exploration budget
prevents pattern selection from converging to zero diversity, while entropy
responses automatically inject diversity when the system detects low entropy.

Evolution v23: Exploration Budget Maintenance - dynamic budget with floor/ceiling.
Evolution v23: Automatic Entropy Response - automatic diversity injection.

Extracted from global_store.py as part of the modularization effort.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from marianne.core.logging import MarianneLogger, get_logger

from .models import EntropyResponseRecord, ExplorationBudgetRecord, PatternEntropyMetrics

_logger = get_logger("learning.global_store")


@dataclass
class EntropyResponseConfig:
    """Configuration for entropy response actions.

    Groups the tuneable parameters for ``trigger_entropy_response()``,
    reducing its parameter count and making configs reusable.
    """

    boost_budget: bool = True
    """Whether to boost exploration budget."""

    revisit_quarantine: bool = True
    """Whether to revisit quarantined patterns."""

    max_quarantine_revisits: int = 3
    """Maximum quarantined patterns to revisit per response."""

    budget_floor: float = 0.05
    """Floor for budget enforcement."""

    budget_ceiling: float = 0.50
    """Ceiling for budget enforcement."""

    budget_boost_amount: float = 0.10
    """Amount to boost budget by."""


@dataclass
class EntropyTriggerContext:
    """Runtime context for an entropy response trigger.

    Bundles the positional parameters of ``trigger_entropy_response()`` into a
    single typed object, keeping the method signature manageable when combined
    with ``EntropyResponseConfig``.
    """

    job_hash: str
    """Hash of the job that triggered the response."""

    entropy_at_trigger: float
    """Entropy value that crossed the threshold."""

    threshold_used: float
    """The threshold that was crossed."""


class BudgetMixin:
    """Mixin providing exploration budget and entropy response functionality.

    This mixin provides methods for managing the exploration budget (which
    controls how much the system explores vs exploits known patterns) and
    automatic entropy responses (which inject diversity when entropy drops).

    The exploration budget uses a floor to ensure diversity never goes to zero,
    and a ceiling to prevent over-exploration. It adjusts dynamically based on
    measured entropy: low entropy triggers boosts, healthy entropy allows decay.

    Requires the following from the composed class:
        - _get_connection() -> context manager yielding sqlite3.Connection
    """

    # Annotations for attributes provided by the composed class (GlobalLearningStoreBase)
    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]

    @staticmethod
    def _where_job_hash(
        job_hash: str | None,
    ) -> tuple[str, tuple[str, ...] | tuple[()]]:
        """Build an optional WHERE clause and params for job_hash filtering.

        Returns a (clause, params) tuple that can be appended to SQL queries.
        When job_hash is None, returns empty strings/tuples so the query
        runs unfiltered.
        """
        if job_hash:
            return "WHERE job_hash = ?", (job_hash,)
        return "", ()

    # =========================================================================
    # v23 Evolution: Exploration Budget Maintenance
    # =========================================================================

    def get_exploration_budget(
        self,
        job_hash: str | None = None,
    ) -> ExplorationBudgetRecord | None:
        """Get the most recent exploration budget record.

        v23 Evolution: Exploration Budget Maintenance - returns the current
        exploration budget state for pattern selection modulation.

        Args:
            job_hash: Optional job hash to filter by specific job.

        Returns:
            The most recent ExplorationBudgetRecord, or None if no budget recorded.
        """
        where, params = self._where_job_hash(job_hash)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT id, job_hash, recorded_at, budget_value,
                       entropy_at_time, adjustment_type, adjustment_reason
                FROM exploration_budget
                {where}
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                params,
            )
            row = cursor.fetchone()

            if not row:
                return None

            return ExplorationBudgetRecord(
                id=row["id"],
                job_hash=row["job_hash"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
                budget_value=row["budget_value"],
                entropy_at_time=row["entropy_at_time"],
                adjustment_type=row["adjustment_type"],
                adjustment_reason=row["adjustment_reason"],
            )

    def get_exploration_budget_history(
        self,
        job_hash: str | None = None,
        limit: int = 50,
    ) -> list[ExplorationBudgetRecord]:
        """Get exploration budget history for analysis.

        v23 Evolution: Exploration Budget Maintenance - returns historical
        budget records for visualization and trend analysis.

        Args:
            job_hash: Optional job hash to filter by specific job.
            limit: Maximum number of records to return.

        Returns:
            List of ExplorationBudgetRecord objects, most recent first.
        """
        where, params = self._where_job_hash(job_hash)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT id, job_hash, recorded_at, budget_value,
                       entropy_at_time, adjustment_type, adjustment_reason
                FROM exploration_budget
                {where}
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (*params, limit),
            )

            return [
                ExplorationBudgetRecord(
                    id=row["id"],
                    job_hash=row["job_hash"],
                    recorded_at=datetime.fromisoformat(row["recorded_at"]),
                    budget_value=row["budget_value"],
                    entropy_at_time=row["entropy_at_time"],
                    adjustment_type=row["adjustment_type"],
                    adjustment_reason=row["adjustment_reason"],
                )
                for row in cursor.fetchall()
            ]

    def update_exploration_budget(
        self,
        job_hash: str,
        budget_value: float,
        adjustment_type: str,
        entropy_at_time: float | None = None,
        adjustment_reason: str | None = None,
        floor: float = 0.05,
        ceiling: float = 0.50,
    ) -> ExplorationBudgetRecord:
        """Update the exploration budget with floor and ceiling enforcement.

        v23 Evolution: Exploration Budget Maintenance - records budget
        adjustments while enforcing floor (never go to zero) and ceiling limits.

        Args:
            job_hash: Hash of the job updating the budget.
            budget_value: Proposed new budget value.
            adjustment_type: Type: 'initial', 'decay', 'boost', 'floor_enforced'.
            entropy_at_time: Optional entropy measurement at adjustment time.
            adjustment_reason: Human-readable reason for adjustment.
            floor: Minimum allowed budget (default 0.05 = 5%).
            ceiling: Maximum allowed budget (default 0.50 = 50%).

        Returns:
            The new ExplorationBudgetRecord.
        """
        # Enforce floor and ceiling
        original_value = budget_value
        budget_value = max(floor, min(ceiling, budget_value))

        # Update adjustment_type if floor was enforced
        if original_value < floor:
            adjustment_type = "floor_enforced"
            adjustment_reason = (
                f"Budget {original_value:.3f} enforced to floor {floor:.3f}"
            )
        elif original_value > ceiling:
            adjustment_type = "ceiling_enforced"
            adjustment_reason = (
                f"Budget {original_value:.3f} enforced to ceiling {ceiling:.3f}"
            )

        record_id = str(uuid.uuid4())
        recorded_at = datetime.now()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO exploration_budget (
                    id, job_hash, recorded_at, budget_value,
                    entropy_at_time, adjustment_type, adjustment_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    job_hash,
                    recorded_at.isoformat(),
                    budget_value,
                    entropy_at_time,
                    adjustment_type,
                    adjustment_reason,
                ),
            )

        _logger.debug(
            f"Updated exploration budget: {budget_value:.3f} ({adjustment_type})"
        )

        return ExplorationBudgetRecord(
            id=record_id,
            job_hash=job_hash,
            recorded_at=recorded_at,
            budget_value=budget_value,
            entropy_at_time=entropy_at_time,
            adjustment_type=adjustment_type,
            adjustment_reason=adjustment_reason,
        )

    def calculate_budget_adjustment(
        self,
        job_hash: str,
        current_entropy: float,
        floor: float = 0.05,
        ceiling: float = 0.50,
        decay_rate: float = 0.95,
        boost_amount: float = 0.10,
        entropy_threshold: float = 0.3,
        initial_budget: float = 0.15,
    ) -> ExplorationBudgetRecord:
        """Calculate and record the next budget adjustment based on entropy.

        v23 Evolution: Exploration Budget Maintenance - implements the core
        budget adjustment logic:
        - When entropy < threshold: boost budget by boost_amount
        - When entropy >= threshold: decay budget by decay_rate
        - Budget never drops below floor or exceeds ceiling

        Args:
            job_hash: Hash of the job.
            current_entropy: Current pattern entropy (0.0-1.0).
            floor: Minimum budget floor (default 0.05).
            ceiling: Maximum budget ceiling (default 0.50).
            decay_rate: Decay multiplier when entropy healthy (default 0.95).
            boost_amount: Amount to add when entropy low (default 0.10).
            entropy_threshold: Entropy level that triggers boost (default 0.3).
            initial_budget: Starting budget if no history (default 0.15).

        Returns:
            The new ExplorationBudgetRecord after adjustment.
        """
        # Get current budget
        current = self.get_exploration_budget(job_hash)

        if current is None:
            # First budget record - initialize
            return self.update_exploration_budget(
                job_hash=job_hash,
                budget_value=initial_budget,
                adjustment_type="initial",
                entropy_at_time=current_entropy,
                adjustment_reason="Initial budget set",
                floor=floor,
                ceiling=ceiling,
            )

        # Calculate new budget based on entropy
        if current_entropy < entropy_threshold:
            # Low entropy - boost exploration to inject diversity
            new_budget = current.budget_value + boost_amount
            adjustment_type = "boost"
            reason = f"Entropy {current_entropy:.3f} < threshold {entropy_threshold:.3f}"
        else:
            # Healthy entropy - decay toward floor
            new_budget = current.budget_value * decay_rate
            adjustment_type = "decay"
            reason = f"Entropy {current_entropy:.3f} >= threshold, decaying"

        return self.update_exploration_budget(
            job_hash=job_hash,
            budget_value=new_budget,
            adjustment_type=adjustment_type,
            entropy_at_time=current_entropy,
            adjustment_reason=reason,
            floor=floor,
            ceiling=ceiling,
        )

    def get_exploration_budget_statistics(
        self,
        job_hash: str | None = None,
    ) -> dict[str, Any]:
        """Get statistics about exploration budget usage.

        v23 Evolution: Exploration Budget Maintenance - provides aggregate
        statistics for monitoring and reporting.

        Args:
            job_hash: Optional job hash to filter by specific job.

        Returns:
            Dict with budget statistics:
            - current_budget: Current budget value
            - avg_budget: Average budget over history
            - min_budget: Minimum recorded budget
            - max_budget: Maximum recorded budget
            - total_adjustments: Total number of adjustments
            - floor_enforcements: Number of times floor was enforced
            - boost_count: Number of boost adjustments
            - decay_count: Number of decay adjustments
        """
        where, params = self._where_job_hash(job_hash)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    COUNT(*) as total,
                    AVG(budget_value) as avg_val,
                    MIN(budget_value) as min_val,
                    MAX(budget_value) as max_val,
                    SUM(CASE WHEN adjustment_type = 'floor_enforced'
                        THEN 1 ELSE 0 END) as floor_count,
                    SUM(CASE WHEN adjustment_type = 'boost'
                        THEN 1 ELSE 0 END) as boost_count,
                    SUM(CASE WHEN adjustment_type = 'decay'
                        THEN 1 ELSE 0 END) as decay_count
                FROM exploration_budget
                {where}
                """,
                params,
            )

            row = cursor.fetchone()

            if not row or row["total"] == 0:
                return {
                    "current_budget": None,
                    "avg_budget": 0.0,
                    "min_budget": 0.0,
                    "max_budget": 0.0,
                    "total_adjustments": 0,
                    "floor_enforcements": 0,
                    "boost_count": 0,
                    "decay_count": 0,
                }

        # Get current budget separately
        current = self.get_exploration_budget(job_hash)

        return {
            "current_budget": current.budget_value if current else None,
            "avg_budget": row["avg_val"] or 0.0,
            "min_budget": row["min_val"] or 0.0,
            "max_budget": row["max_val"] or 0.0,
            "total_adjustments": row["total"],
            "floor_enforcements": row["floor_count"] or 0,
            "boost_count": row["boost_count"] or 0,
            "decay_count": row["decay_count"] or 0,
        }

    # =========================================================================
    # v23 Evolution: Automatic Entropy Response
    # =========================================================================

    def check_entropy_response_needed(
        self,
        job_hash: str,
        entropy_threshold: float = 0.3,
        cooldown_seconds: int = 3600,
    ) -> tuple[bool, float | None, str]:
        """Check if an entropy response is needed based on current conditions.

        v23 Evolution: Automatic Entropy Response - evaluates whether the
        current entropy level warrants a diversity injection response.

        Args:
            job_hash: Hash of the job to check.
            entropy_threshold: Entropy below this triggers response.
            cooldown_seconds: Minimum seconds since last response.

        Returns:
            Tuple of (needs_response, current_entropy, reason):
            - needs_response: True if response should be triggered
            - current_entropy: Current diversity_index or None if not calculable
            - reason: Human-readable explanation of decision
        """
        # Check cooldown - has there been a recent response?
        last_response = self.get_last_entropy_response(job_hash)
        if last_response:
            seconds_since = (datetime.now() - last_response.recorded_at).total_seconds()
            if seconds_since < cooldown_seconds:
                remaining = cooldown_seconds - seconds_since
                return (
                    False,
                    None,
                    f"Cooldown active ({remaining:.0f}s remaining)",
                )

        # Calculate current entropy directly
        # Get patterns and calculate entropy
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.id, COUNT(pa.id) as app_count
                FROM patterns p
                LEFT JOIN pattern_applications pa ON p.id = pa.pattern_id
                GROUP BY p.id
                HAVING app_count > 0
                """
            )
            rows = cursor.fetchall()

        if not rows:
            return (False, None, "No pattern applications to analyze")

        total_apps = sum(row["app_count"] for row in rows)
        if total_apps == 0:
            return (False, None, "No pattern applications to analyze")

        # Calculate diversity index using Shannon entropy
        probabilities = [row["app_count"] / total_apps for row in rows]
        shannon_entropy = -sum(
            p * math.log2(p) for p in probabilities if p > 0
        )
        max_entropy = math.log2(len(rows)) if len(rows) > 1 else 1.0
        diversity_index = shannon_entropy / max_entropy if max_entropy > 0 else 0.0

        # Check if response is needed
        if diversity_index < entropy_threshold:
            return (
                True,
                diversity_index,
                f"Entropy {diversity_index:.3f} < threshold {entropy_threshold:.3f}",
            )

        return (
            False,
            diversity_index,
            f"Entropy {diversity_index:.3f} >= threshold {entropy_threshold:.3f} (healthy)",
        )

    def trigger_entropy_response(
        self,
        job_hash: str = "",
        entropy_at_trigger: float = 0.0,
        threshold_used: float = 0.0,
        *,
        trigger: EntropyTriggerContext | None = None,
        config: EntropyResponseConfig | None = None,
        boost_budget: bool | None = None,
        revisit_quarantine: bool | None = None,
        max_quarantine_revisits: int | None = None,
        budget_floor: float | None = None,
        budget_ceiling: float | None = None,
        budget_boost_amount: float | None = None,
    ) -> EntropyResponseRecord:
        """Execute an entropy response by boosting budget and/or revisiting quarantine.

        v23 Evolution: Automatic Entropy Response - performs the actual response
        actions when entropy has dropped below threshold.

        Supports two calling conventions:
            1. **Positional** (legacy):
               ``trigger_entropy_response(job_hash, entropy, threshold, ...)``
            2. **Bundled** (preferred): ``trigger_entropy_response(trigger=ctx, config=cfg)``

        When *trigger* is supplied, its fields take precedence over
        positional arguments.

        Args:
            job_hash: Hash of the job triggering response (legacy positional).
            entropy_at_trigger: Entropy value that triggered this response (legacy positional).
            threshold_used: The threshold that was crossed (legacy positional).
            trigger: Bundled trigger context (preferred over positional args).
            config: Configuration object grouping all response tuning params.
                Individual keyword arguments override config values when both
                are provided.
            boost_budget: Whether to boost exploration budget.
            revisit_quarantine: Whether to revisit quarantined patterns.
            max_quarantine_revisits: Maximum patterns to revisit.
            budget_floor: Floor for budget enforcement.
            budget_ceiling: Ceiling for budget enforcement.
            budget_boost_amount: Amount to boost budget by.

        Returns:
            The EntropyResponseRecord documenting the response.
        """
        # Resolve trigger context: prefer bundled object, fall back to positional args
        if trigger is not None:
            job_hash = trigger.job_hash
            entropy_at_trigger = trigger.entropy_at_trigger
            threshold_used = trigger.threshold_used

        # Build effective config: start from config (or defaults), then apply
        # any explicit keyword overrides without mutating the caller's object.
        overrides: dict[str, Any] = {
            k: v for k, v in {
                "boost_budget": boost_budget,
                "revisit_quarantine": revisit_quarantine,
                "max_quarantine_revisits": max_quarantine_revisits,
                "budget_floor": budget_floor,
                "budget_ceiling": budget_ceiling,
                "budget_boost_amount": budget_boost_amount,
            }.items() if v is not None
        }
        cfg = replace(config, **overrides) if config else EntropyResponseConfig(**overrides)

        actions_taken: list[str] = []
        patterns_revisited: list[str] = []
        budget_boosted = False
        quarantine_revisit_count = 0

        # Action 1: Boost exploration budget
        if cfg.boost_budget:
            current = self.get_exploration_budget(job_hash)
            if current:
                new_budget = current.budget_value + cfg.budget_boost_amount
            else:
                new_budget = 0.15 + cfg.budget_boost_amount  # Initial + boost

            self.update_exploration_budget(
                job_hash=job_hash,
                budget_value=new_budget,
                adjustment_type="boost",
                entropy_at_time=entropy_at_trigger,
                adjustment_reason=(
                    f"Entropy response: diversity"
                    f" {entropy_at_trigger:.3f} < {threshold_used:.3f}"
                ),
                floor=cfg.budget_floor,
                ceiling=cfg.budget_ceiling,
            )
            budget_boosted = True
            actions_taken.append("budget_boost")
            _logger.info("entropy_budget_boost", boost_amount=round(cfg.budget_boost_amount, 4))

        # Action 2: Revisit quarantined patterns
        if cfg.revisit_quarantine:
            # Get quarantined patterns and mark for review
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT id, pattern_name
                    FROM patterns
                    WHERE quarantine_status = 'quarantined'
                    ORDER BY last_seen DESC
                    LIMIT ?
                    """,
                    (cfg.max_quarantine_revisits,),
                )
                quarantined = cursor.fetchall()

                for row in quarantined:
                    # Mark for review by setting status to PENDING
                    conn.execute(
                        """
                        UPDATE patterns
                        SET quarantine_status = 'pending',
                            quarantine_reason = 'Entropy response: revisiting for revalidation'
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )
                    patterns_revisited.append(row["id"])
                    quarantine_revisit_count += 1
                    _logger.info(
                        f"Entropy response: Revisiting quarantined pattern {row['pattern_name']}"
                    )

            if quarantine_revisit_count > 0:
                actions_taken.append("quarantine_revisit")

        # Record the response
        record_id = str(uuid.uuid4())
        recorded_at = datetime.now()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO entropy_responses (
                    id, job_hash, recorded_at, entropy_at_trigger,
                    threshold_used, actions_taken, budget_boosted,
                    quarantine_revisits, patterns_revisited
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    job_hash,
                    recorded_at.isoformat(),
                    entropy_at_trigger,
                    threshold_used,
                    json.dumps(actions_taken),
                    1 if budget_boosted else 0,
                    quarantine_revisit_count,
                    json.dumps(patterns_revisited),
                ),
            )

        _logger.info(
            f"Entropy response complete: {len(actions_taken)} actions, "
            f"budget_boosted={budget_boosted}, revisited={quarantine_revisit_count}"
        )

        return EntropyResponseRecord(
            id=record_id,
            job_hash=job_hash,
            recorded_at=recorded_at,
            entropy_at_trigger=entropy_at_trigger,
            threshold_used=threshold_used,
            actions_taken=actions_taken,
            budget_boosted=budget_boosted,
            quarantine_revisits=quarantine_revisit_count,
            patterns_revisited=patterns_revisited,
        )

    def get_last_entropy_response(
        self,
        job_hash: str | None = None,
    ) -> EntropyResponseRecord | None:
        """Get the most recent entropy response record.

        v23 Evolution: Automatic Entropy Response - used for cooldown checking.

        Args:
            job_hash: Optional job hash to filter by.

        Returns:
            The most recent EntropyResponseRecord, or None if none found.
        """
        where, params = self._where_job_hash(job_hash)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT id, job_hash, recorded_at, entropy_at_trigger,
                       threshold_used, actions_taken, budget_boosted,
                       quarantine_revisits, patterns_revisited
                FROM entropy_responses
                {where}
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                params,
            )

            row = cursor.fetchone()

            if not row:
                return None

            return EntropyResponseRecord(
                id=row["id"],
                job_hash=row["job_hash"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
                entropy_at_trigger=row["entropy_at_trigger"],
                threshold_used=row["threshold_used"],
                actions_taken=json.loads(row["actions_taken"]),
                budget_boosted=bool(row["budget_boosted"]),
                quarantine_revisits=row["quarantine_revisits"],
                patterns_revisited=(
                    json.loads(row["patterns_revisited"])
                    if row["patterns_revisited"] else []
                ),
            )

    def get_entropy_response_history(
        self,
        job_hash: str | None = None,
        limit: int = 50,
    ) -> list[EntropyResponseRecord]:
        """Get entropy response history for analysis.

        v23 Evolution: Automatic Entropy Response - returns historical
        response records for visualization and trend analysis.

        Args:
            job_hash: Optional job hash to filter by.
            limit: Maximum number of records to return.

        Returns:
            List of EntropyResponseRecord objects, most recent first.
        """
        where, params = self._where_job_hash(job_hash)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT id, job_hash, recorded_at, entropy_at_trigger,
                       threshold_used, actions_taken, budget_boosted,
                       quarantine_revisits, patterns_revisited
                FROM entropy_responses
                {where}
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (*params, limit),
            )

            return [
                EntropyResponseRecord(
                    id=row["id"],
                    job_hash=row["job_hash"],
                    recorded_at=datetime.fromisoformat(row["recorded_at"]),
                    entropy_at_trigger=row["entropy_at_trigger"],
                    threshold_used=row["threshold_used"],
                    actions_taken=json.loads(row["actions_taken"]),
                    budget_boosted=bool(row["budget_boosted"]),
                    quarantine_revisits=row["quarantine_revisits"],
                    patterns_revisited=(
                        json.loads(row["patterns_revisited"])
                        if row["patterns_revisited"] else []
                    ),
                )
                for row in cursor.fetchall()
            ]

    def get_entropy_response_statistics(
        self,
        job_hash: str | None = None,
    ) -> dict[str, Any]:
        """Get statistics about entropy responses.

        v23 Evolution: Automatic Entropy Response - provides aggregate
        statistics for monitoring and reporting.

        Args:
            job_hash: Optional job hash to filter by.

        Returns:
            Dict with response statistics.
        """
        where, params = self._where_job_hash(job_hash)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    COUNT(*) as total,
                    AVG(entropy_at_trigger) as avg_entropy,
                    SUM(budget_boosted) as budget_boosts,
                    SUM(quarantine_revisits) as total_revisits
                FROM entropy_responses
                {where}
                """,
                params,
            )

            row = cursor.fetchone()

            if not row or row["total"] == 0:
                return {
                    "total_responses": 0,
                    "avg_entropy_at_trigger": 0.0,
                    "budget_boosts": 0,
                    "quarantine_revisits": 0,
                    "last_response": None,
                }

        # Get last response time
        last = self.get_last_entropy_response(job_hash)

        return {
            "total_responses": row["total"],
            "avg_entropy_at_trigger": row["avg_entropy"] or 0.0,
            "budget_boosts": row["budget_boosts"] or 0,
            "quarantine_revisits": row["total_revisits"] or 0,
            "last_response": last.recorded_at.isoformat() if last else None,
        }

    # =========================================================================
    # Pattern Entropy Monitoring (used by `mzt patterns-entropy` CLI)
    # =========================================================================

    def calculate_pattern_entropy(self) -> PatternEntropyMetrics:
        """Calculate current Shannon entropy of the pattern population.

        Queries all patterns with at least one application and computes
        Shannon entropy over the application-count distribution. This
        reuses the same algorithm as ``check_entropy_response_needed``
        but returns a structured result for CLI display and recording.

        Returns:
            PatternEntropyMetrics with the current entropy snapshot.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.id, COUNT(pa.id) as app_count
                FROM patterns p
                LEFT JOIN pattern_applications pa ON p.id = pa.pattern_id
                GROUP BY p.id
                """
            )
            rows = cursor.fetchall()

        # Count totals
        unique_count = len(rows)
        effective_rows = [r for r in rows if r["app_count"] > 0]
        effective_count = len(effective_rows)
        total_apps = sum(r["app_count"] for r in rows)

        now = datetime.now()

        if total_apps == 0 or effective_count == 0:
            return PatternEntropyMetrics(
                calculated_at=now,
                shannon_entropy=0.0,
                max_possible_entropy=0.0,
                diversity_index=0.0,
                unique_pattern_count=unique_count,
                effective_pattern_count=0,
                total_applications=0,
                dominant_pattern_share=0.0,
            )

        # Shannon entropy: H = -sum(p_i * log2(p_i))
        probabilities = [r["app_count"] / total_apps for r in effective_rows]
        shannon_entropy = -sum(
            p * math.log2(p) for p in probabilities if p > 0
        )
        max_entropy = math.log2(effective_count) if effective_count > 1 else 1.0
        diversity_index = shannon_entropy / max_entropy if max_entropy > 0 else 0.0
        dominant_share = max(probabilities)

        return PatternEntropyMetrics(
            calculated_at=now,
            shannon_entropy=shannon_entropy,
            max_possible_entropy=max_entropy,
            diversity_index=diversity_index,
            unique_pattern_count=unique_count,
            effective_pattern_count=effective_count,
            total_applications=total_apps,
            dominant_pattern_share=dominant_share,
        )

    def record_pattern_entropy(self, metrics: PatternEntropyMetrics) -> str:
        """Persist a pattern entropy snapshot for historical trend analysis.

        Args:
            metrics: The entropy metrics to record.

        Returns:
            The record ID of the persisted snapshot.
        """
        record_id = str(uuid.uuid4())

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO pattern_entropy_history (
                    id, calculated_at, shannon_entropy, max_possible_entropy,
                    diversity_index, unique_pattern_count, effective_pattern_count,
                    total_applications, dominant_pattern_share, threshold_exceeded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    metrics.calculated_at.isoformat(),
                    metrics.shannon_entropy,
                    metrics.max_possible_entropy,
                    metrics.diversity_index,
                    metrics.unique_pattern_count,
                    metrics.effective_pattern_count,
                    metrics.total_applications,
                    metrics.dominant_pattern_share,
                    1 if metrics.threshold_exceeded else 0,
                ),
            )

        _logger.debug(
            f"Recorded entropy snapshot {record_id[:10]}: "
            f"H={metrics.shannon_entropy:.3f}, diversity={metrics.diversity_index:.3f}"
        )
        return record_id

    def get_pattern_entropy_history(
        self,
        limit: int = 50,
    ) -> list[PatternEntropyMetrics]:
        """Retrieve historical entropy snapshots for trend analysis.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of PatternEntropyMetrics, most recent first.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT calculated_at, shannon_entropy, max_possible_entropy,
                       diversity_index, unique_pattern_count, effective_pattern_count,
                       total_applications, dominant_pattern_share, threshold_exceeded
                FROM pattern_entropy_history
                ORDER BY calculated_at DESC
                LIMIT ?
                """,
                (limit,),
            )

            records: list[PatternEntropyMetrics] = []
            for row in cursor.fetchall():
                records.append(
                    PatternEntropyMetrics(
                        calculated_at=datetime.fromisoformat(row["calculated_at"]),
                        shannon_entropy=row["shannon_entropy"],
                        max_possible_entropy=row["max_possible_entropy"],
                        diversity_index=row["diversity_index"],
                        unique_pattern_count=row["unique_pattern_count"],
                        effective_pattern_count=row["effective_pattern_count"],
                        total_applications=row["total_applications"],
                        dominant_pattern_share=row["dominant_pattern_share"],
                        threshold_exceeded=bool(row["threshold_exceeded"]),
                    )
                )
            return records
