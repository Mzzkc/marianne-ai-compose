"""Shared log-source discovery for daemon-managed score runs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.constants import DAEMON_STATE_DB_PATH
from marianne.core.logging import resolve_daemon_log_path

LogSourceKind = Literal[
    "registry",
    "conductor",
    "workspace",
    "observer",
    "snapshot",
    "interactive_transcript",
]
LogSourceState = Literal["available", "no_lines", "unavailable", "stream_only"]
AliasState = Literal["canonical", "compatibility", "none"]
RawState = Literal["redacted", "raw_debug", "not_applicable"]


@dataclass(frozen=True)
class LogSource:
    """Concrete source that can contribute lines to an operator log view."""

    path: Path
    label: str
    follow: bool = False
    job_filter: str | None = None
    kind: LogSourceKind = "workspace"
    state: LogSourceState = "available"
    alias_state: AliasState = "none"
    raw_state: RawState = "redacted"

    @property
    def source_id(self) -> str:
        """Stable-ish identifier for cross-surface display and tests."""
        return f"{self.kind}:{self.label}"


def read_registry_job_metadata(
    job_id: str,
    *,
    db_path: Path = DAEMON_STATE_DB_PATH,
) -> dict[str, str | None]:
    """Read persisted job file metadata from the daemon registry."""
    path = db_path.expanduser()
    if not path.exists() or path.stat().st_size == 0:
        return {}

    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT workspace, log_path, snapshot_path, config_path
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
    except sqlite3.Error:
        return {}

    return dict(row) if row is not None else {}


def _is_relevant_to_job_window(path: Path, state: CheckpointState) -> bool:
    """Avoid treating stale shared-workspace files as current job sources."""
    if state.started_at is None:
        return True

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False

    started = state.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)

    completed = state.completed_at or datetime.now(UTC)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=UTC)

    return started.timestamp() - 3600 <= mtime <= completed.timestamp() + 3600


def line_matches_job(line: str, job_id: str) -> bool:
    """Return whether a log line belongs to a job.

    Structured JSON records are matched by their ``job_id`` field first. A
    fallback remains for historical compact/spaced JSON strings and deterministic
    interactive transcript names.
    """
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        record = None

    if isinstance(record, dict) and record.get("job_id") == job_id:
        return True

    needle_spaced = f'"job_id": "{job_id}"'
    needle_compact = f'"job_id":"{job_id}"'
    transcript_needle = f"mzt-{job_id}-"
    return (
        needle_spaced in line
        or needle_compact in line
        or transcript_needle in line
    )


def iter_source_lines(source: LogSource) -> list[str]:
    """Read source lines, applying a job filter when required."""
    with open(source.path, encoding="utf-8", errors="replace") as handle:
        if source.job_filter is None:
            return list(handle)
        return [
            line
            for line in handle
            if line_matches_job(line, source.job_filter)
        ]


def _append_source(
    sources: list[LogSource],
    seen: set[Path],
    path: Path | str | None,
    label: str,
    *,
    state: CheckpointState | None = None,
    kind: LogSourceKind = "workspace",
    follow: bool = False,
    job_filter: str | None = None,
    require_relevant: bool = False,
    require_matching_lines: bool = False,
    alias_state: AliasState = "none",
    raw_state: RawState = "redacted",
) -> None:
    if path is None:
        return

    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return
    if require_relevant and state is not None and not _is_relevant_to_job_window(
        candidate, state
    ):
        return

    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    if resolved in seen:
        return

    source = LogSource(
        path=resolved,
        label=label,
        follow=follow,
        job_filter=job_filter,
        kind=kind,
        alias_state=alias_state,
        raw_state=raw_state,
    )
    if require_matching_lines and not iter_source_lines(source):
        return
    if not follow and candidate.stat().st_size == 0:
        return

    seen.add(resolved)
    sources.append(source)


def _append_workspace_sources(
    sources: list[LogSource],
    seen: set[Path],
    workspace: Path,
    state: CheckpointState,
) -> None:
    if not workspace.is_dir():
        return

    for path, label in (
        (workspace / "marianne.log", "workspace/marianne.log"),
        (workspace / "logs" / "marianne.log", "workspace/logs/marianne.log"),
    ):
        _append_source(
            sources,
            seen,
            path,
            label,
            state=state,
            kind="workspace",
            follow=state.status == JobStatus.RUNNING,
            require_relevant=True,
            alias_state="compatibility",
        )

    _append_source(
        sources,
        seen,
        workspace / ".marianne-observer.jsonl",
        "workspace observer events",
        state=state,
        kind="observer",
        follow=state.status == JobStatus.RUNNING,
        job_filter=state.job_id,
        require_relevant=True,
        require_matching_lines=True,
    )

    for pattern, label in (("*.log", "workspace log"), ("logs/*.log", "workspace logs")):
        for path in sorted(workspace.glob(pattern)):
            _append_source(
                sources,
                seen,
                path,
                f"{label}: {path.name}",
                state=state,
                kind="workspace",
                follow=state.status == JobStatus.RUNNING,
                require_relevant=True,
                alias_state="compatibility",
            )


def _append_snapshot_sources(
    sources: list[LogSource],
    seen: set[Path],
    snapshot_path: Path,
    job_id: str,
) -> None:
    if not snapshot_path.is_dir():
        return

    _append_source(
        sources,
        seen,
        snapshot_path / ".marianne-observer.jsonl",
        "snapshot observer events",
        kind="observer",
        job_filter=job_id,
        require_matching_lines=True,
    )
    for path in sorted(snapshot_path.glob("*.log")):
        _append_source(
            sources,
            seen,
            path,
            f"snapshot log: {path.name}",
            kind="snapshot",
        )


def _append_interactive_sources(
    sources: list[LogSource],
    seen: set[Path],
    job_id: str,
    state: CheckpointState,
    *,
    interactive_log_root: Path,
) -> None:
    log_dir = interactive_log_root.expanduser()
    if not log_dir.is_dir():
        return

    def sort_key(path: Path) -> tuple[int, int, str]:
        parts = path.stem.rsplit("-a", 1)
        if len(parts) != 2:
            return (9999, 9999, path.name)
        left, attempt = parts
        sheet = left.rsplit("-s", 1)[-1]
        try:
            return (int(sheet), int(attempt), path.name)
        except ValueError:
            return (9999, 9999, path.name)

    for path in sorted(log_dir.glob(f"mzt-{job_id}-s*-a*.log"), key=sort_key):
        _append_source(
            sources,
            seen,
            path,
            f"interactive transcript: {path.name}",
            state=state,
            kind="interactive_transcript",
            require_relevant=True,
            raw_state="raw_debug",
        )


def _append_conductor_sources(
    sources: list[LogSource],
    seen: set[Path],
    job_id: str,
    state: CheckpointState,
    *,
    conductor_log_path: Path | None,
) -> None:
    candidates: list[tuple[Path, AliasState, str]] = []
    if conductor_log_path is not None:
        candidates.append((conductor_log_path.expanduser(), "canonical", "conductor events"))

    legacy_path = Path("~/.marianne/conductor.log").expanduser()
    if all(path.expanduser() != legacy_path for path, _, _ in candidates):
        candidates.append((legacy_path, "compatibility", "legacy conductor events"))

    for path, alias_state, label in candidates:
        if not path.is_file():
            continue
        source = LogSource(
            path=path.resolve(),
            label=label,
            follow=state.status == JobStatus.RUNNING,
            job_filter=job_id,
            kind="conductor",
            alias_state=alias_state,
        )
        has_lines = bool(iter_source_lines(source))
        if not has_lines and (sources or state.status != JobStatus.RUNNING):
            continue
        _append_source(
            sources,
            seen,
            source.path,
            source.label,
            kind="conductor",
            follow=source.follow,
            job_filter=source.job_filter,
            alias_state=alias_state,
        )


def discover_job_log_sources(
    job_id: str,
    state: CheckpointState,
    *,
    db_path: Path = DAEMON_STATE_DB_PATH,
    registry_metadata: dict[str, str | None] | None = None,
    conductor_log_path: Path | None = None,
    interactive_log_root: Path = Path("~/.marianne/interactive-logs"),
) -> list[LogSource]:
    """Discover authoritative and compatibility log sources for a job."""
    metadata = (
        registry_metadata
        if registry_metadata is not None
        else read_registry_job_metadata(job_id, db_path=db_path)
    )
    sources: list[LogSource] = []
    seen: set[Path] = set()

    _append_source(
        sources,
        seen,
        metadata.get("log_path"),
        "registry log_path",
        kind="registry",
        follow=state.status == JobStatus.RUNNING,
        alias_state="canonical",
    )

    workspaces: list[Path] = []
    if state.worktree_path:
        workspaces.append(Path(state.worktree_path))
    if metadata.get("workspace"):
        workspaces.append(Path(str(metadata["workspace"])))
    if isinstance(state.config_snapshot, dict):
        raw_workspace = state.config_snapshot.get("workspace")
        if raw_workspace:
            workspaces.append(Path(str(raw_workspace)))

    workspace_seen: set[Path] = set()
    for workspace in workspaces:
        try:
            resolved = workspace.expanduser().resolve()
        except OSError:
            resolved = workspace.expanduser()
        if resolved in workspace_seen:
            continue
        workspace_seen.add(resolved)
        _append_workspace_sources(sources, seen, resolved, state)

    snapshot_paths: list[Path] = []
    if metadata.get("snapshot_path"):
        snapshot_paths.append(Path(str(metadata["snapshot_path"])))
    snapshot_root = Path("~/.marianne/snapshots").expanduser() / job_id
    if snapshot_root.is_dir():
        snapshot_paths.extend(
            path for path in sorted(snapshot_root.iterdir(), reverse=True) if path.is_dir()
        )

    snapshot_seen: set[Path] = set()
    for snapshot_path in snapshot_paths:
        try:
            resolved = snapshot_path.expanduser().resolve()
        except OSError:
            resolved = snapshot_path.expanduser()
        if resolved in snapshot_seen:
            continue
        snapshot_seen.add(resolved)
        _append_snapshot_sources(sources, seen, resolved, job_id)

    _append_interactive_sources(
        sources,
        seen,
        job_id,
        state,
        interactive_log_root=interactive_log_root,
    )
    _append_conductor_sources(
        sources,
        seen,
        job_id,
        state,
        conductor_log_path=conductor_log_path or resolve_daemon_log_path(),
    )

    return sources


__all__ = [
    "AliasState",
    "LogSource",
    "LogSourceKind",
    "LogSourceState",
    "RawState",
    "discover_job_log_sources",
    "iter_source_lines",
    "line_matches_job",
    "read_registry_job_metadata",
]
