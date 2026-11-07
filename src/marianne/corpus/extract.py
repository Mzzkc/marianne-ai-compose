"""Deterministic extraction of explicitly selected, trusted repository scores."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path

from marianne.corpus.schema import CorpusProvenance, TrainingExample

COMPOSITION_INSTRUCTION = "Compose a Marianne score using the demonstrated composition patterns."


def extract_in_repo(roots: Iterable[Path]) -> list[TrainingExample]:
    """Read YAML verbatim, excluding nested repositories, locks and symlinks.

    These are trusted local sources, not community submissions. No instruction
    or WHY annotation is inferred from score prose.
    """
    paths: dict[Path, Path] = {}
    for root in sorted(roots):
        if root.is_symlink() or (root / ".git").exists():
            continue
        if not root.is_dir():
            raise ValueError("Repository sources must be existing directories.")
        for directory, dirs, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = sorted(
                name
                for name in dirs
                if name != ".git"
                and not name.endswith(".lock")
                and not (parent / name).is_symlink()
                and not (parent / name / ".git").exists()
            )
            for name in sorted(files):
                path = parent / name
                if path.suffix.lower() not in {".yaml", ".yml"} or path.is_symlink():
                    continue
                paths.setdefault(path.resolve(), path)
    records = []
    for path in sorted(paths.values()):
        # read_text normalizes CRLF; score output must preserve original bytes.
        output = path.read_bytes().decode("utf-8")
        records.append(
            TrainingExample(
                instruction=COMPOSITION_INSTRUCTION,
                output=output,
                provenance=CorpusProvenance(
                    source_path=str(path),
                    source_kind="in_repo",
                    category=path.parent.name,
                    content_hash=hashlib.sha256(output.encode("utf-8")).hexdigest(),
                ),
            )
        )
    return records
