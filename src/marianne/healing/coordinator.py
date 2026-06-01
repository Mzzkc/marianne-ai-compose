"""Self-healing coordinator.

Orchestrates the healing process:
1. Collect error context
2. Run diagnosis pipeline
3. Apply automatic remedies
4. Prompt for suggested remedies
5. Generate healing report
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marianne.core.logging import get_logger
from marianne.healing.diagnosis import Diagnosis, DiagnosisEngine
from marianne.healing.registry import RemedyRegistry
from marianne.healing.remedies.base import Remedy, RemedyCategory, RemedyResult

_logger = get_logger("healing.coordinator")

if TYPE_CHECKING:
    from marianne.healing.context import ErrorContext
    from marianne.healing.remedies.base import Remedy


@dataclass
class HealingReport:
    """Report of a self-healing attempt.

    Captures what was diagnosed, what actions were taken,
    and what issues remain for manual intervention.
    """

    error_context: "ErrorContext"
    """The original error context that triggered healing."""

    diagnoses: list[Diagnosis] = field(default_factory=list)
    """All diagnoses found by the engine."""

    actions_taken: list[tuple[str, RemedyResult]] = field(default_factory=list)
    """(remedy_name, result) for each action attempted."""

    actions_skipped: list[tuple[str, str]] = field(default_factory=list)
    """(remedy_name, reason) for each skipped action."""

    diagnostic_outputs: list[tuple[str, str]] = field(default_factory=list)
    """(remedy_name, diagnostic_text) for guidance-only remedies."""

    @property
    def any_remedies_applied(self) -> bool:
        """Check if any remedies were successfully applied."""
        return any(result.success for _, result in self.actions_taken)

    @property
    def issues_remaining(self) -> int:
        """Count of issues that couldn't be auto-fixed."""
        return len(self.diagnostic_outputs) + len(
            [r for _, r in self.actions_taken if not r.success]
        )

    @property
    def should_retry(self) -> bool:
        """Whether a retry should be attempted after healing.

        Returns True if any automatic remedies succeeded.
        """
        return self.any_remedies_applied

    def format(self, verbose: bool = False) -> str:
        """Generate human-readable report.

        Args:
            verbose: Include full diagnostic output.

        Returns:
            Formatted healing report string.
        """
        lines = [
            "═" * 75,
            f"SELF-HEALING REPORT: Sheet {self.error_context.sheet_number}",
            "═" * 75,
            "",
            "Error Diagnosed:",
            f"  Code: {self.error_context.error_code}",
            f"  Message: {self.error_context.error_message}",
            "",
        ]

        # Remedies applied
        lines.append("Remedies Applied:")
        if self.actions_taken:
            for name, result in self.actions_taken:
                status = "✓" if result.success else "✗"
                category = "[AUTO]" if not self._is_suggested(name) else "[SUGGESTED]"
                lines.append(f"  {status} {category} {result.action_taken}: {result.message}")
        else:
            lines.append("  (none)")
        lines.append("")

        # Remedies skipped
        if self.actions_skipped:
            lines.append("Remedies Skipped:")
            for name, reason in self.actions_skipped:
                lines.append(f"  - {name}: {reason}")
            lines.append("")

        # Diagnostic outputs
        if self.diagnostic_outputs:
            lines.append("Remaining Issues (Manual Fix Required):")
            for name, output in self.diagnostic_outputs:
                lines.append(f"  [{name}]")
                if verbose:
                    for line in output.split("\n"):
                        lines.append(f"    {line}")
                else:
                    # Just show first line
                    first_line = output.split("\n")[0]
                    lines.append(f"    {first_line}")
            lines.append("")

        # Result message
        if self.should_retry:
            result_msg = "HEALED - Retrying sheet"
        elif self.issues_remaining == 0 and not self.any_remedies_applied:
            result_msg = "NO ACTION NEEDED"
        else:
            result_msg = f"INCOMPLETE - {self.issues_remaining} issue(s) remaining"

        lines.extend([
            f"Result: {result_msg}",
            "═" * 75,
        ])

        return "\n".join(lines)

    def _is_suggested(self, remedy_name: str) -> bool:
        """Check if a remedy required user confirmation."""
        # This is a heuristic based on naming
        return "suggest" in remedy_name.lower()


class SelfHealingCoordinator:
    """Orchestrates the self-healing process.

    Takes an error context and coordinates:
    1. Finding applicable remedies
    2. Applying automatic remedies
    3. Prompting for suggested remedies
    4. Collecting diagnostic output

    Example:
        registry = create_default_registry()
        coordinator = SelfHealingCoordinator(registry)

        context = ErrorContext.from_execution_result(...)
        report = await coordinator.heal(context)

        if report.should_retry:
            # Retry the sheet
    """

    def __init__(
        self,
        registry: RemedyRegistry,
        auto_confirm: bool = False,
        dry_run: bool = False,
        disabled_remedies: set[str] | None = None,
        max_healing_attempts: int = 2,
    ) -> None:
        """Initialize the coordinator.

        Args:
            registry: Registry of available remedies.
            auto_confirm: Auto-approve suggested remedies (--yes flag).
            dry_run: Show what would be done without changes.
            disabled_remedies: Set of remedy names to skip.
            max_healing_attempts: Maximum healing cycles before giving up.
        """
        self.registry = registry
        self.auto_confirm = auto_confirm
        self.dry_run = dry_run
        self.disabled_remedies = disabled_remedies or set()
        self.max_healing_attempts = max_healing_attempts
        self._healing_attempt = 0
        self._diagnosis_engine = DiagnosisEngine(registry)

    def _safe_apply(self, remedy: "Remedy", context: "ErrorContext") -> RemedyResult:
        """Apply a remedy, converting any exception into a failed result.

        #201: ``heal()`` runs on the conductor's critical path now (the baton
        invokes it on sheet exhaustion). An unguarded ``remedy.apply()`` that
        raised would crash the whole heal and mask the original failure. A
        crashing remedy is recorded as a failed action so the report stays
        truthful and ``should_retry`` is unaffected by it.
        """
        try:
            return remedy.apply(context)
        except Exception as exc:
            _logger.warning(
                "healing.remedy_apply_crashed",
                remedy=remedy.name,
                error=str(exc),
            )
            return RemedyResult(
                success=False,
                message=f"Remedy raised during apply: {exc}",
                action_taken="apply_crashed",
            )

    async def heal(self, context: "ErrorContext") -> HealingReport:
        """Run the self-healing process.

        1. Find applicable remedies
        2. Apply automatic remedies
        3. Prompt for suggested remedies (unless auto_confirm)
        4. Display diagnostic output for manual-fix issues
        5. Return report of what was done

        Args:
            context: Error context with diagnostic information.

        Returns:
            HealingReport with actions taken and results.
        """
        self._healing_attempt += 1

        # Check max healing attempts
        if self._healing_attempt > self.max_healing_attempts:
            return HealingReport(
                error_context=context,
                diagnoses=[],
                actions_taken=[],
                actions_skipped=[("all", "Max healing attempts exceeded")],
                diagnostic_outputs=[],
            )

        report = HealingReport(
            error_context=context,
            diagnoses=[],
            actions_taken=[],
            actions_skipped=[],
            diagnostic_outputs=[],
        )

        # Find applicable remedies
        applicable = self.registry.find_applicable(context)

        # Surface any remedy diagnosis crashes so the report doesn't mislead
        for remedy_name, error_msg in getattr(self.registry, "diagnosis_errors", []):
            report.actions_skipped.append(
                (remedy_name, f"Diagnosis crashed: {error_msg}")
            )

        for remedy, diagnosis in applicable:
            report.diagnoses.append(diagnosis)

            # Skip disabled remedies
            if remedy.name in self.disabled_remedies:
                report.actions_skipped.append(
                    (remedy.name, "Disabled in configuration")
                )
                continue

            if remedy.category == RemedyCategory.AUTOMATIC:
                # Apply automatic remedies without prompting
                if self.dry_run:
                    preview = remedy.preview(context)
                    report.actions_skipped.append(
                        (remedy.name, f"Dry run: would {preview}")
                    )
                else:
                    # remedy.apply() is synchronous — safe for fast I/O ops
                    result = self._safe_apply(remedy, context)
                    if not result.success:
                        _logger.warning(
                            "healing.remedy_failed",
                            remedy=remedy.name,
                            message=result.message,
                        )
                    report.actions_taken.append((remedy.name, result))

            elif remedy.category == RemedyCategory.SUGGESTED:
                # Prompt for suggested remedies (or auto-confirm)
                if self.auto_confirm or self._prompt_user(remedy, diagnosis):
                    if self.dry_run:
                        preview = remedy.preview(context)
                        report.actions_skipped.append(
                            (remedy.name, f"Dry run: would {preview}")
                        )
                    else:
                        result = self._safe_apply(remedy, context)
                        if not result.success:
                            _logger.warning(
                                "healing.remedy_failed",
                                remedy=remedy.name,
                                message=result.message,
                            )
                        report.actions_taken.append((remedy.name, result))
                else:
                    report.actions_skipped.append(
                        (remedy.name, "User declined")
                    )
                    # Add diagnostic for declined suggestion
                    diagnostic = remedy.generate_diagnostic(context)
                    report.diagnostic_outputs.append((remedy.name, diagnostic))

            elif remedy.category == RemedyCategory.DIAGNOSTIC:
                # Diagnostic only - generate guidance
                diagnostic = remedy.generate_diagnostic(context)
                report.diagnostic_outputs.append((remedy.name, diagnostic))

        return report

    def _prompt_user(self, remedy: "Remedy", diagnosis: Diagnosis) -> bool:
        """Prompt user for confirmation on suggested remedy.

        Args:
            remedy: The remedy to apply.
            diagnosis: The diagnosis for context.

        Returns:
            True if user confirms, False otherwise.
        """
        try:
            from rich.console import Console
            from rich.prompt import Confirm

            console = Console()
            console.print(f"\n[yellow]Suggested fix:[/yellow] {diagnosis.suggestion}")
            console.print(f"[dim]Risk level: {remedy.risk_level.value}[/dim]")
            console.print(f"[dim]Confidence: {diagnosis.confidence:.0%}[/dim]")
            return Confirm.ask("Apply this fix?", default=False)
        except ImportError:
            return False
        except KeyboardInterrupt:
            raise  # Propagate abort — don't swallow as "decline"

    def reset(self) -> None:
        """Reset healing attempt counter.

        Call this before starting a new healing cycle for a different error.
        """
        self._healing_attempt = 0


