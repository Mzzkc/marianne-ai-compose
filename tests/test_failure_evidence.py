"""#201: the retry-prompt failure-evidence formatter.

When a sheet is retried, the agent must be told *specifically* what failed —
not the generic "study the workspace" preamble. `format_failure_evidence`
turns the deterministic, already-captured SheetState failure signals into a
bounded, clearly-labelled UNTRUSTED-EVIDENCE block for the retry preamble.

Design pins (from the 4-model lab on #201):
- validation_details + grounding_guidance are the PRIMARY signal (GLM): the
  ErrorClassifier's error_code is routing metadata, near-useless for the
  dominant validation-failure case.
- The block is framed as UNTRUSTED EVIDENCE, not instructions (GPT-5.5):
  repo-controlled test/stderr output must not be able to redirect the agent.
- Bounded length; structured fields preferred over raw tails.
- Returns None when there's nothing useful → preamble stays generic.
"""

from __future__ import annotations

from marianne.prompts.failure_evidence import format_failure_evidence


class TestNothingUseful:
    def test_all_empty_returns_none(self) -> None:
        assert format_failure_evidence() is None

    def test_only_passed_validations_returns_none(self) -> None:
        # A retry with no failure signal and only-passing validations has
        # nothing to add — the formatter must not emit an empty block.
        details = [{"description": "file exists", "passed": True}]
        assert format_failure_evidence(validation_details=details) is None


class TestValidationDetailsPrimary:
    def test_failed_validations_listed_with_specifics(self) -> None:
        details = [
            {
                "rule_type": "command_succeeds",
                "description": "pytest passes",
                "path": "tests/",
                "passed": False,
                "expected_value": "exit 0",
                "actual_value": "exit 1",
                "suggested_fix": "fix the failing assertion in auth_test.py",
            },
            {"description": "file exists", "passed": True},
        ]
        out = format_failure_evidence(validation_details=details)
        assert out is not None
        assert "pytest passes" in out
        assert "exit 0" in out and "exit 1" in out
        assert "fix the failing assertion" in out
        # The passing validation must NOT be listed as a failure.
        assert "file exists" not in out

    def test_passed_validations_excluded(self) -> None:
        details = [
            {"description": "A", "passed": False},
            {"description": "B-passed", "passed": True},
        ]
        out = format_failure_evidence(validation_details=details)
        assert out is not None
        assert "B-passed" not in out


class TestGroundingGuidance:
    def test_grounding_guidance_included(self) -> None:
        out = format_failure_evidence(grounding_guidance="token rotation not persisted")
        assert out is not None
        assert "token rotation not persisted" in out


class TestErrorContext:
    def test_error_code_and_message_included(self) -> None:
        out = format_failure_evidence(error_code="E601", error_message="validation failed")
        assert out is not None
        assert "E601" in out
        assert "validation failed" in out

    def test_repeated_error_history_surfaced(self) -> None:
        out = format_failure_evidence(
            error_code="E601", error_history=["E601", "E601", "E601"]
        )
        assert out is not None
        # A repeated-failure signal tells the agent it's stuck in a loop.
        assert "E601" in out


class TestStderrSelectivity:
    def test_stderr_bounded(self) -> None:
        big = "x" * 10000
        out = format_failure_evidence(error_code="E003", stderr_tail=big)
        assert out is not None
        # Stderr must be bounded well under its raw size.
        assert len(out) < 6000

    def test_stderr_omitted_when_validations_failed(self) -> None:
        # For a clean validation failure, the agent's own stderr is noise —
        # the validation specifics are what matter (lab: GLM/GPT).
        details = [{"description": "must contain TODO", "passed": False}]
        out = format_failure_evidence(
            validation_details=details, stderr_tail="SOME_NOISY_STDERR_MARKER"
        )
        assert out is not None
        assert "SOME_NOISY_STDERR_MARKER" not in out


class TestDirective:
    """Showing evidence is not enough — the block must DIRECT a fix (composer)."""

    def test_validation_block_is_imperative(self) -> None:
        details = [{"description": "tests pass", "passed": False}]
        out = format_failure_evidence(validation_details=details)
        assert out is not None
        assert "MUST" in out  # directive, not merely informative

    def test_error_only_block_directs_a_fix(self) -> None:
        out = format_failure_evidence(error_code="E001", error_message="timed out")
        assert out is not None
        low = out.lower()
        assert "failed" in low and "do not repeat" in low

    def test_repeated_failure_directs_strategy_change(self) -> None:
        # Same code three times → tell the agent to change approach, not retry blind.
        out = format_failure_evidence(
            error_code="E601", error_history=["E601", "E601", "E601"]
        )
        assert out is not None
        low = out.lower()
        assert "change strategy" in low
        assert "3" in out  # surfaces the repeat count

    def test_single_occurrence_is_not_a_repeat_directive(self) -> None:
        out = format_failure_evidence(error_code="E601", error_history=["E601"])
        assert out is not None
        assert "change strategy" not in out.lower()


class TestUntrustedFraming:
    def test_block_is_labelled_untrusted_not_instructions(self) -> None:
        out = format_failure_evidence(grounding_guidance="do the thing")
        assert out is not None
        low = out.lower()
        # The block must defuse prompt-injection: it is evidence, not orders,
        # and the original task takes precedence.
        assert "evidence" in low
        assert "not" in low and "instruction" in low

    def test_block_is_delimited(self) -> None:
        out = format_failure_evidence(error_code="E601")
        assert out is not None
        # A clearly delimited block so the agent can bound the untrusted region.
        assert "<failure-evidence" in out
        assert "</failure-evidence>" in out
