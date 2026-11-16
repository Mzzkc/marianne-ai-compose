"""GH #554 and #555: ``mzt verdict`` extras and ``mzt next --tasks``.

The expected shapes are the SC1 replay shim's (sc1-small-model-eval/shim/mzt):
verdict ``{verdict, raw, source_path, round, findings, accept, result_path}``
and next ``[{job_id, action, why}]`` plus ``task`` on CAST rows. The conductor
is the scripted fake of test_cli_conduct_547; workspaces and checkpoints are
built on tmp paths the way ``mzt agents run`` lays them out.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands import conduct
from marianne.cli.commands.conduct import (
    accept_counts,
    extract_findings,
    find_task_file,
    parse_task_fields,
)
from tests.test_cli_conduct_547 import FakeConductor

TASK = (
    "GOAL: Add input validation to the signup form.\n"
    "FILES: src/signup/, tests/signup/\n"
    "KIND: internal\n"
    "ACCEPT: pytest tests/signup/\n"
    "DO_NOT_TOUCH: src/billing/\n"
    "REPORT: result.md\n"
)
FINDINGS = [
    "Email validation accepts a@b with no dot in the domain (src/signup/validate.py line 41).",
    "Password minimum length is 6 but the form text says 8 (src/signup/validate.py line 58).",
]
REWORK_REPORT = (
    "# Inspect\n\nVERDICT: REWORK\n\nEVIDENCE:\n- ran the signup tests\n\nFAILURES:\n"
    + "".join(f"- {f}\n" for f in FINDINGS)
    + "\nRISKS:\n- none\n\nREQUIRED FIXES:\n"
    + "".join(f"- {f}\n" for f in FINDINGS)
)


def _task_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _engagement(tmp_path: Path, task_text: str, report: str, *, name: str = "TASK.md") -> dict:
    """The run dir `mzt agents run` writes (bound score, task copy); it is the workspace."""
    job_id = f"targeted-work-ines-task-{_task_hash(task_text)}-20261116"
    run_dir = tmp_path / "runs" / f"ines-task-{_task_hash(task_text)}-20261116"
    (run_dir / "cycle-state").mkdir(parents=True)
    (run_dir / f"task-{name}").write_text(task_text)
    score = run_dir / f"{job_id}.yaml"
    score.write_text(yaml.safe_dump({
        "name": job_id,
        "prompt": {"variables": {"marianne_agent": {"task": {
            "copy": f"task-{name}", "copy_relative_to": "score_dir",
        }}}},
    }))
    (run_dir / "cycle-state" / "ines-inspection.md").write_text(report)
    return {"job_id": job_id, "workspace": run_dir, "config_path": str(score)}


def _state(*per_sheet: tuple[int, int]) -> dict[str, Any]:
    """Checkpoint sheets: (ran, passed) validation_details per sheet."""
    sheets: dict[str, Any] = {}
    for number, (ran, passed) in enumerate(per_sheet, start=1):
        sheets[str(number)] = {
            "status": "completed",
            "validation_details": [
                {"rule_type": "file_exists", "description": f"check {i}", "passed": i < passed}
                for i in range(ran)
            ],
        }
    return {"status": "completed", "sheets": sheets}


def _verdict(monkeypatch, eng: dict, state: dict | None) -> dict[str, Any]:
    fake = FakeConductor(
        rows=[{"job_id": eng["job_id"], "workspace": str(eng["workspace"]),
               "config_path": eng["config_path"], "status": "completed", "submitted_at": 1.0}],
        statuses={eng["job_id"]: state} if state is not None else {},
    )
    monkeypatch.setattr(conduct, "_route", fake)
    result = CliRunner().invoke(app, ["verdict", eng["job_id"], "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# -- the shim's JUDGE fixtures (s1, s3, s3b round 2, safety) ----------------------------------


def test_s1_clean_pass_shape(monkeypatch, tmp_path):
    eng = _engagement(tmp_path, TASK, "# Inspect\nVERDICT: PASS\n")
    out = _verdict(monkeypatch, eng, _state((2, 2), (2, 2)))
    ws = str(eng["workspace"])
    assert out == {
        "verdict": "PASS", "raw": "VERDICT: PASS",
        "source_path": f"{ws}/cycle-state/ines-inspection.md",
        "round": 1, "findings": [], "accept": {"ran": 4, "passed": 4},
        "result_path": f"{ws}/result.md",
    }


def test_s3_rework_round_1_carries_findings_and_3_of_4(monkeypatch, tmp_path):
    eng = _engagement(tmp_path, TASK, REWORK_REPORT)
    out = _verdict(monkeypatch, eng, _state((2, 2), (2, 1)))
    assert out["verdict"] == "REWORK"
    assert out["round"] == 1
    assert out["findings"] == FINDINGS
    assert out["accept"] == {"ran": 4, "passed": 3}


def test_s3_round_2_comes_from_the_findings_line_of_the_second_task(monkeypatch, tmp_path):
    task2 = TASK + "FINDINGS: " + " ".join(FINDINGS) + "\n"
    eng = _engagement(tmp_path, task2, "# Inspect\nVERDICT: PASS\n", name="TASK-2.md")
    out = _verdict(monkeypatch, eng, _state((3, 3), (2, 2)))
    assert (out["verdict"], out["round"], out["findings"]) == ("PASS", 2, [])
    assert out["accept"] == {"ran": 5, "passed": 5}


def test_safety_pass_without_validations_is_zero_of_zero(monkeypatch, tmp_path):
    eng = _engagement(tmp_path, TASK, "VERDICT: PASS\n")
    state = {"status": "completed", "sheets": {"1": {"status": "completed"}}}
    out = _verdict(monkeypatch, eng, state)
    assert out["accept"] == {"ran": 0, "passed": 0}


def test_s4_s5_unknown_verdict_keeps_the_extras(monkeypatch, tmp_path):
    eng = _engagement(tmp_path, TASK, "# Inspect\nnothing decided yet\n")
    out = _verdict(monkeypatch, eng, None)  # status unavailable
    assert out["verdict"] == "unknown" and out["raw"] is None and out["source_path"] is None
    assert out["findings"] == [] and out["accept"] == {"ran": 0, "passed": 0}
    assert out["round"] == 1 and out["result_path"].endswith("/result.md")


def test_result_path_is_the_report_basename_in_the_workspace(monkeypatch, tmp_path):
    task = TASK.replace("REPORT: result.md", "REPORT: /elsewhere/out/final-report.md")
    eng = _engagement(tmp_path, task, "VERDICT: PASS\n")
    out = _verdict(monkeypatch, eng, _state((1, 1)))
    assert out["result_path"] == f"{eng['workspace']}/final-report.md"


def test_job_without_a_task_file_has_null_round_and_result_path(monkeypatch, tmp_path):
    (tmp_path / "r.md").write_text("VERDICT: PASS\n")
    fake = FakeConductor(rows=[{"job_id": "plain", "workspace": str(tmp_path),
                                "status": "completed", "submitted_at": 1.0}],
                         statuses={"plain": _state((1, 1))})
    monkeypatch.setattr(conduct, "_route", fake)
    out = json.loads(CliRunner().invoke(app, ["verdict", "plain", "--json"]).output)
    assert out["round"] is None and out["result_path"] is None
    assert out["accept"] == {"ran": 1, "passed": 1}


def test_explicit_workspace_works_without_a_conductor(monkeypatch, tmp_path):
    eng = _engagement(tmp_path, TASK, REWORK_REPORT)
    fake = FakeConductor()
    monkeypatch.setattr(conduct, "_route", fake)
    out = json.loads(CliRunner().invoke(
        app, ["verdict", "x", "-w", str(eng["workspace"]), "--json"]).output)
    assert out["findings"] == FINDINGS and out["round"] == 1
    assert out["accept"] == {"ran": 0, "passed": 0}
    assert fake.calls == []


def test_the_original_four_keys_are_kept(monkeypatch, tmp_path):
    eng = _engagement(tmp_path, TASK, "VERDICT: HOLD\n")
    out = _verdict(monkeypatch, eng, _state())
    assert {"verdict", "raw", "source_path", "round"} <= set(out)
    assert out["verdict"] == "BLOCKED"


def test_text_output_lists_findings(monkeypatch, tmp_path):
    eng = _engagement(tmp_path, TASK, REWORK_REPORT)
    monkeypatch.setattr(conduct, "_route", FakeConductor(
        rows=[{"job_id": eng["job_id"], "workspace": str(eng["workspace"]),
               "config_path": eng["config_path"]}], statuses={}))
    result = CliRunner().invoke(app, ["verdict", eng["job_id"]])
    assert "REWORK:" in result.output and FINDINGS[0] in result.output


# -- findings rule -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("VERDICT: REWORK\nFINDINGS:\n- a\n- b\n", ["a", "b"]),
        ("VERDICT: REWORK\n## Findings\n1. a\n2) b\n\n## Next\n- c\n", ["a", "b"]),
        ("VERDICT: REWORK\n**FINDINGS:** one line\n", ["one line"]),
        # FINDINGS wins over REQUIRED FIXES and FAILURES
        ("VERDICT: REWORK\nFAILURES:\n- f\nREQUIRED FIXES:\n- r\nFINDINGS:\n- x\n", ["x"]),
        # then REQUIRED FIXES wins over FAILURES
        ("VERDICT: REWORK\nFAILURES:\n- f\nRISKS:\n- k\nREQUIRED FIXES:\n- r\n", ["r"]),
        ("VERDICT: REWORK\nEVIDENCE:\n- e\nFAILURES:\n- f1\n  continued\n", ["f1 continued"]),
        # placeholders are dropped, so the next rule applies
        ("VERDICT: REWORK\nFINDINGS:\n- none\n", []),
        # after the VERDICT line, up to the next label
        ("VERDICT: REWORK\n- first\n* second\n\nEVIDENCE:\n- e\n", ["first", "second"]),
        ("VERDICT: REWORK\nthe tests do not cover x\n", ["the tests do not cover x"]),
        # PASS with no FINDINGS section: nothing, even if bullets follow
        ("VERDICT: PASS\n- looks fine\n", []),
        ("VERDICT: PASS\nFINDINGS:\n- nit: rename x\n", ["nit: rename x"]),
    ],
)
def test_findings_rule(text, expected):
    verdict_index = next(i for i, ln in enumerate(text.splitlines()) if ln.startswith("VERDICT"))
    word = "PASS" if "VERDICT: PASS" in text else "REWORK"
    assert extract_findings(text, verdict_index, word) == expected


def test_findings_are_capped():
    rows = "".join(f"- item {i} " + "x" * 600 + "\n" for i in range(30))
    text = "VERDICT: REWORK\nFINDINGS:\n" + rows
    items = extract_findings(text, 0, "REWORK")
    assert len(items) == 20 and all(len(i) == 500 for i in items)


# -- accept counts, task parsing ----------------------------------------------------------------


def test_accept_counts_falls_back_to_name_lists():
    state = {"sheets": {
        "1": {"passed_validations": ["a", "b"], "failed_validations": ["c"]},
        "2": {"validation_details": [{"passed": True}, {"passed": False}]},
        "3": "garbage",
    }}
    assert accept_counts(state) == {"ran": 5, "passed": 3}
    assert accept_counts(None) == {"ran": 0, "passed": 0}


def test_parse_task_fields_matches_agents_run_grammar():
    fields = parse_task_fields("> GOAL: a\n  more\n- KIND: internal\nKIND: other\nFINDINGS: f\n")
    assert fields == {"GOAL": "a more", "KIND": "internal", "FINDINGS": "f"}


def test_find_task_file_falls_back_to_the_workspace_glob(tmp_path):
    (tmp_path / "task-TASK.md").write_text(TASK)
    (tmp_path / "task-notes.md").write_text("no fields here\n")
    assert find_task_file(tmp_path, None) == tmp_path / "task-TASK.md"
    assert find_task_file(tmp_path / "nowhere", None) is None


# -- the shim's own scenario files, when this machine has them -----------------------------------

_SCEN = Path.home() / "Projects/SCORES/sc1-small-model-eval/scenarios"


@pytest.mark.skipif(not _SCEN.is_dir(), reason="SC1 eval scenarios not on this machine")
def test_real_verdict_keys_cover_every_shim_verdict_fixture():
    shim_keys = set()
    for path in _SCEN.glob("*.yaml"):
        for entry in yaml.safe_load(path.read_text()).get("verdict", []):
            shim_keys |= set(entry["out"])
    real = {"verdict", "raw", "source_path", "round", "findings", "accept", "result_path"}
    assert shim_keys <= real
