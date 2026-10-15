"""Caller-held v2 schedule receipt and terminal proof over a disposable venue."""

import shutil
import sqlite3
from pathlib import Path

import pytest

from marianne.daemon.config import DaemonConfig
from marianne.daemon.ipc.client import DaemonClient
from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.server import DaemonServer
from marianne.daemon.manager import JobManager
from marianne.daemon.process import DaemonProcess
from marianne.daemon.recurrence import RecurrenceController
from marianne.daemon.schedule_registry import ScheduleRegistry


def manager_for(state: Path) -> JobManager:
    manager = JobManager(DaemonConfig(state_db_path=state))
    manager._recurrence_controller = RecurrenceController(
        manager._schedule_registry, manager.submit_job,
        lambda *_: object(), lambda *_: None, manager._is_schedule_active,
    )
    return manager


def score_at(root: Path) -> Path:
    score = root / "daily.yaml"
    score.write_text(
        "name: Daily Report\n"
        f"workspace: {root / 'workspace'}\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        "schedule:\n  interval: 5m\n",
        encoding="utf-8",
    )
    return score


@pytest.mark.asyncio
async def test_v2_receipt_survives_restart_then_cancel_has_terminal_proof(tmp_path: Path) -> None:
    state = tmp_path / "state" / "state.db"
    score = score_at(tmp_path)
    manager = manager_for(state)
    await manager._schedule_registry.open()
    receipt = await manager.register_schedule_v2(score)
    assert receipt["receipt_version"] == 2
    assert len(receipt["registration_id"]) >= 32
    await manager._schedule_registry.close()

    restarted = manager_for(state)
    await restarted._schedule_registry.open()
    handler = RequestHandler()
    DaemonProcess(DaemonConfig())._register_methods(handler, restarted, health=None)
    server = DaemonServer(tmp_path / "venue.sock", handler)
    await server.start()
    client = DaemonClient(tmp_path / "venue.sock")
    try:
        current = await client.current_schedule_v2(receipt)
        assert current.state == "live"
        wrong = dict(receipt, registration_id="wrong-token")
        assert (await client.cancel_schedule_v2(wrong)).state == "inconclusive"
        assert (await client.current_schedule_v2(wrong)).state == "inconclusive"
        assert (await client.cancel_schedule_v2(receipt)).state == "not_registered"
        retired = await client.current_schedule_v2(receipt)
        assert retired.state == "not_registered"
        assert retired.registration_id == receipt["registration_id"]
    finally:
        await client.close()
        await server.stop()
        await restarted._schedule_registry.close()


@pytest.mark.asyncio
async def test_paired_rollback_cannot_certify_v2_absence(tmp_path: Path) -> None:
    state = tmp_path / "state" / "state.db"
    score = score_at(tmp_path)
    async with ScheduleRegistry(state) as registry:
        witness = registry._custody_path
    saved_db, saved_witness = tmp_path / "old.db", tmp_path / "old.witness"
    shutil.copyfile(state, saved_db)
    shutil.copyfile(witness, saved_witness)
    manager = manager_for(state)
    await manager._schedule_registry.open()
    receipt = await manager.register_schedule_v2(score)
    await manager._schedule_registry.close()
    shutil.copyfile(saved_db, state)
    shutil.copyfile(saved_witness, witness)
    restored = manager_for(state)
    await restored._schedule_registry.open()
    handler = RequestHandler()
    DaemonProcess(DaemonConfig())._register_methods(handler, restored, health=None)
    server = DaemonServer(tmp_path / "venue.sock", handler)
    await server.start()
    client = DaemonClient(tmp_path / "venue.sock")
    try:
        result = await client.current_schedule_v2(receipt)
        assert result.state == "inconclusive"
        assert result.reason == "receipt_not_found"
        cancelled = await client.cancel_schedule_v2(receipt)
        assert cancelled.state == "inconclusive"
        assert await restored._schedule_registry.get(receipt["schedule_id"]) is None
    finally:
        await client.close()
        await server.stop()
        await restored._schedule_registry.close()


@pytest.mark.asyncio
async def test_socket_re_registration_invalidates_old_receipt(tmp_path: Path) -> None:
    state = tmp_path / "state" / "state.db"
    manager = manager_for(state)
    await manager._schedule_registry.open()
    handler = RequestHandler()
    DaemonProcess(DaemonConfig())._register_methods(handler, manager, health=None)
    server = DaemonServer(tmp_path / "venue.sock", handler)
    await server.start()
    client = DaemonClient(tmp_path / "venue.sock")
    try:
        score = score_at(tmp_path)
        first = await client.register_schedule_v2(score)
        assert first.state == "live"
        assert first.receipt_version == 2
        assert first.registration_id
        second = await client.register_schedule_v2(score)
        assert second.state == "live"
        assert second.registration_id != first.registration_id
        old = await client.current_schedule_v2(first)
        assert old.state == "inconclusive"
        assert old.reason == "registration_changed"
        assert (await client.current_schedule_v2(second)).state == "live"
        assert (await client.cancel_schedule_v2(first)).state == "inconclusive"
        assert (await client.current_schedule_v2(second)).state == "live"
    finally:
        await client.close()
        await server.stop()
        await manager._schedule_registry.close()


@pytest.mark.asyncio
async def test_copied_venue_and_unbound_request_refuse(tmp_path: Path) -> None:
    original = tmp_path / "original" / "state.db"
    copied = tmp_path / "copied" / "state.db"
    manager = manager_for(original)
    await manager._schedule_registry.open()
    receipt = await manager.register_schedule_v2(score_at(tmp_path))
    await manager._schedule_registry.close()
    copied.parent.mkdir()
    shutil.copyfile(original, copied)
    shutil.copyfile(ScheduleRegistry(original)._custody_path,
                    ScheduleRegistry(copied)._custody_path)
    replacement = manager_for(copied)
    await replacement._schedule_registry.open()
    handler = RequestHandler()
    DaemonProcess(DaemonConfig())._register_methods(handler, replacement, health=None)
    server = DaemonServer(tmp_path / "same-name.sock", handler)
    await server.start()
    client = DaemonClient(tmp_path / "same-name.sock")
    try:
        result = await client.current_schedule_v2(receipt)
        assert result.state == "inconclusive"
        no_token = dict(receipt, registration_id="")
        assert (await client.cancel_schedule_v2(no_token)).state == "inconclusive"
    finally:
        await client.close()
        await server.stop()
        await replacement._schedule_registry.close()


@pytest.mark.asyncio
async def test_interrupted_custody_advance_refuses_v2_current(tmp_path: Path) -> None:
    state = tmp_path / "state" / "state.db"
    manager = manager_for(state)
    await manager._schedule_registry.open()
    receipt = await manager.register_schedule_v2(score_at(tmp_path))
    identity, revision = await manager._schedule_registry._checked_identity()
    manager._schedule_registry._write_custody(identity, revision + 1)
    result = await manager.current_schedule_v2(
        receipt["schedule_id"], receipt["registry_identity"],
        receipt["source_digest"], receipt["registration_id"], 2,
    )
    assert result["state"] == "inconclusive"
    assert result["reason"] == "damaged_state"
    await manager._schedule_registry.close()


@pytest.mark.asyncio
async def test_generic_stop_of_accepted_v2_registration_is_terminal(tmp_path: Path) -> None:
    manager = manager_for(tmp_path / "state" / "state.db")
    await manager._schedule_registry.open()
    receipt = await manager.register_schedule_v2(score_at(tmp_path))
    await manager.cancel_job(receipt["schedule_id"], require_schedule=True)
    result = await manager.current_schedule_v2(
        receipt["schedule_id"], receipt["registry_identity"],
        receipt["source_digest"], receipt["registration_id"], 2,
    )
    assert result["state"] == "not_registered"
    assert result["registration_id"] == receipt["registration_id"]
    await manager._schedule_registry.close()


@pytest.mark.asyncio
async def test_pre_witness_identity_migrates_by_rotation(tmp_path: Path) -> None:
    state = tmp_path / "state.db"
    old_identity = "6eb6d5f0-443c-4aae-99de-5b76ad082730"
    with sqlite3.connect(state) as conn:
        conn.execute("CREATE TABLE registry_identity (singleton INTEGER PRIMARY KEY "
                     "CHECK (singleton = 1), identity TEXT NOT NULL)")
        conn.execute("INSERT INTO registry_identity VALUES (1, ?)", (old_identity,))
    async with ScheduleRegistry(state) as migrated:
        new_identity = await migrated.identity()
        assert new_identity != old_identity
    async with ScheduleRegistry(state) as restarted:
        assert await restarted.identity() == new_identity
