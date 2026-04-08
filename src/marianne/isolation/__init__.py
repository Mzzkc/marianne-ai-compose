"""Isolation module for parallel job execution.

This module provides worktree-based isolation for Marianne jobs, enabling
multiple jobs to execute in parallel without interfering with each other's
file modifications.

Key components:
- GitWorktreeManager: Manages git worktree lifecycle
- WorktreeInfo: Information about a created worktree
- WorktreeResult: Result of worktree operations
- Exception classes for error handling
"""

from marianne.isolation.worktree import (
    BranchExistsError,
    GitWorktreeManager,
    NotGitRepositoryError,
    WorktreeCreationError,
    WorktreeError,
    WorktreeInfo,
    WorktreeLockError,
    WorktreeRemovalError,
    WorktreeResult,
)

# Alias for spec compatibility - WorktreeManager is the Protocol name in the design spec
# GitWorktreeManager is the concrete implementation
WorktreeManager = GitWorktreeManager

__all__ = [
    # Manager
    "GitWorktreeManager",
    "WorktreeManager",  # Alias for spec compatibility
    # Data classes
    "WorktreeInfo",
    "WorktreeResult",
    # Exceptions
    "WorktreeError",
    "WorktreeCreationError",
    "WorktreeRemovalError",
    "WorktreeLockError",
    "BranchExistsError",
    "NotGitRepositoryError",
]
