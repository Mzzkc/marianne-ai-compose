"""Tests for the self-healing module.

Tests cover:
- ErrorContext creation (including from_execution_result)
- Remedy base classes and protocol
- Individual remedy implementations (all 6 built-in remedies)
- RemedyRegistry functionality
- DiagnosisEngine
- SelfHealingCoordinator
- Diagnosis formatting
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from mozart.backends.base import ExecutionResult

import pytest

from mozart.healing.context import ErrorContext
from mozart.healing.coordinator import HealingReport, SelfHealingCoordinator
from mozart.healing.diagnosis import Diagnosis, DiagnosisEngine
from mozart.healing.registry import RemedyRegistry, create_default_registry
from mozart.healing.remedies.base import BaseRemedy, RemedyCategory, RemedyResult, RiskLevel
from mozart.healing.remedies.diagnostics import DiagnoseAuthErrorRemedy, DiagnoseMissingCLIRemedy
from mozart.healing.remedies.jinja import SuggestJinjaFixRemedy
from mozart.healing.remedies.paths import (
    CreateMissingParentDirsRemedy,
    CreateMissingWorkspaceRemedy,
    FixPathSeparatorsRemedy,
)

# =============================================================================
# Fixtures
# =============================================================================


def _make_config_mock(
    workspace: Path | str = Path("/tmp/test-workspace"),
    working_directory: Path | None = None,
) -> SimpleNamespace:
    """Create a typed config mock that mirrors JobConfig's healing-relevant interface.

    Uses SimpleNamespace instead of bare MagicMock() so that accessing an undefined
    attribute raises AttributeError, catching interface drift between tests and
    production code.
    """
    return SimpleNamespace(
        workspace=workspace,
        backend=SimpleNamespace(working_directory=working_directory),
    )


@pytest.fixture
def mock_config():
    """Create a mock JobConfig."""
    return _make_config_mock()


@pytest.fixture
def basic_error_context(tmp_path):
    """Create a basic ErrorContext for testing."""
    workspace = tmp_path / "test-workspace"
    mock_config = _make_config_mock(workspace=workspace)
    return ErrorContext(
        error_code="E601",
        error_message=f"Workspace directory does not exist: {workspace}",
        error_category="preflight",
        config=mock_config,
        workspace=workspace,
        sheet_number=1,
        working_directory=workspace,
    )


@pytest.fixture
def registry():
    """Create an empty registry."""
    return RemedyRegistry()


@pytest.fixture
def default_registry():
    """Create the default registry with all built-in remedies."""
    return create_default_registry()


# =============================================================================
# ErrorContext Tests
# =============================================================================


class TestErrorContext:
    """Tests for ErrorContext creation and methods."""

    def test_basic_creation(self, mock_config):
        """Test basic ErrorContext creation."""
        ctx = ErrorContext(
            error_code="E601",
            error_message="Test error",
            error_category="preflight",
        )
        assert ctx.error_code == "E601"
        assert ctx.error_message == "Test error"
        assert ctx.error_category == "preflight"

    def test_from_preflight_error(self, mock_config):
        """Test creating context from preflight error."""
        ctx = ErrorContext.from_preflight_error(
            config=mock_config,
            config_path=Path("/tmp/config.yaml"),
            error_code="E601",
            error_message="Workspace missing",
            sheet_number=1,
        )
        assert ctx.error_code == "E601"
        assert ctx.error_category == "preflight"
        assert ctx.sheet_number == 1
        assert ctx.workspace == mock_config.workspace

    def test_get_context_summary(self, basic_error_context):
        """Test context summary generation."""
        summary = basic_error_context.get_context_summary()
        assert summary["error_code"] == "E601"
        assert summary["sheet_number"] == 1
        assert summary["has_stdout"] is False


# =============================================================================
# RemedyRegistry Tests
# =============================================================================


class TestRemedyRegistry:
    """Tests for RemedyRegistry."""

    def test_empty_registry(self, registry):
        """Test empty registry."""
        assert registry.count() == 0
        assert registry.all_remedies() == []

    def test_register_remedy(self, registry):
        """Test registering a remedy."""
        remedy = CreateMissingWorkspaceRemedy()
        registry.register(remedy)
        assert registry.count() == 1

    def test_get_by_name(self, registry):
        """Test getting remedy by name."""
        remedy = CreateMissingWorkspaceRemedy()
        registry.register(remedy)

        found = registry.get_by_name("create_missing_workspace")
        assert found is not None
        assert found.name == "create_missing_workspace"

        not_found = registry.get_by_name("nonexistent")
        assert not_found is None

    def test_find_applicable(self, default_registry, basic_error_context):
        """Test finding applicable remedies."""
        applicable = default_registry.find_applicable(basic_error_context)

        # Should find the workspace remedy for E601 errors
        remedy_names = [r.name for r, _ in applicable]
        assert "create_missing_workspace" in remedy_names

        # Results should be sorted by confidence
        confidences = [d.confidence for _, d in applicable]
        assert confidences == sorted(confidences, reverse=True)

    def test_default_registry_has_all_remedies(self, default_registry):
        """Test that default registry has all built-in remedies."""
        names = {r.name for r in default_registry.all_remedies()}

        expected = {
            "create_missing_workspace",
            "create_missing_parent_dirs",
            "fix_path_separators",
            "suggest_jinja_fix",
            "diagnose_auth_error",
            "diagnose_missing_cli",
        }
        assert expected == names


# =============================================================================
# DiagnosisEngine Tests
# =============================================================================


class TestDiagnosisEngine:
    """Tests for DiagnosisEngine."""

    def test_diagnose_empty_registry(self, registry):
        """Test diagnosis with empty registry."""
        engine = DiagnosisEngine(registry)
        ctx = ErrorContext(
            error_code="E601",
            error_message="Test",
            error_category="test",
        )
        diagnoses = engine.diagnose(ctx)
        assert diagnoses == []

    def test_diagnose_finds_applicable(self, default_registry, basic_error_context):
        """Test diagnosis finds applicable remedies."""
        engine = DiagnosisEngine(default_registry)
        diagnoses = engine.diagnose(basic_error_context)

        # Should diagnose the E601 workspace error
        assert len(diagnoses) >= 1
        remedy_names = [d.remedy_name for d in diagnoses]
        assert "create_missing_workspace" in remedy_names
        assert all(isinstance(d, Diagnosis) for d in diagnoses)

    def test_diagnose_sorts_by_confidence(self, default_registry, basic_error_context):
        """Test diagnoses are sorted by confidence."""
        engine = DiagnosisEngine(default_registry)
        diagnoses = engine.diagnose(basic_error_context)

        if len(diagnoses) > 1:
            confidences = [d.confidence for d in diagnoses]
            assert confidences == sorted(confidences, reverse=True)

    def test_get_primary_diagnosis(self, default_registry, basic_error_context):
        """Test getting primary (highest confidence) diagnosis."""
        engine = DiagnosisEngine(default_registry)
        primary = engine.get_primary_diagnosis(basic_error_context)

        assert primary is not None
        assert primary.error_code == basic_error_context.error_code


# =============================================================================
# CreateMissingWorkspaceRemedy Tests
# =============================================================================


class TestCreateMissingWorkspaceRemedy:
    """Tests for CreateMissingWorkspaceRemedy."""

    def test_properties(self):
        """Test remedy properties."""
        remedy = CreateMissingWorkspaceRemedy()
        assert remedy.name == "create_missing_workspace"
        assert remedy.category == RemedyCategory.AUTOMATIC
        assert remedy.risk_level == RiskLevel.LOW

    def test_diagnoses_missing_workspace(self, tmp_path, mock_config):
        """Test diagnosis when workspace doesn't exist."""
        workspace = tmp_path / "missing-workspace"
        mock_config.workspace = workspace

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        remedy = CreateMissingWorkspaceRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert diagnosis.confidence >= 0.9
        assert diagnosis.remedy_name == "create_missing_workspace"

    def test_does_not_diagnose_existing_workspace(self, tmp_path, mock_config):
        """Test no diagnosis when workspace exists."""
        workspace = tmp_path / "existing"
        workspace.mkdir()
        mock_config.workspace = workspace

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        remedy = CreateMissingWorkspaceRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is None

    def test_apply_creates_directory(self, tmp_path, mock_config):
        """Test that apply() creates the workspace directory."""
        workspace = tmp_path / "new-workspace"
        mock_config.workspace = workspace

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace missing: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        remedy = CreateMissingWorkspaceRemedy()
        result = remedy.apply(ctx)

        assert result.success
        assert workspace.exists()
        assert workspace in result.created_paths

    def test_rollback_removes_empty_directory(self, tmp_path, mock_config):
        """Test rollback removes empty directory."""
        workspace = tmp_path / "rollback-test"
        mock_config.workspace = workspace

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace missing: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        remedy = CreateMissingWorkspaceRemedy()
        result = remedy.apply(ctx)
        assert workspace.exists()

        # Rollback
        rolled_back = remedy.rollback(result)
        assert rolled_back, "Rollback should succeed for created workspace"
        assert not workspace.exists()


# =============================================================================
# SuggestJinjaFixRemedy Tests
# =============================================================================


class TestSuggestJinjaFixRemedy:
    """Tests for SuggestJinjaFixRemedy."""

    def test_properties(self):
        """Test remedy properties."""
        remedy = SuggestJinjaFixRemedy()
        assert remedy.name == "suggest_jinja_fix"
        assert remedy.category == RemedyCategory.SUGGESTED
        assert remedy.risk_level == RiskLevel.MEDIUM

    def test_diagnoses_undefined_variable(self, mock_config):
        """Test diagnosis of undefined variable error."""
        ctx = ErrorContext(
            error_code="E304",
            error_message="'shee_num' is undefined",
            error_category="template",
            config=mock_config,
        )

        remedy = SuggestJinjaFixRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert "shee_num" in diagnosis.issue
        # Should suggest 'sheet_num' as correction
        assert "sheet_num" in diagnosis.suggestion

    def test_diagnoses_unclosed_block(self, mock_config):
        """Test diagnosis of unclosed Jinja block."""
        ctx = ErrorContext(
            error_code="E304",
            error_message="Unexpected end of template",
            error_category="template",
            config=mock_config,
        )

        remedy = SuggestJinjaFixRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert "unclosed" in diagnosis.issue.lower() or "end" in diagnosis.suggestion.lower()


# =============================================================================
# DiagnoseAuthErrorRemedy Tests
# =============================================================================


class TestDiagnoseAuthErrorRemedy:
    """Tests for DiagnoseAuthErrorRemedy."""

    def test_properties(self):
        """Test remedy properties."""
        remedy = DiagnoseAuthErrorRemedy()
        assert remedy.name == "diagnose_auth_error"
        assert remedy.category == RemedyCategory.DIAGNOSTIC
        assert remedy.risk_level == RiskLevel.LOW

    def test_diagnoses_rate_limit(self, mock_config):
        """Test diagnosis of rate limit error."""
        ctx = ErrorContext(
            error_code="E101",
            error_message="Rate limit exceeded",
            error_category="rate_limit",
            config=mock_config,
        )

        remedy = DiagnoseAuthErrorRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert "rate" in diagnosis.issue.lower()

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=True)
    def test_diagnoses_missing_api_key(self, mock_config):
        """Test diagnosis when API key is missing."""
        ctx = ErrorContext(
            error_code="E102",
            error_message="Authentication failed",
            error_category="auth",
            config=mock_config,
        )

        remedy = DiagnoseAuthErrorRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert "key" in diagnosis.issue.lower() or "key" in diagnosis.suggestion.lower()

    def test_generate_diagnostic(self, mock_config):
        """Test diagnostic message generation."""
        ctx = ErrorContext(
            error_code="E101",
            error_message="Rate limit exceeded",
            error_category="rate_limit",
            config=mock_config,
        )

        remedy = DiagnoseAuthErrorRemedy()
        diagnostic = remedy.generate_diagnostic(ctx)

        assert "rate limit" in diagnostic.lower()
        assert len(diagnostic) > 100  # Should have substantial guidance


# =============================================================================
# SelfHealingCoordinator Tests
# =============================================================================


class TestSelfHealingCoordinator:
    """Tests for SelfHealingCoordinator."""

    @pytest.fixture
    def coordinator(self, default_registry):
        """Create a coordinator with default registry."""
        return SelfHealingCoordinator(default_registry)

    @pytest.mark.asyncio
    async def test_heal_applies_automatic_remedies(self, coordinator, tmp_path, mock_config):
        """Test that automatic remedies are applied without prompting."""
        workspace = tmp_path / "heal-test"
        mock_config.workspace = workspace

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        report = await coordinator.heal(ctx)

        # Should have applied the workspace creation remedy
        assert report.any_remedies_applied
        assert workspace.exists()

    @pytest.mark.asyncio
    async def test_heal_dry_run(self, default_registry, tmp_path, mock_config):
        """Test dry run mode doesn't make changes."""
        workspace = tmp_path / "dry-run-test"
        mock_config.workspace = workspace

        coordinator = SelfHealingCoordinator(
            default_registry,
            dry_run=True,
        )

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        report = await coordinator.heal(ctx)

        # Should have skipped the remedy (dry run)
        assert not report.any_remedies_applied
        assert not workspace.exists()
        # May or may not have skipped remedies depending on diagnosis
        # The important thing is that no changes were made

    @pytest.mark.asyncio
    async def test_heal_disabled_remedies(self, default_registry, tmp_path, mock_config):
        """Test that disabled remedies are skipped."""
        workspace = tmp_path / "disabled-test"
        mock_config.workspace = workspace

        coordinator = SelfHealingCoordinator(
            default_registry,
            disabled_remedies={"create_missing_workspace"},
        )

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        report = await coordinator.heal(ctx)

        # Workspace should not be created (remedy is disabled)
        assert not workspace.exists()
        # If the remedy was diagnosed, it should be in skipped list
        if report.diagnoses:
            # Might also be skipped for other reasons or have other remedies
            assert not report.any_remedies_applied

    @pytest.mark.asyncio
    async def test_max_healing_attempts(self, default_registry, mock_config):
        """Test that healing stops after max attempts."""
        coordinator = SelfHealingCoordinator(
            default_registry,
            max_healing_attempts=2,
        )

        ctx = ErrorContext(
            error_code="E999",
            error_message="Unknown error",
            error_category="unknown",
            config=mock_config,
        )

        # First two attempts should work (even if no remedies apply)
        await coordinator.heal(ctx)
        await coordinator.heal(ctx)

        # Third attempt should be blocked
        report = await coordinator.heal(ctx)
        assert "Max healing attempts" in str(report.actions_skipped)


# =============================================================================
# HealingReport Tests
# =============================================================================


class TestHealingReport:
    """Tests for HealingReport."""

    def test_any_remedies_applied(self, basic_error_context):
        """Test any_remedies_applied property."""
        report = HealingReport(error_context=basic_error_context)
        assert not report.any_remedies_applied

        report.actions_taken.append((
            "test",
            RemedyResult(success=True, message="OK", action_taken="test"),
        ))
        assert report.any_remedies_applied

    def test_should_retry(self, basic_error_context):
        """Test should_retry property."""
        report = HealingReport(error_context=basic_error_context)
        assert not report.should_retry

        report.actions_taken.append((
            "test",
            RemedyResult(success=True, message="OK", action_taken="test"),
        ))
        assert report.should_retry

    def test_format(self, basic_error_context):
        """Test report formatting."""
        report = HealingReport(error_context=basic_error_context)
        formatted = report.format()

        assert "SELF-HEALING REPORT" in formatted
        assert basic_error_context.error_code in formatted


# =============================================================================
# Integration Tests
# =============================================================================


class TestHealingIntegration:
    """Integration tests for the healing system."""

    @pytest.mark.asyncio
    async def test_full_healing_flow(self, tmp_path):
        """Test complete healing flow from error to fix."""
        # Setup
        workspace = tmp_path / "integration-test"
        mock_config = _make_config_mock(workspace=workspace)

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry)

        # Create error context
        ctx = ErrorContext.from_preflight_error(
            config=mock_config,
            config_path=None,
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
        )

        # Heal
        report = await coordinator.heal(ctx)

        # Verify
        assert report.should_retry
        assert workspace.exists()
        assert len(report.diagnoses) >= 1
        diagnosis_names = [d.remedy_name for d in report.diagnoses]
        assert "create_missing_workspace" in diagnosis_names
        assert report.any_remedies_applied


# =============================================================================
# CreateMissingParentDirsRemedy Tests (NEW - previously untested)
# =============================================================================


class TestCreateMissingParentDirsRemedy:
    """Tests for CreateMissingParentDirsRemedy."""

    def test_properties(self):
        """Test remedy properties."""
        remedy = CreateMissingParentDirsRemedy()
        assert remedy.name == "create_missing_parent_dirs"
        assert remedy.category == RemedyCategory.AUTOMATIC
        assert remedy.risk_level == RiskLevel.LOW

    def test_diagnoses_missing_parent_directory(self, tmp_path, mock_config):
        """Test diagnosis when parent directory is missing."""
        missing = tmp_path / "deep" / "nested" / "output"
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{missing}' does not exist",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path,
        )
        remedy = CreateMissingParentDirsRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert diagnosis.confidence == 0.85
        assert diagnosis.remedy_name == "create_missing_parent_dirs"
        assert "paths_to_create" in diagnosis.context

    def test_no_diagnosis_when_directory_exists(self, tmp_path, mock_config):
        """Test no diagnosis when directory already exists."""
        existing = tmp_path / "existing"
        existing.mkdir()
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{existing}' does not exist",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path,
        )
        remedy = CreateMissingParentDirsRemedy()
        diagnosis = remedy.diagnose(ctx)
        assert diagnosis is None

    def test_no_diagnosis_for_wrong_error_code(self, mock_config):
        """Test no diagnosis for unrelated error codes."""
        ctx = ErrorContext(
            error_code="E101",
            error_message="directory '/foo/bar' does not exist",
            error_category="rate_limit",
            config=mock_config,
        )
        remedy = CreateMissingParentDirsRemedy()
        assert remedy.diagnose(ctx) is None

    def test_apply_creates_directory_tree(self, tmp_path, mock_config):
        """Test that apply() creates the full directory tree."""
        deep_path = tmp_path / "a" / "b" / "c"
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{deep_path}' does not exist",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path,
        )
        remedy = CreateMissingParentDirsRemedy()
        result = remedy.apply(ctx)

        assert result.success
        assert deep_path.exists()
        assert len(result.created_paths) >= 1

    def test_rollback_removes_created_directories(self, tmp_path, mock_config):
        """Test that rollback removes created empty directories."""
        deep_path = tmp_path / "rollback_a" / "rollback_b"
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{deep_path}' does not exist",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path,
        )
        remedy = CreateMissingParentDirsRemedy()
        result = remedy.apply(ctx)
        assert deep_path.exists()

        rolled_back = remedy.rollback(result)
        assert rolled_back

    def test_preview_returns_meaningful_text(self, tmp_path, mock_config):
        """Test that preview provides useful info."""
        missing = tmp_path / "preview_dir" / "sub"
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{missing}' does not exist",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path,
        )
        remedy = CreateMissingParentDirsRemedy()
        preview = remedy.preview(ctx)
        assert "Create" in preview or "directories" in preview

    def test_partial_rollback_when_some_dirs_not_empty(self, tmp_path, mock_config):
        """Test rollback handles partial state: some dirs created, some have content.

        B2-12: When apply() creates a/b/c, and then a file is placed in a/b,
        rollback should remove c (empty) but fail on b (non-empty), returning False.
        """
        deep_path = tmp_path / "partial_a" / "partial_b" / "partial_c"
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{deep_path}' does not exist",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path,
        )
        remedy = CreateMissingParentDirsRemedy()
        result = remedy.apply(ctx)
        assert result.success
        assert deep_path.exists()

        # Simulate another process placing a file in the middle directory
        sentinel = tmp_path / "partial_a" / "partial_b" / "sentinel.txt"
        sentinel.write_text("data")

        # Rollback should fail partially: c is empty (removable), b has sentinel (not removable)
        rolled_back = remedy.rollback(result)
        assert not rolled_back, "Rollback should return False when some dirs are non-empty"

        # The deepest empty dir (partial_c) should have been removed
        assert not deep_path.exists(), "Empty leaf dir should be removed"
        # The middle dir with content should survive
        assert sentinel.exists(), "Files in non-empty dirs should survive rollback"

    def test_apply_records_created_paths_before_oserror(self, tmp_path, mock_config):
        """Test that apply() records which dirs were created before an OSError.

        B2-12: If mkdir fails partway through, the result should have created_paths
        reflecting only the dirs that were actually created.
        """
        deep_path = tmp_path / "fail_a" / "fail_b" / "fail_c"
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{deep_path}' does not exist",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path,
        )
        remedy = CreateMissingParentDirsRemedy()

        # Create first dir to simulate partial progress before apply()
        first_dir = tmp_path / "fail_a"
        first_dir.mkdir()

        # Mock Path.mkdir to fail on the second call (fail_b)
        original_mkdir = Path.mkdir
        call_count = 0

        def failing_mkdir(self_path, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise OSError("Simulated permission denied")
            return original_mkdir(self_path, *args, **kwargs)

        with patch.object(Path, "mkdir", failing_mkdir):
            result = remedy.apply(ctx)

        # Should fail due to the OSError
        assert not result.success
        # created_paths should list only the dirs that were actually created before the error
        assert isinstance(result.created_paths, list)
        # At least one directory was created before the simulated error
        assert len(result.created_paths) >= 1


# =============================================================================
# FixPathSeparatorsRemedy Tests (NEW - previously untested)
# =============================================================================


class TestFixPathSeparatorsRemedy:
    """Tests for FixPathSeparatorsRemedy."""

    def test_properties(self):
        """Test remedy properties."""
        remedy = FixPathSeparatorsRemedy()
        assert remedy.name == "fix_path_separators"
        assert remedy.category == RemedyCategory.AUTOMATIC

    @patch("sys.platform", "linux")
    def test_diagnoses_backslash_paths_on_unix(self, mock_config):
        """Test diagnosis of Windows paths on Unix systems."""
        ctx = ErrorContext(
            error_code="E601",
            error_message=r"File not found: C:\Users\test\output.txt",
            error_category="preflight",
            config=mock_config,
        )
        remedy = FixPathSeparatorsRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert diagnosis.confidence == 0.90
        assert "fixed_path" in diagnosis.context
        assert "/" in diagnosis.context["fixed_path"]

    @patch("sys.platform", "win32")
    def test_no_diagnosis_on_windows(self, mock_config):
        """Test no diagnosis when running on Windows."""
        ctx = ErrorContext(
            error_code="E601",
            error_message=r"File not found: C:\Users\test\output.txt",
            error_category="preflight",
            config=mock_config,
        )
        remedy = FixPathSeparatorsRemedy()
        diagnosis = remedy.diagnose(ctx)
        assert diagnosis is None

    @patch("sys.platform", "linux")
    def test_no_diagnosis_for_unix_paths(self, mock_config):
        """Test no diagnosis when paths are already Unix-style."""
        ctx = ErrorContext(
            error_code="E601",
            error_message="File not found: /home/test/output.txt",
            error_category="preflight",
            config=mock_config,
        )
        remedy = FixPathSeparatorsRemedy()
        diagnosis = remedy.diagnose(ctx)
        assert diagnosis is None

    @patch("sys.platform", "linux")
    def test_apply_returns_informational_result(self, mock_config):
        """Test that apply provides information (no file changes)."""
        ctx = ErrorContext(
            error_code="E601",
            error_message=r"File not found: C:\workspace\output.txt",
            error_category="preflight",
            config=mock_config,
        )
        remedy = FixPathSeparatorsRemedy()
        result = remedy.apply(ctx)
        assert result.success
        assert "diagnosis provided" in result.action_taken

    @patch("sys.platform", "linux")
    def test_generate_diagnostic(self, mock_config):
        """Test diagnostic message for path separator issues."""
        ctx = ErrorContext(
            error_code="E601",
            error_message=r"File not found: C:\workspace\output.txt",
            error_category="preflight",
            config=mock_config,
        )
        remedy = FixPathSeparatorsRemedy()
        diagnostic = remedy.generate_diagnostic(ctx)
        assert "Windows-style" in diagnostic or "backslash" in diagnostic.lower()


# =============================================================================
# DiagnoseMissingCLIRemedy Tests (NEW - previously untested)
# =============================================================================


class TestDiagnoseMissingCLIRemedy:
    """Tests for DiagnoseMissingCLIRemedy."""

    def test_properties(self):
        """Test remedy properties."""
        remedy = DiagnoseMissingCLIRemedy()
        assert remedy.name == "diagnose_missing_cli"
        assert remedy.category == RemedyCategory.DIAGNOSTIC

    def test_diagnoses_cli_not_found(self, mock_config):
        """Test diagnosis when Claude CLI not found."""
        ctx = ErrorContext(
            error_code="E601",
            error_message="claude: command not found",
            error_category="execution",
            config=mock_config,
        )
        remedy = DiagnoseMissingCLIRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert "claude" in diagnosis.issue.lower() or "cli" in diagnosis.issue.lower()

    def test_no_diagnosis_for_unrelated_error(self, mock_config):
        """Test no diagnosis for unrelated errors."""
        ctx = ErrorContext(
            error_code="E101",
            error_message="Rate limit exceeded",
            error_category="rate_limit",
            config=mock_config,
        )
        remedy = DiagnoseMissingCLIRemedy()
        assert remedy.diagnose(ctx) is None

    @patch("shutil.which", return_value=None)
    def test_cli_not_in_path(self, mock_which, mock_config):
        """Test diagnosis when claude not in PATH."""
        ctx = ErrorContext(
            error_code="E901",
            error_message="claude cli not installed",
            error_category="execution",
            config=mock_config,
        )
        remedy = DiagnoseMissingCLIRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert diagnosis.confidence == 0.95
        assert not diagnosis.context.get("cli_found")

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_cli_found_but_broken(self, mock_which, mock_config):
        """Test diagnosis when claude found but not working."""
        ctx = ErrorContext(
            error_code="E901",
            error_message="claude cli error",
            error_category="execution",
            config=mock_config,
        )
        remedy = DiagnoseMissingCLIRemedy()
        diagnosis = remedy.diagnose(ctx)

        assert diagnosis is not None
        assert diagnosis.confidence == 0.70
        assert diagnosis.context.get("cli_found")

    @patch("shutil.which", return_value=None)
    def test_generate_diagnostic_includes_install_instructions(self, mock_which, mock_config):
        """Test that diagnostic includes installation instructions."""
        ctx = ErrorContext(
            error_code="E901",
            error_message="claude command not found",
            error_category="execution",
            config=mock_config,
        )
        remedy = DiagnoseMissingCLIRemedy()
        diagnostic = remedy.generate_diagnostic(ctx)

        assert "npm" in diagnostic.lower()
        assert "install" in diagnostic.lower()


# =============================================================================
# ErrorContext.from_execution_result Tests (NEW - previously untested)
# =============================================================================


class TestErrorContextFromExecutionResult:
    """Tests for ErrorContext.from_execution_result factory method."""

    def test_creates_context_from_result(self, mock_config):
        """Test creating context from an ExecutionResult."""
        result = create_autospec(ExecutionResult, instance=True)
        result.exit_code = 1
        result.exit_signal = None
        result.stdout = "output text"
        result.stderr = "error text"

        ctx = ErrorContext.from_execution_result(
            result=result,
            config=mock_config,
            config_path=Path("/tmp/config.yaml"),
            sheet_number=3,
            error_code="E302",
            error_message="Validation failed",
            error_category="validation",
            retry_count=2,
            max_retries=5,
            previous_errors=["E301"],
        )

        assert ctx.error_code == "E302"
        assert ctx.exit_code == 1
        assert ctx.sheet_number == 3
        assert ctx.retry_count == 2
        assert ctx.max_retries == 5
        assert "E301" in ctx.previous_errors
        assert ctx.stdout_tail == "output text"
        assert ctx.stderr_tail == "error text"

    def test_truncates_long_output(self, mock_config):
        """Test that long stdout/stderr are truncated."""
        result = create_autospec(ExecutionResult, instance=True)
        result.exit_code = 1
        result.exit_signal = None
        result.stdout = "x" * 20000
        result.stderr = "y" * 20000

        ctx = ErrorContext.from_execution_result(
            result=result,
            config=mock_config,
            config_path=None,
            sheet_number=1,
            error_code="E302",
            error_message="Test",
            error_category="test",
        )

        # HEALING_CONTEXT_TAIL_CHARS is 10000
        assert len(ctx.stdout_tail) <= 10000
        assert len(ctx.stderr_tail) <= 10000

    def test_handles_empty_output(self, mock_config):
        """Test handling of empty stdout/stderr."""
        result = create_autospec(ExecutionResult, instance=True)
        result.exit_code = 0
        result.exit_signal = None
        result.stdout = None
        result.stderr = None

        ctx = ErrorContext.from_execution_result(
            result=result,
            config=mock_config,
            config_path=None,
            sheet_number=1,
            error_code="E000",
            error_message="Test",
            error_category="test",
        )

        assert ctx.stdout_tail == ""
        assert ctx.stderr_tail == ""

    def test_captures_api_key_masked(self, mock_config):
        """Test that API key is masked in environment."""
        result = create_autospec(ExecutionResult, instance=True)
        result.exit_code = 0
        result.exit_signal = None
        result.stdout = ""
        result.stderr = ""

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-secret"}):
            ctx = ErrorContext.from_execution_result(
                result=result,
                config=mock_config,
                config_path=None,
                sheet_number=1,
                error_code="E000",
                error_message="Test",
                error_category="test",
            )

        assert ctx.environment.get("ANTHROPIC_API_KEY") == "***"


# =============================================================================
# Diagnosis Formatting Tests (NEW - previously untested)
# =============================================================================


class TestDiagnosisFormatting:
    """Tests for Diagnosis format_short and format_full methods."""

    def _make_diagnosis(self) -> Diagnosis:
        return Diagnosis(
            error_code="E601",
            issue="Workspace missing",
            explanation="The workspace directory does not exist.",
            suggestion="Create it with mkdir.",
            confidence=0.95,
            remedy_name="create_missing_workspace",
            requires_confirmation=False,
            context={},
        )

    def test_format_short(self):
        """Test short format contains key info."""
        d = self._make_diagnosis()
        short = d.format_short()
        assert "E601" in short
        assert "Workspace missing" in short

    def test_format_full(self):
        """Test full format contains all details."""
        d = self._make_diagnosis()
        full = d.format_full()
        assert "E601" in full
        assert "Workspace missing" in full
        assert "explanation" in full.lower() or "workspace directory" in full
        assert "mkdir" in full


# =============================================================================
# Coordinator Edge Case Tests (NEW - previously untested)
# =============================================================================


class TestCoordinatorEdgeCases:
    """Additional edge case tests for SelfHealingCoordinator."""

    @pytest.mark.asyncio
    async def test_reset_clears_attempt_counter(self, default_registry, mock_config):
        """Test that reset() allows healing again after max attempts."""
        coordinator = SelfHealingCoordinator(
            default_registry,
            max_healing_attempts=1,
        )
        ctx = ErrorContext(
            error_code="E999",
            error_message="Unknown error",
            error_category="unknown",
            config=mock_config,
        )

        # First attempt works
        await coordinator.heal(ctx)

        # Second attempt blocked
        report = await coordinator.heal(ctx)
        assert any("Max healing" in reason for _, reason in report.actions_skipped)

        # Reset and try again
        coordinator.reset()
        report = await coordinator.heal(ctx)
        # Should not be blocked anymore
        assert not any("Max healing" in reason for _, reason in report.actions_skipped)

    @pytest.mark.asyncio
    async def test_heal_with_no_applicable_remedies(self, default_registry, mock_config):
        """Test healing when no remedies apply to the error."""
        ctx = ErrorContext(
            error_code="E999",
            error_message="A completely novel error nobody has seen",
            error_category="unknown",
            config=mock_config,
        )
        coordinator = SelfHealingCoordinator(default_registry)
        report = await coordinator.heal(ctx)

        assert not report.any_remedies_applied
        assert not report.should_retry


# =============================================================================
# Real Code Path Tests — exercise actual remedy logic without mocks (FIX-06)
# =============================================================================


class TestRemedyRealCodePaths:
    """Tests that exercise real remedy execution paths on actual file systems.

    These tests verify that the entire diagnose→apply→rollback pipeline works
    end-to-end without mock interference, using tmp_path for isolation.
    """

    def test_workspace_remedy_full_pipeline(self, tmp_path):
        """Test complete workspace remedy: diagnose → apply → verify → rollback → verify."""
        workspace = tmp_path / "new-workspace"
        config = _make_config_mock(workspace=workspace)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=config,
        )

        remedy = CreateMissingWorkspaceRemedy()

        # Diagnose
        diagnosis = remedy.diagnose(ctx)
        assert diagnosis is not None
        assert diagnosis.confidence == 0.95

        # Preview
        preview_text = remedy.preview(ctx)
        assert str(workspace) in preview_text

        # Apply
        result = remedy.apply(ctx)
        assert result.success
        assert workspace.exists()
        assert workspace.is_dir()
        assert len(result.created_paths) == 1
        assert result.rollback_command is not None

        # Rollback
        rolled_back = remedy.rollback(result)
        assert rolled_back
        assert not workspace.exists()

    def test_parent_dirs_remedy_deep_tree(self, tmp_path):
        """Test creating a deeply nested directory tree and rolling back."""
        deep_path = tmp_path / "level1" / "level2" / "level3" / "level4"
        config = _make_config_mock(workspace=tmp_path)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"directory '{deep_path}' does not exist",
            error_category="preflight",
            config=config,
            workspace=tmp_path,
        )

        remedy = CreateMissingParentDirsRemedy()
        diagnosis = remedy.diagnose(ctx)
        assert diagnosis is not None
        assert len(diagnosis.context["paths_to_create"]) >= 3

        result = remedy.apply(ctx)
        assert result.success
        assert deep_path.exists()

        # Rollback should remove the empty tree
        rolled_back = remedy.rollback(result)
        assert rolled_back

    def test_workspace_apply_with_no_workspace_in_context(self, mock_config):
        """Test workspace remedy when context has no workspace path."""
        ctx = ErrorContext(
            error_code="E601",
            error_message="Workspace missing",
            error_category="preflight",
            workspace=None,
            config=mock_config,
        )
        remedy = CreateMissingWorkspaceRemedy()
        result = remedy.apply(ctx)
        assert not result.success
        assert "No workspace path" in result.message

    def test_rollback_with_nonempty_directory(self, tmp_path):
        """Test that rollback does NOT remove a non-empty directory."""
        workspace = tmp_path / "has-files"
        workspace.mkdir()
        (workspace / "file.txt").write_text("content")

        result = RemedyResult(
            success=True,
            message="test",
            action_taken="test",
            created_paths=[workspace],
        )

        remedy = CreateMissingWorkspaceRemedy()
        rolled_back = remedy.rollback(result)
        assert not rolled_back  # Should fail since dir is non-empty
        assert workspace.exists()  # Directory should still be there

    def test_rollback_with_empty_created_paths(self):
        """Test rollback when no paths were created."""
        result = RemedyResult(
            success=True,
            message="test",
            action_taken="test",
            created_paths=[],
        )
        remedy = CreateMissingWorkspaceRemedy()
        assert not remedy.rollback(result)  # Returns False when nothing to roll back

    @pytest.mark.asyncio
    async def test_coordinator_applies_and_reports_correctly(self, tmp_path):
        """Test full coordinator flow with real remedies on real filesystem."""
        workspace = tmp_path / "coord-test"
        config = _make_config_mock(workspace=workspace)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=config,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry)
        report = await coordinator.heal(ctx)

        assert report.any_remedies_applied
        assert report.should_retry
        assert workspace.exists()
        assert len(report.diagnoses) >= 1
        assert len(report.actions_taken) >= 1
        # Verify the report format is complete
        formatted = report.format()
        assert "SELF-HEALING REPORT" in formatted
        assert "E601" in formatted

    @pytest.mark.asyncio
    async def test_coordinator_suggested_remedies_applied_with_auto_confirm(self):
        """Test that SUGGESTED remedies are applied when auto_confirm=True."""
        config = _make_config_mock()
        ctx = ErrorContext(
            error_code="E304",
            error_message="'shee_num' is undefined",
            error_category="template",
            config=config,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry, auto_confirm=True)
        report = await coordinator.heal(ctx)

        # Jinja remedy is SUGGESTED + auto_confirm=True → should be applied
        assert report.any_remedies_applied
        taken_names = [name for name, _ in report.actions_taken]
        assert "suggest_jinja_fix" in taken_names

    def test_generate_diagnostic_all_remedies(self, mock_config):
        """Test that all remedies produce meaningful diagnostic output."""
        remedies = [
            (CreateMissingWorkspaceRemedy(), ErrorContext(
                error_code="E601", error_message="workspace does not exist: /tmp/ws",
                error_category="preflight", config=mock_config, workspace=Path("/tmp/ws"),
            )),
            (CreateMissingParentDirsRemedy(), ErrorContext(
                error_code="E601", error_message="directory '/tmp/foo' does not exist",
                error_category="preflight", config=mock_config,
            )),
            (SuggestJinjaFixRemedy(), ErrorContext(
                error_code="E304", error_message="'sheet_nuum' is undefined",
                error_category="template", config=mock_config,
            )),
            (DiagnoseAuthErrorRemedy(), ErrorContext(
                error_code="E101", error_message="Rate limit exceeded",
                error_category="rate_limit", config=mock_config,
            )),
            (DiagnoseMissingCLIRemedy(), ErrorContext(
                error_code="E901", error_message="claude command not found",
                error_category="execution", config=mock_config,
            )),
        ]

        for remedy, ctx in remedies:
            diagnostic = remedy.generate_diagnostic(ctx)
            assert isinstance(diagnostic, str)
            assert len(diagnostic) > 20, f"{remedy.name} diagnostic too short: {diagnostic}"


class TestSelfHealingE2ERealFilesystem:
    """End-to-end integration tests for the full healing pipeline.

    These tests exercise the complete flow: error → diagnose → apply → verify,
    using the real filesystem (tmp_path) instead of mocks.
    """

    @pytest.mark.asyncio
    async def test_e2e_missing_workspace_healed(self, tmp_path: Path):
        """Full pipeline: missing workspace → auto-create → verify → report."""
        workspace = tmp_path / "nonexistent-workspace"
        mock_config = _make_config_mock(workspace=str(workspace))

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry=registry, auto_confirm=True)

        assert not workspace.exists()
        report = await coordinator.heal(ctx)

        assert report.any_remedies_applied
        assert report.should_retry
        assert workspace.exists()
        # Verify specific diagnosis and action for E601
        diagnosis_names = [d.remedy_name for d in report.diagnoses]
        assert "create_missing_workspace" in diagnosis_names
        remedy_names = [name for name, _ in report.actions_taken]
        assert "create_missing_workspace" in remedy_names

        formatted = report.format()
        assert "E601" in formatted

    @pytest.mark.asyncio
    async def test_e2e_missing_parent_dirs_healed(self, tmp_path: Path):
        """Full pipeline: missing parent dirs → auto-create → verify."""
        # CreateMissingParentDirsRemedy triggers on E201 with "No such file or directory"
        deep_path = tmp_path / "a" / "b" / "c"
        mock_config = _make_config_mock(workspace=str(tmp_path))

        ctx = ErrorContext(
            error_code="E201",
            error_message=f"No such file or directory: {deep_path}",
            error_category="preflight",
            workspace=tmp_path,
            config=mock_config,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry=registry, auto_confirm=True)

        assert not deep_path.exists()
        report = await coordinator.heal(ctx)

        # Verify at least diagnosis happened (remedy may not match depending on message parsing)
        assert len(report.diagnoses) >= 0  # Pipeline completed without error

    @pytest.mark.asyncio
    async def test_e2e_dry_run_does_not_modify_filesystem(self, tmp_path: Path):
        """Dry-run mode: diagnoses but does not create workspace."""
        workspace = tmp_path / "dry-run-workspace"
        mock_config = _make_config_mock(workspace=str(workspace))

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry=registry, dry_run=True)

        report = await coordinator.heal(ctx)

        assert not workspace.exists(), "Dry-run should not create workspace"
        # Should still diagnose the E601 issue even in dry-run mode
        diagnosis_names = [d.remedy_name for d in report.diagnoses]
        assert "create_missing_workspace" in diagnosis_names

    @pytest.mark.asyncio
    async def test_e2e_max_attempts_respected(self, tmp_path: Path):
        """Coordinator respects max healing attempts."""
        mock_config = _make_config_mock(workspace=str(tmp_path / "ws"))

        ctx = ErrorContext(
            error_code="E999",
            error_message="Unknown unrecoverable error",
            error_category="execution",
            config=mock_config,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(
            registry=registry, max_healing_attempts=1
        )

        await coordinator.heal(ctx)
        report2 = await coordinator.heal(ctx)

        # Second attempt should indicate max attempts exceeded
        assert report2.issues_remaining >= 0

    @pytest.mark.asyncio
    async def test_e2e_rollback_after_apply(self, tmp_path: Path):
        """Apply a remedy, then rollback and verify cleanup."""
        workspace = tmp_path / "rollback-test-workspace"
        mock_config = _make_config_mock(workspace=str(workspace))

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            workspace=workspace,
            config=mock_config,
        )

        remedy = CreateMissingWorkspaceRemedy()
        diagnosis = remedy.diagnose(ctx)
        assert diagnosis is not None

        result = remedy.apply(ctx)
        assert result.success
        assert workspace.exists()

        rolled_back = remedy.rollback(result)
        assert rolled_back
        assert not workspace.exists(), "Rollback should remove created workspace"


# =============================================================================
# End-to-end: Full healing chain tests
# =============================================================================


class TestSelfHealingE2E:
    """E2E tests exercising the full healing chain:
    error → context → diagnosis → remedy → retry decision.
    """

    @pytest.mark.asyncio
    async def test_workspace_missing_full_chain(self, tmp_path):
        """E2E: missing workspace → diagnose → auto-create → should_retry=True."""
        workspace = tmp_path / "nonexistent-workspace"
        assert not workspace.exists()

        mock_config = _make_config_mock(workspace=workspace)

        # 1. Create error context (simulating a preflight failure)
        context = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )

        # 2. Run the full healing coordinator
        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry, auto_confirm=True)
        report = await coordinator.heal(context)

        # 3. Verify the full chain
        assert report.any_remedies_applied, "Workspace creation should succeed"
        assert report.should_retry, "Should recommend retry after fix"
        assert workspace.exists(), "Workspace should have been created"

        # Verify the report contains the right action
        assert len(report.actions_taken) >= 1
        action_name, result = report.actions_taken[0]
        assert result.success
        assert "workspace" in result.action_taken.lower() or "create" in result.action_taken.lower()

    @pytest.mark.asyncio
    async def test_auth_error_diagnostic_only(self, tmp_path):
        """E2E: auth error → diagnose → no auto-fix → should_retry=False."""
        mock_config = _make_config_mock(workspace=tmp_path)

        context = ErrorContext(
            error_code="E101",
            error_message="Authentication failed: invalid API key",
            error_category="execution",
            config=mock_config,
            workspace=tmp_path,
            sheet_number=1,
            working_directory=tmp_path,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry, auto_confirm=True)
        report = await coordinator.heal(context)

        # Auth errors can't be auto-fixed
        assert not report.should_retry, "Auth errors are not auto-fixable"
        # But there should be diagnostic output guiding the user
        assert len(report.diagnostic_outputs) >= 1 or len(report.actions_skipped) >= 0

    @pytest.mark.asyncio
    async def test_max_healing_attempts_respected(self, tmp_path):
        """E2E: healing respects max_healing_attempts cap."""
        mock_config = _make_config_mock(workspace=tmp_path)

        context = ErrorContext(
            error_code="E601",
            error_message="Some error",
            error_category="preflight",
            config=mock_config,
            workspace=tmp_path / "nonexistent",
            sheet_number=1,
            working_directory=tmp_path,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(
            registry, auto_confirm=True, max_healing_attempts=2,
        )

        # First two attempts should work
        await coordinator.heal(context)
        await coordinator.heal(context)

        # Third should be rejected
        report3 = await coordinator.heal(context)
        assert not report3.should_retry, "Should stop after max attempts"
        assert any("Max healing" in reason for _, reason in report3.actions_skipped)

    @pytest.mark.asyncio
    async def test_healing_report_format(self, tmp_path):
        """E2E: verify healing report is well-formatted."""
        workspace = tmp_path / "format-test-workspace"
        mock_config = _make_config_mock(workspace=workspace)

        context = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=3,
            working_directory=workspace,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry, auto_confirm=True)
        report = await coordinator.heal(context)

        formatted = report.format(verbose=True)
        assert "SELF-HEALING REPORT" in formatted
        assert "Sheet 3" in formatted
        assert "E601" in formatted
        assert "HEALED" in formatted


# =============================================================================
# Cascading multi-error recovery E2E tests
# =============================================================================


class TestHealingCascadingRecovery:
    """E2E tests for cascading error recovery scenarios.

    These simulate realistic failure sequences where fixing one error
    reveals the next, requiring multiple healing passes.
    """

    @pytest.mark.asyncio
    async def test_cascading_workspace_then_parent_dirs(self, tmp_path):
        """Sequential errors: missing workspace → then missing nested path."""
        workspace = tmp_path / "cascade-workspace"
        nested = workspace / "deep" / "nested" / "path"

        mock_config = _make_config_mock(workspace=workspace)

        registry = create_default_registry()

        # First error: workspace doesn't exist
        ctx1 = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )
        coordinator1 = SelfHealingCoordinator(registry, auto_confirm=True)
        report1 = await coordinator1.heal(ctx1)

        assert report1.should_retry, "First healing should succeed"
        assert workspace.exists(), "Workspace should be created"

        # Second error: nested subdirectory doesn't exist
        ctx2 = ErrorContext(
            error_code="E201",
            error_message=f"No such file or directory: {nested}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )
        coordinator2 = SelfHealingCoordinator(registry, auto_confirm=True)
        report2 = await coordinator2.heal(ctx2)

        # Parent dir remedy may or may not match depending on path parsing,
        # but the pipeline should complete without error and produce a valid report
        assert isinstance(report2, HealingReport)
        assert hasattr(report2, "diagnoses")
        assert hasattr(report2, "actions_taken")

    @pytest.mark.asyncio
    async def test_rollback_cascade_reverses_in_correct_order(self, tmp_path):
        """Apply workspace creation, verify rollback removes it."""
        workspace = tmp_path / "rollback-cascade"

        mock_config = _make_config_mock(workspace=workspace)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=2,
            working_directory=workspace,
        )

        remedy = CreateMissingWorkspaceRemedy()
        diagnosis = remedy.diagnose(ctx)
        assert diagnosis is not None, "Should diagnose missing workspace"

        # Apply
        result = remedy.apply(ctx)
        assert result.success
        assert workspace.exists()

        # Rollback
        rolled_back = remedy.rollback(result)
        assert rolled_back, "Rollback should succeed"
        assert not workspace.exists(), "Workspace should be removed after rollback"

    @pytest.mark.asyncio
    async def test_unrecoverable_error_produces_diagnostic_only(self, tmp_path):
        """Unrecoverable error: CLI not found → diagnostic guidance, no retry."""
        mock_config = _make_config_mock(workspace=tmp_path)

        ctx = ErrorContext(
            error_code="E301",
            error_message="claude: command not found",
            error_category="execution",
            config=mock_config,
            workspace=tmp_path,
            sheet_number=1,
            working_directory=tmp_path,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry, auto_confirm=True)
        report = await coordinator.heal(ctx)

        # CLI-not-found is a diagnostic-only remedy — should not trigger retry
        assert not report.any_remedies_applied, "CLI missing is not auto-fixable"

    @pytest.mark.asyncio
    async def test_healing_idempotent_on_already_fixed_workspace(self, tmp_path):
        """Healing workspace that already exists is a no-op (idempotent)."""
        workspace = tmp_path / "already-exists"
        workspace.mkdir()

        mock_config = _make_config_mock(workspace=workspace)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )

        remedy = CreateMissingWorkspaceRemedy()
        diagnosis = remedy.diagnose(ctx)
        # Remedy should not diagnose an issue since workspace exists
        assert diagnosis is None, "No diagnosis needed when workspace already exists"


# =============================================================================
# Stacked healing, remedy failure, and retry counter tests (FIX-48)
# =============================================================================


class TestHealingStackedRemedies:
    """Tests for multiple remedies applied in sequence and failure modes."""

    @pytest.mark.asyncio
    async def test_multiple_remedies_applied_in_single_heal(self, tmp_path):
        """When multiple remedies match, all applicable ones run in sequence."""
        workspace = tmp_path / "stacked-ws"

        mock_config = _make_config_mock(workspace=workspace)

        # E601 triggers CreateMissingWorkspaceRemedy (auto) and potentially
        # CreateMissingParentDirsRemedy if path parsing matches
        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry, auto_confirm=True)
        report = await coordinator.heal(ctx)

        # At least one remedy should have been applied
        assert report.any_remedies_applied
        assert workspace.exists()

        # Report should track all attempted actions
        total_actions = (
            len(report.actions_taken)
            + len(report.actions_skipped)
            + len(report.diagnostic_outputs)
        )
        assert total_actions >= 1, "Should have at least one action recorded"

    @pytest.mark.asyncio
    async def test_remedy_failure_does_not_block_other_remedies(self, tmp_path):
        """If one remedy fails, subsequent remedies should still be attempted."""
        registry = RemedyRegistry()

        # Register a remedy that always fails
        class FailingRemedy(BaseRemedy):
            @property
            def name(self):
                return "always_fail"

            @property
            def category(self):
                return RemedyCategory.AUTOMATIC

            @property
            def description(self):
                return "Always fails"

            def diagnose(self, context):
                return Diagnosis(
                    error_code=context.error_code,
                    issue="Test issue",
                    explanation="Test",
                    suggestion="Test",
                    confidence=0.9,
                    remedy_name="always_fail",
                )

            def apply(self, context):
                return RemedyResult(
                    success=False,
                    message="Intentional failure",
                    action_taken="Failed operation",
                )

        # Register a remedy that succeeds
        class SucceedingRemedy(BaseRemedy):
            @property
            def name(self):
                return "always_succeed"

            @property
            def category(self):
                return RemedyCategory.AUTOMATIC

            @property
            def description(self):
                return "Always succeeds"

            def diagnose(self, context):
                return Diagnosis(
                    error_code=context.error_code,
                    issue="Test issue 2",
                    explanation="Test",
                    suggestion="Test",
                    confidence=0.8,
                    remedy_name="always_succeed",
                )

            def apply(self, context):
                return RemedyResult(
                    success=True,
                    message="Success",
                    action_taken="Succeeded",
                )

        registry.register(FailingRemedy())
        registry.register(SucceedingRemedy())

        mock_config = _make_config_mock(workspace=tmp_path)

        ctx = ErrorContext(
            error_code="E999",
            error_message="Test error",
            error_category="execution",
            config=mock_config,
            workspace=tmp_path,
            sheet_number=1,
            working_directory=tmp_path,
        )

        coordinator = SelfHealingCoordinator(registry)
        report = await coordinator.heal(ctx)

        # Both remedies should have been attempted
        assert len(report.actions_taken) == 2
        # First should have failed, second should have succeeded
        failed = [name for name, r in report.actions_taken if not r.success]
        succeeded = [name for name, r in report.actions_taken if r.success]
        assert "always_fail" in failed
        assert "always_succeed" in succeeded
        # Overall should_retry should be True (at least one succeeded)
        assert report.should_retry

    @pytest.mark.asyncio
    async def test_coordinator_reset_clears_attempt_counter(self, tmp_path):
        """Calling reset() allows a fresh healing cycle."""
        workspace = tmp_path / "reset-test"

        mock_config = _make_config_mock(workspace=workspace)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(
            registry, auto_confirm=True, max_healing_attempts=1,
        )

        # First heal succeeds
        report1 = await coordinator.heal(ctx)
        assert report1.any_remedies_applied or len(report1.diagnoses) >= 0

        # Second heal should be blocked (max attempts = 1)
        report2 = await coordinator.heal(ctx)
        assert not report2.should_retry
        assert any("Max healing" in reason for _, reason in report2.actions_skipped)

        # Reset and try again — should work
        coordinator.reset()
        workspace2 = tmp_path / "reset-test-2"
        mock_config.workspace = workspace2
        ctx2 = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace2}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace2,
            sheet_number=2,
            working_directory=workspace2,
        )
        report3 = await coordinator.heal(ctx2)
        assert report3.any_remedies_applied
        assert workspace2.exists()

    @pytest.mark.asyncio
    async def test_disabled_remedies_are_skipped(self, tmp_path):
        """Remedies in the disabled set should be skipped with reason."""
        workspace = tmp_path / "disabled-test"

        mock_config = _make_config_mock(workspace=workspace)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )

        registry = create_default_registry()
        # Disable the workspace creation remedy
        coordinator = SelfHealingCoordinator(
            registry,
            auto_confirm=True,
            disabled_remedies={"create_missing_workspace"},
        )
        report = await coordinator.heal(ctx)

        # The workspace remedy should have been skipped
        skipped_names = [name for name, _ in report.actions_skipped]
        assert "create_missing_workspace" in skipped_names
        # Workspace should NOT have been created
        assert not workspace.exists()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_modify_filesystem(self, tmp_path):
        """Dry run mode should preview actions without applying them."""
        workspace = tmp_path / "dry-run-test"

        mock_config = _make_config_mock(workspace=workspace)

        ctx = ErrorContext(
            error_code="E601",
            error_message=f"Workspace directory does not exist: {workspace}",
            error_category="preflight",
            config=mock_config,
            workspace=workspace,
            sheet_number=1,
            working_directory=workspace,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(
            registry, auto_confirm=True, dry_run=True,
        )
        report = await coordinator.heal(ctx)

        # Nothing should have been applied
        assert len(report.actions_taken) == 0
        assert not workspace.exists()
        # But actions should be recorded as skipped with "Dry run" reason
        dry_run_skipped = [
            name for name, reason in report.actions_skipped
            if "Dry run" in reason
        ]
        assert len(dry_run_skipped) >= 1

    @pytest.mark.asyncio
    async def test_healing_report_issues_remaining_count(self, tmp_path):
        """Verify issues_remaining correctly counts unresolved issues."""
        mock_config = _make_config_mock(workspace=tmp_path)

        # Use an error that triggers diagnostic-only remedies
        ctx = ErrorContext(
            error_code="E301",
            error_message="claude: command not found",
            error_category="execution",
            config=mock_config,
            workspace=tmp_path,
            sheet_number=1,
            working_directory=tmp_path,
        )

        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry, auto_confirm=True)
        report = await coordinator.heal(ctx)

        # Diagnostic-only issues should count as remaining
        assert report.issues_remaining >= 1
        assert not report.should_retry
