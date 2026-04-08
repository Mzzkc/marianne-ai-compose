"""Cost tracking mixin for JobRunner.

Contains methods for token counting, cost estimation, and cost limit
enforcement. This module centralizes all cost-related concerns to enable
the Cost Circuit Breaker pattern (v4 evolution).

Architecture:
    CostMixin is mixed with other mixins to compose the full JobRunner.
    It expects the following attributes from base.py:

    Required attributes:
        - config: JobConfig (provides cost_limits configuration)
        - console: Console (for cost warnings)
        - _logger: MarianneLogger
        - _circuit_breaker: CircuitBreaker | None
        - _summary: RunSummary | None

    Provides methods:
        - _track_cost(): Record token usage and calculate costs
        - _check_cost_limits(): Enforce per-sheet and per-job cost limits

Cost Tracking Flow:
    1. Backend execution completes with token counts (exact or estimated)
    2. _track_cost() calculates costs using configured rates
    3. Costs recorded to sheet_state, job state, circuit_breaker, and summary
    4. _check_cost_limits() enforces limits with warnings at thresholds

Token Sources (in order of preference):
    1. Exact: result.input_tokens + result.output_tokens (API backends)
    2. Legacy: result.tokens_used (deprecated total-only field)
    3. Estimated: Output character count / 4 (CLI backend fallback)

v4 Evolution: Cost Circuit Breaker
    The circuit breaker receives real-time cost updates for:
    - Per-window rate limiting
    - Cross-job cost coordination
    - Preemptive pause before hitting hard limits
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from marianne.execution.circuit_breaker import CircuitBreaker

from marianne.backends.base import ExecutionResult
from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.config import JobConfig
from marianne.core.logging import MarianneLogger

from .models import RunSummary


class CostMixin:
    """Mixin providing cost tracking methods for JobRunner.

    This mixin handles all aspects of token usage tracking and cost
    calculation, including:

    - Extracting token counts from execution results
    - Estimating tokens when exact counts unavailable
    - Calculating costs using configured per-token rates
    - Updating state objects with accumulated costs
    - Enforcing cost limits with warnings

    Cost Confidence Levels:
        1.0: Exact token counts from API (highest confidence)
        0.85: Total tokens only (input estimated from output)
        0.7: Character-based estimation (CLI backend fallback)

    The confidence level is tracked per-sheet to indicate data quality.

    Key Attributes (from base.py):
        config: Provides cost_limits with pricing and limits
        _circuit_breaker: Receives real-time cost updates
        _summary: Accumulates run-level totals
    """

    # Type hints for attributes provided by base.py
    config: JobConfig
    console: Console
    _logger: MarianneLogger
    _circuit_breaker: CircuitBreaker | None
    _summary: RunSummary | None

    if TYPE_CHECKING:
        # Methods from JobRunnerBase
        async def _fire_event(
            self, event: str, sheet_num: int, data: dict[str, Any] | None = None,
        ) -> None: ...

    # ─────────────────────────────────────────────────────────────────────
    # Token & Cost Tracking
    # ─────────────────────────────────────────────────────────────────────

    async def _track_cost(
        self,
        result: ExecutionResult,
        sheet_state: SheetState,
        state: CheckpointState,
    ) -> tuple[int, int, float, float]:
        """Track token usage and cost from an execution result.

        Records cost in:
        - sheet_state: Per-sheet token/cost tracking
        - state: Cumulative job totals
        - circuit_breaker: Real-time cost enforcement
        - summary: Final run statistics

        Args:
            result: Execution result from backend.
            sheet_state: Current sheet's state object.
            state: Job checkpoint state.

        Returns:
            Tuple of (input_tokens, output_tokens, estimated_cost, confidence).
            confidence ranges from 0.0 to 1.0:
              - 1.0: exact counts from API backend (input_tokens/output_tokens set)
              - 0.85: estimated from deprecated tokens_used field (total only)
              - 0.7: estimated from output character length (~4 chars/token heuristic)
        """
        config = self.config.cost_limits

        # Get token counts from result - prefer explicit fields, fall back to estimates
        input_tokens = 0
        output_tokens = 0
        confidence = 1.0

        if result.input_tokens is not None and result.output_tokens is not None:
            # Exact counts from API backend (v4 evolution: precise cost tracking)
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            confidence = 1.0  # Exact counts from API
        elif result.tokens_used is not None:
            # Legacy: only total tokens available (deprecated field)
            import warnings
            warnings.warn(
                "Backend produced deprecated 'tokens_used' field; "
                "use 'input_tokens'/'output_tokens' instead",
                DeprecationWarning,
                stacklevel=2,
            )
            output_tokens = result.tokens_used
            # Estimate input from output (rough heuristic: input ~= 2x output for prompts)
            input_tokens = output_tokens * 2
            confidence = 0.85  # Reasonable estimate but not exact
        else:
            # Estimate from output length (CLI backend, ~4 chars per token)
            output_chars = len(result.stdout or "") + len(result.stderr or "")
            output_tokens = max(output_chars // 4, 1)  # At least 1 token
            # No way to know input tokens from CLI - estimate from output
            input_tokens = output_tokens * 2  # Rough heuristic
            confidence = 0.7  # Lower confidence for estimates

        # Calculate estimated cost
        estimated_cost = (
            (input_tokens / 1000 * config.cost_per_1k_input_tokens)
            + (output_tokens / 1000 * config.cost_per_1k_output_tokens)
        )

        # Update sheet state
        sheet_state.input_tokens = (sheet_state.input_tokens or 0) + input_tokens
        sheet_state.output_tokens = (sheet_state.output_tokens or 0) + output_tokens
        sheet_state.estimated_cost = (sheet_state.estimated_cost or 0.0) + estimated_cost
        # Use latest confidence rather than min() — min() makes confidence
        # permanently pessimistic since early estimates drag it down forever
        sheet_state.cost_confidence = confidence

        # Update job state cumulative totals
        state.total_input_tokens += input_tokens
        state.total_output_tokens += output_tokens
        state.total_estimated_cost += estimated_cost

        # Update circuit breaker for real-time tracking
        if self._circuit_breaker is not None:
            await self._circuit_breaker.record_cost(input_tokens, output_tokens, estimated_cost)

        # Update summary
        if self._summary is not None:
            self._summary.total_input_tokens += input_tokens
            self._summary.total_output_tokens += output_tokens
            self._summary.total_estimated_cost += estimated_cost

        # Fire cost update event (Issue #49: Runner Callbacks)
        budget_remaining = None
        if self.config.cost_limits.max_cost_per_job is not None:
            budget_remaining = self.config.cost_limits.max_cost_per_job - state.total_estimated_cost
        await self._fire_event("job.cost_update", 0, {
            "total_cost": round(state.total_estimated_cost, 4),
            "budget_remaining": (
                round(budget_remaining, 4) if budget_remaining is not None else None
            ),
            "input_tokens": state.total_input_tokens,
            "output_tokens": state.total_output_tokens,
        })

        return input_tokens, output_tokens, estimated_cost, confidence

    # ─────────────────────────────────────────────────────────────────────
    # Cost Limit Enforcement
    # ─────────────────────────────────────────────────────────────────────

    def _check_cost_limits(
        self,
        sheet_state: SheetState,
        state: CheckpointState,
    ) -> tuple[bool, str | None]:
        """Check if any cost limits have been exceeded.

        Enforces both per-sheet and per-job cost limits. Also emits
        warnings when approaching the job limit threshold.

        Args:
            sheet_state: Current sheet's state with cost tracking.
            state: Job checkpoint state with cumulative costs.

        Returns:
            Tuple of (exceeded: bool, reason: str | None).
            If exceeded is True, reason contains the limit that was hit.
        """
        config = self.config.cost_limits
        if not config.enabled:
            return False, None

        # Check per-sheet limit
        if config.max_cost_per_sheet is not None:
            sheet_cost = sheet_state.estimated_cost or 0.0
            if sheet_cost > config.max_cost_per_sheet:
                return True, (
                    f"Sheet cost ${sheet_cost:.4f} exceeded limit "
                    f"${config.max_cost_per_sheet:.2f}"
                )

        # Check per-job limit
        if config.max_cost_per_job is not None:
            job_cost = state.total_estimated_cost
            if job_cost > config.max_cost_per_job:
                return True, (
                    f"Job cost ${job_cost:.4f} exceeded limit "
                    f"${config.max_cost_per_job:.2f}"
                )

            # Emit warning at threshold
            warn_threshold = config.max_cost_per_job * config.warn_at_percent / 100
            if job_cost > warn_threshold and not state.cost_limit_reached:
                self._logger.warning(
                    "cost.warning_threshold",
                    job_cost=round(job_cost, 4),
                    max_cost=config.max_cost_per_job,
                    warn_percent=config.warn_at_percent,
                )
                self.console.print(
                    f"[yellow]Cost warning: ${job_cost:.4f} of "
                    f"${config.max_cost_per_job:.2f} limit "
                    f"({job_cost/config.max_cost_per_job*100:.0f}%)[/yellow]"
                )

        return False, None
