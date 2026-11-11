"""Score-level ``memory:`` key (GH #496, spec 03 Interfaces 1).

A score declares named memory attachments here. The models only fix the
shape; value rules that have their own validator code (system, scope,
access, phases, caps bounds) are checked by V340-V347 so an author gets one
named message instead of a schema dump. The conductor does not read this key
yet, so V327 reports it as reserved when a score sets it.

``core/`` stays free of ``marianne.memory``: scope and access grammars are
checked in :mod:`marianne.validation.checks.memory`, which reuses the
helpers in ``marianne.memory``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Attachment-name grammar (spec 03): lower-case, starts with a letter, <= 32 chars.
BACKEND_NAME_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
_BACKEND_NAME_RE = re.compile(BACKEND_NAME_PATTERN)

#: Deprecated provenance spelling normalised to ``optional`` (V345-NOTE).
PROVENANCE_ALIASES = {"off": "optional"}


def _normalize_provenance(value: Any) -> Any:
    # YAML 1.1 reads an unquoted `off` as the boolean False, so a score that
    # writes `provenance: off` reaches us as False; read it as the alias.
    if value is False:
        return "optional"
    if isinstance(value, str):
        return PROVENANCE_ALIASES.get(value, value)
    return value


class MemoryCaps(BaseModel):
    """Per-attachment caps; each value clamps the score-level default."""

    model_config = ConfigDict(extra="forbid")

    recall_cap_bytes: int = Field(
        default=32768,
        description="Reserved: no runtime reader yet (V327 warns when set). "
        "Per-recall page cap in bytes (1024 to 1048576; V345 checks the range).",
    )
    provenance: str = Field(
        default="required",
        description="Reserved: no runtime reader yet (V327 warns when set). "
        "`required` or `optional`; the deprecated `off` is read as `optional`.",
    )

    @field_validator("provenance", mode="before")
    @classmethod
    def _alias(cls, value: Any) -> Any:
        return _normalize_provenance(value)


class MemoryDefaults(BaseModel):
    """Score-level defaults that each attachment's caps are clamped against."""

    model_config = ConfigDict(extra="forbid")

    recall_cap_bytes: int = Field(
        default=32768,
        description="Default per-recall page cap in bytes (1024 to 1048576).",
    )
    provenance: str = Field(
        default="required",
        description="Default provenance mode: `required` or `optional`; "
        "the deprecated `off` is read as `optional`.",
    )
    lock_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Outer timeout budget in seconds for one memory call.",
    )

    @field_validator("provenance", mode="before")
    @classmethod
    def _alias(cls, value: Any) -> Any:
        return _normalize_provenance(value)


class MemoryBackendDecl(BaseModel):
    """One named memory attachment."""

    model_config = ConfigDict(extra="forbid")

    system: str = Field(
        description="Memory backend id. `files` is the only backend that ships "
        "today; V340 names the accepted set.",
    )
    scope: str | None = Field(
        default=None,
        description="Scope expression such as `person/legion` or `local:default`. "
        "Omitted means the solo default `local:default`; V344 checks the grammar.",
    )
    access: str = Field(
        default="inherit",
        description="`inherit` or `grant:<name>[@<version>]`; V346 checks the grammar.",
    )
    trust: Literal["gateway-only", "backend", "trusted"] = Field(
        default="gateway-only",
        description="How far the backend is trusted: `gateway-only` (default), "
        "`backend` or `trusted`.",
    )
    trust_native: bool = Field(
        default=False,
        description="Explicit opt-in to expose the backend's native tools. Default False.",
    )
    phases: list[str] = Field(
        default_factory=lambda: ["all"],
        description='`["all"]` or a list of movement numbers or movement names '
        "in which this attachment is available; V342 checks each entry.",
    )
    caps: MemoryCaps = Field(
        default_factory=MemoryCaps,
        description="Per-attachment caps that clamp the score-level defaults.",
    )
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Backend-specific settings forwarded verbatim; validated by the backend.",
    )

    @field_validator("phases", mode="before")
    @classmethod
    def _stringify_phases(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [str(v) if isinstance(v, int) and not isinstance(v, bool) else v for v in value]
        return value


class MemoryConfig(BaseModel):
    """The top-level ``memory:`` key. ``memory: {}`` is legal."""

    model_config = ConfigDict(extra="forbid")

    backends: dict[str, MemoryBackendDecl] = Field(
        default_factory=dict,
        description=f"Named memory attachments; names match `{BACKEND_NAME_PATTERN}`.",
    )
    defaults: MemoryDefaults = Field(
        default_factory=MemoryDefaults,
        description="Score-level defaults that attachment caps are clamped against.",
    )

    @field_validator("backends")
    @classmethod
    def _check_names(cls, value: dict[str, MemoryBackendDecl]) -> dict[str, MemoryBackendDecl]:
        for name in value:
            if not _BACKEND_NAME_RE.match(name):
                raise ValueError(
                    f"backend name {name!r} must match {BACKEND_NAME_PATTERN} "
                    "(lower-case letter first, then a-z 0-9 -, at most 32 characters)"
                )
        return value
