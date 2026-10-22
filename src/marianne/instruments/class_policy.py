"""Machine probe and ordered policy for a generated class map."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.availability import probe_profile


@dataclass(frozen=True)
class ClassPolicy:
    order: tuple[str, ...]
    requires: frozenset[str] = frozenset()
    either: frozenset[str] = frozenset()
    zero_cost: bool = False
    allow_raw: bool = False


CLASS_POLICIES: dict[str, ClassPolicy] = {
    "strong": ClassPolicy(
        ("claude-code", "codex-cli", "antigravity", "opencode", "goose", "crush"),
        either=frozenset({"thinking", "tool_use"}),
    ),
    "workhorse": ClassPolicy(
        ("codex-cli", "claude-code", "opencode", "antigravity", "goose", "crush"),
        requires=frozenset({"tool_use", "file_editing"}),
    ),
    "fast": ClassPolicy(
        ("antigravity", "opencode", "crush", "codex-cli", "claude-code"), allow_raw=True
    ),
    "cheap": ClassPolicy(
        ("ollama", "crush", "opencode", "antigravity"), zero_cost=True, allow_raw=True
    ),
    "writing": ClassPolicy(("claude-code", "codex-cli", "antigravity", "opencode")),
    "review": ClassPolicy(
        ("codex-cli", "antigravity", "claude-code", "opencode"),
        either=frozenset({"thinking", "tool_use"}),
    ),
    "vision": ClassPolicy(("claude-code", "codex-cli"), requires=frozenset({"vision"})),
    "local": ClassPolicy(("ollama",)),
}


_HINT_RE = re.compile(r"^(first|last|(?:after|before)\s+(\S+))$")


def parse_class_hint(value: str) -> tuple[str, str | None]:
    """``'after opencode'`` → ``('after', 'opencode')``; ``'first'`` → ``('first', None)``.

    Raises ValueError on any other shape — a profile hint is a seating
    instruction, not free text.
    """
    match = _HINT_RE.match(value.strip())
    if match is None:
        raise ValueError(
            f"class hint {value!r} is not one of: first, last, after <profile>, before <profile>"
        )
    if match.group(2):
        return match.group(1).split()[0], match.group(2)
    return match.group(1), None


def probe_builtin_profiles(
    profiles: dict[str, InstrumentProfile],
    hinted: dict[str, InstrumentProfile] | None = None,
) -> dict[str, object]:
    """Probe shipped profiles — and user/venue profiles that carry ``class_hints``.

    Called by explicit setup, not show. A user profile with no hint is never
    seated by the writer (GH #469: ranking a user profile against the shipped
    order is a decision the profile's author makes, not the writer).
    """
    record: dict[str, object] = {}
    hinted = {
        name: profile for name, profile in (hinted or {}).items()
        if profile.class_hints and name not in profiles
    }
    for name, profile in sorted({**profiles, **hinted}.items()):
        # The same probe `classes check` applies later (GH #447): a chain the
        # writer emits is one the checker accepts on the same machine. A local
        # server that answers but does not serve the profile's model is NOT
        # available — the old /api/tags-200 probe wrote `local: [ollama]`
        # that `classes check` then refused.
        record[name] = {
            "kind": profile.kind,
            "available": probe_profile(profile).runnable,
            "execution_status": profile.execution_status,
            "capabilities": sorted(profile.capabilities),
            "zero_cost": bool(profile.models)
            and all(
                model.cost_per_1k_input == 0 and model.cost_per_1k_output == 0
                for model in profile.models
            ),
            "raw_prompt": profile.raw_prompt,
            "class_hints": {
                cls: parse_class_hint(hint) for cls, hint in sorted(profile.class_hints.items())
            } if name in hinted else {},
        }
    return {
        "profiles": record,
        "openrouter_api_key": bool(os.getenv("OPENROUTER_API_KEY")),
        "zai_api_key": bool(os.getenv("ZAI_API_KEY")),
    }


def select_class_chains(probe: dict[str, object]) -> dict[str, list[str]]:
    """Apply the policy table, preserving priority and a four-entry cap."""
    profiles = probe["profiles"]
    assert isinstance(profiles, dict)
    selected: dict[str, list[str]] = {}
    for class_name, policy in CLASS_POLICIES.items():
        order = list(policy.order)
        if not probe["openrouter_api_key"] and "crush" in order:
            order.remove("crush")
            order.append("crush")
        order = _seat_hinted(order, class_name, profiles)
        chain: list[str] = []
        for name in order:
            row = profiles.get(name)
            if not isinstance(row, dict):
                continue
            caps = set(row["capabilities"])
            if not row["available"] or row["execution_status"] != "ready":
                continue
            if row["raw_prompt"] and not policy.allow_raw:
                continue
            if policy.requires - caps or (policy.either and not policy.either & caps):
                continue
            if policy.zero_cost and not row["zero_cost"]:
                continue
            chain.append(name)
            if len(chain) == 4:
                break
        if chain:
            selected[class_name] = chain
    return selected


def _seat_hinted(order: list[str], class_name: str, profiles: dict[str, Any]) -> list[str]:
    """Splice every profile hinting this class into the policy order.

    Hints apply in profile-name order so the result is deterministic. A
    hint naming a profile absent from the chain seats the hinted one last.
    """
    seated = list(order)
    for name in sorted(profiles):
        row = profiles[name]
        hints = row.get("class_hints") if isinstance(row, dict) else None
        if not hints or class_name not in hints:
            continue
        where, anchor = hints[class_name]
        if name in seated:
            seated.remove(name)
        if where == "first":
            seated.insert(0, name)
        elif where == "last" or anchor not in seated:
            seated.append(name)
        else:
            idx = seated.index(anchor)
            seated.insert(idx + 1 if where == "after" else idx, name)
    return seated


def canonical_probe(probe: dict[str, object]) -> bytes:
    return json.dumps(probe, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
