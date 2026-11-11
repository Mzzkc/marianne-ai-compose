"""The ``memory-backend/1.1`` protocol and its per-call ``RequestContext``.

A backend is anything that implements these nine operations. The gateway
(conductor side) builds a :class:`RequestContext` for every call from the
session it admitted, never from data the musician sent, and a backend trusts
the context: principal, canonical scope, permission ceiling and deadline.

Mapping from GH #496's first sketch: ``remember`` is ``declare``, ``export``
is ``stream_facts``. ``consolidate`` is not part of the frozen protocol; a
backend that cannot consolidate says so through its capability table.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from marianne.memory.errors import PermissionDenied, SchemaViolation
from marianne.memory.model import (
    PERMISSION_VALUES,
    BindResult,
    DeclareResult,
    FetchResult,
    ForgetResult,
    MemoryHealth,
    MigrationBatch,
    MigrationEnvelope,
    MigrationResult,
    Permission,
    Provenance,
    RecallResult,
    VerifyResult,
    parse_scope,
    validate_principal,
)

PROTOCOL_VERSION = "memory-backend/1.1"

OPERATIONS: tuple[str, ...] = (
    "declare",
    "recall",
    "verify",
    "bind",
    "forget",
    "fetch",
    "stream_facts",
    "migration_ingest",
    "health",
)


@dataclass(frozen=True)
class RequestContext:
    """The immutable per-call envelope (``memory-backend/1.1``).

    ``deadline_at`` is a POSIX timestamp. A conforming backend clamps every
    lock wait to ``remaining_seconds`` and aborts with E10 before it mutates
    anything once the deadline has passed. ``scope`` is canonicalised on
    construction; ``permissions`` is the session's ceiling.
    """

    principal: str
    scope: str
    permissions: tuple[str, ...]
    deadline_at: float
    dispatch_id: str | None = None
    job_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        validate_principal(self.principal)
        object.__setattr__(self, "scope", parse_scope(self.scope).canonical)
        unknown = sorted(set(self.permissions) - PERMISSION_VALUES)
        if unknown:
            raise SchemaViolation(
                f"unknown permission(s) {unknown}; legal: {sorted(PERMISSION_VALUES)}",
                subtype="permission_invalid",
            )
        object.__setattr__(self, "permissions", tuple(self.permissions))
        if not isinstance(self.deadline_at, (int, float)) or not math.isfinite(self.deadline_at):
            raise SchemaViolation(
                f"deadline_at must be a finite POSIX timestamp, got {self.deadline_at!r}",
                subtype="deadline_invalid",
            )

    @property
    def remaining_seconds(self) -> float:
        """Seconds left before ``deadline_at``; never negative."""
        return max(0.0, self.deadline_at - time.time())

    def has(self, permission: Permission) -> bool:
        return permission.value in self.permissions

    def require(self, permission: Permission) -> None:
        """Raise E2 naming ``permission`` when the ceiling lacks it."""
        if not self.has(permission):
            raise PermissionDenied(
                f"operation needs `{permission.value}`; session holds "
                f"{list(self.permissions) or 'no permissions'}",
                subtype="permission_denied",
            )

    def require_any(self, *permissions: Permission) -> None:
        """Raise E2 unless the ceiling holds at least one of ``permissions``."""
        if not any(self.has(p) for p in permissions):
            names = " or ".join(f"`{p.value}`" for p in permissions)
            raise PermissionDenied(
                f"operation needs {names}; session holds "
                f"{list(self.permissions) or 'no permissions'}",
                subtype="permission_denied",
            )


@runtime_checkable
class MemoryBackend(Protocol):
    """``memory-backend/1.1``: what every memory backend implements.

    Every operation takes the gateway-built :class:`RequestContext` first.
    Failures raise a typed :class:`~marianne.memory.errors.MemoryBackendError`
    subclass; a backend never returns an error as a success-shaped dict.
    """

    def declare(
        self,
        ctx: RequestContext,
        fact: str,
        scope: str,
        provenance: Provenance | None = None,
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> DeclareResult:
        """Remember ``fact`` with its evidence; idempotent per (claim, evidence)."""
        ...

    def recall(
        self,
        ctx: RequestContext,
        query: str,
        scope: str,
        limit: int = 100,
        cursor: str | None = None,
        cap_bytes: int | None = None,
    ) -> RecallResult:
        """Facts relevant to ``query``, within the per-response byte cap.

        ``cap_bytes`` is an optional per-call ceiling the gateway sets to
        ``min(recall_cap_bytes, quota_remaining)``; the backend applies the
        smaller of it and its own cap, stops reading at it and returns its
        cursor. ``None`` keeps the backend's own cap (the 1.1 behaviour).
        """
        ...

    def verify(
        self, ctx: RequestContext, fact_id_or_hash: str, expected_evidence: str = "",
    ) -> VerifyResult:
        """Re-check a fact against its evidence; read-only."""
        ...

    def bind(
        self,
        ctx: RequestContext,
        fact_id: str,
        target_symbol: str,
        evidence_receipt: str | None = None,
    ) -> BindResult:
        """Bind a freshly verified fact to an action; re-checks disk first."""
        ...

    def forget(self, ctx: RequestContext, fact_id: str, reason: str = "") -> ForgetResult:
        """Tombstone a fact (archive, never silent delete)."""
        ...

    def fetch(self, ctx: RequestContext, unit_id: str, offset: int, length: int) -> FetchResult:
        """A bounded byte range of an oversized unit."""
        ...

    def stream_facts(
        self, ctx: RequestContext, scope: str, since_revision: int | None = None,
    ) -> Iterator[MigrationBatch]:
        """Export every live fact and tombstone as ``migration-facts/1`` batches."""
        ...

    def migration_ingest(
        self, ctx: RequestContext, migration_id: str, envelope: MigrationEnvelope,
    ) -> MigrationResult:
        """Staged import: ``begin`` -> ``batch``... -> ``finalize`` | ``abort``."""
        ...

    def health(self) -> MemoryHealth:
        """A ``memory-health/1`` record for this backend's binding."""
        ...
