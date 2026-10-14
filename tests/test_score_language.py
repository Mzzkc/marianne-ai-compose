"""The score-language gate (GH #436): the four layers must agree.

``tests/scripts/check_language.py`` asks the schema, the validator registry, the
docs and the skills the same questions an LLM asks while writing a score
(what IS this key, when is it WRONG, WHY, WHEN) and reports every path-
addressed disagreement. ``tests/fixtures/language_baseline.json`` lists the
gaps known on the day the gate landed; this test is RED only on a NEW gap.
Closing a gap removes its baseline line in the same commit (the fixture
may only shrink), so the language can never drift back without a red run.

Controls that prove each check fires on a planted defect and stays silent on
the correct form live in ``tests/test_check_language_controls.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tests" / "scripts" / "check_language.py"
BASELINE = ROOT / "tests" / "fixtures" / "language_baseline.json"
SKILL_SURFACE = ROOT / "plugins" / "marianne" / "skills" / "score-authoring" / "SKILL.md"

pytestmark = pytest.mark.skipif(
    not SKILL_SURFACE.exists(),
    reason=(
        "plugins submodule not checked out: the gate reads the skill layer and "
        "cannot judge the language without it (git submodule update --init plugins)"
    ),
)


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(ROOT), "--json", *extra],
        capture_output=True, text=True, timeout=600, check=False,
    )


def _regressions(stdout: str) -> str:
    """The NEW findings, which the JSON serialises first and a tail would hide."""
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout[-4000:]
    return "\n".join(
        f"NEW {r['id']}  {r.get('detail', '')}  {' '.join(r.get('where', []))}"
        for r in report.get("regressions", [])
    ) or "(no regression rows; see stderr)"


def test_no_language_regression_outside_the_baseline() -> None:
    result = _run("--baseline", str(BASELINE))
    assert result.returncode == 0, (
        "A schema key, validator code, doc or skill line disagrees with the "
        "others and is not in the baseline. Fix the language (all four layers "
        "in one commit, CLAUDE.md) rather than adding to the fixture:\n"
        + _regressions(result.stdout) + "\n" + result.stderr[-2000:]
    )


def test_baseline_only_shrinks() -> None:
    """Every baseline line must still be a live finding; a closed gap removes its line."""
    fixture = json.loads(BASELINE.read_text(encoding="utf-8"))
    listed = set(fixture["findings"])
    result = _run()
    report = json.loads(result.stdout)
    gating = {f["id"] for f in report["findings"] if f.get("gating", True)}
    stale = sorted(listed - gating)
    assert not stale, (
        "These baseline lines no longer fire; remove them from "
        "tests/fixtures/language_baseline.json so the gate keeps them closed:\n"
        + "\n".join(stale)
    )
