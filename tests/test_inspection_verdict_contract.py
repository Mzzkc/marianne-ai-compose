"""The closed inspect-report contract (compiler: ``VERDICT: PASS|REWORK|BLOCKED`` + ``FINDINGS:``).

``mzt verdict`` must read a report written to the contract without interpreting
prose, and must still leave free prose as ``unknown`` (the mapping is not widened).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.cli.commands.conduct import _scan_verdict, extract_findings, find_verdict

_BODY = (
    "EVIDENCE:\n- ran the suite, 412 passed\n\n"
    "FAILURES:\n- the retry path drops the lock\n\n"
    "RISKS:\n- none beyond the above\n\n"
)


def _report(tmp_path: Path, text: str) -> Path:
    state = tmp_path / "cycle-state"
    state.mkdir()
    (state / "guide-inspection.md").write_text(text, encoding="utf-8")
    return tmp_path


def _read(workspace: Path) -> tuple[str, list[str]]:
    result, text, index = _scan_verdict(workspace)
    return result["verdict"], extract_findings(text, index, result["verdict"])


@pytest.mark.parametrize(
    ("word", "findings_block", "required_fixes", "want_findings"),
    [
        (
            "PASS", "FINDINGS:\n- none\n", "REQUIRED FIXES:\n- none\n", [],
        ),
        (
            "REWORK",
            "FINDINGS:\n- retry path drops the lock\n- missing test for the empty case\n",
            "REQUIRED FIXES:\n- hold the lock across the retry\n",
            ["retry path drops the lock", "missing test for the empty case"],
        ),
        (
            "BLOCKED",
            "FINDINGS:\n- needs independent verification: the author cannot run the Windows "
            "build\n",
            "REQUIRED FIXES:\n- someone with a Windows seat runs it\n",
            ["needs independent verification: the author cannot run the Windows build"],
        ),
    ],
)
def test_report_written_to_the_contract_is_read_exactly(
    tmp_path, word, findings_block, required_fixes, want_findings,
):
    text = (
        f"VERDICT: {word}\nQUALIFIER: bounded to the runtime branch\n\n"
        + _BODY + findings_block + "\n" + required_fixes
    )
    workspace = _report(tmp_path, text)
    assert find_verdict(workspace)["verdict"] == word
    assert find_verdict(workspace)["raw"] == f"VERDICT: {word}"
    verdict, findings = _read(workspace)
    assert verdict == word
    assert findings == want_findings


def test_findings_section_wins_over_required_fixes(tmp_path):
    text = (
        "VERDICT: REWORK\n" + _BODY
        + "REQUIRED FIXES:\n- a fix\n\nFINDINGS:\n- a finding\n"
    )
    assert _read(_report(tmp_path, text)) == ("REWORK", ["a finding"])


@pytest.mark.parametrize(
    "opening",
    [
        "PASS WITH ISSUES",
        "Qualified PASS",
        "REWORK of head 8e2bc88f",
        "SUITE ACCEPT_WITH_REPAIRS; PRODUCT FIRST RUN NOT READY",
        "The runtime branch is a bounded, clean change.",
    ],
)
def test_free_prose_is_still_not_read_as_the_contract(tmp_path, opening):
    """The contract is enforced at write time (validation), not by widening the map."""
    workspace = _report(tmp_path, opening + "\n" + _BODY)
    assert find_verdict(workspace)["verdict"] == "unknown"
