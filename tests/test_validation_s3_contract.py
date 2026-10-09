"""S3 contracts at the score loader, CLI, and structural-check boundaries."""

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.core.config.job import ValidateConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.paths import WorkspaceParentExistsCheck
from marianne.validation.checks.structure import (
    AmbiguousFileReferenceCheck,
    CadenzaTargetCheck,
    ConcertTargetCheck,
    DependencyCycleCheck,
    FanOutCoherenceCheck,
    UnreachableSheetCheck,
    UnusedVariableCheck,
    VariableCoverageCheck,
)

BASE = """name: s3-test
sheet: {size: 1, total_items: 2}
prompt: {template: hello}
validations:
  - {type: file_exists, path: '{workspace}/out.txt'}
"""


@given(st.lists(st.from_regex(r"V[0-9]{3}", fullmatch=True), max_size=12))
def test_validate_config_round_trip(codes: list[str]) -> None:
    """The score-local suppression declaration survives a config snapshot."""
    config = ValidateConfig(suppress=codes)
    assert ValidateConfig.model_validate(config.model_dump()).suppress == codes


def _score(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "score.yaml"
    path.write_text(BASE + extra)
    return path


def test_unknown_fields_are_retained_as_findings(tmp_path: Path) -> None:
    path = _score(tmp_path, "sheetz: 1\nlogging:\n  levelz: info\n")
    config = JobConfig.from_yaml(path)
    assert {field.key for field in config.unknown_fields} == {"sheetz", "levelz"}
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 0
    issues = json.loads(result.stdout)["issues"]
    assert sum(issue["check_id"] == "V010" for issue in issues) == 2


def test_distant_unknown_is_info_and_dict_keys_are_data(tmp_path: Path) -> None:
    path = _score(
        tmp_path,
        "quizzical_unknown: yes\nsheet:\n  size: 1\n  total_items: 2\n  dependencies: {2: [1]}\n",
    )
    # YAML duplicate sheet keys use the last one; the dict[int] key is data.
    config = JobConfig.from_yaml(path)
    assert [field.key for field in config.unknown_fields] == ["quizzical_unknown"]
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 0
    assert any(issue["check_id"] == "V011" for issue in json.loads(result.stdout)["issues"])


def test_suppression_truth_and_strict(tmp_path: Path) -> None:
    path = _score(tmp_path, "sheetz: 1\nvalidate: {suppress: [V010]}\n")
    result = CliRunner().invoke(app, ["validate", str(path), "--json", "--strict"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["valid"] is True
    assert data["suppressed"] == [{"check_id": "V010", "count": 1}]
    assert data["summary"]["sheets"] == 2

    path.write_text(BASE + "validate: {suppress: [V210, V999]}\n")
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["valid"] is False
    assert sum(item["check_id"] == "V012" for item in data["issues"]) == 2


def test_strict_and_errors_only_are_separate(tmp_path: Path) -> None:
    path = _score(tmp_path, "sheetz: 1\n")
    result = CliRunner().invoke(app, ["validate", str(path), "--strict", "--errors-only", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["warning_count"] >= 1
    assert data["issues"] == []


def test_non_score_and_genuine_schema_error_exit_two(tmp_path: Path) -> None:
    path = tmp_path / "fragment.yaml"
    path.write_text("scheduler: {enabled: true}\n")
    assert CliRunner().invoke(app, ["validate", str(path)]).exit_code == 2
    path.write_text(BASE.replace("size: 1", "size: nope"))
    assert CliRunner().invoke(app, ["validate", str(path)]).exit_code == 2


def test_dependency_cycle_and_cadenza_target(tmp_path: Path) -> None:
    path = _score(tmp_path, "")
    config = JobConfig.from_yaml(path)
    config.sheet.dependencies = {1: [2], 2: [1]}
    assert DependencyCycleCheck().check(config, path, path.read_text())[0].check_id == "V209"
    config.sheet.cadenzas = {3: []}
    assert CadenzaTargetCheck().check(config, path, path.read_text())[0].check_id == "V216"


def test_static_concert_target_and_identity_allowlist(tmp_path: Path) -> None:
    path = _score(tmp_path, "on_success:\n  - {type: run_job, job_path: missing.yaml}\n")
    config = JobConfig.from_yaml(path)
    assert ConcertTargetCheck().check(config, path, path.read_text())[0].check_id == "V217"
    config.validations[
        0
    ].path = "{agent_name}/{role}/{focus}/{agent_voice}/{agent_identity_dir}/{missing}"
    issues = VariableCoverageCheck().check(config, path, path.read_text())
    assert len(issues) == 1 and "{missing}" in issues[0].message


def test_chain_workspace_parent_is_advisory_only_in_target_context(tmp_path: Path) -> None:
    child = tmp_path / "child.yaml"
    child.write_text(BASE + f"workspace: {tmp_path / 'produced' / 'child'}\n")
    child_config = JobConfig.from_yaml(child)
    direct = WorkspaceParentExistsCheck().check(child_config, child, child.read_text())
    assert direct and direct[0].severity == ValidationSeverity.ERROR
    parent = _score(tmp_path, "on_success:\n  - {type: run_job, job_path: child.yaml}\n")
    target_issues = ConcertTargetCheck().check(
        JobConfig.from_yaml(parent), parent, parent.read_text()
    )
    assert target_issues and target_issues[0].check_id == "V002"
    assert target_issues[0].severity == ValidationSeverity.WARNING


def test_other_structural_checks_on_concrete_faults(tmp_path: Path) -> None:
    path = _score(tmp_path, "")
    config = JobConfig.from_yaml(path)
    config.sheet.dependencies = {2: [99]}
    assert FanOutCoherenceCheck().check(config, path, path.read_text())[0].check_id == "V214"
    assert UnreachableSheetCheck().check(config, path, path.read_text())[0].check_id == "V219"
    config.validations[0].type = "command_succeeds"
    config.validations[0].command = "cat ./input.txt"
    assert AmbiguousFileReferenceCheck().check(config, path, path.read_text())[0].check_id == "V218"
    config.prompt.variables = {"unused": "value", "marianne_agent": "anchor"}
    assert UnusedVariableCheck().check(config, path, path.read_text())[0].check_id == "V110"


def test_dependency_range_is_structural_exit_one(tmp_path: Path) -> None:
    path = _score(tmp_path, "")
    path.write_text(
        BASE.replace(
            "sheet: {size: 1, total_items: 2}",
            "sheet: {size: 1, total_items: 2, dependencies: {2: [99]}}",
        )
    )
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["issues"][0]["check_id"] == "V214"


def test_info_is_verbose_only_and_run_warns_on_unknown_field(tmp_path: Path) -> None:
    path = _score(tmp_path, "unknownthing: value\n")
    default = CliRunner().invoke(app, ["validate", str(path)])
    verbose = CliRunner().invoke(app, ["validate", str(path), "--verbose"])
    assert default.exit_code == verbose.exit_code == 0
    assert "[V011]" not in default.stdout
    assert "[V011]" in verbose.stdout
    run = CliRunner().invoke(app, ["run", str(path), "--dry-run"])
    assert run.exit_code == 0
    assert "ignored unknown score field" in run.output


def test_nested_instrument_fields_tolerant_but_alias_is_known(tmp_path: Path) -> None:
    path = _score(tmp_path, "instruments: {writer: {profile: cli, profilx: cli}}\n")
    config = JobConfig.from_yaml(path)
    assert [(field.path, field.key) for field in config.unknown_fields] == [
        ("instruments.writer", "profilx")
    ]
