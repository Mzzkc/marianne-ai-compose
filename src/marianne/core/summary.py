"""Job execution summary and completion types.

Contains types used across CLI, daemon, and execution layers
to represent job completion state. These types are the public contract between
the execution engine (baton/runner) and the rest of the system.

Canonical definitions:
- JobCompletionSummary: marianne.core.models
- FatalError, RateLimitExhaustedError, GracefulShutdownError: marianne.core.errors.exceptions
- GroundingDecisionContext, SheetExecutionMode: defined here
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Re-export canonical types for backward compatibility
from marianne.core.errors.exceptions import (  # noqa: F401
    FatalError,
    GracefulShutdownError,
    RateLimitExhaustedError,
)
from marianne.core.models import JobCompletionSummary  # noqa: F401

# RunSummary is an alias for the Pydantic v2 JobCompletionSummary model.
# All existing code using RunSummary continues to work — constructors,
# field access, and property access are backward compatible.
RunSummary = JobCompletionSummary


@dataclass
class GroundingDecisionContext:
    """Context from grounding hooks for completion mode decisions.

    Encapsulates grounding results to inform decision-making about
    whether to retry, complete, or escalate.
    """

    passed: bool
    message: str
    confidence: float = 1.0
    should_escalate: bool = False
    recovery_guidance: str | None = None
    hooks_executed: int = 0

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))

    @classmethod
    def disabled(cls) -> GroundingDecisionContext:
        """Create context when grounding is disabled."""
        return cls(passed=True, message="Grounding not enabled", hooks_executed=0)


class SheetExecutionMode(str, Enum):
    """Mode of sheet execution."""

    NORMAL = "normal"
    COMPLETION = "completion"
    RETRY = "retry"
    ESCALATE = "escalate"
