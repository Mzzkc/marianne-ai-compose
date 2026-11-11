"""The score-facing ``access:`` grammar and the named ``GrantRecord`` type.

``access := "inherit" | "grant:" name ["@" version]`` — permissions never
appear in score YAML; a grant reference names a conductor-side record. This
module only parses the grammar and defines the record shape. There is no
grant store yet (it is administered by ``mzt memory access``, not built in
this wave), so nothing here resolves a reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from marianne.memory.errors import SchemaViolation

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
