"""Escalation mixin for the global learning store.

This module contains the EscalationMixin class that provides escalation
decision recording and learning functionality. When sheets trigger escalation
and receive responses from human or AI handlers, this module records the
decisions for future pattern-based suggestions.

Evolution v11: Escalation Learning Loop - closes the loop between escalation
handlers and learning system, enabling pattern-based suggestions for similar
escalation contexts.

Extracted from global_store.py as part of the modularization effort.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime

from marianne.core.logging import MarianneLogger, get_logger

from .base import WhereBuilder
from .models import EscalationDecisionRecord

_logger = get_logger("learning.global_store")


class EscalationMixin:
    """Mixin providing escalation decision functionality.

    This mixin provides methods for recording and querying escalation decisions.
    When a sheet triggers escalation and receives a response, the decision is
    recorded so Marianne can learn from it and potentially suggest similar actions
    for future escalations with similar contexts.

    Requires the following from the composed class:
        - _get_connection() -> context manager yielding sqlite3.Connection
        - hash_job(job_id: str) -> str (static method)
    """

    # Annotations for attributes provided by the composed class (GlobalLearningStoreBase)
    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]
    hash_job: Callable[..., str]  # GlobalLearningStoreBase.hash_job(job_name, config_hash)

    def record_escalation_decision(
        self,
        job_id: str,
        sheet_num: int,
        confidence: float,
        action: str,
        validation_pass_rate: float,
        retry_count: int,
        guidance: str | None = None,
        outcome_after_action: str | None = None,
        model: str | None = None,
    ) -> str:
        """Record an escalation decision for learning.

        When a sheet triggers escalation and receives a response from
        a human or AI handler, this method records the decision so that
        Marianne can learn from it and potentially suggest similar actions
        for future escalations with similar contexts.

        Evolution v11: Escalation Learning Loop - closes the loop between
        escalation handlers and learning system.

        Args:
            job_id: ID of the job that triggered escalation.
            sheet_num: Sheet number that triggered escalation.
            confidence: Aggregate confidence score at escalation time (0.0-1.0).
            action: Action taken (retry, skip, abort, modify_prompt).
            validation_pass_rate: Pass percentage at escalation time.
            retry_count: Number of retries before escalation.
            guidance: Optional guidance/notes from the handler.
            outcome_after_action: What happened after (success, failed, etc.).
            model: Optional model name used for execution.

        Returns:
            The escalation decision record ID.
        """
        record_id = str(uuid.uuid4())
        job_hash = self.hash_job(job_id)
        now = datetime.now()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO escalation_decisions (
                    id, job_hash, sheet_num, confidence, action,
                    guidance, validation_pass_rate, retry_count,
                    outcome_after_action, recorded_at, model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    job_hash,
                    sheet_num,
                    confidence,
                    action,
                    guidance,
                    validation_pass_rate,
                    retry_count,
                    outcome_after_action,
                    now.isoformat(),
                    model,
                ),
            )

        _logger.info(
            f"Recorded escalation decision {record_id}: sheet={sheet_num}, "
            f"action={action}, confidence={confidence:.1%}"
        )
        return record_id

    def get_escalation_history(
        self,
        job_id: str | None = None,
        action: str | None = None,
        limit: int = 20,
    ) -> list[EscalationDecisionRecord]:
        """Get historical escalation decisions.

        Retrieves past escalation decisions for analysis or display.
        Can filter by job or action type.

        Args:
            job_id: Optional job ID to filter by.
            action: Optional action type to filter by.
            limit: Maximum number of records to return.

        Returns:
            List of EscalationDecisionRecord objects.
        """
        with self._get_connection() as conn:
            wb = WhereBuilder()
            if job_id is not None:
                wb.add("job_hash = ?", self.hash_job(job_id))
            if action is not None:
                wb.add("action = ?", action)

            where_sql, params = wb.build()
            cursor = conn.execute(
                f"""
                SELECT * FROM escalation_decisions
                WHERE {where_sql}
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (*params, limit),
            )

            records = []
            for row in cursor.fetchall():
                records.append(
                    EscalationDecisionRecord(
                        id=row["id"],
                        job_hash=row["job_hash"],
                        sheet_num=row["sheet_num"],
                        confidence=row["confidence"],
                        action=row["action"],
                        guidance=row["guidance"],
                        validation_pass_rate=row["validation_pass_rate"],
                        retry_count=row["retry_count"],
                        outcome_after_action=row["outcome_after_action"],
                        recorded_at=datetime.fromisoformat(row["recorded_at"]),
                        model=row["model"],
                    )
                )

            return records

    def get_similar_escalation(
        self,
        confidence: float,
        validation_pass_rate: float,
        confidence_tolerance: float = 0.15,
        pass_rate_tolerance: float = 15.0,
        limit: int = 5,
    ) -> list[EscalationDecisionRecord]:
        """Get similar past escalation decisions for guidance.

        Finds historical escalations with similar context (confidence and
        pass rate) to help inform the current escalation decision. Can be
        used to suggest actions or provide guidance to human operators.

        Evolution v11: Escalation Learning Loop - enables pattern-based
        suggestions for similar escalation contexts.

        Args:
            confidence: Current confidence level (0.0-1.0).
            validation_pass_rate: Current validation pass percentage.
            confidence_tolerance: How much confidence can differ (default 0.15).
            pass_rate_tolerance: How much pass rate can differ (default 15%).
            limit: Maximum number of similar records to return.

        Returns:
            List of EscalationDecisionRecord from similar past escalations,
            ordered by outcome success (successful outcomes first).
        """
        with self._get_connection() as conn:
            # Find escalations with similar confidence and pass rate
            # Order by: successful outcomes first, then by how close the match is
            cursor = conn.execute(
                """
                SELECT *,
                       ABS(confidence - ?) as conf_diff,
                       ABS(validation_pass_rate - ?) as rate_diff
                FROM escalation_decisions
                WHERE ABS(confidence - ?) <= ?
                  AND ABS(validation_pass_rate - ?) <= ?
                ORDER BY
                    CASE WHEN outcome_after_action = 'success' THEN 0
                         WHEN outcome_after_action = 'skipped' THEN 1
                         WHEN outcome_after_action IS NULL THEN 2
                         ELSE 3 END,
                    conf_diff + (rate_diff / 100.0)
                LIMIT ?
                """,
                (
                    confidence,
                    validation_pass_rate,
                    confidence,
                    confidence_tolerance,
                    validation_pass_rate,
                    pass_rate_tolerance,
                    limit,
                ),
            )

            records = []
            for row in cursor.fetchall():
                records.append(
                    EscalationDecisionRecord(
                        id=row["id"],
                        job_hash=row["job_hash"],
                        sheet_num=row["sheet_num"],
                        confidence=row["confidence"],
                        action=row["action"],
                        guidance=row["guidance"],
                        validation_pass_rate=row["validation_pass_rate"],
                        retry_count=row["retry_count"],
                        outcome_after_action=row["outcome_after_action"],
                        recorded_at=datetime.fromisoformat(row["recorded_at"]),
                        model=row["model"],
                    )
                )

            return records

    def update_escalation_outcome(
        self,
        escalation_id: str,
        outcome_after_action: str,
    ) -> bool:
        """Update the outcome of an escalation decision.

        Called after an escalation action is taken and the result is known.
        This closes the feedback loop by recording whether the action led
        to success or failure.

        Args:
            escalation_id: The escalation record ID to update.
            outcome_after_action: What happened (success, failed, aborted, skipped).

        Returns:
            True if the record was updated, False if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE escalation_decisions
                SET outcome_after_action = ?
                WHERE id = ?
                """,
                (outcome_after_action, escalation_id),
            )
            updated = cursor.rowcount > 0

        if updated:
            _logger.debug(
                f"Updated escalation {escalation_id} outcome: {outcome_after_action}"
            )

        return updated
