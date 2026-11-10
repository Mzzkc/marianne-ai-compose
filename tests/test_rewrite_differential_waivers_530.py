"""#530: a codebase-rewrite waiver excuses only what it names.

The shipped score let a bare filename in ``intended-differences.txt`` exempt
an input's whole output. In proof 4, one design rule about connection framing
was listed against 145 of 245 inputs and hid 44 changed scan verdicts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "examples" / "engineering" / "assets" / "differential_compare.py"
SCORE = REPO / "examples" / "engineering" / "codebase-rewrite.yaml"

# source prints a verdict; target adds a framing line and (for "bad") changes the verdict
SOURCE = 'read x; echo PING; echo "$x: FOUND"'
TARGET_FRAMING_ONLY = 'read x; echo PING; echo IDSESSION; echo "$x: FOUND"'
TARGET_VERDICT_LOST = 'read x; echo PING; echo IDSESSION; echo "$x: OK"'


def _run(tmp_path: Path, waiver_text: str, target: str, inputs: int = 1, *extra: str):
    corpus = tmp_path / "inputs"
    corpus.mkdir()
    for i in range(inputs):
        (corpus / f"case-{i}.txt").write_text(f"sample{i}\n")
    waivers = tmp_path / "intended-differences.txt"
    waivers.write_text(waiver_text)
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--inputs",
            str(corpus),
            "--waivers",
            str(waivers),
            "--source",
            SOURCE,
            "--target",
            target,
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )


def test_identical_programs_pass_with_no_waivers(tmp_path: Path) -> None:
    result = _run(tmp_path, "", SOURCE)
    assert result.returncode == 0, result.stdout
    assert "SAME case-0.txt" in result.stdout


def test_unlisted_difference_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, "", TARGET_FRAMING_ONLY)
    assert result.returncode == 1
    assert "DIFF case-0.txt" in result.stdout


def test_scoped_waiver_covers_exactly_the_framing_line(tmp_path: Path) -> None:
    result = _run(
        tmp_path, "case-0.txt  stdout:+^IDSESSION$  # D4 pool framing\n", TARGET_FRAMING_ONLY
    )
    assert result.returncode == 0, result.stdout
    assert "INTENDED case-0.txt" in result.stdout


def test_scoped_waiver_does_not_hide_a_changed_verdict(tmp_path: Path) -> None:
    """The proof-4 failure: the framing rule was listed, the verdict changed underneath it."""
    result = _run(
        tmp_path, "case-0.txt  stdout:+^IDSESSION$  # D4 pool framing\n", TARGET_VERDICT_LOST
    )
    assert result.returncode == 1, result.stdout
    assert "still differs outside its waiver" in result.stdout


def test_bare_filename_waiver_is_refused(tmp_path: Path) -> None:
    result = _run(tmp_path, "case-0.txt\n# D4 pool framing\n", TARGET_VERDICT_LOST)
    assert result.returncode == 2, result.stdout
    assert "BADWAIVER" in result.stdout and "no scope" in result.stdout


def test_waiver_for_a_file_not_in_the_corpus_is_refused(tmp_path: Path) -> None:
    result = _run(tmp_path, "ghost.txt  exit  # typo\n", SOURCE)
    assert result.returncode == 2
    assert "not a file in the corpus" in result.stdout


def test_exit_scope_covers_only_the_exit_code(tmp_path: Path) -> None:
    target = SOURCE + "; exit 3"
    assert _run(tmp_path, "case-0.txt  exit  # rule\n", target).returncode == 0


def test_blanket_waivers_above_the_share_fail(tmp_path: Path) -> None:
    text = "".join(f"case-{i}.txt  stdout:*  # one rule for everything\n" for i in range(5))
    result = _run(tmp_path, text, TARGET_VERDICT_LOST, 5)
    assert result.returncode == 1
    assert "BLANKET_SHARE_EXCEEDED" in result.stdout


def test_the_shipped_score_uses_the_comparator_everywhere_it_honours_waivers() -> None:
    text = SCORE.read_text(encoding="utf-8")
    assert "differential_compare.py" in text
    # the old whole-output skip must be gone from prompt and validation
    assert "grep -qxF" not in text
    assert "A DIFF is a failure unless that input is listed" not in text


@pytest.mark.parametrize("needle", ["stdout:+", "stdout:-", "bare filename"])
def test_the_score_teaches_the_scoped_format(needle: str) -> None:
    assert needle in SCORE.read_text(encoding="utf-8")
