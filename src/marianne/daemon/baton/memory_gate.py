"""The memory dispatch gate: per-dispatch memory sessions for the baton adapter.

All memory logic the conductor needs at dispatch lives here, so the adapter only
makes three small, guarded calls (register/deregister a job, ``prepare`` before a
backend is acquired, ``teardown`` when the dispatch ends).

* A job without a ``memory:`` key never reaches this module: ``prepare`` returns
  ``None`` at once and nothing heavy (gateway, endpoint, backend, audit log) exists
  until the first dispatch that needs one. Those are created lazily.
* ``prepare`` is all-or-nothing: if any attachment fails to open, every session
  and socket opened for that dispatch so far is closed and a typed
  :class:`MemoryGateError` naming the attachment is raised.
* The harness gets one MCP config file per dispatch, in its own directory
  ``<workspace>/.marianne-memory/<hash>/mcp.json`` (0700/0600), holding the technique servers
  plus one shim entry per attachment. The file names a
  socket and nothing else: no principal, scope, grant or session id.
* ``teardown`` never raises into the caller; it reports what it could not stop.
* Session lifetime is the owner's liveness, read at call time. One ``threading.Event``
  (the dispatch's "ended" flag) is shared by every session of a dispatch and checked first
  on every call. It is set, instantly and from the loop, by ``teardown`` (before any I/O is
  queued), ``deregister_job``, ``shutdown`` and a done-callback on the owner task. No clock,
  lease or renewal is involved: a finished or cancelled sheet's sessions refuse the very next
  call however busy the worker is. A reaper task exists for hygiene only (it enqueues the
  teardown that a flagged dispatch missed, and ends ownerless dispatches after a grace on
  the monotonic clock); if it dies, flagged sessions are still refused and only socket and
  directory cleanup waits for deregister or shutdown.
* All gate I/O (session open/close with its audit fsync, config write/delete, sweeps) runs
  on ONE ordered daemon worker thread. One slow job therefore delays opens and cleanups for
  every dispatch (head-of-line blocking), but it can no longer end a live session: a job that
  runs longer than ``SLOW_JOB_SECONDS`` is logged (``memory_gate.slow_job``, with its kind).
* Still on the event loop: endpoint bind/unbind (socket creation, ``start_unix_server``),
  backend construction and close, the run-directory ``mkdir`` in ``_ensure``, the sweep of
  stale sockets' bookkeeping, and all gate dictionaries. None blocks on a lock or an fsync.
* A failed memory open fails the dispatch; it does not degrade to "no memory". That is
  intended: the sheet's prompt names tools that must exist.
* Same-UID limit (stated once): the socket path is passed in the shim's environment rather
  than argv, which hides it from other users but not from other processes of the same user
  (``/proc/<pid>/environ`` is readable too), and every job of one conductor shares one
  principal; so a sheet with a shell can reach another job's session if it finds the path.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import queue
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from marianne.core.config.memory import MemoryConfig
from marianne.core.logging import get_logger
from marianne.memory.access import parse_access
from marianne.memory.backends.files import files_backend_factory, resolve_files_location
from marianne.memory.errors import MemoryBackendError
from marianne.memory.gateway import AuditLog, Gateway, MemoryCaps
from marianne.memory.gateway.endpoint import MemoryEndpoint
from marianne.memory.manifest import mcp_config_entry, render_manifest
from marianne.memory.resolve import ResolvedBinding, SheetPhase, resolve_memory

_logger = get_logger("daemon.baton.memory_gate")

#: A dispatch with no owner task this long after it opened (monotonic clock) is ended.
OWNERLESS_GRACE_SECONDS = 300.0

#: A worker job that runs longer than this is logged (kind = the job function's name).
SLOW_JOB_SECONDS = 5.0

#: Pause between hygiene passes of the reaper.
REAP_INTERVAL_SECONDS = 30.0

#: Sessions the gate opens carry this deadline: lifetime is the dispatch's flag (see S1).
FAR_FUTURE = 1.0e15

#: Total time ``shutdown`` waits for queued gate work before reporting what is pending.
SHUTDOWN_BOUND_SECONDS = 30.0

#: Per-workspace directory holding one subdirectory per dispatch (``<hash>/mcp.json``).
CONFIG_DIR_NAME = ".marianne-memory"


class MemoryGateError(Exception):
    """A memory attachment could not be delivered. Names the attachment and the reason."""

    def __init__(self, attachment: str, reason: str) -> None:
        self.attachment = attachment
        self.reason = reason
        super().__init__(f"memory attachment {attachment!r}: {reason}")


class MemoryConfigCollision(MemoryGateError):
    """A memory server name is already present in the technique MCP config."""


@dataclass(frozen=True)
class SheetIdentity:
    """The sheet a dispatch is for: number, movement number and (optional) phase name."""

    num: int
    movement: int
    phase: str = ""


@dataclass(frozen=True)
class OpenedSession:
    """One attachment's live session. Conductor-side only; never rendered to a musician."""

    attachment: str
    session_id: str
    socket_path: Path


@dataclass(frozen=True)
class MemoryDelivery:
    manifest: str
    mcp_entries: dict[str, dict[str, Any]]
    sessions: tuple[OpenedSession, ...]


@dataclass(frozen=True)
class TeardownReport:
    """What ``teardown`` left behind: calls still running, and steps that failed."""

    in_flight: int = 0
    errors: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.in_flight and not self.errors


@dataclass(frozen=True)
class PreparedMemory:
    """What the adapter needs from one dispatch: the text to append and the config to attach."""

    dispatch_id: str
    manifest: str
    mcp_config_path: Path


@dataclass
class _Registration:
    config: MemoryConfig
    movement_names: dict[int, str]


class _Worker:
    """One ordered worker thread. A daemon thread, so a job stuck at interpreter exit cannot
    hold the process (a ``ThreadPoolExecutor`` thread is joined at exit)."""

    def __init__(self, slow_job_seconds: float = SLOW_JOB_SECONDS) -> None:
        self._slow = slow_job_seconds
        self._queue: queue.SimpleQueue[tuple[ConcurrentFuture[Any], Callable[..., Any], tuple[Any, ...]] | None] = queue.SimpleQueue()  # noqa: E501
        self._thread = threading.Thread(target=self._loop, name="memory-gate", daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any) -> ConcurrentFuture[Any]:
        fut: ConcurrentFuture[Any] = ConcurrentFuture()
        self._queue.put((fut, fn, args))
        return fut

    def stop(self) -> None:
        """Let the thread end after the jobs already queued; never waits for it."""
        self._queue.put(None)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fut, fn, args = item
            if not fut.set_running_or_notify_cancel():
                continue
            started = time.monotonic()
            try:
                fut.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001 — handed to the awaiter
                fut.set_exception(exc)
            took = time.monotonic() - started
            if took > self._slow:
                _logger.warning(
                    "memory_gate.slow_job",
                    extra={
                        "kind": getattr(fn, "__name__", type(fn).__name__),
                        "seconds": round(took, 3),
                    },
                )


class _BackendRef:
    """One reference on a registered backend; ``release`` is idempotent."""

    def __init__(self, gate: MemoryDispatchGate, key: str) -> None:
        self._gate: MemoryDispatchGate | None = gate
        self.key = key

    def release(self) -> None:
        gate, self._gate = self._gate, None
        if gate is not None:
            gate._release_backend(self.key)


@dataclass
class _Open:
    job_id: str
    session_ids: list[str] = field(default_factory=list)
    config_path: Path | None = None
    dir: Path | None = None  # this dispatch's own config directory
    owner: asyncio.Task[Any] | None = None
    opened_at: float = 0.0  # monotonic
    ended: threading.Event = field(default_factory=threading.Event)
    cleanup_queued: bool = False  # a teardown for this dispatch has been enqueued


def _reason(exc: BaseException) -> str:
    if isinstance(exc, MemoryBackendError):
        text = f"{exc.code.name} {exc.subtype}: {exc.detail}"
        return f"{text} (remedy: {exc.remedy})" if exc.remedy else text
    return f"{type(exc).__name__}: {exc}"


def can_deliver(backend: Any, profile: Any) -> str | None:
    """Why this backend/instrument cannot be handed an MCP config, or ``None`` if it can."""
    if not callable(getattr(backend, "set_mcp_config", None)):
        return "the backend has no set_mcp_config (it cannot be handed an MCP config)"
    if profile is None:
        return "no instrument profile is available to confirm MCP config delivery"
    if getattr(profile, "kind", None) != "cli":
        return "only CLI instruments can be handed an MCP config"
    command = getattr(getattr(profile, "cli", None), "command", None)
    if command is None:
        return "the profile has no CLI command section"
    if not getattr(command, "mcp_config_flag", None) and not getattr(
        command, "mcp_config_workspace_path", None
    ):
        return "the profile has no mcp_config_flag and no mcp_config_workspace_path"
    return None


def lookup_profile(backend_pool: Any, instrument: str) -> Any:
    """The instrument profile the pool would use, or ``None`` when it cannot be read."""
    registry = getattr(backend_pool, "_registry", None)
    if registry is None:
        return None
    try:
        return registry.get(instrument)
    except Exception:  # noqa: BLE001 — an unreadable profile means "cannot confirm delivery"
        return None


def _merged_document(
    existing_path: Path | str | None, entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """The existing config's servers plus ``entries``; a name in both is refused."""
    document: dict[str, Any] = {}
    servers: dict[str, Any] = {}
    if existing_path is not None:
        try:
            loaded = json.loads(Path(existing_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MemoryGateError("(mcp config)", f"existing MCP config unreadable: {exc}") from exc
        if not isinstance(loaded, dict) or not isinstance(loaded.get("mcpServers", {}), dict):
            raise MemoryGateError("(mcp config)", "existing MCP config has no mcpServers object")
        document = dict(loaded)
        servers = dict(loaded.get("mcpServers", {}))
    clash = sorted(set(servers) & set(entries))
    if clash:
        raise MemoryConfigCollision(
            clash[0], f"server name already in the technique MCP config: {', '.join(clash)}",
        )
    servers.update({key: dict(value) for key, value in entries.items()})
    document["mcpServers"] = servers
    return document


def merge_mcp_config(
    existing_path: Path | str | None,
    entries: Mapping[str, Mapping[str, Any]],
    dest_dir: Path | str,
    *,
    name: str = ".memory-mcp.json",
) -> Path:
    """Write ``dest_dir/name`` holding the existing config's servers plus ``entries``.

    Atomic (temp file in the same directory, fsync, ``os.replace``), mode 0600. A name
    present in both the existing config and ``entries`` is refused, never overwritten.
    The existing file is only read. (The gate itself writes through
    :func:`_write_dispatch_config`, which never follows a symlink.)
    """
    document = _merged_document(existing_path, entries)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest, prefix=".memory-mcp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(document, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        target = dest / name
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return target


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_private_dir(parent_fd: int, name: str, *, create: bool) -> int:
    """Open ``name`` under ``parent_fd`` as a real directory we own; never follows a symlink.

    Creates it (0700) when asked. A symlink, a non-directory or a foreign owner raises
    ``OSError``; the caller turns that into a typed refusal.
    """
    if create:
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise PermissionError(f"{name!r} is not owned by this user")
        if create:
            os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_workspace(workspace: Path, *, create: bool) -> int:
    """The workspace root, resolved once (the caller's own path) and opened as a directory."""
    if create:
        workspace.mkdir(parents=True, exist_ok=True)
    return os.open(workspace.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def _write_dispatch_config(
    existing_path: Path | str | None, entries: Mapping[str, Mapping[str, Any]], ddir: Path,
) -> Path:
    """Worker job: create ``ddir`` (0700) and write ``ddir/mcp.json`` (0600) without symlinks.

    The workspace, ``.marianne-memory`` and the hash directory are each opened
    ``O_DIRECTORY | O_NOFOLLOW`` relative to the one before; the file is created
    ``O_EXCL | O_NOFOLLOW``. A symlink or non-directory on the way is a typed refusal.
    """
    document = _merged_document(existing_path, entries)
    workspace = ddir.parent.parent
    fds: list[int] = []
    try:
        try:
            fds.append(_open_workspace(workspace, create=True))
            fds.append(_open_private_dir(fds[-1], CONFIG_DIR_NAME, create=True))
            fds.append(_open_private_dir(fds[-1], ddir.name, create=True))
            fd = os.open(
                "mcp.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=fds[-1],
            )
        except OSError as exc:
            raise MemoryGateError(
                "(mcp config)", f"cannot create the dispatch directory safely: {exc}",
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(document, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink("mcp.json", dir_fd=fds[-1])
            raise
    finally:
        for open_fd in reversed(fds):
            os.close(open_fd)
    return ddir / "mcp.json"


def _remove_dispatch_dir_at(config_fd: int, name: str) -> None:
    """Remove ``name`` (a real directory we own) under ``config_fd``; never follows a link."""
    try:
        fd = _open_private_dir(config_fd, name, create=False)
    except OSError:
        return  # gone, a symlink, a non-directory or foreign: not ours to remove
    try:
        for entry in os.listdir(fd):
            with contextlib.suppress(OSError):
                os.unlink(entry, dir_fd=fd)
    finally:
        os.close(fd)
    with contextlib.suppress(OSError):
        os.rmdir(name, dir_fd=config_fd)


def _remove_dispatch_dir(ddir: Path) -> None:
    """Worker job: remove one dispatch directory through descriptors; never follows a symlink."""
    try:
        ws_fd = _open_workspace(ddir.parent.parent, create=False)
    except OSError:
        return
    try:
        try:
            config_fd = _open_private_dir(ws_fd, CONFIG_DIR_NAME, create=False)
        except OSError:
            return
        try:
            _remove_dispatch_dir_at(config_fd, ddir.name)
        finally:
            os.close(config_fd)
    finally:
        os.close(ws_fd)


def _remove_dispatch_dirs(workspace: Path, keep: Callable[[], set[Path]]) -> int:
    """Worker job: remove every dispatch directory under ``workspace`` not in ``keep``.

    Only real directories directly inside ``<workspace>/.marianne-memory`` that we own and
    whose name is 16 hex digits are touched; symlinks and everything else are left alone.
    ``keep`` is called when the worker gets to each directory, not when the job was queued, so
    a dispatch that began writing its directory in between is never swept.
    """
    try:
        ws_fd = _open_workspace(workspace, create=False)
    except OSError:
        return 0
    removed = 0
    try:
        try:
            config_fd = _open_private_dir(ws_fd, CONFIG_DIR_NAME, create=False)
        except OSError:
            return 0
        try:
            for name in os.listdir(config_fd):
                if len(name) != 16 or any(c not in "0123456789abcdef" for c in name):
                    continue
                if any(p.name == name for p in keep()):
                    continue
                try:
                    info = os.stat(name, dir_fd=config_fd, follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                _remove_dispatch_dir_at(config_fd, name)
                try:
                    os.stat(name, dir_fd=config_fd, follow_symlinks=False)
                except OSError:
                    removed += 1
        finally:
            os.close(config_fd)
    finally:
        os.close(ws_fd)
    return removed


class MemoryDispatchGate:
    """Opens and closes the memory sessions of each dispatch, per (job, dispatch).

    Concurrency model (see also MEMORY-DISPATCH-GATE-NOTES.md):

    * The loop owns all bookkeeping (``_open``, ``_jobs``, registered backends).
    * Every blocking action goes through ONE single-thread worker, in submission order:
      open a session, close a session, write a config, delete a config, sweep. Because
      there is one worker, two actions on the same thing can never overlap.
    * A worker job is never cancelled with its awaiter. Its result is applied on the loop by
      a completion step that runs whether or not anybody still awaits it, and that step
      decides ownership: every successful open is either recorded in an open dispatch or
      has a close enqueued (the one-open-one-owner invariant); a config written for a
      dispatch that is gone is deleted.
    """

    def __init__(
        self, *, run_dir: Path | str, memory_root: Path | str, audit_dir: Path | str,
        user: str, clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        shutdown_bound_seconds: float = SHUTDOWN_BOUND_SECONDS, start_reaper: bool = True,
        ownerless_grace_seconds: float = OWNERLESS_GRACE_SECONDS,
        slow_job_seconds: float = SLOW_JOB_SECONDS,
        reap_interval_seconds: float = REAP_INTERVAL_SECONDS,
    ) -> None:
        self._start_reaper = start_reaper
        self._monotonic = monotonic
        self._ownerless_grace = ownerless_grace_seconds
        self._slow_job_seconds = slow_job_seconds
        self._reap_interval = reap_interval_seconds
        self._reaper: asyncio.Task[None] | None = None
        self._run_dir = Path(run_dir)
        self._memory_root = Path(memory_root)
        self._audit_dir = Path(audit_dir)
        self._user = user
        self._clock = clock
        self._shutdown_bound = shutdown_bound_seconds
        # Heavy members: created on the first dispatch that needs memory.
        self._gateway: Gateway | None = None
        self._endpoint: MemoryEndpoint | None = None
        self._audit: AuditLog | None = None
        self._worker: _Worker | None = None
        self._backend_refs: dict[str, int] = {}  # backend key -> references held
        self._session_backend: dict[str, _BackendRef] = {}  # session id -> its backend reference
        self._jobs: dict[str, _Registration] = {}
        self._open: dict[tuple[str, str], _Open] = {}
        self._workspaces: dict[str, set[Path]] = {}  # job -> workspaces given dispatch dirs
        self._pending: set[asyncio.Future[Any]] = set()  # worker jobs not yet completed
        self._background: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._closing = False

    def now(self) -> float:
        return self._clock()

    # ------------------------------------------------------------ the one worker

    def _submit(self, fn: Callable[..., Any], *args: Any) -> asyncio.Future[Any]:
        """Queue ``fn`` on the single worker. The returned future is completed ON THE LOOP.

        Cancelling the future does not cancel the job; it runs to the end, and callbacks
        added with ``_on_done`` still fire. After shutdown it returns an already-failed
        future and never raises. Its exception is always retrieved, so none goes unreported.
        """
        loop = asyncio.get_running_loop()
        outer: asyncio.Future[Any] = loop.create_future()
        outer.add_done_callback(lambda f: f.cancelled() or f.exception())  # mark retrieved
        if self._closed:  # checked BEFORE _pending is touched
            outer.set_exception(MemoryGateError("(gate)", "the memory gate is shut down"))
            return outer
        if self._worker is None:
            self._worker = _Worker(self._slow_job_seconds)
        self._pending.add(outer)
        outer.add_done_callback(self._pending.discard)
        inner = self._worker.submit(fn, *args)

        def finished(f: Any) -> None:
            def apply() -> None:
                if outer.done():  # cancelled, or failed by shutdown
                    return
                exc = f.exception()
                if exc is not None:
                    outer.set_exception(exc)
                else:
                    outer.set_result(f.result())

            with contextlib.suppress(RuntimeError):  # loop closed at interpreter exit
                loop.call_soon_threadsafe(apply)

        inner.add_done_callback(finished)
        return outer

    def _remove_dir_now_or_queued(self, ddir: Path) -> asyncio.Future[Any] | None:
        """Remove one dispatch dir via the worker, or inline (best effort) after shutdown."""
        if not self._closed:
            return self._submit(_remove_dispatch_dir, ddir)
        with contextlib.suppress(Exception):
            _remove_dispatch_dir(ddir)
        return None

    def _run(self, fn: Callable[..., Any], *args: Any) -> asyncio.Future[Any]:
        """Await-able worker job, shielded: cancelling the caller never cancels the job.

        A future, not a coroutine: no wrapper task exists whose failure could go unretrieved.
        """
        shielded = asyncio.shield(self._submit(fn, *args))
        shielded.add_done_callback(lambda f: f.cancelled() or f.exception())  # mark retrieved
        return shielded

    # ------------------------------------------------------------ registration

    def register_job(
        self, job_id: str, memory_config: MemoryConfig | None,
        movement_names: Mapping[int, str] | None = None,
    ) -> None:
        if memory_config is None:
            self._jobs.pop(job_id, None)
            return
        self._jobs[job_id] = _Registration(memory_config, dict(movement_names or {}))

    def _live_dirs(self) -> set[Path]:
        """Dispatch dirs of open dispatches. Called from the worker: a snapshot, retried if the
        loop mutates ``_open`` while it is copied (``entry.dir`` is set on the loop before the
        write that creates the directory is queued, so a queued sweep always sees it)."""
        while True:
            try:
                return {e.dir for e in list(self._open.values()) if e.dir is not None}
            except RuntimeError:
                continue

    def _end(self, entry: _Open) -> None:
        """Flag a dispatch's sessions as over. Instant, thread-safe, needs no I/O."""
        entry.ended.set()

    def deregister_job(self, job_id: str) -> None:
        """Forget the job, tear down its open dispatches, then remove its dispatch dirs."""
        self._jobs.pop(job_id, None)
        stale = [key for key, entry in self._open.items() if entry.job_id == job_id]
        for key in stale:
            self._end(self._open[key])
            self._open[key].cleanup_queued = True
        workspaces = self._workspaces.pop(job_id, set())
        if not stale and not workspaces:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning(
                "memory_gate.deregister_without_loop",
                extra={"job_id": job_id, "dispatches": len(stale)},
            )
            return

        async def go() -> None:
            for key_job, key_dispatch in stale:
                await self.teardown(key_job, key_dispatch)
            for ws in workspaces:
                with contextlib.suppress(Exception):
                    if self._closed:
                        _remove_dispatch_dirs(ws, self._live_dirs)
                    else:
                        await self._run(_remove_dispatch_dirs, ws, self._live_dirs)

        task = loop.create_task(go())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain(self) -> None:
        """Wait for the gate's own background work and queued worker jobs (not callers' tasks)."""
        while True:
            pending: list[Any] = [*self._background, *self._pending]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------ prepare

    def _ensure(self) -> tuple[Gateway, MemoryEndpoint]:
        if self._closed or self._closing:
            raise MemoryGateError("(gate)", "the memory gate is shut down")
        if self._gateway is None or self._endpoint is None:
            self._run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._audit = AuditLog(self._audit_dir)
            self._gateway = Gateway(audit=self._audit, clock=self._clock)
            self._endpoint = MemoryEndpoint(
                self._gateway, run_dir=self._run_dir, blocking=self._run,
            )
        return self._gateway, self._endpoint

    @staticmethod
    def _backend_key(b: ResolvedBinding) -> str:
        """Registration key: attachment identity plus the policy the backend instance carries.

        The files backend holds provenance, recall cap and lock timeout in its own config,
        so two jobs that name the same attachment with different policy must not share an
        instance (the first job's policy would otherwise bind every later one).
        """
        policy = f"{b.provenance}\x1f{b.recall_cap_bytes}\x1f{b.lock_timeout_seconds!r}"
        return f"{b.attachment_id}~{hashlib.sha256(policy.encode()).hexdigest()[:12]}"

    def _build_backend(self, b: ResolvedBinding) -> Any:
        """The one place a backend is constructed; selecting by ``b.system`` goes here."""
        if b.system != "files":
            raise MemoryGateError(b.name, f"memory system {b.system!r} is not available")
        location = resolve_files_location(b.scope, self._memory_root)
        return files_backend_factory({
            "attachment_id": b.attachment_id, "scope": b.scope, **location.as_config(),
            "recall_cap_bytes": b.recall_cap_bytes, "provenance": b.provenance,
            "lock_timeout_seconds": b.lock_timeout_seconds,
        })

    def _register_backend(self, gateway: Gateway, b: ResolvedBinding) -> _BackendRef:
        """Take a reference on this binding's backend, synchronously, on the loop.

        The count is raised here, before any job is queued, so a sibling dispatch's last
        close cannot drop the backend out from under an open that is still in flight. The
        caller owns the returned handle and must ``release`` it, exactly once, on every path
        that does not end in a recorded open session; a recorded session releases it when it
        closes. The backend is closed and unregistered only when the count reaches zero.
        """
        key = self._backend_key(b)
        if key not in gateway._backends:
            gateway.register_backend(key, self._build_backend(b))
        self._backend_refs[key] = self._backend_refs.get(key, 0) + 1
        return _BackendRef(self, key)

    def _release_backend(self, key: str) -> None:
        left = self._backend_refs.get(key, 1) - 1
        if left > 0:
            self._backend_refs[key] = left
            return
        self._backend_refs.pop(key, None)
        gateway = self._gateway
        backend = gateway._backends.pop(key, None) if gateway is not None else None
        close = getattr(backend, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()

    def _note_open(self, session_id: str, ref: _BackendRef) -> None:
        """A session was recorded: it now owns the reference, released when it closes."""
        self._session_backend[session_id] = ref

    def _note_closed(self, session_id: str) -> None:
        ref = self._session_backend.pop(session_id, None)
        if ref is not None:
            ref.release()

    def _open_one(
        self, key: tuple[str, str], entry: _Open, b: ResolvedBinding, ref: _BackendRef,
        dispatch_id: str, job_id: str, deadline: float,
    ) -> asyncio.Future[tuple[Any, Path]]:
        """Queue one session open; its completion step (on the loop) assigns ownership.

        The step runs even if ``prepare`` was cancelled. If this dispatch is gone, the step
        itself queues the close, so the session cannot be orphaned.
        """
        endpoint = self._endpoint
        assert endpoint is not None
        access = parse_access(b.access)
        result: asyncio.Future[tuple[Any, Path]] = asyncio.get_running_loop().create_future()

        async def run() -> None:
            try:
                got = await endpoint.open_session(
                    principal=b.principal, attachment_id=ref.key, scope=b.scope,
                    permissions=b.permissions, dispatch_id=dispatch_id, deadline_at=deadline,
                    job_id=job_id, access=access if access.kind == "grant" else None,
                    ended=entry.ended,
                    caps=MemoryCaps(
                        recall_cap_bytes=b.recall_cap_bytes,
                        provenance=cast("Literal['required', 'optional']", b.provenance),
                        per_call_budget_seconds=b.lock_timeout_seconds,
                        lock_timeout_seconds=b.lock_timeout_seconds,
                    ),
                )
            except BaseException as exc:  # noqa: BLE001 — handed to the awaiter
                ref.release()  # no session was recorded: the reference goes back
                if not result.done():
                    result.set_exception(exc)
                return
            session, _path = got
            # Completion step, on the loop, regardless of any awaiter.
            self._note_open(session.session_id, ref)
            if self._open.get(key) is entry and not (self._closing or self._closed):
                entry.session_ids.append(session.session_id)
            else:
                self._spawn(self._close_ids([session.session_id]))
            if not result.done():
                result.set_result(got)

        self._spawn(run())
        return result

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _settle(self, coro: Any) -> None:
        """Run cleanup as a gate-owned task and wait for it; a caller cancel does not stop it."""
        await asyncio.shield(self._spawn(coro))

    async def prepare(
        self, job_id: str, sheet: SheetIdentity, dispatch_id: str, deadline: float,
    ) -> MemoryDelivery | None:
        reg = self._jobs.get(job_id)
        if reg is None:
            return None
        try:
            bindings = resolve_memory(
                reg.config,
                SheetPhase(
                    # Only a movement name the author declared (V342 accepts `all`, a number,
                    # or a declared name). The built-in lifecycle names of unnamed movements
                    # (``sheet.phase``) must not match an attachment meant for a named one.
                    name=reg.movement_names.get(sheet.movement, ""),
                    movement=sheet.movement,
                ),
                user=self._user, run_dir=self._run_dir,
            )
        except MemoryBackendError as exc:
            raise MemoryGateError("(resolve)", _reason(exc)) from exc
        if not bindings:
            return None
        key = (job_id, dispatch_id)
        if key in self._open:
            raise MemoryGateError(bindings[0].name, f"dispatch {dispatch_id!r} is already open")
        if deadline <= self._clock():
            raise MemoryGateError(bindings[0].name, "the dispatch deadline has already passed")
        entry = _Open(job_id, opened_at=self._monotonic())
        self._open[key] = entry
        self._ensure_reaper()
        opened: list[OpenedSession] = []
        current = bindings[0].name

        def still_ours() -> None:
            if self._closing or self._closed:
                raise MemoryGateError(current, "the memory gate is shutting down")
            if self._open.get(key) is not entry:
                raise MemoryGateError(current, "the job was deregistered while memory opened")

        try:
            gateway, _endpoint = self._ensure()
            for b in bindings:
                current = b.name
                still_ours()
                ref = self._register_backend(gateway, b)
                try:
                    fut = self._open_one(key, entry, b, ref, dispatch_id, job_id, deadline)
                except BaseException:
                    ref.release()
                    raise
                session, path = await asyncio.shield(fut)
                opened.append(OpenedSession(b.name, session.session_id, path))
                still_ours()
            by_name = {s.attachment: s for s in opened}
            entries: dict[str, dict[str, Any]] = {}
            for b in bindings:
                key_name, value = mcp_config_entry(
                    b, by_name[b.name].socket_path, budget_seconds=b.lock_timeout_seconds,
                )
                entries[key_name] = value
            return MemoryDelivery(render_manifest(bindings), entries, tuple(opened))
        except BaseException as exc:
            # Whatever was recorded is closed by teardown; an open still in flight is closed
            # by its own completion step (the entry is gone by then).
            await self._settle(self.teardown(job_id, dispatch_id, only=entry))
            if isinstance(exc, MemoryGateError):
                raise
            if isinstance(exc, Exception):
                raise MemoryGateError(current, _reason(exc)) from exc
            raise

    async def _close_ids(self, session_ids: list[str]) -> tuple[int, list[str]]:
        """Close sessions (idempotent); returns (calls still running, error texts)."""
        in_flight = 0
        errors: list[str] = []
        endpoint = self._endpoint
        for sid in session_ids:
            try:
                if endpoint is None:
                    raise RuntimeError("endpoint missing")
                in_flight += await endpoint.close_session(sid)
            except BaseException as exc:  # noqa: BLE001 — teardown must not raise into the caller
                errors.append(f"close_session {sid}: {_reason(exc)}")
                try:
                    if endpoint is not None:
                        await endpoint.unbind(sid)
                        gw = self._gateway
                        if gw is not None:
                            await self._run(gw.close_session, sid)
                except BaseException as exc2:  # noqa: BLE001 — recorded, teardown continues
                    errors.append(f"fallback close {sid}: {_reason(exc2)}")
            finally:
                self._note_closed(sid)
        return in_flight, errors

    # ------------------------------------------------------------ config files

    def _dispatch_dir(self, workspace: Path, job_id: str, dispatch_id: str) -> Path:
        digest = hashlib.sha256(f"{job_id}\x1f{dispatch_id}".encode()).hexdigest()[:16]
        return Path(workspace) / CONFIG_DIR_NAME / digest

    def merge_config(
        self, job_id: str, dispatch_id: str, delivery: MemoryDelivery,
        existing_path: Path | str | None, workspace: Path | str,
    ) -> asyncio.Future[Path]:
        """Queue this dispatch's merged MCP config under its own directory.

        Returns a future. Its completion step runs on the loop whether or not anybody is
        still awaiting: if the dispatch is gone by then, the file is queued for deletion.
        """
        ddir = self._dispatch_dir(Path(workspace), job_id, dispatch_id)
        entry = self._open.get((job_id, dispatch_id))
        if entry is not None:
            entry.dir = ddir
            self._workspaces.setdefault(job_id, set()).add(Path(workspace))
        result: asyncio.Future[Path] = asyncio.get_running_loop().create_future()
        job = self._submit(_write_dispatch_config, existing_path, delivery.mcp_entries, ddir)

        def done(f: asyncio.Future[Path]) -> None:
            e = self._open.get((job_id, dispatch_id))
            if f.cancelled() or f.exception() is not None:
                if not result.done():
                    if f.cancelled():
                        result.cancel()
                    else:
                        result.set_exception(f.exception() or RuntimeError("config write failed"))
                return
            path = f.result()
            if e is not None and e is entry and not (self._closing or self._closed):
                e.config_path = path
            else:  # the dispatch is gone: nothing owns this file
                self._remove_dir_now_or_queued(ddir)
            if not result.done():
                result.set_result(path)

        job.add_done_callback(done)
        return result

    async def deliver(
        self, job_id: str, sheet: SheetIdentity, *, timeout_seconds: float,
        existing_mcp_config: Path | None, workspace: Path,
    ) -> PreparedMemory | None:
        """``prepare`` + ``merge_config`` as one all-or-nothing step for the adapter.

        Returns ``None`` when the job has no active attachment for this sheet. On any
        failure everything opened is torn down before the error propagates.
        """
        if job_id not in self._jobs:
            return None
        dispatch_id = f"s{sheet.num}-{uuid.uuid4().hex[:12]}"
        deadline = FAR_FUTURE  # lifetime is the dispatch's flag; see the module docstring
        delivery = await self.prepare(job_id, sheet, dispatch_id, deadline)
        if delivery is None:
            return None
        try:
            path = await asyncio.shield(self.merge_config(
                job_id, dispatch_id, delivery, existing_mcp_config, workspace,
            ))
        except BaseException as exc:
            await self._settle(self.teardown(job_id, dispatch_id))
            if isinstance(exc, Exception) and not isinstance(exc, MemoryGateError):
                raise MemoryGateError("(mcp config)", _reason(exc)) from exc
            raise
        return PreparedMemory(dispatch_id, delivery.manifest, path)

    # ------------------------------------------------------------ owner and reaper

    def set_owner(self, job_id: str, dispatch_id: str, task: asyncio.Task[Any]) -> None:
        """Record the task whose life bounds this dispatch: when it ends, so do the sessions."""
        entry = self._open.get((job_id, dispatch_id))
        if entry is None:
            return
        entry.owner = task
        task.add_done_callback(lambda _t: self._end(entry))

    def _ensure_reaper(self) -> None:
        if not self._start_reaper or self._reaper is not None or self._closing or self._closed:
            return
        self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        try:
            while self._open and not (self._closing or self._closed):
                await asyncio.sleep(self._reap_interval)
                await self.tick()
        finally:
            self._reaper = None

    async def tick(self) -> None:
        """One hygiene pass; public so a test can drive it with an injected clock.

        Enqueues the teardown of a dispatch whose flag is set but whose teardown was never
        enqueued (a missed one), and ends a dispatch that still has no owner after the grace.
        Correctness never depends on it: the flag alone refuses calls.
        """
        now = self._monotonic()
        for (job_id, dispatch_id), entry in list(self._open.items()):
            if entry.cleanup_queued:
                continue
            if not entry.ended.is_set():
                if entry.owner is not None or now - entry.opened_at <= self._ownerless_grace:
                    continue
                self._end(entry)
                reason = "no owner within the grace"
            else:
                reason = "owner finished without teardown"
            entry.cleanup_queued = True
            _logger.warning(
                "memory_gate.reaped",
                extra={"job_id": job_id, "dispatch_id": dispatch_id, "reason": reason},
            )
            self._spawn(self.teardown(job_id, dispatch_id))

    # ------------------------------------------------------------ stale files (SIGKILL)

    async def sweep_stale(self, run_dir: Path | str | None = None) -> int:
        """Run :meth:`_sweep_sockets` on the worker (never on the loop); returns how many.

        With no ``<run_dir>/s`` directory there is nothing to sweep (no memory job has ever
        run here): return 0 without starting the worker thread.
        """
        base = Path(run_dir) if run_dir is not None else self._run_dir
        if not (base / "s").is_dir():
            return 0
        return int(await self._run(self._sweep_sockets, run_dir))

    def _sweep_sockets(self, run_dir: Path | str | None = None) -> int:
        """Unlink socket files left in ``<run_dir>/s`` by a dead process; returns how many.

        Call once at conductor start. Runs as a worker job, through a descriptor held on the ``s``
        directory (``O_NOFOLLOW``, owned by us). It considers only ``s_*.sock`` that are
        real sockets we own and that this process does not hold, and removes one only when
        a non-blocking connect is refused. The probe connect is closed at once; it never
        waits on, or reads from, a live endpoint. A second conductor on one run directory
        is unsupported: a socket it has bound but not yet listening on looks stale.
        """
        import socket as _socket
        import stat as _stat

        base = Path(run_dir) if run_dir is not None else self._run_dir
        mine = {p.name for p in self._bound_socket_paths()}
        try:
            fd = os.open(base / "s", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return 0
        removed = 0
        try:
            if os.fstat(fd).st_uid != os.getuid():
                return 0
            for name in os.listdir(fd):
                if not (name.startswith("s_") and name.endswith(".sock")) or name in mine:
                    continue
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    continue
                if not _stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    continue
                probe = _socket.socket(_socket.AF_UNIX)
                probe.setblocking(False)
                try:
                    probe.connect(f"/proc/self/fd/{fd}/{name}")
                    continue  # accepted: something listens, not stale
                except BlockingIOError:
                    continue  # a listener with a full backlog is alive
                except (ConnectionRefusedError, FileNotFoundError):
                    pass
                except OSError:
                    continue
                finally:
                    probe.close()
                try:
                    os.unlink(name, dir_fd=fd)
                    removed += 1
                except OSError:
                    _logger.warning("memory_gate.sweep_unlink_failed", extra={"name": name})
        finally:
            os.close(fd)
        if removed:
            _logger.info("memory_gate.stale_sockets_removed", extra={"count": removed})
        return removed

    async def sweep_stale_configs(self, workspace: Path | str) -> int:
        """Remove dispatch directories a dead conductor left in ``workspace``; returns how many.

        Runs on the worker, and spares every directory of a dispatch this gate has open (asked
        when the worker reaches it), so it is safe while the recovered job is already running.
        """
        return int(await self._run(_remove_dispatch_dirs, Path(workspace), self._live_dirs))

    def schedule_config_sweep(self, workspaces: Iterable[Path | str]) -> None:
        """Fire-and-forget :meth:`sweep_stale_configs` for each workspace (needs a running loop).

        Called when a job is recovered after a conductor restart: its sheets' dispatch
        directories from the previous process are dead, and nothing else would remove them.
        A failure is logged, never raised.
        """
        paths = list(dict.fromkeys(Path(w) for w in workspaces))
        if not paths:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        async def go() -> None:
            for ws in paths:
                try:
                    await self.sweep_stale_configs(ws)
                except Exception:  # noqa: BLE001 — a failed sweep must not touch the job
                    _logger.warning(
                        "memory_gate.config_sweep_failed", extra={"workspace": str(ws)},
                        exc_info=True,
                    )

        self._spawn(go())

    def _bound_socket_paths(self) -> list[Path]:
        endpoint = self._endpoint
        return [b.path for b in endpoint._bound.values()] if endpoint is not None else []

    def schedule_teardown(self, job_id: str, dispatch_id: str) -> None:
        """Fire-and-forget ``teardown`` for a done-callback (needs a running loop)."""
        try:
            self._spawn(self.teardown(job_id, dispatch_id))
        except RuntimeError:
            return

    # ------------------------------------------------------------ teardown

    async def teardown(
        self, job_id: str, dispatch_id: str, *, only: _Open | None = None,
    ) -> TeardownReport:
        """Close sockets and sessions and remove the config. Idempotent; never raises."""
        entry = self._open.get((job_id, dispatch_id))
        if entry is None or (only is not None and entry is not only):
            return TeardownReport()
        self._end(entry)  # first: the sessions refuse from this instant, before any I/O
        del self._open[(job_id, dispatch_id)]
        errors: list[str] = []
        try:
            in_flight, errors = await asyncio.shield(self._close_ids(list(entry.session_ids)))
        except asyncio.CancelledError:
            in_flight = 0  # the shielded close keeps running; the config removal is queued below
            errors.append("teardown cancelled")
        if entry.dir is not None:
            # Ordered after any write still on the worker; the job survives a cancel here.
            removal = self._remove_dir_now_or_queued(entry.dir)
            if removal is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(removal)
                if removal.done() and removal.exception() is not None:  # the gate shut down
                    with contextlib.suppress(Exception):
                        _remove_dispatch_dir(entry.dir)
        report = TeardownReport(in_flight, tuple(errors))
        if not report.clean:
            _logger.warning(
                "memory_gate.teardown_incomplete",
                extra={
                    "job_id": job_id, "dispatch_id": dispatch_id,
                    "in_flight": in_flight, "errors": list(errors),
                },
            )
        return report

    async def shutdown(self) -> TeardownReport:
        """Close everything within a bound. Never awaits or cancels a caller's task.

        Sets ``_closing`` first (no new prepare; an open still in flight is closed by its
        own completion step), queues a close for every open dispatch, waits for the worker
        queue to drain up to ``shutdown_bound_seconds``, reports what is still pending,
        then stops the worker.
        """
        self._closing = True
        for entry in self._open.values():
            self._end(entry)
            entry.cleanup_queued = True
        reaper, self._reaper = self._reaper, None
        if reaper is not None:
            reaper.cancel()  # the gate's own task; never counted as pending work
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reaper
        in_flight = 0
        errors: list[str] = []
        dirs = [e.dir for e in self._open.values() if e.dir is not None]
        for job_id, dispatch_id in list(self._open):
            self._spawn(self.teardown(job_id, dispatch_id))
        deadline = asyncio.get_running_loop().time() + self._shutdown_bound
        while True:
            pending: list[Any] = [*self._background, *self._pending]
            if not pending:
                break
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                errors.append(f"{len(pending)} gate job(s) still pending at shutdown")
                break
            await asyncio.wait(pending, timeout=left)
        if self._endpoint is not None:
            try:
                remaining = max(deadline - asyncio.get_running_loop().time(), 0.1)
                closed = await asyncio.wait_for(self._endpoint.close(), remaining)
                for sid, left_calls in closed.items():
                    in_flight += left_calls
                    errors.append(f"session {sid}: {left_calls} call(s) still running")
            except TimeoutError:
                errors.append("endpoint close did not finish within the shutdown bound")
            except Exception as exc:  # noqa: BLE001 — shutdown reports, never raises
                errors.append(f"endpoint close: {_reason(exc)}")
        # From here on nothing is queued: late jobs fail at once, and the cleanup that a stuck
        # worker never reached is done inline, best effort.
        self._closed = True
        for fut in list(self._pending):
            if not fut.done():
                fut.set_exception(MemoryGateError("(gate)", "the memory gate is shut down"))
        for ddir in dirs:
            with contextlib.suppress(Exception):
                _remove_dispatch_dir(ddir)
        if self._worker is not None:
            self._worker.stop()
        return TeardownReport(in_flight, tuple(errors))

    # ------------------------------------------------------------ introspection

    def is_open(self, job_id: str, dispatch_id: str) -> bool:
        return (job_id, dispatch_id) in self._open

    @property
    def open_dispatches(self) -> int:
        return len(self._open)


__all__ = [
    "CONFIG_DIR_NAME", "FAR_FUTURE", "OWNERLESS_GRACE_SECONDS", "SHUTDOWN_BOUND_SECONDS",
    "SLOW_JOB_SECONDS",
    "MemoryConfigCollision",
    "MemoryDelivery", "MemoryDispatchGate",
    "MemoryGateError", "OpenedSession", "PreparedMemory", "SheetIdentity", "TeardownReport",
    "can_deliver", "lookup_profile", "merge_mcp_config",
]
