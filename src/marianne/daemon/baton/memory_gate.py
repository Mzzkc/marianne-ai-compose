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
* The harness gets one MCP config file per dispatch (``merge_mcp_config``) holding
  the technique servers plus one shim entry per attachment. The file names a
  socket and nothing else: no principal, scope, grant or session id.
* ``teardown`` never raises into the caller; it reports what it could not stop.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
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

#: Seconds added to a sheet's timeout when a session is armed (see ``arm_deadline``).
DEADLINE_GRACE_SECONDS = 60.0

#: Hard ceiling on a session opened at dispatch, before its sheet starts running. A sheet
#: can wait a long time to start (an instrument that materialises its MCP config in a
#: workspace file holds a per-file lock for a whole run, and that lock is taken inside the
#: backend's ``execute``, after the last point the adapter controls). The sheet's own clock
#: therefore starts at ``arm_deadline``, called just before ``execute``; the ceiling only
#: bounds a session whose sheet never starts, and ``teardown`` normally closes it first.
SESSION_CEILING_SECONDS = 12 * 3600.0


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


@dataclass
class _Open:
    job_id: str
    session_ids: list[str] = field(default_factory=list)
    config_path: Path | None = None


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
    The existing file is only read.
    """
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


class MemoryDispatchGate:
    """Opens and closes the memory sessions of one dispatch at a time, per (job, dispatch)."""

    def __init__(
        self, *, run_dir: Path | str, memory_root: Path | str, audit_dir: Path | str,
        user: str, clock: Callable[[], float] = time.time,
    ) -> None:
        self._run_dir = Path(run_dir)
        self._memory_root = Path(memory_root)
        self._audit_dir = Path(audit_dir)
        self._user = user
        self._clock = clock
        # Heavy members: created on the first dispatch that needs memory.
        self._gateway: Gateway | None = None
        self._endpoint: MemoryEndpoint | None = None
        self._audit: AuditLog | None = None
        self._registered_backends: set[str] = set()
        self._jobs: dict[str, _Registration] = {}
        self._open: dict[tuple[str, str], _Open] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._closing = False
        self._preparing: set[asyncio.Task[Any]] = set()

    def now(self) -> float:
        return self._clock()

    # ------------------------------------------------------------ registration

    def register_job(
        self, job_id: str, memory_config: MemoryConfig | None,
        movement_names: Mapping[int, str] | None = None,
    ) -> None:
        if memory_config is None:
            self._jobs.pop(job_id, None)
            return
        self._jobs[job_id] = _Registration(memory_config, dict(movement_names or {}))

    def deregister_job(self, job_id: str) -> None:
        """Forget the job and tear down any session still open for it (scheduled if async)."""
        self._jobs.pop(job_id, None)
        stale = [key for key, entry in self._open.items() if entry.job_id == job_id]
        if not stale:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning(
                "memory_gate.deregister_without_loop",
                extra={"job_id": job_id, "dispatches": len(stale)},
            )
            return
        for key_job, key_dispatch in stale:
            task = loop.create_task(self.teardown(key_job, key_dispatch))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def drain(self) -> None:
        """Wait for teardowns scheduled by ``deregister_job``."""
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    # ------------------------------------------------------------ prepare

    def _ensure(self) -> tuple[Gateway, MemoryEndpoint]:
        if self._closed or self._closing:
            raise MemoryGateError("(gate)", "the memory gate is shut down")
        if self._gateway is None or self._endpoint is None:
            self._run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._audit = AuditLog(self._audit_dir)
            self._gateway = Gateway(audit=self._audit)
            self._endpoint = MemoryEndpoint(self._gateway, run_dir=self._run_dir)
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

    def _register_backend(self, gateway: Gateway, b: ResolvedBinding) -> None:
        key = self._backend_key(b)
        if key in self._registered_backends:
            return
        if b.system != "files":
            raise MemoryGateError(b.name, f"memory system {b.system!r} is not available")
        location = resolve_files_location(b.scope, self._memory_root)
        backend = files_backend_factory({
            "attachment_id": b.attachment_id, "scope": b.scope, **location.as_config(),
            "recall_cap_bytes": b.recall_cap_bytes, "provenance": b.provenance,
            "lock_timeout_seconds": b.lock_timeout_seconds,
        })
        gateway.register_backend(key, backend)
        self._registered_backends.add(key)

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
                    name=reg.movement_names.get(sheet.movement) or sheet.phase,
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
        entry = _Open(job_id)
        self._open[key] = entry
        task = asyncio.current_task()
        if task is not None:
            self._preparing.add(task)
        opened: list[OpenedSession] = []
        current = bindings[0].name

        def still_ours() -> None:
            # Every await below can let deregister_job or shutdown run; ownership is
            # re-proved after each one, never assumed.
            if self._closing or self._closed:
                raise MemoryGateError(current, "the memory gate is shutting down")
            if self._open.get(key) is not entry:
                raise MemoryGateError(current, "the job was deregistered while memory opened")

        try:
            gateway, endpoint = self._ensure()
            for b in bindings:
                current = b.name
                still_ours()
                self._register_backend(gateway, b)
                access = parse_access(b.access)
                session, path = await endpoint.open_session(
                    principal=b.principal, attachment_id=self._backend_key(b), scope=b.scope,
                    permissions=b.permissions, dispatch_id=dispatch_id, deadline_at=deadline,
                    job_id=job_id, access=access if access.kind == "grant" else None,
                    caps=MemoryCaps(
                        recall_cap_bytes=b.recall_cap_bytes,
                        provenance=cast("Literal['required', 'optional']", b.provenance),
                        per_call_budget_seconds=b.lock_timeout_seconds,
                        lock_timeout_seconds=b.lock_timeout_seconds,
                    ),
                )
                opened.append(OpenedSession(b.name, session.session_id, path))
                entry.session_ids.append(session.session_id)
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
            # Close what this call opened even when someone else already took the entry.
            await asyncio.shield(self._close_sessions([s.session_id for s in opened]))
            await asyncio.shield(self.teardown(job_id, dispatch_id, only=entry))
            if isinstance(exc, MemoryGateError):
                raise
            if isinstance(exc, Exception):
                raise MemoryGateError(current, _reason(exc)) from exc
            raise
        finally:
            if task is not None:
                self._preparing.discard(task)

    async def _close_sessions(self, session_ids: list[str]) -> tuple[int, list[str]]:
        """Close sessions idempotently; returns (calls still running, error texts)."""
        in_flight = 0
        errors: list[str] = []
        endpoint, gateway = self._endpoint, self._gateway
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
                    if gateway is not None:
                        gateway.close_session(sid)
                except BaseException as exc2:  # noqa: BLE001 — recorded, teardown continues
                    errors.append(f"fallback close {sid}: {_reason(exc2)}")
                if isinstance(exc, asyncio.CancelledError):
                    errors.append("teardown cancelled")
        return in_flight, errors

    def merge_config(
        self, job_id: str, dispatch_id: str, delivery: MemoryDelivery,
        existing_path: Path | str | None, dest_dir: Path | str,
    ) -> Path:
        """Write this dispatch's merged MCP config and remember it for ``teardown``."""
        digest = hashlib.sha256(f"{job_id}\x1f{dispatch_id}".encode()).hexdigest()[:16]
        path = merge_mcp_config(
            existing_path, delivery.mcp_entries, dest_dir, name=f".memory-mcp-{digest}.json",
        )
        entry = self._open.get((job_id, dispatch_id))
        if entry is not None:
            entry.config_path = path
        else:  # no open dispatch to own it: do not leave a stray file
            with contextlib.suppress(OSError):
                path.unlink()
            raise MemoryGateError("(mcp config)", "no open dispatch owns this config")
        return path

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
        deadline = self._clock() + SESSION_CEILING_SECONDS
        delivery = await self.prepare(job_id, sheet, dispatch_id, deadline)
        if delivery is None:
            return None
        try:
            path = self.merge_config(job_id, dispatch_id, delivery, existing_mcp_config, workspace)
        except BaseException as exc:
            await asyncio.shield(self.teardown(job_id, dispatch_id))
            if isinstance(exc, Exception) and not isinstance(exc, MemoryGateError):
                raise MemoryGateError("(mcp config)", _reason(exc)) from exc
            raise
        text = delivery.manifest
        return PreparedMemory(dispatch_id, text, path)

    def arm_deadline(self, job_id: str, dispatch_id: str, timeout_seconds: float) -> None:
        """Start the sheet's clock now: sessions end ``timeout + grace`` from this moment.

        Called when execution actually begins. Never raises: an unknown or already-ended
        dispatch is logged and left alone (the gateway refuses to revive a closed session).
        """
        entry = self._open.get((job_id, dispatch_id))
        gateway = self._gateway
        if entry is None or gateway is None:
            return
        deadline = self._clock() + max(timeout_seconds, 0.0) + DEADLINE_GRACE_SECONDS
        for sid in entry.session_ids:
            try:
                gateway.sessions.set_deadline(sid, deadline)
            except MemoryBackendError as exc:
                _logger.warning(
                    "memory_gate.arm_deadline_refused",
                    extra={"job_id": job_id, "dispatch_id": dispatch_id, "reason": _reason(exc)},
                )

    def schedule_teardown(self, job_id: str, dispatch_id: str) -> None:
        """Fire-and-forget ``teardown`` for a done-callback (needs a running loop)."""
        try:
            task = asyncio.get_running_loop().create_task(self.teardown(job_id, dispatch_id))
        except RuntimeError:
            return
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ------------------------------------------------------------ teardown

    async def teardown(
        self, job_id: str, dispatch_id: str, *, only: _Open | None = None,
    ) -> TeardownReport:
        """Close sockets and sessions and remove the config file. Idempotent; never raises."""
        entry = self._open.get((job_id, dispatch_id))
        if entry is None or (only is not None and entry is not only):
            return TeardownReport()
        del self._open[(job_id, dispatch_id)]
        in_flight, errors = await self._close_sessions(list(entry.session_ids))
        if entry.config_path is not None:
            try:
                entry.config_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"remove config {entry.config_path.name}: {exc}")
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
        """Close every dispatch and the endpoint. Called when the conductor stops."""
        self._closing = True  # first: no new prepare starts, in-flight ones unwind
        await self.drain()
        if self._preparing:
            await asyncio.gather(*list(self._preparing), return_exceptions=True)
        in_flight = 0
        errors: list[str] = []
        for job_id, dispatch_id in list(self._open):
            report = await self.teardown(job_id, dispatch_id)
            in_flight += report.in_flight
            errors.extend(report.errors)
        self._closed = True
        if self._endpoint is not None:
            try:
                for sid, left in (await self._endpoint.close()).items():
                    in_flight += left
                    errors.append(f"session {sid}: {left} call(s) still running")
            except Exception as exc:  # noqa: BLE001 — shutdown reports, never raises
                errors.append(f"endpoint close: {_reason(exc)}")
        return TeardownReport(in_flight, tuple(errors))

    # ------------------------------------------------------------ introspection

    def is_open(self, job_id: str, dispatch_id: str) -> bool:
        return (job_id, dispatch_id) in self._open

    @property
    def open_dispatches(self) -> int:
        return len(self._open)


__all__ = [
    "DEADLINE_GRACE_SECONDS", "SESSION_CEILING_SECONDS", "MemoryConfigCollision",
    "MemoryDelivery", "MemoryDispatchGate",
    "MemoryGateError", "OpenedSession", "PreparedMemory", "SheetIdentity", "TeardownReport",
    "can_deliver", "lookup_profile", "merge_mcp_config",
]
