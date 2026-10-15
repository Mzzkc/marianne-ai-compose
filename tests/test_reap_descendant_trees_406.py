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


def test_float_only_caller_survives_a_wall_clock_step(monkeypatch) -> None:
    """GH #446 (Circuit): CLI-instrument musicians and piped skip_when /
    command_succeeds pass only the spawn-time float. psutil's create_time moves
    with every wall-clock step (btime is re-read per call), so an exact compare
    went blind for the parent's life after the first step: a child forked after
    the step was never captured. A step-sized delta must still capture; a
    recycled-PID-sized delta must still refuse."""
    import subprocess

    current = psutil.Process()
    spawn_float = current.create_time()
    child = subprocess.Popen(["sleep", "30"])
    try:
        stepped = spawn_float + 3.0  # Circuit measured +3.00 s steps on WSL
        real_create_time = psutil.Process.create_time
        monkeypatch.setattr(
            psutil.Process, "create_time",
            lambda self: stepped if self.pid == current.pid else real_create_time(self),
        )
        # same process, stepped clock: the child is still captured
        captured = process.snapshot_descendant_trees(current.pid, spawn_float)
        assert child.pid in {c.pid for c in captured}
        # a recycled PID: the dead process's float is hours away, refused
        assert process.snapshot_descendant_trees(current.pid, spawn_float - 7200.0) == ()
        # the tick-bound caller ignores the float entirely
        ticks = process._proc_start_ticks(current.pid)
        assert ticks is not None
        tick_bound = process.snapshot_descendant_trees(
            current.pid, spawn_float - 7200.0, parent_start_ticks=ticks
        )
        assert child.pid in {c.pid for c in tick_bound}
        assert process.snapshot_descendant_trees(
            current.pid, spawn_float, parent_start_ticks=ticks + 1
        ) == ()
    finally:
        child.kill()
        child.wait()
