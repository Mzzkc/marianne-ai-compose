"""Independent functional controls for movement output ownership (GH #384)."""

from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.core.producers import declared_products_from_config
from marianne.core.sheet import build_sheets
from marianne.daemon.baton.musician import (
    _expand_artifact_paths,
    _foreign_artifact_warnings,
    _snapshot_artifacts,
    _write_stdout_to,
    resolve_stdout_to,
)
from marianne.execution.base import ExecutionResult
from marianne.validation.checks.paths import CadenzaOrderingCheck, PreludeCadenzaFileCheck


def _config(tmp_path: Path, *, sheet=None, **overrides) -> JobConfig:
    return JobConfig.model_validate({
        "name": "functional-384", "workspace": str(tmp_path),
        "instrument": "claude-code", "prompt": {"template": "x"},
        "sheet": {"size": 1, "total_items": 3, **(sheet or {})},
        **overrides,
    })


def test_explicit_output_reaches_real_artifact_observer(tmp_path: Path) -> None:
    cfg = _config(tmp_path, movements={3: {"produces": ["out/result.md"]}})
    first, _, owner = build_sheets(cfg)
    observed = _expand_artifact_paths(first, total_sheets=3, total_movements=3)
    assert [path for _, path in observed] == [tmp_path / "out/result.md"]
    before = _snapshot_artifacts(observed)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/result.md").write_text("premature output")
    warnings = _foreign_artifact_warnings(observed, before)
    assert len(warnings) == 1 and "created" in warnings[0]
    assert _expand_artifact_paths(owner, total_sheets=3, total_movements=3) == []
    # Declaring ownership must not manufacture successful validations or files.
    assert owner.validations == []


def test_explicit_declaration_does_not_excuse_a_missing_prelude(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, movements={1: {"produces": ["missing.md"]}},
        sheet={
            "prelude": [{"file": "missing.md", "as": "context"}],
            "cadenzas": {2: [{"file": "missing.md", "as": "context"}]},
            "dependencies": {2: [1]},
        },
    )
    issues = PreludeCadenzaFileCheck().check(cfg, tmp_path / "score.yaml", "")
    assert len(issues) == 1 and issues[0].metadata["source"] == "prelude"
    assert issues[0].check_id == "V108"
    assert CadenzaOrderingCheck().check(cfg, tmp_path / "score.yaml", "") == []


def test_one_earlier_shared_owner_is_enough_for_ordering(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, movements={1: {"produces": ["shared.md"]},
                             3: {"produces": ["shared.md"]}},
        sheet={"dependencies": {2: [1]},
               "cadenzas": {2: [{"file": "shared.md", "as": "context"}]}},
    )
    assert CadenzaOrderingCheck().check(cfg, tmp_path / "score.yaml", "") == []
    assert build_sheets(cfg)[1].foreign_artifacts == []


def test_transitive_ancestor_is_excluded_from_foreign_outputs(tmp_path: Path) -> None:
    cfg = _config(tmp_path, movements={1: {"produces": ["first.md"]}},
                  sheet={"dependencies": {2: [1], 3: [2]}})
    assert [sheet.foreign_artifacts for sheet in build_sheets(cfg)] == [[], [], []]


def test_explicit_fan_out_count_overrides_movement_voices(tmp_path: Path) -> None:
    cfg = _config(tmp_path, movements={2: {"voices": 3, "produces": ["v-{sheet_num}.md"]}},
                  sheet={"fan_out": {2: 2}})
    assert declared_products_from_config(cfg) == {
        2: ["{workspace}/v-2.md"], 3: ["{workspace}/v-3.md"],
    }
    assert len(build_sheets(cfg)) == 4


def test_stdout_declaration_matches_actual_successful_write(tmp_path: Path) -> None:
    cfg = _config(tmp_path, sheet={"stdout_to": {2: "nested/reply.md"}})
    observer, writer, _ = build_sheets(cfg)
    observed = _expand_artifact_paths(observer, total_sheets=3, total_movements=3)
    assert [path for _, path in observed] == [resolve_stdout_to(writer)]
    before = _snapshot_artifacts(observed)
    result = ExecutionResult(success=True, exit_code=0, stdout="whole reply", stderr="",
                             duration_seconds=0.01)
    assert _write_stdout_to(writer, result) is None
    assert (tmp_path / "nested/reply.md").read_text() == "whole reply"
    assert len(_foreign_artifact_warnings(observed, before)) == 1


@pytest.mark.parametrize("target_kind", ["relative", "absolute", "symlink"])
def test_refused_stdout_target_cannot_hide_missing_cadenza_input(
    tmp_path: Path, target_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if target_kind == "relative":
        target = "../outside/never-written.md"
    elif target_kind == "absolute":
        target = str(outside / "never-written.md")
    else:
        (workspace / "alias").symlink_to(outside, target_is_directory=True)
        target = "alias/never-written.md"
    cfg = _config(workspace, sheet={
        "stdout_to": {1: target}, "dependencies": {2: [1]},
        "cadenzas": {2: [{"file": target, "as": "context"}]},
    })
    writer = build_sheets(cfg)[0]
    assert resolve_stdout_to(writer) is None
    issues = PreludeCadenzaFileCheck().check(cfg, workspace / "score.yaml", "")
    assert len(issues) == 1 and issues[0].check_id == "V108"


def test_unrelated_new_declaration_preserves_proxy_output(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, movements={2: {"produces": ["unrelated.md"]}},
        validations=[
            {"type": "file_exists", "path": "{workspace}/legacy.md", "sheet": 3},
        ],
    )
    first, _, owner = build_sheets(cfg)
    watched = _expand_artifact_paths(first, total_sheets=3, total_movements=3)
    assert {path for _, path in watched} == {tmp_path / "unrelated.md", tmp_path / "legacy.md"}
    assert "{workspace}/legacy.md" not in owner.foreign_artifacts


@pytest.mark.parametrize("check", ["ordering", "missing-file"])
def test_declared_output_file_symlink_keeps_its_cadenza_identity(
    tmp_path: Path, check: str,
) -> None:
    (tmp_path / "report.md").symlink_to("actual-output.md")
    cfg = _config(
        tmp_path, movements={3: {"produces": ["report.md"]}},
        sheet={"dependencies": {2: [1]},
               "cadenzas": {2: [{"file": "report.md", "as": "context"}]}},
    )
    if check == "ordering":
        issues = CadenzaOrderingCheck().check(cfg, tmp_path / "score.yaml", "")
        assert len(issues) == 1 and issues[0].check_id == "V109"
    else:
        assert PreludeCadenzaFileCheck().check(cfg, tmp_path / "score.yaml", "") == []
