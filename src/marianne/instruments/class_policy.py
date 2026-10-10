"""Machine probe and ordered policy for a generated class map."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.availability import check_instrument_available
from marianne.instruments.registry import InstrumentRegistry


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


def _ollama_responds(profile: InstrumentProfile) -> bool:
    if profile.http is None:
        return False
    parsed = urlparse(profile.http.base_url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        with urllib.request.urlopen(
            f"{parsed.scheme}://{parsed.netloc}/api/tags", timeout=2
        ) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def probe_builtin_profiles(profiles: dict[str, InstrumentProfile]) -> dict[str, object]:
    """Probe only shipped profiles; this is called by explicit setup, not show."""
    registry = InstrumentRegistry()
    for profile in profiles.values():
        registry.register(profile)
    record: dict[str, object] = {}
    for name, profile in sorted(profiles.items()):
        available, _ = check_instrument_available(name, registry)
        if profile.kind == "http":
            available = _ollama_responds(profile)
        record[name] = {
            "kind": profile.kind,
            "available": available,
            "execution_status": profile.execution_status,
            "capabilities": sorted(profile.capabilities),
            "zero_cost": bool(profile.models)
            and all(
                model.cost_per_1k_input == 0 and model.cost_per_1k_output == 0
                for model in profile.models
            ),
            "raw_prompt": profile.raw_prompt,
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


def canonical_probe(probe: dict[str, object]) -> bytes:
    return json.dumps(probe, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
