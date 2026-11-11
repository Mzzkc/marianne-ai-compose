"""The versioned memory error taxonomy, ``errors/1``.

One list, owned here; every memory part cites it rather than restating it.
A backend raises a typed :class:`MemoryBackendError` subclass whose ``code``
is the wire code the gateway returns to a musician. ``subtype`` carries the
backend-native reason (``custody_violation``, ``claim_mismatch``...) and
``remedy`` names the action that clears it.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar, TypedDict

ERRORS_VERSION = "errors/1"


class ErrorCode(str, Enum):
    """The eleven ``errors/1`` codes. Member name is the wire code."""

    E1 = "unauthenticated"
    E2 = "permission_denied"
    E3 = "scope_refused"
    E4 = "recall_cap_exceeded"
    E5 = "backend_unreachable"
    E6 = "dead_backend"
    E7 = "schema_violation"
    E8 = "authority_revoked"
    E9 = "lock_timeout"
    E10 = "deadline_exceeded"
    E11 = "not_found"


class WireError(TypedDict):
    """The error object a gateway returns for a failed memory call."""

    code: str
    name: str
    subtype: str
    detail: str
    remedy: str


class MemoryBackendError(Exception):
    """Base of every typed memory failure.

    Deliberately not derived from the builtin ``MemoryError`` (which means the
    interpreter ran out of RAM). Construct a subclass, never this base.
    """

    code: ClassVar[ErrorCode]

    def __init__(self, detail: str, *, subtype: str = "", remedy: str = "") -> None:
        self.detail = detail
        self.subtype = subtype or self.code.value
        self.remedy = remedy
        text = f"{self.code.name} {self.code.value}"
        if self.subtype != self.code.value:
            text += f" ({self.subtype})"
        text += f": {detail}"
        if remedy:
            text += f" — remedy: {remedy}"
        super().__init__(text)

    def to_wire(self) -> WireError:
        """Serialise for the gateway's tool-call error response."""
        return {
            "code": self.code.name,
            "name": self.code.value,
            "subtype": self.subtype,
            "detail": self.detail,
            "remedy": self.remedy,
        }


class Unauthenticated(MemoryBackendError):
    """E1: peer credentials missing or wrong for this endpoint."""

    code = ErrorCode.E1


class PermissionDenied(MemoryBackendError):
    """E2: the session lacks the permission the operation needs."""

    code = ErrorCode.E2


class ScopeRefused(MemoryBackendError):
    """E3: scope expression malformed or outside the authorised scope."""

    code = ErrorCode.E3


class RecallCapExceeded(MemoryBackendError):
    """E4: the dispatch's cumulative recall quota is spent."""

    code = ErrorCode.E4


class BackendUnreachable(MemoryBackendError):
    """E5: the backend cannot be reached or its storage cannot be opened."""

    code = ErrorCode.E5


class DeadBackend(MemoryBackendError):
    """E6: reserved — the backend died mid-session."""

    code = ErrorCode.E6


class SchemaViolation(MemoryBackendError):
    """E7: an argument or configuration value fails the contract."""

    code = ErrorCode.E7


class AuthorityRevoked(MemoryBackendError):
    """E8: the session's authority epoch is stale (grant revoked)."""

    code = ErrorCode.E8


class LockTimeout(MemoryBackendError):
    """E9: lock acquisition did not succeed within the call's budget."""

    code = ErrorCode.E9


class DeadlineExceeded(MemoryBackendError):
    """E10: the call deadline passed before mutation began."""

    code = ErrorCode.E10


class NotFound(MemoryBackendError):
    """E11: the fact, unit or attachment does not exist in the bound store."""

    code = ErrorCode.E11


ERROR_CLASSES: dict[ErrorCode, type[MemoryBackendError]] = {
    cls.code: cls
    for cls in (
        Unauthenticated,
        PermissionDenied,
        ScopeRefused,
        RecallCapExceeded,
        BackendUnreachable,
        DeadBackend,
        SchemaViolation,
        AuthorityRevoked,
        LockTimeout,
        DeadlineExceeded,
        NotFound,
    )
}
