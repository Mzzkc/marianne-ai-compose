"""Rate limit event mixin for the global learning store.

This module contains the RateLimitMixin class that provides rate limit
event recording and querying functionality. Rate limits enable cross-workspace
coordination so that parallel jobs can avoid hitting the same API limits.

Evolution #8: Cross-Workspace Circuit Breaker - enables jobs running in
different workspaces to share rate limit awareness.

Extracted from global_store.py as part of the modularization effort.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime

from marianne.core.logging import MarianneLogger, get_logger

from .models import RateLimitEvent

_logger = get_logger("learning.global_store")


class RateLimitMixin:
    """Mixin providing rate limit event functionality.

    This mixin provides methods for recording and querying rate limit events
    across workspaces. When one job hits a rate limit, it records the event
    so that other parallel jobs can check and avoid hitting the same limit.

    Requires the following from the composed class:
        - _get_connection() -> context manager yielding sqlite3.Connection
        - hash_job(job_id: str) -> str (static method)
    """

    # Annotations for attributes provided by the composed class (GlobalLearningStoreBase)
    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]
    hash_job: Callable[..., str]  # GlobalLearningStoreBase.hash_job(job_name, config_hash)

    def record_rate_limit_event(
        self,
        error_code: str,
        duration_seconds: float,
        job_id: str,
        model: str | None = None,
    ) -> str:
        """Record a rate limit event for cross-workspace coordination.

        When one job hits a rate limit, it records the event so that other
        parallel jobs can check and avoid hitting the same limit.

        Evolution #8: Cross-Workspace Circuit Breaker - enables jobs running
        in different workspaces to share rate limit awareness.

        Args:
            error_code: The error code (e.g., 'E101', 'E102').
            duration_seconds: Expected rate limit duration in seconds.
            job_id: ID of the job that encountered the rate limit.
            model: Optional model name that triggered the limit.

        Returns:
            The rate limit event record ID.
        """
        from datetime import timedelta

        record_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        job_hash = self.hash_job(job_id)

        # Use 80% of expected duration as expiry (conservative TTL)
        # This avoids waiting too long while still being safe
        expires_at = now + timedelta(seconds=duration_seconds * 0.8)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO rate_limit_events (
                    id, error_code, model, recorded_at, expires_at,
                    source_job_hash, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    error_code,
                    model,
                    now.isoformat(),
                    expires_at.isoformat(),
                    job_hash,
                    duration_seconds,
                ),
            )

        _logger.info(
            f"Recorded rate limit event {record_id}: {error_code} "
            f"expires in {duration_seconds * 0.8:.0f}s"
        )
        return record_id

    def is_rate_limited(
        self,
        error_code: str | None = None,
        model: str | None = None,
    ) -> tuple[bool, float | None]:
        """Check if there's an active rate limit from another job.

        Queries the rate_limit_events table to see if any unexpired
        rate limit events exist that would affect this job.

        Evolution #8: Cross-Workspace Circuit Breaker - allows jobs to
        check if another job has already hit a rate limit.

        Args:
            error_code: Optional error code to filter by. If None, checks any.
            model: Optional model to filter by. If None, checks any.

        Returns:
            Tuple of (is_limited: bool, seconds_until_expiry: float | None).
            If is_limited is True, seconds_until_expiry indicates when it clears.
        """
        now = datetime.now(UTC)

        with self._get_connection() as conn:
            # Build query based on filters
            query = """
                SELECT expires_at, error_code, model, duration_seconds
                FROM rate_limit_events
                WHERE expires_at > ?
            """
            params: list[str] = [now.isoformat()]

            if error_code is not None:
                query += " AND error_code = ?"
                params.append(error_code)

            if model is not None:
                query += " AND model = ?"
                params.append(model)

            query += " ORDER BY expires_at DESC LIMIT 1"

            cursor = conn.execute(query, params)
            row = cursor.fetchone()

            if row is None:
                return False, None

            # Calculate time until expiry
            expires_at = datetime.fromisoformat(row["expires_at"])
            seconds_until_expiry = (expires_at - now).total_seconds()

            if seconds_until_expiry <= 0:
                return False, None

            _logger.debug(
                f"Rate limit active: {row['error_code']} "
                f"(expires in {seconds_until_expiry:.0f}s)"
            )
            return True, seconds_until_expiry

    def get_active_rate_limits(
        self,
        model: str | None = None,
    ) -> list[RateLimitEvent]:
        """Get all active (unexpired) rate limit events.

        Args:
            model: Optional model to filter by.

        Returns:
            List of RateLimitEvent objects that haven't expired yet.
        """
        now = datetime.now(UTC)

        with self._get_connection() as conn:
            if model:
                cursor = conn.execute(
                    """
                    SELECT * FROM rate_limit_events
                    WHERE expires_at > ? AND model = ?
                    ORDER BY expires_at DESC
                    """,
                    (now.isoformat(), model),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM rate_limit_events
                    WHERE expires_at > ?
                    ORDER BY expires_at DESC
                    """,
                    (now.isoformat(),),
                )

            events = []
            for row in cursor.fetchall():
                events.append(
                    RateLimitEvent(
                        id=row["id"],
                        error_code=row["error_code"],
                        model=row["model"],
                        recorded_at=datetime.fromisoformat(row["recorded_at"]),
                        expires_at=datetime.fromisoformat(row["expires_at"]),
                        source_job_hash=row["source_job_hash"],
                        duration_seconds=row["duration_seconds"],
                    )
                )

            return events

    def cleanup_expired_rate_limits(self) -> int:
        """Remove expired rate limit events from the database.

        This is a housekeeping method that can be called periodically
        to prevent the rate_limit_events table from growing unbounded.

        Returns:
            Number of expired records deleted.
        """
        now = datetime.now(UTC)

        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM rate_limit_events WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            deleted_count = cursor.rowcount

        if deleted_count > 0:
            _logger.debug("rate_limit_events_cleaned", count=deleted_count)

        return deleted_count
