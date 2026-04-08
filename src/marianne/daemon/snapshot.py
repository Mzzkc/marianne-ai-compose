"""Completion snapshot manager for the Marianne daemon.

Captures durable snapshots of workspace artifacts at job completion
and manages TTL-based cleanup to prevent unbounded storage growth.

Snapshots are stored under ``~/.marianne/snapshots/{job_id}/{timestamp}/``
and include key artifacts: state JSON, logs, and validation results.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from marianne.core.logging import get_logger

_logger = get_logger("daemon.snapshot")

# File patterns to capture in a snapshot (glob patterns relative to workspace).
_CAPTURE_PATTERNS = [
    "*.json",                   # State files (e.g., {job_id}.json)
    "*.yaml",                   # Job config files
    "*.yml",                    # Job config files (alt extension)
    "marianne.log",               # Job execution log
    "*.log",                    # Any additional log files
    ".marianne-observer.jsonl",   # Observer event timeline
]


class SnapshotManager:
    """Captures and manages completion snapshots for daemon jobs.

    Snapshots are lightweight copies of key workspace artifacts,
    stored under a persistent directory with TTL-based cleanup.

    Usage::

        manager = SnapshotManager(base_dir=Path("~/.marianne/snapshots"))
        path = manager.capture("my-job", Path("/workspace"))
        manager.cleanup(max_age_hours=168)
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = (base_dir or Path("~/.marianne/snapshots")).expanduser()

    @property
    def base_dir(self) -> Path:
        """Base directory for all snapshots."""
        return self._base_dir

    def capture(
        self,
        job_id: str,
        workspace: Path,
        config_path: Path | None = None,
    ) -> str | None:
        """Capture a snapshot of workspace artifacts at job completion.

        Args:
            job_id: The job identifier.
            workspace: Path to the job's workspace directory.
            config_path: Optional path to the job config file.
                If provided and the file exists, it is copied into the
                snapshot directory.

        Returns:
            The snapshot directory path as a string, or None if capture
            failed (e.g., workspace doesn't exist or is empty).
        """
        if not workspace.is_dir():
            _logger.warning(
                "snapshot.workspace_missing",
                job_id=job_id,
                workspace=str(workspace),
            )
            return None

        timestamp = str(int(time.time()))
        snapshot_dir = self._base_dir / job_id / timestamp
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        for pattern in _CAPTURE_PATTERNS:
            for src in workspace.glob(pattern):
                if not src.is_file():
                    continue
                dst = snapshot_dir / src.name
                # Avoid overwriting if multiple patterns match the same file
                if dst.exists():
                    continue
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                except OSError as exc:
                    _logger.warning(
                        "snapshot.copy_failed",
                        job_id=job_id,
                        file=src.name,
                        error=str(exc),
                    )

        if copied == 0:
            # Remove empty snapshot dir if nothing was captured
            try:
                snapshot_dir.rmdir()
            except OSError:
                pass
            _logger.info(
                "snapshot.empty",
                job_id=job_id,
                workspace=str(workspace),
            )
            return None

        # Enrich snapshot with git context (best-effort, non-fatal).
        self._capture_git_context(workspace, snapshot_dir)

        # Capture observer activity summary from JSONL timeline.
        self._capture_observer_summary(workspace, snapshot_dir)

        # Copy config file into snapshot if provided.
        if config_path is not None and config_path.is_file():
            try:
                shutil.copy2(config_path, snapshot_dir / config_path.name)
                copied += 1
            except OSError as exc:
                _logger.warning(
                    "snapshot.config_copy_failed",
                    file=str(config_path),
                    error=str(exc),
                )

        _logger.info(
            "snapshot.captured",
            job_id=job_id,
            path=str(snapshot_dir),
            files=copied,
        )
        return str(snapshot_dir)

    def cleanup(self, max_age_hours: int = 168) -> int:
        """Remove snapshots older than the given TTL.

        Args:
            max_age_hours: Maximum age in hours. Snapshots older than
                this are deleted. Defaults to 168 (1 week).

        Returns:
            Number of snapshot directories removed.
        """
        if not self._base_dir.is_dir():
            return 0

        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0

        # Walk: base_dir / {job_id} / {timestamp} /
        for job_dir in self._safe_iterdir(self._base_dir):
            if not job_dir.is_dir():
                continue

            for ts_dir in self._safe_iterdir(job_dir):
                if not ts_dir.is_dir():
                    continue

                # Timestamp is the directory name
                try:
                    ts = int(ts_dir.name)
                except ValueError:
                    continue

                if ts < cutoff:
                    try:
                        shutil.rmtree(ts_dir)
                        removed += 1
                    except OSError as exc:
                        _logger.warning(
                            "snapshot.cleanup_failed",
                            path=str(ts_dir),
                            error=str(exc),
                        )

            # Remove empty job directories
            try:
                if job_dir.is_dir() and not any(job_dir.iterdir()):
                    job_dir.rmdir()
            except OSError:
                pass

        if removed > 0:
            _logger.info("snapshot.cleanup", removed=removed)
        return removed

    def list_snapshots(self, job_id: str) -> list[dict[str, str]]:
        """List all snapshots for a given job.

        Args:
            job_id: The job identifier.

        Returns:
            List of dicts with ``timestamp`` and ``path`` keys,
            sorted newest-first.
        """
        job_dir = self._base_dir / job_id
        if not job_dir.is_dir():
            return []

        snapshots: list[dict[str, str]] = []
        for ts_dir in self._safe_iterdir(job_dir):
            if not ts_dir.is_dir():
                continue
            try:
                int(ts_dir.name)  # Validate timestamp format
            except ValueError:
                continue
            snapshots.append({
                "timestamp": ts_dir.name,
                "path": str(ts_dir),
            })

        snapshots.sort(key=lambda s: s["timestamp"], reverse=True)
        return snapshots

    @staticmethod
    def _capture_git_context(workspace: Path, snapshot_dir: Path) -> None:
        """Capture git repo context into ``git-context.json``.

        Runs ``git rev-parse`` and ``git status --porcelain`` in the
        workspace directory. If the workspace is not a git repo or the
        commands fail, the method silently returns without creating the
        file.

        Note: This uses synchronous subprocess calls because ``capture()``
        is itself synchronous. For large repos ``git status`` may block
        for a few seconds. A 10-second timeout prevents unbounded waits.
        """
        _GIT_TIMEOUT = 10  # seconds

        def _run_git(*args: str) -> str | None:
            try:
                result = subprocess.run(  # noqa: S603
                    ["git", *args],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=_GIT_TIMEOUT,
                )
                if result.returncode != 0:
                    return None
                return result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                return None

        # Quick check: is this a git repo?
        head_sha = _run_git("rev-parse", "HEAD")
        if head_sha is None:
            return

        branch = _run_git("rev-parse", "--abbrev-ref", "HEAD") or ""
        status = _run_git("status", "--porcelain") or ""

        context = {
            "head_sha": head_sha,
            "branch": branch,
            "status": status,
        }

        try:
            dst = snapshot_dir / "git-context.json"
            dst.write_text(json.dumps(context, indent=2))
        except OSError as exc:
            _logger.warning(
                "snapshot.git_context_failed",
                error=str(exc),
            )

    @staticmethod
    def _capture_observer_summary(workspace: Path, snapshot_dir: Path) -> None:
        """Generate a summary of observer activity from the JSONL timeline.

        Reads ``.marianne-observer.jsonl`` line-by-line, counts events by type
        (files created/modified/deleted, processes spawned/exited), and writes
        ``observer-summary.json`` to the snapshot directory.

        If the JSONL file does not exist or is unreadable, the method silently
        returns without creating the summary file.
        """
        jsonl_path = workspace / ".marianne-observer.jsonl"
        if not jsonl_path.is_file():
            return

        counts: dict[str, int] = {}
        try:
            with jsonl_path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_type = record.get("event", "unknown")
                    counts[event_type] = counts.get(event_type, 0) + 1
        except OSError:
            return

        if not counts:
            return

        summary = {
            "total_events": sum(counts.values()),
            "by_type": counts,
        }

        try:
            dst = snapshot_dir / "observer-summary.json"
            dst.write_text(json.dumps(summary, indent=2))
        except OSError as exc:
            _logger.warning(
                "snapshot.observer_summary_failed",
                error=str(exc),
            )

    @staticmethod
    def _safe_iterdir(path: Path) -> list[Path]:
        """Iterate a directory, returning empty list on errors."""
        try:
            return list(path.iterdir())
        except OSError:
            return []
