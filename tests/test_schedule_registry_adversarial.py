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
    ScheduleRegistryDataError,
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
async def test_malformed_persisted_schedule_json_has_safe_diagnostic(tmp_path: Path) -> None:
    """Corruption names the schedule but never echoes persisted source content."""
    db_path = tmp_path / "conductor-state.db"
    registry = ScheduleRegistry(db_path)
    await registry.open()
    await _upsert_due(registry)
    await registry.close()

    async with aiosqlite.connect(str(db_path)) as connection:
        await connection.execute(
            "UPDATE schedules SET schedule_json = ? WHERE schedule_id = ?",
            ('{"prompt": "sensitive source content"', "daily-report"),
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
