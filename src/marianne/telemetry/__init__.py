"""Opt-in community telemetry: consent and privacy primitives.

Collection and submission are separate from user-global consent. No import in
this package starts a network client or creates a consent record.
See docs/telemetry.md for the data policy, inspection, revocation, and purge.
"""

from marianne.telemetry.collector import TelemetryCollector
from marianne.telemetry.config import TelemetryConfig
from marianne.telemetry.consent import ConsentRecord, ConsentState
from marianne.telemetry.sanitizer import sanitize_outcome, sanitize_score

__all__ = [
    "ConsentRecord",
    "ConsentState",
    "TelemetryConfig",
    "TelemetryCollector",
    "sanitize_outcome",
    "sanitize_score",
]
