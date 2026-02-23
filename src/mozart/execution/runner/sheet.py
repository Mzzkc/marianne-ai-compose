"""Sheet execution mixin for JobRunner.

Contains the core sheet execution logic including:
- _execute_sheet_with_recovery(): Main sheet execution state machine
- Validation integration methods
- Decision logic for retry/completion/escalation modes
- Grounding hooks execution

Note: Context building (_build_sheet_context, _populate_cross_sheet_context,
_capture_cross_sheet_files) has been extracted to ContextBuildingMixin in
context.py as part of incremental mixin decomposition.

Architecture:
    This mixin requires access to attributes and methods from:
    - JobRunnerBase: config, backend, state_backend, console, _logger, etc.
    - ContextBuildingMixin: _build_sheet_context()
    - PatternsMixin: _query_relevant_patterns(), _record_pattern_feedback()
    - RecoveryMixin: _try_self_healing(), _handle_rate_limit()
    - CostMixin: _track_cost(), _check_cost_limits()

    It provides:
    - _execute_sheet_with_recovery(): Core sheet execution state machine
    - _configure_and_execute_sheet(): Backend config + execution per sheet
    - _record_execution_bookkeeping(): Post-execution output/cost/history
    - _run_preflight_checks(): Pre-execution validation
    - _update_sheet_validation_state(): State update after validation
    - _run_grounding_hooks(): External grounding hook execution
    - _classify_execution(): Error classification
    - _get_retry_delay(): Retry delay calculation
    - _decide_next_action(): Local decision logic
    - _decide_with_judgment(): Judgment-integrated decision logic
    - _build_judgment_query(): Query construction for judgment
    - _map_judgment_to_mode(): Map judgment response to execution mode
    - _check_proactive_checkpoint(): Proactive checkpoint system
    - _handle_escalation(): Escalation handling
    - _update_escalation_outcome(): Escalation outcome recording
    - _record_sheet_outcome(): Outcome recording for learning
    - _poll_broadcast_discoveries(): Pattern broadcast polling
"""

from __future__ import annotations

import asyncio
import random
import re
import sqlite3
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mozart.core.constants import TRUNCATE_STDOUT_TAIL_CHARS
from mozart.execution.runner.patterns import PatternFeedbackContext

if TYPE_CHECKING:
    from rich.console import Console

    from mozart.backends.base import Backend
    from mozart.core.checkpoint import SheetState
    from mozart.core.config import JobConfig
    from mozart.core.errors import ClassificationResult, ErrorClassifier
    from mozart.core.logging import MozartLogger
    from mozart.execution.circuit_breaker import CircuitBreaker
    from mozart.execution.escalation import ConsoleCheckpointHandler, ConsoleEscalationHandler
    from mozart.execution.grounding import GroundingEngine
    from mozart.execution.preflight import PreflightChecker
    from mozart.execution.retry_strategy import AdaptiveRetryStrategy, RetryRecommendation
    from mozart.execution.validation import SheetValidationResult
    from mozart.healing.coordinator import HealingReport, SelfHealingCoordinator
    from mozart.learning.global_store import GlobalLearningStore
    from mozart.learning.judgment import JudgmentClient, JudgmentQuery, JudgmentResponse
    from mozart.learning.outcomes import OutcomeStore
    from mozart.prompts.templating import PromptBuilder
    from mozart.state.base import StateBackend

    from .models import RunSummary

from mozart.backends.base import ExecutionResult
from mozart.core.checkpoint import (
    CheckpointState,
    OutcomeCategory,
    ProgressSnapshotDict,
    SheetStatus,
)
from mozart.core.errors import ClassifiedError
from mozart.execution.escalation import (
    CheckpointContext,
    CheckpointResponse,
    CheckpointTrigger,
    EscalationContext,
    EscalationResponse,
    HistoricalSuggestion,
)
from mozart.execution.preflight import PreflightResult
from mozart.execution.retry_strategy import ErrorRecord
from mozart.execution.validation import (
    FailureHistoryStore,
    HistoricalFailure,
    ValidationEngine,
)
from mozart.learning.judgment import JudgmentQuery
from mozart.prompts.templating import CompletionContext, SheetContext
from mozart.utils.time import utc_now

from .models import (
    ExecutionFailureContext,
    FailureHandlingResult,
    FatalError,
    GracefulShutdownError,
    GroundingDecisionContext,
    ModeDecisionContext,
    ModeDecisionResult,
    SheetExecutionMode,
    SheetExecutionSetup,
    ValidationSuccessContext,
)


class _SheetSkipped(Exception):
    """Internal signal that a sheet was skipped during setup (e.g. via checkpoint)."""


class _StaleExecutionError(Exception):
    """Internal signal that a sheet execution was detected as stale (idle too long)."""

    def __init__(self, idle_seconds: float, timeout: float) -> None:
        self.idle_seconds = idle_seconds
        self.timeout = timeout
        super().__init__(
            f"Sheet idle for {idle_seconds:.1f}s (limit: {timeout}s)"
        )


class SheetExecutionMixin:
    """Mixin providing sheet execution methods for JobRunner.

    This mixin is composed with JobRunnerBase and other mixins to form the
    complete JobRunner class. Type annotations below declare the expected
    interface from other mixins for type checker compatibility.
    """

    _MAX_EXECUTION_HISTORY = 20
    _MAX_HEALING_CYCLES = 2
    _MAX_PREFLIGHT_WARNINGS_LOG = 20
    _CONSECUTIVE_FAILURE_ERROR_THRESHOLD = 3
    _MAX_GUARD_WAITS = 10

    # Type declarations for attributes from JobRunnerBase
    # These are populated at runtime by the base class __init__
    if TYPE_CHECKING:
        config: JobConfig
        backend: Backend
        state_backend: StateBackend
        console: Console
        outcome_store: OutcomeStore | None
        escalation_handler: ConsoleEscalationHandler | None
        checkpoint_handler: ConsoleCheckpointHandler | None
        judgment_client: JudgmentClient | None
        preflight_checker: PreflightChecker
        prompt_builder: PromptBuilder
        error_classifier: ErrorClassifier
        _logger: MozartLogger
        _circuit_breaker: CircuitBreaker | None
        _global_learning_store: GlobalLearningStore | None
        _grounding_engine: GroundingEngine | None
        _healing_coordinator: SelfHealingCoordinator | None
        _retry_strategy: AdaptiveRetryStrategy
        _current_sheet_num: int | None
        _execution_progress_snapshots: list[ProgressSnapshotDict]
        _last_progress_monotonic: float
        _current_sheet_patterns: list[str]
        _applied_pattern_ids: list[str]
        _exploration_pattern_ids: list[str]
        _exploitation_pattern_ids: list[str]
        _shutdown_requested: bool
        _summary: RunSummary | None
        _record_execution_failures: int
        _escalation_update_failures: int

        # Methods from JobRunnerBase
        async def _interruptible_sleep(self, seconds: float) -> None: ...
        async def _fire_event(
            self, event: str, sheet_num: int, data: dict[str, Any] | None = None,
        ) -> None: ...

        # Methods from PatternsMixin
        def _query_relevant_patterns(
            self,
            job_id: str,
            sheet_num: int,
            context_tags: list[str] | None = None,
        ) -> tuple[list[str], list[str]]: ...

        async def _record_pattern_feedback(
            self,
            pattern_ids: list[str],
            context: PatternFeedbackContext,
        ) -> None: ...

        # Methods from RecoveryMixin
        async def _try_self_healing(
            self,
            result: ExecutionResult,
            error: ClassifiedError,
            config_path: Path | None,
            sheet_num: int,
            retry_count: int,
            max_retries: int,
        ) -> HealingReport | None: ...

        async def _handle_rate_limit(
            self,
            state: CheckpointState,
            error_code: str = "E101",
            suggested_wait_seconds: float | None = None,
        ) -> None: ...

        def _classify_execution(
            self, result: ExecutionResult,
        ) -> ClassificationResult: ...

        # Methods from CostMixin
        async def _track_cost(
            self,
            result: ExecutionResult,
            sheet_state: SheetState,
            state: CheckpointState,
        ) -> tuple[int, int, float, float]: ...

        def _check_cost_limits(
            self,
            sheet_state: SheetState,
            state: CheckpointState,
        ) -> tuple[bool, str | None]: ...

        # Methods from ContextBuildingMixin
        def _build_sheet_context(
            self,
            sheet_num: int,
            state: CheckpointState | None = None,
        ) -> Any: ...

    def _log_swallowed_error(
        self,
        counter_attr: str,
        event: str,
        error: Exception,
        **extra: Any,
    ) -> int:
        """Log a swallowed error with escalation on consecutive failures.

        Increments the named counter and logs at warning level normally,
        upgrading to error level after ``_CONSECUTIVE_FAILURE_ERROR_THRESHOLD``
        consecutive failures.

        Args:
            counter_attr: Name of the ``self`` attribute tracking consecutive failures.
            event: Structured log event name.
            error: The caught exception.
            **extra: Additional structured log fields.

        Returns:
            The updated consecutive failure count.
        """
        count: int = getattr(self, counter_attr) + 1
        setattr(self, counter_attr, count)
        log_fn = (
            self._logger.error
            if count >= self._CONSECUTIVE_FAILURE_ERROR_THRESHOLD
            else self._logger.warning
        )
        log_fn(event, error=str(error), consecutive_failures=count, **extra, exc_info=True)
        return count

    async def _idle_watchdog(
        self,
        idle_timeout: float,
        check_interval: float,
        execution_task: asyncio.Task[ExecutionResult],
    ) -> None:
        """Monitor execution for idle (no-output) stalls.

        Runs concurrently with backend.execute(). If no progress callbacks
        arrive within ``idle_timeout`` seconds, cancels the execution task
        and raises ``_StaleExecutionError``.

        Args:
            idle_timeout: Max seconds of inactivity before cancellation.
            check_interval: How often to check for idle.
            execution_task: The running backend.execute() task to cancel on stale.
        """
        while not execution_task.done():
            await asyncio.sleep(check_interval)
            if execution_task.done():
                return
            idle = time.monotonic() - self._last_progress_monotonic
            if idle >= idle_timeout:
                self._logger.warning(
                    "stale_execution_detected",
                    idle_seconds=round(idle, 1),
                    idle_timeout=idle_timeout,
                    sheet_num=self._current_sheet_num,
                )
                execution_task.cancel()
                raise _StaleExecutionError(idle, idle_timeout)

    async def _execute_with_stale_detection(
        self,
        prompt: str,
        timeout_seconds: float | None,
    ) -> ExecutionResult:
        """Execute prompt with optional stale detection watchdog.

        Wraps ``backend.execute()`` with a concurrent idle watchdog when
        ``config.stale_detection.enabled`` is True. Falls back to plain
        execution when disabled.
        """
        cfg = self.config.stale_detection
        if not cfg.enabled:
            return await self.backend.execute(
                prompt, timeout_seconds=timeout_seconds,
            )

        # Seed the monotonic timestamp so the watchdog has a baseline
        start = time.monotonic()
        self._last_progress_monotonic = start

        exec_task = asyncio.create_task(
            self.backend.execute(prompt, timeout_seconds=timeout_seconds),
        )
        try:
            await self._idle_watchdog(
                cfg.idle_timeout_seconds,
                cfg.check_interval_seconds,
                exec_task,
            )
            # Watchdog exited normally (task completed) — get the result
            return await exec_task
        except _StaleExecutionError as exc:
            # Wait briefly for the cancelled task to clean up.
            # CancelledError here is expected (we cancelled exec_task);
            # TimeoutError means cleanup took too long.
            try:
                await asyncio.wait_for(exec_task, timeout=5.0)
            except (asyncio.CancelledError, TimeoutError, OSError):
                self._logger.debug(
                    "stale_cleanup_interrupted",
                    sheet_num=self._current_sheet_num,
                    exc_info=True,
                )
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Stale execution: no output for {exc.idle_seconds:.0f}s "
                       f"(limit: {exc.timeout}s)",
                duration_seconds=time.monotonic() - start,
                exit_code=None,
                exit_reason="timeout",
                error_type="stale",
                error_message=f"No output activity for {exc.idle_seconds:.0f}s",
            )

    async def _prepare_sheet_execution(
        self,
        state: CheckpointState,
        sheet_num: int,
    ) -> tuple[SheetExecutionSetup, SheetContext, ValidationEngine]:
        """Prepare all state needed before the sheet execution loop.

        Handles: context construction, pattern gathering, prompt building,
        preflight checks, and initial checkpoint handling.

        Args:
            state: Current job state.
            sheet_num: Sheet number to execute.

        Returns:
            Tuple of (setup, sheet_context, validation_engine).

        Raises:
            FatalError: If preflight checks fail or user aborts via checkpoint.
        """
        # Build sheet context (with cross-sheet data if configured)
        sheet_context = self._build_sheet_context(sheet_num, state)
        validation_engine = ValidationEngine(
            self.config.workspace,
            sheet_context.to_dict(),
        )

        # Query learned patterns before building prompt (Learning Activation)
        relevant_patterns = await self._gather_learned_patterns(state, sheet_num)

        # Get applicable validation rules for this sheet
        applicable_rules = validation_engine.get_applicable_rules(self.config.validations)

        # Query historical validation failures for history-aware prompts (Evolution v6)
        historical_failures = self._query_historical_failures(state, sheet_num)

        # Build original prompt with learned patterns, validation requirements,
        # and historical failures (Evolution v6: History-Aware Prompt Generation)
        original_prompt = self.prompt_builder.build_sheet_prompt(
            sheet_context,
            patterns=relevant_patterns if relevant_patterns else None,
            validation_rules=applicable_rules if applicable_rules else None,
            failure_history=historical_failures if historical_failures else None,
        )
        current_prompt = original_prompt
        current_mode = SheetExecutionMode.NORMAL

        # Run preflight checks before first execution (Task 2: Prompt Metrics)
        preflight_result = self._run_preflight_checks(
            prompt=original_prompt,
            sheet_context=sheet_context.to_dict(),
            sheet_num=sheet_num,
            state=state,
        )

        # Check for fatal preflight errors
        if preflight_result.has_errors:
            error_summary = "; ".join(preflight_result.errors)
            state.mark_sheet_failed(
                sheet_num,
                f"Preflight check failed: {error_summary}",
                "preflight",
            )
            await self.state_backend.save(state)
            self._logger.error(
                "sheet.preflight_failed",
                sheet_num=sheet_num,
                errors=preflight_result.errors,
            )
            raise FatalError(f"Sheet {sheet_num}: Preflight check failed - {error_summary}")

        # Log sheet start (Task 8: Logging Integration)
        self._logger.info(
            "sheet.started",
            sheet_num=sheet_num,
            execution_mode=current_mode.value,
            prompt_tokens=preflight_result.prompt_metrics.estimated_tokens,
            prompt_lines=preflight_result.prompt_metrics.line_count,
            preflight_warnings=len(preflight_result.warnings),
            patterns_injected=len(relevant_patterns),
        )
        await self._fire_event("sheet.started", sheet_num, {
            "execution_mode": current_mode.value,
            "prompt_tokens": preflight_result.prompt_metrics.estimated_tokens,
        })

        # v21 Evolution: Proactive Checkpoint System
        checkpoint_result = await self._check_proactive_checkpoint(
            state=state,
            sheet_num=sheet_num,
            prompt=current_prompt,
            retry_count=0,
        )
        if checkpoint_result is not None:
            if checkpoint_result.action == "abort":
                state.mark_job_failed("User aborted via checkpoint")
                await self.state_backend.save(state)
                raise FatalError(f"Sheet {sheet_num}: Aborted via checkpoint")
            elif checkpoint_result.action == "skip":
                state.mark_sheet_skipped(sheet_num, reason=checkpoint_result.guidance)
                await self.state_backend.save(state)
                self._logger.info(
                    "sheet.skipped_via_checkpoint",
                    sheet_num=sheet_num,
                    guidance=checkpoint_result.guidance,
                )
                # Signal caller to return early
                raise _SheetSkipped()
            elif checkpoint_result.action == "modify_prompt" and checkpoint_result.modified_prompt:
                current_prompt = checkpoint_result.modified_prompt
                self._logger.info(
                    "sheet.prompt_modified_via_checkpoint",
                    sheet_num=sheet_num,
                )

        setup = SheetExecutionSetup(
            original_prompt=original_prompt,
            current_prompt=current_prompt,
            current_mode=current_mode,
            max_retries=self.config.retry.max_retries,
            max_completion=self.config.retry.max_completion_attempts,
            relevant_patterns=relevant_patterns,
            preflight_warnings=len(preflight_result.warnings),
            preflight_token_estimate=preflight_result.prompt_metrics.estimated_tokens,
        )

        return setup, sheet_context, validation_engine

    async def _check_execution_guards(self, sheet_num: int) -> bool:
        """Check circuit breaker and cross-workspace rate limits before execution.

        Returns True if the caller should ``continue`` the retry loop
        (a guard tripped and we waited/will wait), False if execution
        should proceed normally.
        """
        # Circuit breaker check
        if (
            self._circuit_breaker is not None
            and not await self._circuit_breaker.can_execute()
        ):
            wait_time = await self._circuit_breaker.time_until_retry()
            cb_state = await self._circuit_breaker.get_state()
            self._logger.warning(
                "circuit_breaker.blocked",
                sheet_num=sheet_num,
                state=cb_state.value,
                wait_seconds=wait_time,
            )
            self.console.print(
                f"[yellow]Sheet {sheet_num}: Circuit breaker OPEN - "
                f"waiting {wait_time:.0f}s for recovery[/yellow]"
            )
            if wait_time and wait_time > 0:
                await self._interruptible_sleep(wait_time)
            return True

        # Cross-workspace rate limit check
        cross_ws_enabled = (
            self.config.circuit_breaker.cross_workspace_coordination
            and self.config.circuit_breaker.honor_other_jobs_rate_limits
        )
        if cross_ws_enabled and self._global_learning_store is not None:
            try:
                if self.config.backend.type == "claude_cli":
                    effective_model = self.config.backend.cli_model
                else:
                    effective_model = self.config.backend.model

                is_limited = False
                wait_seconds = None
                if effective_model is not None:
                    is_limited, wait_seconds = (
                        self._global_learning_store.is_rate_limited(
                            model=effective_model,
                        )
                    )
                if is_limited and wait_seconds and wait_seconds > 0:
                    self._logger.info(
                        "rate_limit.cross_workspace_honored",
                        sheet_num=sheet_num,
                        wait_seconds=round(wait_seconds, 0),
                    )
                    self.console.print(
                        f"[yellow]Sheet {sheet_num}: Honoring cross-workspace rate limit - "
                        f"waiting {wait_seconds:.0f}s[/yellow]"
                    )
                    await self._interruptible_sleep(wait_seconds)
            except (sqlite3.Error, OSError) as e:  # Non-critical: cross-workspace check
                self._logger.warning(
                    "rate_limit.cross_workspace_check_failed",
                    sheet_num=sheet_num,
                    error=str(e),
                )

        return False

    async def _handle_validation_success(
        self,
        context: ValidationSuccessContext,
    ) -> str | None:
        """Handle the success path when all validations pass.

        Runs grounding hooks, records pattern feedback, updates learning stores,
        and marks the sheet as completed.

        Args:
            context: ValidationSuccessContext with all required state.

        Returns:
            None if sheet is complete (caller should return).
            "continue" if grounding failed and loop should re-execute.
            "break" if grounding escalation chose to skip (exit loop).
        """
        # Unpack for local readability
        state = context.state
        sheet_num = context.sheet_num
        result = context.result
        validation_result = context.validation_result
        validation_duration = context.validation_duration
        current_prompt = context.current_prompt
        normal_attempts = context.normal_attempts
        completion_attempts = context.completion_attempts
        execution_start_time = context.execution_start_time
        execution_history = context.execution_history
        pending_recovery = context.pending_recovery

        if not result.success:
            self._logger.warning(
                "sheet.exit_code_ignored_validations_passed",
                sheet_num=sheet_num,
                exit_code=result.exit_code,
                exit_reason=result.exit_reason,
                validation_count=len(validation_result.results),
            )
            self.console.print(
                f"[yellow]Sheet {sheet_num}: CLI exit {result.exit_code} ignored - "
                f"all {len(validation_result.results)} validations passed[/yellow]"
            )

        # ===== GROUNDING: External validation hooks (v8 Evolution) =====
        grounding_ctx = await self._run_grounding_hooks(
            sheet_num=sheet_num,
            prompt=current_prompt,
            output=result.stdout or "",
            validation_result=validation_result,
        )

        if not grounding_ctx.passed and self.config.grounding.fail_on_grounding_failure:
            self._logger.warning(
                "sheet.grounding_failed",
                sheet_num=sheet_num,
                message=grounding_ctx.message,
                confidence=grounding_ctx.confidence,
            )
            self.console.print(
                f"[yellow]Sheet {sheet_num}: Grounding failed - "
                f"{grounding_ctx.message}[/yellow]"
            )
            grounding_mode, grounding_reason, _ = await self._decide_with_judgment(
                sheet_num=sheet_num,
                validation_result=validation_result,
                execution_history=execution_history,
                normal_attempts=normal_attempts,
                completion_attempts=completion_attempts,
                grounding_context=grounding_ctx,
            )
            self.console.print(
                f"[dim]Sheet {sheet_num}: Grounding decision: {grounding_mode.value} "
                f"- {grounding_reason}[/dim]"
            )
            if grounding_mode == SheetExecutionMode.ESCALATE:
                grounding_error_history: list[str] = (
                    [grounding_ctx.message] if grounding_ctx.message else []
                )
                try:
                    response = await self._handle_escalation(
                        state=state,
                        sheet_num=sheet_num,
                        validation_result=validation_result,
                        current_prompt=current_prompt,
                        error_history=grounding_error_history,
                        normal_attempts=normal_attempts,
                    )
                    if response.action == "abort":
                        state.mark_sheet_failed(
                            sheet_num,
                            f"Escalation abort: {response.guidance or 'grounding failure'}",
                            "escalation",
                        )
                        grounding_sheet_state = state.sheets[sheet_num]
                        self._update_escalation_outcome(
                            grounding_sheet_state, "aborted", sheet_num
                        )
                        await self.state_backend.save(state)
                        raise FatalError(
                            f"Sheet {sheet_num} aborted via escalation: "
                            f"{response.guidance or 'grounding failure'}"
                        )
                    elif response.action == "skip":
                        state.mark_sheet_completed(
                            sheet_num,
                            validation_passed=False,
                            validation_details=validation_result.to_dict_list(),
                        )
                        grounding_sheet_state = state.sheets[sheet_num]
                        # Tag outcome so learning store can distinguish
                        # escalation skips from genuine validation failures.
                        if grounding_sheet_state.outcome_data is None:
                            grounding_sheet_state.outcome_data = {}
                        grounding_sheet_state.outcome_data["escalation_skipped"] = True
                        self._update_escalation_outcome(
                            grounding_sheet_state, "skipped", sheet_num
                        )
                        await self.state_backend.save(state)
                        return "break"
                except FatalError:
                    raise
            return "continue"

        await self._record_circuit_breaker_success(state, sheet_num)

        execution_duration = time.monotonic() - execution_start_time

        outcome_category, success_without_retry = self._classify_success_outcome(
            normal_attempts, completion_attempts,
        )

        # Populate SheetState learning fields
        sheet_state = state.sheets.get(sheet_num)
        if sheet_state is None:
            self._logger.error("sheet_state_missing", sheet_num=sheet_num)
            state.mark_sheet_failed(
                sheet_num,
                error_message="Internal error: sheet state missing after execution",
                execution_duration_seconds=execution_duration,
            )
            await self.state_backend.save(state)
            return "break"
        sheet_state.success_without_retry = success_without_retry
        sheet_state.outcome_category = outcome_category
        sheet_state.confidence_score = validation_result.pass_percentage / 100.0
        sheet_state.applied_pattern_ids = self._applied_pattern_ids.copy()
        sheet_state.applied_pattern_descriptions = self._current_sheet_patterns.copy()
        if grounding_ctx.hooks_executed > 0:
            sheet_state.grounding_passed = grounding_ctx.passed
            sheet_state.grounding_confidence = grounding_ctx.confidence
            sheet_state.grounding_guidance = grounding_ctx.recovery_guidance

        escalation_pending = bool(
            sheet_state.outcome_data
            and sheet_state.outcome_data.get("escalation_record_id")
        )

        # ===== CRITICAL: Mark completed and save BEFORE auxiliary learning =====
        # Learning store operations (_record_pattern_feedback, _record_sheet_outcome)
        # must never prevent the completion state from being persisted. If any
        # auxiliary operation throws, the sheet would stay "in_progress" forever
        # despite validations passing.
        state.mark_sheet_completed(
            sheet_num,
            validation_passed=True,
            validation_details=validation_result.to_dict_list(),
            execution_duration_seconds=execution_duration,
        )

        sheet_state = state.sheets[sheet_num]
        self._update_escalation_outcome(sheet_state, "success", sheet_num)

        await self.state_backend.save(state)

        # ===== AUXILIARY: Learning store operations (non-critical) =====
        # These improve future runs but must not block state persistence.
        await self._record_success_learning(
            state=state,
            sheet_num=sheet_num,
            validation_result=validation_result,
            grounding_ctx=grounding_ctx,
            execution_duration=execution_duration,
            normal_attempts=normal_attempts,
            completion_attempts=completion_attempts,
            success_without_retry=success_without_retry,
            escalation_pending=escalation_pending,
            pending_recovery=pending_recovery,
        )
        self._logger.info(
            "sheet.completed",
            sheet_num=sheet_num,
            duration_seconds=round(execution_duration, 2),
            validation_duration_seconds=round(validation_duration, 3),
            validation_count=len(validation_result.results),
            validation_pass_rate=100.0,
            outcome_category=outcome_category,
            retry_count=normal_attempts - 1 if normal_attempts > 0 else 0,
            completion_attempts=completion_attempts,
            success_without_retry=success_without_retry,
            exit_code_was_nonzero=not result.success,
        )
        await self._fire_event("sheet.completed", sheet_num, {
            "duration_seconds": round(execution_duration, 2),
            "exit_code": result.exit_code,
            "validation_pass_rate": 100.0,
            "outcome_category": outcome_category,
        })
        # Log validation summary, distinguishing new vs inherited validations
        total_validations = len(validation_result.results)
        new_count = self._count_new_validations(validation_result, sheet_num)
        inherited = total_validations - new_count

        if inherited > 0:
            self.console.print(
                f"[green]Sheet {sheet_num}: {new_count} new + "
                f"{inherited} inherited validations passed "
                f"({total_validations} total)[/green]"
            )
        else:
            self.console.print(
                f"[green]Sheet {sheet_num}: All {total_validations} "
                f"validations passed[/green]"
            )
        return None

    async def _handle_execution_failure(
        self,
        context: ExecutionFailureContext,
    ) -> FailureHandlingResult:
        """Handle non-success execution results: classify, retry, heal, or abort.

        Dispatches to per-failure-mode handlers after common preamble:
        - _handle_rate_limit_failure: Rate limit detected
        - _handle_fatal_error: Non-retryable error
        - _handle_retries_exhausted: Max retries reached (may try self-healing)
        - _handle_adaptive_abort: Adaptive strategy recommends stopping
        - _handle_transient_retry: Normal retry with delay

        Args:
            context: ExecutionFailureContext with all needed state.

        Returns:
            FailureHandlingResult indicating caller action ('continue' or 'fatal').
        """
        classification = self._classify_execution(context.result)
        error = classification.primary

        normal_attempts = context.normal_attempts
        healing_attempts = context.healing_attempts
        pending_recovery = context.pending_recovery

        # Record recovery outcome as failure if we had a pending retry
        if pending_recovery is not None and self._global_learning_store is not None:
            try:
                self._global_learning_store.record_error_recovery(
                    error_code=pending_recovery["error_code"],
                    suggested_wait=pending_recovery["suggested_wait"],
                    actual_wait=pending_recovery["actual_wait"],
                    recovery_success=False,
                    model=self.config.backend.model,
                )
                self._logger.debug(
                    "learning.recovery_recorded",
                    sheet_num=context.sheet_num,
                    error_code=pending_recovery["error_code"],
                    actual_wait=pending_recovery["actual_wait"],
                    recovery_success=False,
                )
            except (sqlite3.Error, OSError) as e:
                self._logger.warning(
                    "learning.recovery_record_failed",
                    sheet_num=context.sheet_num,
                    error=str(e),
                )
            pending_recovery = None

        # Track error for adaptive retry with root cause info
        error_record = ErrorRecord.from_classification_result(
            result=classification,
            sheet_num=context.sheet_num,
            attempt_num=normal_attempts + 1,
        )
        context.error_history.append(error_record)

        # Record failure in circuit breaker
        if self._circuit_breaker is not None and not error.is_rate_limit:
            await self._circuit_breaker.record_failure()
            # Persist CB state change for observability
            cb_stats = await self._circuit_breaker.get_stats()
            cb_state_val = (await self._circuit_breaker.get_state()).value
            context.state.record_circuit_breaker_change(
                state=cb_state_val,
                trigger="failure_recorded",
                consecutive_failures=cb_stats.consecutive_failures,
            )

        # Dispatch to per-failure-mode handler
        if error.is_rate_limit:
            return await self._handle_rate_limit_failure(
                context, error, normal_attempts, healing_attempts, pending_recovery,
            )

        if not error.should_retry:
            return await self._handle_fatal_error(
                context, error, normal_attempts, healing_attempts, pending_recovery,
            )

        # Transient error - get adaptive retry recommendation
        retry_recommendation = self._retry_strategy.analyze(
            error_history=context.error_history,
            max_retries=context.max_retries,
        )
        normal_attempts += 1

        if normal_attempts >= context.max_retries:
            return await self._handle_retries_exhausted(
                context, error, normal_attempts, healing_attempts,
                pending_recovery,
            )

        if not retry_recommendation.should_retry:
            return await self._handle_adaptive_abort(
                context, error, retry_recommendation,
                normal_attempts, healing_attempts, pending_recovery,
            )

        return await self._handle_transient_retry(
            context, error, retry_recommendation,
            normal_attempts, healing_attempts, pending_recovery,
        )

    async def _handle_rate_limit_failure(
        self,
        context: ExecutionFailureContext,
        error: ClassifiedError,
        normal_attempts: int,
        healing_attempts: int,
        pending_recovery: dict[str, Any] | None,
    ) -> FailureHandlingResult:
        """Handle rate limit errors by clearing history and waiting."""
        context.error_history.clear()
        await self._handle_rate_limit(
            context.state,
            error_code=error.error_code.value,
            suggested_wait_seconds=error.suggested_wait_seconds,
        )
        return FailureHandlingResult(
            action="continue",
            normal_attempts=normal_attempts,
            healing_attempts=healing_attempts,
            pending_recovery=pending_recovery,
        )

    async def _handle_fatal_error(
        self,
        context: ExecutionFailureContext,
        error: ClassifiedError,
        normal_attempts: int,
        healing_attempts: int,
        pending_recovery: dict[str, Any] | None,
    ) -> FailureHandlingResult:
        """Handle non-retryable fatal errors."""
        failed_validations = context.validation_result.get_failed_results()
        validation_info = ""
        if failed_validations:
            failed_names = [
                f.rule.description or "unnamed validation"
                for f in failed_validations
            ]
            validation_info = f" (validations failed: {', '.join(failed_names)})"

        context.state.mark_sheet_failed(
            context.sheet_num,
            error.message + validation_info,
            error.category.value,
            exit_code=context.result.exit_code,
            exit_signal=context.result.exit_signal,
            exit_reason=context.result.exit_reason,
            execution_duration_seconds=context.result.duration_seconds,
        )
        await self.state_backend.save(context.state)
        self._logger.error(
            "sheet.failed",
            sheet_num=context.sheet_num,
            error_category=error.category.value,
            error_message=error.message,
            exit_code=context.result.exit_code,
            exit_signal=context.result.exit_signal,
            exit_reason=context.result.exit_reason,
            duration_seconds=round(context.result.duration_seconds or 0, 2),
            validations_passed=context.passed_count,
            validations_failed=context.failed_count,
            failed_validation_names=[f.rule.description for f in failed_validations],
            stdout_tail=(
                context.result.stdout[-TRUNCATE_STDOUT_TAIL_CHARS:]
                if context.result.stdout
                else None
            ),
        )
        await self._fire_event("sheet.failed", context.sheet_num, {
            "error_category": error.category.value,
            "exit_code": context.result.exit_code,
            "duration_seconds": round(context.result.duration_seconds or 0, 2),
        })
        return FailureHandlingResult(
            action="fatal",
            normal_attempts=normal_attempts,
            healing_attempts=healing_attempts,
            pending_recovery=pending_recovery,
            fatal_message=f"Sheet {context.sheet_num}: {error.message}{validation_info}",
        )

    async def _handle_retries_exhausted(
        self,
        context: ExecutionFailureContext,
        error: ClassifiedError,
        normal_attempts: int,
        healing_attempts: int,
        pending_recovery: dict[str, Any] | None,
    ) -> FailureHandlingResult:
        """Handle retry exhaustion: try self-healing, then fail."""
        # Try self-healing before giving up
        if (
            self._healing_coordinator is not None
            and healing_attempts < context.max_healing_cycles
        ):
            healing_report = await self._try_self_healing(
                result=context.result,
                error=error,
                config_path=None,
                sheet_num=context.sheet_num,
                retry_count=normal_attempts,
                max_retries=context.max_retries,
            )
            if healing_report and healing_report.should_retry:
                healing_attempts += 1
                normal_attempts = context.max_retries - 1  # Grant one more retry
                self.console.print(healing_report.format())
                self._logger.info(
                    "sheet.healed",
                    sheet_num=context.sheet_num,
                    healing_attempt=healing_attempts,
                    max_healing_cycles=context.max_healing_cycles,
                    remedies_applied=len(healing_report.actions_taken),
                )
                return FailureHandlingResult(
                    action="continue",
                    normal_attempts=normal_attempts,
                    healing_attempts=healing_attempts,
                    pending_recovery=pending_recovery,
                )

        # Record pattern feedback for failure
        sheet_state = context.state.sheets.get(context.sheet_num)
        if sheet_state is not None:
            sheet_state.applied_pattern_ids = self._applied_pattern_ids.copy()
            sheet_state.applied_pattern_descriptions = self._current_sheet_patterns.copy()
        await self._record_pattern_feedback(
            pattern_ids=self._applied_pattern_ids,
            context=PatternFeedbackContext(
                validation_passed=False,
                success_without_retry=False,
                sheet_num=context.sheet_num,
                grounding_confidence=(
                    context.grounding_ctx.confidence
                    if context.grounding_ctx.hooks_executed > 0
                    else None
                ),
            ),
        )

        context.state.mark_sheet_failed(
            context.sheet_num,
            f"Failed after {context.max_retries} retries: {error.message}",
            error.category.value,
            exit_code=context.result.exit_code,
            exit_signal=context.result.exit_signal,
            exit_reason=context.result.exit_reason,
            execution_duration_seconds=context.result.duration_seconds,
        )
        await self.state_backend.save(context.state)
        self._logger.error(
            "sheet.failed",
            sheet_num=context.sheet_num,
            error_category=error.category.value,
            error_message=f"Retries exhausted: {error.message}",
            attempt=normal_attempts,
            max_retries=context.max_retries,
            exit_code=context.result.exit_code,
            duration_seconds=round(context.result.duration_seconds or 0, 2),
        )
        await self._fire_event("sheet.failed", context.sheet_num, {
            "error_category": error.category.value,
            "exit_code": context.result.exit_code,
            "duration_seconds": round(context.result.duration_seconds or 0, 2),
            "retries_exhausted": True,
        })
        return FailureHandlingResult(
            action="fatal",
            normal_attempts=normal_attempts,
            healing_attempts=healing_attempts,
            pending_recovery=pending_recovery,
            fatal_message=(
                f"Sheet {context.sheet_num} failed after"
                f" {context.max_retries} retries"
            ),
        )

    async def _handle_adaptive_abort(
        self,
        context: ExecutionFailureContext,
        error: ClassifiedError,
        retry_recommendation: RetryRecommendation,
        normal_attempts: int,
        healing_attempts: int,
        pending_recovery: dict[str, Any] | None,
    ) -> FailureHandlingResult:
        """Handle adaptive retry strategy recommending early stop."""
        context.state.mark_sheet_failed(
            context.sheet_num,
            f"Adaptive retry aborted: {retry_recommendation.reason}",
            error.category.value,
            exit_code=context.result.exit_code,
            exit_signal=context.result.exit_signal,
            exit_reason=context.result.exit_reason,
            execution_duration_seconds=context.result.duration_seconds,
        )
        await self.state_backend.save(context.state)
        self._logger.warning(
            "sheet.adaptive_retry_aborted",
            sheet_num=context.sheet_num,
            error_category=error.category.value,
            pattern=retry_recommendation.detected_pattern.value,
            reason=retry_recommendation.reason,
            confidence=round(retry_recommendation.confidence, 3),
            strategy=retry_recommendation.strategy_used,
            attempt=normal_attempts,
        )
        return FailureHandlingResult(
            action="fatal",
            normal_attempts=normal_attempts,
            healing_attempts=healing_attempts,
            pending_recovery=pending_recovery,
            fatal_message=f"Sheet {context.sheet_num} aborted: {retry_recommendation.reason}",
        )

    async def _handle_transient_retry(
        self,
        context: ExecutionFailureContext,
        error: ClassifiedError,
        retry_recommendation: RetryRecommendation,
        normal_attempts: int,
        healing_attempts: int,
        pending_recovery: dict[str, Any] | None,
    ) -> FailureHandlingResult:
        """Handle transient error with retry delay and learning."""
        self._logger.warning(
            "sheet.retry",
            sheet_num=context.sheet_num,
            attempt=normal_attempts,
            max_retries=context.max_retries,
            error_category=error.category.value,
            reason=error.message,
            retry_delay_seconds=round(retry_recommendation.delay_seconds, 2),
            retry_confidence=round(retry_recommendation.confidence, 3),
            retry_pattern=retry_recommendation.detected_pattern.value,
            retry_strategy=retry_recommendation.strategy_used,
        )
        self.console.print(
            f"[yellow]Sheet {context.sheet_num}: {retry_recommendation.detected_pattern.value} - "
            f"retry {normal_attempts}/{context.max_retries} "
            f"(delay: {retry_recommendation.delay_seconds:.1f}s, "
            f"confidence: {retry_recommendation.confidence:.0%})[/yellow]"
        )
        await self._fire_event("sheet.retrying", context.sheet_num, {
            "attempt": normal_attempts,
            "max_retries": context.max_retries,
            "reason": error.category.value,
            "wait_seconds": round(retry_recommendation.delay_seconds, 2),
        })

        # Track pending recovery for learning outcome
        if self._global_learning_store is not None:
            pending_recovery = {
                "error_code": error.error_code.value,
                "suggested_wait": error.suggested_wait_seconds or 0.0,
                "actual_wait": retry_recommendation.delay_seconds,
            }

        # Active Broadcast Polling
        await self._poll_broadcast_discoveries(context.state.job_id, context.sheet_num)

        await asyncio.sleep(retry_recommendation.delay_seconds)
        return FailureHandlingResult(
            action="continue",
            normal_attempts=normal_attempts,
            healing_attempts=healing_attempts,
            pending_recovery=pending_recovery,
        )

    async def _apply_mode_decision(
        self,
        ctx: ModeDecisionContext,
    ) -> ModeDecisionResult:
        """Apply judgment decision and manage mode transitions.

        Handles completion mode, escalation mode, and retry mode after
        validation has determined the sheet is not fully passing.

        Delegates to specialized handlers for each mode:
        - _apply_completion_mode: Build completion prompt and retry
        - _apply_escalation_mode: Invoke escalation handler and dispatch response
        - _apply_retry_mode: Retry or mark fatal if retries exhausted
        """
        next_mode, decision_reason, prompt_modifications = await self._decide_with_judgment(
            sheet_num=ctx.sheet_num,
            validation_result=ctx.validation_result,
            execution_history=ctx.execution_history,
            normal_attempts=ctx.normal_attempts,
            completion_attempts=ctx.completion_attempts,
        )

        self.console.print(
            f"[dim]Sheet {ctx.sheet_num}: Decision: {next_mode.value} - {decision_reason}[/dim]"
        )

        if next_mode == SheetExecutionMode.COMPLETION:
            return await self._apply_completion_mode(ctx, prompt_modifications)

        # Apply prompt modifications from judgment when retrying
        current_prompt = ctx.current_prompt
        if prompt_modifications and next_mode == SheetExecutionMode.RETRY:
            modification_text = "\n".join(prompt_modifications)
            current_prompt = (
                ctx.original_prompt + "\n\n---\nJudgment modifications:\n" + modification_text
            )
            self.console.print(
                f"[blue]Sheet {ctx.sheet_num}: Applying {len(prompt_modifications)} "
                f"prompt modifications from judgment[/blue]"
            )

        if next_mode == SheetExecutionMode.ESCALATE:
            return await self._apply_escalation_mode(ctx, current_prompt)

        # RETRY MODE (default)
        return await self._apply_retry_mode(ctx, current_prompt)

    async def _apply_completion_mode(
        self,
        ctx: ModeDecisionContext,
        prompt_modifications: list[str] | None,
    ) -> ModeDecisionResult:
        """Handle COMPLETION mode: build completion prompt and continue."""
        completion_attempts = ctx.completion_attempts + 1
        ctx.state.sheets[ctx.sheet_num].completion_attempts = completion_attempts

        completion_ctx = CompletionContext(
            sheet_num=ctx.sheet_num,
            total_sheets=ctx.state.total_sheets,
            passed_validations=ctx.validation_result.get_passed_results(),
            failed_validations=ctx.validation_result.get_failed_results(),
            completion_attempt=completion_attempts,
            max_completion_attempts=ctx.max_completion,
            original_prompt=ctx.original_prompt,
            workspace=self.config.workspace,
        )
        current_prompt = self.prompt_builder.build_completion_prompt(
            completion_ctx,
            semantic_hints=prompt_modifications,
        )

        self.console.print(
            f"[yellow]Sheet {ctx.sheet_num}: Entering completion mode "
            f"({ctx.validation_result.passed_count}/{len(ctx.validation_result.results)} passed, "
            f"{ctx.pass_pct:.0f}%). Attempt {completion_attempts}/{ctx.max_completion}[/yellow]"
        )

        await asyncio.sleep(self.config.retry.completion_delay_seconds)
        return ModeDecisionResult(
            action="continue",
            current_prompt=current_prompt,
            current_mode=SheetExecutionMode.COMPLETION,
            normal_attempts=ctx.normal_attempts,
            completion_attempts=completion_attempts,
        )

    async def _apply_escalation_mode(
        self,
        ctx: ModeDecisionContext,
        current_prompt: str,
    ) -> ModeDecisionResult:
        """Handle ESCALATE mode: invoke escalation and dispatch response action."""
        sheet_num = ctx.sheet_num
        state = ctx.state
        normal_attempts = ctx.normal_attempts
        sheet_state = state.sheets[sheet_num]

        error_history = [sheet_state.error_message] if sheet_state.error_message else []

        response = await self._handle_escalation(
            state=state,
            sheet_num=sheet_num,
            validation_result=ctx.validation_result,
            current_prompt=current_prompt,
            error_history=error_history,
            normal_attempts=normal_attempts,
        )

        if response.action == "retry":
            normal_attempts += 1
            if normal_attempts >= ctx.max_retries:
                state.mark_sheet_failed(
                    sheet_num,
                    f"Escalation retry exhausted after {ctx.max_retries} attempts",
                    "escalation",
                )
                self._update_escalation_outcome(state.sheets[sheet_num], "failed", sheet_num)
                await self.state_backend.save(state)
                return ModeDecisionResult(
                    action="fatal",
                    current_prompt=ctx.original_prompt,
                    current_mode=SheetExecutionMode.RETRY,
                    normal_attempts=normal_attempts,
                    completion_attempts=ctx.completion_attempts,
                    fatal_message=f"Sheet {sheet_num} exhausted retries after escalation",
                )
            await asyncio.sleep(self._get_retry_delay(normal_attempts))
            return ModeDecisionResult(
                action="continue",
                current_prompt=ctx.original_prompt,
                current_mode=SheetExecutionMode.RETRY,
                normal_attempts=normal_attempts,
                completion_attempts=ctx.completion_attempts,
            )

        if response.action == "skip":
            state.mark_sheet_completed(
                sheet_num,
                validation_passed=False,
                validation_details=ctx.validation_result.to_dict_list(),
            )
            state.sheets[sheet_num].outcome_category = OutcomeCategory.SKIPPED_BY_ESCALATION
            self._update_escalation_outcome(state.sheets[sheet_num], "skipped", sheet_num)
            await self.state_backend.save(state)
            self.console.print(
                f"[yellow]Sheet {sheet_num}: Skipped via escalation[/yellow]"
            )
            return ModeDecisionResult(
                action="return",
                current_prompt=current_prompt,
                current_mode=ctx.current_mode,
                normal_attempts=normal_attempts,
                completion_attempts=ctx.completion_attempts,
            )

        if response.action == "abort":
            state.mark_sheet_failed(
                sheet_num,
                "Aborted via escalation",
                "escalation",
            )
            self._update_escalation_outcome(state.sheets[sheet_num], "aborted", sheet_num)
            await self.state_backend.save(state)
            return ModeDecisionResult(
                action="fatal",
                current_prompt=current_prompt,
                current_mode=ctx.current_mode,
                normal_attempts=normal_attempts,
                completion_attempts=ctx.completion_attempts,
                fatal_message=f"Sheet {sheet_num}: Job aborted via escalation",
            )

        # response.action == "modify_prompt" (or unknown -- treated as modify)
        if response.modified_prompt is None:
            self.console.print(
                f"[yellow]Sheet {sheet_num}: No modified prompt provided, "
                f"falling back to retry[/yellow]"
            )
            normal_attempts += 1
            current_prompt = ctx.original_prompt
        else:
            current_prompt = response.modified_prompt
            self.console.print(
                f"[blue]Sheet {sheet_num}: Retrying with modified prompt[/blue]"
            )
        await asyncio.sleep(self._get_retry_delay(normal_attempts))
        return ModeDecisionResult(
            action="continue",
            current_prompt=current_prompt,
            current_mode=SheetExecutionMode.RETRY,
            normal_attempts=normal_attempts,
            completion_attempts=ctx.completion_attempts,
        )

    async def _apply_retry_mode(
        self,
        ctx: ModeDecisionContext,
        current_prompt: str,
    ) -> ModeDecisionResult:
        """Handle RETRY mode: retry or mark fatal if retries exhausted."""
        sheet_num = ctx.sheet_num
        normal_attempts = ctx.normal_attempts + 1

        if normal_attempts >= ctx.max_retries:
            sheet_state = ctx.state.sheets[sheet_num]
            sheet_state.applied_pattern_ids = self._applied_pattern_ids.copy()
            sheet_state.applied_pattern_descriptions = self._current_sheet_patterns.copy()
            await self._record_pattern_feedback(
                pattern_ids=self._applied_pattern_ids,
                context=PatternFeedbackContext(
                    validation_passed=False,
                    success_without_retry=False,
                    sheet_num=sheet_num,
                    grounding_confidence=None,
                ),
            )

            ctx.state.mark_sheet_failed(
                sheet_num,
                f"Validation failed after {ctx.max_retries} retries and "
                f"{ctx.completion_attempts} completion attempts "
                f"({ctx.validation_result.failed_count} validations still failing)",
                "validation",
            )
            await self.state_backend.save(ctx.state)
            return ModeDecisionResult(
                action="fatal",
                current_prompt=ctx.original_prompt,
                current_mode=SheetExecutionMode.RETRY,
                normal_attempts=normal_attempts,
                completion_attempts=ctx.completion_attempts,
                fatal_message=(
                    f"Sheet {sheet_num} exhausted all retry options "
                    f"({ctx.validation_result.failed_count} validations failing)"
                ),
            )

        self.console.print(
            f"[red]Sheet {sheet_num}: {ctx.validation_result.failed_count} validations failed "
            f"({ctx.pass_pct:.0f}% passed). Full retry {normal_attempts}/{ctx.max_retries}[/red]"
        )

        await asyncio.sleep(self._get_retry_delay(normal_attempts))
        return ModeDecisionResult(
            action="continue",
            current_prompt=ctx.original_prompt,
            current_mode=SheetExecutionMode.RETRY,
            normal_attempts=normal_attempts,
            completion_attempts=ctx.completion_attempts,
        )

    async def _execute_sheet_with_recovery(
        self,
        state: CheckpointState,
        sheet_num: int,
    ) -> None:
        """Execute a single sheet with full retry/completion logic.

        Flow:
        1. Execute sheet normally
        2. Run validations
        3. If all pass -> complete
        4. If majority pass -> enter completion mode
        5. If minority pass -> full retry

        The method delegates to extracted helpers for each major phase:
        - _prepare_sheet_execution(): Setup and context building
        - _handle_validation_success(): Success path with grounding
        - _handle_execution_failure(): Error classification and recovery
        - _apply_mode_decision(): Judgment, completion, escalation, retry

        Args:
            state: Current job state.
            sheet_num: Sheet number to execute.

        Raises:
            FatalError: If all retries exhausted or fatal error encountered.
        """
        # Track execution timing for learning
        execution_start_time = time.monotonic()

        # Phase 1: Prepare all setup state
        try:
            setup, sheet_context, validation_engine = await self._prepare_sheet_execution(
                state, sheet_num
            )
        except _SheetSkipped:
            return

        # Unpack setup into local variables for the execution loop
        original_prompt = setup.original_prompt
        current_prompt = setup.current_prompt
        current_mode = setup.current_mode
        max_retries = setup.max_retries
        max_completion = setup.max_completion

        # Track attempts
        normal_attempts = 0
        completion_attempts = 0
        healing_attempts = 0
        max_healing_cycles = self._MAX_HEALING_CYCLES

        # Track execution history for judgment (Phase 4)
        # Capped to prevent unbounded memory growth during long retry loops.
        # Most recent results are most relevant for judgment decisions.
        execution_history: deque[ExecutionResult] = deque(maxlen=self._MAX_EXECUTION_HISTORY)

        # Track error history for adaptive retry (Task 13)
        error_history: list[ErrorRecord] = []

        # Evolution #3: Track pending recovery outcome for global learning store
        pending_recovery: dict[str, Any] | None = None

        # Snapshot mtimes before execution (for file_modified checks).
        # Must be outside the retry loop so retries compare against the
        # *pre-execution* baseline, not the post-previous-attempt state.
        validation_engine.snapshot_mtime_files(self.config.validations)

        # Guard spin prevention: cap consecutive guard waits to avoid
        # infinite loops when circuit breaker never recovers.
        consecutive_guard_waits = 0

        while True:
            # Mark sheet started
            state.mark_sheet_started(sheet_num)
            sheet_state = state.sheets[sheet_num]
            sheet_state.execution_mode = current_mode.value  # type: ignore[assignment]  # SheetExecutionMode values match Literal
            await self.state_backend.save(state)

            # Initialize grounding context
            grounding_ctx = GroundingDecisionContext(
                passed=True,
                message="No grounding hooks executed",
                hooks_executed=0,
            )

            # Initialize execution progress tracking (Task 4)
            self._current_sheet_num = sheet_num
            self._execution_progress_snapshots.clear()

            # Check pre-execution guards (circuit breaker + rate limits)
            if await self._check_execution_guards(sheet_num):
                consecutive_guard_waits += 1
                if consecutive_guard_waits >= self._MAX_GUARD_WAITS:
                    self._logger.error(
                        "guard_spin_limit_reached",
                        sheet_num=sheet_num,
                        max_guard_waits=self._MAX_GUARD_WAITS,
                    )
                    msg = (
                        f"Execution guards blocked "
                        f"{self._MAX_GUARD_WAITS} consecutive times"
                    )
                    state.mark_sheet_failed(sheet_num, error_message=msg)
                    await self.state_backend.save(state)
                    raise FatalError(msg)
                continue
            consecutive_guard_waits = 0

            # Execute backend with per-sheet configuration
            result = await self._configure_and_execute_sheet(
                sheet_num,
                state.total_sheets,
                current_prompt,
                current_mode,
                retry_count=normal_attempts,
            )

            # Post-execution bookkeeping: snapshots, output, cost, history
            await self._record_execution_bookkeeping(
                state, sheet_num, sheet_state, result,
                current_prompt, execution_history,
            )

            # ===== VALIDATION-FIRST APPROACH =====
            validation_start = time.monotonic()
            validation_result = await validation_engine.run_validations(
                self.config.validations
            )
            validation_duration = time.monotonic() - validation_start

            # Update state with validation details
            self._update_sheet_validation_state(state, sheet_num, validation_result)
            await self.state_backend.save(state)

            # Fire validation event (Issue #49: Runner Callbacks)
            val_event = (
                "sheet.validation_passed"
                if validation_result.all_passed
                else "sheet.validation_failed"
            )
            await self._fire_event(val_event, sheet_num, {
                "passed_count": validation_result.passed_count,
                "failed_count": validation_result.failed_count,
                "pass_rate": validation_result.pass_percentage,
            })

            if validation_result.all_passed:
                # Delegate success handling to extracted method
                success_action = await self._handle_validation_success(
                    ValidationSuccessContext(
                        state=state,
                        sheet_num=sheet_num,
                        result=result,
                        validation_result=validation_result,
                        validation_duration=validation_duration,
                        current_prompt=current_prompt,
                        normal_attempts=normal_attempts,
                        completion_attempts=completion_attempts,
                        execution_start_time=execution_start_time,
                        execution_history=execution_history,
                        pending_recovery=pending_recovery,
                    )
                )
                if success_action is None:
                    return  # Sheet completed
                elif success_action == "break":
                    break  # Grounding skip — exit loop
                else:
                    # "continue" — grounding failed, re-execute
                    pending_recovery = None
                    continue

            # ===== VALIDATIONS INCOMPLETE =====
            passed_count, failed_count, pass_pct = self._log_incomplete_validations(
                sheet_num, validation_result,
            )
            completion_threshold = self.config.retry.completion_threshold_percent

            # Refresh error_message with current validation failures so that
            # completion/escalation modes see up-to-date failure descriptions.
            failed_descs = [
                r.format_failure_summary()
                for r in validation_result.get_failed_results()
            ]
            if failed_descs:
                sheet_state.error_message = "; ".join(failed_descs[:3])
            else:
                sheet_state.error_message = None

            # ===== RATE LIMIT CHECK (before completion threshold) =====
            if result.rate_limited:
                classification = self._classify_execution(result)
                error = classification.primary

                if error.is_rate_limit:
                    error_history.clear()
                    await self._handle_rate_limit(
                        state,
                        error_code=error.error_code.value,
                        suggested_wait_seconds=error.suggested_wait_seconds,
                    )
                    continue

            # Check if pass_pct is high enough for completion mode
            if pass_pct >= completion_threshold:
                self.console.print(
                    f"[blue]Sheet {sheet_num}: Pass rate"
                    f" ({pass_pct:.0f}%) >= threshold"
                    f" ({completion_threshold}%) - "
                    f"using completion mode[/blue]"
                )
            elif not result.success:
                # Delegate non-success error handling to extracted method
                failure_result = await self._handle_execution_failure(
                    ExecutionFailureContext(
                        state=state,
                        sheet_num=sheet_num,
                        result=result,
                        validation_result=validation_result,
                        passed_count=passed_count,
                        failed_count=failed_count,
                        error_history=error_history,
                        normal_attempts=normal_attempts,
                        max_retries=max_retries,
                        healing_attempts=healing_attempts,
                        max_healing_cycles=max_healing_cycles,
                        pending_recovery=pending_recovery,
                        grounding_ctx=grounding_ctx,
                    )
                )
                # Apply updated counters from failure handler
                normal_attempts = failure_result.normal_attempts
                healing_attempts = failure_result.healing_attempts
                pending_recovery = failure_result.pending_recovery

                if failure_result.action == "fatal":
                    raise FatalError(failure_result.fatal_message)
                elif failure_result.action == "continue":
                    continue

            # ===== JUDGMENT/COMPLETION/ESCALATION/RETRY MODE =====
            mode_result = await self._apply_mode_decision(ModeDecisionContext(
                state=state,
                sheet_num=sheet_num,
                validation_result=validation_result,
                execution_history=execution_history,
                original_prompt=original_prompt,
                current_prompt=current_prompt,
                current_mode=current_mode,
                normal_attempts=normal_attempts,
                completion_attempts=completion_attempts,
                max_retries=max_retries,
                max_completion=max_completion,
                pass_pct=pass_pct,
            ))

            # Apply updated state from mode decision
            current_prompt = mode_result.current_prompt
            current_mode = mode_result.current_mode
            normal_attempts = mode_result.normal_attempts
            completion_attempts = mode_result.completion_attempts

            if mode_result.action == "fatal":
                raise FatalError(mode_result.fatal_message)
            elif mode_result.action == "return":
                return
            elif mode_result.action == "continue":
                continue
            else:
                raise FatalError(
                    f"Unknown mode_result action: {mode_result.action!r}"
                )

    async def _configure_and_execute_sheet(
        self,
        sheet_num: int,
        total_sheets: int,
        current_prompt: str,
        current_mode: SheetExecutionMode,
        retry_count: int = 0,
    ) -> ExecutionResult:
        """Configure per-sheet backend settings and execute the prompt.

        Handles preamble, output log paths, prompt extensions, timeout
        overrides, and per-sheet backend overrides (GH#76, GH#78). Uses
        the override lock to prevent concurrent sheets from stomping on
        each other's settings.

        Args:
            sheet_num: Sheet number being executed.
            total_sheets: Total number of sheets in the concert.
            current_prompt: The prompt to execute.
            current_mode: Current execution mode (for display).
            retry_count: Number of previous failed attempts (0 = first run).

        Returns:
            ExecutionResult from the backend.
        """
        # Per-sheet output log for real-time visibility
        output_log_base = self.config.workspace / "logs" / f"sheet-{sheet_num:02d}"
        self.backend.set_output_log_path(output_log_base)

        # Dynamic preamble — context-aware identity, position, retry status
        from mozart.prompts.preamble import build_preamble

        preamble = build_preamble(
            sheet_num=sheet_num,
            total_sheets=total_sheets,
            workspace=self.config.workspace,
            retry_count=retry_count,
            is_parallel=self.config.parallel.enabled,
        )
        self.backend.set_preamble(preamble)

        # Prompt extensions (GH#76): score-level + sheet-level
        extensions = [
            *self.config.prompt.prompt_extensions,
            *self.config.sheet.prompt_extensions.get(sheet_num, []),
        ]
        self.backend.set_prompt_extensions(extensions)

        sheet_timeout = self.config.backend.timeout_overrides.get(sheet_num)

        # Per-sheet backend overrides (GH#78).
        # Lock protects the apply → execute → clear cycle against
        # concurrent sheets stomping on each other's saved originals.
        sheet_override = self.config.backend.sheet_overrides.get(sheet_num)
        override_params: dict[str, object] | None = None
        if sheet_override:
            override_params = {
                k: v
                for k, v in sheet_override.model_dump().items()
                if v is not None
            }
            if "timeout_seconds" in override_params:  # sheet_overrides takes precedence
                sheet_timeout = float(override_params.pop("timeout_seconds"))  # type: ignore[arg-type]

        self.console.print(
            f"[blue]Sheet {sheet_num}: {current_mode.value} execution[/blue]"
        )

        if override_params:
            async with self.backend.override_lock:
                self.backend.apply_overrides(override_params)
                try:
                    return await self._execute_with_stale_detection(
                        current_prompt, timeout_seconds=sheet_timeout,
                    )
                finally:
                    self.backend.clear_overrides()
        else:
            return await self._execute_with_stale_detection(
                current_prompt, timeout_seconds=sheet_timeout,
            )

    async def _record_execution_bookkeeping(
        self,
        state: CheckpointState,
        sheet_num: int,
        sheet_state: SheetState,
        result: ExecutionResult,
        current_prompt: str,
        execution_history: deque[ExecutionResult],
    ) -> None:
        """Post-execution bookkeeping: progress, output, cost, and history.

        Captures execution progress snapshots, raw output for debugging,
        agent feedback, cost tracking, and records the execution in the
        state backend history.

        Args:
            state: Current job state.
            sheet_num: Sheet number that was executed.
            sheet_state: Sheet-specific state object.
            result: Execution result from the backend.
            current_prompt: The prompt that was executed.
            execution_history: Deque to append the result to.
        """
        if self._execution_progress_snapshots:
            sheet_state.progress_snapshots = self._execution_progress_snapshots.copy()
            sheet_state.last_activity_at = utc_now()

        sheet_state.capture_output(
            result.stdout,
            result.stderr,
            max_bytes=self.config.backend.max_output_capture_bytes,
        )
        self._extract_agent_feedback(sheet_state, result.stdout)
        await self._enforce_cost_limits(result, sheet_state, state, sheet_num)
        execution_history.append(result)

        # Record in history (no-op for backends that don't support it)
        try:
            await self.state_backend.record_execution(
                job_id=self.config.name,
                sheet_num=sheet_num,
                attempt_num=sheet_state.attempt_count,
                prompt=current_prompt[:2000] if current_prompt else None,
                output=result.stdout[-5000:] if result.stdout else None,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
            )
            self._record_execution_failures = 0
        except Exception as e:
            self._log_swallowed_error(
                "_record_execution_failures",
                "record_execution_failed",
                e,
                sheet_num=sheet_num,
                attempt_num=sheet_state.attempt_count,
            )

    async def _gather_learned_patterns(
        self,
        state: CheckpointState,
        sheet_num: int,
    ) -> list[str]:
        """Query and combine local + global learned patterns for a sheet.

        Queries the local outcome store first (workspace-specific patterns),
        then the global learning store (cross-workspace patterns), and
        deduplicates the results. Updates self._current_sheet_patterns and
        self._applied_pattern_ids for feedback tracking.

        Args:
            state: Current job state (for job_id).
            sheet_num: Sheet number to query patterns for.

        Returns:
            Combined list of pattern descriptions (local-first ordering).
        """
        relevant_patterns: list[str] = []
        self._current_sheet_patterns = []
        self._applied_pattern_ids = []

        # Query from local outcome store first (workspace-specific)
        if self.outcome_store is not None:
            try:
                relevant_patterns = await self.outcome_store.get_relevant_patterns(
                    context={
                        "job_id": state.job_id,
                        "sheet_num": sheet_num,
                    },
                    limit=3,
                )
                # Track local pattern IDs so the feedback loop can observe them.
                # Local patterns don't exist in the global store, so we generate
                # synthetic IDs with a "local:" prefix for traceability.
                for idx, _desc in enumerate(relevant_patterns):
                    self._applied_pattern_ids.append(f"local:{sheet_num}:{idx}")
            except Exception as e:
                self._logger.warning(
                    "patterns.query_local_failed",
                    sheet_num=sheet_num,
                    error=str(e),
                    exc_info=True,
                )

        # Query from global learning store (cross-workspace patterns)
        global_patterns, global_pattern_ids = self._query_relevant_patterns(
            job_id=state.job_id,
            sheet_num=sheet_num,
        )

        # Combine patterns (local first, then global, deduplicated)
        if global_patterns:
            for gp, gp_id in zip(global_patterns, global_pattern_ids, strict=True):
                if gp not in relevant_patterns:
                    relevant_patterns.append(gp)
                    self._applied_pattern_ids.append(gp_id)

        # Store applied patterns for feedback tracking
        self._current_sheet_patterns = relevant_patterns.copy()

        if relevant_patterns:
            self._logger.debug(
                "patterns.combined",
                sheet_num=sheet_num,
                local_count=len(relevant_patterns) - len(global_patterns),
                global_count=len(global_patterns),
                total_patterns=len(relevant_patterns),
            )

        return relevant_patterns

    def _query_historical_failures(
        self,
        state: CheckpointState,
        sheet_num: int,
    ) -> list[HistoricalFailure]:
        """Query recent validation failures from prior sheets.

        Uses FailureHistoryStore to find failures from nearby sheets,
        which are injected into prompts for history-aware execution.

        Args:
            state: Current job state (contains sheet histories).
            sheet_num: Current sheet number.

        Returns:
            List of recent failures.  Returns empty list on query error
            (a warning is logged so the failure is not silent).
        """
        historical_failures: list[HistoricalFailure] = []
        try:
            failure_history_store = FailureHistoryStore(state)
            historical_failures = failure_history_store.query_recent_failures(
                current_sheet=sheet_num,
                lookback_sheets=3,
                limit=3,
            )

            if historical_failures:
                self._logger.debug(
                    "history.failures_found",
                    sheet_num=sheet_num,
                    failure_count=len(historical_failures),
                    sheets=[f.sheet_num for f in historical_failures],
                )
        except Exception as e:
            self._logger.warning(
                "history.query_failed",
                sheet_num=sheet_num,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )

        return historical_failures

    def _extract_agent_feedback(
        self,
        sheet_state: SheetState,
        stdout: str,
    ) -> None:
        """Extract structured feedback from agent output (GH#15).

        When feedback collection is enabled, searches the agent's stdout
        for a feedback block matching the configured regex pattern. The
        extracted text is parsed according to the configured format and
        stored in sheet_state.agent_feedback.

        Args:
            sheet_state: SheetState to populate with feedback.
            stdout: Raw stdout from the execution.
        """
        feedback_config = self.config.feedback
        if not feedback_config.enabled or not stdout:
            return

        match = re.search(feedback_config.pattern, stdout)
        if not match or not match.group(1):
            return

        raw_feedback = match.group(1).strip()

        if feedback_config.format == "json":
            import json
            try:
                sheet_state.agent_feedback = json.loads(raw_feedback)
            except json.JSONDecodeError as e:
                self._logger.warning(
                    "feedback.json_parse_failed",
                    error=str(e),
                    raw_length=len(raw_feedback),
                )
        elif feedback_config.format == "yaml":
            try:
                import yaml
                sheet_state.agent_feedback = yaml.safe_load(raw_feedback)
            except Exception as e:
                self._logger.warning(
                    "feedback.yaml_parse_failed",
                    error=str(e),
                    raw_length=len(raw_feedback),
                )
        else:
            # text format — store as-is in a dict wrapper
            sheet_state.agent_feedback = {"text": raw_feedback}

    def _run_preflight_checks(
        self,
        prompt: str,
        sheet_context: dict[str, Any],
        sheet_num: int,
        state: CheckpointState,
    ) -> PreflightResult:
        """Run preflight checks on a prompt before execution.

        Analyzes the prompt for potential issues like excessive size,
        missing file references, and invalid working directories.

        Updates SheetState with metrics and any warnings.

        Args:
            prompt: The prompt text to analyze.
            sheet_context: Context dictionary for template expansion.
            sheet_num: Current sheet number.
            state: Current job state to update.

        Returns:
            PreflightResult with metrics and any warnings/errors.
        """
        from mozart.core.checkpoint import SheetState

        if sheet_num not in state.sheets:
            state.sheets[sheet_num] = SheetState(sheet_num=sheet_num)

        sheet_state = state.sheets[sheet_num]

        result = self.preflight_checker.check(prompt, sheet_context)

        sheet_state.prompt_metrics = result.prompt_metrics.to_dict()
        sheet_state.preflight_warnings = result.warnings.copy()

        metrics = result.prompt_metrics

        if result.has_warnings:
            limit = self._MAX_PREFLIGHT_WARNINGS_LOG
            log_warnings = result.warnings[:limit]
            if len(result.warnings) > limit:
                extra = len(result.warnings) - limit
                log_warnings.append(f"... and {extra} more warnings")
            self._logger.warning(
                "sheet.preflight_warnings",
                sheet_num=sheet_num,
                warnings=log_warnings,
                total_warnings=len(result.warnings),
                estimated_tokens=metrics.estimated_tokens,
            )

        self._logger.debug(
            "sheet.preflight_metrics",
            sheet_num=sheet_num,
            character_count=metrics.character_count,
            estimated_tokens=metrics.estimated_tokens,
            line_count=metrics.line_count,
            word_count=metrics.word_count,
            has_file_references=metrics.has_file_references,
            referenced_path_count=len(metrics.referenced_paths),
        )

        for warning in result.warnings:
            self.console.print(f"[yellow]Sheet {sheet_num} preflight: {warning}[/yellow]")

        for error in result.errors:
            self.console.print(f"[red]Sheet {sheet_num} preflight ERROR: {error}[/red]")

        self.console.print(
            f"[dim]Sheet {sheet_num}: ~{metrics.estimated_tokens:,} tokens, "
            f"{metrics.line_count:,} lines, "
            f"{len(metrics.referenced_paths)} file refs[/dim]"
        )

        return result

    def _update_sheet_validation_state(
        self,
        state: CheckpointState,
        sheet_num: int,
        result: SheetValidationResult,
    ) -> None:
        """Update sheet state with validation tracking.

        Args:
            state: Current job state.
            sheet_num: Sheet number.
            result: Validation results.
        """
        sheet = state.sheets.get(sheet_num)
        if sheet:
            sheet.validation_passed = result.all_passed
            sheet.validation_details = result.to_dict_list()
            sheet.last_pass_percentage = result.pass_percentage

            sheet.passed_validations = [
                r.rule.description or r.rule.path or "validation"
                for r in result.get_passed_results()
            ]
            sheet.failed_validations = [
                r.rule.description or r.rule.path or "validation"
                for r in result.get_failed_results()
            ]

    async def _run_grounding_hooks(
        self,
        sheet_num: int,
        prompt: str,
        output: str,
        validation_result: SheetValidationResult,
    ) -> GroundingDecisionContext:
        """Run external grounding hooks to validate sheet output.

        Executes registered grounding hooks to validate the sheet output against
        external sources.

        Args:
            sheet_num: Sheet number being validated.
            prompt: The prompt used for execution.
            output: The raw output from execution.
            validation_result: Results from internal validation engine.

        Returns:
            GroundingDecisionContext with pass/fail, confidence, and guidance.
        """
        if self._grounding_engine is None or not self.config.grounding.enabled:
            return GroundingDecisionContext.disabled()

        from mozart.execution.grounding import GroundingContext, GroundingPhase

        context = GroundingContext(
            job_id=self.config.name,
            sheet_num=sheet_num,
            prompt=prompt,
            output=output,
            validation_passed=validation_result.all_passed,
            validation_details=validation_result.to_dict_list(),
            metadata={
                "backend": self.config.backend.type,
                "workspace": str(self.config.workspace),
            },
        )

        results = await self._grounding_engine.run_hooks(
            context, GroundingPhase.POST_VALIDATION
        )

        grounding_ctx = GroundingDecisionContext.from_results(results)

        self._logger.info(
            "grounding.hooks_completed",
            sheet_num=sheet_num,
            hooks_run=grounding_ctx.hooks_executed,
            passed=grounding_ctx.passed,
            message=grounding_ctx.message,
            confidence=grounding_ctx.confidence,
        )

        should_log_escalation = (
            not grounding_ctx.passed
            and self.config.grounding.escalate_on_failure
            and grounding_ctx.should_escalate
        )
        if should_log_escalation:
            self._logger.warning(
                "grounding.escalation_triggered",
                sheet_num=sheet_num,
                message=grounding_ctx.message,
            )

        return grounding_ctx

    def _log_incomplete_validations(
        self,
        sheet_num: int,
        validation_result: SheetValidationResult,
    ) -> tuple[int, int, float]:
        """Log incomplete validation results and return metrics.

        Args:
            sheet_num: Sheet number for log context.
            validation_result: Validation result with pass/fail details.

        Returns:
            Tuple of (passed_count, failed_count, pass_percentage).
        """
        passed_count = len(validation_result.get_passed_results())
        failed_count = len(validation_result.get_failed_results())
        pass_pct = validation_result.executed_pass_percentage

        total = passed_count + failed_count
        self.console.print(
            f"[yellow]Sheet {sheet_num}: Validations — "
            f"{failed_count} failed (of {total}), "
            f"{passed_count} passed ({pass_pct:.0f}%)[/yellow]"
        )
        for failed in validation_result.get_failed_results():
            self.console.print(
                f"  [red]✗[/red] {failed.rule.description}"
            )

        return passed_count, failed_count, pass_pct

    @staticmethod
    def _count_new_validations(
        validation_result: SheetValidationResult,
        sheet_num: int,
    ) -> int:
        """Count validations first active at this sheet (not inherited from earlier sheets).

        Parses each validation rule's condition to determine its origin sheet.
        Rules with no condition or conditions that don't specify a sheet threshold
        are assumed to originate at sheet 1.

        Args:
            validation_result: Validation result containing all rules.
            sheet_num: Current sheet number.

        Returns:
            Number of validations whose origin sheet >= sheet_num.
        """
        _ORIGIN_PATTERN = re.compile(r"(?:sheet_num|stage)\s*(?:>=|==)\s*(\d+)")
        count = 0
        for vr in validation_result.results:
            condition = vr.rule.condition
            if condition is None:
                origin = 1
            else:
                match = _ORIGIN_PATTERN.search(condition)
                origin = int(match.group(1)) if match else 1
            if origin >= sheet_num:
                count += 1
        return count

    async def _enforce_cost_limits(
        self,
        result: ExecutionResult,
        sheet_state: SheetState,
        state: CheckpointState,
        sheet_num: int,
    ) -> None:
        """Track execution cost and enforce cost limits.

        Raises GracefulShutdownError if cost limits are exceeded,
        pausing the job for later resumption.

        Args:
            result: Execution result with cost data.
            sheet_state: Current sheet state.
            state: Current job state.
            sheet_num: Sheet number for log context.

        Raises:
            GracefulShutdownError: If cost limit is exceeded.
        """
        if not self.config.cost_limits.enabled:
            return

        await self._track_cost(result, sheet_state, state)

        cost_exceeded, cost_reason = self._check_cost_limits(sheet_state, state)
        if cost_exceeded:
            state.cost_limit_reached = True
            if self._summary is not None:
                self._summary.cost_limit_hit = True
            self._logger.warning(
                "cost.limit_exceeded",
                sheet_num=sheet_num,
                reason=cost_reason,
                job_cost=round(state.total_estimated_cost, 4),
            )
            self.console.print(f"[red]Cost limit exceeded: {cost_reason}[/red]")

            state.mark_job_paused()
            state.error_message = f"Cost limit: {cost_reason}"
            await self.state_backend.save(state)
            raise GracefulShutdownError(f"Cost limit exceeded: {cost_reason}")

    @staticmethod
    def _classify_success_outcome(
        normal_attempts: int,
        completion_attempts: int,
    ) -> tuple[OutcomeCategory, bool]:
        """Classify the outcome category for a successfully validated sheet.

        Args:
            normal_attempts: Number of normal retry attempts used.
            completion_attempts: Number of completion-mode attempts used.

        Returns:
            Tuple of (outcome_category, success_without_retry).
        """
        # normal_attempts counts executions (first run = 1), not retries.
        # A first-attempt success means exactly 1 normal attempt and 0 completion attempts.
        success_without_retry = normal_attempts <= 1 and completion_attempts == 0
        if success_without_retry:
            return OutcomeCategory.SUCCESS_FIRST_TRY, True
        elif completion_attempts > 0:
            return OutcomeCategory.SUCCESS_COMPLETION, False
        else:
            return OutcomeCategory.SUCCESS_RETRY, False

    async def _record_circuit_breaker_success(
        self,
        state: CheckpointState,
        sheet_num: int,
    ) -> None:
        """Record a success event in the circuit breaker if enabled."""
        if self._circuit_breaker is None:
            return
        try:
            await self._circuit_breaker.record_success()
            cb_stats = await self._circuit_breaker.get_stats()
            cb_state_val = (await self._circuit_breaker.get_state()).value
            state.record_circuit_breaker_change(
                state=cb_state_val,
                trigger="success_recorded",
                consecutive_failures=cb_stats.consecutive_failures,
            )
        except Exception as e:
            self._logger.warning(
                "circuit_breaker.success_record_failed",
                sheet_num=sheet_num,
                error=str(e),
                exc_info=True,
            )

    async def _record_success_learning(
        self,
        *,
        state: CheckpointState,
        sheet_num: int,
        validation_result: SheetValidationResult,
        grounding_ctx: GroundingDecisionContext,
        execution_duration: float,
        normal_attempts: int,
        completion_attempts: int,
        success_without_retry: bool,
        escalation_pending: bool,
        pending_recovery: dict[str, Any] | None,
    ) -> None:
        """Record auxiliary learning data after a successful sheet completion.

        Called after the sheet is already marked completed and saved.
        All operations here are non-critical — failures are logged but
        do not affect sheet completion status.
        """
        # Record recovery outcome if we had a pending retry
        if pending_recovery is not None and self._global_learning_store is not None:
            try:
                self._global_learning_store.record_error_recovery(
                    error_code=pending_recovery["error_code"],
                    suggested_wait=pending_recovery["suggested_wait"],
                    actual_wait=pending_recovery["actual_wait"],
                    recovery_success=True,
                    model=self.config.backend.model,
                )
                self._logger.debug(
                    "learning.recovery_recorded",
                    sheet_num=sheet_num,
                    error_code=pending_recovery["error_code"],
                    actual_wait=pending_recovery["actual_wait"],
                    recovery_success=True,
                )
            except (sqlite3.Error, OSError) as e:
                self._logger.warning(
                    "learning.recovery_record_failed",
                    sheet_num=sheet_num,
                    error=str(e),
                )

        # Record pattern feedback to global store
        prior_sheet = state.sheets.get(sheet_num - 1) if sheet_num > 1 else None
        prior_status = prior_sheet.status.value if prior_sheet and prior_sheet.status else None

        validation_types: list[str] | None = sorted({
            r.rule.type for r in validation_result.results if r.rule and r.rule.type
        }) or None

        try:
            await self._record_pattern_feedback(
                pattern_ids=self._applied_pattern_ids,
                context=PatternFeedbackContext(
                    validation_passed=True,
                    success_without_retry=success_without_retry,
                    sheet_num=sheet_num,
                    grounding_confidence=(
                        grounding_ctx.confidence
                        if grounding_ctx.hooks_executed > 0
                        else None
                    ),
                    validation_types=validation_types,
                    prior_sheet_status=prior_status,
                    retry_iteration=max(normal_attempts - 1, 0),
                    escalation_was_pending=escalation_pending,
                ),
            )
        except Exception as e:
            self._logger.warning(
                "learning.pattern_feedback_failed_post_save",
                sheet_num=sheet_num,
                error=str(e),
                exc_info=True,
            )

        # Record outcome for learning if store is available
        try:
            await self._record_sheet_outcome(
                sheet_num=sheet_num,
                job_id=state.job_id,
                validation_result=validation_result,
                execution_duration=execution_duration,
                normal_attempts=normal_attempts,
                completion_attempts=completion_attempts,
                success_without_retry=success_without_retry,
                final_status=SheetStatus.COMPLETED,
            )
        except Exception as e:
            self._logger.warning(
                "learning.sheet_outcome_failed_post_save",
                sheet_num=sheet_num,
                error=str(e),
                exc_info=True,
            )

    # _classify_execution() and _classify_error() are provided by RecoveryMixin

    def _get_retry_delay(self, attempt: int) -> float:
        """Calculate retry delay with exponential backoff and jitter.

        Args:
            attempt: Current attempt number (1-indexed).

        Returns:
            Delay in seconds.
        """
        base = self.config.retry.base_delay_seconds
        exp = self.config.retry.exponential_base
        max_delay = self.config.retry.max_delay_seconds

        delay = min(base * (exp ** (attempt - 1)), max_delay)

        if self.config.retry.jitter:
            delay *= 0.5 + random.random()

        return delay

    async def _poll_broadcast_discoveries(
        self,
        job_id: str,
        sheet_num: int,
    ) -> None:
        """Poll for pattern discoveries from other concurrent jobs.

        v16 Evolution: Active Broadcast Polling - enables jobs to receive
        real-time pattern discoveries from other jobs during retry waits.

        Args:
            job_id: Current job ID (to exclude own discoveries).
            sheet_num: Current sheet number (for logging context).
        """
        if self._global_learning_store is None:
            return

        try:
            discoveries = self._global_learning_store.check_recent_pattern_discoveries(
                exclude_job_id=job_id,
                min_effectiveness=0.5,
                limit=10,
            )

            if discoveries:
                self._logger.info(
                    "broadcast.discoveries_received",
                    sheet_num=sheet_num,
                    discovery_count=len(discoveries),
                    pattern_ids=[d.pattern_id for d in discoveries],
                    pattern_names=[d.pattern_name for d in discoveries],
                    avg_effectiveness=round(
                        sum(d.effectiveness_score for d in discoveries) / len(discoveries),
                        3,
                    ),
                )

                self.console.print(
                    f"[dim]Sheet {sheet_num}: Received {len(discoveries)} pattern "
                    f"broadcast(s) from other jobs[/dim]"
                )

                for discovery in discoveries:
                    self._logger.debug(
                        "broadcast.discovery_detail",
                        sheet_num=sheet_num,
                        pattern_id=discovery.pattern_id,
                        pattern_name=discovery.pattern_name,
                        pattern_type=discovery.pattern_type,
                        effectiveness=round(discovery.effectiveness_score, 3),
                        context_tags=discovery.context_tags,
                    )

        except (sqlite3.Error, OSError, ValueError) as e:
            self._logger.warning(
                "broadcast.polling_failed",
                sheet_num=sheet_num,
                error=str(e),
                exc_info=True,
            )

    async def _record_sheet_outcome(
        self,
        sheet_num: int,
        job_id: str,
        validation_result: SheetValidationResult,
        execution_duration: float,
        normal_attempts: int,
        completion_attempts: int,
        success_without_retry: bool,
        final_status: SheetStatus,
    ) -> None:
        """Record sheet outcome for learning if outcome store is available.

        Args:
            sheet_num: Sheet number that completed.
            job_id: Job identifier.
            validation_result: Validation results from the sheet.
            execution_duration: Total execution time in seconds.
            normal_attempts: Number of normal/retry attempts.
            completion_attempts: Number of completion mode attempts.
            success_without_retry: Whether sheet succeeded on first attempt.
            final_status: Final sheet status.
        """
        if self.outcome_store is None:
            return

        from mozart.learning.outcomes import SheetOutcome

        outcome = SheetOutcome(
            sheet_id=f"{job_id}_sheet_{sheet_num}",
            job_id=job_id,
            validation_results=validation_result.to_dict_list(),
            execution_duration=execution_duration,
            retry_count=normal_attempts - 1 if normal_attempts > 0 else 0,
            completion_mode_used=completion_attempts > 0,
            final_status=final_status,
            validation_pass_rate=validation_result.pass_percentage,
            success_without_retry=success_without_retry,
            patterns_detected=[],
            timestamp=utc_now(),
        )

        await self.outcome_store.record(outcome)

    def _update_escalation_outcome(
        self,
        sheet_state: SheetState,
        outcome: str,
        sheet_num: int,
    ) -> None:
        """Update the outcome of an escalation decision in the global learning store.

        This closes the escalation feedback loop by recording what happened after
        an escalation action was taken.

        Args:
            sheet_state: The sheet state containing escalation_record_id in outcome_data.
            outcome: The final outcome (success, failed, skipped, aborted).
            sheet_num: Sheet number for logging context.
        """
        if self._global_learning_store is None:
            return

        outcome_data = sheet_state.outcome_data or {}
        escalation_record_id = outcome_data.get("escalation_record_id")

        if not escalation_record_id:
            return

        try:
            updated = self._global_learning_store.update_escalation_outcome(
                escalation_id=escalation_record_id,
                outcome_after_action=outcome,
            )
            self._escalation_update_failures = 0
            if updated:
                self._logger.info(
                    "escalation.outcome_updated",
                    sheet_num=sheet_num,
                    escalation_id=escalation_record_id,
                    outcome=outcome,
                )
            else:
                self._logger.warning(
                    "escalation.outcome_update_not_found",
                    sheet_num=sheet_num,
                    escalation_id=escalation_record_id,
                    outcome=outcome,
                )
        except Exception as e:
            self._log_swallowed_error(
                "_escalation_update_failures",
                "escalation.outcome_update_failed",
                e,
                sheet_num=sheet_num,
                escalation_id=escalation_record_id,
                outcome=outcome,
            )

    def _is_escalation_available(self) -> bool:
        """Check if escalation is configured and a handler is available."""
        return (
            self.config.learning.escalation_enabled
            and self.escalation_handler is not None
        )

    def _should_enter_completion_mode(
        self, pass_pct: float, completion_attempts: int
    ) -> bool:
        """Check if completion mode should be attempted.

        Returns True when pass percentage exceeds the threshold AND
        completion attempts haven't been exhausted.
        """
        return (
            pass_pct > self.config.retry.completion_threshold_percent
            and completion_attempts < self.config.retry.max_completion_attempts
        )

    def _decide_next_action(
        self,
        validation_result: SheetValidationResult,
        normal_attempts: int,
        completion_attempts: int,
        grounding_context: GroundingDecisionContext | None = None,
    ) -> tuple[SheetExecutionMode, str, list[str]]:
        """Decide the next action based on confidence, pass percentage, and semantic info.

        This method implements adaptive retry strategy using validation confidence,
        pass percentage, and semantic failure categories.

        Args:
            validation_result: Results from validation engine with confidence scores.
            normal_attempts: Number of retry attempts already made.
            completion_attempts: Number of completion mode attempts already made.
            grounding_context: Optional context from grounding hooks.

        Returns:
            Tuple of (SheetExecutionMode, reason string, semantic_hints list).
        """
        pass_pct = validation_result.executed_pass_percentage
        max_completion = self.config.retry.max_completion_attempts

        high_threshold = self.config.learning.high_confidence_threshold
        min_threshold = self.config.learning.min_confidence_threshold

        semantic_summary = validation_result.get_semantic_summary()
        semantic_hints = validation_result.get_actionable_hints(limit=3)
        dominant_category = semantic_summary.get("dominant_category")

        # Factor grounding confidence into decision
        confidence = validation_result.aggregate_confidence
        grounding_suffix = ""
        if grounding_context is not None and grounding_context.hooks_executed > 0:
            confidence = (confidence * 0.7) + (grounding_context.confidence * 0.3)
            grounding_suffix = f", grounding: {grounding_context.confidence:.2f}"

            if grounding_context.recovery_guidance:
                semantic_hints = list(semantic_hints)
                semantic_hints.insert(0, f"[Grounding] {grounding_context.recovery_guidance}")

            if grounding_context.should_escalate and self._is_escalation_available():
                return (
                    SheetExecutionMode.ESCALATE,
                    f"grounding hook requests escalation: {grounding_context.message}",
                    semantic_hints,
                )

        category_suffix = ""
        if dominant_category:
            category_suffix = f" (dominant: {dominant_category})"
        category_suffix += grounding_suffix

        # High confidence + majority passed -> completion mode
        should_complete = self._should_enter_completion_mode(
            pass_pct, completion_attempts,
        )
        if confidence > high_threshold and should_complete:
            return (
                SheetExecutionMode.COMPLETION,
                f"high confidence ({confidence:.2f}) with {pass_pct:.0f}% passed, "
                f"attempting focused completion{category_suffix}",
                semantic_hints,
            )

        threshold_pct = self.config.retry.completion_threshold_percent
        if confidence > high_threshold and pass_pct > threshold_pct:
            # Completion attempts exhausted
            return (
                SheetExecutionMode.RETRY,
                f"completion attempts exhausted ({completion_attempts}/{max_completion}), "
                f"falling back to full retry{category_suffix}",
                semantic_hints,
            )

        # Low confidence -> escalate if enabled, else retry
        if confidence < min_threshold:
            auto_apply_enabled = self.config.learning.auto_apply_enabled
            auto_apply_threshold = self.config.learning.auto_apply_trust_threshold

            if auto_apply_enabled and self._can_auto_apply(auto_apply_threshold):
                self._logger.info(
                    "auto_apply.bypass_escalation",
                    confidence=confidence,
                    threshold=auto_apply_threshold,
                )
                return (
                    SheetExecutionMode.RETRY,
                    f"low confidence ({confidence:.2f}) but auto-applying from high-trust "
                    f"patterns{category_suffix}",
                    semantic_hints,
                )

            if self._is_escalation_available():
                return (
                    SheetExecutionMode.ESCALATE,
                    f"low confidence ({confidence:.2f}) requires escalation{category_suffix}",
                    semantic_hints,
                )

            return (
                SheetExecutionMode.RETRY,
                f"low confidence ({confidence:.2f}) but escalation not available, "
                f"attempting retry{category_suffix}",
                semantic_hints,
            )

        # Medium confidence zone: use pass percentage to decide
        if self._should_enter_completion_mode(pass_pct, completion_attempts):
            return (
                SheetExecutionMode.COMPLETION,
                f"medium confidence ({confidence:.2f}) with {pass_pct:.0f}% passed, "
                f"attempting completion mode{category_suffix}",
                semantic_hints,
            )

        return (
            SheetExecutionMode.RETRY,
            f"medium confidence ({confidence:.2f}) with {pass_pct:.0f}% passed, "
            f"full retry needed{category_suffix}",
            semantic_hints,
        )

    def _can_auto_apply(self, trust_threshold: float) -> bool:
        """Check if high-trust patterns exist that allow auto-apply.

        Args:
            trust_threshold: Minimum trust score required.

        Returns:
            True if high-trust patterns exist and auto-apply is viable.
        """
        if not self.config.learning.use_global_patterns:
            return False

        try:
            if self._global_learning_store is None:
                return False

            high_trust_patterns = self._global_learning_store.get_patterns_for_auto_apply(
                trust_threshold=trust_threshold,
            )

            if high_trust_patterns:
                self._logger.debug(
                    "auto_apply.patterns_found",
                    count=len(high_trust_patterns),
                    threshold=trust_threshold,
                )
                return True

            return False

        except Exception as e:
            self._logger.warning(
                "auto_apply.check_failed",
                error=str(e),
                trust_threshold=trust_threshold,
                exc_info=True,
            )
            return False

    async def _decide_with_judgment(
        self,
        sheet_num: int,
        validation_result: SheetValidationResult,
        execution_history: Sequence[ExecutionResult],
        normal_attempts: int,
        completion_attempts: int,
        grounding_context: GroundingDecisionContext | None = None,
    ) -> tuple[SheetExecutionMode, str, list[str] | None]:
        """Decide next action using Recursive Light judgment if available.

        Consults the judgment_client (Recursive Light) for TDF-aligned decisions.
        Falls back to local _decide_next_action() if no judgment client is configured.

        Args:
            sheet_num: Current sheet number.
            validation_result: Results from validation engine with confidence scores.
            execution_history: List of ExecutionResults from previous attempts.
            normal_attempts: Number of retry attempts already made.
            completion_attempts: Number of completion mode attempts already made.
            grounding_context: Optional context from grounding hooks.

        Returns:
            Tuple of (SheetExecutionMode, reason string, optional prompt_modifications).
        """
        if self.judgment_client is None:
            mode, reason, semantic_hints = self._decide_next_action(
                validation_result, normal_attempts, completion_attempts, grounding_context
            )
            return mode, reason, semantic_hints

        query = self._build_judgment_query(
            sheet_num=sheet_num,
            validation_result=validation_result,
            execution_history=execution_history,
            normal_attempts=normal_attempts,
        )

        try:
            response = await self.judgment_client.get_judgment(query)

            if response.patterns_learned:
                for pattern in response.patterns_learned:
                    self.console.print(
                        f"[dim]Sheet {sheet_num}: Pattern learned: {pattern}[/dim]"
                    )

            mode = self._map_judgment_to_mode(response, completion_attempts)
            reason = f"RL judgment ({response.confidence:.2f}): {response.reasoning}"

            return mode, reason, response.prompt_modifications

        except Exception as e:
            self._logger.warning(
                "judgment.fallback",
                sheet_num=sheet_num,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            self.console.print(
                f"[yellow]Sheet {sheet_num}: Judgment client error: {e}, "
                f"falling back to local decision[/yellow]"
            )
            mode, reason, semantic_hints = self._decide_next_action(
                validation_result, normal_attempts, completion_attempts, grounding_context
            )
            return mode, reason + " (judgment fallback)", semantic_hints

    def _build_judgment_query(
        self,
        sheet_num: int,
        validation_result: SheetValidationResult,
        execution_history: Sequence[ExecutionResult],
        normal_attempts: int,
    ) -> JudgmentQuery:
        """Build a JudgmentQuery from current execution state.

        Args:
            sheet_num: Current sheet number.
            validation_result: Validation results with confidence.
            execution_history: Previous execution results for this sheet.
            normal_attempts: Number of retry attempts made.

        Returns:
            JudgmentQuery populated with current state.
        """
        error_patterns: list[str] = []
        for result in execution_history:
            if not result.success and result.stderr:
                first_line = result.stderr.split("\n")[0][:100]
                if first_line and first_line not in error_patterns:
                    error_patterns.append(first_line)

        history_dicts: list[dict[str, Any]] = []
        for result in execution_history:
            history_dicts.append({
                "success": result.success,
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "has_stdout": bool(result.stdout),
                "has_stderr": bool(result.stderr),
            })

        return JudgmentQuery(
            job_id=self.config.name,
            sheet_num=sheet_num,
            validation_results=validation_result.to_dict_list(),
            execution_history=history_dicts,
            error_patterns=error_patterns,
            retry_count=normal_attempts,
            confidence=validation_result.aggregate_confidence,
        )

    def _map_judgment_to_mode(
        self,
        response: JudgmentResponse,
        completion_attempts: int,
    ) -> SheetExecutionMode:
        """Map JudgmentResponse.recommended_action to SheetExecutionMode.

        Args:
            response: JudgmentResponse from Recursive Light.
            completion_attempts: Number of completion attempts already made.

        Returns:
            SheetExecutionMode corresponding to the recommended action.

        Raises:
            FatalError: If action is 'abort'.
        """
        action = response.recommended_action

        # Handle abort (raises) and completion (conditional) separately
        if action == "abort":
            raise FatalError(
                f"RL judgment recommends abort: {response.reasoning}"
            )
        if action == "completion":
            max_completion = self.config.retry.max_completion_attempts
            if completion_attempts < max_completion:
                return SheetExecutionMode.COMPLETION
            return SheetExecutionMode.RETRY

        _ACTION_TO_MODE: dict[str, SheetExecutionMode] = {
            "proceed": SheetExecutionMode.NORMAL,
            "retry": SheetExecutionMode.RETRY,
            "escalate": SheetExecutionMode.ESCALATE,
        }
        return _ACTION_TO_MODE.get(action, SheetExecutionMode.RETRY)

    async def _check_proactive_checkpoint(
        self,
        state: CheckpointState,
        sheet_num: int,
        prompt: str,
        retry_count: int,
    ) -> CheckpointResponse | None:
        """Check if a proactive checkpoint is needed before sheet execution.

        v21 Evolution: Proactive Checkpoint System - enables pre-execution approval
        for configurable triggers.

        Args:
            state: Current job state.
            sheet_num: Sheet number about to execute.
            prompt: The prompt that will be used.
            retry_count: Number of retry attempts already made.

        Returns:
            CheckpointResponse if checkpoint was triggered and user responded,
            None if no checkpoint needed or checkpoints disabled.
        """
        if not self.config.checkpoints.enabled:
            return None

        if self.checkpoint_handler is None:
            if self.config.checkpoints.triggers:
                from mozart.execution.escalation import ConsoleCheckpointHandler
                self.checkpoint_handler = ConsoleCheckpointHandler()
            else:
                return None

        triggers = [
            CheckpointTrigger(
                name=t.name,
                sheet_nums=t.sheet_nums,
                prompt_contains=t.prompt_contains,
                min_retry_count=t.min_retry_count,
                requires_confirmation=t.requires_confirmation,
                message=t.message,
            )
            for t in self.config.checkpoints.triggers
        ]

        matching_trigger = await self.checkpoint_handler.should_checkpoint(
            sheet_num=sheet_num,
            prompt=prompt,
            retry_count=retry_count,
            triggers=triggers,
        )

        if matching_trigger is None:
            return None

        self._logger.info(
            "checkpoint.triggered",
            sheet_num=sheet_num,
            trigger_name=matching_trigger.name,
            requires_confirmation=matching_trigger.requires_confirmation,
        )

        self.console.print(
            f"[yellow]Sheet {sheet_num}: Checkpoint triggered - {matching_trigger.name}[/yellow]"
        )

        sheet_state = state.sheets.get(sheet_num)
        previous_errors: list[str] = []
        if sheet_state and sheet_state.error_message:
            previous_errors = [sheet_state.error_message]

        context = CheckpointContext(
            job_id=state.job_id,
            sheet_num=sheet_num,
            prompt=prompt,
            trigger=matching_trigger,
            retry_count=retry_count,
            previous_errors=previous_errors,
        )

        response = await self.checkpoint_handler.checkpoint(context)

        self._logger.info(
            "checkpoint.response",
            sheet_num=sheet_num,
            action=response.action,
            guidance=response.guidance,
        )

        return response

    async def _handle_escalation(
        self,
        state: CheckpointState,
        sheet_num: int,
        validation_result: SheetValidationResult,
        current_prompt: str,
        error_history: list[str],
        normal_attempts: int,
    ) -> EscalationResponse:
        """Handle escalation for low-confidence sheet decisions.

        Builds escalation context and invokes the escalation handler
        to get a decision on how to proceed.

        Args:
            state: Current job state.
            sheet_num: Sheet number being processed.
            validation_result: Validation results from the sheet.
            current_prompt: The prompt that was used for execution.
            error_history: List of previous error messages.
            normal_attempts: Number of retry attempts already made.

        Returns:
            EscalationResponse with the action to take.

        Raises:
            FatalError: If no escalation handler is configured.
        """
        if self.escalation_handler is None:
            raise FatalError(
                f"Sheet {sheet_num}: Escalation requested but no handler configured"
            )

        sheet_state = state.sheets.get(sheet_num)
        if sheet_state is None:
            raise FatalError(f"Sheet {sheet_num}: No sheet state found for escalation")

        # v15 Evolution: Lookup similar past escalations
        historical_suggestions: list[HistoricalSuggestion] = []
        if self._global_learning_store is not None:
            try:
                similar = self._global_learning_store.get_similar_escalation(
                    confidence=validation_result.aggregate_confidence,
                    validation_pass_rate=validation_result.pass_percentage,
                    limit=3,
                )
                if similar:
                    self.console.print(
                        f"[dim]Found {len(similar)} similar past escalation(s)[/dim]"
                    )
                    historical_suggestions = [
                        HistoricalSuggestion(
                            action=past.action,
                            outcome=past.outcome_after_action,
                            confidence=past.confidence,
                            validation_pass_rate=past.validation_pass_rate,
                            guidance=past.guidance,
                        )
                        for past in similar
                    ]
            except Exception as e:
                self._logger.warning(
                    "escalation.similar_lookup_failed",
                    sheet_num=sheet_num,
                    error=str(e),
                    exc_info=True,
                )

        context = EscalationContext(
            job_id=state.job_id,
            sheet_num=sheet_num,
            validation_results=validation_result.to_dict_list(),
            confidence=validation_result.aggregate_confidence,
            retry_count=normal_attempts,
            error_history=error_history,
            prompt_used=current_prompt,
            output_summary=sheet_state.error_message or "",
            historical_suggestions=historical_suggestions,
        )

        self.console.print(
            f"[yellow]Sheet {sheet_num}: Escalating due to low confidence "
            f"({context.confidence:.1%})[/yellow]"
        )

        response = await self.escalation_handler.escalate(context)

        self.console.print(
            f"[blue]Sheet {sheet_num}: Escalation response: {response.action}[/blue]"
        )
        if response.guidance:
            self.console.print(f"[dim]Guidance: {response.guidance}[/dim]")

        # v11 Evolution: Record escalation decision for learning
        escalation_record_id: str | None = None
        if self._global_learning_store is not None:
            try:
                escalation_record_id = self._global_learning_store.record_escalation_decision(
                    job_id=state.job_id,
                    sheet_num=sheet_num,
                    confidence=context.confidence,
                    action=response.action,
                    validation_pass_rate=validation_result.pass_percentage,
                    retry_count=normal_attempts,
                    guidance=response.guidance,
                    model=self.config.backend.model,
                )
                self._logger.info(
                    "escalation.decision_recorded",
                    sheet_num=sheet_num,
                    action=response.action,
                    record_id=escalation_record_id,
                )
                sheet_state.outcome_data = sheet_state.outcome_data or {}
                sheet_state.outcome_data["escalation_record_id"] = escalation_record_id
            except (sqlite3.Error, OSError) as e:
                self._logger.warning(
                    "escalation.record_failed",
                    sheet_num=sheet_num,
                    error=str(e),
                    exc_info=True,
                )

        return response


# Re-export for convenience
__all__ = [
    "SheetExecutionMixin",
]
