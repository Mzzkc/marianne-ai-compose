"""The example READMEs agree with the scores they describe (GH #481).

Wraps ``tests/scripts/check_examples_readme.py``: every relative link
resolves and every score row's Sheets column equals the score's concrete
sheet count. No baseline — the READMEs are ours to keep right.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tests" / "scripts" / "check_examples_readme.py"


def test_example_readmes_agree_with_their_scores() -> None:
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(ROOT), "--json"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    findings = json.loads(proc.stdout)
    assert proc.returncode == 0 and findings == [], json.dumps(findings, indent=1)
