"""Tests for _decide_next_action() core decision branches.

FIX-11e: Tests the retry/completion/escalation decision logic
without grounding context dependency. Covers all branches:
- High confidence + majority passed -> COMPLETION
- Completion attempts exhausted -> RETRY fallback
- Low confidence + escalation available -> ESCALATE
- Low confidence + auto-apply -> RETRY with auto-apply
- Low confidence + no escalation -> RETRY fallback
- Medium confidence + majority passed -> COMPLETION
- Medium confidence + minority passed -> RETRY
- Grounding context integration (escalate, blended confidence)
"""

from unittest.mock import AsyncMock, MagicMock, patch

from marianne.core.config import JobConfig, ValidationRule
from marianne.execution.runner import JobRunner
from marianne.execution.runner.models import GroundingDecisionContext, SheetExecutionMode
from marianne.execution.validation import SheetValidationResult, ValidationResult


def _make_config(**overrides: object) -> JobConfig:
    """Build a minimal JobConfig with optional overrides for learning/retry."""
    base = {
        "name": "test-decision",
        "description": "Test decision logic",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": 30},
        "prompt": {"template": "Process {{ sheet_num }}."},
        "retry": {"max_retries": 3, "max_completion_attempts": 3,
                  "completion_threshold_percent": 50.0},
        "validations": [],
        "pause_between_sheets_seconds": 0,
    }
    # Merge learning overrides
    learning = {
        "high_confidence_threshold": 0.7,
        "min_confidence_threshold": 0.3,
        "escalation_enabled": False,
        "auto_apply_enabled": False,
        "auto_apply_trust_threshold": 0.85,
    }
    if "learning" in overrides:
        learning.update(overrides.pop("learning"))  # type: ignore[union-attr]
    base["learning"] = learning  # type: ignore[assignment]
    base.update(overrides)  # type: ignore[arg-type]
    return JobConfig.model_validate(base)


def _make_validation_result(
    pass_pct: float,
    confidence: float,
    num_results: int = 10,
    dominant_category: str | None = None,
) -> SheetValidationResult:
    """Build a SheetValidationResult with controlled pass percentage and confidence."""
    passed_count = int(num_results * pass_pct / 100.0)
    results = []
    for i in range(num_results):
        passed = i < passed_count
        rule = ValidationRule(
            type="file_exists",
            description=f"check-{i}",
            path=f"/tmp/file-{i}",
        )
        results.append(ValidationResult(
            rule=rule,
            passed=passed,
            confidence=confidence,
            failure_category=dominant_category if not passed else None,
        ))
    return SheetValidationResult(sheet_num=1, results=results)


def _make_runner(config: JobConfig | None = None) -> JobRunner:
    """Build a minimal JobRunner for testing decision methods."""
    cfg = config or _make_config()
    backend = AsyncMock()
    state_backend = AsyncMock()
    runner = JobRunner(config=cfg, backend=backend, state_backend=state_backend)
    return runner


class TestDecideNextActionCoreBranches:
    """Tests for _decide_next_action without grounding context."""

    def test_high_confidence_majority_passed_returns_completion(self) -> None:
        """High confidence (>0.7) + majority passed (>50%) -> COMPLETION mode."""
        runner = _make_runner()
        val = _make_validation_result(pass_pct=80.0, confidence=0.9)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
        )

        assert mode == SheetExecutionMode.COMPLETION
        assert "high confidence" in reason.lower()
        assert "0.9" in reason or "0.90" in reason

    def test_high_confidence_completion_exhausted_returns_retry(self) -> None:
        """High confidence but completion attempts exhausted -> RETRY fallback."""
        runner = _make_runner()
        val = _make_validation_result(pass_pct=80.0, confidence=0.9)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=3,  # exhausted (max_completion_attempts=3)
        )

        assert mode == SheetExecutionMode.RETRY
        assert "exhausted" in reason.lower()

    def test_low_confidence_no_escalation_returns_retry(self) -> None:
        """Low confidence (<0.3) + escalation not available -> RETRY."""
        runner = _make_runner()
        val = _make_validation_result(pass_pct=20.0, confidence=0.1)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
        )

        assert mode == SheetExecutionMode.RETRY
        assert "low confidence" in reason.lower()
        assert "not available" in reason.lower()

    def test_low_confidence_with_escalation_returns_escalate(self) -> None:
        """Low confidence (<0.3) + escalation enabled + handler -> ESCALATE."""
        config = _make_config(learning={"escalation_enabled": True})
        runner = _make_runner(config)
        # Provide a mock escalation handler
        runner.escalation_handler = MagicMock()

        val = _make_validation_result(pass_pct=20.0, confidence=0.1)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
        )

        assert mode == SheetExecutionMode.ESCALATE
        assert "low confidence" in reason.lower()

    def test_low_confidence_auto_apply_bypasses_escalation(self) -> None:
        """Low confidence + auto_apply enabled with high-trust patterns -> RETRY (bypass)."""
        config = _make_config(learning={
            "escalation_enabled": True,
            "auto_apply_enabled": True,
            "auto_apply_trust_threshold": 0.85,
        })
        runner = _make_runner(config)
        runner.escalation_handler = MagicMock()

        # Mock _can_auto_apply to return True
        with patch.object(runner, "_can_auto_apply", return_value=True):
            val = _make_validation_result(pass_pct=20.0, confidence=0.1)

            mode, reason, hints = runner._decide_next_action(
                validation_result=val,
                normal_attempts=1,
                completion_attempts=0,
            )

        assert mode == SheetExecutionMode.RETRY
        assert "auto-apply" in reason.lower()

    def test_medium_confidence_majority_passed_returns_completion(self) -> None:
        """Medium confidence (0.3-0.7) + majority passed -> COMPLETION."""
        runner = _make_runner()
        val = _make_validation_result(pass_pct=70.0, confidence=0.5)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
        )

        assert mode == SheetExecutionMode.COMPLETION
        assert "medium confidence" in reason.lower()

    def test_medium_confidence_minority_passed_returns_retry(self) -> None:
        """Medium confidence + minority passed (<50%) -> RETRY."""
        runner = _make_runner()
        val = _make_validation_result(pass_pct=30.0, confidence=0.5)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
        )

        assert mode == SheetExecutionMode.RETRY
        assert "medium confidence" in reason.lower()

    def test_semantic_hints_passed_through(self) -> None:
        """Semantic hints from validation result are included in response."""
        runner = _make_runner()
        rule = ValidationRule(
            type="file_exists",
            description="check output",
            path="/tmp/output",
        )
        results = [
            ValidationResult(
                rule=rule,
                passed=False,
                confidence=0.5,
                failure_category="missing",
                suggested_fix="Create the output file",
            ),
        ]
        val = SheetValidationResult(sheet_num=1, results=results)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
        )

        assert len(hints) >= 1
        assert any("output file" in h.lower() for h in hints)


class TestDecideNextActionWithGrounding:
    """Tests for _decide_next_action with grounding context integration."""

    def test_grounding_escalation_request(self) -> None:
        """Grounding hook requesting escalation -> ESCALATE (if available)."""
        config = _make_config(learning={"escalation_enabled": True})
        runner = _make_runner(config)
        runner.escalation_handler = MagicMock()

        val = _make_validation_result(pass_pct=80.0, confidence=0.9)
        grounding = GroundingDecisionContext(
            passed=False,
            message="External check failed",
            confidence=0.2,
            should_escalate=True,
            hooks_executed=1,
        )

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=grounding,
        )

        assert mode == SheetExecutionMode.ESCALATE
        assert "grounding" in reason.lower()

    def test_grounding_blends_confidence(self) -> None:
        """Grounding confidence blended 70/30 with validation confidence."""
        runner = _make_runner()
        # High validation confidence (0.9) but low grounding confidence (0.1)
        # Blended: 0.9 * 0.7 + 0.1 * 0.3 = 0.63 + 0.03 = 0.66
        # This is below high_threshold (0.7) so should not be "high confidence"
        val = _make_validation_result(pass_pct=80.0, confidence=0.9)
        grounding = GroundingDecisionContext(
            passed=True,
            message="check passed",
            confidence=0.1,
            hooks_executed=1,
        )

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=grounding,
        )

        # Blended confidence = 0.66, below 0.7 threshold, so it's medium confidence zone
        assert mode == SheetExecutionMode.COMPLETION
        assert "medium confidence" in reason.lower()

    def test_grounding_recovery_guidance_added_to_hints(self) -> None:
        """Grounding recovery guidance is prepended to semantic hints."""
        runner = _make_runner()
        val = _make_validation_result(pass_pct=80.0, confidence=0.5)
        grounding = GroundingDecisionContext(
            passed=False,
            message="checksum mismatch",
            confidence=0.8,
            recovery_guidance="Regenerate the output file",
            hooks_executed=1,
        )

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=grounding,
        )

        assert any("Grounding" in h for h in hints)
        assert any("Regenerate" in h for h in hints)

    def test_no_grounding_context_is_default(self) -> None:
        """Without grounding context, no blending or escalation logic runs."""
        runner = _make_runner()
        val = _make_validation_result(pass_pct=80.0, confidence=0.9)

        mode, reason, hints = runner._decide_next_action(
            validation_result=val,
            normal_attempts=1,
            completion_attempts=0,
            grounding_context=None,
        )

        assert mode == SheetExecutionMode.COMPLETION
        assert "grounding" not in reason.lower()
