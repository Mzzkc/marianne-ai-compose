"""Diagnosis engine for analyzing errors and suggesting remedies.

The diagnosis engine takes an ErrorContext and returns a list of
Diagnosis objects, sorted by confidence. Each diagnosis identifies
what went wrong, why, and how to fix it.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from marianne.core.logging import get_logger

_logger = get_logger("healing.diagnosis")

if TYPE_CHECKING:
    from marianne.healing.context import ErrorContext
    from marianne.healing.registry import RemedyRegistry
    from marianne.healing.remedies.base import RemedyCategory


@dataclass
class Diagnosis:
    """Result of analyzing an error.

    Contains information about what went wrong and how to fix it.
    Multiple diagnoses may be returned for a single error, sorted
    by confidence (highest first).

    Attributes:
        error_code: The error code being diagnosed
        issue: What went wrong (human-readable summary)
        explanation: Why the error happened (detailed)
        suggestion: How to fix it (actionable)
        confidence: 0.0-1.0, higher = more certain this diagnosis is correct
        remedy_name: Name of the remedy that can fix this (if any)
        requires_confirmation: True for suggested remedies
        context: Extra data for the remedy to use
    """

    error_code: str
    issue: str
    explanation: str
    suggestion: str
    confidence: float
    remedy_name: str | None = None
    requires_confirmation: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def format_short(self) -> str:
        """Format as a single-line summary."""
        return f"[{self.error_code}] {self.issue} ({self.confidence:.0%} confidence)"

    def format_full(self) -> str:
        """Format with full details."""
        lines = [
            f"Error: {self.error_code}",
            f"Issue: {self.issue}",
            f"Explanation: {self.explanation}",
            f"Suggestion: {self.suggestion}",
            f"Confidence: {self.confidence:.0%}",
        ]
        if self.remedy_name:
            lines.append(f"Remedy: {self.remedy_name}")
        return "\n".join(lines)


class DiagnosisEngine:
    """Diagnoses errors and suggests remedies.

    Takes an ErrorContext and queries all registered remedies
    to find applicable diagnoses, returning them sorted by
    confidence (highest first).

    Example:
        registry = create_default_registry()
        engine = DiagnosisEngine(registry)

        context = ErrorContext.from_preflight_error(...)
        diagnoses = engine.diagnose(context)

        for diagnosis in diagnoses:
            print(f"{diagnosis.issue}: {diagnosis.suggestion}")
    """

    def __init__(self, remedy_registry: "RemedyRegistry") -> None:
        """Initialize the diagnosis engine.

        Args:
            remedy_registry: Registry containing available remedies.
        """
        self.registry = remedy_registry

    def diagnose(self, context: "ErrorContext") -> list[Diagnosis]:
        """Analyze error and return possible diagnoses.

        Queries all registered remedies and collects their diagnoses.
        Results are sorted by confidence (highest first).

        If a remedy's ``diagnose()`` raises an exception, a zero-confidence
        sentinel Diagnosis is appended with ``remedy_name`` set to the failing
        remedy's class name and ``issue`` describing the failure. This ensures
        callers can detect partial diagnosis results without a return-type change.

        Args:
            context: Error context with diagnostic information.

        Returns:
            List of Diagnosis objects, sorted by confidence descending.
            Empty list if no remedies apply.
        """
        diagnoses: list[Diagnosis] = []

        for remedy in self.registry.all_remedies():
            try:
                diagnosis = remedy.diagnose(context)
                if diagnosis is not None:
                    diagnoses.append(diagnosis)
            except Exception as e:  # noqa: BLE001 — structured logger reports remedy_diagnosis_failed
                remedy_name = type(remedy).__name__
                # Individual remedy failures shouldn't block diagnosis
                _logger.warning(
                    "remedy_diagnosis_failed",
                    remedy=remedy_name,
                    error=str(e),
                )
                # Record the failure so callers know diagnosis is partial
                diagnoses.append(Diagnosis(
                    error_code=context.error_code,
                    issue=f"Remedy {remedy_name} failed during diagnosis: {e}",
                    explanation="This remedy could not be evaluated due to an internal error.",
                    suggestion="Check logs for details; the remedy may need a bug fix.",
                    confidence=0.0,
                    remedy_name=None,
                    context={"failed_remedy": remedy_name, "error": str(e)},
                ))

        # Sort by confidence, highest first
        diagnoses.sort(key=lambda d: d.confidence, reverse=True)
        return diagnoses

    def get_primary_diagnosis(self, context: "ErrorContext") -> Diagnosis | None:
        """Get the highest-confidence diagnosis.

        Convenience method when you only care about the best match.

        Args:
            context: Error context.

        Returns:
            Highest-confidence Diagnosis, or None if no remedies apply.
        """
        diagnoses = self.diagnose(context)
        return diagnoses[0] if diagnoses else None

    def get_automatic_diagnoses(self, context: "ErrorContext") -> list[Diagnosis]:
        """Get diagnoses for automatic remedies only.

        Filters to only diagnoses that can be auto-applied without
        user confirmation.

        Args:
            context: Error context.

        Returns:
            List of diagnoses from AUTOMATIC remedies.
        """
        from marianne.healing.remedies.base import RemedyCategory

        diagnoses = self.diagnose(context)
        return [
            d
            for d in diagnoses
            if d.remedy_name and not d.requires_confirmation
            and self._get_remedy_category(d.remedy_name) == RemedyCategory.AUTOMATIC
        ]

    def _get_remedy_category(self, remedy_name: str) -> "RemedyCategory | None":
        """Get the category of a remedy by name."""

        remedy = self.registry.get_by_name(remedy_name)
        return remedy.category if remedy else None
