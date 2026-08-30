"""Shared helper functions for validation checks.

Extracted from individual check modules to eliminate duplication (D02).
"""

from pathlib import Path


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
