"""Error recovery and retry mixin for JobRunner.

Contains methods for error handling, retry logic, rate limit management,
self-healing coordination, and recovery outcome tracking. This module
centralizes all recovery-related concerns to keep the sheet execution
logic clean and focused.

Architecture:
    RecoveryMixin is mixed with other mixins to compose the full JobRunner.
    It expects the following attributes from base.py:

    Required attributes:
        - config: JobConfig
        - backend: Backend
        - state_backend: StateBackend
        - console: Console
        - _logger: MozartLogger
        - _global_learning_store: GlobalLearningStore | None
        - _healing_coordinator: SelfHealingCoordinator | None
        - error_classifier: ErrorClassifier

    Provides methods:
        - _try_self_healing(): Attempt self-healing when retries exhausted
        - _handle_rate_limit(): Handle rate limit with wait and health check
        - _classify_execution(): Classify execution errors with root cause
        - _classify_error(): Backward compatible error classification
        - _get_retry_delay(): Calculate retry delay with exponential backoff
        - _poll_broadcast_discoveries(): Poll for pattern discoveries during waits
        - _record_error_recovery(): Record recovery outcome to global store
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from mozart.healing.coordinator import HealingReport, SelfHealingCoordinator
    from mozart.learning.global_store import GlobalLearningStore

from mozart.backends.base import Backend, ExecutionResult
from mozart.core.checkpoint import CheckpointState
from mozart.core.config import JobConfig
from mozart.core.errors import ClassificationResult, ClassifiedError, ErrorClassifier
from mozart.core.logging import MozartLogger
from mozart.state.base import StateBackend

from .models import FatalError


class RecoveryMixin:
    """Mixin providing error recovery methods for JobRunner.

    This mixin centralizes error handling, retry logic, and recovery
    coordination. It integrates with:

    - Self-healing coordinator for automatic remediation
    - Global learning store for cross-workspace rate limit coordination
    - Adaptive retry strategy for intelligent delay calculation
    - Error classifier for root cause analysis

    Recovery Flow:
        1. Error occurs during sheet execution
        2. _classify_execution() identifies root cause
        3. If rate limit: _handle_rate_limit() waits and health checks
        4. If retries exhausted: _try_self_healing() attempts remediation
        5. Recovery outcomes recorded to global store for learning

    Cross-Workspace Coordination (Evolution #8):
        Rate limit events are recorded to the global learning store so
        other parallel jobs can honor rate limits without redundant waits.

    Key Attributes (from base.py):
        _healing_coordinator: Provides automatic diagnosis and remediation
        _global_learning_store: Enables cross-workspace learning
        error_classifier: Provides multi-error root cause analysis
    """

    # Type hints for attributes provided by base.py
    config: JobConfig
    backend: Backend
    state_backend: StateBackend
    console: Console
    _logger: MozartLogger
    _global_learning_store: GlobalLearningStore | None
    _healing_coordinator: SelfHealingCoordinator | None
    error_classifier: ErrorClassifier
    rate_limit_callback: Callable[[str, float, str, int], Any] | None

    async def _interruptible_sleep(self, seconds: float) -> None: ...

    def _get_effective_model(self) -> str | None:
        """Resolve the effective model name from backend config.

        Claude CLI uses ``cli_model``, other backends use ``model``.
        Returns None when no model is configured.
        """
        if self.config.backend.type == "claude_cli":
            return self.config.backend.cli_model
        return self.config.backend.model

    # ─────────────────────────────────────────────────────────────────────
    # Self-Healing Coordination
    # ─────────────────────────────────────────────────────────────────────

    async def _try_self_healing(
        self,
        result: ExecutionResult,
        error: ClassifiedError,
        config_path: Path | None,
        sheet_num: int,
        retry_count: int,
        max_retries: int,
    ) -> HealingReport | None:
        """Attempt self-healing when retries are exhausted.

        Creates an ErrorContext from the execution result and runs the
        healing coordinator to diagnose and potentially fix the issue.

        v11 Evolution: Self-Healing - provides automatic diagnosis and
        remediation when normal retry logic is exhausted. The healing
        coordinator evaluates the error context and applies matching
        remedies from its registry.

        Args:
            result: The failed execution result.
            error: The classified error from the failure.
            config_path: Path to the config file (if available).
            sheet_num: Current sheet number.
            retry_count: Number of retries attempted.
            max_retries: Maximum retries configured.

        Returns:
            HealingReport if healing was attempted, None if healing is disabled.
            The report contains:
            - any_remedies_applied: Whether any fixes were applied
            - actions_taken: List of (remedy_name, description) tuples
            - issues_remaining: List of unresolved issues
            - should_retry: Whether to retry after healing
        """
        if self._healing_coordinator is None:
            return None

        from mozart.healing.context import ErrorContext

        self._logger.info(
            "sheet.healing_attempt",
            sheet_num=sheet_num,
            error_code=error.error_code.value,
            retry_count=retry_count,
        )

        self.console.print(
            "\n[yellow]Self-healing: Attempting to diagnose and fix error...[/yellow]"
        )

        # Create error context from execution result
        # This provides the healing coordinator with all necessary context
        context = ErrorContext.from_execution_result(
            result=result,
            config=self.config,
            config_path=config_path,
            sheet_number=sheet_num,
            error_code=error.error_code.value,
            error_message=error.message,
            error_category=error.category.value,
            retry_count=retry_count,
            max_retries=max_retries,
        )

        # Run healing - the coordinator will:
        # 1. Match applicable remedies from registry
        # 2. Apply automatic remedies without prompting
        # 3. Prompt for suggested remedies (unless auto_confirm)
        # 4. Return diagnostic guidance for manual-fix issues
        report = await self._healing_coordinator.heal(context)

        # Log the healing outcome
        if report.any_remedies_applied:
            self._logger.info(
                "sheet.healing_success",
                sheet_num=sheet_num,
                remedies_applied=[name for name, _ in report.actions_taken],
            )
        else:
            self._logger.warning(
                "sheet.healing_failed",
                sheet_num=sheet_num,
                issues_remaining=report.issues_remaining,
            )

        return report

    # ─────────────────────────────────────────────────────────────────────
    # Rate Limit Handling
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _infer_active_sheet_num(state: CheckpointState) -> int:
        """Derive the current sheet number from checkpoint state.

        Returns the sheet currently being attempted, which is one past
        the last completed sheet (or 1 if none have completed).
        """
        return state.last_completed_sheet + 1

    async def _handle_rate_limit(
        self,
        state: CheckpointState,
        error_code: str = "E101",
        suggested_wait_seconds: float | None = None,
    ) -> None:
        """Handle rate limit or quota exhaustion by waiting and health checking.

        This method orchestrates the complete rate limit handling flow:
        1. Determine wait duration and update state counters
        2. Enforce max-wait limits and log/display the event
        3. Record to global store for cross-workspace coordination
        4. Poll for pattern broadcasts during the wait
        5. Wait for the calculated duration
        6. Health check before resuming

        Args:
            state: Current job state.
            error_code: The error code that triggered the rate limit.
                - E101: Generic rate limit
                - E104: Token quota exhaustion (with parsed reset time)
            suggested_wait_seconds: Dynamic wait time from API response.
                If None, uses config.rate_limit.wait_minutes.

        Raises:
            FatalError: If max waits exceeded or health check fails.
        """
        is_quota = error_code == "E104"
        wait_seconds = self._resolve_wait_duration(suggested_wait_seconds)

        # Update counters and persist
        await self._increment_wait_counter(state, is_quota)

        # Log, display, and enforce max-wait limit (may raise FatalError)
        self._log_rate_limit_event(state, is_quota, wait_seconds)

        # Cross-workspace coordination
        await self._record_rate_limit_to_global_store(
            state=state, error_code=error_code, wait_seconds=wait_seconds,
        )

        # Daemon rate coordinator notification (Phase 3 wiring prereq P025)
        if self.rate_limit_callback is not None:
            try:
                backend_type = self.backend.name
                await self.rate_limit_callback(
                    backend_type, wait_seconds, state.job_id,
                    self._infer_active_sheet_num(state),
                )
            except (OSError, ConnectionError, TimeoutError) as e:
                self._logger.warning(
                    "rate_limit.daemon_callback_failed",
                    job_id=state.job_id,
                    error=str(e),
                )

        # Poll for pattern discoveries during the wait window
        await self._poll_broadcast_discoveries(
            job_id=state.job_id,
            sheet_num=self._infer_active_sheet_num(state),
        )

        # Wait and health-check (interruptible so shutdown isn't delayed)
        await self._interruptible_sleep(wait_seconds)
        await self._health_check_after_wait(state, is_quota)

    def _resolve_wait_duration(self, suggested_wait_seconds: float | None) -> float:
        """Determine how long to wait for a rate limit.

        Uses the API-suggested wait time when available, otherwise falls
        back to config.rate_limit.wait_minutes.

        Args:
            suggested_wait_seconds: Dynamic wait from API response, or None.

        Returns:
            Wait duration in seconds.
        """
        if suggested_wait_seconds is not None and suggested_wait_seconds > 0:
            return suggested_wait_seconds
        return self.config.rate_limit.wait_minutes * 60

    async def _increment_wait_counter(
        self, state: CheckpointState, is_quota: bool,
    ) -> None:
        """Increment the appropriate wait counter and persist state.

        Quota exhaustion and regular rate limits are tracked separately
        because they have different max-wait policies.

        Args:
            state: Current job state.
            is_quota: True for quota exhaustion (E104), False for rate limit (E101).
        """
        if is_quota:
            state.quota_waits += 1
        else:
            state.rate_limit_waits += 1
        await self.state_backend.save(state)

    def _log_rate_limit_event(
        self, state: CheckpointState, is_quota: bool, wait_seconds: float,
    ) -> None:
        """Log and display rate limit event, enforcing max-wait limits.

        Both quota exhaustion and regular rate limits are bounded by
        config.rate_limit.max_quota_waits and config.rate_limit.max_waits
        respectively.

        Args:
            state: Current job state.
            is_quota: True for quota exhaustion (E104).
            wait_seconds: Duration of the wait.

        Raises:
            FatalError: If max waits exceeded (quota or regular).
        """
        wait_minutes = wait_seconds / 60

        if is_quota:
            max_quota_waits = self.config.rate_limit.max_quota_waits
            self._logger.warning(
                "quota_exhausted.detected",
                job_id=state.job_id,
                wait_count=state.quota_waits,
                max_quota_waits=max_quota_waits,
                wait_minutes=wait_minutes,
                wait_seconds=wait_seconds,
            )
            if state.quota_waits >= max_quota_waits:
                self._logger.error(
                    "quota_exhausted.max_waits_exceeded",
                    job_id=state.job_id,
                    wait_count=state.quota_waits,
                    max_quota_waits=max_quota_waits,
                )
                raise FatalError(
                    f"Exceeded maximum quota exhaustion waits ({max_quota_waits})"
                )
            self.console.print(
                f"[yellow]Token quota exhausted. Waiting {wait_minutes:.1f} minutes until reset... "
                f"(quota wait {state.quota_waits}/{max_quota_waits})[/yellow]"
            )
        else:
            self._logger.warning(
                "rate_limit.detected",
                job_id=state.job_id,
                wait_count=state.rate_limit_waits,
                max_waits=self.config.rate_limit.max_waits,
                wait_minutes=wait_minutes,
            )
            if state.rate_limit_waits >= self.config.rate_limit.max_waits:
                self._logger.error(
                    "rate_limit.exhausted",
                    job_id=state.job_id,
                    wait_count=state.rate_limit_waits,
                    max_waits=self.config.rate_limit.max_waits,
                )
                raise FatalError(
                    f"Exceeded maximum rate limit waits ({self.config.rate_limit.max_waits})"
                )
            self.console.print(
                f"[yellow]Rate limited. Waiting {wait_minutes:.0f} minutes... "
                f"(wait {state.rate_limit_waits}/{self.config.rate_limit.max_waits})[/yellow]"
            )

    async def _health_check_after_wait(
        self, state: CheckpointState, is_quota: bool,
    ) -> None:
        """Run health check after rate limit wait and log resumption.

        After quota exhaustion, uses the lightweight availability_check()
        (no API calls) with retries, since sending a prompt would likely
        fail again. After regular rate limits, uses full health_check().

        Args:
            state: Current job state.
            is_quota: True for quota exhaustion (E104).

        Raises:
            FatalError: If checks fail after retries (quota) or
                immediately (rate limit).
        """
        event_type = "quota_exhausted" if is_quota else "rate_limit"
        self.console.print("[blue]Running health check...[/blue]")

        if is_quota:
            # After quota exhaustion, use lightweight check that doesn't
            # consume quota. Retry with backoff instead of immediately dying.
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                if await self.backend.availability_check():
                    break
                self._logger.warning(
                    "quota_exhausted.availability_check_failed",
                    job_id=state.job_id,
                    attempt=attempt,
                    max_retries=max_retries,
                )
                if attempt == max_retries:
                    raise FatalError(
                        f"Backend unavailable after {max_retries} availability "
                        f"checks following {event_type} wait"
                    )
                retry_wait = 60.0 * attempt  # 1min, 2min, 3min
                self.console.print(
                    f"[yellow]Backend not yet available. "
                    f"Retrying in {attempt} minute(s)... "
                    f"({attempt}/{max_retries})[/yellow]"
                )
                await self._interruptible_sleep(retry_wait)
        else:
            if not await self.backend.health_check():
                self._logger.error(
                    f"{event_type}.health_check_failed",
                    job_id=state.job_id,
                )
                raise FatalError(
                    f"Backend health check failed after {event_type} wait"
                )

        wait_count = state.quota_waits if is_quota else state.rate_limit_waits
        self._logger.info(
            f"{event_type}.resumed",
            job_id=state.job_id,
            wait_count=wait_count,
        )
        self.console.print("[green]Health check passed, resuming...[/green]")

    async def _record_rate_limit_to_global_store(
        self,
        state: CheckpointState,
        error_code: str,
        wait_seconds: float,
    ) -> None:
        """Record rate limit event to global store for cross-workspace coordination.

        Evolution #8: Cross-Workspace Circuit Breaker - other jobs can query
        the global store to avoid hitting the same rate limit.

        Quota exhaustion (E104) vs temporary rate limit (E101) is already
        distinguished by error_code — callers should pass the appropriate code.

        Args:
            state: Current job state.
            error_code: The error code (E101 = rate limit, E104 = quota exhaustion).
            wait_seconds: Duration of the wait.
        """
        cross_ws_enabled = self.config.circuit_breaker.cross_workspace_coordination
        if not cross_ws_enabled or self._global_learning_store is None:
            return

        effective_model = self._get_effective_model()
        if effective_model is None:
            self._logger.debug(
                "rate_limit.cross_workspace_skip_record",
                job_id=state.job_id,
                reason="no model specified for backend",
            )
            return

        try:
            self._global_learning_store.record_rate_limit_event(
                error_code=error_code,
                duration_seconds=wait_seconds,
                job_id=state.job_id,
                model=effective_model,
            )
            self._logger.info(
                "rate_limit.cross_workspace_recorded",
                job_id=state.job_id,
                error_code=error_code,
                duration_seconds=wait_seconds,
            )
        except (sqlite3.Error, OSError) as e:
            # Global store failure shouldn't block rate limit handling
            self._logger.warning(
                "rate_limit.cross_workspace_record_failed",
                job_id=state.job_id,
                error=str(e),
            )

    # ─────────────────────────────────────────────────────────────────────
    # Error Classification
    # ─────────────────────────────────────────────────────────────────────

    def _classify_execution(self, result: ExecutionResult) -> ClassificationResult:
        """Classify execution errors using multi-error root cause analysis.

        Uses classify_execution() to detect all errors and identify the root
        cause. This is essential for making informed retry decisions and
        for learning from execution failures.

        Evolution v6: Multi-Error Root Cause Analysis - the classifier now
        identifies all errors in an execution and determines which is the
        root cause vs. cascading symptoms.

        Args:
            result: Execution result with stdout, stderr, exit_code.

        Returns:
            ClassificationResult containing:
            - primary: The root cause error (ClassifiedError)
            - secondary: List of secondary/symptom errors
            - confidence: Confidence in root cause identification
        """
        output_format = getattr(self.config.backend, "output_format", None)
        classification = self.error_classifier.classify_execution(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            exit_signal=result.exit_signal,
            exit_reason=result.exit_reason,
            output_format=output_format,
        )

        # Log secondary errors for debugging multi-error scenarios
        if classification.secondary:
            self._logger.debug(
                "runner.secondary_errors_detected",
                primary_code=classification.primary.error_code.value,
                secondary_codes=[e.error_code.value for e in classification.secondary],
                confidence=round(classification.confidence, 3),
            )

        return classification

    def _classify_error(self, result: ExecutionResult) -> ClassifiedError:
        """Classify execution error (backward compatible wrapper).

        This method is maintained for backward compatibility. New code should
        use _classify_execution() to get full root cause analysis.

        Args:
            result: Execution result with error details.

        Returns:
            ClassifiedError (primary error from classification result).
        """
        return self._classify_execution(result).primary

    # ─────────────────────────────────────────────────────────────────────
    # Retry Delay Calculation
    # ─────────────────────────────────────────────────────────────────────

    def _get_retry_delay(self, attempt: int) -> float:
        """Calculate retry delay with exponential backoff and jitter.

        Implements exponential backoff with configurable jitter to prevent
        thundering herd problems when multiple jobs retry simultaneously.

        The formula is: delay = min(base * (exp ^ (attempt-1)), max_delay)
        With jitter: delay = delay * (0.5 + random())

        Args:
            attempt: Current attempt number (1-indexed).

        Returns:
            Delay in seconds.

        Example:
            With base=5, exp=2, max_delay=300:
            - Attempt 1: 5s (plus jitter)
            - Attempt 2: 10s (plus jitter)
            - Attempt 3: 20s (plus jitter)
            - Attempt 4: 40s (plus jitter)
            - ...capped at 300s
        """
        base = self.config.retry.base_delay_seconds
        exp = self.config.retry.exponential_base
        max_delay = self.config.retry.max_delay_seconds

        # Exponential backoff with cap
        delay = min(base * (exp ** (attempt - 1)), max_delay)

        # Add jitter if configured (50-100% of calculated delay)
        if self.config.retry.jitter:
            delay *= 0.5 + random.random()

        return delay

    # ─────────────────────────────────────────────────────────────────────
    # Pattern Broadcast Polling
    # ─────────────────────────────────────────────────────────────────────

    async def _poll_broadcast_discoveries(
        self,
        job_id: str,
        sheet_num: int,
    ) -> None:
        """Poll for pattern discoveries from other concurrent jobs.

        v16 Evolution: Active Broadcast Polling - enables jobs to receive
        real-time pattern discoveries from other jobs during retry waits.
        This activates the v14 broadcasting infrastructure.

        The polling is read-only - discovered patterns are logged but not
        automatically applied. Pattern application happens via the normal
        _query_relevant_patterns() flow.

        This is particularly useful during rate limit waits, which are
        typically longer and provide a good opportunity to collect
        new learning signals.

        Args:
            job_id: Current job ID (to exclude own discoveries).
            sheet_num: Current sheet number (for logging context).
        """
        if self._global_learning_store is None:
            return

        try:
            discoveries = self._global_learning_store.check_recent_pattern_discoveries(
                exclude_job_id=job_id,
                min_effectiveness=0.5,  # Only consider reasonably effective patterns
                limit=10,
            )

            if discoveries:
                # Log discovered patterns for observability
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

                # Console output for visibility
                self.console.print(
                    f"[dim]Sheet {sheet_num}: Received {len(discoveries)} pattern "
                    f"broadcast(s) from other jobs[/dim]"
                )

                # Log individual patterns at debug level
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
            # Polling failure should not block retry - log and continue
            self._logger.warning(
                "broadcast.polling_failed",
                sheet_num=sheet_num,
                error=str(e),
            )

    # ─────────────────────────────────────────────────────────────────────
    # Recovery Outcome Recording
    # ─────────────────────────────────────────────────────────────────────

    async def _record_error_recovery(
        self,
        error_code: str,
        suggested_wait: float,
        actual_wait: float,
        recovery_success: bool,
    ) -> None:
        """Record error recovery outcome to global learning store.

        Evolution #3: Learned Wait Time - records recovery outcomes so
        future jobs can learn optimal wait times for different error types.

        Args:
            error_code: The error code that triggered recovery.
            suggested_wait: The suggested wait time (from config or API).
            actual_wait: The actual wait time used.
            recovery_success: Whether recovery was successful (retry worked).
        """
        if self._global_learning_store is None:
            return

        try:
            self._global_learning_store.record_error_recovery(
                error_code=error_code,
                suggested_wait=suggested_wait,
                actual_wait=actual_wait,
                recovery_success=recovery_success,
            )
            self._logger.debug(
                "learning.recovery_recorded",
                error_code=error_code,
                actual_wait=actual_wait,
                recovery_success=recovery_success,
            )
        except (sqlite3.Error, OSError) as e:
            # Recovery recording failure shouldn't block execution
            self._logger.warning(
                "learning.recovery_record_failed",
                error_code=error_code,
                error=str(e),
            )

    async def _check_cross_workspace_rate_limit(
        self,
        state: CheckpointState,
    ) -> tuple[bool, float | None]:
        """Check if another job has already hit a rate limit we should honor.

        Evolution #8: Cross-Workspace Circuit Breaker - before executing,
        check if another job has recorded a recent rate limit event for
        the same model. If so, honor that rate limit instead of triggering
        another one.

        Args:
            state: Current job state.

        Returns:
            Tuple of (is_rate_limited, wait_time_seconds).
            If is_rate_limited is True, caller should wait for wait_time_seconds.
        """
        if self._global_learning_store is None:
            return False, None

        if not self.config.circuit_breaker.honor_other_jobs_rate_limits:
            return False, None

        effective_model = self._get_effective_model()
        if effective_model is None:
            return False, None

        try:
            is_limited, wait_time = self._global_learning_store.is_rate_limited(
                model=effective_model,
            )
            if is_limited:
                self._logger.info(
                    "rate_limit.cross_workspace_honored",
                    job_id=state.job_id,
                    model=effective_model,
                    wait_seconds=wait_time,
                )
            return is_limited, wait_time
        except (sqlite3.Error, OSError) as e:
            self._logger.warning(
                "rate_limit.cross_workspace_check_failed",
                job_id=state.job_id,
                error=str(e),
            )
            return False, None
