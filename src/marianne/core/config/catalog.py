"""Instrument catalog v2: evidence-backed model records grouped by family (GH #549, MC1a).

The v1 catalog (``plugins/marianne/docs/ref/instrument-catalog.yaml``) is one
2,700-line file whose tiers are price bands and whose entries mostly carry a
single verification date. v2 is a directory: one YAML per model family plus an
``index.yaml``. Each model says which Marianne profiles (or services) reach it
and how it is paid for, which reasoning efforts it maps, and which capability
tier it sits in *at each effort*, so a class such as "Opus-class at high" can be
generated from data instead of from a price band.

This module is pure schema plus a validating loader. Nothing at runtime reads
the catalog yet.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from marianne.core.config.instrument_settings import EffortLevel

CATALOG_SCHEMA_VERSION = 2
INDEX_FILENAME = "index.yaml"

Access = Literal["subscription", "free", "metered", "local"]
Tier = Literal["frontier", "balanced", "light"]
TierKey = EffortLevel | Literal["default"]
RouteKind = Literal["profile", "service"]
Modality = Literal[
    "text",
    "vision",
    "multimodal",
    "audio-in",
    "speech-out",
    "audio-gen",
    "image-gen",
    "video-gen",
    "embedding",
    "reranking",
]


class CatalogError(ValueError):
    """The catalog directory is missing, malformed, or inconsistent."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Route(_Strict):
    profile: str = Field(
        min_length=1,
        description=(
            "Marianne instrument profile that reaches the model (kind 'profile'), "
            "or the hosting service or runtime named by v1 available_via that is "
            "not a Marianne profile, such as self-host or openrouter (kind 'service')."
        ),
    )
    model_id: str = Field(
        min_length=1,
        description=(
            "The model name the route expects (a profile models: row name, or the "
            "service's own identifier); equals the catalog id unless a v1 "
            "runs_models entry spells it differently, for example gpt-5.6-sol[1m]."
        ),
    )
    access: Access = Field(
        description=(
            "How the route is paid for: subscription (flat-rate plan), free "
            "(no cost under typical use), metered (per-token billing) or local "
            "(runs on the user's own hardware). Derived by the v1 converter from "
            "the profile and the v1 tags; correct it from evidence, not price."
        ),
    )
    kind: RouteKind = Field(
        default="profile",
        description="Whether `profile` names a Marianne instrument profile or only a service.",
    )


class Reasoning(_Strict):
    modes: list[EffortLevel] = Field(
        default_factory=list,
        description=(
            "Reasoning efforts the model maps, in Marianne's vocabulary "
            "(minimal, low, medium, high, xhigh, max). Empty means not yet recorded "
            "or the model has no reasoning control."
        ),
    )
    default: EffortLevel | None = Field(
        default=None,
        description="Effort the model runs at when none is requested, when known.",
    )

    @model_validator(mode="after")
    def _default_is_a_mode(self) -> Reasoning:
        if self.default is not None and self.modes and self.default not in self.modes:
            raise ValueError(f"reasoning default '{self.default}' is not in modes {self.modes}")
        return self


class TierStatus(_Strict):
    provisional: bool = Field(
        description=(
            "True while tier_by_effort is a guess (derived from v1 tags) rather than "
            "backed by benchmarks or measured evidence."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="Why the tiers are provisional, or the evidence once they are not.",
    )

    @model_validator(mode="after")
    def _provisional_needs_reason(self) -> TierStatus:
        if self.provisional and not self.reason:
            raise ValueError("a provisional tier must say why")
        return self


class Benchmark(_Strict):
    name: str = Field(min_length=1, description="Benchmark name, for example swe_bench_verified.")
    value: float = Field(description="Reported score as a fraction (0.78 is 78 percent).")
    source_url: str | None = Field(
        default=None,
        description="Where the score was published; null when carried from v1 without a source.",
    )
    as_of: date | None = Field(
        default=None,
        description="Date the score was read; null when carried from v1 without a date.",
    )


class CatalogModel(_Strict):
    id: str = Field(
        min_length=1,
        description="Catalog id, unique across the whole catalog (the v1 musician key).",
    )
    family: str = Field(
        min_length=1,
        description="Family file this model belongs to (lowercase slug, for example claude).",
    )
    lineage: str | None = Field(
        default=None,
        description="Release line inside the family as v1 named it, for example 'Claude 5'.",
    )
    provider: str = Field(min_length=1, description="Organisation that makes the model.")
    released: str | None = Field(
        default=None,
        description="Release date as far as known: YYYY-MM-DD, or YYYY-MM when only the month is.",
    )
    status: str | None = Field(
        default=None,
        description="Lifecycle label carried from v1 (stable, ga, historical, deprecated, ...).",
    )
    successor: str | None = Field(
        default=None, description="Catalog id of the model that replaces this one, when known."
    )
    context_window: int | None = Field(
        default=None, gt=0, description="Input context in tokens; null when not verified."
    )
    max_output_tokens: int | None = Field(
        default=None, gt=0, description="Largest single response in tokens; null when unknown."
    )
    modalities: list[Modality] = Field(
        default_factory=list,
        description="What the model takes in or produces, in v1's modality vocabulary.",
    )
    routes: list[Route] = Field(
        default_factory=list,
        description="Every way Marianne or a user can reach the model.",
    )
    reasoning: Reasoning = Field(
        default_factory=Reasoning, description="Reasoning efforts the model maps."
    )
    tier_by_effort: dict[TierKey, Tier] = Field(
        default_factory=dict,
        description=(
            "Capability tier at each effort level, or under 'default' when effort does "
            "not change the tier: frontier (Opus-class), balanced (Sonnet-class), "
            "light (Haiku-class). Empty for models that are not reasoning workers."
        ),
    )
    tier_status: TierStatus = Field(
        default_factory=lambda: TierStatus(provisional=False),
        description="Whether the tiers are provisional guesses or evidence-backed.",
    )
    benchmarks: list[Benchmark] = Field(
        default_factory=list, description="Published scores with source and date."
    )
    strengths: list[str] = Field(
        default_factory=list, max_length=3, description="At most three one-line strengths."
    )
    weaknesses: list[str] = Field(
        default_factory=list, max_length=3, description="At most three one-line weaknesses."
    )
    quirks: list[str] = Field(
        default_factory=list, description="Behaviour a composer must know before using the model."
    )
    last_verified: date | None = Field(
        default=None, description="When the facts on this record were last checked."
    )
    verified_via: list[str] = Field(
        default_factory=list, description="Evidence lanes behind last_verified."
    )
    legacy: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "v1 fields with no v2 home yet (ratings, tags, task_tags, notes, prices, "
            "license, strengths past three, ...), kept so the conversion loses nothing. "
            "Nothing reads them at runtime; prune as evidence replaces them."
        ),
    )

    @model_validator(mode="after")
    def _unique_routes(self) -> CatalogModel:
        seen: set[tuple[str, str]] = set()
        for route in self.routes:
            key = (route.profile, route.model_id)
            if key in seen:
                raise ValueError(f"model '{self.id}' lists the route {key} twice")
            seen.add(key)
        return self


class CatalogFile(_Strict):
    schema_version: Literal[2] = Field(description="Catalog schema version; always 2 here.")
    generated_at: date = Field(description="Date the file was generated or last regenerated.")
    generated_by: str | None = Field(
        default=None, description="Tool or process that wrote the file."
    )
    families: dict[str, list[CatalogModel]] = Field(
        description="Models by family slug. A generated file holds exactly one family."
    )

    @model_validator(mode="after")
    def _models_match_their_family(self) -> CatalogFile:
        for family, models in self.families.items():
            for model in models:
                if model.family != family:
                    raise ValueError(
                        f"model '{model.id}' has family '{model.family}' under '{family}'"
                    )
        return self


class IndexFamily(_Strict):
    file: str = Field(min_length=1, description="Family file name relative to the catalog dir.")
    model_ids: list[str] = Field(description="Ids of the models in that file, in file order.")


class UnmatchedRoute(_Strict):
    profile: str = Field(min_length=1, description="Instrument profile that listed the model.")
    model_id: str = Field(min_length=1, description="Model name the profile listed.")
    reason: str = Field(
        description="Why no catalog model owns it (no entry in v1, or a placeholder phrase)."
    )


class CatalogIndex(_Strict):
    schema_version: Literal[2] = Field(description="Catalog schema version; always 2 here.")
    generated_at: date = Field(description="Date the index was generated.")
    generated_by: str | None = Field(default=None, description="Tool that wrote the index.")
    source: str | None = Field(
        default=None, description="v1 catalog this directory was converted from."
    )
    model_count: int = Field(ge=0, description="Total models across all family files.")
    families: dict[str, IndexFamily] = Field(description="Family slug to its file and model ids.")
    unmatched_routes: list[UnmatchedRoute] = Field(
        default_factory=list,
        description=(
            "Routes v1 profiles listed for models that have no v1 catalog entry; "
            "kept so no route is lost, and to be turned into entries by MC1b."
        ),
    )

    @model_validator(mode="after")
    def _count_matches(self) -> CatalogIndex:
        total = sum(len(entry.model_ids) for entry in self.families.values())
        if total != self.model_count:
            raise ValueError(f"model_count {self.model_count} but families list {total} ids")
        return self


class Catalog(_Strict):
    index: CatalogIndex = Field(description="The validated index.yaml.")
    files: dict[str, CatalogFile] = Field(description="Family slug to its validated file.")

    def models(self) -> list[CatalogModel]:
        return [
            model for file in self.files.values() for ms in file.families.values() for model in ms
        ]

    def get(self, model_id: str) -> CatalogModel | None:
        return next((m for m in self.models() if m.id == model_id), None)


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc


def load_catalog(directory: Path) -> Catalog:
    """Load and cross-check a v2 catalog directory.

    Raises CatalogError naming the file for any schema violation, a family file
    that disagrees with the index, or a model id used twice.
    """
    index_path = directory / INDEX_FILENAME
    if not index_path.is_file():
        raise CatalogError(f"no {INDEX_FILENAME} in {directory}")
    try:
        index = CatalogIndex.model_validate(_read_yaml(index_path))
    except ValidationError as exc:
        raise CatalogError(f"{index_path}: {exc}") from exc

    files: dict[str, CatalogFile] = {}
    owner: dict[str, str] = {}
    for slug, entry in index.families.items():
        path = directory / entry.file
        if not path.is_file():
            raise CatalogError(f"{index_path} lists {entry.file}, which does not exist")
        try:
            file = CatalogFile.model_validate(_read_yaml(path))
        except ValidationError as exc:
            raise CatalogError(f"{path}: {exc}") from exc
        if list(file.families) != [slug]:
            raise CatalogError(
                f"{path}: families {list(file.families)} but the index says '{slug}'"
            )
        ids = [model.id for model in file.families[slug]]
        if ids != entry.model_ids:
            raise CatalogError(f"{path}: model ids differ from the index")
        for model_id in ids:
            if model_id in owner:
                raise CatalogError(
                    f"model id '{model_id}' is in both '{owner[model_id]}' and '{slug}'"
                )
            owner[model_id] = slug
        files[slug] = file

    on_disk = {p.name for p in directory.glob("*.yaml")} - {INDEX_FILENAME}
    unlisted = on_disk - {entry.file for entry in index.families.values()}
    if unlisted:
        raise CatalogError(f"family files missing from the index: {sorted(unlisted)}")
    return Catalog(index=index, files=files)
