"""Disposable registry and IPC contract checks for the schedule venue seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.daemon.schedule_registry import ScheduleRegistry


@pytest.mark.asyncio
async def test_registry_identity_survives_restart_and_distinguishes_replacement(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state.db"
    async with ScheduleRegistry(state) as first:
        original = await first.identity()
    async with ScheduleRegistry(state) as restarted:
        assert await restarted.identity() == original
    state.unlink()
    async with ScheduleRegistry(state) as replacement:
        assert await replacement.identity() != original


def test_schedule_methods_are_registered() -> None:
    from unittest.mock import MagicMock

    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.process import DaemonProcess

    handler = MagicMock()
    DaemonProcess(DaemonConfig())._register_methods(handler, MagicMock(), health=None)
    methods = {call.args[0] for call in handler.register.call_args_list}
    assert {"schedule.register", "schedule.current", "schedule.cancel"} <= methods


@pytest.mark.asyncio
async def test_bound_registration_current_cancel_and_wrongness(tmp_path: Path) -> None:
    import hashlib

    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.manager import JobManager
    from marianne.daemon.recurrence import RecurrenceController

    score = tmp_path / "daily.yaml"
    score.write_text(
        "name: Daily Report\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        "schedule:\n  interval: 5m\n",
        encoding="utf-8",
    )
    pre_submit = hashlib.sha256(score.read_bytes()).hexdigest()
    score.write_text(score.read_text().replace("template: test", "template: changed"))
    accepted = hashlib.sha256(score.read_bytes()).hexdigest()
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "state.db"))
    await manager._schedule_registry.open()
    manager._recurrence_controller = RecurrenceController(
        manager._schedule_registry, manager.submit_job,
        lambda *_: object(), lambda *_: None, manager._is_schedule_active,
    )
    try:
        receipt = await manager.register_schedule(score)
        assert receipt["state"] == "live"
        assert receipt["source_digest"] == accepted != pre_submit
        identity, schedule_id = receipt["registry_identity"], receipt["schedule_id"]
        wrong = await manager.current_schedule(schedule_id, "wrong-registry", accepted)
        assert wrong["state"] == "inconclusive"
        changed = await manager.current_schedule(schedule_id, identity, pre_submit)
        assert changed["reason"] == "digest_mismatch"
        denied = await manager.cancel_schedule(schedule_id, identity, pre_submit)
        assert denied["state"] == "inconclusive"
        assert await manager._schedule_registry.get(schedule_id) is not None
        from marianne.daemon.exceptions import JobSubmissionError

        with pytest.raises(JobSubmissionError, match="digest changed"):
            await manager.cancel_job(
                schedule_id, expected_source_digest=pre_submit, require_schedule=True,
            )
        assert await manager._schedule_registry.get(schedule_id) is not None
        with_schedule = score.read_text()
        score.write_text(with_schedule.replace("schedule:\n  interval: 5m\n", ""))
        no_schedule = await manager.register_schedule(score)
        assert no_schedule["state"] == "inconclusive"
        assert await manager._schedule_registry.get(schedule_id) is not None
        score.write_text(with_schedule)
        manager._schedule_lineage_reservations[schedule_id] = 1
        running = await manager.current_schedule(schedule_id, identity, accepted)
        assert running["state"] == "running"
        manager._schedule_lineage_reservations.clear()
        cancelled = await manager.cancel_schedule(schedule_id, identity, accepted)
        assert cancelled["cancelled"] is True
        assert cancelled["state"] == "not_registered"
        absent = await manager.current_schedule(schedule_id, identity, accepted)
        assert absent["state"] == "not_registered"
    finally:
        await manager._schedule_registry.close()


def test_forged_venue_response_is_inconclusive() -> None:
    from marianne.daemon.exceptions import DaemonProtocolError
    from marianne.daemon.ipc.client import DaemonClient

    result = DaemonClient._bind_schedule_result(
        {"registry_identity": "forged", "schedule_id": "daily",
         "state": "not_registered", "observed_at": 1.0},
        "daily", "rightful", "accepted-digest",
    )
    assert result.state == "inconclusive"
    assert result.reason == "identity_mismatch"
    with pytest.raises(DaemonProtocolError, match="digest changed"):
        DaemonClient._bind_schedule_result(
            {"registry_identity": "rightful", "schedule_id": "daily",
             "source_digest": "forged", "state": "not_registered",
             "observed_at": 1.0},
            "daily", "rightful", "accepted-digest",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", ["registry_identity", "source_digest"])
async def test_registration_rejects_unbound_live_receipt(
    tmp_path: Path, missing_field: str,
) -> None:
    from unittest.mock import AsyncMock

    from marianne.daemon.exceptions import DaemonProtocolError
    from marianne.daemon.ipc.client import DaemonClient

    response = {
        "registry_identity": "durable-registry", "schedule_id": "daily",
        "source_digest": "accepted-digest", "state": "live", "observed_at": 1.0,
    }
    response.pop(missing_field)
    client = DaemonClient(tmp_path / "unused.sock")
    client.call = AsyncMock(return_value=response)
    with pytest.raises(DaemonProtocolError, match="Unbound schedule registration"):
        await client.register_schedule(tmp_path / "daily.yaml")


@pytest.mark.asyncio
async def test_schedule_wire_refuses_missing_peer_credentials() -> None:
    from unittest.mock import MagicMock

    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.exceptions import DaemonAccessDeniedError
    from marianne.daemon.process import DaemonProcess

    handler = MagicMock()
    DaemonProcess(DaemonConfig())._register_methods(handler, MagicMock(), health=None)
    schedule_register = next(
        call.args[1] for call in handler.register.call_args_list
        if call.args[0] == "schedule.register"
    )
    with pytest.raises(DaemonAccessDeniedError):
        await schedule_register({"config_path": "/tmp/fiction.yaml"}, None)


@pytest.mark.asyncio
async def test_disposable_socket_restart_and_replacement(tmp_path: Path) -> None:
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.ipc.client import DaemonClient
    from marianne.daemon.ipc.handler import RequestHandler
    from marianne.daemon.ipc.server import DaemonServer
    from marianne.daemon.manager import JobManager
    from marianne.daemon.process import DaemonProcess
    from marianne.daemon.recurrence import RecurrenceController

    score = tmp_path / "daily.yaml"
    score.write_text(
        "name: Daily Report\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        "schedule:\n  interval: 5m\n",
        encoding="utf-8",
    )
    socket_path = tmp_path / "venue.sock"

    async def start(db_path: Path) -> tuple[JobManager, DaemonServer, DaemonClient]:
        config = DaemonConfig(state_db_path=db_path, pid_file=tmp_path / "unused.pid")
        manager = JobManager(config)
        await manager._schedule_registry.open()
        manager._recurrence_controller = RecurrenceController(
            manager._schedule_registry, manager.submit_job,
            lambda *_: object(), lambda *_: None, manager._is_schedule_active,
        )
        handler = RequestHandler()
        DaemonProcess(config)._register_methods(handler, manager, health=None)
        server = DaemonServer(socket_path, handler)
        await server.start()
        return manager, server, DaemonClient(socket_path)

    async def stop(
        manager: JobManager, server: DaemonServer, client: DaemonClient,
    ) -> None:
        await client.close()
        await server.stop()
        await manager._schedule_registry.close()

    state = tmp_path / "state.db"
    manager, server, client = await start(state)
    try:
        registered = await client.register_schedule(score)
        assert registered.state == "live"
        assert registered.source_digest is not None
        identity = registered.registry_identity
        schedule_id = registered.schedule_id
        current = await client.current_schedule(schedule_id, identity, registered.source_digest)
        assert current.state == "live"
    finally:
        await stop(manager, server, client)

    manager, server, client = await start(state)
    try:
        same = await client.current_schedule(schedule_id, identity, registered.source_digest)
        assert same.state == "live"
    finally:
        await stop(manager, server, client)

    manager, server, client = await start(tmp_path / "replacement.db")
    try:
        wrong = await client.current_schedule(schedule_id, identity, registered.source_digest)
        assert wrong.state == "inconclusive"
        assert wrong.reason == "identity_mismatch"
    finally:
        await stop(manager, server, client)
