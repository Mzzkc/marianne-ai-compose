"""Tests for marianne.workspace.lifecycle module."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from marianne.core.config import WorkspaceLifecycleConfig
from marianne.workspace.lifecycle import WorkspaceArchiver


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace directory with typical iteration artifacts."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def default_config() -> WorkspaceLifecycleConfig:
    return WorkspaceLifecycleConfig(archive_on_fresh=True)


class TestArchiveNaming:
    """Tests for archive directory naming."""

    def test_iteration_file_present(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """When .iteration file exists, archive is named iteration-N."""
        (workspace / ".iteration").write_text("3")
        (workspace / "01-report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        assert result.name == "iteration-3"

    def test_iteration_file_missing_falls_back_to_timestamp(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """Without .iteration file, falls back to timestamp naming."""
        (workspace / "01-report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        assert result.name.startswith("archive-")

    def test_iteration_file_corrupt(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """Corrupt .iteration file falls back to timestamp naming."""
        (workspace / ".iteration").write_text("not-a-number")
        (workspace / "01-report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        assert result.name.startswith("archive-")

    def test_timestamp_naming_mode(self, workspace: Path):
        """Explicit timestamp naming mode uses timestamp format."""
        config = WorkspaceLifecycleConfig(
            archive_on_fresh=True,
            archive_naming="timestamp",
        )
        (workspace / ".iteration").write_text("5")
        (workspace / "01-report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, config)
        result = archiver.archive()

        # Even with .iteration present, timestamp mode uses timestamp
        assert result.name.startswith("archive-")


class TestFilePreservation:
    """Tests for file preservation vs archival."""

    def test_iteration_file_preserved(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """The .iteration file is preserved in workspace, not moved."""
        (workspace / ".iteration").write_text("1")
        (workspace / "01-report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        archiver.archive()

        assert (workspace / ".iteration").exists()
        assert not (workspace / "01-report.md").exists()

    def test_marianne_state_files_preserved(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """Files matching .marianne-* are preserved."""
        (workspace / ".marianne-state.db").write_text("")
        (workspace / ".marianne-outcomes.json").write_text("{}")
        (workspace / "05-plan.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        archiver.archive()

        assert (workspace / ".marianne-state.db").exists()
        assert (workspace / ".marianne-outcomes.json").exists()
        assert not (workspace / "05-plan.md").exists()

    def test_coverage_file_preserved(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """.coverage file is preserved."""
        (workspace / ".coverage").write_text("")
        (workspace / "report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        archiver.archive()

        assert (workspace / ".coverage").exists()

    def test_archive_directory_preserved(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """The archive/ directory itself is never archived."""
        archive_dir = workspace / "archive" / "iteration-1"
        archive_dir.mkdir(parents=True)
        (archive_dir / "old-report.md").write_text("old data")
        (workspace / "new-report.md").write_text("new data")

        archiver = WorkspaceArchiver(workspace, default_config)
        archiver.archive()

        # Old archive untouched
        assert (archive_dir / "old-report.md").exists()
        # New file archived
        assert not (workspace / "new-report.md").exists()

    def test_worktrees_directory_preserved(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """.worktrees/ directory is preserved."""
        wt = workspace / ".worktrees"
        wt.mkdir()
        (wt / "job-1").mkdir()
        (workspace / "report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        archiver.archive()

        assert (wt / "job-1").exists()

    def test_non_preserved_files_archived(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """Regular workspace files are moved to archive."""
        (workspace / ".iteration").write_text("2")
        (workspace / "01-architecture-review.md").write_text("review")
        (workspace / "02-test-coverage.md").write_text("coverage")
        (workspace / "04-discovery.yaml").write_text("yaml")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        # Files are in archive
        assert (result / "01-architecture-review.md").exists()
        assert (result / "02-test-coverage.md").exists()
        assert (result / "04-discovery.yaml").exists()
        # Files are NOT in workspace
        assert not (workspace / "01-architecture-review.md").exists()
        assert not (workspace / "02-test-coverage.md").exists()
        assert not (workspace / "04-discovery.yaml").exists()

    def test_custom_preserve_patterns(self, workspace: Path):
        """Custom preserve patterns are respected."""
        config = WorkspaceLifecycleConfig(
            archive_on_fresh=True,
            preserve_patterns=[".iteration", "keep-this.txt"],
        )
        (workspace / ".iteration").write_text("1")
        (workspace / "keep-this.txt").write_text("keep")
        (workspace / "remove-this.txt").write_text("remove")

        archiver = WorkspaceArchiver(workspace, config)
        archiver.archive()

        assert (workspace / "keep-this.txt").exists()
        assert not (workspace / "remove-this.txt").exists()


class TestEmptyWorkspace:
    """Tests for edge cases with empty/nonexistent workspaces."""

    def test_empty_workspace(self, workspace: Path, default_config: WorkspaceLifecycleConfig):
        """Empty workspace returns None (nothing to archive)."""
        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        assert result is None

    def test_workspace_only_preserved_files(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """Workspace with only preserved files returns None."""
        (workspace / ".iteration").write_text("5")
        (workspace / ".coverage").write_text("")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        assert result is None

    def test_nonexistent_workspace(self, tmp_path: Path, default_config: WorkspaceLifecycleConfig):
        """Nonexistent workspace returns None."""
        archiver = WorkspaceArchiver(tmp_path / "nonexistent", default_config)
        result = archiver.archive()

        assert result is None


class TestArchiveRotation:
    """Tests for max_archives rotation."""

    def test_rotation_deletes_oldest(self, workspace: Path):
        """When max_archives is exceeded, oldest archives are deleted."""
        import os
        import time

        config = WorkspaceLifecycleConfig(
            archive_on_fresh=True,
            max_archives=2,
        )
        # Create two existing archives with explicitly different mtimes
        archive_base = workspace / "archive"
        old = archive_base / "iteration-1"
        old.mkdir(parents=True)
        (old / "data.md").write_text("old")
        # Set old archive to 100 seconds ago
        old_time = time.time() - 100
        os.utime(old, (old_time, old_time))

        mid = archive_base / "iteration-2"
        mid.mkdir()
        (mid / "data.md").write_text("mid")
        # Set mid archive to 50 seconds ago
        mid_time = time.time() - 50
        os.utime(mid, (mid_time, mid_time))

        # Create a new file to archive
        (workspace / ".iteration").write_text("3")
        (workspace / "report.md").write_text("new")

        archiver = WorkspaceArchiver(workspace, config)
        archiver.archive()

        # Should have 2 archives total (mid + new), oldest deleted
        remaining = sorted(d.name for d in archive_base.iterdir() if d.is_dir())
        assert len(remaining) == 2
        assert "iteration-1" not in remaining

    def test_no_rotation_when_unlimited(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """max_archives=0 means unlimited, no rotation."""
        archive_base = workspace / "archive"
        for i in range(1, 6):
            d = archive_base / f"iteration-{i}"
            d.mkdir(parents=True)
            (d / "data.md").write_text(f"iter-{i}")

        (workspace / ".iteration").write_text("6")
        (workspace / "report.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        archiver.archive()

        # All 6 archives should exist (5 old + 1 new)
        dirs = [d for d in archive_base.iterdir() if d.is_dir()]
        assert len(dirs) == 6


class TestNameCollision:
    """Tests for archive name collision handling."""

    def test_collision_adds_suffix(self, workspace: Path, default_config: WorkspaceLifecycleConfig):
        """If archive name already exists, adds numeric suffix."""
        (workspace / ".iteration").write_text("1")
        # Pre-create the collision
        collision = workspace / "archive" / "iteration-1"
        collision.mkdir(parents=True)
        (collision / "old.md").write_text("old data")

        (workspace / "report.md").write_text("new data")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        assert result.name == "iteration-1-1"
        # Old archive untouched
        assert (collision / "old.md").exists()


class TestErrorTolerance:
    """Tests for error handling (archive failures should warn, not crash)."""

    def test_archive_returns_none_on_error(
        self, tmp_path: Path, default_config: WorkspaceLifecycleConfig
    ):
        """Archive errors are caught and return None."""
        # Use a path that exists but we can't write to
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "report.md").write_text("data")

        # Make archive dir a file to cause mkdir to fail
        archive_blocker = workspace / "archive"
        archive_blocker.write_text("not a directory")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        # Should return None, not raise
        assert result is None

    def test_item_move_failure_continues_other_items(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """When one file fails to move, other files are still archived."""

        (workspace / ".iteration").write_text("1")
        (workspace / "file-a.md").write_text("aaa")
        (workspace / "file-b.md").write_text("bbb")

        original_move = shutil.move

        def failing_move(src, dst, *args, **kwargs):
            if "file-a.md" in str(src):
                raise OSError("Permission denied (mock)")
            return original_move(src, dst, *args, **kwargs)

        archiver = WorkspaceArchiver(workspace, default_config)
        with patch("shutil.move", side_effect=failing_move):
            result = archiver.archive()

        # file-b should have been archived despite file-a failing
        assert result is not None
        assert (result / "file-b.md").exists()

    def test_programming_error_in_item_move_propagates(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """TypeError/ValueError during move propagate (not caught by narrowed handler)."""

        (workspace / ".iteration").write_text("1")
        (workspace / "file.md").write_text("data")

        archiver = WorkspaceArchiver(workspace, default_config)
        with patch("shutil.move", side_effect=TypeError("unexpected type")), \
             pytest.raises(TypeError, match="unexpected type"):
            archiver._do_archive()

    def test_programming_error_in_rotation_propagates(
        self, workspace: Path,
    ):
        """TypeError during rotation propagates (not caught by narrowed handler)."""

        config = WorkspaceLifecycleConfig(archive_on_fresh=True, max_archives=1)

        # Create two existing archives and a new file to trigger rotation
        archive_base = workspace / "archive"
        (archive_base / "iteration-1").mkdir(parents=True)
        (archive_base / "iteration-1" / "data.md").write_text("old")
        (archive_base / "iteration-2").mkdir(parents=True)
        (archive_base / "iteration-2" / "data.md").write_text("mid")
        (workspace / ".iteration").write_text("3")
        (workspace / "report.md").write_text("new")

        archiver = WorkspaceArchiver(workspace, config)
        with patch("shutil.rmtree", side_effect=TypeError("unexpected type")), \
             pytest.raises(TypeError, match="unexpected type"):
            archiver._do_archive()


class TestConfigDefaults:
    """Tests for WorkspaceLifecycleConfig model."""

    def test_defaults(self):
        config = WorkspaceLifecycleConfig()
        assert config.archive_on_fresh is False
        assert config.archive_dir == "archive"
        assert config.archive_naming == "iteration"
        assert config.max_archives == 0
        assert ".iteration" in config.preserve_patterns
        assert ".marianne-*" in config.preserve_patterns

    def test_custom_archive_dir(self):
        config = WorkspaceLifecycleConfig(archive_dir="history")
        assert config.archive_dir == "history"

    def test_config_in_job_config(self, tmp_path: Path):
        """WorkspaceLifecycleConfig is accessible through JobConfig."""
        from marianne.core.config import JobConfig

        config = JobConfig(
            name="test",
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "test"},
            workspace_lifecycle={"archive_on_fresh": True, "max_archives": 5},
        )
        assert config.workspace_lifecycle.archive_on_fresh is True
        assert config.workspace_lifecycle.max_archives == 5

    def test_config_from_yaml(self, tmp_path: Path):
        """WorkspaceLifecycleConfig loads from YAML correctly."""
        from marianne.core.config import JobConfig

        yaml_content = """
name: test-lifecycle
sheet:
  size: 1
  total_items: 3
prompt:
  template: "test {{ sheet_num }}"
workspace_lifecycle:
  archive_on_fresh: true
  archive_dir: old-runs
  max_archives: 10
  preserve_patterns:
    - ".iteration"
    - "important.txt"
"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(yaml_content)
        config = JobConfig.from_yaml(yaml_file)

        assert config.workspace_lifecycle.archive_on_fresh is True
        assert config.workspace_lifecycle.archive_dir == "old-runs"
        assert config.workspace_lifecycle.max_archives == 10
        assert "important.txt" in config.workspace_lifecycle.preserve_patterns


class TestSubdirectoryArchival:
    """Tests for archival of subdirectories within workspace."""

    def test_subdirectories_are_archived(
        self, workspace: Path, default_config: WorkspaceLifecycleConfig
    ):
        """Subdirectories in workspace are also archived."""
        (workspace / ".iteration").write_text("1")
        inner = workspace / "inner-run"
        inner.mkdir()
        (inner / "result.md").write_text("inner data")
        (workspace / "report.md").write_text("outer data")

        archiver = WorkspaceArchiver(workspace, default_config)
        result = archiver.archive()

        assert (result / "inner-run" / "result.md").exists()
        assert (result / "report.md").exists()
        assert not (workspace / "inner-run").exists()
        assert not (workspace / "report.md").exists()
