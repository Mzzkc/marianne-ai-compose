"""Workspace lifecycle management for Marianne jobs.

Handles archival of workspace files when --fresh is used on self-chaining jobs.
This prevents stale validation artifacts from causing false-positive validations
on subsequent iterations.
"""

from __future__ import annotations

import fnmatch
import shutil
from datetime import UTC, datetime
from pathlib import Path

from marianne.core.config import WorkspaceLifecycleConfig
from marianne.core.logging import get_logger

_logger = get_logger("workspace.lifecycle")


class WorkspaceArchiver:
    """Archives workspace files to numbered subdirectories.

    Used when --fresh flag clears state, to also move non-essential workspace
    files out of the way so validations (file_exists, command_succeeds) don't
    pass on stale artifacts from previous iterations.
    """

    def __init__(
        self, workspace: Path, config: WorkspaceLifecycleConfig
    ) -> None:
        self.workspace = workspace
        self.config = config

    def archive(self) -> Path | None:
        """Archive non-preserved workspace files.

        Returns:
            Path to the created archive directory, or None if nothing was archived.
        """
        try:
            return self._do_archive()
        except (OSError, ValueError) as e:
            _logger.warning(
                "workspace_archive_failed",
                workspace=str(self.workspace),
                error=str(e),
                exc_info=True,
            )
            return None

    def _do_archive(self) -> Path | None:
        if not self.workspace.exists():
            return None

        # Determine archive name
        archive_name = self._get_archive_name()
        archive_base = self.workspace / self.config.archive_dir
        archive_path = archive_base / archive_name

        # Handle name collision
        archive_path = self._resolve_collision(archive_path)

        # Collect files to archive (everything not preserved)
        to_archive = self._collect_archivable_items()
        if not to_archive:
            _logger.info(
                "workspace_archive_nothing",
                workspace=str(self.workspace),
            )
            return None

        # Create archive directory
        archive_path.mkdir(parents=True, exist_ok=True)

        # Move files
        archived_count = 0
        for item in to_archive:
            dest = archive_path / item.relative_to(self.workspace)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(dest))
                archived_count += 1
            except (OSError, shutil.Error) as e:
                _logger.warning(
                    "workspace_archive_item_failed",
                    item=str(item),
                    error=str(e),
                )

        if archived_count == 0:
            # Clean up empty archive directory
            try:
                shutil.rmtree(archive_path)
            except OSError as e:
                _logger.debug("empty_archive_cleanup_failed", path=str(archive_path), error=str(e))
            return None

        _logger.info(
            "workspace_archived",
            archive=str(archive_path),
            items=archived_count,
        )

        # Rotate archives if max_archives is set
        if self.config.max_archives > 0:
            self._rotate_archives(archive_base)

        return archive_path

    def _get_archive_name(self) -> str:
        """Determine archive directory name from iteration file or timestamp."""
        if self.config.archive_naming == "iteration":
            iteration_file = self.workspace / ".iteration"
            if iteration_file.exists():
                try:
                    content = iteration_file.read_text().strip()
                    iteration_num = int(content)
                    return f"iteration-{iteration_num}"
                except (ValueError, OSError):
                    _logger.warning(
                        "workspace_archive_iteration_read_failed",
                        path=str(iteration_file),
                    )
            # Fall through to timestamp if iteration file missing/corrupt

        return datetime.now(UTC).strftime("archive-%Y%m%d-%H%M%S")

    def _resolve_collision(self, archive_path: Path) -> Path:
        """Add suffix if archive directory already exists."""
        if not archive_path.exists():
            return archive_path

        for i in range(1, 100):
            candidate = archive_path.with_name(f"{archive_path.name}-{i}")
            if not candidate.exists():
                return candidate

        # Extremely unlikely — fall back to timestamp suffix
        ts = datetime.now(UTC).strftime("%H%M%S")
        return archive_path.with_name(f"{archive_path.name}-{ts}")

    def _collect_archivable_items(self) -> list[Path]:
        """Collect top-level workspace items that should be archived."""
        items: list[Path] = []

        for item in sorted(self.workspace.iterdir()):
            rel = item.relative_to(self.workspace)
            rel_str = str(rel)

            if self._is_preserved(rel_str, item.is_dir()):
                continue

            items.append(item)

        return items

    def _is_preserved(self, rel_path: str, is_dir: bool) -> bool:
        """Check if a path matches any preserve pattern."""
        for pattern in self.config.preserve_patterns:
            # Direct match on the top-level name
            if fnmatch.fnmatch(rel_path, pattern):
                return True

            # For directory patterns like "archive/**", check if the
            # top-level item is the directory prefix
            if pattern.endswith("/**"):
                dir_prefix = pattern[:-3]  # Strip /**
                if rel_path == dir_prefix:
                    return True
                if fnmatch.fnmatch(rel_path, dir_prefix):
                    return True

        return False

    def _rotate_archives(self, archive_base: Path) -> None:
        """Delete oldest archives when max_archives is exceeded."""
        if not archive_base.exists():
            return

        archives = sorted(
            [d for d in archive_base.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
        )

        while len(archives) > self.config.max_archives:
            oldest = archives.pop(0)
            try:
                shutil.rmtree(oldest)
                _logger.info(
                    "workspace_archive_rotated",
                    removed=str(oldest),
                )
            except (OSError, shutil.Error) as e:
                _logger.warning(
                    "workspace_archive_rotation_failed",
                    path=str(oldest),
                    error=str(e),
                )
