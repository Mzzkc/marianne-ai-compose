"""Pure value types for ``memory-backend/1.1``.

Scope keys, permissions, fact identity, evidence-bearing fact records,
tombstones, the ``recall-cursor/1`` and ``memory-health/1`` shapes, and the
``migration-facts/1`` batch. Wire payloads are ``TypedDict``s: at runtime
they are the plain dicts the gateway serialises, statically they are checked.

Nothing here touches the filesystem, a socket or the daemon.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal, NotRequired, TypedDict

from marianne.memory.errors import SchemaViolation, ScopeRefused

# ---------------------------------------------------------------- permissions


class Permission(str, Enum):
    """The seven memory permissions. They live in conductor access records,
    never in score YAML."""

    READ = "read"
    WRITE = "write"
    VERIFY = "verify"
    BIND = "bind"
    FORGET = "forget"
    ADMIN = "admin"
    EXPORT = "export"


ALL_PERMISSIONS: tuple[Permission, ...] = tuple(Permission)
PERMISSION_VALUES: frozenset[str] = frozenset(p.value for p in Permission)

# ---------------------------------------------------------------- scopes

#: Accepted scope families. ``organization`` is accepted on input and
#: canonicalised to ``org``.
SCOPE_FAMILIES: tuple[str, ...] = (
    "person", "agent", "score", "concert", "project", "team", "org", "workspace", "local",
)
_FAMILY_ALIASES = {"organization": "org"}
_SCOPE_RE = re.compile(
    r"^(person|agent|score|concert|project|team|org|organization|workspace|local)"
    r"(:|/)([a-z0-9_.-]+)$"
)


@dataclass(frozen=True, order=True)
class ScopeKey:
    """A canonical scope: one family and one identifier.

    The canonical spelling is ``local:<id>`` for the ``local`` family and
    ``<family>/<id>`` for every other, so one (family, id) pair has exactly
    one string everywhere it is joined on.
    """

    family: str
    ident: str

    @property
    def canonical(self) -> str:
        sep = ":" if self.family == "local" else "/"
        return f"{self.family}{sep}{self.ident}"

    def __str__(self) -> str:
        return self.canonical


DEFAULT_SCOPE = ScopeKey(family="local", ident="default")


def parse_scope(raw: str) -> ScopeKey:
    """Parse and canonicalise a scope expression, or raise E3 ``scope_refused``.

    Grammar: ``<family>(:|/)<id>`` with ``<id>`` in ``[a-z0-9_.-]+``; input is
    stripped and lowercased. An id made only of dots is refused because the
    files backend maps a scope onto a directory and ``..`` would escape it.
    """
    text = raw.strip().lower() if isinstance(raw, str) else ""
    match = _SCOPE_RE.match(text)
    if match is None:
        raise ScopeRefused(
            f"scope {raw!r} does not match <family>/<id> "
            f"(families: {', '.join(SCOPE_FAMILIES)}; id: a-z 0-9 _ . -)",
            subtype="scope_invalid",
            remedy="write a scope like `project/my-project` or `local:default`",
        )
    family = _FAMILY_ALIASES.get(match.group(1), match.group(1))
    ident = match.group(3)
    if set(ident) == {"."}:
        raise ScopeRefused(
            f"scope {raw!r} has a path-like id {ident!r}",
            subtype="scope_invalid",
            remedy="use a name, not `.` or `..`",
        )
    return ScopeKey(family=family, ident=ident)


# ---------------------------------------------------------------- principals

_PRINCIPAL_RE = re.compile(r"^(person|agent|team|org):\S+$")


def validate_principal(principal: str) -> str:
    """Return ``principal`` if it is ``person:|agent:|team:|org:<id>``, else E7."""
    if not isinstance(principal, str) or not _PRINCIPAL_RE.match(principal):
        raise SchemaViolation(
            f"principal {principal!r} is not person:|agent:|team:|org:<id>",
            subtype="principal_invalid",
        )
    return principal


# ---------------------------------------------------------------- fact identity


def content_hash(text: str) -> str:
    """SHA-256 hex digest of the fact text (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derive_fact_id(scope: str, evidence_locator: str, chash: str, claim_digest: str) -> str:
    """Deterministic 16-hex fact id over (scope, locator, content hash, claim digest).

    Fields are NUL-separated so ``("a", "bc")`` and ``("ab", "c")`` differ.
    Re-declaring the same claim with the same evidence yields the same id.
    """
    joined = "\0".join((scope, evidence_locator, chash, claim_digest))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- payloads


class Provenance(TypedDict, total=False):
    """Evidence a musician supplies with a declaration.

    ``evidence_locator`` names where the claim can be re-checked (a path, a
    URL, ``snapshot:<sha256>``); ``observed_at`` is the source observation
    clock, distinct from the backend's ``asserted_at`` write clock.
    """

    evidence_locator: str
    claim_digest: str
    tier: str
    observed_at: float


class ProvenanceReceipt(TypedDict):
    fact_id: str
    claim_digest: str
    evidence_locator: str
    author: str
    written_at: float


class DeclareResult(TypedDict):
    fact_id: str
    hash: str
    written_at: float
    tier: str
    status: Literal["acknowledged"]
    provenance_receipt: ProvenanceReceipt


class PageCursor(TypedDict):
    kind: Literal["page"]
    cursor: str
    section_id: str
    omitted: int


class OversizedUnit(TypedDict):
    kind: Literal["oversized_unit"]
    unit_id: str
    unit_bytes: int
    cap_bytes: int
    remedy: Literal["fetch"]


RecallCursor = PageCursor | OversizedUnit


class RecallItem(TypedDict):
    fact_id: str
    content: str
    score: float
    tier: str
    asserted_at: float
    evidence_locator: str
    status: str


class RecallResult(TypedDict):
    """One recall page.

    ``cursor`` is a page cut (resume with its token) or ``None`` when nothing
    is left. Units too large for the page cap are never truncated: each is
    listed in ``oversized`` with ``remedy: fetch``, so a page cut and several
    oversized markers can travel in one response.
    """

    results: list[RecallItem]
    cursor: PageCursor | None
    oversized: list[OversizedUnit]
    explaining_path: None


class EvidenceReceipt(TypedDict):
    fact_id: str
    claim_digest: str
    evidence_locator: str
    content_hash: str
    evidence_version: str
    verified_at: float
    verifier: str
    expires_at: float
    signature: str


class VerifyResult(TypedDict):
    fact_id: str
    status: Literal["verified", "disk_changed", "evidence_mismatch"]
    evidence_receipt: NotRequired[EvidenceReceipt]
    receipt_token: NotRequired[str]
    correction: NotRequired[dict[str, str]]


class BindResult(TypedDict):
    action_id: str
    fact_id: str
    target_symbol: str
    bound_at: float
    status: Literal["bound"]


class ForgetResult(TypedDict):
    fact_id: str
    tombstone_id: str
    status: Literal["forgotten"]
    erased_at: float


class FetchResult(TypedDict):
    unit_id: str
    bytes: str
    unit_bytes: int
    offset: int
    length: int


class FactRecord(TypedDict):
    """One fact with its evidence and its clocks, as it migrates."""

    fact_id: str
    content: str
    content_hash: str
    claim_digest: str
    evidence_locator: str
    tier: str
    scope: str
    author: str
    asserted_at: float
    revision: int
    incarnation: int


class Tombstone(TypedDict):
    """A generational erasure: revokes the claim at or before ``cutoff``."""

    tombstone_id: str
    fact_id: str
    target: Literal["claim", "contribution"]
    cutoff: int
    target_contribution_ids: list[str]
    claim_digest: str
    evidence_locator: str
    content_hash: str
    erased_at: float
    erased_by: str
    reason: str


class MigrationBatch(TypedDict):
    """One ``migration-facts/1`` record yielded by ``stream_facts``."""

    schema: Literal["migration-facts/1"]
    batch_id: str
    facts: list[FactRecord]
    erasures: list[Tombstone]
    source_revision: int
    is_final: bool


class MigrationEnvelope(TypedDict, total=False):
    phase: Literal["begin", "batch", "finalize", "abort"]
    cut_id: str
    source_revision: int
    batch_id: str
    records: list[FactRecord]
    erasures: list[Tombstone]
    manifest_digest: str
    fact_count: int
    erasure_count: int


class MigrationResult(TypedDict, total=False):
    status: str
    migration_id: str
    batch_id: str
    count: int
    erasure_count: int
    finalized: bool
    manifest_digest: str
    fact_count: int
    receipt: str
    published_revision: int
    aborted: bool


# ---------------------------------------------------------------- recall-cursor/1


def page_cursor(cursor: str, section_id: str, omitted: int) -> PageCursor:
    """A page cut: resume with ``cursor`` to receive the omitted sections."""
    return {"kind": "page", "cursor": cursor, "section_id": section_id, "omitted": omitted}


def oversized_unit_cursor(unit_id: str, unit_bytes: int, cap_bytes: int) -> OversizedUnit:
    """A single unit larger than the page cap: page it with ``fetch``."""
    return {
        "kind": "oversized_unit",
        "unit_id": unit_id,
        "unit_bytes": unit_bytes,
        "cap_bytes": cap_bytes,
        "remedy": "fetch",
    }


# ---------------------------------------------------------------- memory-health/1

HealthStatus = Literal[
    "healthy", "degraded", "unreachable", "unauthenticated", "scope_refused", "unknown",
]
FailureClass = Literal["unreachable", "unauthenticated", "scope_refused", "degraded"]
_HEALTH_STATUSES: frozenset[str] = frozenset(
    {"healthy", "degraded", "unreachable", "unauthenticated", "scope_refused", "unknown"}
)


class MemoryHealth(TypedDict):
    schema: Literal["memory-health/1"]
    status: HealthStatus
    detail: str
    checked_at: float
    failure_class: FailureClass | None


def memory_health(status: HealthStatus, detail: str, *, checked_at: float = 0.0) -> MemoryHealth:
    """Build a ``memory-health/1`` record; ``failure_class`` follows ``status``."""
    if status not in _HEALTH_STATUSES:
        raise SchemaViolation(f"unknown health status {status!r}", subtype="health_status_invalid")
    if not math.isfinite(checked_at):
        raise SchemaViolation("checked_at must be finite", subtype="health_status_invalid")
    failure: FailureClass | None
    if status == "unreachable":
        failure = "unreachable"
    elif status == "unauthenticated":
        failure = "unauthenticated"
    elif status == "scope_refused":
        failure = "scope_refused"
    elif status == "degraded":
        failure = "degraded"
    else:
        failure = None
    return {
        "schema": "memory-health/1",
        "status": status,
        "detail": detail,
        "checked_at": checked_at,
        "failure_class": failure,
    }
