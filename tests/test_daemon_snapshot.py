"""Tests for the SnapshotManager — Phase 4 completion snapshots."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from marianne.daemon.snapshot import SnapshotManager

# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def snapshot_base(tmp_path):
    """Temporary base directory for snapshots."""
    return tmp_path / "snapshots"


@pytest.fixture()
def workspace(tmp_path):
    """Temporary workspace with typical job artifacts."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "my-job.json").write_text('{"status": "completed"}')
    (ws / "marianne.log").write_text("INFO: job completed")
    return ws


@pytest.fixture()
def mgr(snapshot_base):
    """SnapshotManager pointed at temp base dir."""
    return SnapshotManager(base_dir=snapshot_base)


# ─── Capture tests ──────────────────────────────────────────────────


class TestCapture:
    """Tests for SnapshotManager.capture()."""

    def test_capture_creates_snapshot_directory(self, mgr, workspace, snapshot_base):
        result = mgr.capture("test-job", workspace)
        assert result is not None
        assert snapshot_base.exists()
        # Snapshot should be under base/job_id/timestamp/
        job_dir = snapshot_base / "test-job"
        assert job_dir.is_dir()
        ts_dirs = list(job_dir.iterdir())
        assert len(ts_dirs) == 1
        assert ts_dirs[0].is_dir()

    def test_capture_copies_json_files(self, mgr, workspace):
        result = mgr.capture("test-job", workspace)
        snap = Path(result)
        assert (snap / "my-job.json").exists()
        assert (snap / "my-job.json").read_text() == '{"status": "completed"}'

    def test_capture_copies_log_files(self, mgr, workspace):
        result = mgr.capture("test-job", workspace)
        snap = Path(result)
        assert (snap / "marianne.log").exists()
        assert (snap / "marianne.log").read_text() == "INFO: job completed"

    def test_capture_returns_snapshot_path_string(self, mgr, workspace):
        result = mgr.capture("test-job", workspace)
        assert isinstance(result, str)
        assert "test-job" in result

    def test_capture_missing_workspace_returns_none(self, mgr, tmp_path):
        result = mgr.capture("test-job", tmp_path / "nonexistent")
        assert result is None

    def test_capture_empty_workspace_returns_none(self, mgr, tmp_path):
        empty_ws = tmp_path / "empty"
        empty_ws.mkdir()
        result = mgr.capture("test-job", empty_ws)
        assert result is None

    def test_capture_multiple_snapshots_for_same_job(self, mgr, workspace, snapshot_base):
        path1 = mgr.capture("test-job", workspace)
        # Ensure different timestamp
        time.sleep(1.1)
        path2 = mgr.capture("test-job", workspace)
        assert path1 != path2
        job_dir = snapshot_base / "test-job"
        assert len(list(job_dir.iterdir())) == 2

    def test_capture_preserves_additional_json_files(self, mgr, workspace):
        (workspace / "extra.json").write_text('{"extra": true}')
        result = mgr.capture("test-job", workspace)
        snap = Path(result)
        assert (snap / "extra.json").exists()

    def test_capture_ignores_directories_in_workspace(self, mgr, workspace):
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "nested.json").write_text("{}")
        result = mgr.capture("test-job", workspace)
        snap = Path(result)
        # Only top-level files are captured
        assert not (snap / "subdir").exists()


# ─── Cleanup tests ──────────────────────────────────────────────────


class TestCleanup:
    """Tests for TTL-based snapshot cleanup."""

    def test_cleanup_removes_old_snapshots(self, mgr, snapshot_base):
        # Create a snapshot with an old timestamp
        old_ts = str(int(time.time()) - 8 * 24 * 3600)  # 8 days ago
        old_dir = snapshot_base / "test-job" / old_ts
        old_dir.mkdir(parents=True)
        (old_dir / "state.json").write_text("{}")

        removed = mgr.cleanup(max_age_hours=168)  # 1 week
        assert removed == 1
        assert not old_dir.exists()

    def test_cleanup_preserves_recent_snapshots(self, mgr, workspace, snapshot_base):
        # Capture a fresh snapshot
        mgr.capture("test-job", workspace)
        removed = mgr.cleanup(max_age_hours=168)
        assert removed == 0
        job_dir = snapshot_base / "test-job"
        assert len(list(job_dir.iterdir())) == 1

    def test_cleanup_removes_empty_job_dirs(self, mgr, snapshot_base):
        old_ts = str(int(time.time()) - 8 * 24 * 3600)
        old_dir = snapshot_base / "cleanup-job" / old_ts
        old_dir.mkdir(parents=True)
        (old_dir / "state.json").write_text("{}")

        mgr.cleanup(max_age_hours=168)
        # Job dir should be removed since it's now empty
        assert not (snapshot_base / "cleanup-job").exists()

    def test_cleanup_no_base_dir_returns_zero(self, tmp_path):
        mgr = SnapshotManager(base_dir=tmp_path / "nonexistent")
        assert mgr.cleanup() == 0

    def test_cleanup_mixed_old_and_new(self, mgr, snapshot_base):
        job_dir = snapshot_base / "test-job"

        # Old snapshot (8 days ago)
        old_ts = str(int(time.time()) - 8 * 24 * 3600)
        old_dir = job_dir / old_ts
        old_dir.mkdir(parents=True)
        (old_dir / "state.json").write_text("{}")

        # New snapshot (just now)
        new_ts = str(int(time.time()))
        new_dir = job_dir / new_ts
        new_dir.mkdir(parents=True)
        (new_dir / "state.json").write_text("{}")

        removed = mgr.cleanup(max_age_hours=168)
        assert removed == 1
        assert not old_dir.exists()
        assert new_dir.exists()


# ─── List tests ──────────────────────────────────────────────────────


class TestListSnapshots:
    """Tests for SnapshotManager.list_snapshots()."""

    def test_list_empty_returns_empty(self, mgr):
        result = mgr.list_snapshots("nonexistent-job")
        assert result == []

    def test_list_returns_snapshots_newest_first(self, mgr, workspace):
        path1 = mgr.capture("test-job", workspace)
        time.sleep(1.1)
        path2 = mgr.capture("test-job", workspace)

        snapshots = mgr.list_snapshots("test-job")
        assert len(snapshots) == 2
        # Newest first
        assert snapshots[0]["path"] == path2
        assert snapshots[1]["path"] == path1

    def test_list_snapshot_has_required_keys(self, mgr, workspace):
        mgr.capture("test-job", workspace)
        snapshots = mgr.list_snapshots("test-job")
        assert len(snapshots) == 1
        assert "timestamp" in snapshots[0]
        assert "path" in snapshots[0]
        # Timestamp should be a numeric string
        int(snapshots[0]["timestamp"])  # Should not raise


# ─── Edge cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests."""

    def test_base_dir_property(self, mgr, snapshot_base):
        assert mgr.base_dir == snapshot_base

    def test_default_base_dir(self):
        mgr = SnapshotManager()
        assert str(mgr.base_dir).endswith("snapshots")

    def test_capture_with_only_log_file(self, mgr, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "marianne.log").write_text("some logs")
        result = mgr.capture("log-only-job", ws)
        assert result is not None
        snap = Path(result)
        assert (snap / "marianne.log").exists()


# ─── Observer JSONL capture ───────────────────────────────────────────


class TestObserverJSONLCapture:
    """Verify observer JSONL is included in snapshots."""

    def test_captures_observer_jsonl(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Create a mock observer JSONL file
        jsonl = workspace / ".marianne-observer.jsonl"
        jsonl.write_text('{"event":"observer.file_created"}\n')
        # Also create the required state file so capture doesn't bail
        (workspace / "test-job.json").write_text("{}")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None
        captured = Path(snapshot_path) / ".marianne-observer.jsonl"
        assert captured.exists()
        assert captured.read_text() == '{"event":"observer.file_created"}\n'

    def test_snapshot_without_observer_jsonl_still_works(self, tmp_path: Path) -> None:
        """Snapshots work fine without any .marianne-observer.jsonl present."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None
        captured = Path(snapshot_path) / ".marianne-observer.jsonl"
        assert not captured.exists()


# ─── Git context capture ─────────────────────────────────────────────

# Isolated git env to prevent global config interference.
_GIT_ISOLATED_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": "/tmp/nonexistent",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Run a git command with isolated config."""
    import os
    env = {**os.environ, **_GIT_ISOLATED_ENV}
    return subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        env=env,
    )


class TestGitContextCapture:
    """Verify git context capture in snapshots."""

    def test_captures_git_context_in_repo(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        # Initialize a real git repo with isolated config
        _git(["init", "-b", "main"], cwd=workspace)
        _git(["config", "user.email", "test@test.com"], cwd=workspace)
        _git(["config", "user.name", "Test"], cwd=workspace)
        (workspace / "file.txt").write_text("hello")
        _git(["add", "."], cwd=workspace)
        _git(["commit", "-m", "init"], cwd=workspace)

        (workspace / "test-job.json").write_text("{}")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None
        git_ctx = Path(snapshot_path) / "git-context.json"
        assert git_ctx.exists()
        data = json.loads(git_ctx.read_text())
        assert "head_sha" in data
        assert len(data["head_sha"]) == 40  # Full SHA
        assert "branch" in data
        assert data["branch"] == "main"
        assert "status" in data

    def test_captures_dirty_status(self, tmp_path: Path) -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        _git(["init", "-b", "main"], cwd=workspace)
        _git(["config", "user.email", "test@test.com"], cwd=workspace)
        _git(["config", "user.name", "Test"], cwd=workspace)
        (workspace / "file.txt").write_text("hello")
        _git(["add", "."], cwd=workspace)
        _git(["commit", "-m", "init"], cwd=workspace)
        # Create an uncommitted change
        (workspace / "dirty.txt").write_text("uncommitted")

        (workspace / "test-job.json").write_text("{}")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None
        git_ctx = Path(snapshot_path) / "git-context.json"
        data = json.loads(git_ctx.read_text())
        # Status should contain the dirty file
        assert "dirty.txt" in data["status"]

    def test_no_git_context_outside_repo(self, tmp_path: Path) -> None:
        workspace = tmp_path / "not-a-repo"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None
        git_ctx = Path(snapshot_path) / "git-context.json"
        assert not git_ctx.exists()  # Gracefully skipped

    def test_git_context_does_not_prevent_snapshot_on_error(self, tmp_path: Path) -> None:
        """Even if git commands fail, the snapshot itself should succeed."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")
        # No git repo → git commands fail → no git-context.json but snapshot OK
        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None


# ─── Config capture ─────────────────────────────────────────────────


class TestConfigCapture:
    """Tests for config_path parameter in SnapshotManager.capture()."""

    def test_config_captured_when_provided(self, tmp_path: Path) -> None:
        """Config file is copied into the snapshot when config_path is given."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        config = tmp_path / "my-job.yaml"
        config.write_text("sheets:\n  - prompt: hello\n")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace, config_path=config)
        assert snapshot_path is not None
        captured_config = Path(snapshot_path) / "my-job.yaml"
        assert captured_config.exists()
        assert "sheets:" in captured_config.read_text()

    def test_config_missing_file_handled_gracefully(self, tmp_path: Path) -> None:
        """Capture succeeds even if config_path points to a missing file."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        missing_config = tmp_path / "nonexistent.yaml"

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture(
            "test-job", workspace, config_path=missing_config,
        )
        assert snapshot_path is not None
        # No config file should be in the snapshot
        assert not (Path(snapshot_path) / "nonexistent.yaml").exists()

    def test_config_none_by_default(self, tmp_path: Path) -> None:
        """Capture works without config_path (backward compatible)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None


# ─── Observer summary capture ────────────────────────────────────────


class TestObserverSummaryCapture:
    """Tests for observer-summary.json generation from JSONL."""

    def test_observer_summary_generated_from_jsonl(self, tmp_path: Path) -> None:
        """Observer summary JSON is created from JSONL timeline."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        jsonl = workspace / ".marianne-observer.jsonl"
        lines = [
            '{"event":"observer.file_created","path":"/a.txt"}',
            '{"event":"observer.file_created","path":"/b.txt"}',
            '{"event":"observer.file_modified","path":"/a.txt"}',
            '{"event":"observer.process_spawned","pid":123}',
        ]
        jsonl.write_text("\n".join(lines) + "\n")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None

        summary_path = Path(snapshot_path) / "observer-summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text())
        assert summary["total_events"] == 4
        assert summary["by_type"]["observer.file_created"] == 2
        assert summary["by_type"]["observer.file_modified"] == 1
        assert summary["by_type"]["observer.process_spawned"] == 1

    def test_observer_summary_handles_missing_jsonl(self, tmp_path: Path) -> None:
        """No observer summary when JSONL file does not exist."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None
        assert not (Path(snapshot_path) / "observer-summary.json").exists()

    def test_observer_summary_handles_empty_jsonl(self, tmp_path: Path) -> None:
        """No observer summary when JSONL is empty."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")
        (workspace / ".marianne-observer.jsonl").write_text("")

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None
        assert not (Path(snapshot_path) / "observer-summary.json").exists()

    def test_observer_summary_handles_malformed_jsonl(self, tmp_path: Path) -> None:
        """Malformed JSONL lines are skipped, valid ones still counted."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test-job.json").write_text("{}")

        jsonl = workspace / ".marianne-observer.jsonl"
        jsonl.write_text(
            '{"event":"observer.file_created"}\n'
            'not valid json\n'
            '{"event":"observer.file_deleted"}\n'
        )

        manager = SnapshotManager(base_dir=tmp_path / "snapshots")
        snapshot_path = manager.capture("test-job", workspace)
        assert snapshot_path is not None

        summary_path = Path(snapshot_path) / "observer-summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text())
        assert summary["total_events"] == 2
