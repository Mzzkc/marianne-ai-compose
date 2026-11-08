"""Replay the shipped triage tail gates against reused workspace artifacts (#503)."""

import os
from pathlib import Path

import pytest
import yaml

from marianne.core.config import ValidationRule
from marianne.execution.validation.engine import ValidationEngine

SCORE = Path(__file__).parents[1] / "examples/engineering/issue-triage.yaml"
GATES = (
    "Triangulation covers this run's corpus",
    "DAG is fresh for this run's corpus",
    "DAG matches this run's label and issue set",
    "Manifest matches this run's label and issue count",
)


def _gate(description: str) -> ValidationRule:
    rules = yaml.safe_load(SCORE.read_text(encoding="utf-8"))["validations"]
    matches = [r for r in rules if r.get("description") == description]
    assert len(matches) == 1, f"Missing run-binding gate: {description}"
    return ValidationRule(**{**matches[0], "retry_count": 0})


def _workspace(
    tmp_path: Path, *, stale: bool = False, empty: bool = False, label: str = "bug"
) -> Path:
    workspace = tmp_path / "triage 'workspace'"
    (workspace / "issues").mkdir(parents=True)
    numbers = [] if empty else [503, 506]
    corpus = workspace / "issues/sanitized-corpus.yaml"
    corpus.write_text(yaml.safe_dump({"issues": [{"number": n} for n in numbers]}))
    artifact_numbers = [499] if stale else numbers
    artifact_label = "enhancement" if stale else label
    dag_text = yaml.safe_dump({
        "dag_metadata": {"label": artifact_label, "total_issues": len(artifact_numbers)},
        "issues": [{"number": n, "depends_on": []} for n in artifact_numbers],
        "resolved": [],
    })
    dag = workspace / "issue-dag.yaml"
    dag.write_text(dag_text)
    (workspace / f"issue-dag-{artifact_label}.yaml").write_text(dag_text)
    (workspace / "05-triangulation.md").write_text(
        "## Corroborated Edges\n## Uncorroborated Edges\n"
        "## Independent Issues (Tier-1)\n"
        + "\n".join(f"- #{n}" for n in artifact_numbers)
    )
    (workspace / "07-ready.md").write_text(
        f"- Label: {artifact_label}\n- Total triaged: {len(artifact_numbers)}\n"
        "Ready for Score 2 (issue-solver).\n"
    )
    # Explicit timestamps reproduce reuse without sleeps or filesystem-clock races.
    os.utime(corpus, (200, 200))
    os.utime(dag, (100, 100) if stale else (201, 201))
    return workspace


async def _passes(workspace: Path, description: str, label: str = "bug") -> bool:
    engine = ValidationEngine(workspace, {"sheet_num": 9, "issue_label": label})
    result = await engine.run_validations([_gate(description)])
    return result.all_passed


@pytest.mark.parametrize("description", GATES)
async def test_stale_tail_artifacts_refused(tmp_path: Path, description: str) -> None:
    workspace = _workspace(tmp_path, stale=True)
    assert not await _passes(workspace, description)


@pytest.mark.parametrize("description", GATES)
@pytest.mark.parametrize("empty", [False, True], ids=["populated", "empty"])
async def test_fresh_tail_artifacts_accepted(
    tmp_path: Path, description: str, empty: bool
) -> None:
    workspace = _workspace(tmp_path, empty=empty)
    assert await _passes(workspace, description)


@pytest.mark.parametrize("description", GATES)
async def test_fresh_quoted_label_accepted(tmp_path: Path, description: str) -> None:
    label = "team's bugs $(false)"
    workspace = _workspace(tmp_path, label=label)
    assert await _passes(workspace, description, label)


@pytest.mark.parametrize("change", ["label", "missing_issue", "extra_issue"])
async def test_fresh_dag_wrong_identity_refused(tmp_path: Path, change: str) -> None:
    workspace = _workspace(tmp_path)
    path = workspace / "issue-dag.yaml"
    dag = yaml.safe_load(path.read_text())
    if change == "label":
        dag["dag_metadata"]["label"] = "enhancement"
    elif change == "missing_issue":
        dag["issues"].pop()
    else:
        dag["issues"].append({"number": 999})
    path.write_text(yaml.safe_dump(dag))
    assert not await _passes(workspace, GATES[2])


@pytest.mark.parametrize(
    "manifest",
    [
        "- Label: debug\n- Total triaged: 2\n",  # label substring is insufficient
        "- Label: bug\n- Total triaged: 20\n",  # count prefix is insufficient
        "- Label: enhancement\n- Total triaged: 2\nNotes mention bug.\n",
    ],
)
async def test_manifest_wrong_label_or_count_refused(tmp_path: Path, manifest: str) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "07-ready.md").write_text(manifest)
    assert not await _passes(workspace, GATES[3])


async def test_equal_timestamp_fresh_dag_accepted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    os.utime(workspace / "issue-dag.yaml", (200, 200))
    assert await _passes(workspace, GATES[1])


async def test_missing_corpus_issue_in_triangulation_refused(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "05-triangulation.md").write_text("Only #503 and #5060 appear.\n")
    assert not await _passes(workspace, GATES[0])
