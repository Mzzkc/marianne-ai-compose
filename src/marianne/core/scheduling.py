"""Pure recurrence calculation for declared score schedules."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

# croniter does not publish PEP 561 type information.
from croniter import croniter  # type: ignore[import-untyped]

from marianne.core.config.orchestration import ScheduleConfig

_INTERVAL_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?(?P<unit>[smhd])")
_INTERVAL_SECONDS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def parse_interval_seconds(value: str) -> float:
    """Parse a positive, whitespace-free interval declaration into seconds.

    Supported unit suffixes are seconds (``s``), minutes (``m``), hours
    (``h``), and days (``d``). Decimal values are accepted.
    """
    match = _INTERVAL_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Interval must be a positive s/m/h/d duration")

    quantity = float(value[:-1])
    if quantity <= 0:
        raise ValueError("Interval must be a positive s/m/h/d duration")
    return quantity * _INTERVAL_SECONDS[match.group("unit")]


def next_due_at(
    config: ScheduleConfig,
    after: datetime,
    *,
    interval_anchor: datetime | None = None,
) -> datetime:
    """Return the first scheduled UTC instant strictly later than ``after``.

    Cron expressions calculate from an aware datetime in the configured IANA
    zone, preserving that zone's daylight-saving rules. Interval expressions
    advance from the prior scheduled start when supplied, avoiding schedule
    drift caused by delayed dispatch.
    """
    _require_aware(after, name="after")

    if config.cron is not None:
        base = after
        if config.timezone is not None:
            base = after.astimezone(ZoneInfo(config.timezone))
        next_time = cast(datetime, croniter(config.cron, base).get_next(datetime))
        return next_time.astimezone(UTC)

    if config.interval is None:
        raise ValueError("Schedule requires an interval or cron declaration")

    anchor = interval_anchor or after
    _require_aware(anchor, name="interval_anchor")
    duration = timedelta(seconds=parse_interval_seconds(config.interval))
    next_time = anchor + duration
    while next_time <= after:
        next_time += duration
    return next_time.astimezone(UTC)


def _require_aware(value: datetime, *, name: str) -> None:
    """Raise a clear error unless ``value`` identifies an absolute instant."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
