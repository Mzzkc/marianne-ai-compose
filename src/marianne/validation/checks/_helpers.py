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


_PARSED_DOCUMENT_CACHE: dict[str, object] = {}
_PARSED_DOCUMENT_CACHE_LIMIT = 8


def parsed_document(raw_yaml: str) -> dict[object, object] | None:
    """The score's raw YAML as one mapping, parsed once per text.

    Several checks (V004, V327, V328, V329) read the raw document rather
    than the resolved ``JobConfig``. Each parsing it again cost a corpus
    sweep of 68 scores ~9 s on CI and tripped the 30 s test timeout.
    The parse is shared by text identity, so a check must treat the
    mapping as read-only. Returns ``None`` when the text is not a
    mapping or does not parse: every caller already returned no issues
    for that.
    """
    import yaml as _yaml

    if raw_yaml in _PARSED_DOCUMENT_CACHE:
        cached = _PARSED_DOCUMENT_CACHE[raw_yaml]
        return cached if isinstance(cached, dict) else None
    loader = getattr(_yaml, "CSafeLoader", _yaml.SafeLoader)
    try:
        data = _yaml.load(raw_yaml, Loader=loader)  # noqa: S506 - SafeLoader variant
    except _yaml.YAMLError:
        data = None
    if len(_PARSED_DOCUMENT_CACHE) >= _PARSED_DOCUMENT_CACHE_LIMIT:
        _PARSED_DOCUMENT_CACHE.pop(next(iter(_PARSED_DOCUMENT_CACHE)))
    _PARSED_DOCUMENT_CACHE[raw_yaml] = data
    return data if isinstance(data, dict) else None
