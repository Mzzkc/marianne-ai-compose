"""Family-neutral tier classes ``frontier``, ``balanced`` and ``light`` from catalog v2 (MC1c).

The existing capability classes name a job (``review``) or a rough strength
(``strong``). A tier class names a capability tier, the way "Opus class,
Sonnet class, Haiku class" does, with no family in the name: any family's model
may sit in it, at the reasoning effort that makes it that tier.

``tier_chains`` is a pure function from a validated catalog and the profiles a
machine has to the three chains. It is run twice with different inputs:

* against the BUILTIN profiles, to generate the packaged ``classes/default.yaml``
  block (``python -m marianne.instruments.tier_classes --write-default``);
* against the registered profiles of one machine, by
  ``mzt instruments classes tiers`` (printed, or written to a proposal file the
  owner merges by hand; the user layer is never written).

Rules (each has a test):

* only non-provisional tier claims count (a guess is never a class member);
* only routes whose access is in ``allow_access`` (default: no ``metered``);
* models whose status is superseded, deprecated, retired or sunset are skipped;
* each entry pins the LOWEST effort at which the model reaches the tier, the
  cheapest way to get that class, and only an effort the profile maps for that
  model; a route whose profile maps none of the qualifying efforts is skipped;
* where the effort is part of the model slug (antigravity: ``...-high``) the
  entry names the full slug and carries no ``effort``;
* entries alternate families (round robin) so a fallback leaves the family
  that just failed. Order before alternation: subscription routes, then free,
  then local (a flat-rate plan is the dependable seat; a free tier is shared
  quota); a model in its own tier (the tier it reaches at its default effort)
  before one borrowed by raising or lowering its effort; then the cheaper
  effort first.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from marianne.core.config.catalog import Catalog, CatalogModel, Route, Tier, load_catalog
from marianne.core.config.classes import ClassEntry
from marianne.core.config.instrument_settings import EFFORT_LEVELS, EffortLevel
from marianne.core.config.instruments import InstrumentProfile

TIER_CLASSES: tuple[Tier, ...] = ("frontier", "balanced", "light")
DEFAULT_ALLOW_ACCESS = frozenset({"subscription", "free", "local"})
_ACCESS_ORDER = ("subscription", "free", "local", "metered")
SKIPPED_STATUSES = frozenset({"superseded", "deprecated", "retired", "sunset"})

BEGIN_MARKER = (
    "# --- BEGIN generated tier classes (python -m marianne.instruments.tier_classes) ---"
)
END_MARKER = "# --- END generated tier classes ---"


@dataclass(frozen=True)
class TierEntry:
    """One chain step plus the catalog facts that placed it."""

    profile: str
    model: str
    effort: EffortLevel | None
    family: str
    catalog_id: str
    access: str

    def as_class_entry(self) -> ClassEntry:
        config: dict[str, str] = {"model": self.model}
        if self.effort is not None:
            config["effort"] = self.effort
        return ClassEntry.model_validate({"profile": self.profile, "config": config})

    def as_dict(self) -> dict[str, object]:
        config: dict[str, str] = {"model": self.model}
        if self.effort is not None:
            config["effort"] = self.effort
        return {"profile": self.profile, "config": config}


def _slug_effort(model: CatalogModel, route: Route) -> EffortLevel | None:
    """The effort a route's model slug carries (agy ``claude-opus-5-5-high``), else None."""
    for level in EFFORT_LEVELS:
        if route.model_id == f"{model.id}-{level}":
            return level
    return None


def _home_tier(model: CatalogModel) -> Tier | None:
    """The tier the model reaches at its default effort (its own class)."""
    tiers = model.tier_by_effort
    if model.reasoning.default is not None and model.reasoning.default in tiers:
        return tiers[model.reasoning.default]
    return tiers.get("default")


def _lowest_effort_entry(
    model: CatalogModel,
    route: Route,
    profile: InstrumentProfile,
    tier: Tier,
    slug_routes: Mapping[EffortLevel, Route],
) -> tuple[EffortLevel | None, str] | None:
    """(effort to pin, model name to pin) for the cheapest way ``route`` reaches ``tier``."""
    tiers = model.tier_by_effort
    if slug_routes:
        # Effort is part of the slug: the lowest slug that reaches the tier, no effort key.
        for level in EFFORT_LEVELS:
            if level in slug_routes and tiers.get(level) == tier:
                return None, slug_routes[level].model_id
        return None
    mapped = profile.effort_levels_for(route.model_id)
    for level in EFFORT_LEVELS:
        if tiers.get(level) == tier and level in mapped:
            # `minimal` is "the least this seat offers"; where the seat maps it to the
            # same native word as `low` (claude-code, codex-cli, opencode), `low` is the
            # same run under its plain name, so the class says `low`.
            if (
                level == "minimal"
                and mapped.get("low") == mapped[level]
                and tiers.get("low") == tier
            ):
                continue
            return level, route.model_id
    if tiers.get("default") == tier:
        return None, route.model_id
    return None


def _candidates(
    model: CatalogModel,
    tier: Tier,
    profiles: Mapping[str, InstrumentProfile],
    allow_access: Collection[str],
) -> list[TierEntry]:
    by_profile: dict[str, list[Route]] = {}
    for route in model.routes:
        if route.kind != "profile" or route.access not in allow_access:
            continue
        if route.profile not in profiles:
            continue
        by_profile.setdefault(route.profile, []).append(route)
    entries: list[TierEntry] = []
    for profile_name, routes in by_profile.items():
        profile = profiles[profile_name]
        slugged = {
            level: route for route in routes if (level := _slug_effort(model, route)) is not None
        }
        plain = [route for route in routes if _slug_effort(model, route) is None]
        picks: list[tuple[Route, Mapping[EffortLevel, Route]]] = []
        if slugged:
            picks.append((next(iter(slugged.values())), slugged))
        picks.extend((route, {}) for route in plain)
        for route, slug_routes in picks:
            found = _lowest_effort_entry(model, route, profile, tier, slug_routes)
            if found is None:
                continue
            effort, name = found
            access = (slug_routes[next(iter(slug_routes))] if slug_routes else route).access
            entries.append(
                TierEntry(profile_name, name, effort, model.family, model.id, access)
            )
    return entries


def _effort_rank(effort: EffortLevel | None, model: str, model_obj: CatalogModel) -> int:
    """Cost order of an entry: a slug's own effort counts like a pinned one."""
    if effort is not None:
        return EFFORT_LEVELS.index(effort)
    for level in EFFORT_LEVELS:
        if model == f"{model_obj.id}-{level}":
            return EFFORT_LEVELS.index(level)
    return -1  # no effort control: the cheapest there is


def tier_chains(
    catalog: Catalog,
    *,
    profiles_available: Mapping[str, InstrumentProfile] | Iterable[InstrumentProfile],
    allow_access: Collection[str] = DEFAULT_ALLOW_ACCESS,
) -> dict[Tier, list[TierEntry]]:
    """The ``frontier``/``balanced``/``light`` chains the catalog yields for these profiles.

    ``profiles_available`` is the profile set to seat entries on (builtins for the
    packaged default, the registered profiles for a machine). A tier with no
    qualifying entry maps to an empty list; the caller decides what to do with it.
    """
    profiles: Mapping[str, InstrumentProfile] = (
        profiles_available
        if isinstance(profiles_available, Mapping)
        else {p.name: p for p in profiles_available}
    )
    family_order = {slug: i for i, slug in enumerate(catalog.files)}
    result: dict[Tier, list[TierEntry]] = {}
    for tier in TIER_CLASSES:
        ranked: list[tuple[tuple[int, int, int, int, int], TierEntry]] = []
        for position, model in enumerate(catalog.models()):
            if model.status in SKIPPED_STATUSES or model.tier_status.provisional:
                continue
            if tier not in model.tier_by_effort.values():
                continue
            borrowed = 0 if _home_tier(model) == tier else 1
            for entry in _candidates(model, tier, profiles, allow_access):
                key = (
                    _ACCESS_ORDER.index(entry.access) if entry.access in _ACCESS_ORDER else 9,
                    borrowed,
                    _effort_rank(entry.effort, entry.model, model),
                    family_order.get(model.family, len(family_order)),
                    position,
                )
                ranked.append((key, entry))
        ranked.sort(key=lambda pair: pair[0])
        result[tier] = _alternate_families([entry for _, entry in ranked])
    return result


def _alternate_families(ordered: list[TierEntry]) -> list[TierEntry]:
    """Round robin over families, families in order of their best entry."""
    queues: dict[str, list[TierEntry]] = {}
    for entry in ordered:
        queues.setdefault(entry.family, []).append(entry)
    out: list[TierEntry] = []
    while any(queues.values()):
        for family in list(queues):
            if queues[family]:
                out.append(queues[family].pop(0))
    return out


def chains_to_classes(chains: Mapping[Tier, list[TierEntry]]) -> dict[str, list[dict[str, object]]]:
    """Class name -> chain as plain class-file data; empty tiers are omitted."""
    return {tier: [e.as_dict() for e in chains[tier]] for tier in TIER_CLASSES if chains.get(tier)}


def render_classes_block(chains: Mapping[Tier, list[TierEntry]], *, indent: int = 2) -> str:
    """YAML text of the tier classes (flow-style entries, one per line, with a why comment)."""
    pad = " " * indent
    lines: list[str] = []
    for tier in TIER_CLASSES:
        chain = chains.get(tier) or []
        if not chain:
            continue
        lines.append(f"{pad}{tier}:")
        for entry in chain:
            config = f"model: {entry.model}"
            if entry.effort is not None:
                config += f", effort: {entry.effort}"
            lines.append(
                f"{pad}  - {{profile: {entry.profile}, config: {{{config}}}}}"
                f"  # {entry.family}, {entry.access}"
            )
    return "\n".join(lines) + "\n"


def render_proposal(
    chains: Mapping[Tier, list[TierEntry]], *, machine_note: str,
) -> str:
    """A classes-file fragment the owner may merge by hand into ``~/.marianne/classes.yaml``."""
    header = (
        "# Proposal from `mzt instruments classes tiers --write-proposal`.\n"
        "# NOT installed: Marianne never writes your classes file. To use it, copy the\n"
        "# `frontier`, `balanced` and `light` chains you want under `classes:` in\n"
        "# ~/.marianne/classes.yaml (a class defined there replaces the packaged chain\n"
        "# whole), or leave them out and the packaged chains stand.\n"
        f"# {machine_note}\n"
    )
    return header + "version: 1\nclasses:\n" + render_classes_block(chains)


def builtin_profiles() -> dict[str, InstrumentProfile]:
    from marianne.instruments.loader import InstrumentProfileLoader, profile_source_dirs

    return InstrumentProfileLoader.load_directories([profile_source_dirs()[0]])


def splice_default_yaml(text: str, block: str, header: str) -> str:
    """Replace (or append) the generated section of ``default.yaml``; the rest is untouched."""
    section = f"{BEGIN_MARKER}\n{header}{block}{END_MARKER}\n"
    if BEGIN_MARKER in text:
        before, rest = text.split(BEGIN_MARKER, 1)
        _, after = rest.split(END_MARKER, 1)
        return before + section + after.lstrip("\n")
    return text.rstrip("\n") + "\n" + section


def _git_commit(path: Path) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(path), "rev-parse", "--short=8", "HEAD"],  # noqa: S607
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the packaged tier classes.")
    parser.add_argument("--catalog", type=Path, required=True, help="catalog v2 directory")
    parser.add_argument("--write-default", action="store_true",
                        help="splice the block into classes/default.yaml (else print it)")
    args = parser.parse_args(argv)
    catalog = load_catalog(args.catalog)
    chains = tier_chains(catalog, profiles_available=builtin_profiles())
    commit = _git_commit(args.catalog)
    header = (
        "# Generated by marianne.instruments.tier_classes.tier_chains from catalog v2\n"
        f"# ({args.catalog.as_posix().split('/plugins/')[-1]}, plugins commit {commit}, "
        f"catalog generated_at {catalog.index.generated_at}) against the builtin profiles.\n"
        "# Do not edit by hand: change the catalog and regenerate. Rules: non-provisional\n"
        "# tiers only; no metered routes; superseded/deprecated models skipped; each entry\n"
        "# pins the lowest effort that reaches the tier (agy: the effort is in the slug);\n"
        "# families alternate so a fallback leaves the family that failed.\n"
    )
    block = render_classes_block(chains)
    if not args.write_default:
        sys.stdout.write(header + block)
        return 0
    target = Path(__file__).parent / "classes" / "default.yaml"
    target.write_text(
        splice_default_yaml(target.read_text(encoding="utf-8"), block, header), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
