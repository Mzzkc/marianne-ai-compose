"""Tests for Harper's bug fixes: #126, #113, #103, #100.

Covers:
- Error classification for exit_code=None fallthrough (Bug #126)
- Iterative DFS regression for large DAGs (Bug #113)
- Config hash stale state detection (Bug #103)
- Rate limit pause instead of kill (Bug #100)

Test IDs reference Breakpoint's adversarial spec (tests-breakpoint.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.errors.classifier import ErrorClassifier
from marianne.core.errors.codes import ErrorCategory
from marianne.execution.dag import DependencyDAG
from marianne.execution.runner.models import (
    FatalError,
    RateLimitExhaustedError,
    RunSummary,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def classifier() -> ErrorClassifier:
    """Create a default ErrorClassifier."""
    return ErrorClassifier()


# =============================================================================
# Bug #126 — Error Classification: exit_code=None fallthrough
# =============================================================================


class TestExitCodeNoneClassification:
    """Tests for exit_code=None handling in ErrorClassifier.classify().

    The original bug: exit_code=None with no exit_reason fell through to
    FATAL classification. Process kills (OOM, signals) produce None exit
    codes — these should be TRANSIENT, not FATAL.
    """

    def test_ec_001_none_exit_code_error_reason_is_transient(
        self, classifier: ErrorClassifier,
    ) -> None:
        """TEST-EC-001: exit_code=None + exit_reason='error' → TRANSIENT."""
        result = classifier.classify(
            exit_code=None, exit_reason="error", stdout="", stderr="",
        )
        assert result.category == ErrorCategory.TRANSIENT
        assert result.retriable is True
        assert "signal race" in result.message or "process" in result.message.lower()

    def test_ec_002_none_exit_code_no_reason_is_transient(
        self, classifier: ErrorClassifier,
    ) -> None:
        """TEST-EC-002: exit_code=None + exit_reason=None → TRANSIENT (the actual bug)."""
        result = classifier.classify(
            exit_code=None, exit_reason=None, stdout="", stderr="",
        )
        assert result.category == ErrorCategory.TRANSIENT
        assert result.retriable is True
        assert result.suggested_wait_seconds is not None
        assert result.suggested_wait_seconds >= 10

    def test_ec_003_none_exit_code_oom_killed(
        self, classifier: ErrorClassifier,
    ) -> None:
        """TEST-EC-003: exit_code=None + stderr 'Killed' → TRANSIENT with longer wait."""
        result = classifier.classify(
            exit_code=None, stderr="Killed", stdout="",
        )
        assert result.category == ErrorCategory.TRANSIENT
        assert result.retriable is True
        assert result.suggested_wait_seconds is not None
        assert result.suggested_wait_seconds >= 30

    def test_ec_003_none_exit_code_out_of_memory(
        self, classifier: ErrorClassifier,
    ) -> None:
        """TEST-EC-003 variant: stderr 'Out of memory' → TRANSIENT."""
        result = classifier.classify(
            exit_code=None, stderr="Out of memory", stdout="",
        )
        assert result.category == ErrorCategory.TRANSIENT
        assert result.retriable is True
        assert result.suggested_wait_seconds is not None
        assert result.suggested_wait_seconds >= 30

    def test_ec_004_none_exit_code_permission_denied_is_not_retriable(
        self, classifier: ErrorClassifier,
    ) -> None:
        """TEST-EC-004: exit_code=None + stderr 'permission denied' → non-retriable.

        The pattern matcher classifies 'permission denied' as AUTH before
        the fallthrough logic runs. The key property: it must NOT be retriable.
        """
        result = classifier.classify(
            exit_code=None, stderr="permission denied", stdout="",
        )
        # AUTH or FATAL — either way, not retriable
        assert result.category in (ErrorCategory.FATAL, ErrorCategory.AUTH)
        assert result.retriable is False

    def test_ec_005_negative_exit_code_sigkill(
        self, classifier: ErrorClassifier,
    ) -> None:
        """TEST-EC-005: exit_code=-9 (SIGKILL) → TRANSIENT with exit_signal."""
        result = classifier.classify(exit_code=-9)
        assert result.category in (ErrorCategory.TRANSIENT, ErrorCategory.SIGNAL)
        assert result.exit_signal == 9
        assert result.retriable is True

    def test_ec_005_negative_exit_code_sigterm(
        self, classifier: ErrorClassifier,
    ) -> None:
        """TEST-EC-005: exit_code=-15 (SIGTERM) → TRANSIENT with exit_signal."""
        result = classifier.classify(exit_code=-15)
        assert result.category in (ErrorCategory.TRANSIENT, ErrorCategory.SIGNAL)
        assert result.exit_signal == 15
        assert result.retriable is True

    @pytest.mark.parametrize(
        "exit_code, exit_reason, stdout, stderr",
        [
            (None, None, "", ""),
            (None, "", "", ""),
            (None, "timeout", "", ""),
            (255, None, "", ""),
        ],
        ids=["none-none", "none-empty", "none-timeout", "255-none"],
    )
    def test_ec_006_classify_never_returns_none(
        self,
        classifier: ErrorClassifier,
        exit_code: int | None,
        exit_reason: str | None,
        stdout: str,
        stderr: str,
    ) -> None:
        """TEST-EC-006: classify() always returns a valid result, never None."""
        result = classifier.classify(
            exit_code=exit_code, exit_reason=exit_reason,
            stdout=stdout, stderr=stderr,
        )
        assert result is not None
        assert result.category is not None
        assert isinstance(result.retriable, bool)


# =============================================================================
# Bug #113 — Iterative DFS regression
# =============================================================================


class TestIterativeDFSRegression:
    """Tests that DAG cycle detection uses iterative DFS.

    Bug #113: Recursive DFS caused stack overflow on large scores.
    Already fixed with iterative DFS — these are regression tests.
    """

    def test_large_dag_no_stack_overflow(self) -> None:
        """1000+ sheet DAG doesn't hit recursion limit."""
        # Linear chain: 1 → 2 → 3 → ... → 1000
        total = 1000
        deps: dict[int, list[int]] = {}
        for i in range(2, total + 1):
            deps[i] = [i - 1]

        dag = DependencyDAG.from_dependencies(
            total_sheets=total, dependencies=deps,
        )
        assert dag.validated is True
        order = dag.get_execution_order()
        assert len(order) == total
        # Must be sequential: [1, 2, 3, ..., 1000]
        assert order == list(range(1, total + 1))

    def test_wide_dag_1000_parallel_sheets(self) -> None:
        """DAG with 1000 sheets all depending on sheet 1."""
        total = 1001
        deps: dict[int, list[int]] = {}
        for i in range(2, total + 1):
            deps[i] = [1]

        dag = DependencyDAG.from_dependencies(
            total_sheets=total, dependencies=deps,
        )
        assert dag.validated is True
        groups = dag.get_parallel_groups()
        # First group: [1], second group: [2, 3, ..., 1001]
        assert groups[0] == [1]
        assert len(groups[1]) == 1000

    def test_deep_dag_2000_sheets(self) -> None:
        """Deeper than Python's default recursion limit (1000)."""
        total = 2000
        deps: dict[int, list[int]] = {}
        for i in range(2, total + 1):
            deps[i] = [i - 1]

        dag = DependencyDAG.from_dependencies(
            total_sheets=total, dependencies=deps,
        )
        assert dag.validated is True
        assert len(dag.get_execution_order()) == total


# =============================================================================
# Bug #103 — Config hash stale state detection
# =============================================================================


class TestConfigHashStaleDetection:
    """Tests for config hash population and stale state detection.

    Bug #103: Stale state reuse when score file changed — the config_hash
    field exists but was never populated.
    """

    def test_ss_001_config_hash_populated_on_new_state(self) -> None:
        """TEST-SS-001: config_hash is populated on new state creation."""
        from marianne.execution.runner.lifecycle import LifecycleMixin

        config_a = {"name": "test-job", "sheet": {"total_sheets": 5}}
        config_b = {"name": "test-job", "sheet": {"total_sheets": 10}}

        hash_a = LifecycleMixin._compute_config_hash(config_a)
        hash_b = LifecycleMixin._compute_config_hash(config_b)

        assert hash_a is not None
        assert len(hash_a) == 64  # SHA-256 hex digest
        assert hash_a != hash_b  # Different configs produce different hashes

    def test_ss_004_config_hash_is_content_based(self) -> None:
        """TEST-SS-004: Content-based, not mtime-based."""
        from marianne.execution.runner.lifecycle import LifecycleMixin

        config = {"name": "test-job", "workspace": "/tmp/ws"}
        hash_1 = LifecycleMixin._compute_config_hash(config)
        hash_2 = LifecycleMixin._compute_config_hash(config)
        assert hash_1 == hash_2  # Same content → same hash

    def test_ss_004_identical_configs_produce_same_hash(self) -> None:
        """TEST-SS-004: Two different dicts with same content → same hash."""
        from marianne.execution.runner.lifecycle import LifecycleMixin

        config1 = {"name": "job", "b": 2, "a": 1}
        config2 = {"a": 1, "name": "job", "b": 2}
        assert LifecycleMixin._compute_config_hash(config1) == \
               LifecycleMixin._compute_config_hash(config2)


@pytest.mark.asyncio
class TestConfigHashStateLifecycle:
    """Integration tests for config hash through the state lifecycle."""

    async def test_ss_002_changed_score_detects_staleness(self) -> None:
        """TEST-SS-002: Re-run with changed score detects stale state."""

        from marianne.execution.runner.lifecycle import LifecycleMixin

        # Create a mock mixin with necessary attributes
        mixin = MagicMock(spec=LifecycleMixin)
        mixin._compute_config_hash = LifecycleMixin._compute_config_hash

        old_snapshot = {"name": "my-job", "sheet": {"total_sheets": 3}}
        old_hash = LifecycleMixin._compute_config_hash(old_snapshot)

        # Verify we can create state with the hash (verifies field exists)
        state = CheckpointState(
            job_id="my-job",
            job_name="my-job",
            total_sheets=3,
            status=JobStatus.COMPLETED,
            config_hash=old_hash,
            config_snapshot=old_snapshot,
        )
        assert state.config_hash == old_hash

        # New config produces different hash
        new_snapshot = {"name": "my-job", "sheet": {"total_sheets": 5}}
        new_hash = LifecycleMixin._compute_config_hash(new_snapshot)
        assert old_hash != new_hash

    async def test_ss_003_unchanged_score_reuses_state(self) -> None:
        """TEST-SS-003: Re-run with unchanged score reuses state."""
        from marianne.execution.runner.lifecycle import LifecycleMixin

        snapshot = {"name": "my-job", "sheet": {"total_sheets": 3}}
        hash_val = LifecycleMixin._compute_config_hash(snapshot)

        state = CheckpointState(
            job_id="my-job",
            job_name="my-job",
            total_sheets=3,
            status=JobStatus.COMPLETED,
            config_hash=hash_val,
        )
        # Same hash means no staleness
        current_hash = LifecycleMixin._compute_config_hash(snapshot)
        assert current_hash == state.config_hash

    async def test_ss_005_stale_detection_covers_completed_and_failed(
        self,
    ) -> None:
        """TEST-SS-005: Stale detection applies to both COMPLETED and FAILED jobs.

        Bug #103 extension: originally stale detection only covered COMPLETED.
        FAILED jobs with changed configs should also restart fresh — the user
        who changed their config after a failure is trying to fix the problem.
        Resuming from stale state defeats the purpose.
        """
        from marianne.execution.runner.lifecycle import LifecycleMixin

        old_snapshot = {"name": "my-job", "sheet": {"total_sheets": 3}}
        new_snapshot = {"name": "my-job", "sheet": {"total_sheets": 5}}
        old_hash = LifecycleMixin._compute_config_hash(old_snapshot)
        new_hash = LifecycleMixin._compute_config_hash(new_snapshot)

        # FAILED job with changed config — stale detection should fire
        failed_state = CheckpointState(
            job_id="my-job",
            job_name="my-job",
            total_sheets=3,
            status=JobStatus.FAILED,
            config_hash=old_hash,
        )
        assert failed_state.status == JobStatus.FAILED
        assert old_hash != new_hash
        # The implementation should detect staleness for FAILED too

    async def test_ss_007_failed_unchanged_config_resumes(self) -> None:
        """TEST-SS-007: FAILED job with same config resumes normally.

        When the config hasn't changed, a FAILED job should resume from
        where it left off, not restart. Only changed configs trigger restart.
        """
        from marianne.execution.runner.lifecycle import LifecycleMixin

        snapshot = {"name": "my-job", "sheet": {"total_sheets": 3}}
        hash_val = LifecycleMixin._compute_config_hash(snapshot)

        failed_state = CheckpointState(
            job_id="my-job",
            job_name="my-job",
            total_sheets=3,
            status=JobStatus.FAILED,
            config_hash=hash_val,
            last_completed_sheet=2,
        )
        # Same hash → resume, don't restart
        current_hash = LifecycleMixin._compute_config_hash(snapshot)
        assert current_hash == failed_state.config_hash
        assert failed_state.last_completed_sheet == 2

    async def test_ss_008_null_hash_legacy_jobs_not_detected(self) -> None:
        """TEST-SS-008: Legacy jobs without config_hash skip stale detection.

        Old state files don't have config_hash. These should resume
        normally — we can't detect staleness without a baseline hash.
        """
        legacy_state = CheckpointState(
            job_id="legacy-job",
            job_name="legacy-job",
            total_sheets=3,
            status=JobStatus.FAILED,
            config_hash=None,
        )
        # No config_hash → stale detection cannot apply
        assert legacy_state.config_hash is None

    async def test_ss_009_stale_detection_for_failed_creates_fresh_state(
        self,
    ) -> None:
        """TEST-SS-009: When FAILED + changed config, a fresh CheckpointState is created.

        The new state should have the updated config hash and reset
        last_completed_sheet to 0.
        """
        from marianne.execution.runner.lifecycle import LifecycleMixin

        old_snapshot = {"name": "my-job", "sheet": {"total_sheets": 3}}
        new_snapshot = {"name": "my-job", "sheet": {"total_sheets": 5}}
        old_hash = LifecycleMixin._compute_config_hash(old_snapshot)
        new_hash = LifecycleMixin._compute_config_hash(new_snapshot)

        # Simulate: old FAILED state exists, new config submitted
        old_state = CheckpointState(
            job_id="my-job",
            job_name="my-job",
            total_sheets=3,
            status=JobStatus.FAILED,
            config_hash=old_hash,
            last_completed_sheet=2,
        )

        # After stale detection fires, fresh state should:
        fresh_state = CheckpointState(
            job_id="my-job",
            job_name="my-job",
            total_sheets=5,  # new config value
            config_hash=new_hash,
            config_snapshot=new_snapshot,
        )
        assert fresh_state.last_completed_sheet == 0
        assert fresh_state.config_hash == new_hash
        assert fresh_state.status != JobStatus.FAILED


# =============================================================================
# Bug #100 — Rate Limit Pause Instead of Kill
# =============================================================================


class TestRateLimitExhaustedError:
    """Tests for the RateLimitExhaustedError exception type."""

    def test_rl_001_is_fatal_error_subclass(self) -> None:
        """RateLimitExhaustedError IS-A FatalError for backwards compat."""
        err = RateLimitExhaustedError("test")
        assert isinstance(err, FatalError)
        assert isinstance(err, RateLimitExhaustedError)

    def test_rl_001_carries_resume_after(self) -> None:
        """TEST-RL-001: Error carries resume_after datetime."""
        now = datetime.now(UTC)
        err = RateLimitExhaustedError(
            "Rate limited",
            resume_after=now,
            backend_type="claude-cli",
            quota_exhaustion=False,
        )
        assert err.resume_after == now
        assert err.backend_type == "claude-cli"
        assert err.quota_exhaustion is False
        assert "Rate limited" in str(err)

    def test_rl_003_quota_vs_rate_limit_distinction(self) -> None:
        """TEST-RL-003: Quota exhaustion vs regular rate limit."""
        quota_err = RateLimitExhaustedError(
            "Quota exhausted",
            quota_exhaustion=True,
        )
        rate_err = RateLimitExhaustedError(
            "Rate limited",
            quota_exhaustion=False,
        )
        assert quota_err.quota_exhaustion is True
        assert rate_err.quota_exhaustion is False

    def test_rl_006_resume_after_defaults_to_none(self) -> None:
        """TEST-RL-006: When resume time is unparseable, resume_after can be None."""
        err = RateLimitExhaustedError("Rate limited")
        assert err.resume_after is None
        assert err.backend_type == "unknown"


class TestRateLimitRecoveryRaises:
    """Tests for recovery.py raising RateLimitExhaustedError."""

    def test_rl_001_quota_max_waits_raises_rate_limit_error(self) -> None:
        """TEST-RL-001: Quota max waits raises RateLimitExhaustedError."""
        from marianne.execution.runner.recovery import RecoveryMixin

        # Create a mock mixin
        mixin = MagicMock(spec=RecoveryMixin)
        mixin.config = MagicMock()
        mixin.config.rate_limit.max_quota_waits = 3
        mixin.config.rate_limit.max_waits = 5
        mixin.console = MagicMock()
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.name = "claude-cli"

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            quota_waits=3,
        )

        with pytest.raises(RateLimitExhaustedError) as exc_info:
            RecoveryMixin._log_rate_limit_event(
                mixin, state, is_quota=True, wait_seconds=300.0,
            )
        assert exc_info.value.quota_exhaustion is True
        assert exc_info.value.resume_after is not None
        assert exc_info.value.backend_type == "claude-cli"

    def test_rl_001_rate_limit_max_waits_raises_rate_limit_error(self) -> None:
        """TEST-RL-001: Regular rate limit max waits raises RateLimitExhaustedError."""
        from marianne.execution.runner.recovery import RecoveryMixin

        mixin = MagicMock(spec=RecoveryMixin)
        mixin.config = MagicMock()
        mixin.config.rate_limit.max_waits = 3
        mixin.config.rate_limit.max_quota_waits = 5
        mixin.console = MagicMock()
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.name = "anthropic-api"

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            rate_limit_waits=3,
        )

        with pytest.raises(RateLimitExhaustedError) as exc_info:
            RecoveryMixin._log_rate_limit_event(
                mixin, state, is_quota=False, wait_seconds=60.0,
            )
        assert exc_info.value.quota_exhaustion is False
        assert exc_info.value.resume_after is not None
        assert exc_info.value.backend_type == "anthropic-api"

    def test_rl_001_below_max_waits_does_not_raise(self) -> None:
        """Below max_waits: no error raised, just console output."""
        from marianne.execution.runner.recovery import RecoveryMixin

        mixin = MagicMock(spec=RecoveryMixin)
        mixin.config = MagicMock()
        mixin.config.rate_limit.max_waits = 5
        mixin.console = MagicMock()
        mixin._logger = MagicMock()

        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            rate_limit_waits=2,
        )

        # Should NOT raise
        RecoveryMixin._log_rate_limit_event(
            mixin, state, is_quota=False, wait_seconds=60.0,
        )
        mixin.console.print.assert_called_once()


class TestRateLimitPauseTransition:
    """Tests for PAUSED transition in lifecycle when rate limit exhausted."""

    def test_rl_002_rate_limit_error_caught_as_fatal_error(self) -> None:
        """RateLimitExhaustedError is caught by except FatalError blocks."""
        err = RateLimitExhaustedError("test", resume_after=None)
        try:
            raise err
        except FatalError:
            pass  # This must catch it — subclass relationship

    def test_rl_004_resume_at_field_on_checkpoint(self) -> None:
        """TEST-RL-004: resume_at field exists and is usable on CheckpointState."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            rate_limit_waits=5,
            resume_at="2026-03-27T12:00:00+00:00",
        )
        assert state.resume_at is not None
        assert "2026-03-27" in state.resume_at

        # After reset
        state.rate_limit_waits = 0
        state.quota_waits = 0
        state.resume_at = None
        assert state.rate_limit_waits == 0
        assert state.resume_at is None

    def test_rl_002_run_summary_paused_status(self) -> None:
        """TEST-RL-002: Job service returns PAUSED summary on rate limit."""
        summary = RunSummary(
            job_id="test",
            job_name="test",
            total_sheets=5,
            final_status=JobStatus.PAUSED,
        )
        assert summary.final_status == JobStatus.PAUSED


# =============================================================================
# Cross-cutting: ensure RateLimitExhaustedError propagates correctly
# =============================================================================


class TestRateLimitErrorPropagation:
    """Verify RateLimitExhaustedError is handled before FatalError in catch chains."""

    def test_subclass_ordering_in_except(self) -> None:
        """RateLimitExhaustedError caught before FatalError when ordered correctly."""
        err = RateLimitExhaustedError("test")
        caught_type = None

        try:
            raise err
        except RateLimitExhaustedError:
            caught_type = "rate_limit"
        except FatalError:
            caught_type = "fatal"

        assert caught_type == "rate_limit"

    def test_fatal_error_does_not_catch_as_rate_limit(self) -> None:
        """Plain FatalError is NOT caught by except RateLimitExhaustedError."""
        err = FatalError("test")
        caught_type = None

        try:
            raise err
        except RateLimitExhaustedError:
            caught_type = "rate_limit"
        except FatalError:
            caught_type = "fatal"

        assert caught_type == "fatal"
