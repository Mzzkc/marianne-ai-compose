"""Tests for shared log-source discovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.log_sources import (
    discover_job_log_sources,
    iter_source_lines,
    line_matches_job,
)


def _state(job_id: str, workspace: Path, status: JobStatus = JobStatus.RUNNING) -> CheckpointState:
    return CheckpointState(
        job_id=job_id,
        job_name=job_id,
        status=status,
        total_sheets=1,
        started_at=datetime.now(UTC),
        config_snapshot={"workspace": str(workspace)},
    )


def test_line_matches_job_prefers_structured_json() -> None:
    """Structured conductor logs are matched by job_id, not only text needles."""
    assert line_matches_job(json.dumps({"event": "x", "job_id": "alpha"}), "alpha")
    assert not line_matches_job(json.dumps({"event": "x", "job_id": "beta"}), "alpha")
    assert line_matches_job("raw transcript mzt-alpha-s1-a1.log", "alpha")


def test_discover_configured_conductor_log_filters_by_job(tmp_path: Path) -> None:
    """Configured conductor logs are canonical sources and filter by job_id."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conductor_log = tmp_path / "logs" / "conductor.log"
    conductor_log.parent.mkdir()
    conductor_log.write_text(
        "\n".join(
            [
                json.dumps({"event": "job.started", "job_id": "alpha"}),
                json.dumps({"event": "job.started", "job_id": "beta"}),
            ]
        )
        + "\n"
    )

    sources = discover_job_log_sources(
        "alpha",
        _state("alpha", workspace),
        db_path=tmp_path / "missing.db",
        conductor_log_path=conductor_log,
        interactive_log_root=tmp_path / "interactive",
    )

    conductor_sources = [source for source in sources if source.kind == "conductor"]
    assert len(conductor_sources) == 1
    assert conductor_sources[0].alias_state == "canonical"
    assert iter_source_lines(conductor_sources[0]) == [
        json.dumps({"event": "job.started", "job_id": "alpha"}) + "\n"
    ]


def test_discover_workspace_aliases_are_marked_compatibility(tmp_path: Path) -> None:
    """Workspace marianne.log files remain visible as compatibility aliases."""
    workspace = tmp_path / "workspace"
    log_path = workspace / "logs" / "marianne.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("INFO alpha\n")

    sources = discover_job_log_sources(
        "alpha",
        _state("alpha", workspace, status=JobStatus.COMPLETED),
        db_path=tmp_path / "missing.db",
        conductor_log_path=tmp_path / "missing-conductor.log",
        interactive_log_root=tmp_path / "interactive",
    )

    alias_sources = [source for source in sources if source.path == log_path.resolve()]
    assert len(alias_sources) == 1
    assert alias_sources[0].kind == "workspace"
    assert alias_sources[0].alias_state == "compatibility"


def test_discover_interactive_transcripts_are_raw_debug(tmp_path: Path) -> None:
    """Interactive tmux transcripts are labeled as raw debug sources."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    interactive_root = tmp_path / "interactive"
    interactive_root.mkdir()
    transcript = interactive_root / "mzt-alpha-s1-a1.log"
    transcript.write_text("provider screen\n")

    sources = discover_job_log_sources(
        "alpha",
        _state("alpha", workspace, status=JobStatus.COMPLETED),
        db_path=tmp_path / "missing.db",
        conductor_log_path=tmp_path / "missing-conductor.log",
        interactive_log_root=interactive_root,
    )

    transcript_sources = [
        source for source in sources if source.kind == "interactive_transcript"
    ]
    assert len(transcript_sources) == 1
    assert transcript_sources[0].path == transcript.resolve()
    assert transcript_sources[0].raw_state == "raw_debug"
