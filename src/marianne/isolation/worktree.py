"""Git worktree management for parallel job isolation.

This module provides the WorktreeManager implementation for creating, managing,
and cleaning up git worktrees used for parallel job execution. Each worktree
provides an isolated working directory, index, and HEAD.

Example:
    manager = GitWorktreeManager(repo_path)
    result = await manager.create_worktree("job-123", "main")
    if result.success:
        # Execute job in result.worktree.path
        ...
        await manager.remove_worktree(result.worktree.path)
"""

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from marianne.core.logging import get_logger

# Module logger
_logger = get_logger("isolation.worktree")

# Git version requirement for worktree support
MIN_GIT_VERSION: Final[tuple[int, int]] = (2, 15)


# --- Exceptions ---


class WorktreeError(Exception):
    """Base exception for worktree operations."""

    pass


class WorktreeCreationError(WorktreeError):
    """Raised when worktree cannot be created."""

    pass


class WorktreeRemovalError(WorktreeError):
    """Raised when worktree cannot be removed."""

    pass


class WorktreeLockError(WorktreeError):
    """Raised when worktree lock/unlock fails."""

    pass


class BranchExistsError(WorktreeError):
    """Raised when target branch already exists."""

    pass


class NotGitRepositoryError(WorktreeError):
    """Raised when operation attempted outside git repository."""

    pass


# --- Data Classes ---


@dataclass
class WorktreeInfo:
    """Information about a created worktree."""

    path: Path
    """Filesystem path to the worktree directory."""

    branch: str
    """Git branch name checked out in the worktree."""

    commit: str
    """Commit SHA the worktree is based on."""

    locked: bool
    """Whether the worktree is currently locked."""

    job_id: str
    """Marianne job ID associated with this worktree."""


@dataclass
class WorktreeResult:
    """Result of a worktree operation."""

    success: bool
    """Whether the operation succeeded."""

    worktree: WorktreeInfo | None
    """Worktree info if operation succeeded, None otherwise."""

    error: str | None
    """Error message if operation failed, None otherwise."""


# --- GitWorktreeManager Implementation ---


class GitWorktreeManager:
    """Manages git worktree lifecycle for isolated execution.

    This class handles all git worktree operations asynchronously, providing
    isolation for parallel job execution. Each job gets its own worktree
    with a dedicated branch, preventing race conditions when multiple AI
    agents modify code simultaneously.

    Usage:
        manager = GitWorktreeManager(Path("/path/to/repo"))
        result = await manager.create_worktree(
            job_id="review-abc123",
            source_branch="main",
        )
        if result.success:
            # Execute job in result.worktree.path
            ...
            await manager.remove_worktree(result.worktree.path)
    """

    def __init__(self, repo_path: Path) -> None:
        """Initialize the worktree manager.

        Args:
            repo_path: Path to the git repository root.
        """
        self._repo_path = repo_path.resolve()
        self._git_verified = False

    async def _run_git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> tuple[int, str, str]:
        """Run a git command asynchronously.

        Uses asyncio.create_subprocess_exec for safe command execution
        without shell interpolation.

        Args:
            *args: Git command arguments (without 'git' prefix).
            cwd: Working directory (defaults to repo_path).
            check: If True, raise on non-zero exit code.

        Returns:
            Tuple of (exit_code, stdout, stderr).

        Raises:
            WorktreeError: If check=True and command fails.
        """
        cmd = ["git", *args]
        _logger.debug("git_command", args=args, cwd=str(cwd or self._repo_path))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd or self._repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode or 0

        if check and exit_code != 0:
            _logger.error(
                "git_command_failed",
                args=args,
                exit_code=exit_code,
                stderr=stderr[:500],
            )
            raise WorktreeError(f"Git command failed: {' '.join(args)}\n{stderr}")

        return exit_code, stdout, stderr

    def is_git_repository(self) -> bool:
        """Check if the manager's base path is a git repository.

        Returns:
            True if base path is inside a git repository.
        """
        git_dir = self._repo_path / ".git"
        # .git can be a directory (normal repo) or a file (worktree itself)
        return git_dir.exists()

    async def _verify_git_version(self) -> None:
        """Verify git version is sufficient for worktree operations.

        Raises:
            WorktreeError: If git version is too old.
        """
        if self._git_verified:
            return

        try:
            _, stdout, _ = await self._run_git("--version")
            # Parse "git version 2.39.2" -> (2, 39)
            parts = stdout.split()
            if len(parts) >= 3:
                version_str = parts[2]
                version_parts = version_str.split(".")
                if len(version_parts) >= 2:
                    major = int(version_parts[0])
                    minor = int(version_parts[1].split("-")[0])  # Handle "2.39.2-rc0"
                    if (major, minor) < MIN_GIT_VERSION:
                        raise WorktreeError(
                            f"Git version {major}.{minor} is too old. "
                            f"Worktrees require git {MIN_GIT_VERSION[0]}.{MIN_GIT_VERSION[1]}+"
                        )
            self._git_verified = True
        except (ValueError, IndexError) as e:
            _logger.warning("git_version_parse_failed", error=str(e))
            # Continue anyway - git worktree might still work
            self._git_verified = True

    async def _get_current_commit(self) -> str:
        """Get the current HEAD commit SHA.

        Returns:
            Short commit SHA (7 characters).
        """
        _, stdout, _ = await self._run_git("rev-parse", "--short", "HEAD")
        return stdout

    async def _branch_exists(self, branch: str) -> bool:
        """Check if a branch exists.

        Args:
            branch: Branch name to check.

        Returns:
            True if branch exists.
        """
        exit_code, _, _ = await self._run_git(
            "show-ref", "--verify", "--quiet", f"refs/heads/{branch}",
            check=False,
        )
        return exit_code == 0

    async def create_worktree_detached(
        self,
        job_id: str,
        source_ref: str | None = None,
        worktree_base: Path | None = None,
        lock: bool = True,
    ) -> WorktreeResult:
        """Create isolated worktree in detached HEAD mode.

        Unlike create_worktree(), this creates a worktree without a branch,
        allowing multiple worktrees to start from the same commit without
        branch locking conflicts. This is the preferred method for parallel
        job execution.

        Args:
            job_id: Unique job identifier for worktree naming.
            source_ref: Commit/branch to base worktree on (default: HEAD).
            worktree_base: Directory for worktrees (default: repo/.worktrees).
            lock: Whether to lock worktree after creation (default: True).

        Returns:
            WorktreeResult with worktree info on success, error on failure.
            Note: WorktreeInfo.branch will be "(detached)" for detached HEAD.

        Raises:
            NotGitRepositoryError: If not in a git repository.
            WorktreeCreationError: If worktree cannot be created.
        """
        _logger.info(
            "creating_worktree_detached",
            job_id=job_id,
            source_ref=source_ref,
        )

        # Sanitize job_id to prevent path traversal
        if ".." in job_id or "/" in job_id or "\\" in job_id or "\0" in job_id:
            raise WorktreeCreationError(
                f"Invalid job_id '{job_id}': must not contain path separators or '..'"
            )

        # Verify prerequisites
        if not self.is_git_repository():
            error_msg = f"Not a git repository: {self._repo_path}"
            _logger.error("not_git_repo", path=str(self._repo_path))
            raise NotGitRepositoryError(error_msg)

        await self._verify_git_version()

        # Determine paths
        base_path = worktree_base or (self._repo_path / ".worktrees")
        worktree_path = base_path / job_id

        # Check if worktree path already exists
        if worktree_path.exists():
            error_msg = f"Worktree path already exists: {worktree_path}"
            _logger.error("worktree_path_exists", path=str(worktree_path))
            raise WorktreeCreationError(error_msg)

        # Create parent directory
        base_path.mkdir(parents=True, exist_ok=True)

        # Get the source commit SHA for tracking
        source_commit: str
        if source_ref:
            # Resolve source_ref to a commit
            try:
                _, stdout, _ = await self._run_git("rev-parse", "--short", source_ref)
                source_commit = stdout
            except WorktreeError as e:
                raise WorktreeCreationError(f"Cannot resolve ref '{source_ref}': {e}") from e
        else:
            source_commit = await self._get_current_commit()

        # Build git worktree add --detach command
        # Syntax: git worktree add --detach <path> [<commit>]
        cmd_args = ["worktree", "add", "--detach", str(worktree_path)]
        if source_ref:
            cmd_args.append(source_ref)

        try:
            await self._run_git(*cmd_args)
        except WorktreeError as e:
            raise WorktreeCreationError(str(e)) from e

        # Lock if requested
        locked = False
        if lock:
            lock_result = await self.lock_worktree(
                worktree_path,
                reason=f"Marianne job {job_id} in progress (pid={os.getpid()})",
            )
            locked = lock_result.success

        worktree_info = WorktreeInfo(
            path=worktree_path,
            branch="(detached)",  # Indicate detached HEAD mode
            commit=source_commit,
            locked=locked,
            job_id=job_id,
        )

        _logger.info(
            "worktree_created_detached",
            job_id=job_id,
            path=str(worktree_path),
            commit=source_commit,
            locked=locked,
        )

        return WorktreeResult(success=True, worktree=worktree_info, error=None)

    async def create_worktree(
        self,
        job_id: str,
        source_branch: str | None = None,
        branch_prefix: str = "marianne",
        worktree_base: Path | None = None,
        lock: bool = True,
    ) -> WorktreeResult:
        """Create isolated worktree for job execution.

        Creates a new git worktree with a dedicated branch for the job.
        The worktree provides an isolated working directory, index, and HEAD.

        Args:
            job_id: Unique job identifier for worktree and branch naming.
            source_branch: Branch to base worktree on (default: HEAD).
            branch_prefix: Prefix for branch name (default: "marianne").
            worktree_base: Directory for worktrees (default: repo/.worktrees).
            lock: Whether to lock worktree after creation (default: True).

        Returns:
            WorktreeResult with worktree info on success, error on failure.
        """
        _logger.info(
            "creating_worktree",
            job_id=job_id,
            source_branch=source_branch,
            branch_prefix=branch_prefix,
        )

        # Sanitize job_id to prevent path traversal
        if ".." in job_id or "/" in job_id or "\\" in job_id or "\0" in job_id:
            raise WorktreeCreationError(
                f"Invalid job_id '{job_id}': must not contain path separators or '..'"
            )

        # Verify prerequisites
        if not self.is_git_repository():
            error_msg = f"Not a git repository: {self._repo_path}"
            _logger.error("not_git_repo", path=str(self._repo_path))
            raise NotGitRepositoryError(error_msg)

        await self._verify_git_version()

        # Determine paths and branch name
        base_path = worktree_base or (self._repo_path / ".worktrees")
        branch_name = f"{branch_prefix}/{job_id}"
        worktree_path = base_path / job_id

        # Check if branch already exists
        if await self._branch_exists(branch_name):
            error_msg = f"Branch '{branch_name}' already exists"
            _logger.error("branch_exists", branch=branch_name)
            raise BranchExistsError(error_msg)

        # Check if worktree path already exists
        if worktree_path.exists():
            error_msg = f"Worktree path already exists: {worktree_path}"
            _logger.error("worktree_path_exists", path=str(worktree_path))
            raise WorktreeCreationError(error_msg)

        # Create parent directory
        base_path.mkdir(parents=True, exist_ok=True)

        # Build git worktree add command
        cmd_args = ["worktree", "add", "-b", branch_name, str(worktree_path)]
        if source_branch:
            cmd_args.append(source_branch)

        try:
            await self._run_git(*cmd_args)
        except WorktreeError as e:
            raise WorktreeCreationError(str(e)) from e

        # Get commit SHA
        commit = await self._get_current_commit()

        # Lock if requested
        locked = False
        if lock:
            lock_result = await self.lock_worktree(
                worktree_path,
                reason=f"Marianne job {job_id} in progress (pid={os.getpid()})",
            )
            locked = lock_result.success

        worktree_info = WorktreeInfo(
            path=worktree_path,
            branch=branch_name,
            commit=commit,
            locked=locked,
            job_id=job_id,
        )

        _logger.info(
            "worktree_created",
            job_id=job_id,
            path=str(worktree_path),
            branch=branch_name,
            commit=commit,
            locked=locked,
        )

        return WorktreeResult(success=True, worktree=worktree_info, error=None)

    async def remove_worktree(
        self,
        worktree_path: Path,
        force: bool = True,
        delete_branch: bool = False,
    ) -> WorktreeResult:
        """Remove worktree and optionally its branch.

        Removes the worktree directory and cleans up git metadata.
        The associated branch is preserved by default for review/merge.

        Args:
            worktree_path: Path to the worktree to remove.
            force: Force removal even if worktree is dirty (default: True).
            delete_branch: Also delete the associated branch (default: False).

        Returns:
            WorktreeResult indicating success or failure.
        """
        worktree_path = worktree_path.resolve()
        _logger.info(
            "removing_worktree",
            path=str(worktree_path),
            force=force,
            delete_branch=delete_branch,
        )

        # If worktree doesn't exist, consider it a success (idempotent)
        if not worktree_path.exists():
            _logger.debug("worktree_already_removed", path=str(worktree_path))
            return WorktreeResult(success=True, worktree=None, error=None)

        # Get branch name before removal (for optional deletion)
        branch_name: str | None = None
        if delete_branch:
            try:
                _, stdout, _ = await self._run_git(
                    "rev-parse", "--abbrev-ref", "HEAD",
                    cwd=worktree_path,
                    check=False,
                )
                branch_name = stdout
            except Exception as e:
                _logger.warning(
                    "worktree.branch_lookup_failed",
                    worktree_path=str(worktree_path),
                    error=str(e),
                )

        # Check if locking PID is still alive — detached hooks may outlive the job
        try:
            _, lock_reason, _ = await self._run_git(
                "worktree", "list", "--porcelain", check=False,
            )
            # Parse lock reason to extract PID
            for block in lock_reason.split("\n\n"):
                if str(worktree_path) in block and "locked" in block:
                    pid_match = re.search(r"pid=(\d+)", block)
                    if pid_match:
                        lock_pid = int(pid_match.group(1))
                        try:
                            os.kill(lock_pid, 0)  # Check if process exists
                            _logger.warning(
                                "worktree.lock_process_still_alive",
                                path=str(worktree_path),
                                pid=lock_pid,
                            )
                        except OSError:
                            _logger.debug(
                                "worktree.lock_process_dead",
                                path=str(worktree_path),
                                pid=lock_pid,
                            )
        except (WorktreeError, ValueError):
            pass  # Can't check — proceed with cleanup

        # Unlock first (ignore errors - may already be unlocked)
        await self.unlock_worktree(worktree_path)

        # Remove worktree via git
        cmd_args = ["worktree", "remove"]
        if force:
            cmd_args.append("--force")
        cmd_args.append(str(worktree_path))

        try:
            await self._run_git(*cmd_args)
        except WorktreeError:
            # If git worktree remove fails, try manual cleanup
            _logger.warning(
                "git_worktree_remove_failed_trying_manual",
                path=str(worktree_path),
            )
            try:
                shutil.rmtree(worktree_path)
                # Run git worktree prune to clean metadata
                await self._run_git("worktree", "prune", check=False)
            except Exception as e:
                error_msg = f"Failed to remove worktree: {e}"
                _logger.error("worktree_removal_failed", path=str(worktree_path), error=str(e))
                raise WorktreeRemovalError(error_msg) from e

        # Optionally delete the branch
        if delete_branch and branch_name:
            try:
                await self._run_git("branch", "-D", branch_name, check=False)
                _logger.info("branch_deleted", branch=branch_name)
            except Exception as e:
                _logger.warning("branch_deletion_failed", branch=branch_name, error=str(e))

        _logger.info("worktree_removed", path=str(worktree_path))
        return WorktreeResult(success=True, worktree=None, error=None)

    async def lock_worktree(
        self,
        worktree_path: Path,
        reason: str | None = None,
    ) -> WorktreeResult:
        """Lock worktree to prevent accidental removal.

        Locking prevents 'git worktree remove' and 'git worktree prune'
        from affecting this worktree.

        Args:
            worktree_path: Path to the worktree to lock.
            reason: Human-readable reason for the lock.

        Returns:
            WorktreeResult indicating success or failure.
        """
        worktree_path = worktree_path.resolve()
        _logger.debug("locking_worktree", path=str(worktree_path), reason=reason)

        cmd_args = ["worktree", "lock"]
        if reason:
            cmd_args.extend(["--reason", reason])
        cmd_args.append(str(worktree_path))

        try:
            await self._run_git(*cmd_args)
            _logger.info("worktree_locked", path=str(worktree_path))
            return WorktreeResult(success=True, worktree=None, error=None)
        except WorktreeError as e:
            if "already locked" in str(e).lower():
                # Check if the existing lock is stale (owner process dead)
                if await self._is_lock_stale(worktree_path):
                    _logger.warning(
                        "worktree_stale_lock_detected",
                        path=str(worktree_path),
                        message="Lock owner process is dead, force-unlocking",
                    )
                    await self.unlock_worktree(worktree_path)
                    # Retry the lock
                    try:
                        await self._run_git(*cmd_args)
                        _logger.info("worktree_locked_after_stale_cleanup", path=str(worktree_path))
                        return WorktreeResult(success=True, worktree=None, error=None)
                    except WorktreeError as retry_err:
                        raise WorktreeLockError(str(retry_err)) from retry_err
                _logger.debug("worktree_already_locked", path=str(worktree_path))
                return WorktreeResult(success=True, worktree=None, error=None)
            raise WorktreeLockError(str(e)) from e

    async def _is_lock_stale(self, worktree_path: Path) -> bool:
        """Check if a worktree lock is stale by inspecting the lock reason for a PID.

        If the lock reason contains a pid=NNNN pattern and that process is no
        longer running, the lock is considered stale.
        """
        try:
            _, stdout, _ = await self._run_git("worktree", "list", "--porcelain", check=False)
        except Exception:
            _logger.debug("lock_stale_check_failed", path=str(worktree_path), exc_info=True)
            return False

        reason = self._find_lock_reason(stdout, str(worktree_path))
        if reason is None:
            return False

        return self._is_pid_dead(reason)

    @staticmethod
    def _find_lock_reason(porcelain_output: str, worktree_str: str) -> str | None:
        """Extract lock reason for a specific worktree from porcelain output.

        Returns the lock reason string, or None if the worktree is not locked.
        """
        in_target = False
        for line in porcelain_output.splitlines():
            if line.startswith("worktree ") and line[9:] == worktree_str:
                in_target = True
            elif line.startswith("worktree "):
                in_target = False
            elif in_target and line.startswith("locked"):
                return line[7:] if len(line) > 7 else ""
        return None

    @staticmethod
    def _is_pid_dead(lock_reason: str) -> bool:
        """Check if the PID in a lock reason refers to a dead process.

        Returns True if the lock reason contains a pid=NNNN pattern and
        that process is no longer running. Returns False if the process
        is alive, or if no PID is found in the reason.
        """
        pid_match = re.search(r"pid=(\d+)", lock_reason)
        if not pid_match:
            return False

        pid = int(pid_match.group(1))
        try:
            os.kill(pid, 0)  # Signal 0: check if process exists
            return False  # Process alive, lock is valid
        except ProcessLookupError:
            return True  # Process dead, lock is stale
        except PermissionError:
            return False  # Process exists but owned by another user

    async def unlock_worktree(
        self,
        worktree_path: Path,
    ) -> WorktreeResult:
        """Unlock a previously locked worktree.

        Args:
            worktree_path: Path to the worktree to unlock.

        Returns:
            WorktreeResult indicating success or failure.

        Note:
            Returns success if worktree is already unlocked (idempotent).
        """
        worktree_path = worktree_path.resolve()
        _logger.debug("unlocking_worktree", path=str(worktree_path))

        try:
            await self._run_git("worktree", "unlock", str(worktree_path))
            _logger.info("worktree_unlocked", path=str(worktree_path))
            return WorktreeResult(success=True, worktree=None, error=None)
        except WorktreeError as e:
            # May already be unlocked or not exist
            error_str = str(e).lower()
            if "not locked" in error_str or "is not a working tree" in error_str:
                _logger.debug("worktree_not_locked_or_missing", path=str(worktree_path))
                return WorktreeResult(success=True, worktree=None, error=None)
            raise WorktreeLockError(str(e)) from e

    async def list_worktrees(
        self,
        prefix_filter: str | None = None,
    ) -> list[WorktreeInfo]:
        """List all worktrees, optionally filtered by branch prefix.

        Args:
            prefix_filter: Only return worktrees with branches matching prefix.
                          For example, "marianne" returns all marianne/* branches.

        Returns:
            List of WorktreeInfo for matching worktrees.
        """
        _logger.debug("listing_worktrees", prefix_filter=prefix_filter)

        _, stdout, _ = await self._run_git("worktree", "list", "--porcelain")

        worktrees: list[WorktreeInfo] = []
        current_path: Path | None = None
        current_branch: str | None = None
        current_commit: str | None = None
        current_locked = False

        for line in stdout.split("\n"):
            line = line.strip()

            if line.startswith("worktree "):
                # New worktree entry
                if current_path and current_branch:
                    # Save previous entry
                    worktrees.append(WorktreeInfo(
                        path=current_path,
                        branch=current_branch,
                        commit=current_commit or "",
                        locked=current_locked,
                        job_id=self._extract_job_id(current_branch),
                    ))
                current_path = Path(line[9:])
                current_branch = None
                current_commit = None
                current_locked = False

            elif line.startswith("HEAD "):
                current_commit = line[5:]

            elif line.startswith("branch refs/heads/"):
                current_branch = line[18:]

            elif line.startswith("locked"):
                current_locked = True

            elif line == "":
                # Entry separator
                pass

        # Don't forget the last entry
        if current_path and current_branch:
            worktrees.append(WorktreeInfo(
                path=current_path,
                branch=current_branch,
                commit=current_commit or "",
                locked=current_locked,
                job_id=self._extract_job_id(current_branch),
            ))

        # Apply prefix filter
        if prefix_filter:
            prefix = f"{prefix_filter}/"
            worktrees = [wt for wt in worktrees if wt.branch.startswith(prefix)]

        _logger.debug("worktrees_found", count=len(worktrees))
        return worktrees

    def _extract_job_id(self, branch: str) -> str:
        """Extract job ID from branch name.

        Args:
            branch: Branch name like "marianne/job-123".

        Returns:
            Job ID like "job-123", or branch name if no prefix.
        """
        if "/" in branch:
            return branch.split("/", 1)[1]
        return branch

    async def prune_orphaned(
        self,
        prefix_filter: str = "marianne",
        dry_run: bool = False,
    ) -> list[str]:
        """Clean up orphaned worktree metadata.

        Removes metadata for worktrees whose directories no longer exist.
        Only affects worktrees with branches matching the prefix filter.

        Args:
            prefix_filter: Only prune worktrees with matching branch prefix.
            dry_run: If True, return what would be pruned without pruning.

        Returns:
            List of pruned worktree names (or would-be-pruned if dry_run).
        """
        _logger.info(
            "pruning_orphaned_worktrees",
            prefix_filter=prefix_filter,
            dry_run=dry_run,
        )

        # First, list worktrees to identify marianne-prefixed ones
        worktrees = await self.list_worktrees(prefix_filter=prefix_filter)

        # Find orphaned (path doesn't exist)
        orphaned: list[str] = []
        for wt in worktrees:
            if not wt.path.exists():
                orphaned.append(str(wt.path))

        if not dry_run and orphaned:
            # Run git worktree prune to clean metadata
            await self._run_git("worktree", "prune")
            _logger.info("orphaned_worktrees_pruned", count=len(orphaned))

        return orphaned

    async def get_worktree_info(
        self,
        worktree_path: Path,
    ) -> WorktreeInfo | None:
        """Get information about a specific worktree.

        Args:
            worktree_path: Path to the worktree.

        Returns:
            WorktreeInfo if worktree exists, None otherwise.
        """
        worktree_path = worktree_path.resolve()
        worktrees = await self.list_worktrees()

        for wt in worktrees:
            if wt.path.resolve() == worktree_path:
                return wt

        return None


# Alias for spec compatibility - WorktreeManager is the Protocol name in the design spec
# GitWorktreeManager is the concrete implementation
WorktreeManager = GitWorktreeManager
