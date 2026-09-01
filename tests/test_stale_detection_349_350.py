"""Idle-based stale detection in the baton adapter (#349/#350/#352-v1/#198).

`StaleDetectionConfig.idle_timeout_seconds` / `check_interval_seconds` were
parsed + validated but never consumed (#349), and the `StaleCheck` handler only
escalated when the musician task was *dead* — an alive-but-idle process was
rescheduled every hardcoded 60s forever, blocking the pipeline until the
`sheet_timeout` net fired (#350).

A 4-model thinking-lab review converged on this design (archived at
~/lab-archives/2026-05-30-stale-detection-349-350):

* **Activity signal = workspace mtime poll** (Option 3). `sheet.workspace` is
  per-job, so observer-events (Option 1) and mtime-poll have identical
  (job-level) granularity — and mtime-poll is self-contained (no baton→observer
  coupling, no observer-availability false-positive) and trivially testable.
  Idle = ``now - max(newest_workspace_mtime, dispatch_time)``. True per-sheet
  granularity + pure-stdout activity are deferred to #352 (stdout streaming).
* **Escalation** (3 branches): dead → existing synthetic STALE inject;
  alive + enabled + idle≥timeout → mark+cancel; alive + enabled + not-idle →
  reschedule at ``check_interval_seconds``; alive + disabled → existing 60s
  reschedule (pure no-op path). The ``sheet_timeout + 60`` safety net is kept.
* **Kill sequence** (load-bearing, unanimous): set a per-sheet marker *before*
  ``task.cancel()`` and inject NO result in the handler; ``_on_musician_done``
  reads the marker → classifies the synthetic ``SheetAttemptResult`` as
  ``STALE`` (else ``CANCELLED``) — exactly one result, no double-fire.

Tests are deterministic: real ``tmp_path`` workspaces with ``os.utime``-backdated
mtimes drive idle calculation against the real wall clock (no sleeps, no
time-patching).
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from marianne.core.config.execution import StaleDetectionConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import SheetAttemptResult, StaleCheck
from marianne.daemon.baton.state import BatonSheetStatus

_KEY = ("j1", 1)


def _drain_sars(adapter: BatonAdapter) -> list[SheetAttemptResult]:
    """Drain the baton inbox and return only the SheetAttemptResults.

    The baton's StaleCheck logging path also enqueues internal events (e.g.
    DispatchRetry); the stale-detection contract is about the synthetic
    *result* events, so filter to those.
    """
    out: list[SheetAttemptResult] = []
    inbox = adapter.baton.inbox
    while not inbox.empty():
        ev = inbox.get_nowait()
        if isinstance(ev, SheetAttemptResult):
            out.append(ev)
    return out


def _stale_check(adapter: BatonAdapter) -> StaleCheck:
    return StaleCheck(
        job_id="j1",
        sheet_num=1,
        event_generation=adapter.baton.get_job_generation("j1"),
    )


def _make_sheet(workspace: Path) -> Sheet:
    return Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=workspace,
        instrument_name="claude-code",
        prompt_template="test",
        timeout_seconds=600.0,
    )


def _backdate(workspace: Path, seconds_ago: float) -> None:
    """Backdate the workspace dir and every child so its newest mtime is old."""
    t = time.time() - seconds_ago
    for p in [workspace, *workspace.rglob("*")]:
        os.utime(p, (t, t))


async def _sleep_forever() -> None:
    await asyncio.sleep(3600)


def _alive_task() -> asyncio.Task[None]:
    return asyncio.create_task(_sleep_forever())


async def _cancel_and_collect(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _setup(
    tmp_path: Path,
    *,
    enabled: bool,
    idle_timeout: float = 60.0,
    check_interval: float = 10.0,
) -> tuple[BatonAdapter, Path, list[tuple[float, object]]]:
    adapter = BatonAdapter()
    ws = tmp_path / "job-ws"
    ws.mkdir()
    sheet = _make_sheet(ws)
    adapter.register_job(
        "j1",
        [sheet],
        dependencies={},
        stale_detection=StaleDetectionConfig(
            enabled=enabled,
            idle_timeout_seconds=idle_timeout,
            check_interval_seconds=check_interval,
        ),
    )
    state = adapter.baton.get_sheet_state("j1", 1)
    assert state is not None
    state.status = BatonSheetStatus.DISPATCHED

    # Record (delay, event) for every timer schedule.
    scheduled: list[tuple[float, object]] = []
    orig = adapter._timer_wheel.schedule

    def _rec(delay: float, event: object) -> object:
        scheduled.append((delay, event))
        return orig(delay, event)  # type: ignore[arg-type]

    adapter._timer_wheel.schedule = _rec  # type: ignore[assignment]
    return adapter, ws, scheduled


class TestIdleEscalation:
    async def test_idle_fires_after_timeout(self, tmp_path: Path) -> None:
        adapter, ws, _ = _setup(tmp_path, enabled=True, idle_timeout=60.0)
        (ws / "out.txt").write_text("x")
        _backdate(ws, 200.0)  # last activity 200s ago > 60s timeout
        adapter._stale_dispatch_time[_KEY] = time.time() - 200.0
        task = _alive_task()
        adapter._active_tasks[_KEY] = task
        # #344: a kill now requires the subprocess to be DEAD (idle + alive is
        # deferred). Model a dead subprocess so the idle→STALE kill still fires.
        adapter._active_pids[_KEY] = (123, 123)
        adapter._process_is_alive = lambda pid: False  # type: ignore[assignment]

        await adapter._handle_stale_check(_stale_check(adapter))

        assert _KEY in adapter._stale_markers
        await _cancel_and_collect(task)  # let cancellation settle
        assert task.cancelled()

    async def test_idle_cancel_injects_exactly_one_stale_result(
        self, tmp_path: Path
    ) -> None:
        adapter, ws, _ = _setup(tmp_path, enabled=True, idle_timeout=30.0)
        _backdate(ws, 100.0)
        adapter._stale_dispatch_time[_KEY] = time.time() - 100.0
        task = _alive_task()
        adapter._active_tasks[_KEY] = task
        # #344: dead subprocess → idle kill fires (alive would defer).
        adapter._active_pids[_KEY] = (123, 123)
        adapter._process_is_alive = lambda pid: False  # type: ignore[assignment]

        await adapter._handle_stale_check(_stale_check(adapter))
        await _cancel_and_collect(task)

        # done-callback (normally wired via add_done_callback) injects the result
        adapter._on_musician_done("j1", 1, task)

        sars = _drain_sars(adapter)
        assert len(sars) == 1  # exactly one synthetic result — no double-fire
        sar = sars[0]
        assert sar.error_classification == "STALE"
        assert sar.execution_success is False
        assert sar.job_id == "j1"
        assert sar.sheet_num == 1
        assert _KEY not in adapter._stale_markers  # marker consumed

    async def test_activity_resets_idle_clock(self, tmp_path: Path) -> None:
        adapter, ws, scheduled = _setup(
            tmp_path, enabled=True, idle_timeout=60.0, check_interval=10.0
        )
        adapter._stale_dispatch_time[_KEY] = time.time() - 200.0
        # Fresh write → newest mtime is ~now → not idle.
        (ws / "fresh.txt").write_text("progress")
        task = _alive_task()
        adapter._active_tasks[_KEY] = task

        await adapter._handle_stale_check(_stale_check(adapter))

        assert _KEY not in adapter._stale_markers
        assert not task.cancelled()
        # Rescheduled at check_interval_seconds (not the hardcoded 60s).
        stale_delays = [d for d, e in scheduled if isinstance(e, StaleCheck)]
        assert 10.0 in stale_delays
        await _cancel_and_collect(task)

    async def test_busy_sheet_never_killed(self, tmp_path: Path) -> None:
        adapter, ws, _ = _setup(
            tmp_path, enabled=True, idle_timeout=10.0, check_interval=2.0
        )
        adapter._stale_dispatch_time[_KEY] = time.time()
        task = _alive_task()
        adapter._active_tasks[_KEY] = task

        for i in range(5):
            (ws / f"step-{i}.txt").write_text(str(i))  # fresh activity each cycle
            await adapter._handle_stale_check(_stale_check(adapter))
            assert not task.cancelled()

        assert _KEY not in adapter._stale_markers
        await _cancel_and_collect(task)


class TestDisabledIsNoOp:
    async def test_disabled_never_kills_and_reschedules_60s(
        self, tmp_path: Path
    ) -> None:
        adapter, ws, scheduled = _setup(tmp_path, enabled=False)
        _backdate(ws, 9999.0)  # extremely idle
        adapter._stale_dispatch_time[_KEY] = time.time() - 9999.0
        task = _alive_task()
        adapter._active_tasks[_KEY] = task

        await adapter._handle_stale_check(_stale_check(adapter))

        assert _KEY not in adapter._stale_markers
        assert not task.cancelled()
        # Legacy behavior preserved: reschedule at the hardcoded 60s.
        stale_delays = [d for d, e in scheduled if isinstance(e, StaleCheck)]
        assert 60.0 in stale_delays
        await _cancel_and_collect(task)

    async def test_no_stale_config_defaults_to_disabled(self, tmp_path: Path) -> None:
        """A job registered without stale_detection behaves as before (no-op)."""
        adapter = BatonAdapter()
        ws = tmp_path / "ws"
        ws.mkdir()
        adapter.register_job("j1", [_make_sheet(ws)], dependencies={})
        state = adapter.baton.get_sheet_state("j1", 1)
        assert state is not None
        state.status = BatonSheetStatus.DISPATCHED
        _backdate(ws, 9999.0)
        task = _alive_task()
        adapter._active_tasks[_KEY] = task

        await adapter._handle_stale_check(_stale_check(adapter))

        assert not task.cancelled()
        assert _KEY not in adapter._stale_markers
        await _cancel_and_collect(task)


class TestKillSequenceClassification:
    async def test_plain_cancel_is_classified_cancelled(
        self, tmp_path: Path
    ) -> None:
        """A cancellation with no stale marker keeps the existing CANCELLED path."""
        adapter, _, _ = _setup(tmp_path, enabled=True)
        task = _alive_task()
        adapter._active_tasks[_KEY] = task
        await _cancel_and_collect(task)

        adapter._on_musician_done("j1", 1, task)

        sars = _drain_sars(adapter)
        assert len(sars) == 1
        assert sars[0].error_classification == "CANCELLED"

    async def test_dead_task_path_preserved(self, tmp_path: Path) -> None:
        """No live task + DISPATCHED state → existing dead→STALE injection."""
        adapter, _, _ = _setup(tmp_path, enabled=True)
        # No entry in _active_tasks → task is None (dead).
        await adapter._handle_stale_check(_stale_check(adapter))

        sars = _drain_sars(adapter)
        assert len(sars) == 1
        assert sars[0].error_classification == "STALE"


class TestInitialScheduling:
    async def test_enabled_schedules_idle_check_and_dispatch_time(
        self, tmp_path: Path
    ) -> None:
        adapter, _ws, scheduled = _setup(
            tmp_path, enabled=True, idle_timeout=300.0, check_interval=30.0
        )
        adapter._schedule_stale_detection("j1", 1)

        delays = [d for d, e in scheduled if isinstance(e, StaleCheck)]
        # Idle cadence at check_interval AND the sheet_timeout+60 safety net.
        assert 30.0 in delays
        assert 660.0 in delays  # 600s sheet timeout + 60s buffer
        assert _KEY in adapter._stale_dispatch_time

    async def test_disabled_schedules_only_safety_net(self, tmp_path: Path) -> None:
        adapter, _ws, scheduled = _setup(tmp_path, enabled=False)
        adapter._schedule_stale_detection("j1", 1)

        delays = [d for d, e in scheduled if isinstance(e, StaleCheck)]
        assert delays == [660.0]  # only sheet_timeout + 60, no idle cadence
        assert _KEY not in adapter._stale_dispatch_time
