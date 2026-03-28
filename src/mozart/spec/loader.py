"""Specification corpus loader.

Loads spec fragments from a project's spec directory. Supports YAML spec
files (structured fragments with name/tags/content/data) and markdown files
(text fragments with name derived from filename).

The loader is the first stage of the spec corpus pipeline:

    spec_dir/*.yaml  → SpecCorpusLoader.load() → list[SpecFragment]
    spec_dir/*.md   →                           ↓
    CLAUDE.md (opt) →              SpecCorpusConfig.fragments
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mozart.core.config.spec import SpecFragment
from mozart.core.logging import get_logger

_logger = get_logger("spec.loader")

# File extensions that the loader recognizes.
_YAML_EXTENSIONS = {".yaml", ".yml"}
_MARKDOWN_EXTENSIONS = {".md"}
_RECOGNIZED_EXTENSIONS = _YAML_EXTENSIONS | _MARKDOWN_EXTENSIONS


class SpecCorpusError(Exception):
    """Error loading the specification corpus."""


class SpecCorpusLoader:
    """Loads specification fragments from a directory.

    The loader reads all recognized files from the spec directory and
    produces SpecFragment instances. YAML files are parsed for structured
    content (name, tags, kind, content, data). Markdown files become
    text fragments with the filename stem as the name.

    Binary files and unrecognized extensions are silently skipped.
    """

    @staticmethod
    def load(spec_dir: str | Path) -> list[SpecFragment]:
        """Load all spec fragments from a directory.

        Args:
            spec_dir: Path to the specification directory. Must exist.

        Returns:
            List of loaded SpecFragment instances, sorted by name for
            deterministic ordering.

        Raises:
            SpecCorpusError: If the directory does not exist or cannot
                be read.
        """
        spec_path = Path(spec_dir)

        if not spec_path.exists():
            raise SpecCorpusError(
                f"Spec directory does not exist: {spec_path}. "
                f"Set spec.spec_dir in your configuration to a valid directory."
            )

        if not spec_path.is_dir():
            raise SpecCorpusError(
                f"Spec path is not a directory: {spec_path}"
            )

        fragments: list[SpecFragment] = []
        errors: list[str] = []

        # Only read top-level files (no recursion into subdirectories).
        # This is deliberate: spec files are a flat collection, not a tree.
        for file_path in sorted(spec_path.iterdir()):
            if not file_path.is_file():
                continue

            suffix = file_path.suffix.lower()
            if suffix not in _RECOGNIZED_EXTENSIONS:
                _logger.debug(
                    "skipping_unrecognized_file",
                    path=str(file_path),
                    suffix=suffix,
                )
                continue

            try:
                if suffix in _YAML_EXTENSIONS:
                    fragment = _load_yaml_fragment(file_path)
                else:
                    fragment = _load_markdown_fragment(file_path)
                fragments.append(fragment)
                _logger.debug(
                    "loaded_fragment",
                    name=fragment.name,
                    kind=fragment.kind,
                    tags=fragment.tags,
                    content_length=len(fragment.content),
                )
            except Exception as exc:
                error_msg = f"{file_path.name}: {exc}"
                errors.append(error_msg)
                _logger.warning(
                    "fragment_load_error",
                    path=str(file_path),
                    error=str(exc),
                )

        if errors:
            _logger.warning(
                "spec_corpus_load_warnings",
                error_count=len(errors),
                errors=errors,
            )

        # Sort by name for deterministic ordering regardless of filesystem
        fragments.sort(key=lambda f: f.name)

        _logger.info(
            "spec_corpus_loaded",
            fragment_count=len(fragments),
            error_count=len(errors),
            directory=str(spec_path),
        )

        return fragments

    @staticmethod
    def load_claude_md(project_root: str | Path) -> SpecFragment | None:
        """Load CLAUDE.md as a spec fragment if it exists.

        Args:
            project_root: Path to the project root directory.

        Returns:
            SpecFragment with name='claude_md' and kind='text', or None
            if CLAUDE.md does not exist.
        """
        claude_path = Path(project_root) / "CLAUDE.md"
        if not claude_path.is_file():
            return None

        try:
            content = claude_path.read_text(encoding="utf-8")
            if not content.strip():
                return None
            return SpecFragment(
                name="claude_md",
                content=content,
                tags=[],
                kind="text",
            )
        except Exception as exc:
            _logger.warning(
                "claude_md_load_error",
                path=str(claude_path),
                error=str(exc),
            )
            return None


def _load_yaml_fragment(path: Path) -> SpecFragment:
    """Load a single YAML spec file as a SpecFragment.

    Expected YAML structure::

        name: intent
        tags: [goals, trade-offs, purpose]
        kind: structured
        content: |
          The full text content...
        data:
          key: value

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed SpecFragment.

    Raises:
        ValueError: If required fields are missing or invalid.
        yaml.YAMLError: If the file contains invalid YAML.
    """
    raw = path.read_text(encoding="utf-8")
    parsed: Any = yaml.safe_load(raw)

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected YAML mapping at top level, got {type(parsed).__name__}"
        )

    # Extract fields with defaults
    name = parsed.get("name")
    if name is None:
        raise ValueError(
            f"Missing required 'name' field in {path.name}"
        )

    content = parsed.get("content")
    if content is None:
        raise ValueError(
            f"Missing required 'content' field in {path.name}"
        )

    tags = parsed.get("tags", [])
    if not isinstance(tags, list):
        tags = [str(tags)]

    kind = parsed.get("kind", "text")
    data = parsed.get("data")

    return SpecFragment(
        name=str(name),
        content=str(content),
        tags=[str(t) for t in tags],
        kind=kind,
        data=data,
    )


def _load_markdown_fragment(path: Path) -> SpecFragment:
    """Load a markdown file as a text SpecFragment.

    The fragment name is derived from the filename stem (e.g.,
    ``conventions.md`` → name ``conventions``).

    Args:
        path: Path to the markdown file.

    Returns:
        SpecFragment with kind='text'.

    Raises:
        ValueError: If the file is empty.
    """
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"Empty markdown file: {path.name}")

    return SpecFragment(
        name=path.stem,
        content=content,
        tags=[],
        kind="text",
    )
