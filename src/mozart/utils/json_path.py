"""Lightweight JSON dot-path extractor for instrument output parsing.

Used by the instrument plugin system (PluginCliBackend) to extract result
text, error messages, and token counts from CLI instrument output. This is
NOT full JSONPath — it's a minimal extractor covering the patterns observed
across all researched CLI instruments.

Supported syntax:
    key             — top-level key
    key.subkey      — nested access
    key[0]          — array index
    key.*           — wildcard: iterate all values, return first match
    key.*.subkey    — wildcard with nested access

Examples:
    extract_json_path(data, "result")           → data["result"]
    extract_json_path(data, "error.message")    → data["error"]["message"]
    extract_json_path(data, "items[0]")         → data["items"][0]
    extract_json_path(data, "models.*.tokens")  → first data["models"][k]["tokens"]
"""

from __future__ import annotations

import re
from typing import Any

# Matches key[index] patterns like "items[0]", "results[3]"
_ARRAY_INDEX_RE = re.compile(r"^(.+)\[(\d+)]$")


def _extract_segment(data: Any, segment: str) -> Any | None:
    """Extract a single path segment from a data node.

    Handles plain keys and array index notation (key[N]).
    Returns None on any access failure.
    """
    if data is None or not isinstance(data, (dict, list)):
        return None

    # Check for array index notation: key[N]
    match = _ARRAY_INDEX_RE.match(segment)
    if match:
        key, idx_str = match.group(1), int(match.group(2))
        if isinstance(data, dict) and key in data:
            container = data[key]
            if isinstance(container, list) and 0 <= idx_str < len(container):
                return container[idx_str]
        return None

    # Plain key access
    if isinstance(data, dict) and segment in data:
        return data[segment]

    return None


def extract_json_path(data: Any, path: str) -> Any | None:
    """Extract a value from nested data using a dot-path.

    Returns the first matching value, or None if the path doesn't resolve.
    Wildcards (*) iterate all values in a dict and return the first match.

    Args:
        data: Parsed JSON data (typically a dict).
        path: Dot-separated path string, e.g. "error.message", "models.*.tokens".

    Returns:
        The extracted value, or None if not found.
    """
    if not path or data is None:
        return None

    segments = path.split(".")
    current: Any = data

    for i, segment in enumerate(segments):
        if current is None:
            return None

        if segment == "*":
            # Wildcard: iterate all values, recurse with remaining path
            if not isinstance(current, dict):
                return None
            remaining = ".".join(segments[i + 1:])
            if not remaining:
                # Wildcard at end — return first value
                for val in current.values():
                    return val
                return None
            for val in current.values():
                result = extract_json_path(val, remaining)
                if result is not None:
                    return result
            return None

        current = _extract_segment(current, segment)

    return current


def extract_json_path_all(data: Any, path: str) -> list[Any]:
    """Extract ALL matching values from nested data using a dot-path.

    Like extract_json_path but collects all wildcard matches instead of
    returning only the first. Useful for aggregating token counts across
    multiple models.

    Args:
        data: Parsed JSON data (typically a dict).
        path: Dot-separated path string with wildcards.

    Returns:
        List of all matching values (may be empty).
    """
    if not path or data is None:
        return []

    segments = path.split(".")
    return _collect_all(data, segments, 0)


def _collect_all(data: Any, segments: list[str], idx: int) -> list[Any]:
    """Recursively collect all matching values for a segmented path."""
    if data is None or idx >= len(segments):
        return [data] if data is not None else []

    segment = segments[idx]

    if segment == "*":
        if not isinstance(data, dict):
            return []
        results: list[Any] = []
        for val in data.values():
            results.extend(_collect_all(val, segments, idx + 1))
        return results

    next_data = _extract_segment(data, segment)
    if next_data is None:
        return []

    if idx == len(segments) - 1:
        return [next_data]

    return _collect_all(next_data, segments, idx + 1)
