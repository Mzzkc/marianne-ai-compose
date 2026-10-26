"""Gated, guidance-only proposals for specification and score redesign."""

from marianne.healing.context import ErrorContext
from marianne.healing.diagnosis import Diagnosis
from marianne.healing.remedies.base import BaseRemedy, RemedyCategory, RemedyResult


class SuggestSpecFixRemedy(BaseRemedy):
    """Identify spec configuration problems without editing source documents."""

    name = "suggest_spec_fix"
    category = RemedyCategory.DIAGNOSTIC
    description = "Review specification fragments and repair their score configuration"

    def diagnose(self, context: ErrorContext) -> Diagnosis | None:
        if context.error_code not in {"E301", "E302", "E303", "E304"}:
            return None
        if not any(word in context.error_message.lower() for word in ("spec", "corpus")):
            return None
        return Diagnosis(
            error_code=context.error_code,
            issue="Specification corpus configuration failed",
            explanation=context.error_message,
            suggestion=self.preview(context),
            confidence=0.8,
            remedy_name=self.name,
        )

    def preview(self, context: ErrorContext) -> str:
        fragments = context.metadata.get("spec_fragments", [])
        names = ", ".join(str(f.get("name", "unknown")) for f in fragments)
        warning = context.metadata.get("spec_load_error", "")
        return (
            f"Review spec.spec_dir and fragment content in {context.config_path or 'the score'}. "
            f"Loaded fragments: {names or '(none)'}. {warning} "
            "Correct the spec or score configuration, then validate before retrying. "
            "Proposal only; no files have been edited."
        )

    def generate_diagnostic(self, context: ErrorContext) -> str:
        return self.preview(context)

    def apply(self, context: ErrorContext) -> RemedyResult:
        return RemedyResult(False, self.preview(context), "manual spec fix required")


class SuggestRecomposeRemedy(BaseRemedy):
    """Suggest redesign for repeated output failures, never for auth/capacity errors."""

    name = "suggest_recompose"
    category = RemedyCategory.DIAGNOSTIC
    description = "Propose re-compose after exhausted output validation failures"

    def diagnose(self, context: ErrorContext) -> Diagnosis | None:
        if context.error_code not in {"E201", "E202", "E203", "E209"}:
            return None
        if not context.config_path or context.max_retries <= 0:
            return None
        if context.retry_count < context.max_retries:
            return None
        return Diagnosis(
            error_code=context.error_code,
            issue="Output validation still fails after retries",
            explanation="Repeated attempts did not satisfy the score's output requirements.",
            suggestion=self.preview(context),
            confidence=0.65,
            remedy_name=self.name,
        )

    def preview(self, context: ErrorContext) -> str:
        return (
            f"Consider re-compose of {context.config_path or 'the score'}: review the failed "
            f"sheet {context.sheet_number}, its attempt history, spec fragments, prompt, "
            "and validations before generating a revised score. "
            "Proposal only; no re-compose has been executed."
        )

    def generate_diagnostic(self, context: ErrorContext) -> str:
        return self.preview(context)

    def apply(self, context: ErrorContext) -> RemedyResult:
        return RemedyResult(False, self.preview(context), "manual re-compose required")
