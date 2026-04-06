"""Tests for mozart.execution.grounding module.

Tests for grounding hook configuration, factory, and integration.
Evolution v9: Grounding Engine Integration.
"""

import hashlib
import tempfile
from pathlib import Path

import pytest

from marianne.core.config import GroundingConfig, GroundingHookConfig
from marianne.execution.grounding import (
    FileChecksumGroundingHook,
    GroundingContext,
    GroundingEngine,
    GroundingPhase,
    GroundingResult,
    create_hook_from_config,
)


class TestGroundingHookConfig:
    """Tests for GroundingHookConfig model."""

    def test_defaults(self):
        """Test default values are applied."""
        config = GroundingHookConfig(type="file_checksum")
        assert config.type == "file_checksum"
        assert config.name is None
        assert config.expected_checksums == {}
        assert config.checksum_algorithm == "sha256"

    def test_custom_values(self):
        """Test custom values override defaults."""
        config = GroundingHookConfig(
            type="file_checksum",
            name="my_custom_hook",
            expected_checksums={"file.txt": "abc123"},
            checksum_algorithm="md5",
        )
        assert config.name == "my_custom_hook"
        assert config.expected_checksums == {"file.txt": "abc123"}
        assert config.checksum_algorithm == "md5"

    def test_checksum_algorithm_literal(self):
        """Test checksum_algorithm only accepts valid values."""
        # Valid values should work
        config_sha = GroundingHookConfig(type="file_checksum", checksum_algorithm="sha256")
        config_md5 = GroundingHookConfig(type="file_checksum", checksum_algorithm="md5")
        assert config_sha.checksum_algorithm == "sha256"
        assert config_md5.checksum_algorithm == "md5"


class TestGroundingConfig:
    """Tests for GroundingConfig model with hooks list."""

    def test_defaults(self):
        """Test default values are applied."""
        config = GroundingConfig()
        assert config.enabled is False
        assert config.hooks == []
        assert config.fail_on_grounding_failure is True
        assert config.escalate_on_failure is True
        assert config.timeout_seconds == 30.0

    def test_with_hooks(self):
        """Test configuration with hooks list."""
        config = GroundingConfig(
            enabled=True,
            hooks=[
                GroundingHookConfig(
                    type="file_checksum",
                    name="artifact_check",
                    expected_checksums={"output.py": "hash123"},
                )
            ],
        )
        assert config.enabled is True
        assert len(config.hooks) == 1
        assert config.hooks[0].name == "artifact_check"

    def test_multiple_hooks(self):
        """Test configuration with multiple hooks."""
        config = GroundingConfig(
            enabled=True,
            hooks=[
                GroundingHookConfig(type="file_checksum", name="hook1"),
                GroundingHookConfig(type="file_checksum", name="hook2"),
            ],
        )
        assert len(config.hooks) == 2


class TestCreateHookFromConfig:
    """Tests for create_hook_from_config factory function."""

    def test_file_checksum_hook_creation(self):
        """Test creating FileChecksumGroundingHook from config."""
        config = GroundingHookConfig(
            type="file_checksum",
            expected_checksums={"test.txt": "abc123"},
            checksum_algorithm="sha256",
        )
        hook = create_hook_from_config(config)

        assert isinstance(hook, FileChecksumGroundingHook)
        assert hook.name == "file_checksum"  # Default name

    def test_hook_with_custom_name(self):
        """Test hook uses custom name from config."""
        config = GroundingHookConfig(
            type="file_checksum",
            name="my_custom_hook",
        )
        hook = create_hook_from_config(config)

        assert hook.name == "my_custom_hook"

    def test_hook_default_name_fallback(self):
        """Test hook falls back to type when name is None."""
        config = GroundingHookConfig(type="file_checksum", name=None)
        hook = create_hook_from_config(config)

        assert hook.name == "file_checksum"

    def test_invalid_config_type(self):
        """Test factory raises TypeError for invalid config type."""
        with pytest.raises(TypeError, match="Expected GroundingHookConfig"):
            create_hook_from_config({"type": "file_checksum"})  # type: ignore

    def test_unknown_hook_type(self):
        """Test factory raises ValueError for unknown hook type."""
        # Create a config-like object with unknown type
        # Since GroundingHookConfig.type is a Literal, we need to bypass validation
        config = GroundingHookConfig(type="file_checksum")
        # Directly modify the type (bypassing validation)
        object.__setattr__(config, "type", "unknown_type")

        with pytest.raises(ValueError, match="Unknown grounding hook type"):
            create_hook_from_config(config)


class TestFileChecksumGroundingHook:
    """Tests for FileChecksumGroundingHook validation."""

    def test_name_property_default(self):
        """Test name property returns default."""
        hook = FileChecksumGroundingHook()
        assert hook.name == "file_checksum"

    def test_name_property_custom(self):
        """Test name property returns custom name."""
        hook = FileChecksumGroundingHook(name="custom_name")
        assert hook.name == "custom_name"

    def test_phase_property(self):
        """Test phase property returns POST_VALIDATION."""
        hook = FileChecksumGroundingHook()
        assert hook.phase == GroundingPhase.POST_VALIDATION

    @pytest.mark.asyncio
    async def test_validate_no_checksums(self):
        """Test validation passes with no checksums configured."""
        hook = FileChecksumGroundingHook()
        context = GroundingContext(
            job_id="test_job",
            sheet_num=1,
            prompt="test prompt",
            output="test output",
        )

        result = await hook.validate(context)

        assert result.passed is True
        assert result.hook_name == "file_checksum"
        assert "No checksums configured" in result.message

    @pytest.mark.asyncio
    async def test_validate_file_exists_correct_checksum(self):
        """Test validation passes when file has correct checksum."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test content")
            temp_path = f.name

        try:
            # Calculate expected checksum
            hasher = hashlib.sha256()
            with open(temp_path, "rb") as f:
                hasher.update(f.read())
            expected_hash = hasher.hexdigest()

            hook = FileChecksumGroundingHook(
                expected_checksums={temp_path: expected_hash}
            )
            context = GroundingContext(
                job_id="test_job",
                sheet_num=1,
                prompt="test",
                output="test",
            )

            result = await hook.validate(context)

            assert result.passed is True
            assert "1 file checksum(s) validated" in result.message
        finally:
            Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_validate_file_wrong_checksum(self):
        """Test validation fails when file has wrong checksum."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test content")
            temp_path = f.name

        try:
            hook = FileChecksumGroundingHook(
                expected_checksums={temp_path: "wrong_hash_value"}
            )
            context = GroundingContext(
                job_id="test_job",
                sheet_num=1,
                prompt="test",
                output="test",
            )

            result = await hook.validate(context)

            assert result.passed is False
            assert "Checksum validation failed" in result.message
        finally:
            Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_validate_file_not_found(self):
        """Test validation fails when file doesn't exist."""
        hook = FileChecksumGroundingHook(
            expected_checksums={"/nonexistent/file.txt": "abc123"}
        )
        context = GroundingContext(
            job_id="test_job",
            sheet_num=1,
            prompt="test",
            output="test",
        )

        result = await hook.validate(context)

        assert result.passed is False
        assert "file not found" in result.message.lower() or "mismatches" in result.details


class TestGroundingEngine:
    """Tests for GroundingEngine hook registration and execution."""

    def test_add_hook(self):
        """Test adding hooks to engine."""
        engine = GroundingEngine()
        hook = FileChecksumGroundingHook(name="test_hook")

        engine.add_hook(hook)

        assert engine.get_hook_count() == 1

    def test_get_hook_count(self):
        """Test get_hook_count returns correct count."""
        engine = GroundingEngine()

        assert engine.get_hook_count() == 0

        engine.add_hook(FileChecksumGroundingHook(name="hook1"))
        assert engine.get_hook_count() == 1

        engine.add_hook(FileChecksumGroundingHook(name="hook2"))
        assert engine.get_hook_count() == 2

    @pytest.mark.asyncio
    async def test_run_hooks_empty(self):
        """Test run_hooks with no hooks returns empty list."""
        engine = GroundingEngine()
        context = GroundingContext(
            job_id="test", sheet_num=1, prompt="", output=""
        )

        results = await engine.run_hooks(context, GroundingPhase.POST_VALIDATION)

        assert results == []

    @pytest.mark.asyncio
    async def test_run_hooks_phase_filtering(self):
        """Test run_hooks filters by phase."""
        engine = GroundingEngine()
        hook = FileChecksumGroundingHook()  # POST_VALIDATION phase

        engine.add_hook(hook)

        context = GroundingContext(
            job_id="test", sheet_num=1, prompt="", output=""
        )

        # PRE_VALIDATION should not run POST_VALIDATION hook
        pre_results = await engine.run_hooks(context, GroundingPhase.PRE_VALIDATION)
        assert len(pre_results) == 0

        # POST_VALIDATION should run the hook
        post_results = await engine.run_hooks(context, GroundingPhase.POST_VALIDATION)
        assert len(post_results) == 1

    def test_aggregate_results_all_pass(self):
        """Test aggregate_results with all passing results."""
        engine = GroundingEngine()
        results = [
            GroundingResult(passed=True, hook_name="hook1", message="OK"),
            GroundingResult(passed=True, hook_name="hook2", message="OK"),
        ]

        passed, summary = engine.aggregate_results(results)

        assert passed is True
        assert "2 grounding check(s) passed" in summary

    def test_aggregate_results_with_failures(self):
        """Test aggregate_results with some failures."""
        engine = GroundingEngine()
        results = [
            GroundingResult(passed=True, hook_name="hook1", message="OK"),
            GroundingResult(passed=False, hook_name="hook2", message="Failed"),
        ]

        passed, summary = engine.aggregate_results(results)

        assert passed is False
        assert "1/2 grounding check(s) failed" in summary

    def test_aggregate_results_empty(self):
        """Test aggregate_results with empty list."""
        engine = GroundingEngine()

        passed, summary = engine.aggregate_results([])

        assert passed is True
        assert "No grounding hooks executed" in summary


class TestGroundingIntegration:
    """Integration tests for grounding hook config -> engine flow."""

    def test_config_to_engine_registration(self):
        """Test full flow: config -> factory -> engine registration."""
        # Create config with hooks
        config = GroundingConfig(
            enabled=True,
            hooks=[
                GroundingHookConfig(
                    type="file_checksum",
                    name="artifact_validator",
                    expected_checksums={"output.txt": "hash123"},
                ),
            ],
        )

        # Create engine with config
        engine = GroundingEngine(hooks=[], config=config)

        # Register hooks from config
        for hook_config in config.hooks:
            hook = create_hook_from_config(hook_config)
            engine.add_hook(hook)

        # Verify registration
        assert engine.get_hook_count() == 1


class TestGroundingDecisionContext:
    """Tests for GroundingDecisionContext dataclass.

    v10 Evolution: Grounding→Completion Integration tests.
    """

    def test_disabled_context(self):
        """Test disabled() factory method."""
        from marianne.execution.runner import GroundingDecisionContext

        ctx = GroundingDecisionContext.disabled()
        assert ctx.passed is True
        assert ctx.hooks_executed == 0
        assert "not enabled" in ctx.message.lower()
        assert ctx.confidence == 1.0

    def test_from_empty_results(self):
        """Test from_results with empty list."""
        from marianne.execution.runner import GroundingDecisionContext

        ctx = GroundingDecisionContext.from_results([])
        assert ctx.passed is True
        assert ctx.hooks_executed == 0
        assert "No grounding hooks executed" in ctx.message

    def test_from_single_passing_result(self):
        """Test from_results with single passing result."""
        from marianne.execution.runner import GroundingDecisionContext

        result = GroundingResult(
            passed=True,
            hook_name="test_hook",
            message="All checks passed",
            confidence=0.95,
        )
        ctx = GroundingDecisionContext.from_results([result])

        assert ctx.passed is True
        assert ctx.hooks_executed == 1
        assert ctx.confidence == 0.95
        assert ctx.should_escalate is False
        assert "1 grounding check(s) passed" in ctx.message

    def test_from_single_failing_result(self):
        """Test from_results with single failing result."""
        from marianne.execution.runner import GroundingDecisionContext

        result = GroundingResult(
            passed=False,
            hook_name="checksum_hook",
            message="Hash mismatch",
            confidence=0.3,
            recovery_guidance="Re-run with updated checksums",
        )
        ctx = GroundingDecisionContext.from_results([result])

        assert ctx.passed is False
        assert ctx.hooks_executed == 1
        assert ctx.confidence == 0.3
        assert "1/1 grounding check(s) failed" in ctx.message
        assert "checksum_hook: Hash mismatch" in ctx.message
        assert ctx.recovery_guidance == "Re-run with updated checksums"

    def test_from_mixed_results(self):
        """Test from_results with mixed pass/fail."""
        from marianne.execution.runner import GroundingDecisionContext

        results = [
            GroundingResult(passed=True, hook_name="hook1", confidence=0.9),
            GroundingResult(passed=False, hook_name="hook2", message="Failed", confidence=0.4),
            GroundingResult(passed=True, hook_name="hook3", confidence=0.8),
        ]
        ctx = GroundingDecisionContext.from_results(results)

        assert ctx.passed is False  # One failure = overall fail
        assert ctx.hooks_executed == 3
        # Average: (0.9 + 0.4 + 0.8) / 3 = 0.7
        assert abs(ctx.confidence - 0.7) < 0.01
        assert "1/3 grounding check(s) failed" in ctx.message

    def test_escalation_flag_propagates(self):
        """Test that should_escalate propagates from any result."""
        from marianne.execution.runner import GroundingDecisionContext

        results = [
            GroundingResult(passed=True, hook_name="hook1"),
            GroundingResult(
                passed=False, hook_name="hook2", should_escalate=True
            ),
        ]
        ctx = GroundingDecisionContext.from_results(results)

        assert ctx.should_escalate is True

    def test_multiple_recovery_guidance_combined(self):
        """Test that recovery guidance from multiple failures is combined."""
        from marianne.execution.runner import GroundingDecisionContext

        results = [
            GroundingResult(
                passed=False,
                hook_name="hook1",
                recovery_guidance="Fix A",
            ),
            GroundingResult(
                passed=False,
                hook_name="hook2",
                recovery_guidance="Fix B",
            ),
        ]
        ctx = GroundingDecisionContext.from_results(results)

        assert ctx.recovery_guidance is not None
        assert "Fix A" in ctx.recovery_guidance
        assert "Fix B" in ctx.recovery_guidance


class TestGroundingCompletionIntegration:
    """Tests for grounding→completion mode integration.

    v10 Evolution: Verifies grounding confidence affects decision-making.
    """

    def test_decide_next_action_without_grounding(self):
        """Test _decide_next_action works without grounding context."""
        from unittest.mock import MagicMock

        from marianne.execution.runner import JobRunner

        # Create minimal JobRunner mock
        config = MagicMock()
        config.retry.completion_threshold_percent = 60.0
        config.retry.max_completion_attempts = 3
        config.learning.high_confidence_threshold = 0.8
        config.learning.min_confidence_threshold = 0.3
        config.learning.escalation_enabled = False

        validation_result = MagicMock()
        validation_result.aggregate_confidence = 0.75
        validation_result.executed_pass_percentage = 70.0
        validation_result.get_semantic_summary.return_value = {}
        validation_result.get_actionable_hints.return_value = []

        runner = MagicMock(spec=JobRunner)
        runner.config = config
        runner.escalation_handler = None

        # Call the method directly (not through mock)
        mode, reason, hints = JobRunner._decide_next_action(
            runner,
            validation_result=validation_result,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=None,
        )

        # Should work without grounding
        assert mode is not None
        assert reason is not None

    def test_decide_next_action_with_grounding_adjusts_confidence(self):
        """Test grounding confidence adjusts overall confidence."""
        from unittest.mock import MagicMock

        from marianne.execution.runner import GroundingDecisionContext, JobRunner

        config = MagicMock()
        config.retry.completion_threshold_percent = 60.0
        config.retry.max_completion_attempts = 3
        config.learning.high_confidence_threshold = 0.8
        config.learning.min_confidence_threshold = 0.3
        config.learning.escalation_enabled = False

        # High validation confidence (0.85) but low grounding confidence (0.4)
        validation_result = MagicMock()
        validation_result.aggregate_confidence = 0.85
        validation_result.executed_pass_percentage = 80.0
        validation_result.get_semantic_summary.return_value = {}
        validation_result.get_actionable_hints.return_value = []

        grounding_ctx = GroundingDecisionContext(
            passed=False,
            message="Checksum mismatch",
            confidence=0.4,
            hooks_executed=1,
        )

        runner = MagicMock(spec=JobRunner)
        runner.config = config
        runner.escalation_handler = None

        mode, reason, hints = JobRunner._decide_next_action(
            runner,
            validation_result=validation_result,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=grounding_ctx,
        )

        # Combined confidence = 0.85 * 0.7 + 0.4 * 0.3 = 0.715
        # Should include grounding info in reason
        assert "grounding" in reason.lower()

    def test_decide_next_action_grounding_triggers_escalation(self):
        """Test grounding should_escalate triggers escalation mode."""
        from unittest.mock import MagicMock

        from marianne.execution.runner import (
            GroundingDecisionContext,
            JobRunner,
            SheetExecutionMode,
        )

        config = MagicMock()
        config.retry.completion_threshold_percent = 60.0
        config.retry.max_completion_attempts = 3
        config.learning.high_confidence_threshold = 0.8
        config.learning.min_confidence_threshold = 0.3
        config.learning.escalation_enabled = True

        validation_result = MagicMock()
        validation_result.aggregate_confidence = 0.85
        validation_result.executed_pass_percentage = 80.0
        validation_result.get_semantic_summary.return_value = {}
        validation_result.get_actionable_hints.return_value = []

        grounding_ctx = GroundingDecisionContext(
            passed=False,
            message="Critical grounding failure",
            confidence=0.5,
            should_escalate=True,
            hooks_executed=1,
        )

        runner = MagicMock(spec=JobRunner)
        runner.config = config
        runner.escalation_handler = MagicMock()  # Handler available

        mode, reason, hints = JobRunner._decide_next_action(
            runner,
            validation_result=validation_result,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=grounding_ctx,
        )

        assert mode == SheetExecutionMode.ESCALATE
        assert "escalation" in reason.lower()

    def test_grounding_recovery_guidance_in_hints(self):
        """Test grounding recovery guidance is included in hints."""
        from unittest.mock import MagicMock

        from marianne.execution.runner import GroundingDecisionContext, JobRunner

        config = MagicMock()
        config.retry.completion_threshold_percent = 60.0
        config.retry.max_completion_attempts = 3
        config.learning.high_confidence_threshold = 0.8
        config.learning.min_confidence_threshold = 0.3
        config.learning.escalation_enabled = False

        validation_result = MagicMock()
        validation_result.aggregate_confidence = 0.75
        validation_result.executed_pass_percentage = 70.0
        validation_result.get_semantic_summary.return_value = {}
        validation_result.get_actionable_hints.return_value = ["existing hint"]

        grounding_ctx = GroundingDecisionContext(
            passed=False,
            message="Hash mismatch",
            confidence=0.6,
            recovery_guidance="Update expected checksums",
            hooks_executed=1,
        )

        runner = MagicMock(spec=JobRunner)
        runner.config = config
        runner.escalation_handler = None

        mode, reason, hints = JobRunner._decide_next_action(
            runner,
            validation_result=validation_result,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=grounding_ctx,
        )

        # Recovery guidance should be in hints
        assert any("Grounding" in h and "Update expected checksums" in h for h in hints)
