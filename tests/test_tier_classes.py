"""MC1c: tier classes frontier/balanced/light generated from catalog v2.

The generator is pure, so its rules are tested on small synthetic catalogs.
The packaged default and the CLI are tested against the real files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config.catalog import (
    Catalog,
    CatalogFile,
    CatalogIndex,
    CatalogModel,
    load_catalog,
)
from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    EffortMapping,
    InstrumentProfile,
)
from marianne.instruments.classes import load_class_map, parse_classes_file
from marianne.instruments.tier_classes import (
    TIER_CLASSES,
    splice_default_yaml,
    tier_chains,
)

REPO = Path(__file__).parent.parent
CATALOG_DIR = REPO / "plugins" / "marianne" / "docs" / "ref" / "catalog"
DEFAULT_YAML = REPO / "src" / "marianne" / "instruments" / "classes" / "default.yaml"
needs_catalog = pytest.mark.skipif(
    not (CATALOG_DIR / "index.yaml").is_file(),
    reason="catalog v2 is an optional plugin submodule asset",
)

ALL_LEVELS = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
              "xhigh": "xhigh", "max": "max"}


def _profile(name: str, levels: dict[str, str] | None = None) -> InstrumentProfile:
    effort = (
        EffortMapping(delivery="flag", flag="--effort", levels=levels)  # type: ignore[arg-type]
        if levels is not None
        else None
    )
    return InstrumentProfile(
        name=name,
        display_name=name,
        kind="cli",
        capabilities={"tool_use"},
        cli=CliProfile(
            command=CliCommand(executable="bash", prompt_flag=None),
            output=CliOutputConfig(format="text"),
        ),
        effort=effort,
    )


def _model(model_id: str, family: str, tiers: dict[str, str], **kw: Any) -> CatalogModel:
    routes = kw.pop("routes", [{"profile": "p", "model_id": model_id, "access": "subscription"}])
    tier_status = kw.pop("tier_status", {"provisional": False})
    status = kw.pop("status", "current")
    return CatalogModel.model_validate({
        "id": model_id, "family": family, "provider": family, "status": status,
        "routes": routes, "tier_by_effort": tiers, "tier_status": tier_status,
        "reasoning": {"modes": [k for k in tiers if k != "default"]}, **kw,
    })


def _catalog(*models: CatalogModel) -> Catalog:
    families: dict[str, list[CatalogModel]] = {}
    for m in models:
        families.setdefault(m.family, []).append(m)
    files = {
        fam: CatalogFile(schema_version=2, generated_at="2026-11-16", families={fam: ms})
        for fam, ms in families.items()
    }
    index = CatalogIndex.model_validate({
        "schema_version": 2, "generated_at": "2026-11-16", "model_count": len(models),
        "families": {f: {"file": f"{f}.yaml", "model_ids": [m.id for m in ms]}
                     for f, ms in families.items()},
    })
    return Catalog(index=index, files=files)


PROFILES = {"p": _profile("p", ALL_LEVELS)}


def _ids(chain: list[Any]) -> list[tuple[str, str | None]]:
    return [(e.model, e.effort) for e in chain]


# -- generator rules --------------------------------------------------------


def test_metered_routes_are_never_seated() -> None:
    cat = _catalog(_model("m", "a", {"high": "frontier"}, routes=[
        {"profile": "p", "model_id": "m", "access": "metered"}]))
    assert tier_chains(cat, profiles_available=PROFILES)["frontier"] == []
    # An explicit opt-in is the only way in.
    allowed = tier_chains(cat, profiles_available=PROFILES,
                          allow_access={"subscription", "free", "local", "metered"})
    assert _ids(allowed["frontier"]) == [("m", "high")]


def test_service_routes_are_not_profile_entries() -> None:
    cat = _catalog(_model("m", "a", {"high": "frontier"}, routes=[
        {"profile": "p", "model_id": "m", "access": "subscription", "kind": "service"}]))
    assert tier_chains(cat, profiles_available=PROFILES)["frontier"] == []


def test_provisional_tiers_are_ignored() -> None:
    cat = _catalog(
        _model("guess", "a", {"high": "frontier"},
               tier_status={"provisional": True, "reason": "carried from v1"}),
        _model("known", "b", {"high": "frontier"}),
    )
    assert _ids(tier_chains(cat, profiles_available=PROFILES)["frontier"]) == [("known", "high")]


@pytest.mark.parametrize("status", ["superseded", "deprecated", "retired", "sunset"])
def test_dead_models_are_skipped(status: str) -> None:
    cat = _catalog(_model("old", "a", {"high": "frontier"}, status=status))
    assert tier_chains(cat, profiles_available=PROFILES)["frontier"] == []


def test_lowest_effort_that_reaches_the_tier_is_pinned() -> None:
    cat = _catalog(_model("m", "a", {
        "low": "light", "medium": "balanced", "high": "balanced", "xhigh": "frontier",
        "max": "frontier"}))
    chains = tier_chains(cat, profiles_available=PROFILES)
    assert _ids(chains["light"]) == [("m", "low")]
    assert _ids(chains["balanced"]) == [("m", "medium")]
    assert _ids(chains["frontier"]) == [("m", "xhigh")]


def test_minimal_is_named_low_when_the_seat_maps_both_to_one_word() -> None:
    cat = _catalog(_model("m", "a", {"minimal": "light", "low": "light", "high": "balanced"}))
    assert _ids(tier_chains(cat, profiles_available=PROFILES)["light"]) == [("m", "low")]


def test_unmapped_effort_is_refused_not_guessed() -> None:
    # The profile maps only high: the model reaches balanced at medium, which the seat
    # cannot apply (V335), so no balanced entry is emitted; frontier at high is.
    profiles = {"p": _profile("p", {"high": "high"})}
    cat = _catalog(_model("m", "a", {"medium": "balanced", "high": "frontier"}))
    chains = tier_chains(cat, profiles_available=profiles)
    assert chains["balanced"] == []
    assert _ids(chains["frontier"]) == [("m", "high")]


def test_profile_with_no_effort_mapping_only_seats_effortless_models() -> None:
    profiles = {"p": _profile("p", None)}
    cat = _catalog(
        _model("reasoner", "a", {"high": "frontier"}),
        _model("plain", "b", {"default": "frontier"}),
    )
    assert _ids(tier_chains(cat, profiles_available=profiles)["frontier"]) == [("plain", None)]


def test_slug_effort_names_the_full_slug_and_omits_effort() -> None:
    routes = [{"profile": "p", "model_id": f"m-{lv}", "access": "subscription"}
              for lv in ("low", "medium", "high")]
    cat = _catalog(_model("m", "a", {"low": "light", "medium": "balanced", "high": "frontier"},
                          routes=routes))
    chains = tier_chains(cat, profiles_available=PROFILES)
    assert _ids(chains["light"]) == [("m-low", None)]
    assert _ids(chains["balanced"]) == [("m-medium", None)]
    assert _ids(chains["frontier"]) == [("m-high", None)]
    assert all(e.as_dict()["config"] == {"model": e.model} for e in chains["frontier"])


def test_slug_route_missing_for_the_tiers_effort_is_not_invented() -> None:
    routes = [{"profile": "p", "model_id": "m-low", "access": "subscription"}]
    cat = _catalog(_model("m", "a", {"low": "light", "high": "frontier"}, routes=routes))
    assert tier_chains(cat, profiles_available=PROFILES)["frontier"] == []


def test_families_alternate_where_possible() -> None:
    cat = _catalog(
        _model("a1", "a", {"high": "frontier"}),
        _model("a2", "a", {"high": "frontier"}),
        _model("b1", "b", {"high": "frontier"}),
        _model("c1", "c", {"high": "frontier"}),
    )
    chain = tier_chains(cat, profiles_available=PROFILES)["frontier"]
    assert [e.family for e in chain] == ["a", "b", "c", "a"]
    # Consecutive entries differ in family while another family remains.
    assert all(x.family != y.family for x, y in zip(chain[:3], chain[1:4], strict=False))


def test_free_and_local_seat_after_subscription() -> None:
    cat = _catalog(
        _model("free-m", "a", {"default": "light"},
               routes=[{"profile": "p", "model_id": "free-m", "access": "free"}]),
        _model("sub-m", "b", {"default": "light"}),
    )
    chain = tier_chains(cat, profiles_available=PROFILES)["light"]
    assert [e.catalog_id for e in chain] == ["sub-m", "free-m"]


def test_routes_to_profiles_this_machine_lacks_are_dropped() -> None:
    cat = _catalog(_model("m", "a", {"high": "frontier"}))
    assert tier_chains(cat, profiles_available={})["frontier"] == []


# -- the packaged default ---------------------------------------------------


def test_packaged_default_parses_and_defines_the_tier_classes() -> None:
    parsed = parse_classes_file(DEFAULT_YAML.read_text(encoding="utf-8"))
    for tier in TIER_CLASSES:
        chain = parsed.classes[tier]
        assert chain, tier
        for entry in chain:
            assert entry.config.model, (tier, entry)
    # Nothing existing was removed or reordered.
    assert parsed.classes["strong"] == parse_classes_file(
        "version: 1\nclasses:\n  strong: [claude-code, codex-cli, antigravity, opencode]\n"
    ).classes["strong"]
    assert list(parsed.classes)[:11] == [
        "strong", "workhorse", "fast", "cheap", "writing", "review", "vision", "local",
        "high-context", "image", "video",
    ]


def test_packaged_default_has_no_metered_or_unmapped_entry() -> None:
    from marianne.instruments.tier_classes import builtin_profiles

    profiles = builtin_profiles()
    parsed = parse_classes_file(DEFAULT_YAML.read_text(encoding="utf-8"))
    for tier in TIER_CLASSES:
        for entry in parsed.classes[tier]:
            profile = profiles[entry.profile]
            if entry.config.effort is not None:
                assert profile.effort_refusal(
                    entry.config.effort, model=entry.config.model, schema_mode=False
                ) is None, (tier, entry)


@needs_catalog
def test_packaged_default_is_what_the_generator_yields() -> None:
    """Drift guard: edit the catalog, regenerate (python -m marianne.instruments.tier_classes)."""
    from marianne.instruments.tier_classes import builtin_profiles, render_classes_block

    chains = tier_chains(load_catalog(CATALOG_DIR), profiles_available=builtin_profiles())
    expected = render_classes_block(chains)
    text = DEFAULT_YAML.read_text(encoding="utf-8")
    begin = text.index("# --- BEGIN generated tier classes")
    end = text.index("# --- END generated tier classes")
    block = "\n".join(
        line for line in text[begin:end].splitlines() if not line.startswith("#")
    ) + "\n"
    assert block == expected


def test_splice_replaces_only_the_generated_section() -> None:
    base = "version: 1\nclasses:\n  strong: [x]\n"
    once = splice_default_yaml(base, "  light:\n    - y\n", "# h\n")
    twice = splice_default_yaml(once, "  light:\n    - z\n", "# h\n")
    assert twice.startswith(base)
    assert "- y" not in twice and "- z" in twice
    assert twice.count("BEGIN generated") == 1


@pytest.mark.parametrize("tier", ["frontier", "balanced", "light"])
def test_validate_resolves_a_tier_class_on_a_fixture_score(
    tier: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marianne.instruments import loader

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    score = tmp_path / "score.yaml"
    score.write_text(yaml.safe_dump({
        "name": "tier-fixture",
        "workspace": str(tmp_path / "ws"),
        "instrument": tier,
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": "Say hi."},
    }, sort_keys=False))
    # The packaged chains seat builtin profiles; `which` must find their binaries.
    monkeypatch.setattr("shutil.which", lambda name: f"/test/bin/{name}")
    assert loader.profile_source_dirs()[0].is_dir()
    result = CliRunner().invoke(app, ["validate", str(score), "--json"])
    data = json.loads(result.stdout)
    codes = {i["check_id"] for i in data["issues"]}
    # V210/V310/V321: the class name did not resolve; V335: a chain entry's effort is refused.
    assert not codes & {"V210", "V310", "V321", "V335"}, data["issues"]
    summary = [i for i in data["issues"] if i["check_id"] == "V325"]
    assert summary and f"{tier} \u2192" in summary[0]["message"], data["issues"]


# -- CLI --------------------------------------------------------------------


@needs_catalog
def test_cli_prints_the_three_chains_and_never_touches_the_user_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    (home / ".marianne").mkdir(parents=True)
    user_layer = home / ".marianne" / "classes.yaml"
    sentinel = "version: 1\nclasses:\n  strong: [codex-cli]\n"
    user_layer.write_text(sentinel)
    before = user_layer.stat().st_mtime_ns
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: f"/test/bin/{name}")
    proposal = tmp_path / "proposal.yaml"

    result = CliRunner().invoke(
        app, ["instruments", "classes", "tiers", "--catalog", str(CATALOG_DIR),
              "--write-proposal", str(proposal)],
    )
    assert result.exit_code == 0, result.output
    for tier in TIER_CLASSES:
        assert f"{tier}:" in result.output
    assert user_layer.read_text() == sentinel
    assert user_layer.stat().st_mtime_ns == before
    assert not (home / ".marianne" / "backups").exists()
    fragment = parse_classes_file(proposal.read_text())
    assert set(fragment.classes) == set(TIER_CLASSES)
    assert "NOT installed" in proposal.read_text()


@needs_catalog
def test_cli_without_a_proposal_writes_nothing_under_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app, ["instruments", "classes", "tiers", "--catalog", str(CATALOG_DIR), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert set(json.loads(result.stdout)) == set(TIER_CLASSES)
    assert not (home / ".marianne" / "classes.yaml").exists()


def test_cli_refuses_to_write_the_proposal_onto_a_class_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    (home / ".marianne").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    target = home / ".marianne" / "classes.yaml"
    result = CliRunner().invoke(
        app, ["instruments", "classes", "tiers", "--catalog", str(CATALOG_DIR),
              "--write-proposal", str(target), "--force"],
    )
    assert result.exit_code == 1
    assert not target.exists()


def test_cli_refuses_to_overwrite_an_existing_proposal_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "p.yaml"
    target.write_text("keep")
    result = CliRunner().invoke(
        app, ["instruments", "classes", "tiers", "--catalog", str(CATALOG_DIR),
              "--write-proposal", str(target)],
    )
    assert result.exit_code == 1
    assert target.read_text() == "keep"


def test_load_class_map_exposes_the_tier_classes(tmp_path: Path) -> None:
    class_map = load_class_map(
        user_path=tmp_path / "none.yaml", venue_path=tmp_path / "none2.yaml",
    )
    assert {"frontier", "balanced", "light"} <= set(class_map.classes)
    assert all(e.source_layer == "default" for e in class_map.classes.values())
