"""Lifecycle coverage for the durable recurring-schedule registry."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from marianne.core.config import ScheduleConfig
from marianne.daemon.registry import JobRegistry
from marianne.daemon.schedule_registry import ScheduleRegistry


@pytest.fixture
async def registry(tmp_path: Path) -> AsyncIterator[ScheduleRegistry]:
    """Provide an opened registry backed by an isolated conductor database."""
    value = ScheduleRegistry(tmp_path / "conductor-state.db")
    await value.open()
    yield value
    await value.close()


async def _upsert(
    registry: ScheduleRegistry,
    schedule_id: str = "weekday-report",
    *,
    score_name: str = "weekday-report",
    score_path: Path = Path("/scores/weekday-report.yaml"),
    schedule: ScheduleConfig | None = None,
    source_digest: str = "digest-v1",
    next_due_at: float = 100.0,
) -> None:
    """Register the default schedule, allowing tests to vary one property."""
    await registry.upsert(
        schedule_id,
        score_name,
        score_path,
        schedule or ScheduleConfig(interval="5m"),
        source_digest,
        next_due_at,
    )


async def test_open_migrates_empty_sqlite_file(tmp_path: Path) -> None:
    """Opening an empty conductor database creates the independent schedules table."""
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    registry = ScheduleRegistry(db_path)
    await registry.open()
    try:
        assert await registry.list() == []
    finally:
        await registry.close()

    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()
    assert "schedules" in tables


async def test_schedule_table_coexists_with_existing_job_registry(tmp_path: Path) -> None:
    """Schedule-table migration is additive in the conductor's shared state DB."""
    db_path = tmp_path / "conductor-state.db"
    jobs = JobRegistry(db_path)
    await jobs.open()
    try:
        await jobs.register_job("manual-job", Path("/scores/manual.yaml"), Path("/workspaces"))
        schedules = ScheduleRegistry(db_path)
        await schedules.open()
        try:
            await _upsert(schedules)
            assert await schedules.get("weekday-report") is not None
        finally:
            await schedules.close()
        assert await jobs.get_job("manual-job") is not None
    finally:
        await jobs.close()


async def test_upsert_replaces_same_id_and_preserves_created_at(
    registry: ScheduleRegistry,
) -> None:
    """A replacement keeps stable creation provenance while updating its projection."""
    await _upsert(registry, source_digest="digest-before", next_due_at=100.0)
    original = await registry.get("weekday-report")
    assert original is not None

    await _upsert(
        registry,
        score_name="weekday-report-v2",
        score_path=Path("/scores/weekday-report-v2.yaml"),
        schedule=ScheduleConfig(cron="0 9 * * 1-5", timezone="Europe/Berlin"),
        source_digest="digest-after",
        next_due_at=200.0,
    )

    replaced = await registry.get("weekday-report")
    assert replaced is not None
    assert replaced.created_at == original.created_at
    assert replaced.updated_at >= original.updated_at
    assert replaced.score_name == "weekday-report-v2"
    assert replaced.score_path == Path("/scores/weekday-report-v2.yaml")
    assert replaced.source_digest == "digest-after"
    assert replaced.next_due_at == 200.0
    assert json.loads(replaced.schedule_json) == {
        "cron": "0 9 * * 1-5",
        "enabled": True,
        "interval": None,
        "jitter_seconds": 0,
        "misfire": "skip",
        "overlap": "skip",
        "timezone": "Europe/Berlin",
    }


async def test_registration_persists_across_close_and_reopen(tmp_path: Path) -> None:
    """Schedule state survives the conductor connection lifecycle."""
    db_path = tmp_path / "conductor-state.db"
    first = ScheduleRegistry(db_path)
    await first.open()
    await _upsert(first, next_due_at=123.5)
    await first.close()

    second = ScheduleRegistry(db_path)
    await second.open()
    try:
        record = await second.get("weekday-report")
    finally:
        await second.close()

    assert record is not None
    assert record.score_path == Path("/scores/weekday-report.yaml")
    assert record.next_due_at == 123.5
    assert record.enabled is True


async def test_pause_and_resume_change_only_enabled_state(registry: ScheduleRegistry) -> None:
    """Explicit schedule lifecycle controls preserve the registration."""
    await _upsert(registry)

    await registry.pause("weekday-report")
    paused = await registry.get("weekday-report")
    assert paused is not None
    assert paused.enabled is False

    await registry.resume("weekday-report")
    resumed = await registry.get("weekday-report")
    assert resumed is not None
    assert resumed.enabled is True
    assert resumed.created_at == paused.created_at


async def test_remove_deletes_registration(registry: ScheduleRegistry) -> None:
    """Removal makes an existing schedule unavailable for later ticks."""
    await _upsert(registry)

    await registry.remove("weekday-report")

    assert await registry.get("weekday-report") is None
    assert await registry.list() == []


async def test_list_orders_by_stable_schedule_id(registry: ScheduleRegistry) -> None:
    """Listing uses stable IDs rather than write-timing-dependent ordering."""
    await _upsert(registry, "zeta")
    await _upsert(registry, "alpha")
    await _upsert(registry, "middle")

    assert [record.schedule_id for record in await registry.list()] == [
        "alpha",
        "middle",
        "zeta",
    ]


async def test_submission_and_tick_outcome_update_lifecycle_projection(
    registry: ScheduleRegistry,
) -> None:
    """The registry records controller results without calculating recurrence itself."""
    await _upsert(registry, next_due_at=100.0)
    claim = await registry.claim_due("weekday-report", 100.0, now=100.0)
    assert claim is not None

    await registry.record_submission("weekday-report", 100.0, "child-100")
    await registry.record_tick_outcome(
        "weekday-report",
        100.0,
        "overlap_skipped",
        next_due_at=200.0,
        dropped=True,
    )
    dropped = await registry.get("weekday-report")
    assert dropped is not None
    assert dropped.last_due_at == 100.0
    assert dropped.last_run_id == "child-100"
    assert dropped.last_outcome == "overlap_skipped"
    assert dropped.next_due_at == 200.0
    assert dropped.consecutive_drops == 1

    claim = await registry.claim_due("weekday-report", 200.0, now=200.0)
    assert claim is not None
    await registry.record_tick_outcome(
        "weekday-report",
        200.0,
        "submitted",
        next_due_at=300.0,
        dropped=False,
    )
    submitted = await registry.get("weekday-report")
    assert submitted is not None
    assert submitted.last_due_at == 200.0
    assert submitted.last_outcome == "submitted"
    assert submitted.next_due_at == 300.0
    assert submitted.consecutive_drops == 0
