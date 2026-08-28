"""Concurrency, corruption, and availability tests for schedule leases."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from marianne.core.config import ScheduleConfig
from marianne.daemon.schedule_registry import (
    DueClaim,
    ScheduleRegistry,
    ScheduleRegistryBusyError,
    ScheduleRegistryClaimError,
    ScheduleRegistryDataError,
    ScheduleRegistryError,
)


async def _upsert_due(registry: ScheduleRegistry, *, due_at: float = 100.0) -> None:
    """Seed one known schedule at a deterministic epoch due time."""
    await registry.upsert(
        "daily-report",
        "daily-report",
        Path("/scores/daily-report.yaml"),
        ScheduleConfig(interval="1h"),
        "opaque-source-digest",
        due_at,
    )


@pytest.mark.adversarial
async def test_two_connections_claim_one_due_identity(tmp_path: Path) -> None:
    """Concurrent connections can never both lease the same due schedule tick."""
    db_path = tmp_path / "conductor-state.db"
    first = ScheduleRegistry(db_path)
    second = ScheduleRegistry(db_path)
    await first.open()
    await second.open()
    try:
        await _upsert_due(first)
        barrier = asyncio.Barrier(2)

        async def claim(registry: ScheduleRegistry) -> DueClaim | None:
            await barrier.wait()
            return await registry.claim_due("daily-report", 100.0, now=100.0)

        claims = await asyncio.gather(claim(first), claim(second))
    finally:
        await first.close()
        await second.close()

    assert claims.count(DueClaim("daily-report", 100.0)) == 1
    assert claims.count(None) == 1


@pytest.mark.adversarial
async def test_claim_rejects_stale_earlier_due_time(tmp_path: Path) -> None:
    """An old timer cannot claim a schedule whose persisted due time moved forward."""
    async with ScheduleRegistry(tmp_path / "conductor-state.db") as registry:
        await _upsert_due(registry, due_at=100.0)

        claim = await registry.claim_due("daily-report", 99.0, now=100.0)

    assert claim is None


@pytest.mark.adversarial
async def test_claim_rejects_disabled_schedule(tmp_path: Path) -> None:
    """Paused registrations never acquire a due-time lease."""
    async with ScheduleRegistry(tmp_path / "conductor-state.db") as registry:
        await _upsert_due(registry)
        await registry.pause("daily-report")

        claim = await registry.claim_due("daily-report", 100.0, now=100.0)

    assert claim is None


@pytest.mark.adversarial
@pytest.mark.parametrize(
    "schedule_json",
    [
        '{"prompt": "sensitive source content"',
        "{}",
        '{"interval": "1h", "hostile": "sensitive source content"}',
    ],
)
async def test_invalid_persisted_schedule_json_has_safe_diagnostic(
    tmp_path: Path, schedule_json: str
) -> None:
    """Invalid JSON/configuration names the schedule without echoing source content."""
    db_path = tmp_path / "conductor-state.db"
    registry = ScheduleRegistry(db_path)
    await registry.open()
    await _upsert_due(registry)
    await registry.close()

    async with aiosqlite.connect(str(db_path)) as connection:
        await connection.execute(
            "UPDATE schedules SET schedule_json = ? WHERE schedule_id = ?",
            (schedule_json, "daily-report"),
        )
        await connection.commit()

    await registry.open()
    try:
        with pytest.raises(ScheduleRegistryDataError) as exc_info:
            await registry.get("daily-report")
    finally:
        await registry.close()

    message = str(exc_info.value)
    assert "daily-report" in message
    assert "sensitive source content" not in message


@pytest.mark.adversarial
@pytest.mark.parametrize("next_due_at", [100.0, 99.0])
async def test_tick_outcome_rejects_non_advancing_next_due(
    tmp_path: Path, next_due_at: float
) -> None:
    """A claimed tick cannot overwrite its schedule with an equal or older due time."""
    async with ScheduleRegistry(tmp_path / "conductor-state.db") as registry:
        await _upsert_due(registry)
        assert await registry.claim_due("daily-report", 100.0, now=100.0) is not None

        with pytest.raises(ValueError, match="later than due_at"):
            await registry.record_tick_outcome(
                "daily-report",
                100.0,
                "submitted",
                next_due_at=next_due_at,
                dropped=False,
            )

        record = await registry.get("daily-report")

    assert record is not None
    assert record.next_due_at == 100.0
    assert record.last_outcome is None


@pytest.mark.adversarial
async def test_lifecycle_writers_reject_missing_or_unclaimed_due(tmp_path: Path) -> None:
    """A lifecycle transition must name a durable claim rather than silently no-op."""
    async with ScheduleRegistry(tmp_path / "conductor-state.db") as registry:
        with pytest.raises(ScheduleRegistryClaimError, match="missing"):
            await registry.record_submission("missing", 100.0, "child-100")

        await _upsert_due(registry)
        with pytest.raises(ScheduleRegistryClaimError, match="daily-report"):
            await registry.record_submission("daily-report", 100.0, "child-100")
        with pytest.raises(ScheduleRegistryClaimError, match="daily-report"):
            await registry.record_tick_outcome(
                "daily-report",
                100.0,
                "submitted",
                next_due_at=200.0,
                dropped=False,
            )


@pytest.mark.adversarial
async def test_lifecycle_writers_reject_a_superseded_claim(tmp_path: Path) -> None:
    """A former due identity cannot mutate state after the next due is claimed."""
    async with ScheduleRegistry(tmp_path / "conductor-state.db") as registry:
        await _upsert_due(registry)
        assert await registry.claim_due("daily-report", 100.0, now=100.0) is not None
        await registry.record_tick_outcome(
            "daily-report",
            100.0,
            "submitted",
            next_due_at=200.0,
            dropped=False,
        )
        assert await registry.claim_due("daily-report", 200.0, now=200.0) is not None

        with pytest.raises(ScheduleRegistryClaimError, match="daily-report"):
            await registry.record_submission("daily-report", 100.0, "stale-child")
        with pytest.raises(ScheduleRegistryClaimError, match="daily-report"):
            await registry.record_tick_outcome(
                "daily-report",
                100.0,
                "stale",
                next_due_at=300.0,
                dropped=True,
            )


@pytest.mark.adversarial
async def test_failed_mutation_rolls_back_before_later_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit failure cannot leak a write into a later successful transaction."""
    async with ScheduleRegistry(tmp_path / "conductor-state.db") as registry:
        original_commit = registry._db.commit

        async def fail_commit() -> None:
            raise aiosqlite.OperationalError("injected commit failure")

        monkeypatch.setattr(registry._db, "commit", fail_commit)
        with pytest.raises(ScheduleRegistryError, match="upsert"):
            await _upsert_due(registry)
        monkeypatch.setattr(registry._db, "commit", original_commit)

        assert await registry.get("daily-report") is None
        await _upsert_due(registry, due_at=200.0)
        record = await registry.get("daily-report")

    assert record is not None
    assert record.next_due_at == 200.0


@pytest.mark.adversarial
async def test_claim_reports_bounded_database_lock(tmp_path: Path) -> None:
    """A writer lock is surfaced as a typed failure instead of an unbounded wait."""
    db_path = tmp_path / "conductor-state.db"
    registry = ScheduleRegistry(db_path, busy_timeout_seconds=0.01)
    await registry.open()
    await _upsert_due(registry)

    holder = await aiosqlite.connect(str(db_path))
    await holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ScheduleRegistryBusyError, match="claim due"):
            await registry.claim_due("daily-report", 100.0, now=100.0)
    finally:
        await holder.rollback()
        await holder.close()
        await registry.close()


@pytest.mark.adversarial
async def test_claim_is_not_replayed_after_reopening(tmp_path: Path) -> None:
    """The private durable lease identity survives a conductor connection restart."""
    db_path = tmp_path / "conductor-state.db"
    first = ScheduleRegistry(db_path)
    await first.open()
    await _upsert_due(first)
    first_claim = await first.claim_due("daily-report", 100.0, now=100.0)
    await first.close()

    second = ScheduleRegistry(db_path)
    await second.open()
    try:
        replay = await second.claim_due("daily-report", 100.0, now=100.0)
    finally:
        await second.close()

    assert first_claim == DueClaim("daily-report", 100.0)
    assert replay is None
