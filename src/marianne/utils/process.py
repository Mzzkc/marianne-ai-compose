"""Process safety utilities.

Shared guards for process group operations that prevent PID-recycle
or mock-object bugs from escalating into session-wide kills (F-490).
Also hosts identity-bound descendant cleanup for CLI execution.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
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


def _descendant_alive(identity: DescendantIdentity) -> bool:
    """True while the captured process still exists with the same identity."""
    try:
        current = psutil.Process(identity.pid)
        return (
            current.status() != psutil.STATUS_ZOMBIE
            and current.create_time() == identity.create_time
            and current.uids().real == identity.uid
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


@dataclass(frozen=True)
class BoundedCommandResult:
    """Outcome of :func:`run_bounded_command`."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    """The PARENT did not exit within ``timeout_seconds``."""
    drain_grace_fired: bool
    """The parent exited but an inherited pipe stayed open past the grace."""


async def _drain_pipe(stream: asyncio.StreamReader | None, chunks: list[bytes]) -> None:
    # A mocked process (tests) carries non-StreamReader attributes; refuse to
    # await them — same guard cli_backend applies, for the same OOM reason.
    if not isinstance(stream, asyncio.StreamReader):
        return
    while True:
        chunk = await stream.read(65536)
        if not isinstance(chunk, bytes) or not chunk:
            return
        chunks.append(chunk)


async def run_bounded_command(
    proc: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
    pgid: int | None,
    context: str,
    post_exit_drain_grace_seconds: float = 2.0,
    kill_grace_seconds: float = 2.0,
) -> BoundedCommandResult:
    """Wait for a spawned ``bash -c`` command the #406-safe way (GH #410).

    ``proc.communicate()`` waits for pipe EOF, which a backgrounded grandchild
    that inherited stdout/stderr can hold open long after the parent exited;
    the caller then burns its whole timeout and misreports a clean exit as a
    failure. This helper is the one owner of the correct sequence, shared by
    ``skip_when`` commands and ``command_succeeds`` validations (the CLI
    instrument path has its own richer variant in ``cli_backend``):

    1. drain both pipes concurrently while waiting for the PARENT's exit
       (``proc.wait`` polled on returncode, not pipe EOF), bounded by
       ``timeout_seconds``;
    2. after the parent exits, give the drains ``post_exit_drain_grace_seconds``
       to reach EOF; a pipe still open after that belongs to a descendant;
    3. on every exit path, SIGTERM -> grace -> SIGKILL the captured group
       (``safe_killpg`` refuses our own group) and reap identity-matched
       descendants, so nothing outlives the call.

    The returned ``stdout``/``stderr`` hold whatever was read, even on timeout.
    """
    out: list[bytes] = []
    err: list[bytes] = []
    drains = [
        asyncio.create_task(_drain_pipe(proc.stdout, out)),
        asyncio.create_task(_drain_pipe(proc.stderr, err)),
    ]
    descendants: dict[int, DescendantIdentity] = {}
    try:
        parent_create_time: float | None = psutil.Process(proc.pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
        parent_create_time = None

    def _capture() -> None:
        if proc.pid is not None and parent_create_time is not None:
            descendants.update(
                (d.pid, d) for d in snapshot_descendant_trees(proc.pid, parent_create_time)
            )

    async def _parent_exit() -> int | None:
        started = time.monotonic()
        while proc.returncode is None:
            _capture()
            await asyncio.sleep(0.001 if time.monotonic() - started < 0.2 else 0.05)
        _capture()
        return proc.returncode

    timed_out = False
    grace_fired = False
    try:
        try:
            await asyncio.wait_for(_parent_exit(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
        if not timed_out:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*drains), timeout=post_exit_drain_grace_seconds
                )
            except TimeoutError:
                grace_fired = True
    finally:
        for d in drains:
            d.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
        # Group kill when anything may still be alive: the parent (timeout),
        # a descendant that held a pipe past the grace, or a captured
        # descendant. A clean exit with EOF and no descendants signals
        # nothing (Process Lifecycle Phase 1: no killpg after a clean exit).
        something_alive = (
            proc.returncode is None
            or grace_fired
            or any(_descendant_alive(x) for x in descendants.values())
        )
        if pgid is not None and something_alive:
            try:
                safe_killpg(pgid, signal.SIGTERM, context=f"{context}.kill_grace")
            except (ProcessLookupError, PermissionError):
                pass
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=kill_grace_seconds)
                except TimeoutError:
                    try:
                        safe_killpg(pgid, signal.SIGKILL, context=f"{context}.kill_force")
                    except (ProcessLookupError, PermissionError):
                        pass
                    try:
                        await proc.wait()
                    except ProcessLookupError:
                        pass
            else:
                # Parent is gone; anything left in the group had its grace above.
                try:
                    safe_killpg(pgid, signal.SIGKILL, context=f"{context}.kill_force")
                except (ProcessLookupError, PermissionError):
                    pass
        elif pgid is None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
        if descendants:
            reap_descendant_trees(tuple(descendants.values()))
            # Signal delivery is asynchronous: give the captured descendants a
            # bounded moment to actually disappear so the caller's "nothing
            # outlives the call" contract holds, then SIGKILL stragglers.
            deadline = time.monotonic() + kill_grace_seconds
            while time.monotonic() < deadline:
                if not any(_descendant_alive(x) for x in descendants.values()):
                    break
                await asyncio.sleep(0.02)
            else:
                for straggler in descendants.values():
                    if _descendant_alive(straggler):
                        try:
                            os.kill(straggler.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=kill_grace_seconds)
            except (TimeoutError, ProcessLookupError):
                pass
    return BoundedCommandResult(
        returncode=proc.returncode,
        stdout=b"".join(out),
        stderr=b"".join(err),
        timed_out=timed_out,
        drain_grace_fired=grace_fired,
    )
