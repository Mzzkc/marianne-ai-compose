"""Specification corpus configuration models.

Defines SpecFragment (a single spec document) and SpecCorpusConfig (the
collection of fragments loaded from a project's spec directory). These models
are the data layer for the spec corpus pipeline:

    YAML/MD files → SpecCorpusLoader → list[SpecFragment]
                                         ↓
                              SpecCorpusConfig (in JobConfig)
                                         ↓
                              PromptBuilder (injected per-sheet)
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SpecFragment(BaseModel):
    """A single specification fragment loaded from the spec corpus.

    Each fragment corresponds to one file in the project's spec directory.
    Structured YAML files produce fragments with parsed ``data``; markdown
    files produce text fragments.

    Fragments are tagged for per-sheet filtering: a score can declare
    ``spec_tags: {1: ["goals", "safety"]}`` so sheet 1 only receives
    fragments matching those tags.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        description="Fragment identifier, typically the filename stem "
        "(e.g., 'intent', 'constraints').",
    )
    content: str = Field(
        description="The full text content of the fragment. For YAML spec files, "
        "this is the 'content' field value. For markdown files, the entire file body.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags for per-sheet filtering. Fragments with no tags are "
        "included in all sheets unless explicitly filtered out.",
    )
    kind: Literal["text", "structured"] = Field(
        default="text",
        description="Fragment kind: 'text' for plain markdown, "
        "'structured' for parsed YAML with a data section.",
    )
    data: dict[str, Any] | None = Field(
        default=None,
        description="Parsed structured data from the YAML 'data' field. "
        "None for text fragments.",
    )

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        """Ensure fragment name is not empty or whitespace-only."""
        if not v.strip():
            raise ValueError("SpecFragment name must not be empty")
        return v

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        """Ensure fragment content is not empty."""
        if not v.strip():
            raise ValueError("SpecFragment content must not be empty")
        return v


class SpecCorpusConfig(BaseModel):
    """Configuration for the specification corpus.

    Controls where spec fragments are loaded from and how they are filtered
    for injection into agent prompts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_dir: str = Field(
        default="",
        description="Path to the specification corpus directory, "
        "relative to the project root. Empty string means no spec directory "
        "configured (spec loading is opt-in).",
    )
    include_claude_md: bool = Field(
        default=False,
        description="Whether to include CLAUDE.md as a spec fragment. "
        "When True, the loader will look for CLAUDE.md in the project root "
        "and include it as a text fragment with name='claude_md'.",
    )
    fragments: list[SpecFragment] = Field(
        default_factory=list,
        description="Loaded spec fragments. Populated by the loader at runtime, "
        "not set in score YAML.",
    )

    def get_fragments_by_tags(self, tags: list[str]) -> list[SpecFragment]:
        """Filter fragments by tags.

        Args:
            tags: Tags to filter by. A fragment matches if it has at least
                one tag in common with the filter list. An empty filter list
                returns all fragments (no filtering).

        Returns:
            List of matching fragments.
        """
        if not tags:
            return list(self.fragments)

        tag_set = set(tags)
        return [f for f in self.fragments if tag_set & set(f.tags)]

    def corpus_hash(self) -> str:
        """Compute a deterministic hash of the corpus content.

        The hash is order-independent: the same set of fragments produces
        the same hash regardless of insertion order. This prevents false
        drift detection when filesystem listing order varies across OS.

        Returns:
            Hex digest string. Empty corpus produces a consistent empty hash.
        """
        if not self.fragments:
            return hashlib.sha256(b"").hexdigest()

        # Sort by name for order independence, then hash content
        sorted_fragments = sorted(self.fragments, key=lambda f: f.name)
        hasher = hashlib.sha256()
        for frag in sorted_fragments:
            hasher.update(frag.name.encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(frag.content.encode("utf-8"))
            hasher.update(b"\x00")
        return hasher.hexdigest()
