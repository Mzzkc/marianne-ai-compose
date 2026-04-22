"""Escalation protocol for low-confidence sheet execution decisions.

Provides a mechanism for escalating to external decision-makers (human or AI)
when sheet confidence is too low to proceed automatically.

Phase 2 of AGI Evolution: Confidence-Based Execution

v21 Evolution: Proactive Checkpoint System - adds pre-execution checkpoints
that ask for confirmation BEFORE dangerous operations, complementing the
reactive escalation system.

TODO (Pre-Instrument Execution Atlas, Phase 6 — DEFERRED):
    This module has zero production callers. The atlas classified it as
    dead code, but deletion is deferred pending the fermata escalation
    feature described in ``.marianne/spec/intent.yaml``. Once the baton
    implements fermata via its own event types (see
    ``daemon/baton/events.py``), this module should be deleted. Until
    then it is retained so that the ``CheckpointTrigger`` /
    ``EscalationDecision`` vocabulary is discoverable by future
    contributors. Do NOT add new imports of this module — the baton
    event types are the forward path. See phase-6-report.md for the
    full rationale.
"""

from dataclasses import dataclass, field
from typing import Literal

from marianne.core.checkpoint import SheetState, ValidationDetailDict
from marianne.execution.validation import SheetValidationResult

# =============================================================================
# v21 Evolution: Proactive Checkpoint System
# =============================================================================


@dataclass
class CheckpointTrigger:
    """Configuration for a proactive checkpoint trigger.

    Defines conditions that should trigger a pre-execution checkpoint,
    asking for confirmation BEFORE the sheet executes.

    v21 Evolution: Proactive Checkpoint System - enables pre-action validation.
    """

    name: str
    """Name/identifier for this trigger."""

    sheet_nums: list[int] | None = None
    """Specific sheet numbers to checkpoint (None = all sheets)."""

    prompt_contains: list[str] | None = None
    """Keywords in prompt that trigger checkpoint (case-insensitive)."""

    min_retry_count: int | None = None
    """Trigger if retry count >= this value."""

    requires_confirmation: bool = True
    """Whether to require explicit confirmation (True) or just warn (False)."""

    message: str = ""
    """Custom message to show when checkpoint triggers."""


@dataclass
class CheckpointContext:
    """Context provided to checkpoint handlers for decision-making.

    Contains all relevant information about the sheet that's about to execute,
    enabling informed pre-execution decisions.

    v21 Evolution: Proactive Checkpoint System.
    """

    job_id: str
    """Unique identifier for the job."""

    sheet_num: int
    """Sheet number about to execute."""

    prompt: str
    """The prompt that will be used for sheet execution."""

    trigger: CheckpointTrigger
    """The trigger that caused this checkpoint."""

    retry_count: int = 0
    """Number of retry attempts already made for this sheet."""

    previous_errors: list[str] = field(default_factory=list)
    """Errors from previous attempts (if any)."""


@dataclass
class CheckpointResponse:
    """Response from a checkpoint handler specifying how to proceed.

    v21 Evolution: Proactive Checkpoint System.
    """

    action: Literal["proceed", "abort", "skip", "modify_prompt"]
    """Action to take:
    - proceed: Continue with execution
    - abort: Stop the entire job
    - skip: Skip this sheet and continue to the next
    - modify_prompt: Proceed with modified prompt
    """

    modified_prompt: str | None = None
    """Modified prompt to use if action is 'modify_prompt'."""

    guidance: str | None = None
    """Optional guidance or reasoning for the decision."""



# =============================================================================
# Reactive Escalation (existing system)
# =============================================================================


@dataclass
class HistoricalSuggestion:
    """A suggestion from historical escalation decisions.

    Represents a past escalation decision that was similar to the current
    situation, providing guidance based on what worked (or didn't) before.
    """

    action: str
    """Action taken in the past (retry, skip, abort, modify_prompt)."""

    outcome: str | None
    """Outcome after the action (success, failed, skipped, aborted, unknown)."""

    confidence: float
    """Confidence level at which this past escalation occurred."""

    validation_pass_rate: float
    """Validation pass rate at which this past escalation occurred."""

    guidance: str | None
    """Any guidance or notes recorded with this past decision."""


@dataclass
class EscalationContext:
    """Context provided to escalation handlers for decision-making.

    Contains all relevant information about the sheet execution state
    that led to escalation.

    v15 Evolution: Added historical_suggestions field to surface similar
    past escalation decisions that may inform the current decision.
    """

    job_id: str
    """Unique identifier for the job."""

    sheet_num: int
    """Sheet number that triggered escalation."""

    validation_results: list[ValidationDetailDict]
    """Serialized validation results from the sheet."""

    confidence: float
    """Aggregate confidence score that triggered escalation (0.0-1.0)."""

    retry_count: int
    """Number of retry attempts already made."""

    error_history: list[str]
    """List of error messages from previous attempts."""

    prompt_used: str
    """The prompt that was used for the sheet execution."""

    output_summary: str
    """Summary of the sheet execution output."""

    historical_suggestions: list[HistoricalSuggestion] = field(default_factory=list)
    """Similar past escalation decisions that may inform this decision.

    v15 Evolution: Populated from get_similar_escalation() results.
    Ordered by relevance (successful outcomes first).
    """


@dataclass
class EscalationResponse:
    """Response from an escalation handler specifying how to proceed.

    Determines the next action for a sheet that triggered escalation.
    """

    action: Literal["retry", "skip", "abort", "modify_prompt"]
    """Action to take:
    - retry: Retry the sheet with the same or modified prompt
    - skip: Skip this sheet and continue to the next
    - abort: Stop the entire job
    - modify_prompt: Retry with a modified prompt (requires modified_prompt)
    """

    modified_prompt: str | None = None
    """Modified prompt to use if action is 'modify_prompt'."""

    guidance: str | None = None
    """Optional guidance or reasoning for the decision."""

    confidence_boost: float = 0.0
    """Amount to boost confidence threshold for next attempt (0.0-1.0).
    Allows temporary threshold adjustment based on escalation feedback.
    """


class ConsoleEscalationHandler:
    """Console-based escalation handler that prompts the user.

    Implements the EscalationHandler protocol for interactive
    human-in-the-loop decision making.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.6,
        auto_retry_on_first_failure: bool = True,
    ) -> None:
        """Initialize the console escalation handler.

        Args:
            confidence_threshold: Confidence below which to escalate (0.0-1.0).
            auto_retry_on_first_failure: If True, auto-retry on first failure
                without escalating (reduces noise for transient issues).
        """
        self.confidence_threshold = confidence_threshold
        self.auto_retry_on_first_failure = auto_retry_on_first_failure

    async def should_escalate(
        self,
        sheet_state: SheetState,
        validation_result: SheetValidationResult,
        confidence: float,
    ) -> bool:
        """Determine if escalation is needed.

        Escalates when:
        - Confidence is below threshold
        - AND (not first attempt OR auto_retry_on_first_failure is False)

        Args:
            sheet_state: Current sheet state.
            validation_result: Validation results.
            confidence: Aggregate confidence score.

        Returns:
            True if user should be prompted for a decision.
        """
        # Don't escalate if confidence is acceptable
        if confidence >= self.confidence_threshold:
            return False

        # Escalate if auto-retry is disabled OR this is not the first attempt
        return not (self.auto_retry_on_first_failure and sheet_state.attempt_count <= 1)

    async def escalate(self, context: EscalationContext) -> EscalationResponse:
        """Prompt user for escalation decision via console.

        Args:
            context: Full escalation context.

        Returns:
            EscalationResponse based on user input.
        """
        self._print_context_summary(context)
        return await self._prompt_for_action(context)

    def _print_context_summary(self, context: EscalationContext) -> None:
        """Print a summary of the escalation context to console."""
        separator = "=" * 60
        print(f"\n{separator}")
        print("ESCALATION REQUIRED - Low Confidence Sheet Execution")
        print(separator)
        print(f"Job ID:       {context.job_id}")
        print(f"Sheet:        {context.sheet_num}")
        print(f"Confidence:   {context.confidence:.1%}")
        print(f"Retry Count:  {context.retry_count}")
        print("-" * 60)

        # Validation summary
        passed = sum(1 for v in context.validation_results if v.get("passed", False))
        failed = len(context.validation_results) - passed
        print(f"Validations:  {passed} passed, {failed} failed")

        if failed > 0:
            print("\nFailed validations:")
            for v in context.validation_results:
                if not v.get("passed", False):
                    desc = v.get("description") or v.get("path") or "unnamed"
                    error = v.get("error_message", "no details")
                    print(f"  - {desc}: {error}")

        # Error history
        if context.error_history:
            print("\nRecent errors:")
            for i, error in enumerate(context.error_history[-3:], 1):
                # Truncate long error messages
                error_short = error[:100] + "..." if len(error) > 100 else error
                print(f"  {i}. {error_short}")

        # Output summary (truncated)
        if context.output_summary:
            summary = context.output_summary[:200]
            if len(context.output_summary) > 200:
                summary += "..."
            print(f"\nOutput summary: {summary}")

        # v15 Evolution: Display historical suggestions
        if context.historical_suggestions:
            print("\n" + "-" * 60)
            print("HISTORICAL SUGGESTIONS (similar past decisions):")
            for i, suggestion in enumerate(context.historical_suggestions[:3], 1):
                outcome_str = suggestion.outcome or "unknown"
                if outcome_str == "success":
                    outcome_icon = "✓"
                elif outcome_str == "failed":
                    outcome_icon = "✗"
                else:
                    outcome_icon = "?"
                print(f"  {i}. {suggestion.action.upper()} → {outcome_icon} {outcome_str}")
                conf_pct = f"{suggestion.confidence:.1%}"
                rate_pct = f"{suggestion.validation_pass_rate:.0f}%"
                print(f"     (conf={conf_pct}, pass_rate={rate_pct})")
                if suggestion.guidance:
                    guidance_short = suggestion.guidance[:80]
                    if len(suggestion.guidance) > 80:
                        guidance_short += "..."
                    print(f"     Notes: {guidance_short}")

        print(separator)

    async def _prompt_for_action(
        self, context: EscalationContext
    ) -> EscalationResponse:
        """Prompt user for action and return response.

        Args:
            context: Escalation context (for modify_prompt option).

        Returns:
            EscalationResponse based on user choice.
        """
        print("\nActions:")
        print("  [r] Retry - Try the sheet again with same prompt")
        print("  [s] Skip  - Skip this sheet and continue")
        print("  [a] Abort - Stop the entire job")
        print("  [m] Modify - Retry with modified prompt")
        print()

        while True:
            try:
                choice = input("Choose action [r/s/a/m]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                # Handle non-interactive or interrupted input
                print("\nNo input available, defaulting to abort.")
                return EscalationResponse(
                    action="abort",
                    guidance="Non-interactive environment or interrupted",
                )

            if choice == "r":
                guidance = self._get_optional_guidance()
                return EscalationResponse(
                    action="retry",
                    guidance=guidance,
                )

            elif choice == "s":
                guidance = self._get_optional_guidance()
                return EscalationResponse(
                    action="skip",
                    guidance=guidance,
                )

            elif choice == "a":
                return EscalationResponse(
                    action="abort",
                    guidance="User aborted via console",
                )

            elif choice == "m":
                modified_prompt = self._get_modified_prompt(context.prompt_used)
                if modified_prompt is None:
                    print("Modification cancelled, please choose again.")
                    continue
                guidance = self._get_optional_guidance()
                return EscalationResponse(
                    action="modify_prompt",
                    modified_prompt=modified_prompt,
                    guidance=guidance,
                )

            else:
                print(f"Invalid choice: '{choice}'. Please enter r, s, a, or m.")

    def _get_optional_guidance(self) -> str | None:
        """Prompt for optional guidance/notes."""
        try:
            guidance = input("Add notes (optional, press Enter to skip): ").strip()
            return guidance if guidance else None
        except (EOFError, KeyboardInterrupt):
            return None

    def _get_modified_prompt(self, original_prompt: str) -> str | None:
        """Get modified prompt from user.

        Args:
            original_prompt: The original prompt to modify.

        Returns:
            Modified prompt string, or None if cancelled.
        """
        print("\nOriginal prompt (first 500 chars):")
        print("-" * 40)
        print(original_prompt[:500])
        if len(original_prompt) > 500:
            print("...")
        print("-" * 40)

        print("\nEnter modification instructions (or 'cancel' to go back):")
        print("You can add a prefix, suffix, or replacement instruction.")

        try:
            instruction = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if instruction.lower() == "cancel":
            return None

        # Simple modification: treat as suffix by default
        if instruction.startswith("PREFIX:"):
            return instruction[7:].strip() + "\n\n" + original_prompt
        elif instruction.startswith("REPLACE:"):
            return instruction[8:].strip()
        else:
            # Default: append as additional instruction
            return original_prompt + "\n\n---\nAdditional guidance: " + instruction


# =============================================================================
# v21 Evolution: Console Checkpoint Handler
# =============================================================================


class ConsoleCheckpointHandler:
    """Console-based checkpoint handler that prompts the user before execution.

    Implements the CheckpointHandler protocol for interactive
    human-in-the-loop decision making BEFORE sheet execution.

    v21 Evolution: Proactive Checkpoint System.
    """

    async def should_checkpoint(
        self,
        sheet_num: int,
        prompt: str,
        retry_count: int,
        triggers: list[CheckpointTrigger],
    ) -> CheckpointTrigger | None:
        """Determine if a checkpoint is needed before sheet execution.

        Checks all configured triggers against the current sheet context.

        Args:
            sheet_num: Sheet number about to execute.
            prompt: The prompt to be used.
            retry_count: Number of retry attempts already made.
            triggers: List of configured checkpoint triggers.

        Returns:
            The first matching CheckpointTrigger, or None if no checkpoint needed.
        """
        prompt_lower = prompt.lower()

        for trigger in triggers:
            # Check sheet number match
            if trigger.sheet_nums is not None and sheet_num not in trigger.sheet_nums:
                continue

            # Check prompt keyword match
            if trigger.prompt_contains is not None:
                keyword_match = any(
                    kw.lower() in prompt_lower
                    for kw in trigger.prompt_contains
                )
                if not keyword_match:
                    continue

            # Check retry count threshold
            if trigger.min_retry_count is not None and retry_count < trigger.min_retry_count:
                continue

            # All conditions passed - trigger matched
            return trigger

        return None

    async def checkpoint(self, context: CheckpointContext) -> CheckpointResponse:
        """Handle checkpoint and prompt user for decision.

        Args:
            context: Full context about the checkpoint trigger.

        Returns:
            CheckpointResponse with the action to take.
        """
        self._print_checkpoint_prompt(context)

        if not context.trigger.requires_confirmation:
            # Warning only - proceed automatically
            return CheckpointResponse(
                action="proceed",
                guidance="Auto-proceed (warning-only checkpoint)",
            )

        return await self._prompt_for_checkpoint_action(context)

    def _print_checkpoint_prompt(self, context: CheckpointContext) -> None:
        """Print checkpoint prompt to console."""
        separator = "=" * 60
        print(f"\n{separator}")
        print("CHECKPOINT - Pre-Execution Approval Required")
        print(separator)
        print(f"Job ID:       {context.job_id}")
        print(f"Sheet:        {context.sheet_num}")
        print(f"Retry Count:  {context.retry_count}")
        print(f"Trigger:      {context.trigger.name}")

        if context.trigger.message:
            print("-" * 60)
            print(f"MESSAGE: {context.trigger.message}")

        # Show prompt preview (truncated)
        print("-" * 60)
        prompt_preview = context.prompt[:300]
        if len(context.prompt) > 300:
            prompt_preview += "..."
        print(f"Prompt preview:\n{prompt_preview}")

        # Show previous errors if any
        if context.previous_errors:
            print("-" * 60)
            print("Previous errors:")
            for i, error in enumerate(context.previous_errors[-3:], 1):
                error_short = error[:100] + "..." if len(error) > 100 else error
                print(f"  {i}. {error_short}")

        print(separator)

    async def _prompt_for_checkpoint_action(
        self,
        context: CheckpointContext,
    ) -> CheckpointResponse:
        """Prompt user for checkpoint action.

        Args:
            context: Checkpoint context.

        Returns:
            CheckpointResponse based on user choice.
        """
        print("\nActions:")
        print("  [p] Proceed - Continue with sheet execution")
        print("  [s] Skip    - Skip this sheet and continue")
        print("  [a] Abort   - Stop the entire job")
        print("  [m] Modify  - Proceed with modified prompt")
        print()

        while True:
            try:
                choice = input("Choose action [p/s/a/m]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nNo input available, defaulting to abort.")
                return CheckpointResponse(
                    action="abort",
                    guidance="Non-interactive environment or interrupted",
                )

            if choice == "p":
                guidance = self._get_optional_guidance()
                return CheckpointResponse(
                    action="proceed",
                    guidance=guidance,
                )

            elif choice == "s":
                guidance = self._get_optional_guidance()
                return CheckpointResponse(
                    action="skip",
                    guidance=guidance,
                )

            elif choice == "a":
                return CheckpointResponse(
                    action="abort",
                    guidance="User aborted via checkpoint",
                )

            elif choice == "m":
                modified_prompt = self._get_modified_prompt(context.prompt)
                if modified_prompt is None:
                    print("Modification cancelled, please choose again.")
                    continue
                guidance = self._get_optional_guidance()
                return CheckpointResponse(
                    action="modify_prompt",
                    modified_prompt=modified_prompt,
                    guidance=guidance,
                )

            else:
                print(f"Invalid choice: '{choice}'. Please enter p, s, a, or m.")

    def _get_optional_guidance(self) -> str | None:
        """Prompt for optional guidance/notes."""
        try:
            guidance = input("Add notes (optional, press Enter to skip): ").strip()
            return guidance if guidance else None
        except (EOFError, KeyboardInterrupt):
            return None

    def _get_modified_prompt(self, original_prompt: str) -> str | None:
        """Get modified prompt from user."""
        print("\nOriginal prompt (first 500 chars):")
        print("-" * 40)
        print(original_prompt[:500])
        if len(original_prompt) > 500:
            print("...")
        print("-" * 40)

        print("\nEnter modification instructions (or 'cancel' to go back):")
        print("You can add a prefix, suffix, or replacement instruction.")

        try:
            instruction = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if instruction.lower() == "cancel":
            return None

        if instruction.startswith("PREFIX:"):
            return instruction[7:].strip() + "\n\n" + original_prompt
        elif instruction.startswith("REPLACE:"):
            return instruction[8:].strip()
        else:
            return original_prompt + "\n\n---\nAdditional guidance: " + instruction
