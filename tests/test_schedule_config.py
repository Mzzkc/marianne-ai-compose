"""Tests for recurring score schedule declarations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marianne.core.config import JobConfig, MisfirePolicy, OverlapPolicy, ScheduleConfig


def _minimal_score() -> dict[str, object]:
    """Return the smallest score payload required by ``JobConfig``."""
    return {
        "name": "scheduled-score",
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": "Perform the score."},
    }


def test_cron_schedule_declaration_uses_documented_defaults() -> None:
    """A five-field cron score carries the declared policy defaults."""
    config = ScheduleConfig(cron="0 9 * * 1-5", timezone="Europe/Berlin")

    assert config.enabled is True
    assert config.cron == "0 9 * * 1-5"
    assert config.timezone == "Europe/Berlin"
    assert config.misfire is MisfirePolicy.SKIP
    assert config.overlap is OverlapPolicy.SKIP
    assert config.jitter_seconds == 0


def test_interval_schedule_declaration_accepts_positive_decimal_duration() -> None:
    """Intervals support positive decimal values in the supported units."""
    config = ScheduleConfig(interval="1.5h")

    assert config.interval == "1.5h"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"cron": "0 9 * * *", "interval": "1h"},
    ],
)
def test_schedule_requires_exactly_one_timing_expression(data: dict[str, str]) -> None:
    """A score is either cron-driven or interval-driven, never neither/both."""
    with pytest.raises(ValidationError, match="exactly one"):
        ScheduleConfig(**data)


@pytest.mark.parametrize(
    "cron",
    [
        "not a cron",
        "0 9 * * * *",
    ],
)
def test_schedule_rejects_invalid_or_non_five_field_cron(cron: str) -> None:
    """Only valid five-field cron declarations are accepted."""
    with pytest.raises(ValidationError, match="five-field"):
        ScheduleConfig(cron=cron)


def test_schedule_rejects_invalid_iana_timezone() -> None:
    """Timezone declarations must name an IANA zone."""
    with pytest.raises(ValidationError, match="IANA"):
        ScheduleConfig(cron="0 9 * * *", timezone="Mars/Olympus_Mons")


@pytest.mark.parametrize("interval", ["0s", "-1s", " 1s", "1s ", "1w", "1"])
def test_schedule_rejects_non_positive_or_malformed_intervals(interval: str) -> None:
    """Schedule declarations reject invalid interval spellings at parse time."""
    with pytest.raises(ValidationError, match="positive"):
        ScheduleConfig(interval=interval)


@pytest.mark.parametrize("interval", ["0.0000001s", "9" * 400 + "s"])
def test_schedule_rejects_unrepresentable_intervals(interval: str) -> None:
    """Schedule declarations must resolve to finite, non-zero datetimes."""
    with pytest.raises(ValidationError, match="representable"):
        ScheduleConfig(interval=interval)


def test_job_config_keeps_old_score_serialization_schedule_free() -> None:
    """Scores without a declaration preserve their compact legacy dump."""
    score = JobConfig.model_validate(_minimal_score())

    assert score.schedule is None
    assert "schedule" not in score.model_dump(exclude_none=True)


def test_job_config_accepts_schedule_and_minimum_wall_time() -> None:
    """The job surface carries the new optional declaration and wall bound."""
    score_data = _minimal_score()
    score_data["schedule"] = {"interval": "30m"}
    score_data["max_wall_seconds"] = 60.0

    score = JobConfig.model_validate(score_data)

    assert score.schedule is not None
    assert score.schedule.interval == "30m"
    assert score.max_wall_seconds == 60.0


def test_job_config_rejects_wall_time_below_one_minute() -> None:
    """A max wall duration cannot be shorter than the supported minimum."""
    score_data = _minimal_score()
    score_data["max_wall_seconds"] = 59.9

    with pytest.raises(ValidationError, match="60"):
        JobConfig.model_validate(score_data)
