"""Input digest computation and difference detection for conditional skip (GH #570).

Pure module: digests prompt and dispatch components, checks declared product
hashes, and reports diffs for diagnosable miss logs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

import marianne
from marianne.core.prompt_receipt import PromptReceipt

if TYPE_CHECKING:
    from marianne.core.sheet import Sheet
    from marianne.daemon.baton.prompt import RenderedPrompt

REASON_PREFIX = "unchanged since it last passed"
INPUT_COMPONENTS: tuple[str, ...] = (
    "prompt",
    "preamble",
    "prompt_extensions",
    "instrument",
    "model",
    "instrument_config",
    "validations",
    "stdout_to",
    "profile",
    "marianne_version",
)


def canonical_json(value: object) -> str:
    """Produce deterministic JSON for a value or BaseModel."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]
    elif isinstance(value, dict):
        value = {
            k: v.model_dump(mode="json") if isinstance(v, BaseModel) else v
            for k, v in value.items()
        }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    """sha256:<hex> of a UTF-8 string."""
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def sha256_file(path: Path) -> str:
    """sha256:<hex> of file contents."""
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def dispatch_digest(
    *,
    rendered: RenderedPrompt,
    sheet: Sheet,
    instrument: str,
    model: str | None,
    profile: object,
    stdout_to: str | None = None,
    **kwargs: Any,
) -> dict[str, str]:
    """Compute the dispatch digest mapping each component in INPUT_COMPONENTS to its value/hash."""
    profile_digest: str
    if profile is None:
        profile_digest = "none"
    elif hasattr(profile, "model_dump_json"):
        try:
            dump = profile.model_dump_json()
            if isinstance(dump, str):
                profile_digest = sha256_text(dump)
            else:
                profile_digest = "unreadable"
        except Exception:  # noqa: BLE001 -- fallback to unreadable on profile dump failure
            profile_digest = "unreadable"
    elif hasattr(profile, "model_dump"):
        try:
            profile_digest = sha256_text(canonical_json(profile))
        except Exception:  # noqa: BLE001 -- fallback to unreadable on canonical_json failure
            profile_digest = "unreadable"
    else:
        profile_digest = "unreadable"

    if stdout_to is not None:
        effective_stdout = stdout_to
    elif sheet.stdout_to is not None:
        effective_stdout = sheet.stdout_to
    else:
        effective_stdout = "none"

    prompt_exts: list[str] = list(rendered.prompt_extensions or ())
    return {
        "prompt": sha256_text(rendered.prompt),
        "preamble": sha256_text(rendered.preamble),
        "prompt_extensions": sha256_text("\x00".join(prompt_exts)),
        "instrument": str(instrument),
        "model": str(model) if model is not None else "none",
        "instrument_config": sha256_text(canonical_json(sheet.instrument_config)),
        "validations": sha256_text(canonical_json(sheet.validations)),
        "stdout_to": str(effective_stdout),
        "profile": profile_digest,
        "marianne_version": marianne.__version__,
    }


def first_difference(
    recorded: Mapping[str, str],
    current: Mapping[str, str],
) -> str | None:
    """Return the name of the first component that differs between recorded and current."""
    for comp in INPUT_COMPONENTS:
        if recorded.get(comp) != current.get(comp):
            return comp
    for k in sorted(set(recorded) | set(current)):
        if recorded.get(k) != current.get(k):
            return k
    return None


def source_digests(receipt: PromptReceipt | None) -> list[dict[str, str]]:
    """Convert a PromptReceipt into diagnostic source rows with hashes only, never text."""
    if receipt is None:
        return []
    results: list[dict[str, str]] = []
    for s in receipt.sources:
        sha = s.sha256 if s.sha256 is not None else f"dropped:{s.dropped_reason or 'unknown'}"
        results.append({
            "kind": str(s.kind),
            "path": str(s.path),
            "resolved_path": str(s.resolved_path) if s.resolved_path is not None else "",
            "sha256": sha,
        })
    return results


def describe_source_change(
    recorded: list[dict[str, str]],
    current: list[dict[str, str]],
) -> str | None:
    """Return a human-readable explanation of what changed in sources."""
    rec_by_key = {(s.get("kind"), s.get("path")): s for s in recorded}
    cur_by_key = {(s.get("kind"), s.get("path")): s for s in current}

    for cur in current:
        k = (cur.get("kind"), cur.get("path"))
        rec = rec_by_key.get(k)
        if rec is None:
            return f"added {cur.get('kind')}:{cur.get('path')}"
        if rec.get("sha256") != cur.get("sha256"):
            return f"{cur.get('kind')} {cur.get('path')} changed"

    for rec in recorded:
        k = (rec.get("kind"), rec.get("path"))
        if k not in cur_by_key:
            return f"removed {rec.get('kind')}:{rec.get('path')}"

    return None


def product_digests(workspace: Path, products: Iterable[str]) -> dict[str, str]:
    """Resolve declared product paths against workspace and return their sha256 or 'missing'."""
    results: dict[str, str] = {}
    for item in products:
        expanded = item.replace("{workspace}", str(workspace))
        p = Path(expanded).expanduser()
        resolved = (workspace / p).resolve() if not p.is_absolute() else p.resolve()
        abs_str = str(resolved)
        if resolved.is_file():
            results[abs_str] = sha256_file(resolved)
        else:
            results[abs_str] = "missing"
    return results


def changed_products(workspace: Path, recorded: Mapping[str, str]) -> str | None:
    """Check recorded products against workspace. Returns first path that changed or is missing."""
    for path_str, expected_sha in recorded.items():
        p = Path(path_str)
        if not p.is_file():
            return path_str
        current_sha = sha256_file(p)
        if current_sha != expected_sha:
            return path_str
    return None
