"""Catalog v2 schema, the v1 converter, and the generated catalog directory (GH #549, MC1a)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config.catalog import (
    Benchmark,
    CatalogError,
    CatalogModel,
    Reasoning,
    Route,
    TierStatus,
    _Strict,
    load_catalog,
)
from marianne.instruments.catalog_v1 import convert_v1, family_slug, write_catalog

REF = Path(__file__).parent.parent / "plugins" / "marianne" / "docs" / "ref"
V1_PATH = REF / "instrument-catalog.yaml"
V2_DIR = REF / "catalog"

needs_catalog = pytest.mark.skipif(
    not V1_PATH.is_file(), reason="instrument catalog is an optional plugin submodule asset"
)


def _v1() -> dict[str, Any]:
    loaded = yaml.safe_load(V1_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _model(**overrides: Any) -> CatalogModel:
    base: dict[str, Any] = {"id": "m", "family": "f", "provider": "p"}
    return CatalogModel(**{**base, **overrides})


# -- schema ----------------------------------------------------------------


def test_every_model_field_has_a_description() -> None:
    for name, field in CatalogModel.model_fields.items():
        assert field.description, f"CatalogModel.{name} has no description"


_VALID: dict[type[_Strict], dict[str, Any]] = {
    CatalogModel: {"id": "m", "family": "f", "provider": "p"},
    Route: {"profile": "claude-code", "model_id": "m", "access": "subscription"},
    Reasoning: {},
    TierStatus: {"provisional": False},
    Benchmark: {"name": "b", "value": 0.5},
}


@given(key=st.text(min_size=1, max_size=12).filter(lambda k: k not in CatalogModel.model_fields))
def test_every_catalog_model_refuses_an_unknown_key(key: str) -> None:
    """_Strict forbids extras, so a misspelled field never vanishes silently."""
    for cls, valid in _VALID.items():
        assert issubclass(cls, _Strict)
        cls(**valid)
        if key in cls.model_fields:
            continue
        with pytest.raises(ValidationError):
            cls(**{**valid, key: 1})


def test_provisional_tier_must_say_why() -> None:
    with pytest.raises(ValidationError, match="must say why"):
        TierStatus(provisional=True)


def test_reasoning_default_must_be_a_mode() -> None:
    with pytest.raises(ValidationError, match="not in modes"):
        Reasoning(modes=["low", "high"], default="max")


def test_more_than_three_strengths_is_refused() -> None:
    with pytest.raises(ValidationError):
        _model(strengths=["a", "b", "c", "d"])


def test_unknown_effort_and_unknown_access_are_refused() -> None:
    with pytest.raises(ValidationError):
        _model(tier_by_effort={"ultra": "frontier"})
    with pytest.raises(ValidationError):
        _model(routes=[{"profile": "claude-code", "model_id": "m", "access": "free-ish"}])


def test_duplicate_route_is_refused() -> None:
    route = {"profile": "claude-code", "model_id": "m", "access": "subscription"}
    with pytest.raises(ValidationError, match="twice"):
        _model(routes=[route, route])


def test_tier_by_effort_accepts_efforts_and_default() -> None:
    model = _model(tier_by_effort={"default": "balanced", "max": "frontier"})
    assert model.tier_by_effort == {"default": "balanced", "max": "frontier"}


# -- converter on a synthetic v1 ---------------------------------------------


def _tiny_v1() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "instruments": {
            "claude-code": {"runs_models": ["big", "gone", "big-v2[1m]"]},
            "aider": {"runs_models": ["any LiteLLM-supported model"]},
            "ollama": {"runs_models": ["tiny:7b"]},
        },
        "musicians": {
            "big": {
                "provider": "anthropic",
                "family": "Big 5",
                "tags": ["max", "premier"],
                "modality_tags": ["text"],
                "available_via": ["claude-code", "bedrock"],
                "ratings": {"reasoning": 5},
                "notes": "kept",
                "last_verified": "2026-04-29",
                "strengths": ["a", "b", "c", "d"],
                "thinking_levels": ["low", "high"],
            },
            "big-v2": {
                "provider": "anthropic",
                "family": "Big 5",
                "tags": ["heavy"],
                "modality_tags": ["text"],
                "ratings": {"reasoning": 4},
            },
            "tiny:7b": {
                "provider": "x",
                "family": "Tiny",
                "tags": ["quick", "open-weights"],
                "modality_tags": ["text"],
                "available_via": ["ollama"],
            },
            "embedder": {
                "provider": "x",
                "family": "Embed",
                "tags": ["standard"],
                "modality_tags": ["embedding"],
            },
        },
    }


def test_converter_maps_tags_to_provisional_tiers() -> None:
    files, _ = convert_v1(_tiny_v1(), date(2026, 11, 16))
    by_id = {m.id: m for f in files.values() for ms in f.families.values() for m in ms}
    assert by_id["big"].tier_by_effort == {"default": "frontier"}
    assert by_id["big-v2"].tier_by_effort == {"default": "balanced"}  # heavy, reasoning 4
    assert by_id["tiny:7b"].tier_by_effort == {"default": "light"}
    assert by_id["embedder"].tier_by_effort == {}
    assert by_id["big"].tier_status.provisional and by_id["big"].tier_status.reason
    assert not by_id["embedder"].tier_status.provisional


def test_converter_keeps_routes_legacy_and_overflow() -> None:
    files, index = convert_v1(_tiny_v1(), date(2026, 11, 16))
    by_id = {m.id: m for f in files.values() for ms in f.families.values() for m in ms}
    big = by_id["big"]
    assert {(r.profile, r.model_id, r.access) for r in big.routes} == {
        ("claude-code", "big", "subscription"),
        ("bedrock", "big", "metered"),
    }
    assert [(r.profile, r.model_id) for r in by_id["big-v2"].routes] == [
        ("claude-code", "big-v2[1m]")
    ]
    assert by_id["tiny:7b"].routes[0].access == "local"
    assert big.legacy["ratings"] == {"reasoning": 5} and big.legacy["notes"] == "kept"
    assert big.legacy["strengths_all"] == ["a", "b", "c", "d"] and len(big.strengths) == 3
    assert big.reasoning.modes == ["low", "high"]
    assert {(u.profile, u.model_id) for u in index.unmatched_routes} == {
        ("claude-code", "gone"),
        ("aider", "any LiteLLM-supported model"),
    }


def test_converter_refuses_non_v1() -> None:
    with pytest.raises(ValueError, match="v1"):
        convert_v1({"schema_version": 2})


@pytest.mark.parametrize(
    ("label", "slug"),
    [
        ("Claude 5", "claude"),
        ("GPT-5.6", "gpt"),
        ("o-series", "gpt"),
        ("GLM 4.5", "glm"),
        ("Qwen3.6", "qwen"),
        ("QwQ", "qwen"),
        ("PixArt-Σ", "pixart"),
        ("Some Thing 2", "some-thing"),
    ],
)
def test_family_slug(label: str, slug: str) -> None:
    assert family_slug(label) == slug


# -- loader ------------------------------------------------------------------


def test_loader_rejects_index_file_disagreement(tmp_path: Path) -> None:
    files, index = convert_v1(_tiny_v1(), date(2026, 11, 16))
    write_catalog_from(tmp_path, _tiny_v1())
    family_file = tmp_path / "big.yaml"
    text = family_file.read_text(encoding="utf-8").replace("id: big-v2", "id: big-v3")
    family_file.write_text(text, encoding="utf-8")
    with pytest.raises(CatalogError, match="differ from the index"):
        load_catalog(tmp_path)
    assert files and index


def test_loader_rejects_unlisted_family_file_and_missing_index(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="no index.yaml"):
        load_catalog(tmp_path)
    write_catalog_from(tmp_path, _tiny_v1())
    (tmp_path / "stray.yaml").write_text("schema_version: 2\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="missing from the index"):
        load_catalog(tmp_path)


def write_catalog_from(out: Path, v1: dict[str, Any]) -> None:
    src = out.parent / f"{out.name}-v1.yaml"
    src.write_text(yaml.safe_dump(v1), encoding="utf-8")
    write_catalog(src, out, date(2026, 11, 16))


# -- the real catalog ---------------------------------------------------------


@needs_catalog
def test_round_trip_loses_no_musician_and_no_route(tmp_path: Path) -> None:
    v1 = _v1()
    write_catalog(V1_PATH, tmp_path, date(2026, 11, 16))
    catalog = load_catalog(tmp_path)
    models = {m.id: m for m in catalog.models()}

    assert set(models) == set(v1["musicians"]), "every v1 musician appears exactly once"
    assert len(catalog.models()) == len(v1["musicians"])

    unmatched = {(u.profile, u.model_id) for u in catalog.index.unmatched_routes}
    for profile, row in v1["instruments"].items():
        for name in row.get("runs_models") or []:
            owner = name if name in models else re.sub(r"\[\d+[mk]\]$", "", name)
            if owner in models:
                assert (profile, name) in {(r.profile, r.model_id) for r in models[owner].routes}
            else:
                assert (profile, name) in unmatched
    for key, row in v1["musicians"].items():
        have = {r.profile for r in models[key].routes}
        assert set(row.get("available_via") or []) <= have, key
        for field in ("provider", "context_window", "max_output_tokens"):
            assert getattr(models[key], field) == row.get(field), (key, field)


@needs_catalog
def test_every_v1_field_survives_in_a_v2_field_or_legacy(tmp_path: Path) -> None:
    v1 = _v1()
    write_catalog(V1_PATH, tmp_path, date(2026, 11, 16))
    models = {m.id: m for m in load_catalog(tmp_path).models()}
    homed = {
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
    for key, row in v1["musicians"].items():
        missing = set(row) - homed - set(models[key].legacy)
        assert not missing, f"{key} dropped {sorted(missing)}"


@needs_catalog
def test_committed_catalog_directory_loads_and_covers_v1() -> None:
    catalog = load_catalog(V2_DIR)
    # MC1b refreshes add models v1 never had; nothing v1 had may be lost.
    assert set(_v1()["musicians"]) <= {m.id for m in catalog.models()}
    assert catalog.index.model_count == len(catalog.models())
    provisional = [m for m in catalog.models() if m.tier_status.provisional]
    assert provisional and all(m.tier_by_effort for m in provisional)
    assert all(m.tier_status.reason for m in provisional)


@needs_catalog
def test_cli_convert_and_check(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "catalog"
    result = runner.invoke(app, ["instruments", "catalog", "convert", str(V1_PATH), str(out)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["instruments", "catalog", "check", str(out)])
    assert result.exit_code == 0, result.output
    assert "provisional" in result.output
    result = runner.invoke(app, ["instruments", "catalog", "check", str(tmp_path / "none")])
    assert result.exit_code == 1
