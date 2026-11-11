"""Read a harness's per-session usage record after an interactive sheet.

Interactive (tmux) sessions have no JSON stdout to parse, so token usage is
recovered from the record the harness itself writes for the session. The
profile's ``cli.interactive.usage`` block says where that record lives and how
to read it; nothing here is specific to one harness.

Safety contract: the file is streamed line by line (never loaded whole),
malformed lines are skipped, and no message content is ever logged or kept —
only integer token counts and a model name leave this module.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marianne.core.config.instruments import InteractiveUsageConfig
from marianne.core.logging import get_logger
from marianne.execution.base import ExecutionResult
from marianne.utils.json_path import extract_json_path

_logger = get_logger("interactive.usage")


@dataclass(frozen=True)
class UsageReading:
    """What a session's usage record yielded.

    ``reason`` is None only for a measured reading. Otherwise the session is
    unmeasured and the counts must not be trusted as zero.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str | None = None
    responses: int = 0
    reason: str | None = None

    @property
    def measured(self) -> bool:
        """True when at least one usage-bearing response was read."""
        return self.reason is None


def unmeasured(reason: str) -> UsageReading:
    """A reading that says the session's usage is unknown."""
    return UsageReading(reason=reason)


def resolve_record_path(template: str, session_id: str) -> Path | None:
    """Expand ``~`` and ``{session_id}``; return the newest matching file."""
    pattern = os.path.expanduser(template.replace("{session_id}", session_id))
    matches = [Path(p) for p in glob.glob(pattern)]
    files = [p for p in matches if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _count(data: Any, path: str | None) -> int:
    if not path:
        return 0
    value = extract_json_path(data, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


def _matches_filter(data: Any, record_filter: dict[str, str]) -> bool:
    return all(extract_json_path(data, k) == v for k, v in record_filter.items())


def read_usage_file(path: Path, config: InteractiveUsageConfig) -> UsageReading:
    """Stream ``path`` and total one usage entry per dedupe key.

    A response can be written as several lines sharing a dedupe key; the LAST
    one carries the final counts, so earlier ones are overwritten. Lines with
    no dedupe key count individually. Sidechain (subagent) lines are included
    when they are in the same file.
    """
    per_key: dict[str, tuple[int, int, int, int, str | None]] = {}
    anonymous = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(data, dict) or not _matches_filter(
                    data, config.record_filter,
                ):
                    continue
                model_raw = extract_json_path(data, config.model_path) if config.model_path else None
                model = model_raw if isinstance(model_raw, str) and model_raw else None
                if model in config.ignore_models:
                    continue
                entry = (
                    _count(data, config.input_tokens_path),
                    _count(data, config.output_tokens_path),
                    _count(data, config.cache_read_tokens_path),
                    _count(data, config.cache_write_tokens_path),
                    model,
                )
                key_raw = (
                    extract_json_path(data, config.dedupe_key_path)
                    if config.dedupe_key_path else None
                )
                if isinstance(key_raw, str) and key_raw:
                    per_key[key_raw] = entry
                else:
                    anonymous += 1
                    per_key[f"\0line:{line_no}"] = entry
    except OSError as exc:
        return unmeasured(f"record unreadable: {type(exc).__name__}")

    if not per_key:
        return unmeasured("record has no usage-bearing responses")

    by_model: dict[str, int] = {}
    for _, out, _, _, model in per_key.values():
        if model:
            by_model[model] = by_model.get(model, 0) + out
    top_model = max(by_model, key=lambda m: by_model[m]) if by_model else None
    return UsageReading(
        input_tokens=sum(e[0] for e in per_key.values()),
        output_tokens=sum(e[1] for e in per_key.values()),
        cache_read_tokens=sum(e[2] for e in per_key.values()),
        cache_write_tokens=sum(e[3] for e in per_key.values()),
        model=top_model,
        responses=len(per_key),
    )


def apply_usage(result: ExecutionResult, reading: UsageReading) -> None:
    """Fill the result's token fields, or mark it unmeasured (never zero).

    Unmeasured travels through ``usage_unmeasured``, which the musician folds
    into the existing ``cost_uncertain`` flag that ``mzt status`` renders.
    """
    if not reading.measured:
        result.usage_unmeasured = True
        return
    result.input_tokens = reading.input_tokens
    result.output_tokens = reading.output_tokens
    result.cache_read_tokens = reading.cache_read_tokens
    result.cache_write_tokens = reading.cache_write_tokens
    if reading.model:
        result.model_observed = reading.model


def read_session_usage(
    config: InteractiveUsageConfig | None, session_id: str | None,
) -> UsageReading:
    """Blocking read for one session; callers run it via ``asyncio.to_thread``."""
    if config is None or session_id is None:
        return unmeasured("profile declares no interactive usage record")
    try:
        path = resolve_record_path(config.record_path, session_id)
    except OSError as exc:
        return unmeasured(f"record lookup failed: {type(exc).__name__}")
    if path is None:
        return unmeasured("usage record not found")
    return read_usage_file(path, config)
