"""Versioned, consent-gated local collection. This module never uses a network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marianne.telemetry.consent import ConsentRecord
from marianne.telemetry.sanitizer import sanitize_outcome, sanitize_score
from marianne.telemetry.spool import TelemetrySpool

if TYPE_CHECKING:
    from marianne.core.config import JobConfig


class TelemetryCollector:
    """Capture sanitized score and live sheet outcomes for later inspection.

    Callers own error isolation: a storage failure must never fail a job.
    Consent is user-global, independent of score-controlled telemetry settings.
    """

    def __init__(self, consent: ConsentRecord | None = None) -> None:
        self.consent = consent if consent is not None else ConsentRecord()

    def collect(
        self, score: JobConfig, status: str, sheets: list[dict[str, Any]],
    ) -> bool:
        """Stage one envelope only when enabled and the current grant is valid."""
        if not score.telemetry.enabled:
            return False
        consent_id = self.consent.consent_id
        if consent_id is None:
            return False
        envelope = {
            "schema_version": 1,
            "collected_at": datetime.now(UTC).isoformat(),
            "consent_id": consent_id,
            "payload": {
                "score": sanitize_score(score),
                "job": {"status": status, "sheet_count": len(sheets)},
                "sheets": [sanitize_outcome(sheet) for sheet in sheets],
            },
        }
        spool = TelemetrySpool(score.telemetry.spool_path, consent_path=self.consent.path)
        with self.consent.collection_transaction():
            return spool.append(
                envelope, authorize=lambda: self.consent.consent_id == consent_id,
            )
