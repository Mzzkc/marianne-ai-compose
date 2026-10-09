"""Process safety utilities.

Shared guards for process group operations that prevent PID-recycle
or mock-object bugs from escalating into session-wide kills (F-490).
Also hosts identity-bound descendant cleanup for CLI execution.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass

import psutil

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


@dataclass(frozen=True)
class DescendantIdentity:
    pid: int
    create_time: float
    uid: int
    pgid: int


@dataclass(frozen=True)
class DescendantReapCounts:
    signalled: int = 0
    already_gone: int = 0
    skipped_identity_mismatch: int = 0
    skipped_unsafe_group: int = 0


def snapshot_descendant_trees(
    parent_pid: int, parent_create_time: float,
) -> tuple[DescendantIdentity, ...]:
    """Capture actual descendants while the parent still owns their ancestry."""
    try:
        parent = psutil.Process(parent_pid)
        if parent.create_time() != parent_create_time:
            return ()
        descendants = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ()
    captured: list[DescendantIdentity] = []
    for child in descendants:
        try:
            captured.append(DescendantIdentity(
                pid=child.pid, create_time=child.create_time(),
                uid=child.uids().real, pgid=os.getpgid(child.pid),
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return tuple(captured)


def snapshot_pipe_holders(
    pipe_inodes: frozenset[int], parent_create_time: float,
) -> tuple[DescendantIdentity, ...]:
    """Capture owners of this invocation's pipes when ancestry was reparented.

    The backend owns the read ends, so these inodes cannot be recycled while
    it scans. Only same-UID processes born after the direct parent qualify.
    The reaper still requires an unchanged PID, start time, UID and PGID.
    """
    if not pipe_inodes or not os.path.isdir("/proc"):
        return ()
    wanted = {f"pipe:[{inode}]" for inode in pipe_inodes}
    captured: list[DescendantIdentity] = []
    for proc in psutil.process_iter(["pid", "create_time", "uids"]):
        try:
            if proc.pid == os.getpid():
                continue
            info = proc.info
            uids = info["uids"]
            if uids is None or uids.real != os.getuid():
                continue
            born = info["create_time"]
            if born is None or born < parent_create_time:
                continue
            fd_dir = f"/proc/{proc.pid}/fd"
            owns_pipe = False
            for fd in os.listdir(fd_dir):
                try:
                    if os.readlink(f"{fd_dir}/{fd}") in wanted:
                        owns_pipe = True
                        break
                except OSError:
                    continue
            if not owns_pipe:
                continue
            captured.append(DescendantIdentity(
                proc.pid, born, uids.real, os.getpgid(proc.pid),
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return tuple(captured)


def reap_descendant_trees(
    descendants: tuple[DescendantIdentity, ...],
) -> DescendantReapCounts:
    """Signal only surviving, unchanged leaders of captured descendant groups."""
    signalled = already_gone = skipped_identity_mismatch = skipped_unsafe_group = 0
    seen: set[int] = set()
    for identity in descendants:
        if identity.pid in seen:
            continue
        seen.add(identity.pid)
        try:
            current = psutil.Process(identity.pid)
            if current.status() == psutil.STATUS_ZOMBIE:
                already_gone += 1
                continue
            if (current.create_time() != identity.create_time
                    or current.uids().real != identity.uid
                    or os.getpgid(identity.pid) != identity.pgid):
                skipped_identity_mismatch += 1
                continue
            # A matched group leader pins the observed PGID to the captured
            # process. Other captured descendants belong to that group.
            if identity.pgid != identity.pid or identity.uid != os.getuid():
                skipped_unsafe_group += 1
                continue
            # Recheck directly at the signal boundary; a changed leader is
            # never authority for a group signal.
            if (current.create_time() != identity.create_time
                    or os.getpgid(identity.pid) != identity.pgid):
                skipped_identity_mismatch += 1
                continue
            if safe_killpg(identity.pgid, signal.SIGTERM,
                           context="reap_descendant"):
                signalled += 1
            else:
                skipped_unsafe_group += 1
        except (psutil.NoSuchProcess, ProcessLookupError):
            already_gone += 1
        except (psutil.AccessDenied, PermissionError, OSError):
            skipped_unsafe_group += 1
    return DescendantReapCounts(signalled, already_gone,
                                skipped_identity_mismatch, skipped_unsafe_group)
