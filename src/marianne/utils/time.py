"""Time utilities for Marianne.

Provides timezone-aware datetime functions to replace deprecated datetime methods.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime.

    This is the recommended replacement for the deprecated datetime.utcnow().
    Returns a timezone-aware datetime object with UTC timezone.

    Returns:
        datetime: Current UTC time with tzinfo=UTC
    """
    return datetime.now(UTC)
