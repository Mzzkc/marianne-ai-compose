"""Slice 1 (gateway wave): the on-disk grant store and its resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from marianne.memory import (
    AuthorityRevoked,
    NotFound,
    PermissionDenied,
    SchemaViolation,
    ScopeRefused,
    parse_access,
)
from marianne.memory.access import GrantStore, scope_covers

P = "agent:musician-1"


@pytest.fixture
def store(tmp_path: Path) -> GrantStore:
    return GrantStore(tmp_path / "access")


@pytest.mark.parametrize(
    ("grant", "target", "ok"),
    [
        ("project/a", "project/a", True),
        ("project/a", "project/a/sub", True),
        ("project/a", "project/a/sub/deeper", True),
        ("project/a", "project/ab", False),
        ("project/a", "project/b", False),
        ("project/a", "score/a", False),
        ("project/a/sub", "project/a", False),
        ("local:default", "local:default", True),
        ("organization/acme", "org/acme/x", True),
    ],
)
def test_scope_covers_exact_or_descendant(grant: str, target: str, ok: bool) -> None:
    assert scope_covers(grant, target) is ok


def test_scope_covers_refuses_garbage(store: GrantStore) -> None:
    with pytest.raises(ScopeRefused):
        scope_covers("project/a", "nonsense")


def test_create_resolve_allows(store: GrantStore) -> None:
    store.create("g1", P, "project/a", ("read", "write"))
    rec = store.resolve(P, "project/a/x", "write")
    assert rec.name == "g1" and rec.version == 1 and rec.is_active()


def test_resolve_refusal_types(store: GrantStore) -> None:
    store.create("g1", P, "project/a", ("read",))
    with pytest.raises(PermissionDenied):
        store.resolve(P, "project/a", "write")
    with pytest.raises(ScopeRefused):
        store.resolve(P, "project/b", "read")
    with pytest.raises(ScopeRefused):
        store.resolve("agent:other", "project/a", "read")


def test_update_supersedes_and_links_lineage(store: GrantStore) -> None:
    store.create("g1", P, "project/a", ("read",))
    v2 = store.update("g1", permissions=("read", "write"))
    assert v2.version == 2 and v2.parent_version == 1
    versions = store.versions("g1")
    assert [v.revocation_state for v in versions] == ["superseded", "active"]
    assert store.resolve(P, "project/a", "write").version == 2


def test_float_ref_takes_highest_active_pin_keeps_superseded(store: GrantStore) -> None:
    store.create("g1", P, "project/a", ("read",))
    store.update("g1", permissions=("read", "write"))
    assert store.resolve_ref(parse_access("grant:g1")).version == 2
    pinned = store.resolve_ref(parse_access("grant:g1@1"))
    assert pinned.version == 1 and pinned.permissions == ("read",)


def test_pin_missing_version_and_unknown_name(store: GrantStore) -> None:
    store.create("g1", P, "project/a", ("read",))
    with pytest.raises(NotFound) as exc:
        store.resolve_ref(parse_access("grant:g1@9"))
    assert exc.value.subtype == "grant_version_not_found"
    with pytest.raises(NotFound) as exc2:
        store.resolve_ref(parse_access("grant:nope"))
    assert exc2.value.subtype == "grant_not_found"
    with pytest.raises(SchemaViolation):
        store.resolve_ref(parse_access("inherit"))


def test_revoked_lineage_confers_nothing(store: GrantStore) -> None:
    store.create("g1", P, "project/a", ("read",))
    store.update("g1", permissions=("read", "write"))
    store.revoke("g1")
    assert all(v.revocation_state == "revoked" for v in store.versions("g1"))
    assert store.versions("g1")[-1].revoked_at is not None
    for ref in ("grant:g1", "grant:g1@1", "grant:g1@2"):
        with pytest.raises(AuthorityRevoked) as exc:
            store.resolve_ref(parse_access(ref))
        assert exc.value.subtype == "grant_revoked"
    with pytest.raises(AuthorityRevoked):
        store.resolve(P, "project/a", "read")


def test_validation(store: GrantStore) -> None:
    for bad in (
        {"name": "Bad Name", "principal": P, "scope": "project/a", "permissions": ("read",)},
        {"name": "g", "principal": "nobody", "scope": "project/a", "permissions": ("read",)},
        {"name": "g", "principal": P, "scope": "project/a", "permissions": ("fly",)},
        {"name": "g", "principal": P, "scope": "project/a", "permissions": ()},
    ):
        with pytest.raises((SchemaViolation, ScopeRefused)):
            store.create(**bad)  # type: ignore[arg-type]
    with pytest.raises(ScopeRefused):
        store.create("g", P, "junk", ("read",))
    store.create("g", P, "project/a", ("read",))
    with pytest.raises(SchemaViolation):
        store.create("g", P, "project/a", ("read",))


def test_persists_across_instances_and_leaves_no_temp(store: GrantStore, tmp_path: Path) -> None:
    store.create("g1", P, "project/a", ("read",))
    again = GrantStore(tmp_path / "access")
    assert again.resolve(P, "project/a", "read").name == "g1"
    assert not [p for p in (tmp_path / "access").iterdir() if p.name.endswith(".tmp")]


def test_write_is_atomic_via_replace(
    store: GrantStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create("g1", P, "project/a", ("read",))
    calls: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(
        os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1]
    )
    store.update("g1", permissions=("read", "write"))
    assert calls.index("fsync") < calls.index("replace")


def test_failed_write_keeps_old_bytes(
    store: GrantStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store.create("g1", P, "project/a", ("read",))

    def boom(a: object, b: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.update("g1", permissions=("read", "write"))
    monkeypatch.undo()
    assert [v.version for v in store.versions("g1")] == [1]
    assert not [p for p in (tmp_path / "access").iterdir() if p.name.endswith(".tmp")]


def test_corrupt_file_is_refused_not_ignored(store: GrantStore, tmp_path: Path) -> None:
    store.create("g1", P, "project/a", ("read",))
    (tmp_path / "access" / "g1.json").write_text("{not json")
    with pytest.raises(SchemaViolation):
        store.versions("g1")
