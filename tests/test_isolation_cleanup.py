"""Tests for worktree isolation cleanup decision logic and edge cases.

Covers the IsolationMixin._cleanup_isolation() decision matrix and
_setup_isolation() fallback paths using mocked git operations.

GH#82 — Isolation cleanup at 0% coverage.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import IsolationMode
from marianne.isolation.worktree import WorktreeResult

_WORKTREE_MANAGER = "marianne.isolation.worktree.GitWorktreeManager"

_OK_RESULT = WorktreeResult(success=True, worktree=None, error=None)


# ---------------------------------------------------------------------------
# Minimal IsolationMixin host for testing
# ---------------------------------------------------------------------------


def _make_mixin(
    *,
    isolation_enabled: bool = True,
    cleanup_on_success: bool = True,
    cleanup_on_failure: bool = False,
    lock_during_execution: bool = True,
    fallback_on_error: bool = True,
    workspace: Path | None = None,
    working_directory: Path | None = None,
):
    """Build a minimal object that satisfies IsolationMixin type stubs."""
    from marianne.execution.runner.isolation import IsolationMixin

    class _Host(IsolationMixin):
        pass

    host = _Host()

    iso = MagicMock()
    iso.enabled = isolation_enabled
    iso.mode = IsolationMode.WORKTREE
    iso.cleanup_on_success = cleanup_on_success
    iso.cleanup_on_failure = cleanup_on_failure
    iso.lock_during_execution = lock_during_execution
    iso.fallback_on_error = fallback_on_error
    iso.source_branch = None
    iso.get_worktree_base = MagicMock(return_value=Path("/tmp/worktrees"))

    config = MagicMock()
    config.isolation = iso
    config.workspace = workspace or Path("/tmp/ws")
    config.backend = MagicMock()
    config.backend.working_directory = working_directory

    host.config = config
    host.backend = MagicMock()
    host._logger = MagicMock()

    return host


def _make_state(
    *,
    status: JobStatus = JobStatus.COMPLETED,
    worktree_path: str | None = "/tmp/worktrees/test-job",
    worktree_locked: bool = True,
    worktree_branch: str | None = "mozart/test-job",
    worktree_base_commit: str | None = "abc123",
) -> CheckpointState:
    """Build a minimal CheckpointState for cleanup tests."""
    return CheckpointState(
        job_id="test-job",
        job_name="Test Job",
        config_hash="abc",
        total_sheets=5,
        status=status,
        worktree_path=worktree_path,
        worktree_locked=worktree_locked,
        worktree_branch=worktree_branch,
        worktree_base_commit=worktree_base_commit,
    )


def _patch_worktree_manager(
    *,
    unlock_result: WorktreeResult = _OK_RESULT,
    remove_result: WorktreeResult = _OK_RESULT,
    unlock_side_effect: Exception | None = None,
):
    """Patch GitWorktreeManager with configurable unlock/remove behavior."""
    ctx = patch(_WORKTREE_MANAGER)

    class _PatchContext:
        def __init__(self, patch_ctx):
            self._patch_ctx = patch_ctx
            self.mgr: MagicMock | None = None

        def __enter__(self):
            mock_cls = self._patch_ctx.__enter__()
            self.mgr = mock_cls.return_value
            if unlock_side_effect is not None:
                self.mgr.unlock_worktree = AsyncMock(side_effect=unlock_side_effect)
            else:
                self.mgr.unlock_worktree = AsyncMock(return_value=unlock_result)
            self.mgr.remove_worktree = AsyncMock(return_value=remove_result)
            return self

        def __exit__(self, *args):
            return self._patch_ctx.__exit__(*args)

    return _PatchContext(ctx)


# ---------------------------------------------------------------------------
# Cleanup decision matrix tests
# ---------------------------------------------------------------------------


class TestCleanupDecisionMatrix:
    """Test the should_cleanup decision logic for all status/config combos."""

    @pytest.mark.asyncio
    async def test_completed_cleanup_on_success_true(self, tmp_path: Path) -> None:
        """COMPLETED + cleanup_on_success=True -> worktree removed."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_success=True)
        state = _make_state(status=JobStatus.COMPLETED, worktree_path=str(wt_dir))

        with _patch_worktree_manager() as p:
            await host._cleanup_isolation(state)

            p.mgr.remove_worktree.assert_called_once()
            assert state.worktree_path is None

    @pytest.mark.asyncio
    async def test_completed_cleanup_on_success_false(self, tmp_path: Path) -> None:
        """COMPLETED + cleanup_on_success=False -> worktree preserved."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_success=False)
        state = _make_state(status=JobStatus.COMPLETED, worktree_path=str(wt_dir))

        await host._cleanup_isolation(state)

        assert state.worktree_path == str(wt_dir)
        host._logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_failed_cleanup_on_failure_true(self, tmp_path: Path) -> None:
        """FAILED + cleanup_on_failure=True -> worktree removed."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_failure=True)
        state = _make_state(status=JobStatus.FAILED, worktree_path=str(wt_dir))

        with _patch_worktree_manager() as p:
            await host._cleanup_isolation(state)

            p.mgr.remove_worktree.assert_called_once()
            assert state.worktree_path is None

    @pytest.mark.asyncio
    async def test_failed_cleanup_on_failure_false(self, tmp_path: Path) -> None:
        """FAILED + cleanup_on_failure=False -> worktree preserved."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_failure=False)
        state = _make_state(status=JobStatus.FAILED, worktree_path=str(wt_dir))

        await host._cleanup_isolation(state)

        assert state.worktree_path == str(wt_dir)

    @pytest.mark.asyncio
    async def test_paused_never_cleanup(self, tmp_path: Path) -> None:
        """PAUSED -> worktree always preserved regardless of config."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_success=True, cleanup_on_failure=True)
        state = _make_state(status=JobStatus.PAUSED, worktree_path=str(wt_dir))

        await host._cleanup_isolation(state)

        assert state.worktree_path == str(wt_dir)
        host._logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_cancelled_preserved(self, tmp_path: Path) -> None:
        """CANCELLED -> worktree preserved (not COMPLETED or FAILED)."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_success=True, cleanup_on_failure=True)
        state = _make_state(status=JobStatus.CANCELLED, worktree_path=str(wt_dir))

        await host._cleanup_isolation(state)

        assert state.worktree_path == str(wt_dir)


# ---------------------------------------------------------------------------
# Cleanup error handling
# ---------------------------------------------------------------------------


class TestCleanupErrorHandling:
    """Test cleanup behavior when operations fail."""

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_unlock_fails(self, tmp_path: Path) -> None:
        """Cleanup continues even if unlock_worktree fails."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_success=True)
        state = _make_state(
            status=JobStatus.COMPLETED, worktree_path=str(wt_dir), worktree_locked=True,
        )

        fail_result = WorktreeResult(success=False, worktree=None, error="lock stuck")
        with _patch_worktree_manager(unlock_result=fail_result) as p:
            await host._cleanup_isolation(state)

            p.mgr.remove_worktree.assert_called_once()
            host._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_removal_failure_preserves_state(
        self, tmp_path: Path,
    ) -> None:
        """When removal fails, worktree state is NOT cleared."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_success=True)
        state = _make_state(
            status=JobStatus.COMPLETED, worktree_path=str(wt_dir),
        )

        fail_result = WorktreeResult(success=False, worktree=None, error="busy")
        with _patch_worktree_manager(remove_result=fail_result):
            await host._cleanup_isolation(state)

            assert state.worktree_path == str(wt_dir)
            host._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_exception_does_not_crash(self, tmp_path: Path) -> None:
        """Unexpected exception during cleanup is caught and logged."""
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()

        host = _make_mixin(cleanup_on_success=True)
        state = _make_state(
            status=JobStatus.COMPLETED, worktree_path=str(wt_dir),
        )

        with _patch_worktree_manager(unlock_side_effect=RuntimeError("boom")):
            await host._cleanup_isolation(state)

            host._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_worktree_path_returns_early(self) -> None:
        """No worktree_path in state -> cleanup returns immediately."""
        host = _make_mixin()
        state = _make_state(worktree_path=None)

        await host._cleanup_isolation(state)

    @pytest.mark.asyncio
    async def test_worktree_already_removed(self, tmp_path: Path) -> None:
        """Worktree path set but directory missing -> debug log and return."""
        nonexistent = tmp_path / "gone"
        host = _make_mixin()
        state = _make_state(worktree_path=str(nonexistent))

        await host._cleanup_isolation(state)

        host._logger.debug.assert_called()


# ---------------------------------------------------------------------------
# Setup fallback tests
# ---------------------------------------------------------------------------


class TestSetupFallback:
    """Test _setup_isolation() fallback and error paths."""

    @pytest.mark.asyncio
    async def test_isolation_disabled_returns_none(self) -> None:
        """When isolation is disabled, returns None immediately."""
        host = _make_mixin(isolation_enabled=False)
        state = _make_state(worktree_path=None)
        result = await host._setup_isolation(state)
        assert result is None

    @pytest.mark.asyncio
    async def test_reuse_existing_worktree(self, tmp_path: Path) -> None:
        """When worktree_path exists on disk, reuses it."""
        wt_dir = tmp_path / "existing-wt"
        wt_dir.mkdir()

        host = _make_mixin()
        state = _make_state(worktree_path=str(wt_dir))

        result = await host._setup_isolation(state)
        assert result == wt_dir

    @pytest.mark.asyncio
    async def test_previous_worktree_missing_creates_new(
        self, tmp_path: Path,
    ) -> None:
        """When previous worktree was deleted, creates a new one."""
        gone = tmp_path / "deleted-wt"
        host = _make_mixin(workspace=tmp_path)
        state = _make_state(worktree_path=str(gone))

        wt_info = MagicMock()
        wt_info.path = tmp_path / "new-wt"
        wt_info.branch = "mozart/test-job"
        wt_info.commit = "def456"
        wt_info.locked = True

        with patch(_WORKTREE_MANAGER) as mock_mgr_cls:
            mgr = mock_mgr_cls.return_value
            mgr.is_git_repository = MagicMock(return_value=True)
            mgr.create_worktree_detached = AsyncMock(
                return_value=WorktreeResult(success=True, worktree=wt_info, error=None)
            )

            result = await host._setup_isolation(state)

            assert result == wt_info.path
            host._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_not_git_repo_fallback(self, tmp_path: Path) -> None:
        """Non-git repo + fallback_on_error=True -> returns None."""
        host = _make_mixin(fallback_on_error=True, workspace=tmp_path)
        state = _make_state(worktree_path=None)

        with patch(_WORKTREE_MANAGER) as mock_mgr_cls:
            mock_mgr_cls.return_value.is_git_repository = MagicMock(return_value=False)

            result = await host._setup_isolation(state)

            assert result is None
            assert state.isolation_fallback_used is True
            assert state.isolation_mode == "none"

    @pytest.mark.asyncio
    async def test_not_git_repo_no_fallback_raises(self, tmp_path: Path) -> None:
        """Non-git repo + fallback_on_error=False -> raises FatalError."""
        from marianne.execution.runner.models import FatalError

        host = _make_mixin(fallback_on_error=False, workspace=tmp_path)
        state = _make_state(worktree_path=None)

        with patch(_WORKTREE_MANAGER) as mock_mgr_cls:
            mock_mgr_cls.return_value.is_git_repository = MagicMock(return_value=False)

            with pytest.raises(FatalError, match="git repository"):
                await host._setup_isolation(state)
