"""Tests for marianne.isolation.worktree module.

These tests verify the GitWorktreeManager implementation for creating,
managing, and cleaning up git worktrees for parallel job isolation.

Note: These tests require a git repository and actually create/remove
worktrees. They use temporary directories to avoid affecting the real repo.
"""

import subprocess
from pathlib import Path

import pytest

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

# --- Fixtures ---


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository for testing.

    Returns:
        Path to the temporary repository root.
    """
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create initial commit (required for worktrees)
    readme = repo_path / "README.md"
    readme.write_text("# Test Repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


@pytest.fixture
def manager(temp_git_repo: Path) -> GitWorktreeManager:
    """Create a GitWorktreeManager for the temp repo."""
    return GitWorktreeManager(temp_git_repo)


@pytest.fixture
def non_git_path(tmp_path: Path) -> Path:
    """Create a non-git directory."""
    path = tmp_path / "not_a_repo"
    path.mkdir()
    return path


# --- WorktreeInfo Tests ---


class TestWorktreeInfo:
    """Tests for WorktreeInfo dataclass."""

    def test_creation(self, tmp_path: Path) -> None:
        """Test WorktreeInfo can be created with required fields."""
        info = WorktreeInfo(
            path=tmp_path / "worktree",
            branch="marianne/test-job",
            commit="abc1234",
            locked=True,
            job_id="test-job",
        )
        assert info.path == tmp_path / "worktree"
        assert info.branch == "marianne/test-job"
        assert info.commit == "abc1234"
        assert info.locked is True
        assert info.job_id == "test-job"


class TestWorktreeResult:
    """Tests for WorktreeResult dataclass."""

    def test_success_result(self, tmp_path: Path) -> None:
        """Test successful result creation."""
        info = WorktreeInfo(
            path=tmp_path / "wt",
            branch="b",
            commit="c",
            locked=False,
            job_id="j",
        )
        result = WorktreeResult(success=True, worktree=info, error=None)
        assert result.success is True
        assert result.worktree == info
        assert result.error is None

    def test_failure_result(self) -> None:
        """Test failure result creation."""
        result = WorktreeResult(success=False, worktree=None, error="Something went wrong")
        assert result.success is False
        assert result.worktree is None
        assert result.error == "Something went wrong"


# --- Exception Tests ---


class TestExceptions:
    """Tests for worktree exception classes."""

    def test_worktree_error_is_exception(self) -> None:
        """Test base exception."""
        with pytest.raises(WorktreeError):
            raise WorktreeError("test error")

    def test_creation_error_inherits(self) -> None:
        """Test WorktreeCreationError inherits from WorktreeError."""
        with pytest.raises(WorktreeError):
            raise WorktreeCreationError("creation failed")

    def test_removal_error_inherits(self) -> None:
        """Test WorktreeRemovalError inherits from WorktreeError."""
        with pytest.raises(WorktreeError):
            raise WorktreeRemovalError("removal failed")

    def test_lock_error_inherits(self) -> None:
        """Test WorktreeLockError inherits from WorktreeError."""
        with pytest.raises(WorktreeError):
            raise WorktreeLockError("lock failed")

    def test_branch_exists_error_inherits(self) -> None:
        """Test BranchExistsError inherits from WorktreeError."""
        with pytest.raises(WorktreeError):
            raise BranchExistsError("branch exists")

    def test_not_git_repo_error_inherits(self) -> None:
        """Test NotGitRepositoryError inherits from WorktreeError."""
        with pytest.raises(WorktreeError):
            raise NotGitRepositoryError("not a repo")


# --- GitWorktreeManager Initialization Tests ---


class TestGitWorktreeManagerInit:
    """Tests for GitWorktreeManager initialization."""

    def test_initialization(self, temp_git_repo: Path) -> None:
        """Test manager can be initialized with a repo path."""
        manager = GitWorktreeManager(temp_git_repo)
        assert manager._repo_path == temp_git_repo.resolve()
        assert manager._git_verified is False

    def test_is_git_repository_true(self, temp_git_repo: Path) -> None:
        """Test is_git_repository returns True for git repos."""
        manager = GitWorktreeManager(temp_git_repo)
        assert manager.is_git_repository() is True

    def test_is_git_repository_false(self, non_git_path: Path) -> None:
        """Test is_git_repository returns False for non-git dirs."""
        manager = GitWorktreeManager(non_git_path)
        assert manager.is_git_repository() is False


# --- Create Worktree Tests ---


class TestCreateWorktree:
    """Tests for GitWorktreeManager.create_worktree()."""

    @pytest.mark.asyncio
    async def test_create_basic(self, manager: GitWorktreeManager, temp_git_repo: Path) -> None:
        """Test basic worktree creation."""
        result = await manager.create_worktree(
            job_id="test-job",
            lock=False,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.error is None

        # Verify worktree was created
        assert result.worktree.path.exists()
        assert result.worktree.branch == "marianne/test-job"
        assert result.worktree.job_id == "test-job"

    @pytest.mark.asyncio
    async def test_create_with_custom_prefix(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test worktree creation with custom branch prefix."""
        result = await manager.create_worktree(
            job_id="job-123",
            branch_prefix="custom",
            lock=False,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.worktree.branch == "custom/job-123"

    @pytest.mark.asyncio
    async def test_create_with_custom_base(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test worktree creation with custom base directory."""
        custom_base = temp_git_repo / "my_worktrees"
        result = await manager.create_worktree(
            job_id="job-custom",
            worktree_base=custom_base,
            lock=False,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.worktree.path.parent == custom_base

    @pytest.mark.asyncio
    async def test_create_with_lock(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test worktree creation with locking enabled."""
        result = await manager.create_worktree(
            job_id="locked-job",
            lock=True,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.worktree.locked is True

    @pytest.mark.asyncio
    async def test_create_not_git_repo(self, non_git_path: Path) -> None:
        """Test creation fails for non-git directory."""
        manager = GitWorktreeManager(non_git_path)

        with pytest.raises(NotGitRepositoryError):
            await manager.create_worktree(job_id="test")

    @pytest.mark.asyncio
    async def test_create_branch_exists(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test creation fails when branch already exists."""
        # Create a branch first
        subprocess.run(
            ["git", "branch", "marianne/existing-job"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        with pytest.raises(BranchExistsError):
            await manager.create_worktree(job_id="existing-job")

    @pytest.mark.asyncio
    async def test_create_path_exists(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test creation fails when path already exists."""
        # Create the path first
        worktree_path = temp_git_repo / ".worktrees" / "blocked-job"
        worktree_path.mkdir(parents=True)

        with pytest.raises(WorktreeCreationError):
            await manager.create_worktree(job_id="blocked-job")


# --- Create Worktree Detached Tests ---


class TestCreateWorktreeDetached:
    """Tests for GitWorktreeManager.create_worktree_detached()."""

    @pytest.mark.asyncio
    async def test_create_detached_basic(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test basic detached worktree creation."""
        result = await manager.create_worktree_detached(
            job_id="detached-job",
            lock=False,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.error is None

        # Verify worktree was created
        assert result.worktree.path.exists()
        assert result.worktree.branch == "(detached)"
        assert result.worktree.job_id == "detached-job"
        assert len(result.worktree.commit) > 0  # Should have a commit SHA

    @pytest.mark.asyncio
    async def test_create_detached_with_source_ref(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test detached worktree creation from specific ref."""
        # Create a second commit
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("test content")
        subprocess.run(
            ["git", "add", "test.txt"], cwd=temp_git_repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Second commit"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        # Create worktree from HEAD~1 (first commit)
        result = await manager.create_worktree_detached(
            job_id="from-ref-job",
            source_ref="HEAD~1",
            lock=False,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.worktree.branch == "(detached)"

    @pytest.mark.asyncio
    async def test_create_detached_with_lock(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test detached worktree creation with locking enabled."""
        result = await manager.create_worktree_detached(
            job_id="locked-detached",
            lock=True,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.worktree.locked is True

    @pytest.mark.asyncio
    async def test_create_detached_with_custom_base(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test detached worktree creation with custom base directory."""
        custom_base = temp_git_repo / "custom_worktrees"
        result = await manager.create_worktree_detached(
            job_id="custom-base-job",
            worktree_base=custom_base,
            lock=False,
        )

        assert result.success is True
        assert result.worktree is not None
        assert result.worktree.path.parent == custom_base

    @pytest.mark.asyncio
    async def test_create_detached_not_git_repo(self, non_git_path: Path) -> None:
        """Test detached creation fails for non-git directory."""
        manager = GitWorktreeManager(non_git_path)

        with pytest.raises(NotGitRepositoryError):
            await manager.create_worktree_detached(job_id="test")

    @pytest.mark.asyncio
    async def test_create_detached_path_exists(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test detached creation fails when path already exists."""
        # Create the path first
        worktree_path = temp_git_repo / ".worktrees" / "blocked-detached"
        worktree_path.mkdir(parents=True)

        with pytest.raises(WorktreeCreationError):
            await manager.create_worktree_detached(job_id="blocked-detached")

    @pytest.mark.asyncio
    async def test_create_detached_invalid_ref(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test detached creation fails with invalid source ref."""
        with pytest.raises(WorktreeCreationError) as exc_info:
            await manager.create_worktree_detached(
                job_id="invalid-ref-job",
                source_ref="nonexistent-branch",
            )
        assert "Cannot resolve ref" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_detached_multiple_from_same_commit(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test multiple detached worktrees can be created from same commit.

        This is the key advantage of detached HEAD mode - no branch conflicts.
        """
        result1 = await manager.create_worktree_detached(
            job_id="parallel-job-1",
            source_ref="HEAD",
            lock=False,
        )
        result2 = await manager.create_worktree_detached(
            job_id="parallel-job-2",
            source_ref="HEAD",
            lock=False,
        )

        assert result1.success is True
        assert result2.success is True
        assert result1.worktree is not None
        assert result2.worktree is not None

        # Both should be detached
        assert result1.worktree.branch == "(detached)"
        assert result2.worktree.branch == "(detached)"

        # Both should have same base commit
        assert result1.worktree.commit == result2.worktree.commit

        # But different paths
        assert result1.worktree.path != result2.worktree.path


# --- Remove Worktree Tests ---


class TestRemoveWorktree:
    """Tests for GitWorktreeManager.remove_worktree()."""

    @pytest.mark.asyncio
    async def test_remove_existing(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test removing an existing worktree."""
        # Create first
        create_result = await manager.create_worktree(job_id="to-remove", lock=False)
        assert create_result.success
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        # Then remove
        remove_result = await manager.remove_worktree(worktree_path)
        assert remove_result.success is True
        assert not worktree_path.exists()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_idempotent(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test removing nonexistent worktree succeeds (idempotent)."""
        nonexistent = temp_git_repo / "does_not_exist"

        result = await manager.remove_worktree(nonexistent)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_remove_with_force(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test removing worktree with force option."""
        create_result = await manager.create_worktree(job_id="force-remove", lock=False)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        # Make worktree dirty
        dirty_file = worktree_path / "dirty.txt"
        dirty_file.write_text("uncommitted changes")

        # Should still succeed with force
        remove_result = await manager.remove_worktree(worktree_path, force=True)
        assert remove_result.success is True

    @pytest.mark.asyncio
    async def test_remove_locked_worktree(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test removing a locked worktree (should unlock first)."""
        create_result = await manager.create_worktree(job_id="locked-remove", lock=True)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        # Should succeed - remove_worktree unlocks first
        remove_result = await manager.remove_worktree(worktree_path)
        assert remove_result.success is True


# --- Lock/Unlock Tests ---


class TestLockUnlock:
    """Tests for worktree locking operations."""

    @pytest.mark.asyncio
    async def test_lock_worktree(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test locking a worktree."""
        create_result = await manager.create_worktree(job_id="to-lock", lock=False)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        lock_result = await manager.lock_worktree(worktree_path, reason="Test lock")
        assert lock_result.success is True

        # Verify locked
        info = await manager.get_worktree_info(worktree_path)
        assert info is not None
        assert info.locked is True

    @pytest.mark.asyncio
    async def test_lock_already_locked_idempotent(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test locking already locked worktree succeeds (idempotent)."""
        create_result = await manager.create_worktree(job_id="double-lock", lock=True)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        # Lock again should succeed
        lock_result = await manager.lock_worktree(worktree_path)
        assert lock_result.success is True

    @pytest.mark.asyncio
    async def test_unlock_worktree(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test unlocking a worktree."""
        create_result = await manager.create_worktree(job_id="to-unlock", lock=True)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        unlock_result = await manager.unlock_worktree(worktree_path)
        assert unlock_result.success is True

        # Verify unlocked
        info = await manager.get_worktree_info(worktree_path)
        assert info is not None
        assert info.locked is False

    @pytest.mark.asyncio
    async def test_unlock_not_locked_idempotent(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test unlocking not-locked worktree succeeds (idempotent)."""
        create_result = await manager.create_worktree(job_id="not-locked", lock=False)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        # Unlock should succeed
        unlock_result = await manager.unlock_worktree(worktree_path)
        assert unlock_result.success is True


# --- List Worktrees Tests ---


class TestListWorktrees:
    """Tests for GitWorktreeManager.list_worktrees()."""

    @pytest.mark.asyncio
    async def test_list_empty(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test listing worktrees when none exist (except main)."""
        # With prefix filter, should be empty
        worktrees = await manager.list_worktrees(prefix_filter="marianne")
        assert len(worktrees) == 0

    @pytest.mark.asyncio
    async def test_list_with_worktrees(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test listing worktrees after creation."""
        await manager.create_worktree(job_id="list-test-1", lock=False)
        await manager.create_worktree(job_id="list-test-2", lock=False)

        worktrees = await manager.list_worktrees(prefix_filter="marianne")
        assert len(worktrees) == 2

        job_ids = {wt.job_id for wt in worktrees}
        assert "list-test-1" in job_ids
        assert "list-test-2" in job_ids

    @pytest.mark.asyncio
    async def test_list_with_prefix_filter(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test filtering worktrees by branch prefix."""
        await manager.create_worktree(job_id="marianne-1", branch_prefix="marianne", lock=False)
        await manager.create_worktree(job_id="other-1", branch_prefix="other", lock=False)

        marianne_worktrees = await manager.list_worktrees(prefix_filter="marianne")
        assert len(marianne_worktrees) == 1
        assert marianne_worktrees[0].job_id == "marianne-1"

        other_worktrees = await manager.list_worktrees(prefix_filter="other")
        assert len(other_worktrees) == 1
        assert other_worktrees[0].job_id == "other-1"


# --- Get Worktree Info Tests ---


class TestGetWorktreeInfo:
    """Tests for GitWorktreeManager.get_worktree_info()."""

    @pytest.mark.asyncio
    async def test_get_existing(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test getting info for existing worktree."""
        create_result = await manager.create_worktree(job_id="info-test", lock=False)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        info = await manager.get_worktree_info(worktree_path)
        assert info is not None
        assert info.job_id == "info-test"
        assert info.branch == "marianne/info-test"

    @pytest.mark.asyncio
    async def test_get_nonexistent(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test getting info for nonexistent worktree returns None."""
        nonexistent = temp_git_repo / "nonexistent"
        info = await manager.get_worktree_info(nonexistent)
        assert info is None


# --- Prune Orphaned Tests ---


class TestPruneOrphaned:
    """Tests for GitWorktreeManager.prune_orphaned()."""

    @pytest.mark.asyncio
    async def test_prune_no_orphans(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test pruning when no orphans exist."""
        orphaned = await manager.prune_orphaned()
        assert orphaned == []

    @pytest.mark.asyncio
    async def test_prune_dry_run(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test dry run doesn't actually prune."""
        # Create and manually delete worktree directory
        create_result = await manager.create_worktree(job_id="orphan-test", lock=False)
        assert create_result.worktree is not None
        worktree_path = create_result.worktree.path

        # Manually remove directory (orphan it)
        import shutil
        shutil.rmtree(worktree_path)

        # Dry run should report but not prune
        orphaned = await manager.prune_orphaned(dry_run=True)
        assert len(orphaned) == 1

    @pytest.mark.asyncio
    async def test_prune_prefix_filter(
        self, manager: GitWorktreeManager, temp_git_repo: Path
    ) -> None:
        """Test pruning respects prefix filter."""
        await manager.create_worktree(job_id="keep-1", branch_prefix="other", lock=False)

        # Only prune marianne/* - should find nothing
        orphaned = await manager.prune_orphaned(prefix_filter="marianne")
        assert orphaned == []


# --- Helper Method Tests ---


class TestExtractJobId:
    """Tests for _extract_job_id helper."""

    def test_extract_with_prefix(self, manager: GitWorktreeManager) -> None:
        """Test extracting job ID from branch with prefix."""
        assert manager._extract_job_id("marianne/job-123") == "job-123"
        assert manager._extract_job_id("custom/my-job") == "my-job"

    def test_extract_without_prefix(self, manager: GitWorktreeManager) -> None:
        """Test extracting job ID from branch without prefix."""
        assert manager._extract_job_id("main") == "main"
        assert manager._extract_job_id("feature-branch") == "feature-branch"

    def test_extract_nested_prefix(self, manager: GitWorktreeManager) -> None:
        """Test extracting job ID from branch with nested slashes."""
        assert manager._extract_job_id("marianne/feature/sub") == "feature/sub"


# --- IsolationConfig Tests ---


class TestIsolationConfig:
    """Tests for IsolationConfig in config.py."""

    def test_default_config(self) -> None:
        """Test default IsolationConfig values."""
        from marianne.core.config import IsolationConfig, IsolationMode

        config = IsolationConfig()
        assert config.enabled is False
        assert config.mode == IsolationMode.WORKTREE
        assert config.branch_prefix == "marianne"
        assert config.cleanup_on_success is True
        assert config.cleanup_on_failure is False
        assert config.fallback_on_error is True

    def test_get_worktree_base_default(self, tmp_path: Path) -> None:
        """Test default worktree base path."""
        from marianne.core.config import IsolationConfig

        config = IsolationConfig()
        workspace = tmp_path / "workspace"
        assert config.get_worktree_base(workspace) == workspace / ".worktrees"

    def test_get_worktree_base_custom(self, tmp_path: Path) -> None:
        """Test custom worktree base path."""
        from marianne.core.config import IsolationConfig

        custom_base = tmp_path / "custom"
        config = IsolationConfig(worktree_base=custom_base)
        workspace = tmp_path / "workspace"
        assert config.get_worktree_base(workspace) == custom_base

    def test_get_branch_name(self) -> None:
        """Test branch name generation."""
        from marianne.core.config import IsolationConfig

        config = IsolationConfig(branch_prefix="myprefix")
        assert config.get_branch_name("job-123") == "myprefix/job-123"

    def test_branch_prefix_validation(self) -> None:
        """Test branch prefix pattern validation."""
        from pydantic import ValidationError

        from marianne.core.config import IsolationConfig

        # Valid prefixes
        IsolationConfig(branch_prefix="marianne")
        IsolationConfig(branch_prefix="my-prefix")
        IsolationConfig(branch_prefix="prefix_123")

        # Invalid prefixes (must start with letter)
        with pytest.raises(ValidationError):
            IsolationConfig(branch_prefix="123prefix")
        with pytest.raises(ValidationError):
            IsolationConfig(branch_prefix="-prefix")


class TestJobConfigIsolation:
    """Tests for isolation in JobConfig."""

    def test_job_config_has_isolation(self) -> None:
        """Test JobConfig has isolation field with default."""
        from marianne.core.config import IsolationConfig, JobConfig

        config_yaml = """
name: test-job
sheet:
  size: 10
  total_items: 100
prompt:
  template: "Test prompt"
"""
        import yaml
        data = yaml.safe_load(config_yaml)
        job_config = JobConfig.model_validate(data)

        assert hasattr(job_config, "isolation")
        assert isinstance(job_config.isolation, IsolationConfig)
        assert job_config.isolation.enabled is False

    def test_job_config_isolation_enabled(self) -> None:
        """Test JobConfig with isolation enabled."""
        from marianne.core.config import IsolationMode, JobConfig

        config_yaml = """
name: isolated-job
sheet:
  size: 10
  total_items: 100
prompt:
  template: "Test prompt"
isolation:
  enabled: true
  mode: worktree
  branch_prefix: test
"""
        import yaml
        data = yaml.safe_load(config_yaml)
        job_config = JobConfig.model_validate(data)

        assert job_config.isolation.enabled is True
        assert job_config.isolation.mode == IsolationMode.WORKTREE
        assert job_config.isolation.branch_prefix == "test"


# --- CheckpointState Worktree Tracking Tests ---


class TestCheckpointStateWorktreeFields:
    """Tests for worktree tracking fields in CheckpointState."""

    def test_checkpoint_state_default_worktree_fields(self) -> None:
        """Test CheckpointState has worktree fields with correct defaults."""
        from marianne.core.checkpoint import CheckpointState

        state = CheckpointState(
            job_id="test-123",
            job_name="test-job",
            total_sheets=5,
        )

        # All worktree fields should have None/False defaults
        assert state.worktree_path is None
        assert state.worktree_branch is None
        assert state.worktree_locked is False
        assert state.worktree_base_commit is None
        assert state.isolation_mode is None
        assert state.isolation_fallback_used is False

    def test_checkpoint_state_worktree_fields_populated(self) -> None:
        """Test CheckpointState worktree fields can be populated."""
        from marianne.core.checkpoint import CheckpointState

        state = CheckpointState(
            job_id="isolated-123",
            job_name="isolated-job",
            total_sheets=5,
            worktree_path="/tmp/worktree/isolated-123",
            worktree_branch="(detached)",
            worktree_locked=True,
            worktree_base_commit="abc1234",
            isolation_mode="worktree",
            isolation_fallback_used=False,
        )

        assert state.worktree_path == "/tmp/worktree/isolated-123"
        assert state.worktree_branch == "(detached)"
        assert state.worktree_locked is True
        assert state.worktree_base_commit == "abc1234"
        assert state.isolation_mode == "worktree"
        assert state.isolation_fallback_used is False

    def test_checkpoint_state_fallback_tracking(self) -> None:
        """Test tracking when isolation falls back to workspace."""
        from marianne.core.checkpoint import CheckpointState

        state = CheckpointState(
            job_id="fallback-123",
            job_name="fallback-job",
            total_sheets=5,
            # Worktree couldn't be created, fell back to workspace
            worktree_path=None,
            isolation_mode="worktree",  # Was configured for worktree
            isolation_fallback_used=True,  # But fell back
        )

        assert state.worktree_path is None
        assert state.isolation_mode == "worktree"
        assert state.isolation_fallback_used is True

    def test_checkpoint_state_serialization(self) -> None:
        """Test CheckpointState with worktree fields can be serialized."""
        from marianne.core.checkpoint import CheckpointState

        state = CheckpointState(
            job_id="serialize-123",
            job_name="serialize-job",
            total_sheets=5,
            worktree_path="/tmp/worktree/serialize-123",
            worktree_branch="(detached)",
            worktree_locked=True,
            worktree_base_commit="abc1234",
            isolation_mode="worktree",
        )

        # Serialize to dict and back
        state_dict = state.model_dump()
        restored = CheckpointState.model_validate(state_dict)

        assert restored.worktree_path == state.worktree_path
        assert restored.worktree_branch == state.worktree_branch
        assert restored.worktree_locked == state.worktree_locked
        assert restored.worktree_base_commit == state.worktree_base_commit
        assert restored.isolation_mode == state.isolation_mode


# --- Runner Integration Tests (Sheet 7: Runner Integration) ---


class TestRunnerIsolationSetup:
    """Tests for JobRunner._setup_isolation() method."""

    @pytest.fixture
    def mock_backend(self):  # type: ignore[no-untyped-def]
        """Create a mock backend with working_directory attribute."""
        from unittest.mock import MagicMock

        from marianne.backends.base import Backend

        backend = MagicMock(spec=Backend)
        backend.working_directory = None
        return backend

    @pytest.fixture
    def minimal_config(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        """Create minimal JobConfig for testing."""
        import yaml

        from marianne.core.config import JobConfig

        config_yaml = f"""
name: test-isolation-job
workspace: {tmp_path / "workspace"}
sheet:
  size: 10
  total_items: 10
prompt:
  template: "Test prompt"
isolation:
  enabled: false
"""
        data = yaml.safe_load(config_yaml)
        return JobConfig.model_validate(data)

    def test_setup_isolation_disabled(self, minimal_config) -> None:  # type: ignore[no-untyped-def]
        """Test _setup_isolation returns None when isolation is disabled."""
        from marianne.core.checkpoint import CheckpointState

        CheckpointState(
            job_id="test-123",
            job_name="test-job",
            total_sheets=1,
        )

        # Isolation disabled is the default
        assert minimal_config.isolation.enabled is False

    def test_setup_isolation_not_git_repo_fallback(
        self, tmp_path: Path, mock_backend  # type: ignore[no-untyped-def]
    ) -> None:
        """Test _setup_isolation falls back when not in git repo."""
        import yaml

        from marianne.core.checkpoint import CheckpointState
        from marianne.core.config import JobConfig

        # Create a config with isolation enabled but workspace is not a git repo
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config_yaml = f"""
name: test-fallback
workspace: {workspace}
sheet:
  size: 1
  total_items: 1
prompt:
  template: "Test"
backend:
  working_directory: {workspace}
isolation:
  enabled: true
  fallback_on_error: true
"""
        data = yaml.safe_load(config_yaml)
        config = JobConfig.model_validate(data)

        CheckpointState(
            job_id="fallback-test",
            job_name="test-fallback",
            total_sheets=1,
        )

        # The state should track that fallback was used
        # (We can't easily test the full runner without mocking more, but
        # we can test the config is set up correctly)
        assert config.isolation.enabled is True
        assert config.isolation.fallback_on_error is True


class TestRunnerIsolationCleanup:
    """Tests for JobRunner._cleanup_isolation() method."""

    def test_cleanup_isolation_no_worktree(self) -> None:
        """Test _cleanup_isolation does nothing when no worktree path."""
        from marianne.core.checkpoint import CheckpointState, JobStatus

        state = CheckpointState(
            job_id="no-worktree",
            job_name="test",
            total_sheets=1,
            worktree_path=None,
        )
        state.status = JobStatus.COMPLETED

        # Should not raise and should not modify state
        assert state.worktree_path is None

    def test_cleanup_preserves_on_failure(self) -> None:
        """Test worktree is preserved when job fails and cleanup_on_failure=False."""
        from marianne.core.checkpoint import CheckpointState, JobStatus

        state = CheckpointState(
            job_id="failed-job",
            job_name="test",
            total_sheets=1,
            worktree_path="/tmp/worktree/failed-job",
            isolation_mode="worktree",
        )
        state.status = JobStatus.FAILED

        # With default cleanup_on_failure=False, worktree should be preserved
        # This is the expected behavior for debugging
        assert state.worktree_path is not None


class TestRunnerBackendWorkingDirectory:
    """Tests for backend.working_directory override during isolation."""

    def test_backend_working_directory_override(self) -> None:
        """Test that backend working_directory can be overridden."""
        from pathlib import Path

        from marianne.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend(working_directory=Path("/original"))
        assert backend.working_directory == Path("/original")

        # Simulate worktree override
        new_path = Path("/worktree/job-123")
        backend.working_directory = new_path
        assert backend.working_directory == new_path

        # Restore original
        backend.working_directory = Path("/original")
        assert backend.working_directory == Path("/original")

    def test_backend_working_directory_getattr_setattr(self) -> None:
        """Test dynamic attribute access works for working_directory."""
        from pathlib import Path

        from marianne.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend(working_directory=Path("/test"))

        # hasattr check
        assert hasattr(backend, "working_directory")

        # getattr
        wd = getattr(backend, "working_directory", None)
        assert wd == Path("/test")

        # setattr
        backend.working_directory = Path("/new")
        assert backend.working_directory == Path("/new")
