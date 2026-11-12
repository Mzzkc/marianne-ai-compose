"""A backend is referenced from registration, not from open completion (S2)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.baton.memory_gate import SheetIdentity
from marianne.memory.errors import MemoryBackendError
from marianne.memory.gateway import audit as audit_module
from marianne.memory.gateway import endpoint as endpoint_module

from .test_memory_gate_liveness import Clock, make, run, two  # noqa: F401  (shared helpers)

OFFSETS = 240


def _slow_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injected worker delay: every audit append holds the single worker for a moment."""
    original = audit_module.AuditLog.append

    def slow(self: Any, *a: Any, **k: Any) -> Any:
        time.sleep(0.0005)
        return original(self, *a, **k)

    monkeypatch.setattr(audit_module.AuditLog, "append", slow)


def test_sibling_open_racing_the_last_close_never_breaks_a_session_or_a_deliver(
    short_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slow_audit(monkeypatch)

    async def one(offset: int, root: Path) -> tuple[str, str]:
        gate = make(root)
        try:
            a = await gate.deliver(
                "j", SheetIdentity(1, 1, "work"), timeout_seconds=60, existing_mcp_config=None,
                workspace=root / "ws",
            )
            assert a is not None
            ending = asyncio.ensure_future(gate.teardown("j", a.dispatch_id))
            for _ in range(offset):  # the offset is a number of loop turns, not a duration
                await asyncio.sleep(0)
            try:
                b = await gate.deliver(
                    "j", SheetIdentity(2, 1, "work"), timeout_seconds=60,
                    existing_mcp_config=None, workspace=root / "ws",
                )
            except Exception as exc:  # noqa: BLE001
                await ending
                return "deliver_failed", repr(exc)
            await ending
            assert b is not None and gate._gateway is not None
            sid = gate._open[("j", b.dispatch_id)].session_ids[0]
            try:
                await asyncio.to_thread(gate._gateway.recall, sid, "x")
            except MemoryBackendError as exc:
                return "broken_session", repr(exc)
            return "ok", ""
        finally:
            await gate.shutdown()

    async def main() -> None:
        results: dict[str, int] = {}
        for offset in range(OFFSETS):
            root = short_tmp / f"o{offset}"
            root.mkdir()
            verdict, detail = await one(offset, root)
            results[verdict] = results.get(verdict, 0) + 1
            assert verdict == "ok", f"offset {offset}: {verdict} {detail}"
        assert results == {"ok": OFFSETS}

    run(main)


def test_failed_delivers_leave_no_backend_and_no_reference(
    short_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = endpoint_module.MemoryEndpoint.open_session
    calls = {"n": 0}

    async def flaky(self: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] % 2 == 1:  # the first attachment of every deliver fails
            raise RuntimeError("boom")
        return await real(self, **kw)

    monkeypatch.setattr(endpoint_module.MemoryEndpoint, "open_session", flaky)

    async def main() -> None:
        gate = make(short_tmp)
        try:
            for n in range(5):
                with pytest.raises(Exception, match="boom"):
                    await gate.deliver(
                        "j", SheetIdentity(n + 1, 1, "work"), timeout_seconds=60,
                        existing_mcp_config=None, workspace=short_tmp / "ws",
                    )
                calls["n"] += 1  # keep the next deliver's first open on an odd count
            await gate.drain()
            assert gate._gateway is not None
            assert gate._gateway._backends == {} and gate._backend_refs == {}
            assert gate.open_dispatches == 0
        finally:
            await gate.shutdown()

    run(main)


def test_cancelled_deliver_and_failed_config_write_release_their_reference(
    short_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marianne.daemon.baton.memory_gate as mg

    def broken(*_a: Any, **_k: Any) -> Any:
        raise OSError("disk full")

    async def main() -> None:
        gate = make(short_tmp)
        try:
            monkeypatch.setattr(mg, "_write_dispatch_config", broken)
            with pytest.raises(Exception, match="disk full"):
                await gate.deliver(
                    "j", SheetIdentity(1, 1, "work"), timeout_seconds=60,
                    existing_mcp_config=None, workspace=short_tmp / "ws",
                )
            await gate.drain()
            assert gate._gateway is not None
            assert gate._gateway._backends == {} and gate._backend_refs == {}
            task = asyncio.ensure_future(gate.deliver(
                "j", SheetIdentity(2, 1, "work"), timeout_seconds=60,
                existing_mcp_config=None, workspace=short_tmp / "ws",
            ))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await gate.drain()
            assert gate._gateway._backends == {} and gate._backend_refs == {}
        finally:
            await gate.shutdown()

    run(main)
