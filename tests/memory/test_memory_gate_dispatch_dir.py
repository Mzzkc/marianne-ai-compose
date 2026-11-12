"""The per-dispatch directory is created and removed without following symlinks (S4)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from marianne.daemon.baton.memory_gate import MemoryGateError, SheetIdentity

from .test_memory_gate_liveness import deliver, make, run


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_symlinked_config_dir_is_refused_and_nothing_is_created_or_removed_under_the_target(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        try:
            ws = short_tmp / "ws"
            ws.mkdir()
            elsewhere = short_tmp / "elsewhere"
            elsewhere.mkdir()
            victim = elsewhere / "0123456789abcdef"  # looks like a dispatch dir; must survive
            victim.mkdir()
            (victim / "mcp.json").write_text("{}")
            before = tree(elsewhere)
            (ws / ".marianne-memory").symlink_to(elsewhere)
            with pytest.raises(MemoryGateError, match=r"\(mcp config\)"):
                await gate.deliver(
                    "j", SheetIdentity(1, 1, "work"), timeout_seconds=60,
                    existing_mcp_config=None, workspace=ws,
                )
            await gate.drain()
            assert tree(elsewhere) == before
            assert gate.open_dispatches == 0 and not (short_tmp / "run" / "s").exists() or not list(
                (short_tmp / "run" / "s").iterdir()
            )
            gate.deregister_job("j")  # the sweep must not follow the link either
            await gate.drain()
            assert tree(elsewhere) == before
        finally:
            await gate.shutdown()

    run(main)


def test_a_file_at_the_config_dir_is_a_typed_refusal(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        try:
            ws = short_tmp / "ws"
            ws.mkdir()
            (ws / ".marianne-memory").write_text("x")
            with pytest.raises(MemoryGateError, match=r"\(mcp config\)"):
                await gate.deliver(
                    "j", SheetIdentity(1, 1, "work"), timeout_seconds=60,
                    existing_mcp_config=None, workspace=ws,
                )
            assert (ws / ".marianne-memory").read_text() == "x" and gate.open_dispatches == 0
        finally:
            await gate.shutdown()

    run(main)


def test_a_symlink_swapped_in_for_the_hash_directory_is_not_followed_on_removal(
    short_tmp: Path,
) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        try:
            dispatch, _sid = await deliver(gate, short_tmp)
            ddir = gate._open[("j", dispatch)].dir
            assert ddir is not None
            target = short_tmp / "target"
            target.mkdir()
            (target / "keep").write_text("k")
            for child in ddir.iterdir():
                child.unlink()
            ddir.rmdir()
            ddir.symlink_to(target)
            await gate.teardown("j", dispatch)
            assert (target / "keep").read_text() == "k"
        finally:
            await gate.shutdown()

    run(main)


def test_config_dir_and_file_are_owner_only_and_removed_by_teardown(short_tmp: Path) -> None:
    async def main() -> None:
        gate = make(short_tmp)
        try:
            ws = short_tmp / "ws"
            dispatch, _sid = await deliver(gate, short_tmp)
            root = ws / ".marianne-memory"
            ddir = gate._open[("j", dispatch)].dir
            assert ddir is not None
            assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
            assert stat.S_IMODE(os.stat(ddir).st_mode) == 0o700
            assert stat.S_IMODE(os.stat(ddir / "mcp.json").st_mode) == 0o600
            await gate.teardown("j", dispatch)
            assert not ddir.exists()
        finally:
            await gate.shutdown()

    run(main)


def test_sweep_asks_who_is_live_when_it_reaches_a_directory_not_when_it_was_queued(
    short_tmp: Path,
) -> None:
    from marianne.daemon.baton.memory_gate import CONFIG_DIR_NAME, _remove_dispatch_dirs

    ws = short_tmp / "ws"
    root = ws / CONFIG_DIR_NAME
    root.mkdir(parents=True, mode=0o700)
    late = root / "aaaaaaaaaaaaaaaa"
    late.mkdir(mode=0o700)
    stale = root / "bbbbbbbbbbbbbbbb"
    stale.mkdir(mode=0o700)
    asked: list[int] = []

    def keep() -> set[Path]:
        asked.append(1)
        return {late}  # became live after the sweep was queued

    assert _remove_dispatch_dirs(ws, keep) == 1
    assert late.exists() and not stale.exists() and asked
