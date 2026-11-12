"""Slice 1: the memory dispatch gate, without the adapter (real gateway + endpoint + files)."""

from __future__ import annotations

import asyncio
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
        assert all(
            s.state.value in {"closed", "CLOSED"} or "closed" in s.state.value.lower()
            for s in gate._gateway.sessions._sessions.values()
        )

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
        sessions = gate._gateway.sessions._sessions  # type: ignore[union-attr]
        assert sessions and all("closed" in s.state.value.lower() for s in sessions.values())

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
        endpoint.close_session = bad  # type: ignore[method-assign]
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
        path = gate.merge_config("j", "d1", d, None, short_tmp / "ws")
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
