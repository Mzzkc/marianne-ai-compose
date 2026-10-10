"""Pure models for ordered capability-class instrument chains."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CLASS_NAME_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
ClassName = Annotated[str, StringConstraints(pattern=CLASS_NAME_PATTERN, max_length=40)]
LayerName = Literal["default", "user", "venue"]

# These names are fixed by shipped profile filenames, independent of a user's machine.
BUILTIN_PROFILE_NAMES = frozenset(
    {
        "aider",
        "antigravity",
        "claude-code",
        "cli",
        "cline-cli",
        "codex-cli",
        "crush",
        "gemini-cli",
        "goose",
        "ollama",
        "opencode",
    }
)


def canonical_json(value: object) -> bytes:
    """Stable bytes for file provenance and per-job resolution identity."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def classes_digest(classes: dict[str, ClassSnapshotEntry]) -> str:
    return hashlib.sha256(
        canonical_json({name: entry.model_dump(mode="json") for name, entry in classes.items()})
    ).hexdigest()


class ClassEntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = Field(default=None, min_length=1)


class ClassEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(min_length=1)
    config: ClassEntryConfig = Field(default_factory=ClassEntryConfig)

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_profile(cls, value: object) -> object:
        return {"profile": value} if isinstance(value, str) else value


class GeneratedBy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["mzt-instruments-classes-write", "nannerl"]
    at: datetime
    probe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ClassesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    generated: GeneratedBy | None = None
    classes: dict[ClassName, list[ClassEntry] | None]

    @model_validator(mode="after")
    def _chains_are_well_formed(self) -> ClassesFile:
        for name, chain in self.classes.items():
            if name in BUILTIN_PROFILE_NAMES:
                raise ValueError(f"class '{name}' is a builtin instrument profile name")
            if chain is None:
                continue
            if not chain:
                raise ValueError(f"class '{name}' has an empty chain; remove it or write null")
            seen: set[tuple[str, str | None]] = set()
            for entry in chain:
                key = (entry.profile, entry.config.model)
                if key in seen:
                    raise ValueError(f"class '{name}' lists the same step twice: {key}")
                seen.add(key)
        return self


class ClassLayerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layer: LayerName
    path: Path
    sha256: str | None = None


class ClassSnapshotEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain: list[ClassEntry] = Field(min_length=1)
    source_layer: LayerName
    resolved_at: Literal["submit", "resume"] = "submit"


class ClassSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classes: dict[ClassName, ClassSnapshotEntry] = Field(default_factory=dict)
    layers: list[ClassLayerRecord] = Field(default_factory=list)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_classes(
        cls,
        classes: dict[str, ClassSnapshotEntry],
        layers: list[ClassLayerRecord],
    ) -> ClassSnapshot:
        return cls(classes=classes, layers=layers, digest=classes_digest(classes))


class InstrumentResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested: str
    kind: Literal["class"] = "class"
    chain: list[str]
    dropped_duplicates: list[str] = Field(default_factory=list)
    snapshot_digest: str
