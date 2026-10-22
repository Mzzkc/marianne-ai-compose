"""GH #386: a sheet that writes another sheet's declared artifact is named.

The producer signal is the existing proxy (a sheet-gated ``file_exists``
validation). build_sheets gives each sheet the artifacts non-ancestor
sheets own; the musician snapshots them around execution and reports a
write as an advisory warning on the attempt result, which the baton folds
into SheetState.preflight_warnings (status/diagnose surface it).
"""

from __future__ import annotations

import time
from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.producers import declared_products, foreign_artifacts, producer_basenames
from marianne.core.sheet import build_sheets
from marianne.daemon.baton.musician import (
    _expand_artifact_paths,
    _foreign_artifact_warnings,
    _snapshot_artifacts,
)

VALIDATIONS = [
    {"type": "file_exists", "path": "{workspace}/01-research.md", "condition": "sheet_num == 1"},
    {"type": "file_exists", "path": "{workspace}/02-ledger.md", "condition": "sheet_num == 2"},
    {"type": "file_exists", "path": "{workspace}/03-review.md", "condition": "sheet_num == 3"},
    {"type": "content_contains", "path": "{workspace}/03-review.md", "pattern": "x",
     "condition": "sheet_num == 3"},
    {"type": "file_exists", "path": "{workspace}/global.md"},
]


def _config(tmp_path: Path, deps: dict | None = None) -> JobConfig:
    return JobConfig.model_validate({
        "name": "fab",
        "workspace": str(tmp_path / "ws"),
        "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 3, "dependencies": deps or {}},
        "prompt": {"template": "x"},
        "validations": VALIDATIONS,
    })


class TestProducerProxy:
    def test_declared_products_only_gated_file_exists(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path)
        assert declared_products(cfg.validations) == {
            1: ["{workspace}/01-research.md"],
            2: ["{workspace}/02-ledger.md"],
            3: ["{workspace}/03-review.md"],
        }
        assert producer_basenames(cfg.validations)["02-ledger.md"] == {2}

    def test_foreign_excludes_self_and_ancestors(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path, deps={2: [1], 3: [2]})
        v, d = cfg.validations, cfg.sheet.dependencies
        assert foreign_artifacts(1, v, d) == [
            "{workspace}/02-ledger.md", "{workspace}/03-review.md",
        ]
        assert foreign_artifacts(2, v, d) == ["{workspace}/03-review.md"]
        assert foreign_artifacts(3, v, d) == []

    def test_concurrent_sheet_is_foreign(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path, deps={3: [1, 2]})
        assert "{workspace}/02-ledger.md" in foreign_artifacts(
            1, cfg.validations, cfg.sheet.dependencies,
        )

    def test_build_sheets_carries_foreign_artifacts(self, tmp_path: Path) -> None:
        sheets = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))
        assert sheets[0].foreign_artifacts == [
            "{workspace}/02-ledger.md", "{workspace}/03-review.md",
        ]
        assert sheets[2].foreign_artifacts == []


class TestMusicianGuard:
    def test_write_by_this_attempt_is_named(self, tmp_path: Path) -> None:
        sheet = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))[0]
        sheet.workspace.mkdir()
        paths = _expand_artifact_paths(sheet, total_sheets=3, total_movements=3)
        assert [p.name for _, p in paths] == ["02-ledger.md", "03-review.md"]
        before = _snapshot_artifacts(paths)
        (sheet.workspace / "03-review.md").write_text("fabricated")
        warnings = _foreign_artifact_warnings(paths, before)
        assert len(warnings) == 1
        assert "created {workspace}/03-review.md" in warnings[0]
        assert "non-ancestor sheet" in warnings[0]

    def test_pre_existing_untouched_file_is_silent(self, tmp_path: Path) -> None:
        sheet = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))[0]
        sheet.workspace.mkdir()
        (sheet.workspace / "02-ledger.md").write_text("from a prior run")
        paths = _expand_artifact_paths(sheet, total_sheets=3, total_movements=3)
        before = _snapshot_artifacts(paths)
        assert _foreign_artifact_warnings(paths, before) == []

    def test_modification_is_named_as_modified(self, tmp_path: Path) -> None:
        sheet = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))[0]
        sheet.workspace.mkdir()
        target = sheet.workspace / "02-ledger.md"
        target.write_text("v1")
        paths = _expand_artifact_paths(sheet, total_sheets=3, total_movements=3)
        before = _snapshot_artifacts(paths)
        past = time.time() - 10
        import os
        os.utime(target, (past, past))
        before = _snapshot_artifacts(paths)
        target.write_text("v2")
        warnings = _foreign_artifact_warnings(paths, before)
        assert len(warnings) == 1 and "modified {workspace}/02-ledger.md" in warnings[0]

    def test_own_artifact_is_not_watched(self, tmp_path: Path) -> None:
        sheet = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))[2]
        assert _expand_artifact_paths(sheet, total_sheets=3, total_movements=3) == []


class TestQuarantine:
    def test_quarantine_moves_the_fabrication_under_scratch_and_says_so(
        self, tmp_path: Path,
    ) -> None:
        from marianne.core.config.workspace import scratch_root

        sheet = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))[0]
        sheet.workspace.mkdir()
        paths = _expand_artifact_paths(sheet, total_sheets=3, total_movements=3)
        before = _snapshot_artifacts(paths)
        fabricated = sheet.workspace / "03-review.md"
        fabricated.write_text("fabricated review")
        quarantine = scratch_root(sheet.workspace) / "quarantine" / "sheet-1"
        warnings = _foreign_artifact_warnings(paths, before, quarantine=quarantine)
        assert len(warnings) == 1
        assert not fabricated.exists()
        moved = quarantine / "03-review.md"
        assert moved.read_text() == "fabricated review"
        assert f"moved to {moved}" in warnings[0]

    def test_warn_policy_leaves_the_file(self, tmp_path: Path) -> None:
        sheet = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))[0]
        sheet.workspace.mkdir()
        paths = _expand_artifact_paths(sheet, total_sheets=3, total_movements=3)
        before = _snapshot_artifacts(paths)
        (sheet.workspace / "03-review.md").write_text("fabricated")
        warnings = _foreign_artifact_warnings(paths, before)
        assert (sheet.workspace / "03-review.md").exists()
        assert "moved to" not in warnings[0]

    def test_sheet_carries_the_policy_from_workspace_lifecycle(self, tmp_path: Path) -> None:
        from marianne.daemon.baton.musician import _quarantine_dir

        config = _config(tmp_path, deps={2: [1]})
        assert build_sheets(config)[0].foreign_artifact_policy == "warn"
        strict = JobConfig.model_validate({
            **config.model_dump(mode="json"),
            "workspace_lifecycle": {"foreign_artifacts": "quarantine"},
        })
        sheet = build_sheets(strict)[0]
        assert sheet.foreign_artifact_policy == "quarantine"
        assert _quarantine_dir(sheet) == (
            sheet.workspace / ".marianne-scratch" / "quarantine" / "sheet-1"
        )

    def test_failed_move_leaves_the_file_and_says_so(self, tmp_path: Path) -> None:
        sheet = build_sheets(_config(tmp_path, deps={2: [1], 3: [2]}))[0]
        sheet.workspace.mkdir()
        paths = _expand_artifact_paths(sheet, total_sheets=3, total_movements=3)
        before = _snapshot_artifacts(paths)
        (sheet.workspace / "03-review.md").write_text("fabricated")
        # A regular file where the quarantine directory must go.
        blocker = tmp_path / "blocked"
        blocker.write_text("")
        warnings = _foreign_artifact_warnings(paths, before, quarantine=blocker / "sheet-1")
        assert (sheet.workspace / "03-review.md").exists()
        assert "quarantine failed" in warnings[0] and "file left in place" in warnings[0]
