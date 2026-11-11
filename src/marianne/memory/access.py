"""The score-facing ``access:`` grammar and the named ``GrantRecord`` type.

``access := "inherit" | "grant:" name ["@" version]`` — permissions never
appear in score YAML; a grant reference names a conductor-side record. This
module only parses the grammar and defines the record shape. There is no
grant store yet (it is administered by ``mzt memory access``, not built in
this wave), so nothing here resolves a reference.

:class:`GrantStore` (gateway wave) is the on-disk store: one JSON file per
grant name holding every version, written atomically. It resolves a
(principal, scope, permission) question to a :class:`GrantRecord` or raises
E2 / E3 / E8.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from marianne.memory.errors import (
    AuthorityRevoked,
    NotFound,
    PermissionDenied,
    SchemaViolation,
    ScopeRefused,
)
from marianne.memory.model import PERMISSION_VALUES, parse_scope, validate_principal

_GRANT_NAME = r"[a-z0-9_-]+"
_ACCESS_RE = re.compile(rf"^grant:({_GRANT_NAME})(?:@([1-9][0-9]*))?$")


@dataclass(frozen=True)
class AccessRef:
    """A parsed ``access:`` value: ``inherit`` or a (possibly pinned) grant."""

    kind: Literal["inherit", "grant"]
    name: str | None = None
    version: int | None = None

    def __str__(self) -> str:
        if self.kind == "inherit":
            return "inherit"
        pin = f"@{self.version}" if self.version is not None else ""
        return f"grant:{self.name}{pin}"


INHERIT = AccessRef(kind="inherit")


def parse_access(raw: str) -> AccessRef:
    """Parse an ``access:`` expression or raise E7 ``schema_violation``."""
    if raw == "inherit":
        return INHERIT
    match = _ACCESS_RE.match(raw) if isinstance(raw, str) else None
    if match is None:
        raise SchemaViolation(
            f"access {raw!r} is neither `inherit` nor `grant:<name>[@<version>]` "
            "(name: a-z 0-9 _ -; version: a positive integer)",
            subtype="access_invalid",
            remedy="write `access: inherit` or `access: grant:<name>` / `grant:<name>@<n>`",
        )
    version = int(match.group(2)) if match.group(2) else None
    return AccessRef(kind="grant", name=match.group(1), version=version)


@dataclass(frozen=True)
class GrantRecord:
    """One version of a named grant (``grant-record/1``).

    Updating a grant writes version ``n+1`` and marks ``n`` superseded with
    ``parent_version`` linking the lineage. A pinned reference to a
    superseded version keeps that version's permissions; a revoked version
    never confers authority.
    """

    name: str
    version: int
    principal: str
    scope: str
    permissions: tuple[str, ...]
    revocation_state: Literal["active", "revoked", "superseded"]
    created_at: float
    revoked_at: float | None = None
    parent_version: int | None = None

    def is_active(self) -> bool:
        return self.revocation_state == "active"


# ---------------------------------------------------------------- scope matching

_SEGMENT_RE = re.compile(r"^[a-z0-9_.-]+$")


def _split_scope(raw: str) -> tuple[str, tuple[str, ...]]:
    """Canonical base scope plus any hierarchical path segments (BE3 grammar).

    ``project/a/sub`` is base ``project/a`` with path ``("sub",)``.
    """
    text = raw.strip().lower() if isinstance(raw, str) else ""
    head, *rest = text.split("/")
    # ``local:default/x`` -> head ``local:default``; ``project/a/x`` -> head ``project``.
    if ":" not in head:
        if not rest:
            return parse_scope(text).canonical, ()
        head, rest = f"{head}/{rest[0]}", rest[1:]
    base = parse_scope(head).canonical
    for seg in rest:
        if not _SEGMENT_RE.match(seg) or set(seg) == {"."}:
            raise ScopeRefused(
                f"scope {raw!r} has an invalid path segment {seg!r}",
                subtype="scope_invalid",
            )
    return base, tuple(rest)


def scope_covers(grant_scope: str, target_scope: str) -> bool:
    """True when ``target_scope`` equals ``grant_scope`` or descends from it."""
    g_base, g_path = _split_scope(grant_scope)
    t_base, t_path = _split_scope(target_scope)
    return g_base == t_base and t_path[: len(g_path)] == g_path


# ---------------------------------------------------------------- store

_NAME_RE = re.compile(rf"^{_GRANT_NAME}$")


def _atomic_write_text(path: Path, text: str) -> None:
    """Same-directory temp file, fsync, ``os.replace``, fsync the directory."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:10]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _check_permissions(permissions: Iterable[str]) -> tuple[str, ...]:
    perms = tuple(permissions)
    unknown = sorted(set(perms) - PERMISSION_VALUES)
    if unknown or not perms:
        raise SchemaViolation(
            f"grant permissions must be a non-empty subset of {sorted(PERMISSION_VALUES)}; "
            f"got {list(perms)}",
            subtype="permission_invalid",
        )
    return tuple(dict.fromkeys(perms))


class GrantStore:
    """Versioned grants under ``root``, one ``<name>.json`` per lineage.

    Revocation is per lineage: it marks every version revoked. A pinned
    reference to a superseded version keeps that version's permissions.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._lock = threading.RLock()

    def _path(self, name: str) -> Path:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise SchemaViolation(
                f"grant name {name!r} must match {_GRANT_NAME}", subtype="grant_name_invalid"
            )
        return self._root / f"{name}.json"

    def _load(self, name: str) -> list[GrantRecord]:
        path = self._path(name)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        try:
            rows = json.loads(raw)
            return [GrantRecord(**{**r, "permissions": tuple(r["permissions"])}) for r in rows]
        except (ValueError, TypeError, KeyError) as exc:
            raise SchemaViolation(
                f"grant file {path.name} is corrupt: {exc}", subtype="grant_store_corrupt",
                remedy="restore the grant file from backup; it is never auto-repaired",
            ) from None

    def _save(self, name: str, records: list[GrantRecord]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self._path(name), json.dumps([asdict(r) for r in records], sort_keys=True, indent=1)
        )

    def names(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.json"))

    def versions(self, name: str) -> list[GrantRecord]:
        """Every version of ``name``, oldest first (empty when unknown)."""
        with self._lock:
            return self._load(name)

    def create(
        self, name: str, principal: str, scope: str, permissions: Iterable[str],
    ) -> GrantRecord:
        validate_principal(principal)
        canonical = "/".join(filter(None, [_split_scope(scope)[0], *_split_scope(scope)[1]]))
        perms = _check_permissions(permissions)
        with self._lock:
            if self._load(name):
                raise SchemaViolation(
                    f"grant {name!r} already exists", subtype="grant_exists",
                    remedy="use update to write a new version",
                )
            rec = GrantRecord(
                name=name, version=1, principal=principal, scope=canonical,
                permissions=perms, revocation_state="active", created_at=time.time(),
            )
            self._save(name, [rec])
            return rec

    def update(
        self, name: str, *, permissions: Iterable[str] | None = None,
        scope: str | None = None,
    ) -> GrantRecord:
        """Write version n+1 and mark n superseded (parent link kept)."""
        with self._lock:
            history = self._load(name)
            if not history:
                raise NotFound(f"no grant {name!r}", subtype="grant_not_found")
            head = history[-1]
            if not head.is_active():
                raise AuthorityRevoked(
                    f"grant {name!r} is {head.revocation_state}", subtype="grant_revoked"
                )
            new_scope = head.scope
            if scope is not None:
                base, path = _split_scope(scope)
                new_scope = "/".join([base, *path])
            new = GrantRecord(
                name=name, version=head.version + 1, principal=head.principal,
                scope=new_scope,
                permissions=_check_permissions(
                    head.permissions if permissions is None else permissions
                ),
                revocation_state="active", created_at=time.time(),
                parent_version=head.version,
            )
            history[-1] = _replace_state(head, "superseded")
            self._save(name, [*history, new])
            return new

    def revoke(self, name: str) -> None:
        """Revoke the whole lineage; no version confers authority afterwards."""
        with self._lock:
            history = self._load(name)
            if not history:
                raise NotFound(f"no grant {name!r}", subtype="grant_not_found")
            now = time.time()
            self._save(name, [_replace_state(r, "revoked", now) for r in history])

    def resolve_ref(self, ref: AccessRef) -> GrantRecord:
        """Float ``grant:<n>`` -> highest active; pin ``@v`` -> that version."""
        if ref.kind != "grant" or ref.name is None:
            raise SchemaViolation("`inherit` names no grant", subtype="access_invalid")
        with self._lock:
            history = self._load(ref.name)
        if not history:
            raise NotFound(f"no grant {ref.name!r}", subtype="grant_not_found")
        if any(r.revocation_state == "revoked" for r in history):
            raise AuthorityRevoked(f"grant {ref.name!r} is revoked", subtype="grant_revoked")
        if ref.version is not None:
            for rec in history:
                if rec.version == ref.version:
                    return rec
            raise NotFound(
                f"grant {ref.name!r} has no version {ref.version}",
                subtype="grant_version_not_found",
            )
        return history[-1]

    def resolve(self, principal: str, scope: str, permission: str) -> GrantRecord:
        """Answer one (principal, scope, permission) question or refuse.

        Only active grants held by ``principal`` that cover ``scope`` count.
        E8 when the only candidates are revoked; E3 when no grant of the
        principal covers the scope; E2 when one covers it without ``permission``.
        """
        if permission not in PERMISSION_VALUES:
            raise SchemaViolation(
                f"unknown permission {permission!r}", subtype="permission_invalid"
            )
        _split_scope(scope)
        covering: list[GrantRecord] = []
        revoked_hit = False
        with self._lock:
            for name in self.names():
                history = self._load(name)
                head = history[-1]
                if head.principal != principal or not scope_covers(head.scope, scope):
                    continue
                if head.revocation_state == "revoked":
                    revoked_hit = True
                elif head.is_active():
                    covering.append(head)
        for rec in covering:
            if permission in rec.permissions:
                return rec
        if covering:
            raise PermissionDenied(
                f"{principal} holds no `{permission}` on {scope}", subtype="permission_denied",
            )
        if revoked_hit:
            raise AuthorityRevoked(
                f"every grant of {principal} covering {scope} is revoked", subtype="grant_revoked"
            )
        raise ScopeRefused(f"{principal} has no grant covering {scope}", subtype="scope_refused")


def _replace_state(
    rec: GrantRecord, state: Literal["superseded", "revoked"], revoked_at: float | None = None,
) -> GrantRecord:
    data: dict[str, Any] = asdict(rec)
    data["revocation_state"] = state
    if revoked_at is not None:
        data["revoked_at"] = revoked_at
    data["permissions"] = tuple(data["permissions"])
    return GrantRecord(**data)
