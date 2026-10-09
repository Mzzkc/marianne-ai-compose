"""Base classes and protocols for remedies.

Defines the Remedy protocol and supporting types that all
concrete remedy implementations must follow.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from marianne.core.logging import get_logger

logger = get_logger("healing.remedies")
if TYPE_CHECKING:
    from marianne.healing.context import ErrorContext
    from marianne.healing.diagnosis import Diagnosis


class RemedyCategory(str, Enum):
    """Determines how a remedy is applied.

    AUTOMATIC: Applied without asking (safe, reversible operations)
    SUGGESTED: Requires user confirmation (modifies files)
    DIAGNOSTIC: Cannot auto-fix, provides guidance only
    """

    AUTOMATIC = "automatic"
    SUGGESTED = "suggested"
    DIAGNOSTIC = "diagnostic"


class RiskLevel(str, Enum):
    """Risk level of applying the remedy.

    Used to inform users about the potential impact of the fix.
    """

    LOW = "low"  # Safe, reversible, no data loss
    MEDIUM = "medium"  # May have side effects, backup recommended
    HIGH = "high"  # Significant changes, careful review needed


@dataclass
class RemedyResult:
    """Result of applying a remedy.

    Tracks what was done and how to undo it if needed.
    """

    success: bool
    """Whether the remedy was applied successfully."""

    message: str
    """Human-readable description of what happened."""

    action_taken: str
    """Brief description of the action taken."""

    rollback_command: str | None = None
    """Shell command to undo the remedy (if possible)."""

    created_paths: list[Path] = field(default_factory=list)
    """Paths that were created by the remedy."""

    modified_files: list[Path] = field(default_factory=list)
    """Files that were modified by the remedy."""

    backup_paths: list[Path] = field(default_factory=list)
    """Backup files created before modification."""

    def __str__(self) -> str:
        """Human-readable string representation."""
        status = "✓" if self.success else "✗"
        return f"[{status}] {self.action_taken}: {self.message}"


class Remedy(Protocol):
    """Protocol for remediation actions.

    Remedies diagnose specific error patterns and can optionally
    apply fixes. Each remedy must implement:
    - name: Unique identifier
    - category: AUTOMATIC, SUGGESTED, or DIAGNOSTIC
    - risk_level: LOW, MEDIUM, or HIGH
    - description: Human-readable explanation
    - diagnose(): Check if this remedy applies
    - preview(): Show what would change
    - apply(): Make the actual changes
    - rollback(): Undo changes if needed
    - generate_diagnostic(): Provide guidance for manual fix
    """

    @property
    def name(self) -> str:
        """Unique identifier for this remedy."""
        ...

    @property
    def category(self) -> RemedyCategory:
        """How this remedy should be applied."""
        ...

    @property
    def risk_level(self) -> RiskLevel:
        """Risk level of applying this remedy."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of what this remedy does."""
        ...

    def diagnose(self, context: "ErrorContext") -> "Diagnosis | None":
        """Check if this remedy applies to the error.

        Returns Diagnosis if applicable, None otherwise.
        The diagnosis includes confidence score and fix suggestion.

        Args:
            context: Error context with diagnostic information.

        Returns:
            Diagnosis if this remedy can help, None otherwise.
        """
        ...

    def preview(self, context: "ErrorContext") -> str:
        """Show what would be changed without making changes.

        Args:
            context: Error context.

        Returns:
            Human-readable description of planned changes.
        """
        ...

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """Apply the remedy.

        Only called if diagnose() returned a Diagnosis and:
        - category == AUTOMATIC, or
        - category == SUGGESTED and user confirmed

        Args:
            context: Error context.

        Returns:
            Result with success status and details.
        """
        ...

    def rollback(self, result: RemedyResult) -> bool:
        """Undo the remedy if possible.

        Called if remedy was applied but subsequent validation failed.

        Args:
            result: The result from apply().

        Returns:
            True if rollback succeeded, False otherwise.
        """
        ...

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        """Generate detailed diagnostic message.

        Called for DIAGNOSTIC category, or when user declines SUGGESTED.

        Args:
            context: Error context.

        Returns:
            Formatted guidance for manual fix.
        """
        ...


class BaseRemedy:
    """Base class providing common remedy functionality.

    Concrete remedies can inherit from this to get default
    implementations of common methods.
    """

    @property
    def name(self) -> str:
        """Override in subclass."""
        raise NotImplementedError

    @property
    def category(self) -> RemedyCategory:
        """Override in subclass."""
        raise NotImplementedError

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def description(self) -> str:
        """Override in subclass."""
        raise NotImplementedError

    def diagnose(self, context: "ErrorContext") -> "Diagnosis | None":
        """Override in subclass."""
        raise NotImplementedError

    def preview(self, context: "ErrorContext") -> str:
        """Default preview implementation."""
        return f"Would apply remedy: {self.name}"

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """Override in subclass."""
        raise NotImplementedError

    def rollback(self, result: RemedyResult) -> bool:
        """Default rollback - remove created paths."""
        if not result.created_paths:
            return False

        all_removed = True
        for path in reversed(result.created_paths):
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    # Only remove if empty
                    if not any(path.iterdir()):
                        path.rmdir()
                    else:
                        all_removed = False
            except OSError as exc:
                logger.warning("healing.rollback_failed", path=str(path), error=str(exc))
                all_removed = False

        return all_removed

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        """Default diagnostic message."""
        return (
            f"Remedy '{self.name}' cannot be automatically applied.\n"
            f"Manual intervention required."
        )
