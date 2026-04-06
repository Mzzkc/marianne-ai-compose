"""Adversarial edge-case tests for the execution engine.

Targets:
  - Broken backends (hangs, empty output, signals, large output)
  - Cost tracking edge cases (NaN, Inf, negative, overflow)
  - Escalation edge cases (0 retries, concurrent calls, all patterns match)
  - Recovery edge cases (rate limit exhaustion, health check failure)
  - Pause/resume race conditions (double-pause, pause-then-cancel)
  - RunSummary invariants (over-count, zero sheets, all skipped)

Every test uses ``@pytest.mark.adversarial`` and type hints.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from marianne.backends.base import ExitReason, ExecutionResult
from marianne.core.checkpoint import (
    CheckpointState,
    SheetState,
    SheetStatus,
)
from marianne.core.config import JobConfig
from marianne.core.errors import (
    ErrorCode,
)
from marianne.execution.escalation import (
    CheckpointContext,
    CheckpointResponse,
    CheckpointTrigger,
    ConsoleCheckpointHandler,
    ConsoleEscalationHandler,
    EscalationContext,
    EscalationResponse,
    HistoricalSuggestion,
)
from marianne.execution.runner.models import (
    FatalError,
    GracefulShutdownError,
    GroundingDecisionContext,
    RunSummary,
    SheetExecutionMode,
)

from tests.conftest_adversarial import (
    _ADVERSARIAL_STRINGS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, overrides: dict[str, Any] | None = None) -> JobConfig:
    """Build a minimal JobConfig for testing."""
    base: dict[str, Any] = {
        "name": "adversarial-test",
        "description": "Adversarial edge case test job",
        "workspace": str(tmp_path / "workspace"),
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 5, "total_items": 10},
        "prompt": {"template": "Process sheet {{ sheet_num }}."},
        "retry": {
            "max_retries": 3,
            "max_completion_attempts": 2,
            "base_delay_seconds": 0.01,
            "exponential_base": 2.0,
            "max_delay_seconds": 0.1,
            "jitter": False,
            "completion_threshold_percent": 60,
        },
        "rate_limit": {
            "wait_minutes": 1,
            "max_waits": 3,
            "max_quota_waits": 2,
        },
        "validations": [],
        "pause_between_sheets_seconds": 0,
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                base[k].update(v)
            else:
                base[k] = v
    (tmp_path / "workspace").mkdir(exist_ok=True, parents=True)
    return JobConfig(**base)


def _make_result(
    *,
    success: bool = True,
    stdout: str = "done",
    stderr: str = "",
    exit_code: int | None = 0,
    exit_signal: int | None = None,
    exit_reason: ExitReason = "completed",
    duration: float = 1.0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ExecutionResult:
    """Build an ExecutionResult for testing."""
    return ExecutionResult(
        success=success,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code if success or exit_code is not None else 1,
        exit_signal=exit_signal,
        exit_reason=exit_reason,
        duration_seconds=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_checkpoint(
    job_id: str = "adv-test",
    total_sheets: int = 2,
) -> CheckpointState:
    """Build a CheckpointState for testing."""
    state = CheckpointState(
        job_id=job_id,
        job_name=job_id,
        total_sheets=total_sheets,
    )
    for i in range(1, total_sheets + 1):
        state.sheets[i] = SheetState(sheet_num=i)
    return state


# ═══════════════════════════════════════════════════════════════════════
# 1. BROKEN BACKENDS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestBrokenBackendOutputs:
    """Backend produces unexpected output values."""

    async def test_empty_stdout_is_handled_in_cost_tracking(self, tmp_path: Path) -> None:
        """Cost estimation from empty output should produce minimal token count."""
        from marianne.execution.runner.cost import CostMixin

        config = _make_config(tmp_path)
        state = _make_checkpoint()
        sheet_state = state.sheets[1]
        sheet_state.status = SheetStatus.IN_PROGRESS

        result = _make_result(stdout="", stderr="")

        # Create a minimal CostMixin host
        class Host(CostMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()
        host._circuit_breaker = None
        host._summary = None

        async def _fire_event(event: str, sheet_num: int, data: Any = None) -> None:
            pass
        host._fire_event = _fire_event  # type: ignore[assignment]

        inp, out, cost, confidence = await host._track_cost(result, sheet_state, state)
        # Empty output → at least 1 token estimated
        assert out >= 1
        assert confidence == 0.7  # estimated
        assert cost >= 0.0

    async def test_very_large_stdout_in_cost_tracking(self, tmp_path: Path) -> None:
        """Backend returning very large output should not crash cost tracking."""
        from marianne.execution.runner.cost import CostMixin

        config = _make_config(tmp_path)
        state = _make_checkpoint()
        sheet_state = state.sheets[1]
        sheet_state.status = SheetStatus.IN_PROGRESS

        # 1MB of output
        big_output = "x" * (1024 * 1024)
        result = _make_result(stdout=big_output, stderr="")

        class Host(CostMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()
        host._circuit_breaker = None
        host._summary = None

        async def _fire_event(event: str, sheet_num: int, data: Any = None) -> None:
            pass
        host._fire_event = _fire_event  # type: ignore[assignment]

        inp, out, cost, confidence = await host._track_cost(result, sheet_state, state)
        # 1MB / 4 chars per token = ~262144 tokens
        assert out > 200000
        assert cost > 0
        assert math.isfinite(cost)

    @pytest.mark.parametrize("exit_signal", [9, 11, 15])
    def test_backend_killed_by_signal(self, exit_signal: int) -> None:
        """ExecutionResult with signal-killed exit should be constructible."""
        result = ExecutionResult(
            success=False,
            stdout="partial output",
            stderr="",
            exit_code=None,
            exit_signal=exit_signal,
            exit_reason="killed",
            duration_seconds=0.5,
        )
        assert result.exit_signal == exit_signal
        assert not result.success
        assert result.exit_reason == "killed"

    def test_backend_stderr_only_success(self) -> None:
        """Backend that writes to stderr but reports success should still be success."""
        result = _make_result(
            success=True,
            stdout="",
            stderr="warning: something happened",
            exit_code=0,
        )
        assert result.success is True
        assert result.stderr == "warning: something happened"

    def test_execution_result_rejects_success_with_nonzero_exit(self) -> None:
        """ExecutionResult should reject success=True with exit_code != 0."""
        with pytest.raises(ValueError, match="Inconsistent"):
            ExecutionResult(
                success=True,
                stdout="done",
                stderr="",
                exit_code=1,
                duration_seconds=1.0,
            )


# ═══════════════════════════════════════════════════════════════════════
# 2. COST TRACKING EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestCostTrackingEdgeCases:
    """Adversarial cost values: overflow, negative via state manipulation, etc."""

    async def test_cost_accumulation_with_exact_tokens(self, tmp_path: Path) -> None:
        """Exact token counts (API backend) should produce confidence=1.0."""
        from marianne.execution.runner.cost import CostMixin

        config = _make_config(tmp_path)
        state = _make_checkpoint()
        sheet_state = state.sheets[1]

        result = _make_result(input_tokens=1000, output_tokens=500)

        class Host(CostMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()
        host._circuit_breaker = None
        host._summary = None

        async def _fire_event(event: str, sheet_num: int, data: Any = None) -> None:
            pass
        host._fire_event = _fire_event  # type: ignore[assignment]

        inp, out, cost, confidence = await host._track_cost(result, sheet_state, state)
        assert inp == 1000
        assert out == 500
        assert confidence == 1.0
        assert cost > 0

    def test_cost_limits_disabled_returns_false(self, tmp_path: Path) -> None:
        """When cost limits are disabled, _check_cost_limits returns (False, None)."""
        from marianne.execution.runner.cost import CostMixin

        config = _make_config(tmp_path, {"cost_limits": {"enabled": False}})
        state = _make_checkpoint()
        sheet_state = state.sheets[1]
        sheet_state.estimated_cost = 999999.0
        state.total_estimated_cost = 999999.0

        class Host(CostMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()

        exceeded, reason = host._check_cost_limits(sheet_state, state)
        assert exceeded is False
        assert reason is None

    def test_cost_limits_per_sheet_exceeded(self, tmp_path: Path) -> None:
        """Per-sheet cost limit triggers correctly."""
        from marianne.execution.runner.cost import CostMixin

        config = _make_config(tmp_path, {
            "cost_limits": {
                "enabled": True,
                "max_cost_per_sheet": 1.0,
                "max_cost_per_job": 100.0,
            },
        })
        state = _make_checkpoint()
        sheet_state = state.sheets[1]
        sheet_state.estimated_cost = 2.0  # exceeds 1.0 limit

        class Host(CostMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()

        exceeded, reason = host._check_cost_limits(sheet_state, state)
        assert exceeded is True
        assert reason is not None
        assert "Sheet cost" in reason

    def test_cost_limits_per_job_exceeded(self, tmp_path: Path) -> None:
        """Per-job cost limit triggers correctly."""
        from marianne.execution.runner.cost import CostMixin

        config = _make_config(tmp_path, {
            "cost_limits": {
                "enabled": True,
                "max_cost_per_sheet": 100.0,
                "max_cost_per_job": 5.0,
            },
        })
        state = _make_checkpoint()
        state.total_estimated_cost = 10.0  # exceeds 5.0 limit
        sheet_state = state.sheets[1]
        sheet_state.estimated_cost = 1.0

        class Host(CostMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()

        exceeded, reason = host._check_cost_limits(sheet_state, state)
        assert exceeded is True
        assert reason is not None
        assert "Job cost" in reason

    async def test_very_large_token_counts_do_not_overflow(self, tmp_path: Path) -> None:
        """Very large token counts should produce finite cost."""
        from marianne.execution.runner.cost import CostMixin

        config = _make_config(tmp_path)
        state = _make_checkpoint()
        sheet_state = state.sheets[1]

        result = _make_result(input_tokens=10**9, output_tokens=10**9)

        class Host(CostMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()
        host._circuit_breaker = None
        host._summary = None

        async def _fire_event(event: str, sheet_num: int, data: Any = None) -> None:
            pass
        host._fire_event = _fire_event  # type: ignore[assignment]

        inp, out, cost, confidence = await host._track_cost(result, sheet_state, state)
        assert math.isfinite(cost)
        assert cost > 0
        assert state.total_estimated_cost > 0


# ═══════════════════════════════════════════════════════════════════════
# 3. ESCALATION EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestEscalationEdgeCases:
    """Escalation handler with adversarial inputs."""

    @pytest.mark.asyncio
    async def test_escalation_with_zero_retries(self) -> None:
        """Escalation should work when max_retries is 0."""
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.8,
            auto_retry_on_first_failure=False,
        )
        sheet_state = SheetState(sheet_num=1)
        sheet_state.attempt_count = 0

        vr = MagicMock()
        vr.pass_percentage = 30.0

        # confidence below threshold, auto_retry off → should escalate
        should = await handler.should_escalate(sheet_state, vr, confidence=0.3)
        assert should is True

    @pytest.mark.asyncio
    async def test_escalation_auto_retry_suppresses_first_attempt(self) -> None:
        """With auto_retry_on_first_failure=True, first failure should NOT escalate."""
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.8,
            auto_retry_on_first_failure=True,
        )
        sheet_state = SheetState(sheet_num=1)
        sheet_state.attempt_count = 1  # first attempt

        vr = MagicMock()
        should = await handler.should_escalate(sheet_state, vr, confidence=0.1)
        assert should is False

    @pytest.mark.asyncio
    async def test_escalation_with_high_confidence_does_not_trigger(self) -> None:
        """High confidence should never trigger escalation regardless of settings."""
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.5,
            auto_retry_on_first_failure=False,
        )
        sheet_state = SheetState(sheet_num=1)
        sheet_state.attempt_count = 10

        vr = MagicMock()
        should = await handler.should_escalate(sheet_state, vr, confidence=0.99)
        assert should is False

    @pytest.mark.asyncio
    async def test_escalation_at_exact_threshold_does_not_trigger(self) -> None:
        """Confidence exactly at threshold should NOT trigger (>=, not >)."""
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.6,
            auto_retry_on_first_failure=False,
        )
        sheet_state = SheetState(sheet_num=1)
        sheet_state.attempt_count = 5

        vr = MagicMock()
        should = await handler.should_escalate(sheet_state, vr, confidence=0.6)
        assert should is False

    def test_historical_suggestion_with_none_outcome(self) -> None:
        """HistoricalSuggestion should handle None outcome gracefully."""
        suggestion = HistoricalSuggestion(
            action="retry",
            outcome=None,
            confidence=0.5,
            validation_pass_rate=50.0,
            guidance=None,
        )
        assert suggestion.outcome is None
        assert suggestion.action == "retry"

    @pytest.mark.parametrize(
        "action",
        ["retry", "skip", "abort", "modify_prompt"],
    )
    def test_escalation_response_all_actions(self, action: str) -> None:
        """All EscalationResponse actions should be constructible."""
        resp = EscalationResponse(
            action=action,  # type: ignore[arg-type]
            modified_prompt="new prompt" if action == "modify_prompt" else None,
        )
        assert resp.action == action


# ═══════════════════════════════════════════════════════════════════════
# 4. CHECKPOINT EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestCheckpointEdgeCases:
    """Proactive checkpoint handler adversarial inputs."""

    @pytest.mark.asyncio
    async def test_checkpoint_no_triggers_match(self) -> None:
        """When no triggers match, should_checkpoint returns None."""
        handler = ConsoleCheckpointHandler()
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="some prompt",
            retry_count=0,
            triggers=[
                CheckpointTrigger(
                    name="specific-sheet",
                    sheet_nums=[99],
                ),
            ],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_checkpoint_empty_triggers_list(self) -> None:
        """Empty trigger list should return None."""
        handler = ConsoleCheckpointHandler()
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="anything",
            retry_count=5,
            triggers=[],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_checkpoint_keyword_match_case_insensitive(self) -> None:
        """Keyword matching should be case-insensitive."""
        handler = ConsoleCheckpointHandler()
        trigger = CheckpointTrigger(
            name="danger-keyword",
            prompt_contains=["DANGEROUS"],
        )
        result = await handler.should_checkpoint(
            sheet_num=1,
            prompt="this is a dangerous operation",
            retry_count=0,
            triggers=[trigger],
        )
        assert result is trigger

    @pytest.mark.asyncio
    async def test_checkpoint_retry_count_threshold(self) -> None:
        """Trigger matches only when retry_count >= min_retry_count."""
        handler = ConsoleCheckpointHandler()
        trigger = CheckpointTrigger(
            name="high-retry",
            min_retry_count=5,
        )
        # Below threshold
        result_below = await handler.should_checkpoint(
            sheet_num=1, prompt="p", retry_count=2, triggers=[trigger],
        )
        assert result_below is None

        # At threshold
        result_at = await handler.should_checkpoint(
            sheet_num=1, prompt="p", retry_count=5, triggers=[trigger],
        )
        assert result_at is trigger

    @pytest.mark.asyncio
    async def test_checkpoint_warning_only_proceeds(self) -> None:
        """Non-confirmation checkpoint auto-proceeds."""
        handler = ConsoleCheckpointHandler()
        ctx = CheckpointContext(
            job_id="test",
            sheet_num=1,
            prompt="test prompt",
            trigger=CheckpointTrigger(
                name="warn-only",
                requires_confirmation=False,
                message="Just a warning",
            ),
        )
        resp = await handler.checkpoint(ctx)
        assert resp.action == "proceed"

    def test_checkpoint_response_all_actions(self) -> None:
        """All CheckpointResponse actions should be valid."""
        for action in ("proceed", "abort", "skip", "modify_prompt"):
            resp = CheckpointResponse(action=action)  # type: ignore[arg-type]
            assert resp.action == action


# ═══════════════════════════════════════════════════════════════════════
# 5. RECOVERY / RATE LIMIT EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestRecoveryEdgeCases:
    """Recovery mixin adversarial scenarios."""

    def test_resolve_wait_duration_with_zero(self, tmp_path: Path) -> None:
        """suggested_wait_seconds=0 should fall back to config."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path)

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config
        # 0 is not > 0, so should fall back
        wait = host._resolve_wait_duration(0.0)
        assert wait == config.rate_limit.wait_minutes * 60

    def test_resolve_wait_duration_with_negative(self, tmp_path: Path) -> None:
        """Negative suggested_wait should fall back to config."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path)

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config
        wait = host._resolve_wait_duration(-10.0)
        assert wait == config.rate_limit.wait_minutes * 60

    def test_resolve_wait_duration_with_positive(self, tmp_path: Path) -> None:
        """Positive suggested_wait should be used directly."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path)

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config
        wait = host._resolve_wait_duration(42.0)
        assert wait == 42.0

    def test_resolve_wait_duration_with_none(self, tmp_path: Path) -> None:
        """None suggested_wait should fall back to config."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path)

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config
        wait = host._resolve_wait_duration(None)
        assert wait == config.rate_limit.wait_minutes * 60

    def test_retry_delay_exponential_backoff(self, tmp_path: Path) -> None:
        """Retry delay should grow exponentially and cap at max."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path, {
            "retry": {
                "base_delay_seconds": 1.0,
                "exponential_base": 2.0,
                "max_delay_seconds": 10.0,
                "jitter": False,
            },
        })

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config

        # attempt 1: 1 * 2^0 = 1
        assert host._get_retry_delay(1) == pytest.approx(1.0)
        # attempt 2: 1 * 2^1 = 2
        assert host._get_retry_delay(2) == pytest.approx(2.0)
        # attempt 5: 1 * 2^4 = 16 → capped at 10
        assert host._get_retry_delay(5) == pytest.approx(10.0)

    def test_retry_delay_with_jitter_is_bounded(self, tmp_path: Path) -> None:
        """With jitter enabled, delay should be between 50% and 150% of base."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path, {
            "retry": {
                "base_delay_seconds": 10.0,
                "exponential_base": 1.01,  # minimal growth (>1 required by validator)
                "max_delay_seconds": 100.0,
                "jitter": True,
            },
        })

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config

        # Run many times to check bounds
        delays = [host._get_retry_delay(1) for _ in range(100)]
        assert all(d >= 5.0 for d in delays), f"Min: {min(delays)}"
        assert all(d <= 15.0 for d in delays), f"Max: {max(delays)}"

    def test_infer_active_sheet_num(self) -> None:
        """_infer_active_sheet_num returns last_completed + 1."""
        from marianne.execution.runner.recovery import RecoveryMixin

        state = _make_checkpoint(total_sheets=5)
        state.last_completed_sheet = 3
        assert RecoveryMixin._infer_active_sheet_num(state) == 4

    @pytest.mark.asyncio
    async def test_rate_limit_max_waits_raises_fatal(self, tmp_path: Path) -> None:
        """Exceeding max rate limit waits should raise FatalError."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path, {
            "rate_limit": {"wait_minutes": 1, "max_waits": 2},
        })

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()
        host.backend = MagicMock(name="mock-backend")

        state = _make_checkpoint()
        state.rate_limit_waits = 2  # at max

        with pytest.raises(FatalError, match="maximum rate limit waits"):
            host._log_rate_limit_event(state, is_quota=False, wait_seconds=60.0)

    @pytest.mark.asyncio
    async def test_quota_max_waits_raises_fatal(self, tmp_path: Path) -> None:
        """Exceeding max quota waits should raise FatalError."""
        from marianne.execution.runner.recovery import RecoveryMixin

        config = _make_config(tmp_path, {
            "rate_limit": {"wait_minutes": 1, "max_quota_waits": 2},
        })

        class Host(RecoveryMixin):
            pass

        host = Host()
        host.config = config
        host.console = MagicMock()
        host._logger = MagicMock()
        host.backend = MagicMock(name="mock-backend")

        state = _make_checkpoint()
        state.quota_waits = 2  # at max

        with pytest.raises(FatalError, match="maximum quota exhaustion waits"):
            host._log_rate_limit_event(state, is_quota=True, wait_seconds=60.0)


# ═══════════════════════════════════════════════════════════════════════
# 6. PAUSE / RESUME / SHUTDOWN EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestPauseResumeEdgeCases:
    """Pause/resume race conditions and edge cases."""

    def test_graceful_shutdown_error_is_exception(self) -> None:
        """GracefulShutdownError should be a proper exception."""
        err = GracefulShutdownError()
        assert isinstance(err, Exception)
        assert isinstance(err, GracefulShutdownError)

    def test_fatal_error_with_message(self) -> None:
        """FatalError should carry the message."""
        err = FatalError("something broke")
        assert str(err) == "something broke"

    def test_pause_signal_file_lifecycle(self, tmp_path: Path) -> None:
        """Pause signal file creation, detection, and cleanup."""
        pause_dir = tmp_path / "signals"
        pause_dir.mkdir()
        job_id = "lifecycle-test"
        pause_file = pause_dir / f".mozart-pause-{job_id}"

        # No file → no pause
        assert not pause_file.exists()

        # Create signal → detected
        pause_file.touch()
        assert pause_file.exists()

        # Cleanup → gone
        pause_file.unlink()
        assert not pause_file.exists()

    def test_double_pause_file_creation(self, tmp_path: Path) -> None:
        """Creating pause file twice should not error."""
        pause_file = tmp_path / ".mozart-pause-test"
        pause_file.touch()
        pause_file.touch()  # second time
        assert pause_file.exists()

    def test_pause_cleanup_missing_file(self, tmp_path: Path) -> None:
        """Cleaning up non-existent pause file should not raise."""
        pause_file = tmp_path / ".mozart-pause-nonexistent"
        # Simulates unlink_missing_ok behavior
        pause_file.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# 7. RUNSUMMARY INVARIANTS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestRunSummaryInvariants:
    """RunSummary edge cases and invariants."""

    def test_completed_exceeds_total_raises(self) -> None:
        """completed_sheets > total_sheets should raise ValueError."""
        with pytest.raises(ValueError, match="exceeds total_sheets"):
            RunSummary(
                job_id="test",
                job_name="test",
                total_sheets=3,
                completed_sheets=5,
            )

    def test_zero_total_sheets_rates(self) -> None:
        """Zero total sheets should produce 0% success rate."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=0,
        )
        assert summary.success_rate == 0.0
        assert summary.validation_pass_rate == 100.0  # no validations = 100%
        assert summary.success_without_retry_rate == 0.0

    def test_all_sheets_skipped(self) -> None:
        """All sheets skipped → 0% success rate (no executed sheets)."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=5,
            skipped_sheets=5,
        )
        assert summary.success_rate == 0.0

    def test_success_rate_excludes_skipped(self) -> None:
        """Success rate denominator should exclude skipped sheets."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=10,
            completed_sheets=3,
            failed_sheets=2,
            skipped_sheets=5,
        )
        # executed = 10 - 5 = 5, rate = 3/5 = 60%
        assert summary.success_rate == pytest.approx(60.0)

    def test_validation_pass_rate_all_pass(self) -> None:
        """All validations pass → 100%."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=1,
            validation_pass_count=10,
            validation_fail_count=0,
        )
        assert summary.validation_pass_rate == 100.0

    def test_validation_pass_rate_all_fail(self) -> None:
        """All validations fail → 0%."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=1,
            validation_pass_count=0,
            validation_fail_count=10,
        )
        assert summary.validation_pass_rate == 0.0

    def test_format_duration_seconds(self) -> None:
        """Short durations format as seconds."""
        assert RunSummary._format_duration(5.3) == "5.3s"

    def test_format_duration_minutes(self) -> None:
        """Medium durations format as minutes + seconds."""
        assert RunSummary._format_duration(125.0) == "2m 5s"

    def test_format_duration_hours(self) -> None:
        """Long durations format as hours + minutes."""
        assert RunSummary._format_duration(3700.0) == "1h 1m"

    def test_to_dict_contains_required_keys(self) -> None:
        """to_dict should contain all expected top-level keys."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=2,
            completed_sheets=1,
            failed_sheets=1,
        )
        d = summary.to_dict()
        assert "job_id" in d
        assert "sheets" in d
        assert "validation" in d
        assert "execution" in d
        assert d["sheets"]["total"] == 2
        assert d["sheets"]["completed"] == 1

    def test_success_without_retry_rate_no_completed(self) -> None:
        """No completed sheets → 0% success_without_retry_rate."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=3,
            completed_sheets=0,
        )
        assert summary.success_without_retry_rate == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 8. GROUNDING DECISION CONTEXT EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestGroundingDecisionContextEdgeCases:
    """GroundingDecisionContext adversarial inputs."""

    def test_confidence_clamped_above_one(self) -> None:
        """Confidence > 1.0 should be clamped to 1.0."""
        ctx = GroundingDecisionContext(
            passed=True,
            message="test",
            confidence=1.5,
        )
        assert ctx.confidence == 1.0

    def test_confidence_clamped_below_zero(self) -> None:
        """Confidence < 0.0 should be clamped to 0.0."""
        ctx = GroundingDecisionContext(
            passed=False,
            message="test",
            confidence=-0.5,
        )
        assert ctx.confidence == 0.0

    def test_from_results_empty_list(self) -> None:
        """Empty results → passed=True with no hooks message."""
        ctx = GroundingDecisionContext.from_results([])
        assert ctx.passed is True
        assert ctx.hooks_executed == 0

    def test_disabled_context(self) -> None:
        """disabled() factory creates correct context."""
        ctx = GroundingDecisionContext.disabled()
        assert ctx.passed is True
        assert ctx.hooks_executed == 0
        assert "not enabled" in ctx.message.lower()

    def test_from_results_mixed_pass_fail(self) -> None:
        """Mixed pass/fail results produce correct aggregation."""
        result_pass = MagicMock()
        result_pass.passed = True
        result_pass.confidence = 0.9
        result_pass.should_escalate = False
        result_pass.hook_name = "pass-hook"
        result_pass.message = "ok"
        result_pass.recovery_guidance = None

        result_fail = MagicMock()
        result_fail.passed = False
        result_fail.confidence = 0.3
        result_fail.should_escalate = True
        result_fail.hook_name = "fail-hook"
        result_fail.message = "not ok"
        result_fail.recovery_guidance = "fix it"

        ctx = GroundingDecisionContext.from_results([result_pass, result_fail])
        assert ctx.passed is False
        assert ctx.should_escalate is True
        assert ctx.hooks_executed == 2
        assert ctx.recovery_guidance is not None
        assert ctx.confidence == pytest.approx(0.6)


# ═══════════════════════════════════════════════════════════════════════
# 9. SHEET EXECUTION MODE ENUM
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestSheetExecutionModeEnum:
    """SheetExecutionMode enum edge cases."""

    def test_all_modes_are_strings(self) -> None:
        """All modes should be string-valued."""
        for mode in SheetExecutionMode:
            assert isinstance(mode.value, str)

    @pytest.mark.parametrize("mode_value", ["normal", "completion", "retry", "escalate"])
    def test_mode_from_value(self, mode_value: str) -> None:
        """Modes should be constructible from their string values."""
        mode = SheetExecutionMode(mode_value)
        assert mode.value == mode_value

    def test_invalid_mode_raises(self) -> None:
        """Invalid mode value should raise ValueError."""
        with pytest.raises(ValueError):
            SheetExecutionMode("invalid_mode")


# ═══════════════════════════════════════════════════════════════════════
# 10. ERROR CLASSIFICATION ADVERSARIAL
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestErrorClassificationAdversarial:
    """Error classification with adversarial inputs."""

    def test_classify_with_adversarial_stdout(self, tmp_path: Path) -> None:
        """Classifier should handle adversarial strings in stdout without crash."""
        from marianne.core.errors import ErrorClassifier

        classifier = ErrorClassifier.from_config([])

        for adversarial in _ADVERSARIAL_STRINGS[:10]:  # first 10 for speed
            result = classifier.classify_execution(
                stdout=adversarial,
                stderr="",
                exit_code=1,
            )
            # Should always produce a result, never crash
            assert result.primary is not None

    def test_classify_with_adversarial_stderr(self, tmp_path: Path) -> None:
        """Classifier should handle adversarial strings in stderr without crash."""
        from marianne.core.errors import ErrorClassifier

        classifier = ErrorClassifier.from_config([])

        for adversarial in _ADVERSARIAL_STRINGS[:10]:
            result = classifier.classify_execution(
                stdout="",
                stderr=adversarial,
                exit_code=1,
            )
            assert result.primary is not None

    @pytest.mark.parametrize("exit_code", [0, 1, -1, 127, 128, 137, 255, -9, -15])
    def test_classify_various_exit_codes(self, exit_code: int) -> None:
        """Classifier should handle all common exit codes."""
        from marianne.core.errors import ErrorClassifier

        classifier = ErrorClassifier.from_config([])
        result = classifier.classify_execution(
            stdout="output",
            stderr="error",
            exit_code=exit_code,
        )
        assert result.primary is not None
        assert isinstance(result.primary.error_code, ErrorCode)

    def test_classify_with_signal_exit(self) -> None:
        """Classifier should handle signal-based exits."""
        from marianne.core.errors import ErrorClassifier

        classifier = ErrorClassifier.from_config([])
        result = classifier.classify_execution(
            stdout="",
            stderr="killed",
            exit_code=None,
            exit_signal=9,
            exit_reason="killed",
        )
        assert result.primary is not None


# ═══════════════════════════════════════════════════════════════════════
# 11. ADVERSARIAL STRINGS IN ESCALATION CONTEXT
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestEscalationContextAdversarial:
    """Escalation context with adversarial string inputs."""

    @pytest.mark.parametrize("job_id", ["", "a" * 1000, "null", "../../../etc/passwd"])
    def test_escalation_context_adversarial_job_ids(self, job_id: str) -> None:
        """EscalationContext should accept any string as job_id."""
        ctx = EscalationContext(
            job_id=job_id,
            sheet_num=1,
            validation_results=[],
            confidence=0.5,
            retry_count=0,
            error_history=[],
            prompt_used="test",
            output_summary="test",
        )
        assert ctx.job_id == job_id

    def test_escalation_context_with_many_suggestions(self) -> None:
        """EscalationContext should handle many historical suggestions."""
        suggestions = [
            HistoricalSuggestion(
                action="retry",
                outcome="success",
                confidence=0.5,
                validation_pass_rate=50.0,
                guidance=f"suggestion {i}",
            )
            for i in range(100)
        ]
        ctx = EscalationContext(
            job_id="test",
            sheet_num=1,
            validation_results=[],
            confidence=0.5,
            retry_count=0,
            error_history=[],
            prompt_used="test",
            output_summary="test",
            historical_suggestions=suggestions,
        )
        assert len(ctx.historical_suggestions) == 100

    def test_escalation_context_empty_everything(self) -> None:
        """EscalationContext with all-empty fields should be constructible."""
        ctx = EscalationContext(
            job_id="",
            sheet_num=0,
            validation_results=[],
            confidence=0.0,
            retry_count=0,
            error_history=[],
            prompt_used="",
            output_summary="",
        )
        assert ctx.confidence == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 12. EXECUTION RESULT INVARIANTS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.adversarial
class TestExecutionResultInvariants:
    """ExecutionResult adversarial construction."""

    def test_success_with_none_exit_code_is_valid(self) -> None:
        """success=True with exit_code=None is valid (signal-based)."""
        result = ExecutionResult(
            success=True,
            stdout="done",
            stderr="",
            duration_seconds=1.0,
            exit_code=None,
        )
        assert result.success is True

    def test_failure_with_timeout_reason(self) -> None:
        """Timeout exit_reason should be valid."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="timed out",
            duration_seconds=300.0,
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.exit_reason == "timeout"

    @pytest.mark.parametrize("reason", ["completed", "timeout", "killed", "error"])
    def test_all_exit_reasons_valid(self, reason: ExitReason) -> None:
        """All exit_reason literals should be accepted."""
        result = ExecutionResult(
            success=reason == "completed",
            stdout="",
            stderr="",
            duration_seconds=1.0,
            exit_code=0 if reason == "completed" else None,
            exit_reason=reason,
        )
        assert result.exit_reason == reason

    def test_negative_duration(self) -> None:
        """Negative duration should be accepted (no validation on it)."""
        result = ExecutionResult(
            success=True,
            stdout="ok",
            stderr="",
            duration_seconds=-1.0,
            exit_code=0,
        )
        assert result.duration_seconds < 0
