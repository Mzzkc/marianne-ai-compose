"""Identity controls for captured CLI descendants (#406)."""

from __future__ import annotations

import os

import psutil

from marianne.utils import process


def test_recycled_pid_is_never_signalled(monkeypatch) -> None:
    current = psutil.Process()
    identity = process.DescendantIdentity(
        pid=current.pid,
        create_time=current.create_time() - 1,
        uid=os.getuid(),
        pgid=current.pid,
    )
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(process, "safe_killpg", lambda pgid, sig, **kw: calls.append((pgid, sig)))

    counts = process.reap_descendant_trees((identity,))

    assert counts.skipped_identity_mismatch == 1
    assert counts.signalled == 0
    assert calls == []


def test_vanished_pid_is_counted_without_signal(monkeypatch) -> None:
    pid = max(psutil.pids()) + 100_000
    identity = process.DescendantIdentity(pid, 1.0, os.getuid(), pid)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(process, "safe_killpg", lambda pgid, sig, **kw: calls.append((pgid, sig)))

    counts = process.reap_descendant_trees((identity,))

    assert counts.already_gone == 1
    assert counts.signalled == 0
    assert calls == []


def test_guard_refusal_is_not_counted_as_signal(monkeypatch) -> None:
    current = psutil.Process()
    identity = process.DescendantIdentity(
        current.pid, current.create_time(), os.getuid(), current.pid,
    )
    monkeypatch.setattr(process.os, "getpgid", lambda pid: current.pid)
    calls: list[tuple[int, int]] = []

    def refuse(pgid: int, sig: int, *, context: str) -> bool:
        calls.append((pgid, sig))
        return False

    monkeypatch.setattr(process, "safe_killpg", refuse)
    counts = process.reap_descendant_trees((identity,))

    assert len(calls) == 1
    assert counts.signalled == 0
    assert counts.skipped_identity_mismatch == 0
    assert counts.skipped_unsafe_group == 1


def test_linux_start_ticks_survive_psutil_wall_clock_drift(monkeypatch) -> None:
    current = psutil.Process()
    ticks = process._proc_start_ticks(current.pid)
    assert ticks is not None
    identity = process.DescendantIdentity(
        current.pid, current.create_time() - 3, os.getuid(), current.pid, ticks,
    )
    monkeypatch.setattr(process.os, "getpgid", lambda pid: current.pid)
    signals: list[int] = []
    monkeypatch.setattr(
        process, "safe_killpg",
        lambda pgid, sig, **kw: signals.append(pgid) or True,
    )

    counts = process.reap_descendant_trees((identity,))

    assert counts.signalled == 1
    assert signals == [current.pid]


def test_linux_start_ticks_refuse_recycled_pid_despite_matching_float(monkeypatch) -> None:
    current = psutil.Process()
    ticks = process._proc_start_ticks(current.pid)
    assert ticks is not None
    identity = process.DescendantIdentity(
        current.pid, current.create_time(), os.getuid(), current.pid, ticks - 1,
    )
    monkeypatch.setattr(process.os, "getpgid", lambda pid: current.pid)
    signals: list[int] = []
    monkeypatch.setattr(
        process, "safe_killpg",
        lambda pgid, sig, **kw: signals.append(pgid) or True,
    )

    counts = process.reap_descendant_trees((identity,))

    assert counts.skipped_identity_mismatch == 1
    assert signals == []


def test_float_only_caller_survives_a_wall_clock_step() -> None:
    """GH #446 (Circuit): a caller that recorded only the spawn-time float must
    still capture after the clock moved (Circuit measured ~6 s/min of drift
    under Hyper-V time sync) and must still refuse a recycled PID. The probe
    offsets the RECORDED float; it never patches psutil, because psutil's own
    children() reuse guard compares the parent's live create_time with each
    child's and drops the child when the parent reads newer (Circuit P2: the
    first version of this test failed 4/4 in any process under 3 s old)."""
    import subprocess

    current = psutil.Process()
    live_float = current.create_time()
    child = subprocess.Popen(["sleep", "30"])
    try:
        # recorded 3 s behind the live reading (one clock correction): captured
        captured = process.snapshot_descendant_trees(current.pid, live_float - 3.0)
        assert child.pid in {c.pid for c in captured}
        # recorded hours behind (a dead process's float on a recycled PID): refused
        assert process.snapshot_descendant_trees(current.pid, live_float - 7200.0) == ()
        # the tick-bound caller ignores the float entirely
        ticks = process._proc_start_ticks(current.pid)
        assert ticks is not None
        tick_bound = process.snapshot_descendant_trees(
            current.pid, live_float - 7200.0, parent_start_ticks=ticks
        )
        assert child.pid in {c.pid for c in tick_bound}
        assert process.snapshot_descendant_trees(
            current.pid, live_float, parent_start_ticks=ticks + 1
        ) == ()
    finally:
        child.kill()
        child.wait()


def test_spawn_identity_records_ticks_so_every_caller_is_drift_proof() -> None:
    """GH #446 complete fix: the three non-supervisor callers (CLI musicians,
    piped skip_when/command_succeeds) now record the kernel start tick at
    spawn through ``spawn_identity`` and pass it, so a float that has drifted
    past any tolerance never blinds them."""
    import subprocess

    created, ticks = process.spawn_identity(os.getpid())
    assert created is not None and ticks is not None
    assert ticks == process._proc_start_ticks(os.getpid())
    child = subprocess.Popen(["sleep", "30"])
    try:
        # a float drifted far beyond any step tolerance, with the tick: still captured
        captured = process.snapshot_descendant_trees(
            os.getpid(), created - 600.0, parent_start_ticks=ticks
        )
        assert child.pid in {c.pid for c in captured}
    finally:
        child.kill()
        child.wait()
    assert process.spawn_identity(2**22 - 1) == (None, None)
