"""Concurrency, corruption, and availability tests for schedule leases."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

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
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schedule_json", '{"interval": "not-a-duration"}'),
        ("next_due_at", "not-a-number"),
        ("enabled", 2),
        ("last_outcome", b"not-text"),
        ("updated_at", "not-a-number"),
    ],
)
async def test_bulk_list_exposes_one_idempotent_disabled_diagnostic_per_bad_row(
    tmp_path: Path,
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt peer is visible, disabled once, and cannot block a healthy one."""
    db_path = tmp_path / "conductor-state.db"
    logged = MagicMock()
    monkeypatch.setattr("marianne.daemon.schedule_registry._logger.error", logged)
    async with ScheduleRegistry(db_path) as registry:
        await _upsert_due(registry)
        await registry.upsert(
            "healthy-report",
            "healthy-report",
            Path("/scores/healthy-report.yaml"),
            ScheduleConfig(interval="1h"),
            "healthy-digest",
            200.0,
        )

    async with aiosqlite.connect(str(db_path)) as connection:
        if field == "enabled":
            await connection.execute("PRAGMA ignore_check_constraints = ON")
        await connection.execute(
            f"UPDATE schedules SET {field} = ? WHERE schedule_id = ?",
            (value, "daily-report"),
        )
        await connection.commit()

    async with ScheduleRegistry(db_path) as registry:
        first = await registry.list()
        async with aiosqlite.connect(str(db_path)) as connection:
            first_write = await (
                await connection.execute(
                    "SELECT enabled, last_outcome, updated_at FROM schedules "
                    "WHERE schedule_id = ?",
                    ("daily-report",),
                )
            ).fetchone()
        second = await registry.list()
        async with aiosqlite.connect(str(db_path)) as connection:
            second_write = await (
                await connection.execute(
                    "SELECT enabled, last_outcome, updated_at FROM schedules "
                    "WHERE schedule_id = ?",
                    ("daily-report",),
                )
            ).fetchone()
        with pytest.raises(ScheduleRegistryDataError):
            await registry.get("daily-report")
        for lifecycle in (registry.pause, registry.resume, registry.remove):
            with pytest.raises(ScheduleRegistryDataError):
                await lifecycle("daily-report")

    assert [record.schedule_id for record in first] == ["daily-report", "healthy-report"]
    assert [record.schedule_id for record in second] == ["daily-report", "healthy-report"]
    diagnostic = first[0]
    assert diagnostic.enabled is False
    assert diagnostic.diagnostic == "registry_data_error"
    assert diagnostic.last_outcome == "registry_data_error"
    assert diagnostic.schedule_json == ""
    assert diagnostic.score_path == Path("<registry-data-error>")
    assert first_write is not None
    assert first_write[:2] == (0, "registry_data_error")
    assert second_write == first_write
    assert second[0].diagnostic == "registry_data_error"
    assert second[1].enabled is True
    logged.assert_called_once_with(
        "schedule.registry_data_disabled",
        schedule_id="daily-report",
    )


@pytest.mark.adversarial
async def test_bulk_list_repairs_raw_schedule_keys_without_public_key_collisions(
    tmp_path: Path,
) -> None:
    """Empty and non-text SQLite keys retain exact repair identity but safe labels."""
    db_path = tmp_path / "conductor-state.db"
    async with ScheduleRegistry(db_path) as registry:
        await _upsert_due(registry)
        await registry.upsert(
            "second-report",
            "second-report",
            Path("/scores/second-report.yaml"),
            ScheduleConfig(interval="1h"),
            "second-digest",
            200.0,
        )

    async with aiosqlite.connect(str(db_path)) as connection:
        await connection.execute(
            "UPDATE schedules SET schedule_id = ?, schedule_json = ? "
            "WHERE schedule_id = ?",
            ("", '{"interval": "bad"}', "daily-report"),
        )
        await connection.execute(
            "UPDATE schedules SET schedule_id = ?, next_due_at = ? "
            "WHERE schedule_id = ?",
            (b"\x00bad-key", "not-a-number", "second-report"),
        )
        await connection.commit()

    async with ScheduleRegistry(db_path) as registry:
        first = await registry.list()
        second = await registry.list()

    assert len(first) == len(second) == 2
    assert all(record.enabled is False for record in first)
    assert all(record.diagnostic == "registry_data_error" for record in first)
    assert len({record.schedule_id for record in first}) == 2
    assert all(record.schedule_id.startswith("registry-data-error-") for record in first)
    async with aiosqlite.connect(str(db_path)) as connection:
        rows = await (
            await connection.execute(
                "SELECT typeof(schedule_id), enabled, last_outcome FROM schedules "
                "ORDER BY rowid"
            )
        ).fetchall()
    assert rows == [("text", 0, "registry_data_error"), ("blob", 0, "registry_data_error")]


@pytest.mark.adversarial
@pytest.mark.parametrize(
    ("operation", "mutation_name"),
    [
        ("pause", "set enabled state"),
        ("resume", "set enabled state"),
        ("remove", "remove"),
    ],
)
async def test_lifecycle_mutation_cannot_cross_a_concurrent_quarantine(
    tmp_path: Path,
    operation: str,
    mutation_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale lifecycle preflight cannot mutate a row quarantined by another registry."""
    db_path = tmp_path / "conductor-state.db"
    first = ScheduleRegistry(db_path)
    second = ScheduleRegistry(db_path)
    await first.open()
    await second.open()
    entered_mutation = asyncio.Event()
    allow_mutation = asyncio.Event()
    logged = MagicMock()
    monkeypatch.setattr("marianne.daemon.schedule_registry._logger.error", logged)
    original_execute = first._execute_mutation

    async def hold_lifecycle_mutation(*args: object, **kwargs: object) -> aiosqlite.Cursor:
        if args[0] == mutation_name:
            entered_mutation.set()
            await allow_mutation.wait()
        return await original_execute(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(first, "_execute_mutation", hold_lifecycle_mutation)
    try:
        await _upsert_due(first)
        lifecycle = getattr(first, operation)
        lifecycle_task = asyncio.create_task(lifecycle("daily-report"))
        await entered_mutation.wait()

        await second._db.execute(
            "UPDATE schedules SET updated_at = ? WHERE schedule_id = ?",
            ("not-a-number", "daily-report"),
        )
        await second._db.commit()
        quarantined = await second.list()
        async with aiosqlite.connect(str(db_path)) as connection:
            first_write = await (
                await connection.execute(
                    "SELECT enabled, last_outcome, updated_at FROM schedules "
                    "WHERE schedule_id = ?",
                    ("daily-report",),
                )
            ).fetchone()

        allow_mutation.set()
        with pytest.raises(ScheduleRegistryDataError):
            await lifecycle_task

        later = await second.list()
        async with aiosqlite.connect(str(db_path)) as connection:
            second_write = await (
                await connection.execute(
                    "SELECT enabled, last_outcome, updated_at FROM schedules "
                    "WHERE schedule_id = ?",
                    ("daily-report",),
                )
            ).fetchone()
    finally:
        allow_mutation.set()
        await first.close()
        await second.close()

    assert quarantined[0].diagnostic == "registry_data_error"
    assert later[0].diagnostic == "registry_data_error"
    assert first_write is not None
    assert first_write[:2] == (0, "registry_data_error")
    assert first_write == second_write
    logged.assert_called_once_with(
        "schedule.registry_data_disabled",
        schedule_id="daily-report",
    )


@pytest.mark.adversarial
@pytest.mark.parametrize(
    ("operation", "mutation_name", "missing_message"),
    [
        ("resume", "set enabled state", "enabled-state mutation"),
        ("remove", "remove", "removal"),
    ],
)
async def test_stale_lifecycle_mutation_reports_a_missing_row_distinctly(
    tmp_path: Path,
    operation: str,
    mutation_name: str,
    missing_message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-row lifecycle write reports deletion, not a data quarantine."""
    db_path = tmp_path / "conductor-state.db"
    first = ScheduleRegistry(db_path)
    second = ScheduleRegistry(db_path)
    await first.open()
    await second.open()
    entered_mutation = asyncio.Event()
    allow_mutation = asyncio.Event()
    original_execute = first._execute_mutation

    async def hold_lifecycle_mutation(*args: object, **kwargs: object) -> aiosqlite.Cursor:
        if args[0] == mutation_name:
            entered_mutation.set()
            await allow_mutation.wait()
        return await original_execute(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(first, "_execute_mutation", hold_lifecycle_mutation)
    try:
        await _upsert_due(first)
        lifecycle_task = asyncio.create_task(getattr(first, operation)("daily-report"))
        await entered_mutation.wait()
        await second.remove("daily-report")
        allow_mutation.set()
        with pytest.raises(ScheduleRegistryError, match=missing_message):
            await lifecycle_task
    finally:
        allow_mutation.set()
        await first.close()
        await second.close()


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
