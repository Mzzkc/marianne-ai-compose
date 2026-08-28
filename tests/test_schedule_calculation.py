"""Tests for pure recurring-schedule time calculation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from marianne.core.config import ScheduleConfig
from marianne.core.scheduling import next_due_at, parse_interval_seconds


def test_next_due_at_calculates_utc_cron_tick() -> None:
    """Cron schedules advance from an aware UTC instant to an aware UTC result."""
    after = datetime(2026, 1, 1, 8, 30, tzinfo=UTC)

    due_at = next_due_at(ScheduleConfig(cron="0 9 * * *"), after)

    assert due_at == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def test_next_due_at_honors_melbourne_dst_with_aware_base() -> None:
    """Croniter receives local aware time, preserving Melbourne's DST offset."""
    after = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    config = ScheduleConfig(cron="30 3 * * *", timezone="Australia/Melbourne")

    due_at = next_due_at(config, after)

    assert due_at == datetime(2026, 10, 3, 16, 30, tzinfo=UTC)


def test_interval_schedule_advances_from_previous_scheduled_start() -> None:
    """Intervals remain anchored to scheduled starts rather than dispatch delay."""
    anchor = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    after = datetime(2026, 1, 1, 10, 31, tzinfo=UTC)
    config = ScheduleConfig(interval="15m")

    due_at = next_due_at(config, after, interval_anchor=anchor)

    assert due_at == datetime(2026, 1, 1, 10, 45, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("0.5s", 0.5), ("2m", 120.0), ("1.25h", 4500.0), ("1d", 86400.0)],
)
def test_parse_interval_seconds_supports_documented_units(value: str, seconds: float) -> None:
    """Interval parsing produces seconds for every documented unit."""
    assert parse_interval_seconds(value) == seconds


@pytest.mark.parametrize("value", ["1w", "-1m", " 1s", "1s ", "0s", "1"])
def test_parse_interval_seconds_rejects_invalid_units_and_values(value: str) -> None:
    """Unsupported units, whitespace, and non-positive values fail fast."""
    with pytest.raises(ValueError, match="positive"):
        parse_interval_seconds(value)


def test_next_due_at_rejects_naive_after_datetime() -> None:
    """Recurrence calculation requires a timezone-aware input instant."""
    with pytest.raises(ValueError, match="timezone-aware"):
        next_due_at(ScheduleConfig(interval="1h"), datetime(2026, 1, 1, 10, 0))


def test_interval_next_time_is_strictly_monotonic() -> None:
    """The next interval tick is always later than the supplied instant."""
    anchor = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    config = ScheduleConfig(interval="15m")
    first_due_at = next_due_at(config, anchor, interval_anchor=anchor)

    second_due_at = next_due_at(config, first_due_at, interval_anchor=anchor)

    assert first_due_at > anchor
    assert second_due_at > first_due_at
    assert second_due_at == datetime(2026, 1, 1, 10, 30, tzinfo=UTC)
