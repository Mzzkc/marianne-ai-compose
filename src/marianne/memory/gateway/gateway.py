"""The in-process gateway: session lookup, grant check, deadline clamp, backend call (BE3/5/6/7)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

from marianne.memory.access import AccessRef, GrantStore, scope_covers
from marianne.memory.backend_protocol import MemoryBackend, RequestContext
from marianne.memory.errors import (
    AuthorityRevoked,
    DeadlineExceeded,
    MemoryBackendError,
    NotFound,
    PermissionDenied,
    RecallCapExceeded,
    SchemaViolation,
    ScopeRefused,
)
from marianne.memory.gateway.audit import AuditLog
from marianne.memory.gateway.session import (
    TERMINAL,
    MemoryCaps,
    Session,
    SessionState,
    SessionTable,
)
from marianne.memory.model import (
    BindResult,
    DeclareResult,
    FetchResult,
    ForgetResult,
    Permission,
    Provenance,
    RecallResult,
    VerifyResult,
    parse_scope,
)

T = TypeVar("T")

#: Tool -> (permission, is_write) per BE3 step 2.
TOOL_PERMISSIONS: dict[str, tuple[Permission, bool]] = {
    "declare": (Permission.WRITE, True),
    "recall": (Permission.READ, False),
    "verify": (Permission.VERIFY, False),
    "bind": (Permission.BIND, True),
    "forget": (Permission.FORGET, True),
    "fetch": (Permission.READ, False),
}


class Gateway:
    """A library a conductor can host. Holds no socket; reaches backends only via the Protocol."""

    def __init__(self, *, grants: GrantStore | None = None, audit: AuditLog | None = None) -> None:
        self.sessions = SessionTable()
        self._audit = audit
        self._grants = grants
        self._backends: dict[str, MemoryBackend] = {}

    # ------------------------------------------------------------ wiring

    def register_backend(self, attachment_id: str, backend: MemoryBackend) -> None:
        self._backends[attachment_id] = backend

    def open_session(
        self, *, principal: str, attachment_id: str, scope: str, dispatch_id: str,
        deadline_at: float, permissions: tuple[str, ...] = (), job_id: str | None = None,
        caps: MemoryCaps | None = None, access: AccessRef | None = None,
    ) -> Session:
        """Admit a session. A ``grant:`` reference fixes the permission ceiling (BE3.3/3.4)."""
        if attachment_id not in self._backends:
            raise NotFound(
                f"attachment {attachment_id!r} has no registered backend",
                subtype="attachment_unbound",
            )
        canonical = parse_scope(scope).canonical
        if access is not None and access.kind == "grant":
            if self._grants is None:
                raise SchemaViolation(
                    "grant reference given but the gateway has no grant store",
                    subtype="grant_store_missing",
                )
            grant = self._grants.resolve_ref(access)
            if grant.principal != principal or not scope_covers(grant.scope, canonical):
                raise ScopeRefused(
                    f"grant {grant.name!r} does not belong to {principal} at {canonical}",
                    subtype="scope_refused",
                )
            permissions = tuple(
                p for p in grant.permissions if not permissions or p in permissions
            )
        session = self.sessions.open(
            principal=principal, attachment_id=attachment_id, scope=canonical,
            permissions=permissions, dispatch_id=dispatch_id, deadline_at=deadline_at,
            job_id=job_id, caps=caps,
        )
        session.grant_ref = access if access is not None and access.kind == "grant" else None
        if self._audit is not None:
            self._audit.append(
                dispatch_id=session.dispatch_id, session_id=session.session_id,
                principal=session.principal, event="session_opened", scope=session.scope,
                result_status="ok",
            )
        return session

    def close_session(self, session_id: str) -> Session:
        s = self.sessions.get(session_id)
        was_live = s.state not in TERMINAL
        self.sessions.close(session_id)
        if self._audit is not None and was_live:
            self._audit.append(
                dispatch_id=s.dispatch_id, session_id=s.session_id, principal=s.principal,
                event="session_closed", seal=True, scope=s.scope, result_status="ok",
                call_count=s.call_count, bytes_read=s.bytes_read, bytes_written=s.bytes_written,
                duration_seconds=time.time() - s.opened_at,
            )
        return s

    # ------------------------------------------------------------ the pipeline

    def _context(
        self, s: Session, permission: Permission, budget_seconds: float | None,
    ) -> RequestContext:
        now = time.time()
        budget = s.caps.per_call_budget_seconds
        if budget_seconds is not None:
            budget = min(budget, budget_seconds)
        deadline = min(s.deadline_at, now + budget)
        if deadline <= now:
            raise DeadlineExceeded(
                "the session or call budget is already spent",
                subtype="deadline_exceeded", remedy="open a new session or raise the budget",
            )
        return RequestContext(
            principal=s.principal, scope=s.scope, permissions=s.permissions,
            deadline_at=deadline, dispatch_id=s.dispatch_id, job_id=s.job_id,
            session_id=s.session_id,
        )

    def _check_grant(self, s: Session, permission: Permission) -> None:
        if self._grants is None:
            return
        ref = s.grant_ref
        try:
            if ref is not None:
                self._grants.resolve_ref(ref)
        except AuthorityRevoked:
            self.sessions.revoke(s.session_id)
            raise

    def _scope(self, s: Session, scope: str | None) -> str:
        if scope is None:
            return s.scope
        canonical = parse_scope(scope).canonical
        if canonical != s.scope and not scope_covers(s.scope, canonical):
            raise ScopeRefused(
                f"scope {scope!r} is outside the session scope {s.scope}", subtype="scope_refused",
            )
        return canonical

    def _run(
        self, session_id: str, tool: str, budget_seconds: float | None,
        call: Callable[[MemoryBackend, RequestContext, Session], T],
        fact_id: str | None = None,
    ) -> T:
        permission, _ = TOOL_PERMISSIONS[tool]
        started = time.time()
        s = self.sessions.get(session_id)  # unknown session: nothing to attribute an audit line to
        try:
            s = self.sessions.admit_call(session_id, permission.value)
            self._check_grant(s, permission)
            ctx = self._context(s, permission, budget_seconds)
            result = call(self._backends[s.attachment[0]], ctx, s)
        except MemoryBackendError as exc:
            self._record(s, tool, started, fact_id, None, exc)
            raise
        self._record(s, tool, started, fact_id, result, None)
        return result

    def _record(
        self, s: Session, tool: str, started: float, fact_id: str | None,
        result: Any, exc: MemoryBackendError | None,
    ) -> None:
        if self._audit is None:
            return
        returned: list[str] = []
        size = 0
        if isinstance(result, dict):
            fact_id = fact_id or result.get("fact_id")
            for item in result.get("results", ()):
                returned.append(item["fact_id"])
                size += len(item["content"].encode("utf-8"))
            if "bytes" in result:
                size = len(str(result["bytes"]).encode("utf-8"))
        if tool == "recall" or tool == "fetch":
            s.bytes_read += size
        self._audit.append(
            dispatch_id=s.dispatch_id, session_id=s.session_id, principal=s.principal,
            event="operation", scope=s.scope, tool=tool, fact_id=fact_id,
            returned_fact_ids=returned, result_status="ok" if exc is None else "refused",
            latency_ms=(time.time() - started) * 1000.0, bytes=size,
            error_code=None if exc is None else exc.code.name,
        )

    # ------------------------------------------------------------ the six tools

    def declare(
        self, session_id: str, fact: str, *, scope: str | None = None,
        provenance: Provenance | None = None, confidence: float = 1.0,
        tags: list[str] | None = None, budget_seconds: float | None = None,
    ) -> DeclareResult:
        def go(b: MemoryBackend, ctx: RequestContext, s: Session) -> DeclareResult:
            if s.caps.provenance == "required" and not provenance:
                raise SchemaViolation(
                    "this attachment requires provenance on every declare",
                    subtype="provenance_required", remedy="pass provenance with evidence_locator",
                )
            return b.declare(ctx, fact, self._scope(s, scope), provenance, confidence, tags)

        return self._run(session_id, "declare", budget_seconds, go)

    def recall(
        self, session_id: str, query: str, *, scope: str | None = None, limit: int = 100,
        cursor: str | None = None, budget_seconds: float | None = None,
    ) -> RecallResult:
        def go(b: MemoryBackend, ctx: RequestContext, s: Session) -> RecallResult:
            quota = s.caps.dispatch_recall_quota_bytes
            if quota is not None and s.recall_bytes_used >= quota:
                raise RecallCapExceeded(
                    f"dispatch recall quota of {quota} bytes is spent",
                    subtype="recall_cap_exceeded", remedy="narrow the query or fetch by unit",
                )
            result = b.recall(ctx, query, self._scope(s, scope), limit, cursor)
            s.recall_bytes_used += sum(len(i["content"].encode("utf-8")) for i in result["results"])
            return result

        return self._run(session_id, "recall", budget_seconds, go)

    def verify(
        self, session_id: str, fact_id_or_hash: str, expected_evidence: str = "", *,
        budget_seconds: float | None = None,
    ) -> VerifyResult:
        def go(b: MemoryBackend, ctx: RequestContext, s: Session) -> VerifyResult:
            result = b.verify(ctx, fact_id_or_hash, expected_evidence)
            token = result.get("receipt_token")
            if token:
                s.receipts[result["fact_id"]] = token
            return result

        return self._run(session_id, "verify", budget_seconds, go, fact_id_or_hash)

    def bind(
        self, session_id: str, fact_id: str, target_symbol: str,
        evidence_receipt: str | None = None, *, budget_seconds: float | None = None,
    ) -> BindResult:
        def go(b: MemoryBackend, ctx: RequestContext, s: Session) -> BindResult:
            receipt = evidence_receipt or s.receipts.get(fact_id)
            if receipt is None:
                raise PermissionDenied(
                    f"no evidence receipt for {fact_id!r}: verify it first",
                    subtype="evidence_unverified", remedy="call verify, then bind",
                )
            return b.bind(ctx, fact_id, target_symbol, receipt)

        return self._run(session_id, "bind", budget_seconds, go, fact_id)

    def forget(
        self, session_id: str, fact_id: str, reason: str = "", *,
        budget_seconds: float | None = None,
    ) -> ForgetResult:
        def go(b: MemoryBackend, ctx: RequestContext, s: Session) -> ForgetResult:
            s.receipts.pop(fact_id, None)
            return b.forget(ctx, fact_id, reason)

        return self._run(session_id, "forget", budget_seconds, go, fact_id)

    def fetch(
        self, session_id: str, unit_id: str, offset: int, length: int, *,
        budget_seconds: float | None = None,
    ) -> FetchResult:
        def go(b: MemoryBackend, ctx: RequestContext, s: Session) -> FetchResult:
            return b.fetch(ctx, unit_id, offset, length)

        return self._run(session_id, "fetch", budget_seconds, go, unit_id)


__all__ = ["TOOL_PERMISSIONS", "Gateway", "SessionState"]
