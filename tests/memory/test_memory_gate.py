"""Slice 1: the memory dispatch gate, without the adapter (real gateway + endpoint + files)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from marianne.core.config.instruments import InstrumentProfile
from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.memory_gate import (
    MemoryConfigCollision,
    MemoryDispatchGate,
    MemoryGateError,
    SheetIdentity,
    can_deliver,
    merge_mcp_config,
)
from marianne.memory.errors import MemoryBackendError

BUILTINS = Path(__file__).resolve().parents[2] / "src" / "marianne" / "instruments" / "builtins"
SHEET = SheetIdentity(num=1, movement=1, phase="work")
T = 20.0


def cfg(**kw: Any) -> MemoryConfig:
    return MemoryConfig.model_validate(kw)


def two() -> MemoryConfig:
    return cfg(backends={
        "notes": {"system": "files", "scope": "project/a"},
        "ledger": {"system": "files", "scope": "project/b"},
    })


def make(root: Path) -> MemoryDispatchGate:
    run = root / "run"
    run.mkdir(mode=0o700, exist_ok=True)
    return MemoryDispatchGate(
        run_dir=run, memory_root=root / "files", audit_dir=root / "audit", user="ada",
        clock=time.time,
    )


def drive(root: Path, body: Callable[[MemoryDispatchGate], Awaitable[None]]) -> None:
    async def main() -> None:
        gate = make(root)
        try:
            await asyncio.wait_for(body(gate), T)
        finally:
            await asyncio.wait_for(gate.shutdown(), T)

    asyncio.run(main())


def sockets(root: Path) -> list[str]:
    d = root / "run" / "s"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def deadline() -> float:
    return time.time() + 60


def test_no_memory_key_allocates_nothing(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", None)
        assert await gate.prepare("j", SHEET, "d1", deadline()) is None
        assert await gate.prepare("unknown-job", SHEET, "d2", deadline()) is None
        assert gate._gateway is None and gate._endpoint is None and gate._audit is None
        assert not (short_tmp / "audit").exists()
        assert sockets(short_tmp) == []

    drive(short_tmp, body)


def test_memory_with_no_active_attachment_returns_none_and_stays_lazy(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        c = cfg(backends={"only2": {"system": "files", "phases": ["2"]}})
        gate.register_job("j", c, {1: "Plan"})
        assert await gate.prepare("j", SHEET, "d1", deadline()) is None
        assert gate._gateway is None

    drive(short_tmp, body)


def test_prepare_opens_sessions_and_returns_delivery(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        d = await gate.prepare("j", SHEET, "d1", deadline())
        assert d is not None
        assert gate._gateway is not None  # created lazily by this dispatch
        assert set(d.mcp_entries) == {"memory-notes", "memory-ledger"}
        assert [s.attachment for s in d.sessions] == ["notes", "ledger"]
        assert len(sockets(short_tmp)) == 2
        for s in d.sessions:
            assert s.socket_path.exists()
            assert stat.S_IMODE(os.stat(s.socket_path).st_mode) == 0o600
        assert "memory-notes" in d.manifest and "Memory Available" in d.manifest
        blob = json.dumps(d.mcp_entries)
        for secret in ("person:", "project/a", "ada"):
            assert secret not in blob
        assert not any(s.session_id in blob for s in d.sessions)
        report = await gate.teardown("j", "d1")
        assert report.clean
        assert sockets(short_tmp) == []
        assert _live_gateway_sessions(gate) == 0

    drive(short_tmp, body)


def test_teardown_is_idempotent(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        assert await gate.prepare("j", SHEET, "d1", deadline()) is not None
        assert (await gate.teardown("j", "d1")).clean
        assert (await gate.teardown("j", "d1")).clean
        assert (await gate.teardown("j", "never-opened")).clean
        assert gate.open_dispatches == 0

    drive(short_tmp, body)


class _Boom(RuntimeError):
    pass


def test_second_attachment_failure_rolls_back_the_first(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        endpoint = gate._ensure()[1]
        real = endpoint.open_session
        calls = {"n": 0}

        async def flaky(**kw: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 2:
                raise _Boom("injected: disk gone")
            return await real(**kw)

        endpoint.open_session = flaky  # type: ignore[method-assign]
        with pytest.raises(MemoryGateError) as info:
            await gate.prepare("j", SHEET, "d1", deadline())
        assert info.value.attachment == "ledger"
        assert "injected: disk gone" in str(info.value)
        assert calls["n"] == 2
        assert sockets(short_tmp) == []
        assert gate.open_dispatches == 0
        assert _live_gateway_sessions(gate) == 0

    drive(short_tmp, body)


def test_unknown_backend_system_is_refused_with_attachment_name(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        c = MemoryConfig.model_construct(  # bypass nothing: system is a free string
            backends=cfg(backends={"x": {"system": "mem0"}}).backends,
            defaults=cfg().defaults,
        )
        gate.register_job("j", c)
        with pytest.raises(MemoryGateError, match="'x'.*mem0"):
            await gate.prepare("j", SHEET, "d1", deadline())
        assert sockets(short_tmp) == [] and gate.open_dispatches == 0

    drive(short_tmp, body)


def test_unsafe_run_dir_is_a_typed_failure_with_backend_reason(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        os.chmod(short_tmp / "run", 0o777)
        gate.register_job("j", two())
        with pytest.raises(MemoryGateError) as info:
            await gate.prepare("j", SHEET, "d1", deadline())
        assert "run_dir_unsafe" in str(info.value)
        assert info.value.attachment == "notes"
        assert gate.open_dispatches == 0

    drive(short_tmp, body)


def test_expired_deadline_refused_before_anything_opens(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        with pytest.raises(MemoryGateError, match="deadline"):
            await gate.prepare("j", SHEET, "d1", time.time() - 1)
        assert gate._gateway is None

    drive(short_tmp, body)


def test_duplicate_dispatch_id_refused(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        assert await gate.prepare("j", SHEET, "d1", deadline()) is not None
        with pytest.raises(MemoryGateError, match="already open"):
            await gate.prepare("j", SHEET, "d1", deadline())
        assert len(sockets(short_tmp)) == 2  # the first dispatch is untouched
        await gate.teardown("j", "d1")

    drive(short_tmp, body)


def test_deregister_job_tears_down_open_sessions(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        assert await gate.prepare("j", SHEET, "d1", deadline()) is not None
        gate.deregister_job("j")
        await gate.drain()
        assert gate.open_dispatches == 0 and sockets(short_tmp) == []
        assert await gate.prepare("j", SHEET, "d2", deadline()) is None  # forgotten

    drive(short_tmp, body)


def test_shutdown_closes_everything(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        await gate.prepare("j", SHEET, "d1", deadline())
        await gate.prepare("j", SheetIdentity(2, 1, "work"), "d2", deadline())
        assert len(sockets(short_tmp)) == 4
        report = await gate.shutdown()
        assert report.clean and sockets(short_tmp) == []
        with pytest.raises(MemoryGateError, match="shut down"):
            await gate.prepare("j", SHEET, "d3", deadline())

    drive(short_tmp, body)


def test_teardown_never_raises_and_reports(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        await gate.prepare("j", SHEET, "d1", deadline())
        endpoint = gate._endpoint
        assert endpoint is not None

        async def bad(sid: str) -> int:
            raise _Boom("close failed")

        real = endpoint.close_session
        endpoint.close_session = bad  # type: ignore[method-assign,assignment]
        report = await gate.teardown("j", "d1")
        assert not report.clean and "close failed" in report.errors[0]
        endpoint.close_session = real  # type: ignore[method-assign]
        assert sockets(short_tmp) == []  # the fallback unbound them anyway

    drive(short_tmp, body)


# ------------------------------------------------------------------ merge_mcp_config


def test_merge_includes_existing_servers_and_entries(tmp_path: Path) -> None:
    existing = tmp_path / "tech.json"
    existing.write_text(json.dumps({"mcpServers": {"fs": {"command": "proxy"}}}))
    before = existing.read_text()
    out = merge_mcp_config(existing, {"memory-a": {"command": "py", "args": ["x"]}}, tmp_path / "d")
    data = json.loads(out.read_text())
    assert set(data["mcpServers"]) == {"fs", "memory-a"}
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert existing.read_text() == before
    assert [p.name for p in (tmp_path / "d").iterdir()] == [out.name]  # no temp left


def test_merge_without_existing(tmp_path: Path) -> None:
    out = merge_mcp_config(None, {"memory-a": {"command": "py"}}, tmp_path)
    assert json.loads(out.read_text()) == {"mcpServers": {"memory-a": {"command": "py"}}}


def test_merge_refuses_collision_and_writes_nothing(tmp_path: Path) -> None:
    existing = tmp_path / "tech.json"
    existing.write_text(json.dumps({"mcpServers": {"memory-a": {"command": "mine"}}}))
    dest = tmp_path / "d"
    with pytest.raises(MemoryConfigCollision):
        merge_mcp_config(existing, {"memory-a": {"command": "py"}}, dest)
    assert not dest.exists() or list(dest.iterdir()) == []
    assert json.loads(existing.read_text())["mcpServers"]["memory-a"]["command"] == "mine"


def test_merge_refuses_unreadable_existing(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(MemoryGateError):
        merge_mcp_config(bad, {"memory-a": {}}, tmp_path / "d")


def test_merge_failure_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: Any, dst: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        merge_mcp_config(None, {"memory-a": {}}, tmp_path / "d")
    assert list((tmp_path / "d").iterdir()) == []


def test_gate_merge_config_is_removed_by_teardown(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        d = await gate.prepare("j", SHEET, "d1", deadline())
        assert d is not None
        path = await gate.merge_config("j", "d1", d, None, short_tmp / "ws")
        assert path.exists() and set(json.loads(path.read_text())["mcpServers"]) == set(
            d.mcp_entries
        )
        await gate.teardown("j", "d1")
        assert not path.exists()

    drive(short_tmp, body)


# ------------------------------------------------------------------ can_deliver


def profile(name: str) -> InstrumentProfile:
    return InstrumentProfile.model_validate(yaml.safe_load((BUILTINS / f"{name}.yaml").read_text()))


class _WithSetter:
    def set_mcp_config(self, p: Path | None) -> None:
        pass


def test_can_deliver_accepts_flag_and_workspace_file_instruments() -> None:
    assert can_deliver(_WithSetter(), profile("claude-code")) is None
    assert can_deliver(_WithSetter(), profile("gemini-cli")) is None


def test_can_deliver_refuses_cli_instrument() -> None:
    reason = can_deliver(_WithSetter(), profile("cli"))
    assert reason is not None and "mcp_config_flag" in reason


def test_can_deliver_refuses_backend_without_setter_and_missing_profile() -> None:
    assert "set_mcp_config" in (can_deliver(object(), profile("claude-code")) or "")
    assert can_deliver(_WithSetter(), None) is not None


# ------------------------------------------------------------------ F1: orphaned sessions


def _live_gateway_sessions(gate: MemoryDispatchGate) -> int:
    assert gate._gateway is not None
    return sum(
        1 for s in gate._gateway.sessions._sessions.values()
        if s.state.value.lower() not in ("closed", "revoked", "expired")
    )


async def _slow_second_open(gate: MemoryDispatchGate) -> None:
    endpoint = gate._ensure()[1]
    real = endpoint.open_session
    calls = {"n": 0}

    async def slow(**kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            await asyncio.sleep(0.3)
        return await real(**kw)

    endpoint.open_session = slow  # type: ignore[method-assign]


async def _deliver(gate: MemoryDispatchGate, root: Path) -> Any:
    return await gate.deliver(
        "j", SHEET, timeout_seconds=30, existing_mcp_config=None, workspace=root / "ws",
    )


@pytest.mark.parametrize("how", ["deregister", "shutdown"])
def test_deregister_or_shutdown_mid_open_leaves_nothing(short_tmp: Path, how: str) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        await _slow_second_open(gate)
        task = asyncio.create_task(_deliver(gate, short_tmp))
        await asyncio.sleep(0.15)  # the second open_session is in flight
        if how == "deregister":
            gate.deregister_job("j")
        else:
            await gate.shutdown()
        with pytest.raises(MemoryGateError):
            await asyncio.wait_for(task, T)
        await gate.drain()
        endpoint = gate._endpoint
        assert endpoint is not None
        assert endpoint._bound == {}
        assert sockets(short_tmp) == []
        assert _live_gateway_sessions(gate) == 0
        assert gate.open_dispatches == 0

    drive(short_tmp, body)


def test_prepare_refused_once_shutdown_has_begun(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        await gate.shutdown()
        with pytest.raises(MemoryGateError):
            await gate.prepare("j", SHEET, "dX", deadline())
        assert sockets(short_tmp) == []

    drive(short_tmp, body)


def test_entry_build_failure_tears_down(short_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import marianne.daemon.baton.memory_gate as mg

    def boom(*a: Any, **k: Any) -> Any:
        raise _Boom("entry failed")

    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        monkeypatch.setattr(mg, "mcp_config_entry", boom)
        with pytest.raises(MemoryGateError, match="entry failed"):
            await gate.prepare("j", SHEET, "d1", deadline())
        assert sockets(short_tmp) == [] and gate.open_dispatches == 0
        assert _live_gateway_sessions(gate) == 0

    drive(short_tmp, body)


# ------------------------------------------------- D2: the session lives as long as its dispatch


def test_session_outlives_its_sheet_timeout_and_dies_with_its_dispatch(short_tmp: Path) -> None:
    async def main() -> None:
        clk = _Clock()
        run = short_tmp / "run"
        run.mkdir(mode=0o700)
        gate = MemoryDispatchGate(
            run_dir=run, memory_root=short_tmp / "files", audit_dir=short_tmp / "audit",
            user="ada", clock=clk, start_reaper=False,
        )
        try:
            gate.register_job("j", two())
            prepared = await gate.deliver(
                "j", SHEET, timeout_seconds=100, existing_mcp_config=None,
                workspace=short_tmp / "ws",
            )
            assert prepared is not None and gate._gateway is not None
            sid = gate._open[("j", prepared.dispatch_id)].session_ids[0]
            sessions = gate._gateway.sessions
            assert sessions._clock is clk  # one clock: the gate's reaches the table
            stop = asyncio.Event()
            owner = asyncio.create_task(stop.wait())
            gate.set_owner("j", prepared.dispatch_id, owner)
            for _ in range(80):  # 400 s behind a workspace lock, past the 100 s sheet timeout
                clk.now += 5
                await gate.tick()
            sessions.admit_call(sid, "read")  # still works
            stop.set()
            await gate.teardown("j", prepared.dispatch_id)
            with pytest.raises(MemoryBackendError):
                sessions.admit_call(sid, "read")  # torn down: refused
        finally:
            await gate.shutdown()

    asyncio.run(asyncio.wait_for(main(), T))


class _Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# ------------------------------------------------------------------ F5: policy is per session


def _policy_cfg(provenance: str, cap: int) -> MemoryConfig:
    # The resolver lets an attachment only tighten provenance; loosening is a score default.
    return cfg(
        backends={"notes": {
            "system": "files", "scope": "project/shared", "caps": {"recall_cap_bytes": cap},
        }},
        defaults={"provenance": provenance},
    )


@pytest.mark.parametrize("order", [("required", "optional"), ("optional", "required")])
def test_same_attachment_two_jobs_each_keep_their_own_policy(
    short_tmp: Path, order: tuple[str, str],
) -> None:
    from marianne.memory.errors import MemoryBackendError

    async def body(gate: MemoryDispatchGate) -> None:
        caps = {"required": 2048, "optional": 8192}
        sids: dict[str, str] = {}
        for n, prov in enumerate(order):
            job = f"job-{prov}"
            gate.register_job(job, _policy_cfg(prov, caps[prov]))
            d = await gate.prepare(job, SHEET, f"d{n}", deadline())
            assert d is not None
            sids[prov] = d.sessions[0].session_id
        gw = gate._gateway
        assert gw is not None
        with pytest.raises(MemoryBackendError):  # required: a bare declare is refused
            gw.declare(sids["required"], "no evidence")
        gw.declare(sids["optional"], "no evidence is fine here")  # optional: accepted
        backends: list[Any] = list(gw._backends.values())
        assert sorted(b._cfg.recall_cap_bytes for b in backends) == [2048, 8192]
        assert sorted(b._cfg.provenance for b in backends) == ["optional", "required"]

    drive(short_tmp, body)


# ------------------------------------------------------------------ F7: no fsync on the loop


def test_fsync_never_runs_on_the_event_loop_thread(
    short_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    seen: list[tuple[str, bool]] = []
    real = os.fsync
    loop_thread: list[int] = []

    def spy(fd: int) -> None:
        seen.append(("fsync", threading.get_ident() == loop_thread[0]))
        real(fd)

    monkeypatch.setattr(os, "fsync", spy)

    async def body(gate: MemoryDispatchGate) -> None:
        loop_thread.append(threading.get_ident())
        gate.register_job("j", two())
        prepared = await gate.deliver(
            "j", SHEET, timeout_seconds=30, existing_mcp_config=None,
            workspace=short_tmp / "ws",
        )
        assert prepared is not None
        await gate.teardown("j", prepared.dispatch_id)

    drive(short_tmp, body)
    assert seen, "expected audit and config fsyncs"
    assert not [s for s in seen if s[1]], "an fsync ran on the event loop thread"


# ------------------------------------------------------------------ F8: stale files after SIGKILL


def _stale_socket(path: Path) -> None:
    import socket

    s = socket.socket(socket.AF_UNIX)
    s.bind(str(path))
    s.close()  # leaves the socket file behind, bound by nobody


def test_sweep_stale_removes_only_unbound_sockets_in_our_s_dir(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        sdir = short_tmp / "run" / "s"
        sdir.mkdir(mode=0o700)
        _stale_socket(sdir / "s_dead0000000000a.sock")
        (sdir / "notes.txt").write_text("keep me")
        (sdir / "s_regular00000000b.sock").write_text("a regular file named like a socket")
        outside = short_tmp / "outside.sock"
        _stale_socket(outside)
        (sdir / "s_link0000000000c.sock").symlink_to(outside)
        gate.register_job("j", two())
        live = await gate.prepare("j", SHEET, "d1", deadline())
        assert live is not None
        removed = await gate.sweep_stale()
        assert removed == 1
        names = sorted(p.name for p in sdir.iterdir())
        assert "s_dead0000000000a.sock" not in names
        assert {"notes.txt", "s_regular00000000b.sock", "s_link0000000000c.sock"} <= set(names)
        assert outside.exists()  # a symlink is never followed
        assert all(s.socket_path.exists() for s in live.sessions)  # our own are untouched

    drive(short_tmp, body)


def test_sweep_stale_without_run_dir_or_s_dir_is_a_noop(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        assert await gate.sweep_stale() == 0

    drive(short_tmp, body)




# ------------------------------------------------------------------ D1: one ordered worker


def _slow_open(gate: MemoryDispatchGate, nth: int, seconds: float) -> None:
    """Slow the n-th gateway open_session. It runs on the gate's worker thread."""
    gw = gate._ensure()[0]
    real = gw.open_session
    calls = {"n": 0}

    def slow(**kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == nth:
            time.sleep(seconds)
        return real(**kw)

    gw.open_session = slow  # type: ignore[method-assign]


def test_cancel_during_threaded_open_leaves_no_live_session(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        _slow_open(gate, 2, 0.4)
        task = asyncio.create_task(_deliver(gate, short_tmp))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await gate.drain()
        assert _live_gateway_sessions(gate) == 0
        assert gate.open_dispatches == 0
        assert sockets(short_tmp) == []
        assert gate._endpoint is not None and gate._endpoint._bound == {}

    drive(short_tmp, body)


def test_cancel_during_config_write_leaves_no_stray_file(
    short_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marianne.daemon.baton.memory_gate as mg

    real = mg.merge_mcp_config

    def slow(*a: Any, **k: Any) -> Any:
        time.sleep(0.4)
        return real(*a, **k)

    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        monkeypatch.setattr(mg, "merge_mcp_config", slow)
        task = asyncio.create_task(_deliver(gate, short_tmp))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await gate.drain()
        ws = short_tmp / "ws"
        stray = [p for p in ws.rglob("*") if p.is_file()] if ws.exists() else []
        assert stray == []
        assert gate.open_dispatches == 0 and _live_gateway_sessions(gate) == 0

    drive(short_tmp, body)


def test_two_closes_of_one_session_write_one_sealed_row(short_tmp: Path) -> None:
    import glob
    import threading

    from marianne.memory.gateway.audit import verify_audit_file

    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        d = await gate.prepare("j", SHEET, "d1", deadline())
        assert d is not None and gate._gateway is not None
        gw = gate._gateway
        sid = d.sessions[0].session_id
        real = gw.sessions.close_if_live

        def slow(s: str) -> Any:
            time.sleep(0.2)
            return real(s)

        gw.sessions.close_if_live = slow  # type: ignore[method-assign]
        threads = [threading.Thread(target=gw.close_session, args=(sid,)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        path = glob.glob(str(short_tmp / "audit" / "d1" / f"{sid}.jsonl"))[0]
        assert Path(path).read_text().count("session_closed") == 1
        assert verify_audit_file(path).ok

    drive(short_tmp, body)


def test_parallel_delivers_in_one_workspace_keep_both_configs(
    short_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marianne.daemon.baton.memory_gate as mg

    real = mg.merge_mcp_config
    calls = {"n": 0}

    def slow_first(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.5)
        return real(*a, **k)

    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        monkeypatch.setattr(mg, "merge_mcp_config", slow_first)
        ws = short_tmp / "ws"
        a = asyncio.create_task(gate.deliver(
            "j", SheetIdentity(1, 1, "work"), timeout_seconds=30, existing_mcp_config=None,
            workspace=ws,
        ))
        await asyncio.sleep(0.1)
        b = await gate.deliver(
            "j", SheetIdentity(2, 1, "work"), timeout_seconds=30, existing_mcp_config=None,
            workspace=ws,
        )
        pa = await a
        assert pa is not None and b is not None
        assert pa.mcp_config_path.exists() and b.mcp_config_path.exists()
        assert gate.is_open("j", pa.dispatch_id) and gate.is_open("j", b.dispatch_id)

    drive(short_tmp, body)


def test_deregister_removes_stale_dispatch_dirs_but_not_live_or_symlinks(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        ws = short_tmp / "ws"
        gate.register_job("j", two())
        live = await gate.deliver(
            "j", SHEET, timeout_seconds=30, existing_mcp_config=None, workspace=ws,
        )
        assert live is not None
        root = ws / ".marianne-memory"
        stale = root / "deadbeefdeadbeef"
        stale.mkdir()
        (stale / "mcp.json").write_text("{}")
        target = short_tmp / "elsewhere"
        target.mkdir()
        (target / "keep.txt").write_text("x")
        (root / "linked").symlink_to(target)
        gate.register_job("other", two())
        gate.deregister_job("other")  # an unrelated job: nothing known about this workspace
        await gate.drain()
        assert stale.exists()
        await gate.teardown("j", live.dispatch_id)
        gate.register_job("j", two())
        again = await gate.deliver(
            "j", SheetIdentity(2, 1, "work"), timeout_seconds=30, existing_mcp_config=None,
            workspace=ws,
        )
        assert again is not None
        assert stale.exists()  # never during a live deliver
        gate.deregister_job("j")
        await gate.drain()
        assert not stale.exists()
        assert (root / "linked").is_symlink() and (target / "keep.txt").exists()
        assert not again.mcp_config_path.exists()  # its own dispatch was torn down first

    drive(short_tmp, body)


# ------------------------------------------------------------------ D3: bounded shutdown


def test_shutdown_does_not_wait_for_a_caller_task_that_outlives_prepare(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        gate.register_job("j", two())
        _slow_open(gate, 1, 0.2)

        async def caller() -> None:
            with contextlib.suppress(MemoryGateError):
                await gate.prepare("j", SHEET, "d1", deadline())
            await asyncio.sleep(30)  # the caller (the baton loop) lives on

        task = asyncio.create_task(caller())
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await asyncio.wait_for(gate.shutdown(), 5)
        assert time.monotonic() - t0 < 4
        assert not task.done()  # shutdown never cancelled or awaited the caller
        task.cancel()
        assert _live_gateway_sessions(gate) == 0 and sockets(short_tmp) == []

    asyncio.run(asyncio.wait_for(main(), T))


def test_shutdown_does_not_wait_for_a_caller_that_tore_down_inline(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        gate.register_job("j", two())
        d = await gate.prepare("j", SHEET, "d1", deadline())
        assert d is not None

        async def caller() -> None:
            await gate.teardown("j", "d1")
            await asyncio.sleep(30)

        task = asyncio.create_task(caller())
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await asyncio.wait_for(gate.shutdown(), 5)
        assert time.monotonic() - t0 < 4 and not task.done()
        task.cancel()

    asyncio.run(asyncio.wait_for(main(), T))


def test_shutdown_returns_within_its_bound_with_a_stuck_job_and_reports_it(
    short_tmp: Path,
) -> None:
    import threading

    async def main() -> None:
        run = short_tmp / "run"
        run.mkdir(mode=0o700)
        gate = MemoryDispatchGate(
            run_dir=run, memory_root=short_tmp / "files", audit_dir=short_tmp / "audit",
            user="ada", shutdown_bound_seconds=0.5,
        )
        release = threading.Event()
        try:
            gate.register_job("j", two())
            d = await gate.prepare("j", SHEET, "d1", deadline())
            assert d is not None
            gate._submit(release.wait, 10)  # a stuck worker job ahead of every close
            t0 = time.monotonic()
            report = await asyncio.wait_for(gate.shutdown(), 5)
            assert time.monotonic() - t0 < 3
            assert any("still pending" in e for e in report.errors)
        finally:
            release.set()

    asyncio.run(asyncio.wait_for(main(), T))


# ------------------------------------------------------------------ D4: listening sockets survive


def test_sweep_stale_keeps_a_listening_socket_not_bound_by_this_gate(short_tmp: Path) -> None:
    import socket

    async def body(gate: MemoryDispatchGate) -> None:
        sdir = short_tmp / "run" / "s"
        sdir.mkdir(mode=0o700)
        listening = socket.socket(socket.AF_UNIX)
        listening.bind(str(sdir / "s_listening00000a.sock"))
        listening.listen(1)
        _stale_socket(sdir / "s_dead000000000b.sock")
        try:
            assert await gate.sweep_stale() == 1
            names = {p.name for p in sdir.iterdir()}
            assert "s_listening00000a.sock" in names  # alive: must survive
            assert "s_dead000000000b.sock" not in names
        finally:
            listening.close()

    drive(short_tmp, body)


# ------------------------------------------------------------------ D5c / D5d


def test_closed_sessions_are_forgotten_but_a_late_call_hears_closed(short_tmp: Path) -> None:
    from marianne.memory.errors import NotFound, Unauthenticated

    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        d = await gate.prepare("j", SHEET, "d1", deadline())
        assert d is not None and gate._gateway is not None
        sid = d.sessions[0].session_id
        await gate.teardown("j", "d1")
        table = gate._gateway.sessions
        assert sid not in table._sessions  # dropped after its audit close was sealed
        with pytest.raises(Unauthenticated):  # "closed", not "not found"
            table.get(sid)
        with pytest.raises(NotFound):
            table.get("s-never-existed")
        assert gate._gateway.close_session(sid) is None  # a late close is a no-op

    drive(short_tmp, body)


def test_backend_is_closed_and_unregistered_when_its_last_session_closes(short_tmp: Path) -> None:
    async def body(gate: MemoryDispatchGate) -> None:
        gate.register_job("j", two())
        a = await gate.prepare("j", SHEET, "d1", deadline())
        b = await gate.prepare("j", SheetIdentity(2, 1, "work"), "d2", deadline())
        assert a is not None and b is not None and gate._gateway is not None
        assert len(gate._gateway._backends) == 2
        await gate.teardown("j", "d1")
        assert len(gate._gateway._backends) == 2  # d2 still uses both
        await gate.teardown("j", "d2")
        assert gate._gateway._backends == {} and gate._backend_refs == {}

    drive(short_tmp, body)


def test_workspace_materialised_mcp_config_is_owner_only(tmp_path: Path) -> None:
    from marianne.execution.instruments.workspace_mcp_config import _write_private

    target = tmp_path / "cfg.json"
    target.write_bytes(b"old")
    os.chmod(target, 0o644)
    _write_private(target, b"{}")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600 and target.read_bytes() == b"{}"


# ------------------------------------------------------------------ lease (replaces D2's 12 h)


def _lease_gate(root: Path, clk: _Clock) -> MemoryDispatchGate:
    run = root / "run"
    run.mkdir(mode=0o700, exist_ok=True)
    return MemoryDispatchGate(
        run_dir=run, memory_root=root / "files", audit_dir=root / "audit", user="ada",
        clock=clk, start_reaper=False,  # ticks are driven by hand: no real sleeps
    )


async def _owner(stop: asyncio.Event) -> None:
    await stop.wait()


def test_live_owner_keeps_its_session_for_hours_past_the_sheet_timeout(short_tmp: Path) -> None:
    from marianne.daemon.baton.memory_gate import LEASE_SECONDS

    async def main() -> None:
        clk = _Clock()
        gate = _lease_gate(short_tmp, clk)
        stop = asyncio.Event()
        try:
            gate.register_job("j", two())
            p = await gate.deliver(
                "j", SHEET, timeout_seconds=100, existing_mcp_config=None,
                workspace=short_tmp / "ws",
            )
            assert p is not None and gate._gateway is not None
            owner = asyncio.create_task(_owner(stop))
            gate.set_owner("j", p.dispatch_id, owner)
            sid = gate._open[("j", p.dispatch_id)].session_ids[0]
            sessions = gate._gateway.sessions
            for waited in (400, 4000, 40000):  # behind a sibling's workspace lock, say
                while clk.now < 1_000_000.0 + waited:
                    clk.now += LEASE_SECONDS / 5
                    await gate.tick()
                sessions.admit_call(sid, "read")
            assert gate.is_open("j", p.dispatch_id)
        finally:
            stop.set()
            await gate.shutdown()

    asyncio.run(asyncio.wait_for(main(), T))


def test_finished_owner_with_skipped_teardown_is_reaped_within_a_lease(short_tmp: Path) -> None:
    from marianne.daemon.baton.memory_gate import LEASE_SECONDS

    async def main() -> None:
        clk = _Clock()
        gate = _lease_gate(short_tmp, clk)
        try:
            gate.register_job("j", two())
            p = await gate.deliver(
                "j", SHEET, timeout_seconds=100, existing_mcp_config=None,
                workspace=short_tmp / "ws",
            )
            assert p is not None and gate._gateway is not None
            owner = asyncio.create_task(asyncio.sleep(0))
            gate.set_owner("j", p.dispatch_id, owner)
            await owner  # the task finished; nobody called teardown
            sid = gate._open[("j", p.dispatch_id)].session_ids[0]
            sessions = gate._gateway.sessions
            assert len(sockets(short_tmp)) == 2
            clk.now += LEASE_SECONDS / 5
            await gate.tick()
            await gate.drain()
            assert not gate.is_open("j", p.dispatch_id) and sockets(short_tmp) == []
            with pytest.raises(MemoryBackendError) as info:
                sessions.admit_call(sid, "read")
            assert "the memory session for this sheet has ended" in _shim_message(info.value)
        finally:
            await gate.shutdown()

    asyncio.run(asyncio.wait_for(main(), T))


def _shim_message(exc: MemoryBackendError) -> str:
    from marianne.memory.shim import _error_payload

    return str(_error_payload(exc)["message"])


def test_dispatch_without_an_owner_is_reaped_after_one_lease(short_tmp: Path) -> None:
    from marianne.daemon.baton.memory_gate import LEASE_SECONDS

    async def main() -> None:
        clk = _Clock()
        gate = _lease_gate(short_tmp, clk)
        try:
            gate.register_job("j", two())
            p = await gate.deliver(
                "j", SHEET, timeout_seconds=100, existing_mcp_config=None,
                workspace=short_tmp / "ws",
            )
            assert p is not None
            clk.now += LEASE_SECONDS - 1
            await gate.tick()
            assert gate.is_open("j", p.dispatch_id)  # not yet one lease old
            clk.now += 2
            await gate.tick()
            await gate.drain()
            assert not gate.is_open("j", p.dispatch_id) and sockets(short_tmp) == []
        finally:
            await gate.shutdown()

    asyncio.run(asyncio.wait_for(main(), T))


def test_renewal_cannot_revive_a_closed_session_and_is_not_over_the_socket() -> None:
    from marianne.memory.errors import Unauthenticated
    from marianne.memory.gateway import SessionTable
    from marianne.memory.gateway.endpoint import FORBIDDEN_FIELDS
    from marianne.memory.shim import TOOLS

    clk = _Clock()
    table = SessionTable(clock=clk)
    sid = table.open(
        principal="person:ada", attachment_id="a", scope="project/a", permissions=("read",),
        dispatch_id="d", deadline_at=clk.now + 300,
    ).session_id
    table.set_deadline(sid, clk.now + 600)
    assert table.get(sid).deadline_at == clk.now + 600
    table.close(sid)
    with pytest.raises(Unauthenticated):
        table.set_deadline(sid, clk.now + 9999)
    expired = table.open(
        principal="person:ada", attachment_id="a", scope="project/a", permissions=("read",),
        dispatch_id="d2", deadline_at=clk.now + 10,
    ).session_id
    clk.now += 11
    with pytest.raises(MemoryBackendError):
        table.set_deadline(expired, clk.now + 9999)
    assert "set_deadline" not in TOOLS and "deadline" not in FORBIDDEN_FIELDS - {"deadline"}


def test_session_not_renewed_for_a_lease_refuses_fail_closed(short_tmp: Path) -> None:
    from marianne.daemon.baton.memory_gate import LEASE_SECONDS

    async def main() -> None:
        clk = _Clock()
        gate = _lease_gate(short_tmp, clk)  # no reaper at all: it "died"
        try:
            gate.register_job("j", two())
            p = await gate.deliver(
                "j", SHEET, timeout_seconds=100, existing_mcp_config=None,
                workspace=short_tmp / "ws",
            )
            assert p is not None and gate._gateway is not None
            sid = gate._open[("j", p.dispatch_id)].session_ids[0]
            gate._gateway.sessions.admit_call(sid, "read")
            clk.now += LEASE_SECONDS + 1
            with pytest.raises(MemoryBackendError):
                gate._gateway.sessions.admit_call(sid, "read")
        finally:
            await gate.shutdown()

    asyncio.run(asyncio.wait_for(main(), T))


def test_shutdown_stops_the_reaper_and_does_not_count_it_as_pending(short_tmp: Path) -> None:
    async def main() -> None:
        run = short_tmp / "run"
        run.mkdir(mode=0o700)
        gate = MemoryDispatchGate(
            run_dir=run, memory_root=short_tmp / "files", audit_dir=short_tmp / "audit",
            user="ada",
        )
        gate.register_job("j", two())
        p = await gate.deliver(
            "j", SHEET, timeout_seconds=100, existing_mcp_config=None, workspace=short_tmp / "ws",
        )
        assert p is not None
        reaper = gate._reaper
        assert reaper is not None and not reaper.done()  # started lazily at the first open
        report = await asyncio.wait_for(gate.shutdown(), 5)
        assert reaper.done() and gate._reaper is None
        assert report.clean  # the reaper was never "pending work"

    asyncio.run(asyncio.wait_for(main(), T))


def test_reaper_stops_by_itself_when_no_dispatch_is_open(short_tmp: Path) -> None:
    async def main() -> None:
        run = short_tmp / "run"
        run.mkdir(mode=0o700)
        gate = MemoryDispatchGate(
            run_dir=run, memory_root=short_tmp / "files", audit_dir=short_tmp / "audit",
            user="ada",
        )
        try:
            gate.register_job("j", two())
            p = await gate.deliver(
                "j", SHEET, timeout_seconds=100, existing_mcp_config=None,
                workspace=short_tmp / "ws",
            )
            assert p is not None
            await gate.teardown("j", p.dispatch_id)
            await gate.tick()  # nothing open: the loop ends
            for _ in range(100):
                if gate._reaper is None:
                    break
                await asyncio.sleep(0.01)
            assert gate._reaper is None
        finally:
            await gate.shutdown()

    asyncio.run(asyncio.wait_for(main(), T))
