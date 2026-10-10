"""Pure sheet expansion and existing-score identity (PO-C2/C3/C4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from marianne.core.config.classes import ClassEntry, ClassSnapshot, ClassSnapshotEntry
from marianne.core.config.job import JobConfig
from marianne.core.sheet import build_sheets
from marianne.instruments.classes import load_class_map


def _snapshot(**chains: list[str | dict[str, object]]) -> ClassSnapshot:
    return ClassSnapshot.from_classes(
        {
            name: ClassSnapshotEntry(
                chain=[ClassEntry.model_validate(entry) for entry in chain],
                source_layer="user",
            )
            for name, chain in chains.items()
        },
        [],
    )


def _job(**overrides: object) -> JobConfig:
    data: dict[str, object] = {
        "name": "classes-resolver", "workspace": "/tmp/classes-resolver",
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "Work"},
    }
    data.update(overrides)
    return JobConfig.model_validate(data)


def test_primary_tail_inline_fallback_models_and_repeats() -> None:
    classes = _snapshot(
        strong=[
            {"profile": "claude-code", "config": {"model": "opus"}},
            {"profile": "codex-cli", "config": {"model": "gpt-6-sol"}},
        ],
        fast=["antigravity", "codex-cli"],
    )
    sheet = build_sheets(
        _job(instrument="strong", instrument_fallbacks=["fast", "codex-cli", "cli"]),
        classes=classes,
    )[0]
    assert sheet.instrument_name == "claude-code"
    assert sheet.instrument_config["model"] == "opus"
    assert sheet.instrument_fallbacks == ["codex-cli", "antigravity", "codex-cli", "cli"]
    assert sheet.instrument_fallback_configs == [
        {"model": "gpt-6-sol"}, {}, {}, {},
    ]
    assert sheet.instrument_resolution is not None
    assert sheet.instrument_resolution.requested == "strong"
    assert sheet.instrument_resolution.dropped_duplicates == ["codex-cli"]


def test_alias_wins_over_class_with_same_name() -> None:
    sheet = build_sheets(
        _job(
            instrument="strong",
            instruments={"strong": {"profile": "cli", "config": {"model": "fixed"}}},
        ),
        classes=_snapshot(strong=["claude-code", "codex-cli"]),
    )[0]
    assert sheet.instrument_name == "cli"
    assert sheet.instrument_resolution is None
    assert sheet.instrument_config["model"] == "fixed"


def test_all_tracked_scores_keep_sheet_identity_under_snapshots() -> None:
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "ls-files", "examples/**/*.yaml", "scores/*.yaml"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert len(tracked) >= 60
    empty = _snapshot()
    default = load_class_map(
        user_path=root / "no-user-classes.yaml", venue_path=root / "no-venue-classes.yaml",
    )
    packaged = default.snapshot(set(default.classes))
    score_count = 0
    for relative in tracked:
        raw = yaml.safe_load((root / relative).read_text())
        if relative.endswith("_scheduler/conductor-snippet.yaml"):
            assert isinstance(raw, dict) and set(raw) == {"scheduler"}
            continue
        score_count += 1
        config = JobConfig.from_yaml(root / relative)
        original = [sheet.model_dump(mode="json") for sheet in build_sheets(config)]
        assert [
            sheet.model_dump(mode="json") for sheet in build_sheets(config, classes=empty)
        ] == original, relative
        assert [
            sheet.model_dump(mode="json") for sheet in build_sheets(config, classes=packaged)
        ] == original, relative
    assert score_count == len(tracked) - 1
