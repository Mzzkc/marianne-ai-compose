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
