"""Versioned examples for composition craft, independent of any training backend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CORPUS_SCHEMA_VERSION: Literal[1] = 1


class CorpusProvenance(BaseModel):
    """Local source location and, for telemetry, opaque collection lineage."""

    model_config = ConfigDict(extra="forbid")

    source_path: str
    source_kind: Literal["in_repo", "telemetry"]
    category: str | None = None
    schema_version: Literal[1] = CORPUS_SCHEMA_VERSION
    source_schema_version: int | None = None
    content_hash: str | None = None
    consent_hash: str | None = None


class TrainingExample(BaseModel):
    """A score and an optional human-authored explanation of its design."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1)
    input: str | None = None
    output: str = Field(min_length=1)
    rationale: str | None = None
    provenance: CorpusProvenance
