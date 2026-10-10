"""Shared helper functions for validation checks.

Extracted from individual check modules to eliminate duplication (D02).
"""

from pathlib import Path


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance; the nearest-name instrument for typo suggestions."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        row = [i]
        for j, char_b in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (char_a != char_b)))
        previous = row
    return previous[-1]


def nearest_name(name: str, candidates: set[str], *, max_distance: int = 2) -> str | None:
    """Nearest candidate within ``max_distance`` edits (§8 V-CLS-01 suggestion)."""
    near = [c for c in candidates if edit_distance(name, c) <= max_distance]
    return min(near, key=lambda c: (edit_distance(name, c), c)) if near else None


def find_line_in_yaml(yaml_str: str, marker: str) -> int | None:
    """Find the line number of a marker in the YAML string.

    Args:
        yaml_str: Raw YAML content as string.
        marker: Substring to search for in each line.

    Returns:
        1-based line number if found, None otherwise.
    """
    for i, line in enumerate(yaml_str.split("\n"), 1):
        if marker in line:
            return i
    return None


def resolve_path(path: Path, config_path: Path) -> Path:
    """Resolve a potentially relative path against the config file location.

    Args:
        path: Path that may be relative or absolute.
        config_path: Path to the config YAML file (used as reference for relative paths).

    Returns:
        Absolute path resolved against config file's parent directory.
    """
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return config_path.parent / expanded
