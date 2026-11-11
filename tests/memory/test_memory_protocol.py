"""The memory backend contract (`memory-backend/1.1`, GH #496 wave A).

Pure value types and typed errors only: no I/O, no daemon. These tests pin
the shapes the files backend (and later the gateway) must agree on.
"""

from __future__ import annotations

import ast
import dataclasses
import math
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from marianne.memory import (
    ALL_PERMISSIONS,
    PROTOCOL_VERSION,
    AccessRef,
    BackendUnreachable,
    DeadlineExceeded,
    ErrorCode,
    GrantRecord,
    LockTimeout,
    MemoryBackend,
    MemoryBackendError,
    NotFound,
    Permission,
    PermissionDenied,
    RequestContext,
    SchemaViolation,
    ScopeKey,
    ScopeRefused,
    content_hash,
    derive_fact_id,
    memory_health,
    oversized_unit_cursor,
    page_cursor,
    parse_access,
    parse_scope,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "marianne" / "memory"


# ---------------------------------------------------------------- errors/1


def test_error_taxonomy_is_exactly_errors_v1() -> None:
    assert {c.name: c.value for c in ErrorCode} == {
        "E1": "unauthenticated",
        "E2": "permission_denied",
        "E3": "scope_refused",
        "E4": "recall_cap_exceeded",
        "E5": "backend_unreachable",
        "E6": "dead_backend",
        "E7": "schema_violation",
        "E8": "authority_revoked",
        "E9": "lock_timeout",
        "E10": "deadline_exceeded",
        "E11": "not_found",
    }


@pytest.mark.parametrize(
    ("cls", "code"),
    [
        (PermissionDenied, ErrorCode.E2),
        (ScopeRefused, ErrorCode.E3),
        (BackendUnreachable, ErrorCode.E5),
        (SchemaViolation, ErrorCode.E7),
        (LockTimeout, ErrorCode.E9),
        (DeadlineExceeded, ErrorCode.E10),
        (NotFound, ErrorCode.E11),
    ],
)
def test_each_typed_error_carries_its_code(cls: type[MemoryBackendError], code: ErrorCode) -> None:
    err = cls("detail text", subtype="sub", remedy="do the thing")
    assert isinstance(err, MemoryBackendError)
    assert err.code is code
    assert err.to_wire() == {
        "code": code.name,
        "name": code.value,
        "subtype": "sub",
        "detail": "detail text",
        "remedy": "do the thing",
    }
    assert code.value in str(err)


def test_every_error_code_has_exactly_one_typed_exception() -> None:
    from marianne.memory.errors import ERROR_CLASSES

    assert set(ERROR_CLASSES) == set(ErrorCode)
    for code, cls in ERROR_CLASSES.items():
        assert cls.code is code


def test_error_base_does_not_shadow_builtin_memory_error() -> None:
    assert not issubclass(MemoryBackendError, MemoryError)


# ---------------------------------------------------------------- RequestContext


def _ctx(**over: Any) -> RequestContext:
    base: dict[str, Any] = {
        "principal": "person:alice",
        "scope": "local:default",
        "permissions": ("read",),
        "deadline_at": time.time() + 5.0,
    }
    base.update(over)
    return RequestContext(**base)


def test_request_context_is_frozen_v1_1_shape() -> None:
    names = [f.name for f in dataclasses.fields(RequestContext)]
    assert names == [
        "principal", "scope", "permissions", "deadline_at",
        "dispatch_id", "job_id", "session_id",
    ]
    ctx = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.principal = "person:mallory"  # type: ignore[misc]
    assert PROTOCOL_VERSION == "memory-backend/1.1"


def test_remaining_seconds_clamps_to_zero() -> None:
    assert _ctx(deadline_at=time.time() - 10).remaining_seconds == 0.0
    assert 0.0 < _ctx(deadline_at=time.time() + 3).remaining_seconds <= 3.0


def test_require_names_the_missing_permission() -> None:
    ctx = _ctx(permissions=("read",))
    ctx.require(Permission.READ)
    with pytest.raises(PermissionDenied, match="write"):
        ctx.require(Permission.WRITE)
    # any-of form: verify may be satisfied by read (files verify rule)
    ctx.require_any(Permission.VERIFY, Permission.READ)


@pytest.mark.parametrize(
    "bad",
    [
        {"permissions": ("read", "sudo")},
        {"deadline_at": math.nan},
        {"deadline_at": math.inf},
        {"principal": ""},
        {"principal": "root"},
        {"principal": "person:"},
    ],
)
def test_request_context_rejects_malformed_fields(bad: dict[str, Any]) -> None:
    with pytest.raises(SchemaViolation):
        _ctx(**bad)


def test_request_context_refuses_bad_scope_with_e3() -> None:
    with pytest.raises(ScopeRefused):
        _ctx(scope="nonsense")


def test_request_context_canonicalises_scope() -> None:
    assert _ctx(scope=" Organization/Acme ").scope == "org/acme"


def test_seven_permissions() -> None:
    assert {p.value for p in ALL_PERMISSIONS} == {
        "read", "write", "verify", "bind", "forget", "admin", "export",
    }


# ---------------------------------------------------------------- scope keys


@pytest.mark.parametrize(
    ("raw", "canonical", "family", "ident"),
    [
        ("local:default", "local:default", "local", "default"),
        ("person/legion", "person/legion", "person", "legion"),
        ("project/alpha.1", "project/alpha.1", "project", "alpha.1"),
        ("organization/acme", "org/acme", "org", "acme"),
        ("  Concert/Rewrite-1 ", "concert/rewrite-1", "concert", "rewrite-1"),
        ("workspace/shared", "workspace/shared", "workspace", "shared"),
        # one identity per (family, id): the separator is canonicalised
        ("local/default", "local:default", "local", "default"),
        ("person:legion", "person/legion", "person", "legion"),
    ],
)
def test_scope_grammar_accepts_and_canonicalises(
    raw: str, canonical: str, family: str, ident: str,
) -> None:
    key = parse_scope(raw)
    assert key == ScopeKey(family=family, ident=ident)
    assert key.canonical == canonical
    assert str(key) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "person",
        "person/",
        "bogus/x",
        "person/a/b",
        "person/..",
        "project/.",
        "person/a b",
        "../etc",
        "person/ALICE\x00",
    ],
)
def test_scope_grammar_refuses_with_e3(raw: str) -> None:
    with pytest.raises(ScopeRefused) as info:
        parse_scope(raw)
    assert info.value.code is ErrorCode.E3


# ---------------------------------------------------------------- access grammar


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("inherit", AccessRef(kind="inherit")),
        ("grant:audit", AccessRef(kind="grant", name="audit")),
        ("grant:audit@2", AccessRef(kind="grant", name="audit", version=2)),
        ("grant:team_research-1@10", AccessRef(kind="grant", name="team_research-1", version=10)),
    ],
)
def test_access_grammar_legal_forms(raw: str, expected: AccessRef) -> None:
    assert parse_access(raw) == expected
    assert str(parse_access(raw)) == raw


@pytest.mark.parametrize(
    "raw",
    ["read, write", "grant:Audit_Team", "grant:audit@v2", "grant:audit@0", "grant:", "Inherit"],
)
def test_access_grammar_illegal_forms(raw: str) -> None:
    with pytest.raises(SchemaViolation):
        parse_access(raw)


def test_grant_record_shape_is_frozen() -> None:
    rec = GrantRecord(
        name="audit", version=1, principal="person:alice", scope="project/x",
        permissions=("read",), revocation_state="active", created_at=1.0,
    )
    assert rec.is_active()
    assert [f.name for f in dataclasses.fields(GrantRecord)] == [
        "name", "version", "principal", "scope", "permissions",
        "revocation_state", "created_at", "revoked_at", "parent_version",
    ]


# ---------------------------------------------------------------- facts


def test_fact_identity_is_deterministic_and_separated() -> None:
    h = content_hash("the sky is blue")
    assert len(h) == 64
    a = derive_fact_id("local:default", "file:a.md", h, h)
    assert a == derive_fact_id("local:default", "file:a.md", h, h)
    assert len(a) == 16
    # field boundaries matter: no concatenation collisions
    assert derive_fact_id("a", "bc", h, h) != derive_fact_id("ab", "c", h, h)


# ---------------------------------------------------------------- cursors & health


def test_recall_cursor_v1_shapes() -> None:
    assert page_cursor("tok", "sec-3", 7) == {
        "kind": "page", "cursor": "tok", "section_id": "sec-3", "omitted": 7,
    }
    assert oversized_unit_cursor("u1", 100_000, 32_768) == {
        "kind": "oversized_unit", "unit_id": "u1", "unit_bytes": 100_000,
        "cap_bytes": 32_768, "remedy": "fetch",
    }


def test_memory_health_v1() -> None:
    rec = memory_health("healthy", "ok", checked_at=12.0)
    assert rec == {
        "schema": "memory-health/1", "status": "healthy", "detail": "ok",
        "checked_at": 12.0, "failure_class": None,
    }
    bad = memory_health("unreachable", "gone", checked_at=1.0)
    assert bad["failure_class"] == "unreachable"
    with pytest.raises(SchemaViolation):
        memory_health("fine", "nope")  # type: ignore[arg-type]


# ---------------------------------------------------------------- Protocol


class _Conforming:
    def declare(self, ctx: RequestContext, fact: str, scope: str,
                provenance: Any = None, confidence: float = 1.0,
                tags: Any = None) -> Any: ...
    def recall(self, ctx: RequestContext, query: str, scope: str,
               limit: int = 100, cursor: str | None = None) -> Any: ...
    def verify(self, ctx: RequestContext, fact_id_or_hash: str,
               expected_evidence: str = "") -> Any: ...
    def bind(self, ctx: RequestContext, fact_id: str, target_symbol: str,
             evidence_receipt: str | None = None) -> Any: ...
    def forget(self, ctx: RequestContext, fact_id: str, reason: str = "") -> Any: ...
    def fetch(self, ctx: RequestContext, unit_id: str, offset: int, length: int) -> Any: ...
    def stream_facts(self, ctx: RequestContext, scope: str,
                     since_revision: int | None = None) -> Iterator[Any]:
        return iter(())
    def migration_ingest(self, ctx: RequestContext, migration_id: str, envelope: Any) -> Any: ...
    def health(self) -> Any: ...


class _MissingExport:
    def declare(self) -> None: ...
    def recall(self) -> None: ...


def test_protocol_is_runtime_checkable() -> None:
    assert isinstance(_Conforming(), MemoryBackend)
    assert not isinstance(_MissingExport(), MemoryBackend)


def test_protocol_operations_are_the_frozen_nine() -> None:
    from marianne.memory.backend_protocol import OPERATIONS

    assert OPERATIONS == (
        "declare", "recall", "verify", "bind", "forget", "fetch",
        "stream_facts", "migration_ingest", "health",
    )


# ---------------------------------------------------------------- purity fence


_FORBIDDEN_IMPORTS = {
    "os", "subprocess", "socket", "fcntl", "shutil", "asyncio",
    "marianne.daemon", "marianne.execution", "marianne.cli", "marianne.learning",
}


@pytest.mark.parametrize(
    "module", ["__init__.py", "backend_protocol.py", "errors.py", "model.py", "access.py"],
)
def test_contract_modules_are_pure(module: str) -> None:
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    hits = {
        name for name in imported
        if any(name == bad or name.startswith(bad + ".") for bad in _FORBIDDEN_IMPORTS)
    }
    assert not hits, f"{module} imports I/O or runtime packages: {sorted(hits)}"
