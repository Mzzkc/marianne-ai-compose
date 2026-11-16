"""Lossless converter from the v1 instrument catalog to the v2 directory (GH #549, MC1a).

Pure functions plus one writer. ``mzt instruments catalog convert`` is the CLI
home. The conversion keeps every v1 musician (as a v2 model), every route v1
states (``instruments.*.runs_models`` and ``available_via``) and every v1 field
that has no v2 home (under ``legacy``). Tiers are *derived guesses*, always
marked provisional: v1 tiers are price bands, which mean nothing on a
subscription.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from marianne.core.config.catalog import (
    CATALOG_SCHEMA_VERSION,
    INDEX_FILENAME,
    CatalogFile,
    CatalogIndex,
    CatalogModel,
    IndexFamily,
    Route,
    UnmatchedRoute,
)

CONVERTER_NAME = "mzt instruments catalog convert"

# v1 fields this converter maps to a v2 field; everything else goes to `legacy`.
_MAPPED = frozenset(
    {
        "provider",
        "family",
        "released",
        "status",
        "successor",
        "superseded_by",
        "context_window",
        "max_output_tokens",
        "modality_tags",
        "available_via",
        "thinking_levels",
        "reasoning_effort",
        "benchmarks",
        "strengths",
        "weaknesses",
        "quirks",
        "last_verified",
        "verified_via",
    }
)

# Family slug by v1 family-label prefix (first match wins). A label matching no
# prefix becomes its own slug.
_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude", "claude"),
    ("gpt", "gpt"),
    ("o-series", "gpt"),
    ("text-embedding", "gpt"),
    ("gemini", "gemini"),
    ("gemma", "gemma"),
    ("glm", "glm"),
    ("qwen", "qwen"),
    ("qwq", "qwen"),
    ("deepseek", "deepseek"),
    ("mistral", "mistral"),
    ("codestral", "mistral"),
    ("phi", "phi"),
    ("llama", "llama"),
    ("nemotron", "nemotron"),
    ("command", "cohere"),
    ("aya", "cohere"),
    ("embed", "cohere"),
    ("rerank", "cohere"),
    ("whisper", "whisper"),
    ("flux", "flux"),
    ("jina", "jina"),
    ("nomic", "nomic"),
    ("bge", "bge"),
    ("voyage", "voyage"),
    ("ltx", "ltx"),
    ("hidream", "hidream"),
    ("minicpm", "minicpm"),
    ("jais", "jais"),
    ("yi", "yi"),
    ("minimax", "minimax"),
    ("elevenlabs", "elevenlabs"),
    ("ideogram", "ideogram"),
    ("recraft", "recraft"),
    ("sd", "stable-diffusion"),
    ("stable", "stable-diffusion"),
    ("pixart", "pixart"),
)

# Hosting or runtime names in v1 available_via that run on the user's hardware.
_LOCAL_SERVICES = frozenset(
    {
        "self-host",
        "ollama",
        "huggingface",
        "vllm",
        "sglang",
        "comfyui",
        "gradio",
        "sentence-transformers",
        "nemo",
        "local_python",
        "diffusers",
        "transformers",
        "modelscope",
    }
)
_SUBSCRIPTION_PROFILES = frozenset(
    {
        "claude-code",
        "codex-cli",
        "gemini-cli",
        "antigravity",
        "z-ai-coding-plan",
        "claude-code-z-ai-gateway",
    }
)
_SPECIALIST_MODALITIES = frozenset(
    {"embedding", "reranking", "image-gen", "video-gen", "audio-gen", "speech-out"}
)
_PROVISIONAL_REASON = (
    "Derived from v1 price-band tags by catalog v1->v2 conversion (max/premier -> "
    "frontier; heavy -> frontier when v1 ratings.reasoning >= 5 else balanced; "
    "standard -> balanced; quick -> light), not from benchmarks or measured "
    "evidence. Replace from evidence in MC1b."
)
_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


def family_slug(label: str) -> str:
    """Family file slug for a v1 family label ('Claude 5' -> 'claude')."""
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    for prefix, slug in _FAMILY_PREFIXES:
        if base.startswith(prefix):
            return slug
    stripped = re.sub(r"\s+v?\d[\w.\-]*$", "", label.strip())
    return re.sub(r"[^a-z0-9]+", "-", stripped.lower()).strip("-") or base


def _access(profile: str, model_id: str, tags: set[str]) -> str:
    if profile in _LOCAL_SERVICES:
        return "local"
    if profile in _SUBSCRIPTION_PROFILES or model_id.startswith("zai-coding-plan/"):
        return "subscription"
    if model_id.endswith(":free") or ("free" in tags and profile in {"opencode", "openrouter"}):
        return "free"
    return "metered"


def _tier(tags: set[str], ratings: dict[str, Any]) -> str | None:
    if tags & {"max", "premier"}:
        return "frontier"
    if "heavy" in tags:
        reasoning = ratings.get("reasoning")
        return "frontier" if isinstance(reasoning, (int, float)) and reasoning >= 5 else "balanced"
    if "standard" in tags:
        return "balanced"
    if "quick" in tags:
        return "light"
    return None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _owner(name: str, musicians: dict[str, Any]) -> str | None:
    if name in musicians:
        return name
    stripped = re.sub(r"\[\d+[mk]\]$", "", name)
    return stripped if stripped in musicians else None


def _routes(
    v1: dict[str, Any],
) -> tuple[dict[str, list[Route]], list[UnmatchedRoute]]:
    musicians: dict[str, Any] = v1["musicians"]
    instruments: dict[str, Any] = v1["instruments"]
    routes: dict[str, list[Route]] = {key: [] for key in musicians}
    unmatched: list[UnmatchedRoute] = []

    def add(owner: str, profile: str, model_id: str) -> None:
        if any(r.profile == profile and r.model_id == model_id for r in routes[owner]):
            return
        tags = set(musicians[owner].get("tags") or [])
        routes[owner].append(
            Route(
                profile=profile,
                model_id=model_id,
                access=_access(profile, owner, tags),  # type: ignore[arg-type]
                kind="profile" if profile in instruments else "service",
            )
        )

    for profile, row in instruments.items():
        for name in row.get("runs_models") or []:
            owner = _owner(name, musicians)
            if owner is None:
                reason = (
                    "placeholder phrase, not a model"
                    if name.lower().startswith("any ")
                    else "no v1 musician entry for this model"
                )
                unmatched.append(UnmatchedRoute(profile=profile, model_id=name, reason=reason))
            else:
                add(owner, profile, name)
    for key, row in musicians.items():
        for via in row.get("available_via") or []:
            add(key, via, key)
    return routes, unmatched


def _model(key: str, row: dict[str, Any], routes: list[Route]) -> CatalogModel:
    tags = set(row.get("tags") or [])
    modalities = list(row.get("modality_tags") or [])
    legacy: dict[str, Any] = {k: v for k, v in row.items() if k not in _MAPPED}
    legacy["tags"] = row.get("tags") or []

    modes = [
        lv
        for lv in _EFFORTS
        if lv in (row.get("thinking_levels") or row.get("reasoning_effort") or [])
    ]
    for field in ("thinking_levels", "reasoning_effort"):
        if row.get(field) and set(row[field]) - set(_EFFORTS):
            legacy[field] = row[field]

    verified = _parse_date(row.get("last_verified"))
    if row.get("last_verified") is not None and verified is None:
        legacy["last_verified_raw"] = row["last_verified"]

    strengths = list(row.get("strengths") or [])
    weaknesses = list(row.get("weaknesses") or [])
    if len(strengths) > 3:
        legacy["strengths_all"] = strengths
    if len(weaknesses) > 3:
        legacy["weaknesses_all"] = weaknesses

    tier = None
    if not set(modalities) & _SPECIALIST_MODALITIES and "text" in modalities:
        tier = _tier(tags, row.get("ratings") or {})
    status: dict[str, Any] = {"provisional": False}
    if tier:
        status = {"provisional": True, "reason": _PROVISIONAL_REASON}

    return CatalogModel(
        id=key,
        family=family_slug(row["family"]),
        lineage=row["family"],
        provider=row["provider"],
        released=str(row["released"]) if row.get("released") is not None else None,
        status=row.get("status"),
        successor=row.get("successor") or row.get("superseded_by"),
        context_window=row.get("context_window"),
        max_output_tokens=row.get("max_output_tokens"),
        modalities=modalities,
        routes=routes,
        reasoning={"modes": modes},  # type: ignore[arg-type]
        tier_by_effort={"default": tier} if tier else {},  # type: ignore[dict-item]
        tier_status=status,  # type: ignore[arg-type]
        benchmarks=[
            {"name": name, "value": value}  # type: ignore[misc]
            for name, value in (row.get("benchmarks") or {}).items()
        ],
        strengths=strengths[:3],
        weaknesses=weaknesses[:3],
        quirks=list(row.get("quirks") or []),
        last_verified=verified,
        verified_via=list(row.get("verified_via") or []),
        legacy=legacy,
    )


def convert_v1(
    v1: dict[str, Any], generated_at: date | None = None
) -> tuple[dict[str, CatalogFile], CatalogIndex]:
    """Convert a loaded v1 catalog into family files and an index."""
    if v1.get("schema_version") != 1:
        raise ValueError(f"expected a v1 catalog, got schema_version={v1.get('schema_version')!r}")
    generated_at = generated_at or date.today()
    routes, unmatched = _routes(v1)
    by_family: dict[str, list[CatalogModel]] = {}
    for key, row in v1["musicians"].items():
        model = _model(key, row, routes[key])
        by_family.setdefault(model.family, []).append(model)
    files = {
        slug: CatalogFile(
            schema_version=CATALOG_SCHEMA_VERSION,  # type: ignore[arg-type]
            generated_at=generated_at,
            generated_by=CONVERTER_NAME,
            families={slug: models},
        )
        for slug, models in sorted(by_family.items())
    }
    index = CatalogIndex(
        schema_version=CATALOG_SCHEMA_VERSION,  # type: ignore[arg-type]
        generated_at=generated_at,
        generated_by=CONVERTER_NAME,
        source="instrument-catalog.yaml (schema_version 1)",
        model_count=sum(len(m) for m in by_family.values()),
        families={
            slug: IndexFamily(file=f"{slug}.yaml", model_ids=[m.id for m in models])
            for slug, models in sorted(by_family.items())
        },
        unmatched_routes=unmatched,
    )
    return files, index


def _dump(model: Any) -> str:
    data = model.model_dump(mode="json", exclude_defaults=False)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def write_catalog(source: Path, out_dir: Path, generated_at: date | None = None) -> CatalogIndex:
    """Read a v1 catalog file and write the v2 directory; returns the index."""
    v1 = yaml.safe_load(source.read_text(encoding="utf-8"))
    files, index = convert_v1(v1, generated_at)
    out_dir.mkdir(parents=True, exist_ok=True)
    for slug, file in files.items():
        header = (
            f"# Catalog v2 family '{slug}'. Generated by `{CONVERTER_NAME}` from the v1 "
            "catalog.\n# Tiers marked provisional are guesses from v1 tags, not evidence.\n"
        )
        (out_dir / f"{slug}.yaml").write_text(header + _dump(file), encoding="utf-8")
    (out_dir / INDEX_FILENAME).write_text(_dump(index), encoding="utf-8")
    return index
