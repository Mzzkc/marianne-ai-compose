"""Linux instrument supervisor: every descendant of an instrument dies with it.

Run as ``python -I instrument_supervisor.py [--io low|idle|inherit]
[--report PATH] -- <instrument argv>``. The supervisor becomes a child
subreaper, so a grandchild whose parent exits (an agent's search helper,
a disowned shell) re-parents HERE instead of to init, where no capture
source could see it. When the instrument exits, or the supervisor is
signalled, every adopted process is ended (TERM, short grace, KILL) and
reaped before the supervisor exits with the instrument's own status.

The supervisor shares the instrument's process group, stdin, stdout and
stderr, so the backend's existing group signal, prompt pipe and output
drains are unchanged. It imports nothing from ``marianne`` and runs under
``-I`` so nothing in the workspace can shadow it (GH #445).

Every signal here targets one explicit PID through ``os.kill``; there is
no group signal in this file by design (F-490 guard applies elsewhere).
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

_PR_SET_CHILD_SUBREAPER = 36
_CHILD_GRACE_SECONDS = 1.0
_ORPHAN_GRACE_SECONDS = 0.5
_REAP_DEADLINE_SECONDS = 0.5
_POLL_SECONDS = 0.05

# ioprio_set(2): class in bits 13+, data in the low 13 bits.
_IOPRIO_CLASS_BE = 2
_IOPRIO_CLASS_IDLE = 3
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_SET_SYSCALL = {"x86_64": 251, "aarch64": 30, "i686": 289, "i386": 289}


def _apply_io_priority(mode: str) -> str:
    """Lower this process's I/O class; children inherit it. Returns what applied."""
    if mode == "inherit":
        return "inherit"
    number = _IOPRIO_SET_SYSCALL.get(platform.machine())
    if number is None:
        return "unavailable"
    if mode == "idle":
        value = _IOPRIO_CLASS_IDLE << 13
    else:
        value = (_IOPRIO_CLASS_BE << 13) | 7
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(number, _IOPRIO_WHO_PROCESS, 0, value) != 0:
        return "unavailable"
    return mode


def _apply_open_files_cap(cap: int | None) -> int | None:
    """Lower this process's soft RLIMIT_NOFILE; the instrument's tree inherits it.

    Never raised above the hard limit; ``None`` leaves the conductor's limit
    in place. Returns the cap that applied (or None).
    """
    if cap is None:
        return None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        value = min(cap, hard) if hard != resource.RLIM_INFINITY else cap
        if value < soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (value, hard))
        return value
    except (ValueError, OSError):
        return None


def _children_of(pid: int) -> list[int]:
    """PIDs whose parent is ``pid`` right now, from /proc (live or zombie)."""
    found: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                stat = handle.read()
        except OSError:
            continue
        # comm may contain spaces/parens: parse after the LAST ')'.
        tail = stat.rsplit(b")", 1)[-1].split()
        if len(tail) > 1 and tail[1] == str(pid).encode():
            found.append(int(entry))
    return found


def _reap(child_pid: int, status: dict[str, int | None]) -> int:
    """Reap every exited child once; record the instrument's status if seen."""
    reaped = 0
    while True:
        try:
            pid, code = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return reaped
        if pid == 0:
            return reaped
        reaped += 1
        if pid == child_pid:
            if os.WIFSIGNALED(code):
                status["signal"] = os.WTERMSIG(code)
            else:
                status["exit_code"] = os.WEXITSTATUS(code)


def _signal_each(pids: list[int], sig: int) -> int:
    sent = 0
    for pid in pids:
        try:
            os.kill(pid, sig)
            sent += 1
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
    return sent


def _sweep(child_pid: int, status: dict[str, int | None], me: int) -> dict[str, int]:
    """End and reap every process still parented here, bounded."""
    counts = {"term": 0, "kill": 0, "unreaped": 0}
    _reap(child_pid, status)
    survivors = _children_of(me)
    if not survivors:
        return counts
    counts["term"] = _signal_each(survivors, signal.SIGTERM)
    deadline = time.monotonic() + _ORPHAN_GRACE_SECONDS
    while time.monotonic() < deadline:
        _reap(child_pid, status)
        survivors = _children_of(me)
        if not survivors:
            return counts
        time.sleep(_POLL_SECONDS)
    # A killed orphan's own children re-parent here; keep sweeping until
    # the subtree is empty or the deadline passes.
    deadline = time.monotonic() + _REAP_DEADLINE_SECONDS
    while True:
        survivors = _children_of(me)
        if not survivors:
            return counts
        counts["kill"] += _signal_each(survivors, signal.SIGKILL)
        _reap(child_pid, status)
        if time.monotonic() >= deadline:
            counts["unreaped"] = len(_children_of(me))
            return counts
        time.sleep(_POLL_SECONDS)


def _write_report(path: Path | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    try:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, path)
    except OSError:
        pass


def main(argv: list[str]) -> int:
    io_mode = "low"
    open_files_cap: int | None = None
    report: Path | None = None
    index = 0
    while index < len(argv) and argv[index] != "--":
        if argv[index] == "--io" and index + 1 < len(argv):
            io_mode = argv[index + 1]
            index += 2
        elif argv[index] == "--max-open-files" and index + 1 < len(argv):
            try:
                open_files_cap = int(argv[index + 1])
            except ValueError:
                print("instrument_supervisor: --max-open-files needs an integer", file=sys.stderr)
                return 125
            index += 2
        elif argv[index] == "--report" and index + 1 < len(argv):
            report = Path(argv[index + 1])
            index += 2
        else:
            print(f"instrument_supervisor: unknown option {argv[index]!r}", file=sys.stderr)
            return 125
    command = argv[index + 1:]
    if not command:
        print("instrument_supervisor: no instrument command after --", file=sys.stderr)
        return 125

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        print("instrument_supervisor: PR_SET_CHILD_SUBREAPER refused", file=sys.stderr)
        return 125
    io_applied = _apply_io_priority(io_mode)
    open_files_applied = _apply_open_files_cap(open_files_cap)

    forwarded: list[int] = []

    def forward(sig: int, _: object) -> None:
        forwarded.append(sig)

    for forwarded_sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(forwarded_sig, forward)

    try:
        child = subprocess.Popen(command, close_fds=True)
    except OSError as exc:
        print(f"instrument_supervisor: cannot start {command[0]!r}: {exc}", file=sys.stderr)
        _write_report(report, {"spawn_error": str(exc), "io_priority": io_applied,
                               "max_open_files": open_files_applied})
        return 127
    child_pid = child.pid
    me = os.getpid()
    status: dict[str, int | None] = {"exit_code": None, "signal": None}
    adopted_during_run = 0
    seen: set[int] = set()
    signalled_child_at: float | None = None

    while status["exit_code"] is None and status["signal"] is None:
        _reap(child_pid, status)
        if forwarded:
            pending = forwarded.pop(0)
            try:
                os.kill(child_pid, pending)
            except ProcessLookupError:
                pass
            if signalled_child_at is None:
                signalled_child_at = time.monotonic()
        if (signalled_child_at is not None
                and time.monotonic() - signalled_child_at > _CHILD_GRACE_SECONDS):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            signalled_child_at = time.monotonic() + 3600.0  # once
        for pid in _children_of(me):
            if pid != child_pid and pid not in seen:
                seen.add(pid)
                adopted_during_run += 1
        time.sleep(_POLL_SECONDS)

    counts = _sweep(child_pid, status, me)
    _write_report(report, {
        "child_pid": child_pid,
        "exit_code": status["exit_code"],
        "signal": status["signal"],
        "adopted_during_run": adopted_during_run,
        "swept_term": counts["term"],
        "swept_kill": counts["kill"],
        "unreaped": counts["unreaped"],
        "io_priority": io_applied,
        "max_open_files": open_files_applied,
    })
    # Popen never saw the exit; stop its finalizer from calling waitpid again.
    child.returncode = status["exit_code"] if status["signal"] is None else -int(status["signal"])
    if status["signal"] is not None:
        fatal = int(status["signal"])
        if fatal != signal.SIGKILL:
            signal.signal(fatal, signal.SIG_DFL)
        os.kill(me, fatal)
        time.sleep(1.0)  # unreachable for a fatal default; a caught signal falls through
        return 128 + fatal
    return int(status["exit_code"] or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
