"""#344 obs2: idle stale-detection must not SIGKILL a still-alive agent.

The idle detector measured activity as workspace-file mtime and killed on the
FIRST idle-timeout crossing. An agent doing long, legitimate work that writes no
files (goose finalizing silently; an agent blocked on a model/network call) looks
idle and was destroyed mid-work → permanent-fail → cascade.

A 4-model thinking-lab (~/workspaces/staledetect-lab, 2026-06-07) converged: the
detector kills on ABSENCE of a weak signal; it must instead require proof of
stuckness. The minimal non-invasive fix (NOT the #352 streaming rewrite, which
cannot see a silent finalize either) is a process-liveness gate with
suspicion-before-cancel:

- before cancel, probe the sheet's subprocess via the adapter's existing
  ``_active_pids`` -> ``/proc/<pid>/stat``.
- ALIVE (or probe unavailable -> fail-open) -> defer (reschedule), counting an
  idle strike; only after ``max_idle_checks_before_kill`` consecutive
  idle-and-alive strikes is the kill allowed (a bounded backstop against a
  genuinely-hung-but-alive process; the subprocess-level timeout is the ultimate
  wall).
- DEAD / zombie / gone -> kill immediately (unchanged intent).
- any activity (idle < timeout) -> strikes reset.

Tests are deterministic: real tmp_path workspaces with os.utime-backdated mtimes
+ a monkeypatched ``_process_is_alive`` (no real subprocess needed).
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


def _make_sheet(workspace: Path) -> Sheet:
    return Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=workspace,
        instrument_name="goose",
        prompt_template="test",
        timeout_seconds=600.0,
    )


def _backdate(workspace: Path, seconds_ago: float) -> None:
    t = time.time() - seconds_ago
    for p in [workspace, *workspace.rglob("*")]:
        os.utime(p, (t, t))


async def _sleep_forever() -> None:
    await asyncio.sleep(3600)


async def _cancel_and_collect(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _idle_setup(
    tmp_path: Path, *, max_idle_checks: int = 3
) -> tuple[BatonAdapter, asyncio.Task[None]]:
    """An enabled, idle (200s), alive-task sheet with a tracked subprocess PID."""
    adapter = BatonAdapter()
    ws = tmp_path / "job-ws"
    ws.mkdir()
    (ws / "out.txt").write_text("x")
    _backdate(ws, 200.0)
    adapter.register_job(
        "j1",
        [_make_sheet(ws)],
        dependencies={},
        stale_detection=StaleDetectionConfig(
            enabled=True,
            idle_timeout_seconds=60.0,
            check_interval_seconds=10.0,
            max_idle_checks_before_kill=max_idle_checks,
        ),
    )
    state = adapter.baton.get_sheet_state("j1", 1)
    assert state is not None
    state.status = BatonSheetStatus.DISPATCHED
    adapter._stale_dispatch_time[_KEY] = time.time() - 200.0
    task = asyncio.create_task(_sleep_forever())
    adapter._active_tasks[_KEY] = task
    adapter._active_pids[_KEY] = (424242, 424242)  # tracked subprocess
    return adapter, task


class TestLivenessGate:
    async def test_alive_idle_is_deferred_not_killed(self, tmp_path: Path) -> None:
        adapter, task = _idle_setup(tmp_path, max_idle_checks=3)
        adapter._process_is_alive = lambda pid: True  # type: ignore[assignment]

        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))

        assert _KEY not in adapter._stale_markers  # NOT killed
        assert not task.cancelled()
        assert adapter._stale_idle_strikes.get(_KEY) == 1  # one strike recorded
        await _cancel_and_collect(task)

    async def test_alive_idle_killed_after_backstop_strikes(self, tmp_path: Path) -> None:
        adapter, task = _idle_setup(tmp_path, max_idle_checks=3)
        adapter._process_is_alive = lambda pid: True  # type: ignore[assignment]

        # Strikes 1 and 2 defer; strike 3 (== max) kills.
        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))
        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))
        assert _KEY not in adapter._stale_markers
        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))
        assert _KEY in adapter._stale_markers  # backstop kill

        await _cancel_and_collect(task)
        assert task.cancelled()

    async def test_dead_subprocess_killed_immediately(self, tmp_path: Path) -> None:
        adapter, task = _idle_setup(tmp_path, max_idle_checks=3)
        adapter._process_is_alive = lambda pid: False  # process gone/zombie

        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))

        assert _KEY in adapter._stale_markers  # dead → kill on first check
        await _cancel_and_collect(task)
        assert task.cancelled()

    async def test_no_tracked_pid_fails_open_to_deferral(self, tmp_path: Path) -> None:
        adapter, task = _idle_setup(tmp_path, max_idle_checks=3)
        del adapter._active_pids[_KEY]  # no PID → cannot probe → fail open (alive)

        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))

        assert _KEY not in adapter._stale_markers  # fail-open → defer, never false-kill
        assert not task.cancelled()
        await _cancel_and_collect(task)

    async def test_activity_resets_strikes(self, tmp_path: Path) -> None:
        adapter, task = _idle_setup(tmp_path, max_idle_checks=3)
        adapter._process_is_alive = lambda pid: True  # type: ignore[assignment]

        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))
        assert adapter._stale_idle_strikes.get(_KEY) == 1

        # Fresh write → not idle → strikes must reset.
        ws = adapter._job_sheets["j1"][1].workspace
        (ws / "progress.txt").write_text("more")
        await adapter._handle_stale_check(StaleCheck(job_id="j1", sheet_num=1))

        assert _KEY not in adapter._stale_idle_strikes  # reset
        assert _KEY not in adapter._stale_markers
        await _cancel_and_collect(task)


class TestProcessIsAlive:
    def test_self_pid_is_alive(self) -> None:
        adapter = BatonAdapter()
        assert adapter._process_is_alive(os.getpid()) is True

    def test_missing_pid_is_not_alive(self) -> None:
        adapter = BatonAdapter()
        # A PID that cannot exist (kernel rejects pid 0 here; use a huge one).
        assert adapter._process_is_alive(2_000_000_000) is False
