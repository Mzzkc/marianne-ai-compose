"""#408: conductor config + instrument caps hot-apply without restart.

Real-manager / real-baton integration tests. Precedents:
``test_baton_terminal_publication.py`` (real JobManager, mocked edges only)
and ``test_job_wall_deadline_fix1.py`` (real timing assertions).

Every cap assertion binds through the REAL dispatch decision path
(``dispatch.py`` model_concurrency / max_concurrent_sheets reads fed by the
adapter's per-cycle ``_build_dispatch_config``), never through setter
return values. The musician-spawn edge (``_dispatch_callback``) is the only
stub — the same edge precedents mock.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager
from marianne.daemon.process import DaemonProcess, _load_config
from marianne.daemon.scheduler import GlobalSheetScheduler
from marianne.daemon.types import ConfigReloadResult
from marianne.instruments.registry import InstrumentRegistry

_WS = Path("/tmp/408-test-ws")


# ─── Fixtures ──────────────────────────────────────────────────────────


def _sheet(num: int, *, instrument: str = "alpha", model: str | None = "m1") -> Sheet:
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=_WS,
        instrument_name=instrument,
        prompt_template=f"sheet {num}",
        timeout_seconds=60.0,
        instrument_config={"model": model} if model else {},
    )


def _profile_yaml(
    *,
    name: str = "alpha",
    max_concurrent: int = 2,
    gate_keys: list[str] | None = None,
) -> str:
    lines = [
        f"name: {name}",
        f"display_name: {name}",
        "kind: cli",
        "default_model: m1",
        "models:",
        "  - name: m1",
        "    context_window: 1000",
        "    cost_per_1k_input: 0.0",
        "    cost_per_1k_output: 0.0",
        f"    max_concurrent: {max_concurrent}",
        "cli:",
        "  command:",
        "    executable: echo",
        '    prompt_flag: "-n"',
        "  output:",
        "    format: text",
        "  errors:",
        "    success_exit_codes: [0]",
    ]
    if gate_keys is not None:
        lines += [
            "  interactive:",
            '    ready_pattern: "READY>"',
            "    startup_gates:",
            '      - pattern: "trust this folder"',
            f"        keys: {json.dumps(gate_keys)}",
        ]
    return "\n".join(lines) + "\n"


def _profile_dir(tmp_path: Path) -> Path:
    org = tmp_path / "org"
    org.mkdir(exist_ok=True)
    (org / "alpha.yaml").write_text(_profile_yaml(max_concurrent=2))
    return org


def _config_file(tmp_path: Path, **values: Any) -> Path:
    path = tmp_path / "conductor.yaml"
    path.write_text(yaml.dump(values if values else {"max_concurrent_jobs": 3}))
    return path


def _manager(
    tmp_path: Path,
    *,
    config: DaemonConfig,
    org_dir: Path,
    with_scheduler: bool = False,
) -> JobManager:
    """Real JobManager with the minimal live objects boot would create.

    The adapter and registry are the REAL production objects the reload
    path mutates; no other subsystem is started (precedent:
    test_baton_terminal_publication.py). The boot-time profile load and
    cap sync mirror JobManager.start() exactly.
    """
    from marianne.instruments.loader import load_all_profiles

    manager = JobManager(config, profile_dirs=(org_dir, tmp_path / "venue"))
    adapter = BatonAdapter(max_concurrent_sheets=config.max_concurrent_sheets)
    manager._baton_adapter = adapter
    manager._instrument_registry = InstrumentRegistry()
    profiles = load_all_profiles(
        organization_dir=org_dir, venue_dir=tmp_path / "venue"
    )
    manager._instrument_registry.replace_all(profiles)
    adapter.sync_model_concurrency(manager._model_caps_from_profiles(profiles))
    if with_scheduler:
        manager._scheduler_instance = GlobalSheetScheduler(config)
    return manager


async def _until(predicate, timeout: float = 10.0, interval: float = 0.02) -> None:
    """Await a condition without fixed sleeps; fail loudly on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not reached within timeout")
        await asyncio.sleep(interval)


class _DispatchRecorder:
    """Stub musician-spawn edge: records dispatches, never completes them."""

    def __init__(self) -> None:
        self.dispatched: list[tuple[str, int]] = []

    async def __call__(self, job_id: str, sheet_num: int, state: Any) -> None:
        self.dispatched.append((job_id, sheet_num))
        return None


# ─── Real dispatch loop: per-model caps (#408 + #397 class) ───────────


async def test_raised_model_cap_releases_waiting_sheets_through_real_dispatch(
    tmp_path: Path,
) -> None:
    """Cap 2 → 4 sheets dispatch, 2 blocked; cap raise → 3rd/4th dispatch.

    The block is proven persistent first (a forced dispatch cycle still
    refuses), and the release happens with NO completion events in flight —
    the only input is the cap sync the reload path performs.
    """
    adapter = BatonAdapter(max_concurrent_sheets=10)
    recorder = _DispatchRecorder()
    adapter._dispatch_callback = recorder
    adapter.register_job(
        "job", [_sheet(i) for i in range(1, 5)], {i: [] for i in range(1, 5)}
    )
    adapter.sync_model_concurrency({"alpha:m1": 2})

    loop_task = asyncio.create_task(adapter.run())
    try:
        await _until(lambda: len(recorder.dispatched) == 2)
        assert {sn for _, sn in recorder.dispatched} == {1, 2}

        # Force extra dispatch cycles: the cap must keep refusing — this is
        # a persistent block, not a timing artifact.
        for _ in range(3):
            adapter._baton.enqueue_dispatch_retry()
        await _until(lambda: adapter._baton.inbox.empty())
        await asyncio.sleep(0.05)  # settle one loop iteration
        assert len(recorder.dispatched) == 2, "cap 2 must keep refusing"
        states = adapter._baton.get_sheet_state
        assert states("job", 3) is not None
        assert states("job", 3).status.value == "pending"
        assert states("job", 4).status.value == "pending"

        # The exact operation reload_configuration performs on a cap change.
        adapter.sync_model_concurrency({"alpha:m1": 4})
        await _until(lambda: len(recorder.dispatched) == 4)
        assert {sn for _, sn in recorder.dispatched} == {1, 2, 3, 4}
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await adapter.shutdown()


async def test_lowered_model_cap_never_cancels_and_blocks_new_dispatch(
    tmp_path: Path,
) -> None:
    """4 running at cap 4; lowering to 1 cancels nothing and admits nothing.

    Lower semantics (#408, #231): in-flight sheets are never cancelled; new
    dispatch waits until the running count drains below the new cap.
    """
    adapter = BatonAdapter(max_concurrent_sheets=10)
    recorder = _DispatchRecorder()
    adapter._dispatch_callback = recorder
    adapter.register_job(
        "job", [_sheet(i) for i in range(1, 7)], {i: [] for i in range(1, 7)}
    )
    adapter.sync_model_concurrency({"alpha:m1": 4})

    loop_task = asyncio.create_task(adapter.run())
    try:
        await _until(lambda: len(recorder.dispatched) == 4)
        running = [1, 2, 3, 4]

        adapter.sync_model_concurrency({"alpha:m1": 1})  # lower under load
        for _ in range(3):
            adapter._baton.enqueue_dispatch_retry()
        await _until(lambda: adapter._baton.inbox.empty())
        await asyncio.sleep(0.05)
        assert len(recorder.dispatched) == 4, "lowered cap must not admit"

        for sn in running:
            state = adapter._baton.get_sheet_state("job", sn)
            assert state is not None
            assert state.status.value == "dispatched", (
                "lowering a cap must never cancel in-flight sheets"
            )
        for sn in (5, 6):
            state = adapter._baton.get_sheet_state("job", sn)
            assert state is not None
            assert state.status.value == "pending"
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await adapter.shutdown()


async def test_global_sheet_ceiling_resize_flows_through_real_dispatch(
    tmp_path: Path,
) -> None:
    """max_concurrent_sheets 2 → 4 sheets: 2 dispatch, resize to 4 → all 4.

    Proves the adapter's construction snapshot (manager.py boot value) is
    NOT frozen: the live read at dispatch.py:216 sees the resized ceiling.
    Two distinct model keys keep the per-model cap out of the way.
    """
    adapter = BatonAdapter(max_concurrent_sheets=2)
    recorder = _DispatchRecorder()
    adapter._dispatch_callback = recorder
    sheets = [_sheet(1), _sheet(2), _sheet(3, model="m2"), _sheet(4, model="m2")]
    adapter.register_job("job", sheets, {i: [] for i in range(1, 5)})
    adapter.sync_model_concurrency({"alpha:m1": 9, "alpha:m2": 9})

    loop_task = asyncio.create_task(adapter.run())
    try:
        await _until(lambda: len(recorder.dispatched) == 2)
        adapter._baton.enqueue_dispatch_retry()
        await _until(lambda: adapter._baton.inbox.empty())
        await asyncio.sleep(0.05)
        assert len(recorder.dispatched) == 2, "global ceiling must hold at 2"

        # The exact operation reload_configuration performs on a ceiling change.
        adapter.set_max_concurrent_sheets(4)
        await _until(lambda: len(recorder.dispatched) == 4)
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await adapter.shutdown()


def test_cap_sync_removes_vanished_profile_entries() -> None:
    """sync_model_concurrency REMOVES keys for disappeared profiles."""
    adapter = BatonAdapter()
    adapter.sync_model_concurrency({"alpha:m1": 2, "gone:m1": 3})
    diff = adapter.sync_model_concurrency({"alpha:m1": 4})

    snapshot = adapter.model_concurrency_snapshot()
    assert snapshot == {"alpha:m1": 4}
    assert "gone:m1" not in snapshot
    assert diff["removed"] == ["gone:m1"]
    assert diff["changed"] == ["alpha:m1"]


# ─── Manager: the single reload path ──────────────────────────────────


async def test_reload_configuration_applies_caps_sheets_and_gate(
    tmp_path: Path,
) -> None:
    """One reload: gate + sheet ceiling + scheduler + model caps all move."""
    org = _profile_dir(tmp_path)
    config = _load_config(_config_file(tmp_path, max_concurrent_jobs=3,
                                       max_concurrent_sheets=2))
    manager = _manager(tmp_path, config=config, org_dir=org, with_scheduler=True)
    adapter = manager._baton_adapter
    assert adapter is not None
    old_gate = manager._concurrency_semaphore

    result = await manager.reload_configuration("test")

    assert result.success is True
    assert result.config_generation == 2
    # Nothing changed in the file yet — applied lists only real effects.
    assert not result.applied or all(
        entry.startswith("instrument_profiles") for entry in result.applied
    )

    # ── Raise everything through the file, reload, verify live objects ──
    _config_file(tmp_path, max_concurrent_jobs=6, max_concurrent_sheets=5)
    (org / "alpha.yaml").write_text(_profile_yaml(max_concurrent=4))
    raised = await manager.reload_configuration("test")

    assert raised.success is True
    assert raised.config_generation == 3
    assert adapter.max_concurrent_sheets == 5
    assert any("max_concurrent_sheets: 2→5" in e for e in raised.applied)
    # Gate resized IN PLACE (#231) — same object, new limit.
    assert manager._concurrency_semaphore is old_gate
    assert manager.config.max_concurrent_jobs == 6
    # Scheduler snapshot trap closed: live object resized.
    assert manager._scheduler_instance is not None
    assert manager._scheduler_instance._max_concurrent == 5
    # Live caps through the adapter snapshot (dispatch's source of truth).
    assert adapter.model_concurrency_snapshot().get("alpha:m1") == 4

    # ── Removed profile → cap entry gone (removal, not just overwrite) ──
    (org / "alpha.yaml").unlink()
    removed = await manager.reload_configuration("test")
    assert removed.success is True
    assert "alpha:m1" not in adapter.model_concurrency_snapshot()
    assert manager._instrument_registry.get("alpha") is None


async def test_reload_configuration_fail_closed_on_invalid_config(
    tmp_path: Path,
) -> None:
    """Invalid file: nothing applied, generation frozen, error reported."""
    org = _profile_dir(tmp_path)
    cfg_path = _config_file(tmp_path, max_concurrent_jobs=3)
    config = _load_config(cfg_path)
    manager = _manager(tmp_path, config=config, org_dir=org)

    ok = await manager.reload_configuration("test")
    assert ok.success is True
    generation_before = manager.config_generation

    cfg_path.write_text("max_concurrent_jobs: not-a-number\n")
    failed = await manager.reload_configuration("test")

    assert failed.success is False
    assert failed.error is not None
    assert failed.declined
    assert manager.config_generation == generation_before
    assert manager.config.max_concurrent_jobs == 3
    assert manager.config is config or manager.config.max_concurrent_jobs == 3


async def test_reload_configuration_restart_only_fields_keep_running_values(
    tmp_path: Path,
) -> None:
    """socket/pid/state-db changes are declined; live config keeps reality."""
    org = _profile_dir(tmp_path)
    other_socket = tmp_path / "other.sock"
    cfg_path = _config_file(tmp_path)
    config = _load_config(cfg_path)
    running_socket = config.socket.path
    manager = _manager(tmp_path, config=config, org_dir=org)

    cfg_path.write_text(yaml.dump({"socket": {"path": str(other_socket)}}))
    result = await manager.reload_configuration("test")

    assert result.success is True
    assert any(e.startswith("socket:") for e in result.declined)
    assert manager.config.socket.path == running_socket, (
        "restart-only change must not silently re-point the running socket"
    )


async def test_reload_declines_when_config_file_missing_or_unrecorded(
    tmp_path: Path,
) -> None:
    org = _profile_dir(tmp_path)
    config = DaemonConfig(state_db_path=tmp_path / "s.db")  # config_file None
    manager = _manager(tmp_path, config=config, org_dir=org)
    no_file = await manager.reload_configuration("test")
    assert no_file.success is False
    assert no_file.config_generation == 1

    cfg_path = _config_file(tmp_path)
    config2 = _load_config(cfg_path)
    manager2 = _manager(tmp_path, config=config2, org_dir=org)
    cfg_path.unlink()
    missing = await manager2.reload_configuration("test")
    assert missing.success is False
    assert "missing" in (missing.error or "")
    assert manager2.config_generation == 1


async def test_edited_profile_gate_keys_apply_live_397(tmp_path: Path) -> None:
    """#397 reproducer shape: an edited profile's trust-gate keys apply live.

    The reported case: editing a profile's startup-gate keys (e.g.
    ``["Enter"]`` → ``["Down", "Enter"]``) required a conductor restart.
    Through the reload path the edit must reach the live registry.
    """
    org = _profile_dir(tmp_path)
    (org / "alpha.yaml").write_text(
        _profile_yaml(max_concurrent=2, gate_keys=["Enter"])
    )
    config = _load_config(_config_file(tmp_path))
    manager = _manager(tmp_path, config=config, org_dir=org)

    await manager.reload_configuration("test")
    profile = manager._instrument_registry.get("alpha")
    assert profile is not None
    assert profile.cli.interactive is not None
    assert profile.cli.interactive.startup_gates[0].keys == ["Enter"]

    (org / "alpha.yaml").write_text(
        _profile_yaml(max_concurrent=2, gate_keys=["Down", "Enter"])
    )
    await manager.reload_configuration("test")
    edited = manager._instrument_registry.get("alpha")
    assert edited is not None
    assert edited.cli.interactive is not None
    assert edited.cli.interactive.startup_gates[0].keys == ["Down", "Enter"]


async def test_status_exposes_generation_and_live_caps(tmp_path: Path) -> None:
    org = _profile_dir(tmp_path)
    (org / "alpha.yaml").write_text(_profile_yaml(max_concurrent=7))
    config = _load_config(_config_file(tmp_path))
    manager = _manager(tmp_path, config=config, org_dir=org)

    status = await manager.get_daemon_status()
    assert status["config_generation"] == 1
    assert status["config_loaded_at"] is not None
    assert status["last_config_reload"] is None
    assert status["model_concurrency"].get("alpha:m1") == 7

    result = await manager.reload_configuration("test")
    status2 = await manager.get_daemon_status()
    assert status2["config_generation"] == result.config_generation == 2
    assert status2["last_config_reload"]["success"] is True
    assert status2["last_config_reload"]["reason"] == "test"


# ─── Process delegation: SIGHUP ≡ IPC ≡ watcher ────────────────────────


def _result(success: bool = True, generation: int = 2) -> ConfigReloadResult:
    return ConfigReloadResult(
        success=success,
        reason="stub",
        config_generation=generation,
        applied=["max_concurrent_jobs: 3→8"] if success else [],
        declined=[] if success else ["reload_failed: stub"],
        error=None if success else "stub failure",
    )


class _StubManager:
    """Records reload calls; mimics the manager surface the delegate uses."""

    def __init__(self, config: DaemonConfig) -> None:
        self.config = config
        self.reload_calls: list[tuple[str, str | None]] = []
        self._next = _result()

    async def reload_configuration(
        self, reason: str, *, profile: str | None = None
    ) -> ConfigReloadResult:
        self.reload_calls.append((reason, profile))
        return self._next


async def test_sighup_delegates_to_single_reload_path(tmp_path: Path) -> None:
    """SIGHUP is a trigger, not an orchestrator: it calls the one path."""
    config = _load_config(_config_file(tmp_path))
    dp = DaemonProcess(config, start_profile="dev")
    stub = _StubManager(config)
    dp._manager = stub

    await dp._handle_sighup()

    assert stub.reload_calls == [("sighup", "dev")]
    assert dp._config is config  # synced from manager.config

    # Start profile is threaded so a reload never drops the --profile overlay.
    assert stub.reload_calls[0][1] == "dev"


async def test_ipc_reload_handler_delegates_identically(tmp_path: Path) -> None:
    """The daemon.reload RPC handler funnels into the same single path,
    threading the start profile exactly like SIGHUP does."""
    from marianne.daemon.ipc.handler import RequestHandler

    config = _load_config(_config_file(tmp_path))
    dp = DaemonProcess(config, start_profile="dev")
    stub = _StubManager(config)
    dp._manager = stub

    handler = RequestHandler()
    dp._register_methods(handler, stub, health=None)
    assert "daemon.reload" in handler._methods

    reload_handler = handler._methods["daemon.reload"]
    payload = await reload_handler({"reason": "ipc"}, None)
    assert payload["success"] is True
    assert payload["config_generation"] == 2
    assert stub.reload_calls == [("ipc", "dev")]


async def test_daemon_reload_over_real_socket(tmp_path: Path) -> None:
    """Real DaemonServer + real manager + DaemonClient: reload advances
    generation, fail-closed keeps it, status exposes the #408 fields."""
    from marianne.daemon.ipc.client import DaemonClient
    from marianne.daemon.ipc.handler import RequestHandler
    from marianne.daemon.ipc.server import DaemonServer
    from marianne.daemon.types import DaemonStatus

    org = _profile_dir(tmp_path)
    cfg_path = _config_file(tmp_path, max_concurrent_jobs=3)
    config = _load_config(cfg_path)
    socket_path = tmp_path / "conductor.sock"
    config = config.model_copy(
        update={"socket": config.socket.model_copy(update={"path": socket_path})}
    )
    manager = _manager(tmp_path, config=config, org_dir=org)

    dp = DaemonProcess(config)
    dp._manager = manager  # normally wired in run()
    handler = RequestHandler()
    dp._register_methods(handler, manager, health=None)
    server = DaemonServer(socket_path, handler)
    await server.start()
    client = DaemonClient(socket_path, timeout=5.0)
    try:
        status_before = await client.status()
        assert isinstance(status_before, DaemonStatus)
        assert status_before.config_generation == 1
        assert status_before.model_concurrency.get("alpha:m1") == 2

        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 9}))
        result = await client.reload_config(reason="ipc-test")
        assert result["success"] is True
        assert result["config_generation"] == 2
        assert any("max_concurrent_jobs" in e for e in result["applied"])

        status_after = await client.status()
        assert status_after.config_generation == 2
        assert status_after.last_config_reload is not None
        assert status_after.last_config_reload.reason == "ipc-test"
        assert manager.config.max_concurrent_jobs == 9

        # Fail-closed over the wire: invalid config leaves generation intact.
        cfg_path.write_text("max_concurrent_jobs: []\n")
        failed = await client.reload_config(reason="ipc-test")
        assert failed["success"] is False
        assert failed["error"]
        still = await client.status()
        assert still.config_generation == 2
        assert manager.config.max_concurrent_jobs == 9
    finally:
        await client.close()
        await server.stop()


# ─── Watcher ───────────────────────────────────────────────────────────


class _ReloadRecorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, reason: str) -> ConfigReloadResult:
        self.calls.append(reason)
        return _result()


async def test_watcher_triggers_on_config_and_profile_changes(tmp_path: Path) -> None:
    from marianne.daemon.hot_reload import ConfigWatcher

    org = _profile_dir(tmp_path)
    cfg_path = _config_file(tmp_path)
    recorder = _ReloadRecorder()
    enabled = True
    watcher = ConfigWatcher(
        config_file=cfg_path,
        profile_dirs=[org],
        reload_fn=recorder,
        enabled_fn=lambda: enabled,
        debounce_fn=lambda: 0.05,
        poll_interval=0.05,
    )
    await watcher.start()
    try:
        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 4}))
        await _until(lambda: len(recorder.calls) == 1, timeout=5.0)
        assert recorder.calls == ["watcher"]

        (org / "alpha.yaml").write_text(_profile_yaml(max_concurrent=5))
        await _until(lambda: len(recorder.calls) == 2, timeout=5.0)

        # Off-switch honored live (#408 hot_reload.enabled).
        enabled = False
        cfg_path.write_text(yaml.dump({"max_concurrent_jobs": 5}))
        await asyncio.sleep(0.3)  # bounded negative window
        assert len(recorder.calls) == 2, "disabled watcher must not fire"

        # Re-enabling resumes watching.
        enabled = True
        await _until(lambda: len(recorder.calls) == 3, timeout=5.0)
    finally:
        await watcher.stop()
    assert not watcher.is_running


# ─── Broken profile files: keep-previous + declined + partial (P1) ─────


_BROKEN_YAML = "name: alpha\n  bad indent: [\n"


async def test_broken_profile_keeps_previous_entry_and_caps(
    tmp_path: Path,
) -> None:
    """SC1 (commission-exact sequence, real manager): a profile that
    breaks mid-edit is NOT removed — entry retained, cap retained,
    declined names the file with the loader's reason, partial=True,
    success=True, generation advanced; repair → entry updated."""
    org = _profile_dir(tmp_path)
    config = _load_config(_config_file(tmp_path))
    manager = _manager(tmp_path, config=config, org_dir=org)

    warm = await manager.reload_configuration("warm")
    assert warm.success is True
    assert warm.partial is False
    old = manager._instrument_registry.get("alpha")
    assert old is not None
    old_sha = old._source_sha256
    assert manager._baton_adapter is not None
    assert manager._baton_adapter.model_concurrency_snapshot().get(
        "alpha:m1"
    ) == 2

    (org / "alpha.yaml").write_text(_BROKEN_YAML)
    result = await manager.reload_configuration("edit-accident")

    assert result.success is True  # applied, not fail-closed
    assert result.partial is True  # honest qualification
    assert result.config_generation == warm.config_generation + 1
    assert result.declined, "the broken file must be reported"
    entry = result.declined[0]
    assert entry.startswith("profile load failed (")
    assert "alpha.yaml" in entry
    assert "instrument_yaml_parse_error" in entry
    assert "previous entry 'alpha' retained" in entry

    kept = manager._instrument_registry.get("alpha")
    assert kept is not None, "entry must NOT be silently removed"
    assert kept._source_sha256 == old_sha  # the previous bytes, not new
    assert manager._baton_adapter.model_concurrency_snapshot().get(
        "alpha:m1"
    ) == 2  # cap key retained

    # Repair: the next reload restores and updates the entry.
    (org / "alpha.yaml").write_text(_profile_yaml(max_concurrent=5))
    repaired = await manager.reload_configuration("repair")
    assert repaired.success is True
    assert repaired.partial is False
    assert not repaired.declined
    fresh = manager._instrument_registry.get("alpha")
    assert fresh is not None
    assert fresh._source_sha256 != old_sha
    assert manager._baton_adapter.model_concurrency_snapshot().get(
        "alpha:m1"
    ) == 5


async def test_fresh_profile_wins_over_kept_previous_on_name_collision(
    tmp_path: Path,
) -> None:
    """A healthy fresh profile owning the name beats the kept previous
    entry (new truth wins); the broken file is still reported."""
    org = _profile_dir(tmp_path)
    venue = tmp_path / "venue"
    venue.mkdir()
    config = _load_config(_config_file(tmp_path))
    manager = _manager(tmp_path, config=config, org_dir=org)

    await manager.reload_configuration("warm")

    (org / "alpha.yaml").write_text(_BROKEN_YAML)
    (venue / "override.yaml").write_text(_profile_yaml(max_concurrent=9))
    result = await manager.reload_configuration("collision")

    assert result.success is True
    assert result.partial is True
    assert any("alpha.yaml" in d for d in result.declined)
    assert all("retained" not in d for d in result.declined)
    live = manager._instrument_registry.get("alpha")
    assert live is not None
    assert live._source_path == (venue / "override.yaml").resolve()
    assert manager._baton_adapter is not None
    assert manager._baton_adapter.model_concurrency_snapshot().get(
        "alpha:m1"
    ) == 9


async def test_keep_previous_keys_on_source_path_not_filename(
    tmp_path: Path,
) -> None:
    """Merge key is _source_path: a file whose declared name differs
    from its filename still gets its previous entry retained when the
    file breaks (name↔file are independent; legal name overrides)."""
    org = tmp_path / "org"
    org.mkdir()
    (org / "gamma.yaml").write_text(_profile_yaml(name="delta"))
    config = _load_config(_config_file(tmp_path))
    manager = _manager(tmp_path, config=config, org_dir=org)

    await manager.reload_configuration("warm")
    assert manager._instrument_registry.get("delta") is not None

    (org / "gamma.yaml").write_text(_BROKEN_YAML)
    result = await manager.reload_configuration("edit-accident")

    assert result.partial is True
    assert any(
        "gamma.yaml" in d and "previous entry 'delta' retained" in d
        for d in result.declined
    )
    kept = manager._instrument_registry.get("delta")
    assert kept is not None, "entry keyed by source path, not filename"
    assert manager._baton_adapter is not None
    assert manager._baton_adapter.model_concurrency_snapshot().get(
        "delta:m1"
    ) == 2


def test_boot_and_plain_load_contract_unchanged(tmp_path: Path) -> None:
    """SC2/SC3: boot semantics and the load_all_profiles contract stay
    tolerant — a broken file is skipped with a warning and absent from
    the registry; only the failures-aware variant reports it."""
    from marianne.instruments.loader import (
        load_all_profiles,
        load_all_profiles_with_failures,
    )

    org = _profile_dir(tmp_path)
    (org / "alpha.yaml").write_text(_BROKEN_YAML)
    venue = tmp_path / "venue"

    profiles = load_all_profiles(organization_dir=org, venue_dir=venue)
    assert "alpha" not in profiles  # boot: skip-with-warning

    merged, failures = load_all_profiles_with_failures(
        organization_dir=org, venue_dir=venue
    )
    assert merged == profiles  # same tolerant dict contract
    assert [f.path.name for f in failures] == ["alpha.yaml"]
    assert failures[0].reason_code == "instrument_yaml_parse_error"
    assert failures[0].path == (org / "alpha.yaml").resolve()


# ─── conductor-status: pure JSON stdout + socket-only pid (P2) ─────────


def _daemon_payload(pid: int) -> dict[str, Any]:
    return {
        "pid": pid,
        "uptime_seconds": 5.0,
        "running_jobs": 0,
        "total_jobs_active": 0,
        "memory_usage_mb": 12.0,
        "version": "test",
        "protocol_version": 1,
        "config_generation": 3,
        "model_concurrency": {"alpha:m1": 2},
    }


class TestConductorStatusJsonPurity:
    """SC4/SC5: ``--json`` stdout is one parseable JSON document; an
    explicit ``--socket`` never reads/asserts/unlinks a pid file."""

    def _patch_run(self, monkeypatch, value) -> None:
        import marianne.daemon.process as process_module

        def _fake_run(coro):
            coro.close()
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(process_module.asyncio, "run", _fake_run)

    def test_socket_only_json_is_pure_and_pid_file_untouched(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        import marianne.daemon.process as process_module

        self._patch_run(
            monkeypatch,
            ({"uptime_seconds": 5.0}, {"status": "ready"}, _daemon_payload(4242)),
        )
        read_calls: list[Path] = []

        def _record_read(pid_file: Path) -> int | None:
            read_calls.append(pid_file)
            return None  # simulate: no live pid anywhere

        monkeypatch.setattr(process_module, "_read_pid", _record_read)

        process_module.get_conductor_status(
            socket_path=tmp_path / "clone.sock", as_json=True,
        )

        captured = capsys.readouterr()
        payload = json.loads(captured.out)  # SC4: whole stdout parses
        assert payload["pid"] == 4242  # SC5: daemon-authoritative pid
        assert payload["config_generation"] == 3
        assert read_calls == []  # SC5: pid file never read

    def test_socket_only_json_failure_empty_stdout_exit_1(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        import pytest

        self._patch_run(monkeypatch, OSError("socket nowhere"))

        import marianne.daemon.process as process_module

        with pytest.raises(process_module.typer.Exit):
            process_module.get_conductor_status(
                socket_path=tmp_path / "clone.sock", as_json=True,
            )
        captured = capsys.readouterr()
        assert captured.out == ""  # machine surface stays empty
        assert captured.err  # human reason on stderr

    def test_socket_only_human_reports_daemon_pid(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        import marianne.daemon.process as process_module

        self._patch_run(
            monkeypatch,
            ({"uptime_seconds": 5.0}, {"status": "ready"}, _daemon_payload(777)),
        )
        read_calls: list[Path] = []
        monkeypatch.setattr(
            process_module, "_read_pid",
            lambda pid_file: read_calls.append(pid_file) or None,
        )

        process_module.get_conductor_status(socket_path=tmp_path / "c.sock")

        captured = capsys.readouterr()
        assert "PID 777" in captured.out
        assert read_calls == []

    def test_json_with_explicit_pid_file_still_pure(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        import marianne.daemon.process as process_module

        pid_file = tmp_path / "m.pid"
        pid_file.write_text("12345")
        self._patch_run(
            monkeypatch,
            (
                {"uptime_seconds": 5.0},
                {"status": "ready"},
                _daemon_payload(4242),
            ),
        )
        monkeypatch.setattr(
            process_module, "_pid_alive", lambda pid: True,
        )

        process_module.get_conductor_status(
            pid_file=pid_file, socket_path=None, as_json=True,
        )

        captured = capsys.readouterr()
        payload = json.loads(captured.out)  # pure even with pid file
        assert payload["pid"] == 4242  # daemon payload overrides file pid

    def test_json_not_running_stdout_empty_stderr_says_so(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        import pytest

        import marianne.daemon.process as process_module

        pid_file = tmp_path / "m.pid"  # absent — not running
        with pytest.raises(process_module.typer.Exit):
            process_module.get_conductor_status(
                pid_file=pid_file, socket_path=None, as_json=True,
            )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "not running" in captured.err
