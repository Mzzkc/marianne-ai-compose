"""#197: per-JOB git worktree isolation for the baton.

Composer decision (2026-06-12): per-job (not per-sheet) isolation — one
worktree per job, so sheets within a job still share a workspace and
F-210 cross-sheet context is preserved, while concurrent jobs get
filesystem isolation. Gated on ``isolation.enabled`` (default off →
no behavior change). The GitWorktreeManager already exists and is
tested; this wires it into the job lifecycle (setup before dispatch,
cleanup on completion, fallback-on-error).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.daemon.manager import (
    JobManager,
    _cleanup_worktree_isolation,
    _setup_worktree_isolation,
)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=repo, check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
    return repo


def _config(workspace: Path, *, enabled: bool, **iso: object) -> JobConfig:
    return JobConfig.model_validate(
        {
            "name": "iso-job",
            "workspace": str(workspace),
            "instrument": "claude-code",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "x"},
            "isolation": {"enabled": enabled, **iso},
        }
    )


def _manager() -> JobManager:
    mgr = JobManager.__new__(JobManager)
    mgr._job_worktrees = {}
    return mgr


class TestWorktreeSetup:
    async def test_disabled_is_noop(self, tmp_path: Path) -> None:
        mgr = _manager()
        cfg = _config(tmp_path, enabled=False)
        out = await _setup_worktree_isolation("j", cfg, mgr._job_worktrees)
        assert out.workspace == cfg.workspace  # unchanged
        assert "j" not in mgr._job_worktrees

    async def test_enabled_creates_worktree_and_overrides_workspace(
        self, tmp_path: Path
    ) -> None:
        repo = _git_repo(tmp_path)
        mgr = _manager()
        cfg = _config(repo, enabled=True)

        out = await _setup_worktree_isolation("job-a", cfg, mgr._job_worktrees)

        assert out.workspace != repo  # workspace redirected to the worktree
        assert out.workspace.exists()
        assert "job-a" in mgr._job_worktrees
        assert mgr._job_worktrees["job-a"] == out.workspace
        # The worktree is a real checkout of the repo (seed file present).
        assert (out.workspace / "seed.txt").exists()

    async def test_non_git_workspace_falls_back_when_allowed(
        self, tmp_path: Path
    ) -> None:
        # fallback_on_error default True: a non-repo workspace continues
        # without isolation rather than failing the job.
        mgr = _manager()
        cfg = _config(tmp_path, enabled=True, fallback_on_error=True)
        out = await _setup_worktree_isolation("j", cfg, mgr._job_worktrees)
        assert out.workspace == cfg.workspace  # no worktree, original kept
        assert "j" not in mgr._job_worktrees

    async def test_non_git_workspace_raises_when_no_fallback(
        self, tmp_path: Path
    ) -> None:
        mgr = _manager()
        cfg = _config(tmp_path, enabled=True, fallback_on_error=False)
        from marianne.isolation.worktree import WorktreeError

        with pytest.raises(WorktreeError):
            await _setup_worktree_isolation("j", cfg, mgr._job_worktrees)


class TestWorktreeCleanup:
    async def test_cleanup_removes_worktree_on_success(
        self, tmp_path: Path
    ) -> None:
        repo = _git_repo(tmp_path)
        mgr = _manager()
        cfg = _config(repo, enabled=True, cleanup_on_success=True)
        out = await _setup_worktree_isolation("job-b", cfg, mgr._job_worktrees)
        wt = out.workspace
        assert wt.exists()

        await _cleanup_worktree_isolation("job-b", cfg, mgr._job_worktrees, success=True)
        assert not wt.exists()
        assert "job-b" not in mgr._job_worktrees

    async def test_cleanup_preserves_on_failure_by_default(
        self, tmp_path: Path
    ) -> None:
        repo = _git_repo(tmp_path)
        mgr = _manager()
        # cleanup_on_failure defaults False — keep the worktree for debugging.
        cfg = _config(repo, enabled=True)
        out = await _setup_worktree_isolation("job-c", cfg, mgr._job_worktrees)
        wt = out.workspace

        await _cleanup_worktree_isolation("job-c", cfg, mgr._job_worktrees, success=False)
        assert wt.exists()  # preserved for debugging

    async def test_cleanup_noop_when_no_worktree(self, tmp_path: Path) -> None:
        mgr = _manager()
        cfg = _config(tmp_path, enabled=False)
        # Must not raise when there's nothing to clean.
        await _cleanup_worktree_isolation("nope", cfg, mgr._job_worktrees, success=True)
