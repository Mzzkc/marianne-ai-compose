"""Process safety utilities.

Shared guards for process group operations that prevent PID-recycle
or mock-object bugs from escalating into session-wide kills (F-490).
Also hosts ``reap_descendant_trees`` — the Claude Code #1935 orphan
process cleanup helper relocated from ``backends/claude_cli.py`` in
Phase 4c of the backend atlas migration.
"""

from __future__ import annotations

import os
import signal

from marianne.core.logging import get_logger

_logger = get_logger("utils.process")


def safe_killpg(pgid: int, sig: int, *, context: str = "") -> bool:
    """Session-safe wrapper around os.killpg (F-490).

    Refuses when pgid would target init, the caller's own process group,
    or an invalid value. Prevents PID-recycle or mock-object bugs from
    translating into ``kill(-1, SIGKILL)`` that nukes the user session.

    In particular, ``os.killpg(1, sig)`` compiles to ``kill(-1, sig)``
    in the kernel, which sends the signal to every process the caller
    owns except init — killing systemd --user, every terminal, pytest,
    and the daemon.

    Guards:
    - ``pgid <= 1``: init (1), own pgroup in killpg(0) idiom (0), or invalid
    - ``pgid == os.getpgid(0)``: our own process group (would kill us plus
      whatever shell/pytest/terminal shares the group)

    Returns True if the signal was actually sent, False if blocked. Callers
    should treat False the same as a successful kill for cleanup purposes —
    the target is either unreachable or would have killed the caller.
    """
    if pgid <= 1:
        _logger.warning(
            "killpg_guard_refused",
            reason="pgid_le_1", pgid=pgid, signal=sig, context=context,
        )
        return False
    try:
        own_pgid = os.getpgid(0)
        if pgid == own_pgid:
            _logger.warning(
                "killpg_guard_refused",
                reason="own_pgroup", pgid=pgid, signal=sig, context=context,
            )
            return False
    except OSError:
        pass  # getpgid failed — fall through to killpg with validated pgid
    os.killpg(pgid, sig)
    return True


def reap_descendant_trees(pid: int) -> None:
    """Kill all surviving descendants of a dead process.

    When a backend subprocess (e.g. claude-code) exits, its children may
    have their own process groups (Claude Code #1935 — the Bash tool spawns
    commands with ``start_new_session``).  These children get reparented
    to init (PID 1) but keep their independent PGIDs — so ``killpg``
    on the backend's PGID does not reach them.

    This helper uses psutil to find processes that were descendants of
    the given backend PID and kills each orphaned process group.  It is
    scoped to processes the current user owns that look like shell
    commands from Claude Code's Bash tool (``shell-snapshot`` in cmdline),
    so there is no risk of signalling unrelated processes.

    Relocated from ``marianne.backends.claude_cli._reap_descendant_trees``
    in Phase 4c of the backend atlas migration so the helper survives
    retirement of the native Claude CLI backend class.
    """
    try:
        import psutil
    except ImportError:
        return

    my_uid = os.getuid()
    killed_pgids: set[int] = set()

    for proc in psutil.process_iter(["pid", "ppid", "uids"]):
        try:
            info = proc.info
            if info["ppid"] != 1:
                continue
            uids = info.get("uids")
            if uids is None or uids.real != my_uid:
                continue
            child_pid = info["pid"]
            child_pgid = os.getpgid(child_pid)
            # Only kill process groups led by this process (session leaders
            # that were children of our backend).  Skip if already killed.
            if child_pgid != child_pid or child_pgid in killed_pgids:
                continue
            # Safety: never kill our own process group or PID 1's group
            if child_pgid <= 1 or child_pgid == os.getpgrp():
                continue
            # Check if this process was spawned around the same time as
            # the backend, by verifying it looks like a shell command
            # from claude's Bash tool (shell-snapshot in cmdline).
            cmdline = proc.cmdline()
            cmdline_str = " ".join(cmdline)
            if "shell-snapshot" not in cmdline_str:
                continue
            safe_killpg(child_pgid, signal.SIGTERM, context="reap_descendant")
            killed_pgids.add(child_pgid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    if killed_pgids:
        _logger.info(
            "reap_descendant_trees",
            backend_pid=pid,
            killed_pgids=sorted(killed_pgids),
            count=len(killed_pgids),
        )
