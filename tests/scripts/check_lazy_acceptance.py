#!/usr/bin/env python3
"""Count example sheets whose acceptance a lazy fixture satisfies (GH #480).

The 21 Oct examples QA found the dominant authoring defect (96 of 218
findings): validations written as mirrors of the prompt. The required
headings and keywords are already in the prompt, so an output that repeats
them — headings, one keyword per rule, filler — passes every gate.

This gate productises the QA voices' probes. For every example sheet it
builds a workspace-local fixture that satisfies each rule as literally as
possible — the file a ``file_exists`` names, the exact ``content_contains``
text, a string the ``content_regex`` matches (via its own literal
characters), plus 800 words of filler — and runs the real
``ValidationEngine``. A sheet whose applicable rules ALL pass on that
fixture has content-only acceptance that filler satisfies. ``command_succeeds``
and ``file_modified`` rules are not synthesised: a sheet that carries one is
judged by it, and a lazy fixture fails it unless the command is itself
lazy (the QA's third arm — a separate probe).

Output: one row per lazy-accepted sheet. The baseline fixture holds today's
rows; the test fails on a NEW row and asks for the fixture to shrink when a
row disappears. Exit 1 on any row when run without --baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import logging
import re
import sys
import tempfile
from pathlib import Path

FILLER = ("stub placeholder " * 400).strip() + "\n"


def _regex_witness(pattern: str) -> str | None:
    """A string the pattern matches, built from the pattern's own literal
    text; None when the pattern needs a real structure we will not fake."""
    candidate = re.sub(r"\(\?[a-z]+\)", "", pattern)          # inline flags
    candidate = re.sub(r"\\b|\^|\$", "", candidate)            # anchors
    candidate = re.sub(r"\\s[*+]?", " ", candidate)
    candidate = re.sub(r"\\d\+?", "7", candidate)
    candidate = re.sub(r"\\w\+?|\.\*\??|\.\+\??|\[\^[^\]]*\][*+?]?", "x", candidate)
    candidate = re.sub(r"\[([A-Za-z])[A-Za-z]\]", r"\1", candidate)     # [Pp] -> P
    candidate = re.sub(r"\[[^\]]*\]", "x", candidate)
    candidate = candidate.split("|")[0]
    candidate = re.sub(r"[()?*+{}]", "", candidate)
    candidate = candidate.replace("\\", "")
    try:
        if re.search(pattern, candidate, re.MULTILINE):
            return candidate
    except re.error:
        return None
    return None


def lazy_sheets(root: Path) -> list[dict[str, str]]:
    logging.disable(logging.CRITICAL)
    from marianne.core.config.execution import (  # type: ignore[import-untyped]
        ValidationRule,
    )
    from marianne.core.config.job import JobConfig  # type: ignore[import-untyped]
    from marianne.core.sheet import build_sheets  # type: ignore[import-untyped]
    from marianne.execution.validation.engine import (  # type: ignore[import-untyped]
        ValidationEngine,
    )

    rows: list[dict[str, str]] = []
    for score in sorted((root / "examples").rglob("*.yaml")):
        try:
            cfg = JobConfig.from_yaml(score)
        except Exception:  # noqa: BLE001 — unparseable examples are the corpus sweep's finding
            continue
        if not cfg.validations:
            continue
        sheets = build_sheets(cfg)
        total_movements = len({s.movement for s in sheets}) or 1
        with tempfile.TemporaryDirectory(prefix="lazy-") as tmp:
            ws = Path(tmp) / "ws"
            for sheet in sheets:
                sheet.workspace = ws
                ctx = sheet.template_variables(
                    total_sheets=len(sheets), total_movements=total_movements,
                )
                engine = ValidationEngine(workspace=ws, sheet_context=ctx)
                rules: list[ValidationRule] = engine.get_applicable_rules(sheet.validations)
                if not rules or any(r.type in ("command_succeeds", "file_modified") for r in rules):
                    continue
                # Synthesise the fixture: one file per path, filler + every
                # literal that rule asks for.
                wanted: dict[Path, list[str]] = {}
                synthesisable = True
                for r in rules:
                    if not r.path:
                        synthesisable = False
                        break
                    try:
                        path = engine.expand_path(r.path)
                    except Exception:  # noqa: BLE001 — an unexpandable path is V105's finding
                        synthesisable = False
                        break
                    wanted.setdefault(path, [])
                    if r.type == "content_contains" and r.pattern:
                        wanted[path].append(r.pattern)
                    elif r.type == "content_regex" and r.pattern:
                        witness = _regex_witness(r.pattern)
                        if witness is None:
                            synthesisable = False
                            break
                        wanted[path].append(witness)
                if not synthesisable:
                    continue
                for path, literals in wanted.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(FILLER + "\n".join(literals) + "\n" + FILLER)
                result = asyncio.run(engine.run_validations(rules))
                if result.all_passed:
                    rows.append({
                        "score": str(score.relative_to(root)),
                        "sheet": str(sheet.num),
                        "rules": str(len(rules)),
                    })
                for path in wanted:
                    with contextlib.suppress(OSError):
                        path.unlink()
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    with contextlib.redirect_stdout(io.StringIO()):
        rows = lazy_sheets(args.repo)
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"{r['score']} sheet {r['sheet']} ({r['rules']} rules)")
        print(f"Lazy-accepted sheets: {len(rows)}")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
