"""Pattern discovery broadcasting mixin for GlobalLearningStore.

Provides real-time pattern broadcasting for cross-job learning:
- record_pattern_discovery: Broadcast a new pattern to other jobs
- check_recent_pattern_discoveries: Poll for patterns from other jobs
- cleanup_expired_pattern_discoveries: Maintenance cleanup
- get_active_pattern_discoveries: Get all unexpired discoveries

v14 Evolution: Real-time Pattern Broadcasting
"""

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from marianne.core.logging import MarianneLogger
from marianne.learning.store.base import GlobalLearningStoreBase, _logger
from marianne.learning.store.models import PatternDiscoveryEvent

if TYPE_CHECKING:
    from marianne.learning.store.patterns_query import PatternQueryProtocol

    _BroadcastBase = PatternQueryProtocol
else:
    _BroadcastBase = object


class PatternBroadcastMixin(_BroadcastBase):
    """Mixin providing pattern discovery broadcasting methods.

    This mixin requires that the composed class provides:
    - _get_connection(): Context manager yielding sqlite3.Connection
    - _row_to_discovery_event(): For row conversion (from PatternQueryMixin)
    """

    _logger: MarianneLogger
    _get_connection: Callable[[], AbstractContextManager[sqlite3.Connection]]

    def record_pattern_discovery(
        self,
        pattern_id: str,
        pattern_name: str,
        pattern_type: str,
        job_id: str,
        effectiveness_score: float = 1.0,
        context_tags: list[str] | None = None,
        ttl_seconds: float = 300.0,
    ) -> str:
        """Record a pattern discovery for cross-job broadcasting.

        When a job discovers a new pattern, it broadcasts the discovery so
        other concurrent jobs can benefit immediately.

        Args:
            pattern_id: ID of the discovered pattern.
            pattern_name: Human-readable name of the pattern.
            pattern_type: Type of pattern (validation_failure, etc.).
            job_id: ID of the job that discovered the pattern.
            effectiveness_score: Initial effectiveness score (0.0-1.0).
            context_tags: Optional context tags for pattern matching.
            ttl_seconds: Time-to-live in seconds (default 5 minutes).

        Returns:
            The discovery event record ID.
        """
        record_id = str(uuid.uuid4())
        now = datetime.now()
        job_hash = GlobalLearningStoreBase.hash_job(job_id)
        expires_at = now + timedelta(seconds=ttl_seconds)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO pattern_discovery_events (
                    id, pattern_id, pattern_name, pattern_type,
                    source_job_hash, recorded_at, expires_at,
                    effectiveness_score, context_tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    pattern_id,
                    pattern_name,
                    pattern_type,
                    job_hash,
                    now.isoformat(),
                    expires_at.isoformat(),
                    effectiveness_score,
                    json.dumps(context_tags or []),
                ),
            )

        _logger.info(
            f"Broadcast pattern discovery '{pattern_name}' (type: {pattern_type}), "
            f"expires in {ttl_seconds:.0f}s"
        )
        return record_id

    def check_recent_pattern_discoveries(
        self,
        exclude_job_id: str | None = None,
        pattern_type: str | None = None,
        min_effectiveness: float = 0.0,
        limit: int = 20,
    ) -> list[PatternDiscoveryEvent]:
        """Check for recent pattern discoveries from other jobs.

        Args:
            exclude_job_id: Optional job ID to exclude (typically self).
            pattern_type: Optional filter by pattern type.
            min_effectiveness: Minimum effectiveness score to include.
            limit: Maximum number of discoveries to return.

        Returns:
            List of PatternDiscoveryEvent objects from other jobs.
        """
        now = datetime.now()
        exclude_hash = GlobalLearningStoreBase.hash_job(exclude_job_id) if exclude_job_id else None

        with self._get_connection() as conn:
            query = """
                SELECT * FROM pattern_discovery_events
                WHERE expires_at > ?
                AND effectiveness_score >= ?
            """
            params: list[str | float] = [now.isoformat(), min_effectiveness]

            if exclude_hash is not None:
                query += " AND source_job_hash != ?"
                params.append(exclude_hash)

            if pattern_type is not None:
                query += " AND pattern_type = ?"
                params.append(pattern_type)

            query += " ORDER BY recorded_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [self._row_to_discovery_event(row) for row in cursor.fetchall()]

    def cleanup_expired_pattern_discoveries(self) -> int:
        """Remove expired pattern discovery events.

        Returns:
            Number of expired events removed.
        """
        now = datetime.now()

        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM pattern_discovery_events WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            deleted = cursor.rowcount

        if deleted > 0:
            _logger.debug("pattern_discovery_events_cleaned", count=deleted)

        return deleted

    def get_active_pattern_discoveries(
        self,
        pattern_type: str | None = None,
    ) -> list[PatternDiscoveryEvent]:
        """Get all active (unexpired) pattern discovery events.

        Args:
            pattern_type: Optional filter by pattern type.

        Returns:
            List of PatternDiscoveryEvent objects that haven't expired yet.
        """
        now = datetime.now()

        with self._get_connection() as conn:
            if pattern_type:
                cursor = conn.execute(
                    """
                    SELECT * FROM pattern_discovery_events
                    WHERE expires_at > ? AND pattern_type = ?
                    ORDER BY recorded_at DESC
                    """,
                    (now.isoformat(), pattern_type),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM pattern_discovery_events
                    WHERE expires_at > ?
                    ORDER BY recorded_at DESC
                    """,
                    (now.isoformat(),),
                )

            return [self._row_to_discovery_event(row) for row in cursor.fetchall()]
