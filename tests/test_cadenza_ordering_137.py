"""#137: cadenza-ordering validation using existing signals (no new config).

Composer decision (2026-06-12): detect the ordering bug WITHOUT adding a
`produces:` field. The insight — a ``file_exists`` validation gated to a
sheet is the author already declaring that sheet produces the file.
Cross-referenced with cadenzas (which sheet READS a file) and the
dependency DAG (run order), we can flag: a sheet's cadenza reads a file
whose producer is NOT a DAG-ancestor (not guaranteed to have run yet).

WARNING-level only: ``file_exists`` is a producer PROXY, not a guarantee,
so this never false-ERRORs a valid score (the failure mode that killed
the path-heuristic approach).
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.validation.checks.paths import (
    CadenzaOrderingCheck,
    PreludeCadenzaFileCheck,
)


def _config(*, cadenzas: dict, validations: list, dependencies: dict) -> JobConfig:
    return JobConfig.model_validate(
        {
            "name": "ord",
            "workspace": "./ws",
            "instrument": "claude-code",
            "sheet": {
                "size": 1,
                "total_items": 3,
                "cadenzas": cadenzas,
                "dependencies": dependencies,
            },
            "prompt": {"template": "x"},
            "validations": validations,
        }
    )


def _run(config: JobConfig) -> list:
    return CadenzaOrderingCheck().check(config, Path("s.yaml"), "")


class TestCadenzaOrdering:
    def test_producer_is_ancestor_no_warning(self) -> None:
        # Sheet 1 produces report.md; sheet 2 depends on 1 and reads it. Safe.
        cfg = _config(
            cadenzas={2: [{"file": "report.md", "as": "context"}]},
            validations=[
                {"type": "file_exists", "path": "report.md", "sheet": 1}
            ],
            dependencies={2: [1]},
        )
        assert _run(cfg) == []

    def test_producer_runs_later_warns(self) -> None:
        # Sheet 2 reads report.md, but report.md is only produced at sheet 3
        # (which depends on 2) — the cadenza reads it before it exists.
        cfg = _config(
            cadenzas={2: [{"file": "report.md", "as": "context"}]},
            validations=[
                {"type": "file_exists", "path": "report.md", "sheet": 3}
            ],
            dependencies={3: [2]},
        )
        issues = _run(cfg)
        assert len(issues) == 1
        assert issues[0].severity.value == "warning"
        assert "report.md" in issues[0].message

    def test_concurrent_producer_warns(self) -> None:
        # Sheets 2 and 3 both depend on 1 (concurrent). Sheet 2 reads a file
        # produced by sheet 3 — not guaranteed to have run.
        cfg = _config(
            cadenzas={2: [{"file": "out.md", "as": "context"}]},
            validations=[{"type": "file_exists", "path": "out.md", "sheet": 3}],
            dependencies={2: [1], 3: [1]},
        )
        issues = _run(cfg)
        assert len(issues) == 1
        assert "out.md" in issues[0].message

    def test_no_producer_signal_no_warning(self) -> None:
        # A cadenza file with no file_exists validation anywhere: no producer
        # signal to reason about — V108 (disk existence) handles that case.
        cfg = _config(
            cadenzas={2: [{"file": "context.md", "as": "context"}]},
            validations=[],
            dependencies={2: [1]},
        )
        assert _run(cfg) == []

    def test_basename_match_through_workspace_prefix(self) -> None:
        # Cadenza reads {workspace}/report.md; validation checks report.md.
        # Same file by basename — the ordering bug is still detected.
        cfg = _config(
            cadenzas={2: [{"file": "{workspace}/report.md", "as": "context"}]},
            validations=[{"type": "file_exists", "path": "report.md", "sheet": 3}],
            dependencies={3: [2]},
        )
        assert len(_run(cfg)) == 1

    def test_directory_cadenza_ignored(self) -> None:
        # Directory cadenzas inject a whole dir — no single producer file to
        # reason about; skip them (no false positives).
        cfg = _config(
            cadenzas={2: [{"directory": "ctx/", "as": "context"}]},
            validations=[{"type": "file_exists", "path": "ctx/a.md", "sheet": 3}],
            dependencies={3: [2]},
        )
        assert _run(cfg) == []

    def test_non_file_exists_validation_ignored(self) -> None:
        # Only file_exists is a producer proxy; command_succeeds is not.
        cfg = _config(
            cadenzas={2: [{"file": "report.md", "as": "context"}]},
            validations=[
                {"type": "command_succeeds", "command": "true", "sheet": 3}
            ],
            dependencies={3: [2]},
        )
        assert _run(cfg) == []


def _run_v108(config: JobConfig) -> list:
    return PreludeCadenzaFileCheck().check(config, Path("s.yaml"), "")


class TestV108Suppression:
    """#137 point 5: V108 must not false-warn that a cadenza file is missing
    when a stage is declared to produce it at runtime. The ordering of that
    producer is V109's concern, not the disk-existence check's."""

    def test_suppressed_for_produced_cadenza_file(self) -> None:
        # report.md is absent on disk but sheet 1 is declared to produce it;
        # sheet 2 (depends on 1) reads it. V108 must stay silent — the file is
        # generated at runtime, not a missing static asset.
        cfg = _config(
            cadenzas={2: [{"file": "report.md", "as": "context"}]},
            validations=[{"type": "file_exists", "path": "report.md", "sheet": 1}],
            dependencies={2: [1]},
        )
        v108 = [i for i in _run_v108(cfg) if i.check_id == "V108"]
        assert v108 == []

    def test_still_warns_for_unproduced_missing_file(self) -> None:
        # No producer signal for missing.md: V108's disk-existence warning
        # must still fire (suppression is producer-gated, not blanket).
        cfg = _config(
            cadenzas={2: [{"file": "missing.md", "as": "context"}]},
            validations=[],
            dependencies={2: [1]},
        )
        v108 = [i for i in _run_v108(cfg) if i.check_id == "V108"]
        assert len(v108) == 1
        assert "missing.md" in v108[0].message

    def test_suppression_does_not_hide_ordering_bug(self) -> None:
        # A produced-but-too-late file: V108 suppresses its not-found noise,
        # but V109 still raises the ordering warning. The bug stays visible.
        cfg = _config(
            cadenzas={2: [{"file": "late.md", "as": "context"}]},
            validations=[{"type": "file_exists", "path": "late.md", "sheet": 3}],
            dependencies={3: [2]},
        )
        v108 = [i for i in _run_v108(cfg) if i.check_id == "V108"]
        v109 = _run(cfg)
        assert v108 == []  # disk-existence noise suppressed
        assert len(v109) == 1  # ordering bug still surfaced
        assert "late.md" in v109[0].message
