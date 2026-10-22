#!/usr/bin/env python3
"""Check that the example READMEs agree with the scores they describe.

Two derivable facts, both hand-written today and both found drifting by the
21 Oct examples QA (24 readme findings, GH #481):

* every relative link in an examples README resolves to a file or directory
  that exists;
* every score row's ``Sheets`` column equals the score's concrete sheet count
  (``JobConfig.sheet.total_sheets`` — fan-out expanded).

A gate that reads the score file beats a rule in prose. Exit 1 on any finding.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
_ROW = re.compile(
    r"^\| \[\*{0,2}([a-z0-9-]+)\*{0,2}\]\(([^)]+\.yaml)\) \|[^|]*\| *(\d+)[^|]*\|", re.M
)


def readmes(root: Path) -> list[Path]:
    found = sorted((root / "examples").glob("*/README.md"))
    found.append(root / "examples" / "README.md")
    extra = root / "examples" / "finance" / "24x7-trader" / "README.md"
    if extra.exists():
        found.append(extra)
    return found


_SUBMODULE_PATH = re.compile(r"^\s*path\s*=\s*(\S+)", re.M)


def absent_submodules(root: Path) -> list[Path]:
    """Submodule roots that are not checked out (CI skips rosetta-corpus and
    compiler on purpose). A link into one is unverifiable here, not dead."""
    gitmodules = root / ".gitmodules"
    if not gitmodules.exists():
        return []
    roots = [root / m.group(1) for m in _SUBMODULE_PATH.finditer(gitmodules.read_text())]
    return [r for r in roots if not r.is_dir() or not any(r.iterdir())]


def check(root: Path) -> list[dict[str, str]]:
    logging.disable(logging.CRITICAL)
    from marianne.core.config.job import JobConfig  # type: ignore[import-untyped]

    findings: list[dict[str, str]] = []
    skipped = absent_submodules(root)
    for readme in readmes(root):
        text = readme.read_text()
        rel = str(readme.relative_to(root))
        for m in _LINK.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (readme.parent / target).resolve()
            if any(resolved.is_relative_to(sub.resolve()) for sub in skipped):
                continue
            if not resolved.exists():
                findings.append({"check": "dead-link", "readme": rel, "target": target})
        for m in _ROW.finditer(text):
            name, score_rel, claimed = m.group(1), m.group(2), int(m.group(3))
            score = readme.parent / score_rel
            if not score.exists():
                continue  # the dead-link row already names it
            try:
                actual = JobConfig.from_yaml(score).sheet.total_sheets
            except Exception as exc:  # noqa: BLE001 — an unparseable example is its own finding
                findings.append({
                    "check": "unparseable", "readme": rel, "score": name,
                    "error": str(exc)[:120],
                })
                continue
            if actual != claimed:
                findings.append({
                    "check": "sheet-count", "readme": rel, "score": name,
                    "claimed": str(claimed), "actual": str(actual),
                })
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    # Profile loaders log to stdout while scores parse; keep --json clean.
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        findings = check(args.repo)
    if args.json:
        print(json.dumps(findings, indent=1))
    else:
        for f in findings:
            print(" ".join(f"{k}={v}" for k, v in f.items()))
        print(f"Findings: {len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
