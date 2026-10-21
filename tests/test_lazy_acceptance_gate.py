"""Example sheets whose acceptance a lazy fixture satisfies only shrink (GH #480).

Wraps ``tests/scripts/check_lazy_acceptance.py``: the real ValidationEngine
run against a synthesised headings-plus-filler fixture per sheet. The
baseline fixture holds today's lazy-accepted sheets (132 of 689 on 21 Oct).
A new row fails; a vanished row asks for the fixture to shrink, the way the
language and corpus baselines do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tests" / "scripts" / "check_lazy_acceptance.py"
BASELINE = ROOT / "tests" / "fixtures" / "lazy_acceptance_baseline.json"


def _key(row: dict[str, str]) -> tuple[str, str]:
    return (row["score"], row["sheet"])


def test_lazy_accepted_sheets_only_shrink() -> None:
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(ROOT), "--json"],
        capture_output=True, text=True, timeout=900, check=False,
    )
    current = {_key(r) for r in json.loads(proc.stdout)}
    baseline = {_key(r) for r in json.loads(BASELINE.read_text())}
    new = sorted(current - baseline)
    assert not new, (
        "New example sheets pass their validations on a headings-plus-filler "
        f"fixture: {new}. Add a command_succeeds gate or a rule a stub cannot "
        "satisfy (GH #480)."
    )
    gone = sorted(baseline - current)
    assert not gone, (
        f"These sheets no longer accept the lazy fixture — shrink the baseline: {gone}"
    )
