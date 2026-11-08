"""Functional boundary tests for the complete shipped triage tail (#503)."""

import os
from pathlib import Path

import pytest
import yaml

from marianne.core.config import ValidationRule
from marianne.execution.validation.engine import ValidationEngine

SCORE = Path(__file__).parents[1] / "examples/engineering/issue-triage.yaml"


def _tail_rules() -> list[ValidationRule]:
    return [
        ValidationRule(**{**rule, "retry_count": 0})
        for rule in yaml.safe_load(SCORE.read_text())["validations"]
        if rule.get("condition") in {"sheet_num >= 7", "sheet_num >= 8", "sheet_num >= 9"}
    ]


@pytest.fixture
def tail_workspace(tmp_path: Path) -> Path:
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues/sanitized-corpus.yaml").write_text(
        yaml.safe_dump({"issues": [{"number": 503}, {"number": 506}]})
    )
    (tmp_path / "05-triangulation.md").write_text(
        "## Corroborated Edges\n## Uncorroborated Edges\n"
        "## Independent Issues\n#503 #506\n"
    )
    (tmp_path / "06-dag.md").write_text("## DAG Summary\n## Verification\nAcyclic: 2 issues\n")
    (tmp_path / "issue-dag.yaml").write_text(yaml.safe_dump({
        "dag_metadata": {"label": "bug", "total_issues": 2},
        # Reversed order verifies set equality rather than sequence equality.
        "issues": [{"number": 506, "depends_on": [503]}, {"number": 503, "depends_on": []}],
        "resolved": [],
    }))
    (tmp_path / "07-ready.md").write_text(
        "- Label: bug\n- Total triaged: 2\nReady for Score 2 (issue-solver).\n"
    )
    os.utime(tmp_path / "issues/sanitized-corpus.yaml", (200, 200))
    os.utime(tmp_path / "issue-dag.yaml", (201, 201))
    return tmp_path


@pytest.mark.parametrize("sheet_num", [7, 8, 9])
async def test_tail_stage_only_requires_artifacts_due_now(
    tail_workspace: Path, sheet_num: int
) -> None:
    if sheet_num < 9:
        (tail_workspace / "07-ready.md").unlink()
    if sheet_num < 8:
        (tail_workspace / "issue-dag.yaml").unlink()
        (tail_workspace / "06-dag.md").unlink()
    result = await ValidationEngine(
        tail_workspace, {"sheet_num": sheet_num, "issue_label": "bug"}
    ).run_validations(_tail_rules())
    assert result.all_passed, result


async def test_same_label_same_issues_still_refuses_stale_dag(tail_workspace: Path) -> None:
    os.utime(tail_workspace / "issue-dag.yaml", (199, 199))
    result = await ValidationEngine(
        tail_workspace, {"sheet_num": 9, "issue_label": "bug"}
    ).run_validations(_tail_rules())
    assert not result.all_passed


@pytest.mark.parametrize("corruption", ["cycle", "unknown_dependency"])
async def test_fresh_identity_does_not_bypass_dependency_checks(
    tail_workspace: Path, corruption: str
) -> None:
    path = tail_workspace / "issue-dag.yaml"
    dag = yaml.safe_load(path.read_text())
    dag["issues"][1]["depends_on"] = [506] if corruption == "cycle" else [999]
    path.write_text(yaml.safe_dump(dag))
    result = await ValidationEngine(
        tail_workspace, {"sheet_num": 9, "issue_label": "bug"}
    ).run_validations(_tail_rules())
    assert not result.all_passed


@pytest.mark.parametrize("manifest_suffix", ["- Label: bug\n", "- Total triaged: 2\n"])
async def test_duplicate_manifest_fields_refused(
    tail_workspace: Path, manifest_suffix: str
) -> None:
    path = tail_workspace / "07-ready.md"
    path.write_text(path.read_text() + manifest_suffix)
    result = await ValidationEngine(
        tail_workspace, {"sheet_num": 9, "issue_label": "bug"}
    ).run_validations(_tail_rules())
    assert not result.all_passed


@pytest.mark.parametrize(
    "artifact",
    ["issues/sanitized-corpus.yaml", "05-triangulation.md", "issue-dag.yaml", "07-ready.md"],
)
@pytest.mark.parametrize("damage", ["missing", "malformed"])
async def test_missing_or_malformed_artifact_fails_closed(
    tail_workspace: Path, artifact: str, damage: str
) -> None:
    path = tail_workspace / artifact
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("[unterminated\n")
    result = await ValidationEngine(
        tail_workspace, {"sheet_num": 9, "issue_label": "bug"}
    ).run_validations(_tail_rules())
    assert not result.all_passed
