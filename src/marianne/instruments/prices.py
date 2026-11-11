"""Price drift between instrument profiles and models.dev (``mzt instruments prices``).

Marianne prices a sheet from the ``models`` rows of the profile it ran on
(``cost_per_1k_input`` / ``_output`` / ``_cache_read`` / ``_cache_write``).
Those rows are hand-maintained and drift: the 2026-11-10 audit found 16 of
87 builtin rows wrong, Gemini under-stated up to 10x and the GPT-5.6 family
on launch prices. This module compares every priced row against models.dev
(``https://models.dev/api.json``: MIT, bot-synced, USD per million tokens,
``cost.{input,output,cache_read,cache_write}``) and can rewrite the four
price fields on the BUILTIN profiles only. User and venue layers belong to
the composer; the command reports their drift and leaves the files alone.

Nothing here runs at conductor time. Prices change in a commit like any
other change to a builtin, after a human reads the drift table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marianne.core.config.instruments import InstrumentProfile, ModelCapacity

MODELS_DEV_URL = "https://models.dev/api.json"
PRICE_FIELDS: tuple[str, ...] = (
    "cost_per_1k_input",
    "cost_per_1k_output",
    "cost_per_1k_cache_read",
    "cost_per_1k_cache_write",
)
_MODELS_DEV_KEYS: dict[str, str] = {
    "cost_per_1k_input": "input",
    "cost_per_1k_output": "output",
    "cost_per_1k_cache_read": "cache_read",
    "cost_per_1k_cache_write": "cache_write",
}

# Profile name -> how its model ids map onto a models.dev provider. A
# profile absent here is reported "unmapped": a subscription route whose
# rows are deliberately $0 (antigravity), a local server (ollama,
# claude-code-ollama), or a harness whose model ids are its own. Mapping
# it would turn a correct $0 into a false drift.
_PROFILE_PROVIDERS: dict[str, tuple[tuple[str, str], ...]] = {
    # (id prefix, models.dev provider); first match wins; "" matches all.
    "claude-code": (("glm-", "zai-coding-plan"), ("", "anthropic")),
    "codex-cli": (("", "openai"),),
    "gemini-cli": (("", "google"),),
    "opencode": (("zai-coding-plan/", "zai-coding-plan"), ("", "openrouter")),
}


def default_cache_path() -> Path:
    """Where the last fetched models.dev snapshot is kept for ``--offline``."""
    return Path.home() / ".marianne" / "cache" / "models.dev.api.json"


@dataclass
class PriceRow:
    """One profile model row compared against models.dev."""

    profile: str
    model: str
    provider: str | None
    status: str  # match | drift | absent | unmapped
    source_path: Path | None
    builtin: bool
    last_updated: str | None = None
    ours: dict[str, float | None] = field(default_factory=dict)
    theirs: dict[str, float | None] = field(default_factory=dict)
    drifted: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "model": self.model,
            "provider": self.provider,
            "status": self.status,
            "builtin": self.builtin,
            "source_path": str(self.source_path) if self.source_path else None,
            "last_updated": self.last_updated,
            "ours": self.ours,
            "models_dev": self.theirs,
            "drifted": list(self.drifted),
        }


def resolve_provider(profile_name: str, model_name: str) -> tuple[str | None, str]:
    """Map a profile's model id onto ``(models.dev provider, models.dev id)``.

    ``[1m]`` is Marianne's long-context suffix, not part of the provider id;
    a ``provider/id`` form keeps only the id. Returns ``(None, id)`` for a
    profile with no mapping.
    """
    lookup = model_name.removesuffix("[1m]")
    rules = _PROFILE_PROVIDERS.get(profile_name.split(" (", 1)[0])
    if rules is None:
        return None, lookup
    for prefix, provider in rules:
        if lookup.startswith(prefix):
            if "/" in lookup and prefix.endswith("/"):
                lookup = lookup[len(prefix):]
            return provider, lookup
    return None, lookup


def _per_1k(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= max(1e-9, abs(b) * 1e-6)


def compare_profiles(
    profiles: dict[str, InstrumentProfile],
    snapshot: dict[str, Any],
    *,
    builtins_dir: Path,
) -> list[PriceRow]:
    """Compare every model row of every profile against the snapshot.

    ``profiles`` may hold builtins and overlays side by side (the caller
    loads each layer on its own, since the merged registry would hide a
    builtin behind a same-named user profile); a row is ``builtin`` when
    its file lives under ``builtins_dir``.
    """
    rows: list[PriceRow] = []
    for name in sorted(profiles):
        profile = profiles[name]
        src = profile._source_path
        builtin = src is not None and src.parent == builtins_dir.resolve()
        for cap in profile.models:
            rows.append(_compare_row(name, cap, snapshot, src, builtin))
    return rows


def load_layers(
    builtins_dir: Path, overlay_dirs: list[Path],
) -> tuple[dict[str, InstrumentProfile], dict[str, InstrumentProfile]]:
    """Load the builtin layer and the overlay layers as two separate maps.

    The registry merges layers by name, so a user ``claude-code.yaml`` would
    make the builtin's rows invisible to a drift report. Overlay keys are
    ``"<name> (<dir>)"`` so two layers that both override a name stay apart.
    """
    from marianne.instruments.loader import InstrumentProfileLoader

    builtins = InstrumentProfileLoader.load_directories([builtins_dir])
    overlays: dict[str, InstrumentProfile] = {}
    for d in overlay_dirs:
        if not d.is_dir():
            continue
        for name, prof in InstrumentProfileLoader.load_directories(
            [d], lower_layers=[builtins_dir, *overlay_dirs[: overlay_dirs.index(d)]],
        ).items():
            overlays[f"{name} ({d})"] = prof
    return builtins, overlays


def _compare_row(
    profile_name: str,
    cap: ModelCapacity,
    snapshot: dict[str, Any],
    src: Path | None,
    builtin: bool,
) -> PriceRow:
    ours: dict[str, float | None] = {f: getattr(cap, f) for f in PRICE_FIELDS}
    provider, lookup = resolve_provider(profile_name, cap.name)
    row = PriceRow(profile_name, cap.name, provider, "unmapped", src, builtin, ours=ours)
    if provider is None:
        return row
    entry = (snapshot.get(provider) or {}).get("models", {}).get(lookup)
    if not isinstance(entry, dict):
        row.status = "absent"
        return row
    cost = entry.get("cost") or {}
    theirs = {f: _per_1k(cost.get(k)) for f, k in _MODELS_DEV_KEYS.items()}
    row.theirs = theirs
    row.last_updated = entry.get("last_updated")
    # A cache field models.dev does not price is not a drift: the estimate
    # falls back to the input rate, which is the conservative contract. For
    # the same reason an UNSET cache field of ours is compared at the rate
    # the estimate would actually charge (the input rate), so a $0 plan row
    # with no cache fields does not "drift" from models.dev's explicit 0.
    def _effective(name: str) -> float | None:
        value = ours[name]
        if value is None and name.startswith("cost_per_1k_cache"):
            return ours["cost_per_1k_input"]
        return value

    drifted = tuple(
        f for f in PRICE_FIELDS
        if theirs[f] is not None and not _same(_effective(f), theirs[f])
    )
    row.drifted = drifted
    row.status = "drift" if drifted else "match"
    return row


def summarize(rows: list[PriceRow]) -> dict[str, int]:
    out = {"match": 0, "drift": 0, "absent": 0, "unmapped": 0}
    for r in rows:
        out[r.status] += 1
    return out


# ---------------------------------------------------------------------------
# Writing builtins
# ---------------------------------------------------------------------------

_NAME_LINE = re.compile(r'^(\s*)-\s*name:\s*"?(?P<name>[^"\n]+?)"?\s*(#.*)?$')


def _fmt(value: float) -> str:
    """YAML-friendly float: no exponent, no trailing zeros, never ``0``-less."""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


def rewrite_builtin_prices(text: str, model: str, prices: dict[str, float | None]) -> str:
    """Rewrite the price fields of one ``- name: <model>`` block in profile YAML text.

    Text-level so the file's comments survive. A field models.dev prices and
    the block lacks is inserted after ``cost_per_1k_output``; a field
    models.dev does not price is left as it is.
    """
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        m = _NAME_LINE.match(line)
        if m and m.group("name") == model:
            start = i
            break
    if start is None:
        raise KeyError(f"model row {model!r} not found")
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped or stripped.startswith("#"):
            continue
        cur = len(lines[j]) - len(lines[j].lstrip())
        if cur <= indent:
            end = j
            break
    block = lines[start:end]
    field_indent = " " * (indent + 2)
    present: dict[str, int] = {}
    for k, line in enumerate(block):
        m2 = re.match(r"^\s*(cost_per_1k_\w+):", line)
        if m2:
            present[m2.group(1)] = k
    for f in PRICE_FIELDS:
        value = prices.get(f)
        if value is None:
            continue
        new_line = f"{field_indent}{f}: {_fmt(value)}"
        if f in present:
            k = present[f]
            comment = ""
            cm = re.search(r"\s+#.*$", block[k])
            if cm:
                comment = cm.group(0)
            block[k] = new_line + comment
        else:
            anchor = present.get("cost_per_1k_output", present.get("cost_per_1k_input"))
            if anchor is None:
                raise KeyError(f"model row {model!r} has no cost_per_1k_output line to anchor on")
            # Keep insertion order: cache_read before cache_write.
            insert_at = anchor + 1
            for prior in PRICE_FIELDS[: PRICE_FIELDS.index(f)]:
                if prior in present and present[prior] > anchor:
                    insert_at = max(insert_at, present[prior] + 1)
            block.insert(insert_at, new_line)
            present = {
                name: (idx + 1 if idx >= insert_at else idx) for name, idx in present.items()
            }
            present[f] = insert_at
    return "\n".join(lines[:start] + block + lines[end:])


def apply_drift(
    rows: list[PriceRow], *, builtins_dir: Path,
) -> tuple[list[PriceRow], list[PriceRow]]:
    """Write drifted builtin rows back to their YAML files.

    Returns ``(written, refused)``: refused rows are drifted rows whose file
    is not under the builtins directory (the composer's layer).
    """
    written: list[PriceRow] = []
    refused: list[PriceRow] = []
    by_file: dict[Path, list[PriceRow]] = {}
    for r in rows:
        if r.status != "drift":
            continue
        if not r.builtin or r.source_path is None or r.source_path.parent != builtins_dir.resolve():
            refused.append(r)
            continue
        by_file.setdefault(r.source_path, []).append(r)
    for path, file_rows in by_file.items():
        text = path.read_text(encoding="utf-8")
        for r in file_rows:
            text = rewrite_builtin_prices(text, r.model, r.theirs)
            written.append(r)
        path.write_text(text, encoding="utf-8")
    return written, refused


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def load_snapshot_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path}: not a models.dev api.json object")
    return data


def fetch_snapshot(*, timeout: float = 20.0) -> dict[str, Any]:
    """GET models.dev api.json. Raises on any transport or shape failure."""
    import httpx

    resp = httpx.get(MODELS_DEV_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data:
        raise ValueError("models.dev returned no providers")
    return data


def save_snapshot(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)
