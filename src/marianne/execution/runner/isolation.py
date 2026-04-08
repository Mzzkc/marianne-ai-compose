"""Worktree isolation mixin for JobRunner.

Contains methods for git worktree setup and cleanup, enabling parallel-safe
job execution. When isolation is enabled, each job runs in its own isolated
git worktree with separate working directory, index, and HEAD.

Architecture:
    IsolationMixin is mixed with other mixins to compose the full JobRunner.
    It expects the following attributes from base.py:

    Required attributes:
        - config: JobConfig (provides isolation configuration)
        - backend: Backend (working_directory may be overridden)
        - _logger: MarianneLogger

    Provides methods:
        - _setup_isolation(): Create worktree before job execution
        - _cleanup_isolation(): Remove worktree after job completion

Isolation Flow:
    1. Job starts, _setup_isolation() called in run()
    2. If isolation enabled and mode=worktree:
       a. Check for existing worktree from resume
       b. Create new worktree in detached HEAD mode
       c. Override backend.working_directory to worktree path
    3. Job executes with isolated working directory
    4. Job completes, _cleanup_isolation() called
    5. Worktree removed based on success/failure config

v2 Evolution: Worktree Isolation
    Enables parallel-safe job execution by:
    - Creating detached HEAD worktrees (~24ms overhead)
    - Sharing git objects (storage efficient)
    - Locking worktrees during execution
    - Configurable cleanup on success/failure

Fallback Behavior:
    When fallback_on_error=True (default), isolation failures are
    logged and execution continues without isolation. This ensures
    jobs don't fail due to git issues.
"""

from __future__ import annotations

from pathlib import Path

from marianne.backends.base import Backend
from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import IsolationMode, JobConfig
from marianne.core.logging import MarianneLogger

from .models import FatalError


class IsolationMixin:
    """Mixin providing worktree isolation methods for JobRunner.

    This mixin handles git worktree management for parallel-safe
    job execution, including:

    - Creating isolated worktrees for concurrent jobs
    - Reusing existing worktrees on resume
    - Cleaning up worktrees based on job outcome
    - Fallback to non-isolated execution on errors

    Worktree Benefits:
        - Fast creation (~24ms vs full clone)
        - Storage efficient (shared .git objects)
        - True isolation (separate working tree, index, HEAD)
        - Lock support prevents accidental removal

    Cleanup Behavior:
        - Success: cleanup_on_success controls removal (default: True)
        - Failure: cleanup_on_failure controls removal (default: False)
        - Paused: Never cleanup (job will resume)

    Key Attributes (from base.py):
        config: Provides isolation settings and workspace path
        backend: Working directory may be overridden to worktree
    """

    # Type hints for attributes provided by base.py
    config: JobConfig
    backend: Backend
    _logger: MarianneLogger

    # ─────────────────────────────────────────────────────────────────────
    # Worktree Setup
    # ─────────────────────────────────────────────────────────────────────

    async def _setup_isolation(self, state: CheckpointState) -> Path | None:
        """Set up worktree isolation if configured.

        Creates an isolated git worktree for parallel-safe execution when
        isolation.enabled is True. The worktree provides a separate working
        directory, index, and HEAD so multiple jobs can run concurrently.

        Args:
            state: Job checkpoint state for tracking worktree info.

        Returns:
            Path to the worktree if created, None if isolation disabled or failed.

        Raises:
            FatalError: If isolation is required but cannot be established
                and fallback_on_error is False.
        """
        if not self.config.isolation.enabled:
            self._logger.debug("isolation_disabled")
            return None

        if self.config.isolation.mode != IsolationMode.WORKTREE:
            self._logger.warning(
                "unsupported_isolation_mode",
                mode=self.config.isolation.mode.value,
            )
            return None

        # Check for existing worktree from resume
        if state.worktree_path:
            existing_path = Path(state.worktree_path)
            if existing_path.exists():
                self._logger.info(
                    "reusing_existing_worktree",
                    path=str(existing_path),
                )
                return existing_path
            else:
                # Previous worktree was deleted - log warning and create new one
                self._logger.warning(
                    "previous_worktree_missing_creating_new",
                    previous_path=str(existing_path),
                )

        # Import worktree manager (lazy import to avoid circular deps)
        from marianne.isolation.worktree import (
            GitWorktreeManager,
            NotGitRepositoryError,
            WorktreeCreationError,
        )

        # Determine the source path for the git repository
        # Use working_directory if set, otherwise fall back to workspace
        repo_path = self.config.backend.working_directory or self.config.workspace

        manager = GitWorktreeManager(repo_path)

        # Check if in git repo
        if not manager.is_git_repository():
            if self.config.isolation.fallback_on_error:
                self._logger.warning(
                    "not_git_repo_fallback_to_workspace",
                    path=str(repo_path),
                )
                state.isolation_fallback_used = True
                state.isolation_mode = "none"
                return None
            raise FatalError(f"Worktree isolation requires a git repository: {repo_path}")

        # Create worktree (detached HEAD mode for parallel safety)
        try:
            worktree_base = self.config.isolation.get_worktree_base(self.config.workspace)
            result = await manager.create_worktree_detached(
                job_id=state.job_id,
                source_ref=self.config.isolation.source_branch,
                worktree_base=worktree_base,
                lock=self.config.isolation.lock_during_execution,
            )

            if result.success and result.worktree:
                # Update state with worktree info
                state.worktree_path = str(result.worktree.path)
                state.worktree_branch = result.worktree.branch
                state.worktree_locked = result.worktree.locked
                state.worktree_base_commit = result.worktree.commit
                state.isolation_mode = "worktree"

                self._logger.info(
                    "worktree_created",
                    path=str(result.worktree.path),
                    commit=result.worktree.commit,
                    locked=result.worktree.locked,
                )
                return result.worktree.path
            else:
                raise WorktreeCreationError(result.error or "Unknown worktree creation error")

        except (NotGitRepositoryError, WorktreeCreationError) as e:
            if self.config.isolation.fallback_on_error:
                self._logger.warning(
                    "worktree_creation_failed_fallback",
                    error=str(e),
                )
                state.isolation_fallback_used = True
                state.isolation_mode = "none"
                return None
            raise FatalError(f"Failed to create worktree: {e}") from e

    # ─────────────────────────────────────────────────────────────────────
    # Worktree Cleanup
    # ─────────────────────────────────────────────────────────────────────

    async def _cleanup_isolation(self, state: CheckpointState) -> None:
        """Clean up worktree isolation based on job outcome.

        Handles worktree removal based on job success/failure and cleanup
        configuration. Preserves worktree on failure for debugging by default.

        Args:
            state: Job checkpoint state with worktree info and final status.
        """
        if not state.worktree_path:
            return

        worktree_path = Path(state.worktree_path)
        if not worktree_path.exists():
            self._logger.debug(
                "worktree_already_removed",
                path=str(worktree_path),
            )
            return

        # Determine if we should cleanup based on outcome
        should_cleanup = False
        if (
            state.status == JobStatus.COMPLETED
            and self.config.isolation.cleanup_on_success
        ) or (
            state.status == JobStatus.FAILED
            and self.config.isolation.cleanup_on_failure
        ):
            should_cleanup = True
        elif state.status == JobStatus.PAUSED:
            # Never cleanup on pause - job will resume
            should_cleanup = False

        if not should_cleanup:
            self._logger.info(
                "worktree_preserved",
                path=str(worktree_path),
                status=state.status.value,
                reason="cleanup_disabled_for_outcome",
            )
            return

        # Import worktree manager (lazy import)
        from marianne.isolation.worktree import GitWorktreeManager

        repo_path = self.config.backend.working_directory or self.config.workspace
        manager = GitWorktreeManager(repo_path)

        try:
            # Unlock first if locked
            if state.worktree_locked:
                unlock_result = await manager.unlock_worktree(worktree_path)
                if unlock_result.success:
                    state.worktree_locked = False
                else:
                    self._logger.warning(
                        "worktree_unlock_failed",
                        path=str(worktree_path),
                        error=unlock_result.error,
                    )

            # Remove worktree (force=True to handle dirty state)
            remove_result = await manager.remove_worktree(
                worktree_path,
                force=True,
                delete_branch=False,  # Preserve branch for potential inspection
            )

            if remove_result.success:
                self._logger.info("worktree_removed", path=str(worktree_path))
                # Clear worktree tracking from state
                state.worktree_path = None
                state.worktree_branch = None
                state.worktree_base_commit = None
            else:
                self._logger.warning(
                    "worktree_removal_failed",
                    path=str(worktree_path),
                    error=remove_result.error,
                )
                # Don't fail the job for cleanup issues

        except Exception as e:
            self._logger.warning(
                "worktree_cleanup_exception",
                path=str(worktree_path),
                error=str(e),
            )
            # Don't fail the job for cleanup issues - log and continue

    # ─────────────────────────────────────────────────────────────────────
    # Working Directory Management
    # ─────────────────────────────────────────────────────────────────────

    def _get_effective_working_directory(self, state: CheckpointState) -> Path:
        """Get the effective working directory for execution.

        Returns the worktree path if isolation is active, otherwise
        returns the configured working directory or workspace.

        Args:
            state: Job checkpoint state with optional worktree info.

        Returns:
            Path to use as working directory for backend execution.
        """
        if state.worktree_path:
            worktree_path = Path(state.worktree_path)
            if worktree_path.exists():
                return worktree_path

        return self.config.backend.working_directory or self.config.workspace
