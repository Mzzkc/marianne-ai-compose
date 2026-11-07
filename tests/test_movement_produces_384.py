"""GH #384: movement output declarations reach ordering and artifact guards."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from marianne.core import producers
from marianne.core.config import JobConfig
from marianne.core.config.job import MovementDef
from marianne.core.sheet import build_sheets
from marianne.daemon.baton.musician import _expand_artifact_paths, resolve_stdout_to
from marianne.validation.checks.paths import CadenzaOrderingCheck, PreludeCadenzaFileCheck


def _config(tmp_path: Path, *, movements=None, validations=None, **sheet) -> JobConfig:
    return JobConfig.model_validate({
        "name": "products", "workspace": str(tmp_path), "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 3, **sheet},
        "prompt": {"template": "x"},
        "movements": movements or {}, "validations": validations or [],
    })


def test_schema_defaults_are_independent_and_unknown_keys_still_fail() -> None:
    first = MovementDef()
    assert first.produces == []
    first.produces.append("out.md")
    assert MovementDef().produces == []
    assert MovementDef(produces=["out.md"]).produces == ["out.md"]
    assert MovementDef.model_fields["produces"].description
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MovementDef(produce=["out.md"])
    with pytest.raises(ValidationError):
        MovementDef(produces="out.md")


def test_merge_unions_independent_sources_in_priority_order(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, movements={1: {"produces": ["declared.md"]}},
        stdout_to={1: "captured.md", 2: "second.md"},
        validations=[{"type": "file_exists", "path": "proxy.md", "sheet": 1}],
    )
    assert producers.declared_products_from_config(cfg) == {
        1: ["{workspace}/declared.md", "{workspace}/captured.md", "proxy.md"],
        2: ["{workspace}/second.md"],
    }


def test_explicit_owner_overrides_stdout_and_proxy_for_same_path(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, movements={3: {"produces": ["{workspace}/report.md", "other/report.md"]}},
        stdout_to={1: "report.md"},
        validations=[{"type": "file_exists", "path": "report.md", "sheet": 2}],
    )
    assert producers.declared_products_from_config(cfg) == {
        3: ["{workspace}/report.md", "{workspace}/other/report.md"],
    }


def test_stdout_owner_overrides_proxy_for_same_path(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, stdout_to={3: "{workspace}/report.md"},
        validations=[{"type": "file_exists", "path": "report.md", "sheet": 1}],
    )
    assert producers.declared_products_from_config(cfg) == {3: ["{workspace}/report.md"]}


def test_proxy_only_map_is_unchanged_including_duplicates(tmp_path: Path) -> None:
    cfg = _config(tmp_path, validations=[
        {"type": "file_exists", "path": "{workspace}/old.md", "sheet": 2},
        {"type": "file_exists", "path": "{workspace}/old.md", "sheet": 2},
        {"type": "file_exists", "path": "ungated.md"},
    ])
    assert producers.declared_products_from_config(cfg) == producers.declared_products(
        cfg.validations,
    )


@pytest.mark.parametrize("fan_out", [False, True])
def test_movement_products_expand_to_concrete_voices(tmp_path: Path, fan_out: bool) -> None:
    cfg = _config(
        tmp_path,
        movements={
            2: {"produces": ["{workspace}/voice-{sheet_num}.md"],
                **({} if fan_out else {"voices": 2})},
            3: {"produces": ["final.md"]},
        },
        **({"fan_out": {2: 2}} if fan_out else {}),
    )
    expected = {
        2: ["{workspace}/voice-2.md"], 3: ["{workspace}/voice-3.md"],
        4: ["{workspace}/final.md"],
    }
    assert producers.declared_products_from_config(cfg) == expected
    # Checkpoint round-tripping must not re-expand movement declarations.
    restored = JobConfig.model_validate(cfg.model_dump())
    assert producers.declared_products_from_config(restored) == expected
    paths = _expand_artifact_paths(build_sheets(cfg)[0], total_sheets=4, total_movements=3)
    assert {path for _, path in paths} == {
        tmp_path / "voice-2.md", tmp_path / "voice-3.md", tmp_path / "final.md",
    }


def test_stdout_fan_out_map_matches_actual_writer_targets(tmp_path: Path) -> None:
    cfg = _config(tmp_path, movements={2: {"voices": 2}}, stdout_to={2: "reply.txt"})
    products = producers.declared_products_from_config(cfg)
    assert products == {2: ["{workspace}/reply-2.txt"], 3: ["{workspace}/reply-3.txt"]}
    for sheet in build_sheets(cfg)[1:3]:
        declared_path = Path(products[sheet.num][0].replace("{workspace}", str(tmp_path)))
        assert declared_path == resolve_stdout_to(sheet)


@pytest.mark.parametrize("signal", ["explicit", "stdout"])
@pytest.mark.parametrize("ancestor", [False, True])
def test_ordering_and_missing_file_checks_use_new_signals(
    tmp_path: Path, signal: str, ancestor: bool,
) -> None:
    producer = 1 if ancestor else 3
    cfg = _config(
        tmp_path,
        movements=(
            {producer: {"produces": ["{workspace}/report.md"]}} if signal == "explicit" else {}
        ),
        stdout_to={producer: "report.md"} if signal == "stdout" else {},
        cadenzas={2: [{"file": "report.md", "as": "context"}]},
        dependencies={2: [1], 3: [2]},
    )
    issues = CadenzaOrderingCheck().check(cfg, tmp_path / "s.yaml", "")
    if ancestor:
        assert issues == []
    else:
        assert len(issues) == 1
        assert issues[0].check_id == "V109"
        assert issues[0].severity.value == "warning"
        assert "report.md" in issues[0].message
    assert PreludeCadenzaFileCheck().check(cfg, tmp_path / "s.yaml", "") == []


def test_stale_proxy_cannot_hide_explicit_ordering_bug(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, movements={3: {"produces": ["{workspace}/report.md"]}},
        validations=[{"type": "file_exists", "path": "report.md", "sheet": 1}],
        cadenzas={2: [{"file": "report.md", "as": "context"}]}, dependencies={2: [1]},
    )
    assert len(CadenzaOrderingCheck().check(cfg, tmp_path / "s.yaml", "")) == 1


def test_build_sheets_guards_explicit_and_stdout_products(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, movements={3: {"produces": ["{workspace}/final.md"]}},
        stdout_to={2: "reply.txt"}, dependencies={3: [1, 2]},
    )
    sheets = build_sheets(cfg)
    assert sheets[0].foreign_artifacts == ["{workspace}/reply.txt", "{workspace}/final.md"]
    assert sheets[1].foreign_artifacts == ["{workspace}/final.md"]
    assert sheets[2].foreign_artifacts == []


def test_shared_explicit_product_is_not_foreign_to_its_owners(tmp_path: Path) -> None:
    cfg = _config(tmp_path, movements={2: {"voices": 2, "produces": ["shared.md"]}})
    sheets = build_sheets(cfg)
    assert sheets[0].foreign_artifacts == ["{workspace}/shared.md"]
    assert sheets[1].foreign_artifacts == []
    assert sheets[2].foreign_artifacts == []


@pytest.mark.parametrize("spelling", ["./shared.md", "absolute", "symlink"])
def test_equivalent_explicit_owners_do_not_guard_each_others_output(
    tmp_path: Path, spelling: str,
) -> None:
    first = "shared.md"
    second = str(tmp_path / "shared.md") if spelling == "absolute" else spelling
    if spelling == "symlink":
        (tmp_path / "real").mkdir()
        (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
        first, second = "alias/shared.md", "real/shared.md"
    cfg = _config(tmp_path, movements={1: {"produces": [first]}, 3: {"produces": [second]}})
    sheets = build_sheets(cfg)
    assert sheets[0].foreign_artifacts == []
    assert sheets[2].foreign_artifacts == []


def test_proxy_only_foreign_guards_preserve_legacy_shared_path_behavior(tmp_path: Path) -> None:
    cfg = _config(tmp_path, validations=[
        {"type": "file_exists", "path": "{workspace}/shared.md", "sheet": 1},
        {"type": "file_exists", "path": "{workspace}/shared.md", "sheet": 3},
    ])
    for sheet in build_sheets(cfg):
        assert sheet.foreign_artifacts == producers.foreign_artifacts(
            sheet.num, cfg.validations, cfg.sheet.dependencies,
        )
