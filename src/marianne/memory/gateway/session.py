"""Per-dispatch sessions (BE2): ``admitted -> active -> draining -> closed | revoked``."""

from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from marianne.memory.errors import (
    AuthorityRevoked,
    NotFound,
    PermissionDenied,
    SchemaViolation,
    Unauthenticated,
)
from marianne.memory.model import PERMISSION_VALUES, parse_scope, validate_principal


class SessionState(str, Enum):
    ADMITTED = "admitted"
    ACTIVE = "active"
    DRAINING = "draining"
    CLOSED = "closed"
    REVOKED = "revoked"


_LEGAL: dict[SessionState, frozenset[SessionState]] = {
    SessionState.ADMITTED: frozenset({
        SessionState.ACTIVE, SessionState.DRAINING, SessionState.CLOSED, SessionState.REVOKED,
    }),
    SessionState.ACTIVE: frozenset({
        SessionState.DRAINING, SessionState.CLOSED, SessionState.REVOKED,
    }),
    SessionState.DRAINING: frozenset({SessionState.CLOSED, SessionState.REVOKED}),
    SessionState.CLOSED: frozenset(),
    SessionState.REVOKED: frozenset(),
}
TERMINAL = frozenset({SessionState.CLOSED, SessionState.REVOKED})


@dataclass(frozen=True)
class MemoryCaps:
    """Per-session caps (spec Interfaces 1 ``MemoryCaps``)."""

    recall_cap_bytes: int = 32768
    dispatch_recall_quota_bytes: int | None = None
    provenance: Literal["required", "optional"] = "optional"
    per_call_budget_seconds: float = 10.0
    lock_timeout_seconds: float = 10.0


@dataclass
class Session:
    session_id: str
    principal: str
    attachment: tuple[str, str]  # (attachment_id, canonical_scope)
    permissions: tuple[str, ...]
    caps: MemoryCaps
    deadline_at: float
    dispatch_id: str
    job_id: str | None
    opened_at: float
    state: SessionState = SessionState.ADMITTED
    recall_bytes_used: int = 0
    call_count: int = 0
    last_receipt: str | None = None
    receipts: dict[str, str] = field(default_factory=dict)

    @property
    def scope(self) -> str:
        return self.attachment[1]


class SessionTable:
    """Thread-safe table of sessions; legal transitions only."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def open(
        self, *, principal: str, attachment_id: str, scope: str,
        permissions: tuple[str, ...], dispatch_id: str, deadline_at: float,
        job_id: str | None = None, caps: MemoryCaps | None = None,
    ) -> Session:
        validate_principal(principal)
        canonical = parse_scope(scope).canonical
        unknown = sorted(set(permissions) - PERMISSION_VALUES)
        if unknown:
            raise SchemaViolation(f"unknown permission(s) {unknown}", subtype="permission_invalid")
        if not attachment_id or not dispatch_id:
            raise SchemaViolation(
                "attachment_id and dispatch_id are required", subtype="session_invalid"
            )
        if not isinstance(deadline_at, (int, float)) or not math.isfinite(deadline_at):
            raise SchemaViolation("deadline_at must be finite", subtype="deadline_invalid")
        session = Session(
            session_id=f"s-{uuid.uuid4().hex[:16]}", principal=principal,
            attachment=(attachment_id, canonical), permissions=tuple(permissions),
            caps=caps or MemoryCaps(), deadline_at=float(deadline_at),
            dispatch_id=dispatch_id, job_id=job_id, opened_at=time.time(),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError:
                raise NotFound(f"no session {session_id!r}", subtype="session_not_found") from None

    def transition(self, session_id: str, to: SessionState) -> Session:
        with self._lock:
            s = self.get(session_id)
            if to not in _LEGAL[s.state]:
                raise SchemaViolation(
                    f"session {s.state.value} -> {to.value} is not a legal transition",
                    subtype="session_transition_illegal",
                )
            s.state = to
            return s

    def admit_call(self, session_id: str, *, writes: bool) -> Session:
        """Gate one call by state; the first successful gate activates the session."""
        with self._lock:
            s = self.get(session_id)
            if s.state is SessionState.REVOKED:
                raise AuthorityRevoked(
                    "session authority was revoked", subtype="authority_revoked",
                    remedy="open a new session",
                )
            if s.state is SessionState.CLOSED:
                raise Unauthenticated(
                    "session is closed", subtype="session_closed", remedy="open a new session",
                )
            if s.state is SessionState.DRAINING and writes:
                raise PermissionDenied(
                    "session is draining; new writes are refused", subtype="session_draining",
                )
            if s.state is SessionState.ADMITTED:
                s.state = SessionState.ACTIVE
            s.call_count += 1
            return s

    def close(self, session_id: str) -> Session:
        with self._lock:
            s = self.get(session_id)
            if s.state not in TERMINAL:
                s.state = SessionState.CLOSED
            return s

    def revoke(self, session_id: str) -> Session:
        with self._lock:
            s = self.get(session_id)
            if s.state not in TERMINAL:
                s.state = SessionState.REVOKED
            return s

    def revoke_for(self, *, principal: str) -> list[str]:
        """Revoke every live session of ``principal``; returns their ids."""
        with self._lock:
            hit = [
                s.session_id for s in self._sessions.values()
                if s.principal == principal and s.state not in TERMINAL
            ]
            for sid in hit:
                self._sessions[sid].state = SessionState.REVOKED
            return hit
