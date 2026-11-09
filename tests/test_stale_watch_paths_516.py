"""Writes outside the workspace count as activity when the score names the root (GH #516).

Two fleet members were killed by the idle backstop while writing code into a
project worktree outside the score workspace (2026-11-09). The workspace walk
saw nothing; ``stale_detection.watch_paths`` adds the roots the musician
actually writes to.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from marianne.core.config.execution import StaleDetectionConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import StaleCheck
from marianne.daemon.baton.state import BatonSheetStatus

_KEY = ("j1", 1)


def _backdate(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))
    for p in path.rglob("*"):
        os.utime(p, (t, t))


async def _sleep_forever() -> None:
    # A never-finishing musician stand-in; polled by the stale check, never slept on.
    while True:
        await asyncio.sleep(0)


def _setup(
    tmp_path: Path, watch_paths: list[str]
) -> tuple[BatonAdapter, asyncio.Task[None], Path]:
    adapter = BatonAdapter()
    ws = tmp_path / "job-ws"
    ws.mkdir()
    (ws / "out.txt").write_text("x")
    _backdate(ws, 200.0)
    worktree = tmp_path / "worktree"
    (worktree / "src").mkdir(parents=True)
    sheet = Sheet(
        num=1, movement=1, voice=None, voice_count=1, instrument_name="cli",
        prompt_template="x", workspace=ws, variables={"project_root": str(worktree)},
    )
    adapter.register_job(
        "j1", [sheet], dependencies={},
        stale_detection=StaleDetectionConfig(
            enabled=True, idle_timeout_seconds=60.0, check_interval_seconds=10.0,
            max_idle_checks_before_kill=1, watch_paths=watch_paths,
        ),
    )
    state = adapter.baton.get_sheet_state("j1", 1)
    assert state is not None
    state.status = BatonSheetStatus.DISPATCHED
    adapter._stale_dispatch_time[_KEY] = time.time() - 200.0
    task = asyncio.create_task(_sleep_forever())
    adapter._active_tasks[_KEY] = task
    adapter._active_pids[_KEY] = (424242, 424242)
    adapter._process_is_alive = lambda pid: True  # type: ignore[assignment]
    return adapter, task, worktree


def _check(adapter: BatonAdapter) -> StaleCheck:
    return StaleCheck(
        job_id="j1", sheet_num=1, event_generation=adapter.baton.get_job_generation("j1")
    )


async def _finish(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_fresh_write_in_watched_worktree_is_activity(tmp_path: Path) -> None:
    adapter, task, worktree = _setup(tmp_path, ["{{ project_root }}"])
    (worktree / "src" / "mod.py").write_text("print(1)\n")  # written just now
    await adapter._handle_stale_check(_check(adapter))
    assert _KEY not in adapter._stale_markers, "a live write in a watched root must not be idle"
    assert not task.cancelled()
    await _finish(task)


async def test_same_write_without_watch_paths_is_killed(tmp_path: Path) -> None:
    adapter, task, worktree = _setup(tmp_path, [])
    (worktree / "src" / "mod.py").write_text("print(1)\n")
    await adapter._handle_stale_check(_check(adapter))
    assert _KEY in adapter._stale_markers, "the pre-#516 behaviour: the worktree write is invisible"
    await _finish(task)


async def test_stale_watched_root_does_not_rescue(tmp_path: Path) -> None:
    adapter, task, worktree = _setup(tmp_path, ["{{ project_root }}"])
    (worktree / "src" / "mod.py").write_text("print(1)\n")
    _backdate(worktree, 200.0)  # an old write is not activity
    await adapter._handle_stale_check(_check(adapter))
    assert _KEY in adapter._stale_markers
    await _finish(task)


def test_watch_dirs_resolve_templates_and_skip_absent(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "root").mkdir()
    sheet = Sheet(num=1, movement=1, voice=None, voice_count=1, instrument_name="cli",
                  prompt_template="x", workspace=ws,
                  variables={"project_root": str(tmp_path / "root")})
    cfg = StaleDetectionConfig(enabled=True, watch_paths=[
        "{{ project_root }}", "{{ workspace }}", str(tmp_path / "absent"),
        "~/definitely-not-a-dir-516", "{{ unknown }}/x",
    ])
    dirs = BatonAdapter._stale_watch_dirs(cfg, sheet)
    assert dirs == [tmp_path / "root", ws]
