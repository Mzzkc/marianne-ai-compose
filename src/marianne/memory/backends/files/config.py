"""Configuration, tree location and the closed failure taxonomy of the files backend.

Nothing in this module touches the filesystem: the factory's validation is
cheap and pure, and :func:`resolve_files_location` only computes paths.

``FILES_SUBTYPES`` is the closed set of reasons a files-backend call can fail,
each mapped onto its ``errors/1`` code. The spec's fourteen subtypes are kept;
wave A adds five, each named here:

- ``cursor_invalid`` (E7): a recall cursor that was forged, tampered with, or
  minted against a tree that has changed since.
- ``fact_invalid`` (E7): fact text the tier format cannot store (empty, or a
  line that looks like a fact marker), or a tier name that is not a plain
  ``*.md`` file name.
- ``state_corrupt`` (E5): the backend's own ``state.json`` cannot be parsed.
- ``argument_invalid`` (E7): a call argument out of range (fetch offset inside
  a UTF-8 character, a non-positive ``limit``...).
- ``storage_error`` (E5): the operating system refused a read or write.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from marianne.memory.errors import ERROR_CLASSES, ErrorCode, MemoryBackendError, ScopeRefused
from marianne.memory.model import parse_scope

FILES_SUBTYPES: Mapping[str, ErrorCode] = MappingProxyType(
    {
        "config_invalid": ErrorCode.E7,
        "scope_unknown": ErrorCode.E3,
        "custody_violation": ErrorCode.E2,
        "preimage_mismatch": ErrorCode.E5,
        "interrupted_adapter_write": ErrorCode.E5,
        "fact_not_found": ErrorCode.E11,
        "evidence_missing": ErrorCode.E7,
        "claim_mismatch": ErrorCode.E7,
        "evidence_unavailable": ErrorCode.E11,
        "capability_unsupported": ErrorCode.E7,
        "cursor_scope_mismatch": ErrorCode.E7,
        "permission_denied": ErrorCode.E2,
        "lock_timeout": ErrorCode.E9,
        "deadline_exceeded": ErrorCode.E10,
        # wave-A additions (see the module docstring)
        "cursor_invalid": ErrorCode.E7,
        "fact_invalid": ErrorCode.E7,
        "state_corrupt": ErrorCode.E5,
        "argument_invalid": ErrorCode.E7,
        "storage_error": ErrorCode.E5,
    }
)

DEFAULT_RECALL_CAP_BYTES = 32768
MIN_RECALL_CAP_BYTES = 1024
MAX_RECALL_CAP_BYTES = 1024 * 1024


def files_error(subtype: str, detail: str, *, remedy: str = "") -> MemoryBackendError:
    """The typed ``errors/1`` exception for a files-backend ``subtype``."""
    code = FILES_SUBTYPES[subtype]
    return ERROR_CLASSES[code](detail, subtype=subtype, remedy=remedy)


def _absolute_path(value: str) -> str:
    if not os.path.isabs(value):
        raise ValueError(f"{value!r} is not an absolute path")
    if ".." in Path(value).parts:
        raise ValueError(f"{value!r} contains a `..` component")
    return os.path.normpath(value)


class FilesBackendConfig(BaseModel):
    """One files-backend attachment, as the gateway hands it to the factory."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    attachment_id: str = Field(
        min_length=1,
        max_length=256,
        description="Identity of the attachment (attachment name and canonical scope).",
    )
    scope: str = Field(
        min_length=1,
        description="Scope expression this attachment serves; canonicalised on load.",
    )
    memory_tree: str = Field(
        min_length=1,
        description="Absolute path of the Markdown tier directory for this scope.",
    )
    attachment_dir: str = Field(
        min_length=1,
        description=(
            "Absolute path where displaced versions, preimages, archives and action "
            "receipts are kept. Must be on the same filesystem as memory_tree."
        ),
    )
    state_root: str = Field(
        min_length=1,
        description="Absolute path of the per-tree writer state root (`.../files/trees`).",
    )
    registry_root: str = Field(
        default="",
        description="AGENTS registry root for registry persons. Not supported in wave A.",
    )
    recall_cap_bytes: int = Field(
        default=DEFAULT_RECALL_CAP_BYTES,
        ge=MIN_RECALL_CAP_BYTES,
        le=MAX_RECALL_CAP_BYTES,
        description="Per-response recall page cap in bytes (1 KiB to 1 MiB).",
    )
    legacy: Literal["verbatim", "sectioned"] = Field(
        default="sectioned",
        description="Recall formatting mode. Only `sectioned` is built in wave A.",
    )
    provenance: Literal["required", "optional"] = Field(
        default="required",
        description="Whether declare must carry an evidence locator.",
    )
    lock_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=3600,
        allow_inf_nan=False,
        description="Longest wait for the tree lock; also clamped by the call deadline.",
    )

    @field_validator("scope")
    @classmethod
    def _canonical_scope(cls, value: str) -> str:
        try:
            return parse_scope(value).canonical
        except ScopeRefused as exc:
            raise ValueError(exc.detail) from None

    @field_validator("memory_tree", "attachment_dir", "state_root")
    @classmethod
    def _absolute(cls, value: str) -> str:
        return _absolute_path(value)


@dataclass(frozen=True)
class FilesLocation:
    """Where one scope's files live: the tier tree, its attachment dir, the state root."""

    memory_tree: str
    attachment_dir: str
    state_root: str

    def as_config(self) -> dict[str, str]:
        return {
            "memory_tree": self.memory_tree,
            "attachment_dir": self.attachment_dir,
            "state_root": self.state_root,
        }


def default_files_root() -> Path:
    """``~/.marianne/memory/files`` for the current user."""
    return Path.home() / ".marianne" / "memory" / "files"


def resolve_files_location(scope: str, root: Path | str | None = None) -> FilesLocation:
    """Map a scope onto its scope-family tree under ``root``.

    ``project/demo`` -> ``<root>/project/demo``; ``local:default`` ->
    ``<root>/local/default``; ``organization/x`` -> ``<root>/org/x``. Writer
    state for every tree lives under ``<root>/trees``. A scope that would
    resolve outside ``root`` is refused (E3 ``scope_unknown``). No I/O.
    """
    try:
        key = parse_scope(scope)
    except ScopeRefused as exc:
        raise files_error("scope_unknown", exc.detail, remedy=exc.remedy) from None
    chosen = root if root is not None else default_files_root()
    base = os.path.normpath(os.path.abspath(os.fspath(chosen)))
    tree = os.path.normpath(os.path.join(base, key.family, key.ident))
    if os.path.dirname(os.path.dirname(tree)) != base:
        raise files_error(
            "scope_unknown",
            f"scope {key.canonical!r} resolves outside the memory root",
            remedy="use a scope id made of a-z 0-9 _ . - that is not a path",
        )
    return FilesLocation(
        memory_tree=tree,
        attachment_dir=tree,
        state_root=os.path.join(base, "trees"),
    )
