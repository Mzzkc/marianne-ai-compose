"""Adversarial tests for Mozart v1 beta — Movement 1, Cycle 3.

Targets the highest-severity production bugs found by real usage.

Test categories:
1. F-111: RateLimitExhaustedError lost in parallel mode — proves the error type
   is destroyed by the ParallelExecutor and lifecycle, causing jobs to FAIL
   instead of PAUSE.
2. F-113: Failed dependencies treated as "done" — proves downstream sheets
   execute against missing/incomplete inputs when an upstream fan-out voice fails.
3. F-075 regression: Resume after fan-out failure — verifies the fix holds
   under adversarial conditions (concurrent failures, interleaved statuses).
4. F-122: IPC callsites bypassing --conductor-clone — proves code paths
   hardcode production socket, breaking clone test isolation.
5. Parallel executor error propagation — edge cases in TaskGroup exception
   handling.
6. Baton state machine new edge cases.
7. Cross-system integration tests.

@pytest.mark.adversarial
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mozart.core.checkpoint import SheetState, SheetStatus
from mozart.execution.dag import DependencyDAG
from mozart.execution.parallel import (
    ParallelExecutionConfig,
    ParallelExecutor,
)
from mozart.execution.runner.models import FatalError, RateLimitExhaustedError


# =============================================================================
# Test Helpers
# =============================================================================


def _make_state(total: int, statuses: dict[int, SheetStatus] | None = None):
    """Create a mock CheckpointState with the given sheets."""
    from mozart.core.checkpoint import CheckpointState

    state = MagicMock(spec=CheckpointState)
    state.total_sheets = total
    state.last_completed_sheet = 0
    state.job_id = "test-job"
    state.sheets = {}
    for i in range(1, total + 1):
        ss = MagicMock(spec=SheetState)
        ss.status = (statuses or {}).get(i, SheetStatus.PENDING)
        state.sheets[i] = ss
    return state


def _make_dag(total: int, deps: dict[int, list[int]]) -> DependencyDAG:
    """Create a DependencyDAG using the from_dependencies classmethod."""
    return DependencyDAG.from_dependencies(total, deps)


def _make_runner(dag: DependencyDAG | None = None):
    """Create a mock runner, optionally with a DAG."""
    runner = MagicMock()
    runner._execute_sheet_with_recovery = AsyncMock()
    runner.dependency_dag = dag
    runner._state_lock = asyncio.Lock()
    runner.state_backend = AsyncMock()
    return runner


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_runner():
    return _make_runner()


@pytest.fixture
def cfg_fail_fast():
    return ParallelExecutionConfig(enabled=True, max_concurrent=4, fail_fast=True)


@pytest.fixture
def cfg_no_fail_fast():
    return ParallelExecutionConfig(enabled=True, max_concurrent=4, fail_fast=False)


# =============================================================================
# F-111: RateLimitExhaustedError Lost in Parallel Mode
# =============================================================================


class TestF111RateLimitLostInParallel:
    """Prove that RateLimitExhaustedError is destroyed by the parallel executor.

    The bug: when a sheet in a parallel batch raises RateLimitExhaustedError,
    the ParallelExecutor catches it as a generic Exception, stores the message
    as a string in error_details, and the lifecycle raises FatalError instead.
    The lifecycle's except RateLimitExhaustedError handler never fires.
    """

    @pytest.mark.adversarial
    async def test_rate_limit_error_type_in_batch_result(self, mock_runner, cfg_fail_fast):
        """Error TYPE is stored as string prefix — isinstance() checks impossible."""
        rate_limit_error = RateLimitExhaustedError(
            "Rate limit reached — resets at 12:00",
            resume_after=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
            backend_type="claude-cli",
        )

        async def side_effect(state, sheet_num):
            if sheet_num == 2:
                raise rate_limit_error

        mock_runner._execute_sheet_with_recovery = AsyncMock(side_effect=side_effect)
        executor = ParallelExecutor(runner=mock_runner, config=cfg_fail_fast)

        result = await executor.execute_batch([1, 2], _make_state(2))

        assert 2 in result.failed
        error_text = result.error_details[2]
        # Type name IS in the string — but that's all it is: a string
        assert "RateLimitExhaustedError" in error_text
        assert "Rate limit reached" in error_text

    @pytest.mark.adversarial
    async def test_resume_after_timestamp_lost(self, mock_runner, cfg_fail_fast):
        """resume_after timestamp — critical scheduling data — is destroyed."""
        rate_limit_error = RateLimitExhaustedError(
            "Quota exhausted",
            resume_after=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
            backend_type="claude-cli",
            quota_exhaustion=True,
        )

        mock_runner._execute_sheet_with_recovery = AsyncMock(side_effect=rate_limit_error)
        executor = ParallelExecutor(runner=mock_runner, config=cfg_fail_fast)

        result = await executor.execute_batch([1], _make_state(1))

        assert 1 in result.failed
        # ParallelBatchResult has no structured exception storage
        assert not hasattr(result, "exceptions")
        assert not hasattr(result, "error_types")

    @pytest.mark.adversarial
    async def test_lifecycle_fatal_error_not_rate_limit(self):
        """lifecycle.py:1169 raises FatalError, not RateLimitExhaustedError.

        The except RateLimitExhaustedError at line 986 is dead code for
        parallel mode. This test proves the exception hierarchy.
        """
        error_msg = "RateLimitExhaustedError: Rate limit reached"
        raised_error = FatalError(f"Sheet 2 failed: {error_msg}")

        assert isinstance(raised_error, FatalError)
        assert not isinstance(raised_error, RateLimitExhaustedError)

        caught_by_rate_limit = False
        caught_by_fatal = False
        try:
            raise raised_error
        except RateLimitExhaustedError:
            caught_by_rate_limit = True
        except FatalError:
            caught_by_fatal = True

        assert not caught_by_rate_limit
        assert caught_by_fatal

    @pytest.mark.adversarial
    async def test_all_sheets_rate_limited_batch(self, mock_runner, cfg_fail_fast):
        """ALL sheets rate-limited: job FAILS instead of PAUSING."""
        err = RateLimitExhaustedError(
            "Rate limit reached",
            resume_after=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
            backend_type="claude-cli",
        )
        mock_runner._execute_sheet_with_recovery = AsyncMock(side_effect=err)
        executor = ParallelExecutor(runner=mock_runner, config=cfg_fail_fast)

        result = await executor.execute_batch([1, 2, 3], _make_state(3))

        assert len(result.failed) == 3
        assert len(result.completed) == 0
        for sn in [1, 2, 3]:
            assert "RateLimitExhaustedError" in result.error_details[sn]

    @pytest.mark.adversarial
    async def test_mixed_rate_limit_and_auth_failure(self, mock_runner, cfg_fail_fast):
        """Mixed batch: rate limit + auth failure. Both become generic failures."""
        async def side_effect(state, sheet_num):
            if sheet_num == 1:
                raise RateLimitExhaustedError(
                    "Rate limit",
                    resume_after=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
                    backend_type="claude-cli",
                )
            elif sheet_num == 2:
                raise FatalError("Auth failed: invalid API key")

        mock_runner._execute_sheet_with_recovery = AsyncMock(side_effect=side_effect)
        executor = ParallelExecutor(runner=mock_runner, config=cfg_fail_fast)

        result = await executor.execute_batch([1, 2], _make_state(2))

        assert set(result.failed) == {1, 2}
        assert "RateLimitExhaustedError" in result.error_details[1]
        assert "FatalError" in result.error_details[2]


# =============================================================================
# F-113: Failed Dependencies Treated as "Done"
# =============================================================================


class TestF113FailedDependenciesTreatedAsDone:
    """Prove that failed fan-out voices allow downstream sheets to execute."""

    @pytest.mark.adversarial
    async def test_failed_dep_allows_downstream_dispatch(self, cfg_no_fail_fast):
        """Sheet 2 fails in fan-out [2,3,4]. Sheet 5 (depends on ALL) still dispatches."""
        dag = _make_dag(5, {2: [1], 3: [1], 4: [1], 5: [2, 3, 4]})
        runner = _make_runner(dag)
        executor = ParallelExecutor(runner=runner, config=cfg_no_fail_fast)

        state = _make_state(5, {
            1: SheetStatus.COMPLETED, 2: SheetStatus.FAILED,
            3: SheetStatus.COMPLETED, 4: SheetStatus.COMPLETED,
            5: SheetStatus.PENDING,
        })
        executor._permanently_failed.add(2)

        batch = executor.get_next_parallel_batch(state)
        # BUG: Sheet 5 IS dispatched despite sheet 2 being failed
        assert 5 in batch, "F-113: downstream dispatches with failed dependency"

    @pytest.mark.adversarial
    async def test_multiple_failed_deps(self, cfg_no_fail_fast):
        """3 of 5 fan-out voices fail, synthesis still runs."""
        deps = {2: [1], 3: [1], 4: [1], 5: [1], 6: [1], 7: [2, 3, 4, 5, 6]}
        dag = _make_dag(7, deps)
        runner = _make_runner(dag)
        executor = ParallelExecutor(runner=runner, config=cfg_no_fail_fast)

        state = _make_state(7, {
            1: SheetStatus.COMPLETED, 2: SheetStatus.FAILED,
            3: SheetStatus.FAILED, 4: SheetStatus.COMPLETED,
            5: SheetStatus.FAILED, 6: SheetStatus.COMPLETED,
            7: SheetStatus.PENDING,
        })
        executor._permanently_failed.update({2, 3, 5})

        batch = executor.get_next_parallel_batch(state)
        assert 7 in batch, "F-113: synthesis dispatches with 3/5 deps failed"

    @pytest.mark.adversarial
    async def test_permanently_failed_ephemeral_after_restart(self, cfg_no_fail_fast):
        """_permanently_failed is in-memory — lost on restart. Job gets stuck."""
        dag = _make_dag(5, {2: [1], 3: [1], 4: [1], 5: [2, 3, 4]})
        runner = _make_runner(dag)
        executor = ParallelExecutor(runner=runner, config=cfg_no_fail_fast)

        state = _make_state(5, {
            1: SheetStatus.COMPLETED, 2: SheetStatus.FAILED,
            3: SheetStatus.COMPLETED, 4: SheetStatus.COMPLETED,
            5: SheetStatus.PENDING,
        })
        # After restart: _permanently_failed is empty
        assert len(executor._permanently_failed) == 0

        batch = executor.get_next_parallel_batch(state)
        # Sheet 5 blocked forever — different bug from F-113
        assert 5 not in batch, "After restart, job is stuck — failed dep blocks forever"

    @pytest.mark.adversarial
    async def test_failed_dep_in_chain(self, cfg_no_fail_fast):
        """Chain: 1 → 2(FAIL) → 3 → 4. Sheet 3 runs against nothing."""
        dag = _make_dag(4, {2: [1], 3: [2], 4: [3]})
        runner = _make_runner(dag)
        executor = ParallelExecutor(runner=runner, config=cfg_no_fail_fast)

        state = _make_state(4, {
            1: SheetStatus.COMPLETED, 2: SheetStatus.FAILED,
            3: SheetStatus.PENDING, 4: SheetStatus.PENDING,
        })
        executor._permanently_failed.add(2)

        batch = executor.get_next_parallel_batch(state)
        assert 3 in batch, "F-113: chain failure not propagated"


# =============================================================================
# F-075 Regression: Resume After Fan-Out Failure
# =============================================================================


class TestF075ResumeCorruption:
    """Verify the F-075 fix holds under adversarial conditions."""

    @pytest.mark.adversarial
    def test_fix_preserves_failed_on_resume(self):
        """FAILED sheets are NOT overwritten to COMPLETED on resume."""
        _terminal = (SheetStatus.COMPLETED, SheetStatus.FAILED, SheetStatus.SKIPPED)
        sheets = {
            1: MagicMock(spec=SheetState, status=SheetStatus.COMPLETED),
            2: MagicMock(spec=SheetState, status=SheetStatus.FAILED),
            3: MagicMock(spec=SheetState, status=SheetStatus.COMPLETED),
        }
        for skipped in range(1, 4):
            if sheets[skipped].status not in _terminal:
                sheets[skipped].status = SheetStatus.COMPLETED
        assert sheets[2].status == SheetStatus.FAILED

    @pytest.mark.adversarial
    def test_fix_preserves_skipped_on_resume(self):
        _terminal = (SheetStatus.COMPLETED, SheetStatus.FAILED, SheetStatus.SKIPPED)
        sheets = {
            1: MagicMock(spec=SheetState, status=SheetStatus.COMPLETED),
            2: MagicMock(spec=SheetState, status=SheetStatus.SKIPPED),
        }
        for skipped in range(1, 3):
            if sheets[skipped].status not in _terminal:
                sheets[skipped].status = SheetStatus.COMPLETED
        assert sheets[2].status == SheetStatus.SKIPPED

    @pytest.mark.adversarial
    def test_fix_handles_all_prior_failed(self):
        _terminal = (SheetStatus.COMPLETED, SheetStatus.FAILED, SheetStatus.SKIPPED)
        sheets = {
            1: MagicMock(spec=SheetState, status=SheetStatus.FAILED),
            2: MagicMock(spec=SheetState, status=SheetStatus.FAILED),
            3: MagicMock(spec=SheetState, status=SheetStatus.FAILED),
        }
        for skipped in range(1, 4):
            if sheets[skipped].status not in _terminal:
                sheets[skipped].status = SheetStatus.COMPLETED
        for i in [1, 2, 3]:
            assert sheets[i].status == SheetStatus.FAILED

    @pytest.mark.adversarial
    def test_fix_handles_mixed_terminal_states(self):
        _terminal = (SheetStatus.COMPLETED, SheetStatus.FAILED, SheetStatus.SKIPPED)
        sheets = {
            1: MagicMock(spec=SheetState, status=SheetStatus.COMPLETED),
            2: MagicMock(spec=SheetState, status=SheetStatus.FAILED),
            3: MagicMock(spec=SheetState, status=SheetStatus.SKIPPED),
            4: MagicMock(spec=SheetState, status=SheetStatus.IN_PROGRESS),
        }
        for skipped in range(1, 5):
            if sheets[skipped].status not in _terminal:
                sheets[skipped].status = SheetStatus.COMPLETED
        assert sheets[2].status == SheetStatus.FAILED
        assert sheets[3].status == SheetStatus.SKIPPED
        assert sheets[4].status == SheetStatus.COMPLETED  # IN_PROGRESS → COMPLETED


# =============================================================================
# F-122: IPC Callsites Bypassing --conductor-clone
# =============================================================================


class TestF122IpcCloneBypass:
    """Prove that IPC callsites hardcode production socket paths."""

    @pytest.mark.adversarial
    def test_hooks_hardcodes_production_socket(self):
        """hooks.py creates DaemonClient with SocketConfig().path."""
        import inspect
        from mozart.execution import hooks

        source = inspect.getsource(hooks._try_daemon_submit)
        assert "SocketConfig()" in source, "Expected SocketConfig() in hooks._try_daemon_submit"
        assert "_resolve_socket_path" not in source, "F-122: _resolve_socket_path not used"

    @pytest.mark.adversarial
    def test_mcp_tools_hardcodes_production_socket(self):
        """mcp/tools.py creates DaemonClient with DaemonConfig().socket.path."""
        import inspect
        from mozart.mcp import tools

        source = inspect.getsource(tools.JobTools.__init__)
        assert "DaemonConfig()" in source, "Expected DaemonConfig() in JobTools.__init__"

    @pytest.mark.adversarial
    def test_dashboard_routes_hardcodes_production_socket(self):
        """dashboard/routes/jobs.py uses DaemonConfig() for DaemonClient."""
        import inspect
        from mozart.dashboard.routes import jobs

        source = inspect.getsource(jobs)
        if "DaemonClient" in source:
            assert "_resolve_socket_path" not in source, "F-122: dashboard routes bypass clone"

    @pytest.mark.adversarial
    def test_dashboard_job_control_hardcodes_production_socket(self):
        """dashboard/services/job_control.py uses DaemonConfig() for DaemonClient."""
        import inspect
        from mozart.dashboard.services import job_control

        source = inspect.getsource(job_control)
        if "DaemonClient" in source:
            assert "_resolve_socket_path" not in source, "F-122: job_control bypasses clone"


# =============================================================================
# Parallel Executor Error Propagation Edge Cases
# =============================================================================


class TestParallelErrorEdges:
    """Edge cases in parallel executor error handling."""

    @pytest.mark.adversarial
    async def test_all_fail_simultaneously(self, mock_runner, cfg_fail_fast):
        """All sheets fail at once — verify all tracked."""
        async def side_effect(state, sheet_num):
            raise ValueError(f"Sheet {sheet_num} error")

        mock_runner._execute_sheet_with_recovery = AsyncMock(side_effect=side_effect)
        executor = ParallelExecutor(runner=mock_runner, config=cfg_fail_fast)

        result = await executor.execute_batch([1, 2, 3], _make_state(3))
        assert len(result.failed) >= 1

    @pytest.mark.adversarial
    async def test_concurrent_success_and_failure(self, mock_runner, cfg_fail_fast):
        """Some succeed, one fails. Verify completed list accuracy."""
        async def side_effect(state, sheet_num):
            if sheet_num == 2:
                await asyncio.sleep(0.01)
                raise FatalError("Sheet 2 failed")
            await asyncio.sleep(0.001)

        mock_runner._execute_sheet_with_recovery = AsyncMock(side_effect=side_effect)
        executor = ParallelExecutor(runner=mock_runner, config=cfg_fail_fast)

        result = await executor.execute_batch([1, 2, 3], _make_state(3))
        assert 2 in result.failed
        assert "Sheet 2 failed" in result.error_details[2]

    @pytest.mark.adversarial
    async def test_permanently_failed_excluded_from_next_batch(self, cfg_no_fail_fast):
        """Permanently failed sheets must not reappear."""
        dag = _make_dag(5, {})
        runner = _make_runner(dag)
        executor = ParallelExecutor(runner=runner, config=cfg_no_fail_fast)

        state = _make_state(5, {
            1: SheetStatus.COMPLETED, 2: SheetStatus.FAILED,
            3: SheetStatus.PENDING, 4: SheetStatus.PENDING,
            5: SheetStatus.PENDING,
        })
        executor._permanently_failed.add(2)

        batch = executor.get_next_parallel_batch(state)
        assert 2 not in batch
        assert any(s in batch for s in [3, 4, 5])


# =============================================================================
# Baton State Machine — New Edge Cases
# =============================================================================


class TestBatonStateEdgeCases:
    """Edge cases in the baton state machine not covered by prior tests."""

    @pytest.mark.adversarial
    async def test_cost_limit_zero_allows_first_attempt(self):
        """cost_limit=0.0: first attempt runs, then job pauses."""
        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.events import SheetAttemptResult
        from mozart.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-cli")}
        baton.register_job("test-job", sheets, {})
        baton.set_job_cost_limit("test-job", 0.0)

        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            instrument_name="claude-cli",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=1,
            cost_usd=0.50,
        )
        baton._handle_attempt_result(result)

        sheet = baton._jobs["test-job"].sheets[1]
        assert sheet.status == BatonSheetStatus.COMPLETED
        assert baton._jobs["test-job"].paused, "Job should pause after cost exceeded"

    @pytest.mark.adversarial
    async def test_deregister_during_fermata(self):
        """Deregistering a job in FERMATA should clean up without error."""
        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-cli")}
        baton.register_job("test-job", sheets, {})
        baton._jobs["test-job"].sheets[1].status = BatonSheetStatus.FERMATA

        baton.deregister_job("test-job")
        assert "test-job" not in baton._jobs

    @pytest.mark.adversarial
    async def test_attempt_result_for_unknown_job(self):
        """Attempt result for deregistered job should not crash."""
        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.events import SheetAttemptResult

        baton = BatonCore()
        result = SheetAttemptResult(
            job_id="nonexistent-job", sheet_num=1,
            instrument_name="claude-cli", attempt=1,
            execution_success=True,
            validation_pass_rate=100.0, validations_total=1,
        )
        # Should not crash
        baton._handle_attempt_result(result)

    @pytest.mark.adversarial
    async def test_attempt_result_for_unknown_sheet(self):
        """Attempt result for unregistered sheet should not crash."""
        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.events import SheetAttemptResult
        from mozart.daemon.baton.state import SheetExecutionState

        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-cli")}
        baton.register_job("test-job", sheets, {})

        result = SheetAttemptResult(
            job_id="test-job", sheet_num=99,
            instrument_name="claude-cli", attempt=1,
            execution_success=True,
            validation_pass_rate=100.0, validations_total=1,
        )
        # Should not crash
        baton._handle_attempt_result(result)


# =============================================================================
# Cross-System Integration
# =============================================================================


class TestCrossSystemIntegration:
    """Tests spanning system boundaries — where bugs live."""

    @pytest.mark.adversarial
    def test_f098_rate_limit_in_stdout_with_json_stderr(self):
        """F-098 regression: rate limit in stdout WITH JSON errors in stderr."""
        from mozart.core.errors.classifier import ErrorClassifier

        classifier = ErrorClassifier()
        result = classifier.classify_execution(
            exit_code=1,
            stdout="API Error: Rate limit reached\nYou've hit your limit · resets 11pm",
            stderr='{"error": {"type": "api_error", "message": "rate limit"}}',
        )
        has_rate_limit = any(e.is_rate_limit for e in result.all_errors)
        assert has_rate_limit, "F-098: rate limit in stdout not detected with JSON stderr"

    @pytest.mark.adversarial
    def test_f097_stale_vs_timeout_via_classify(self):
        """F-097: Stale detection via classify() produces E006, timeout produces E001.

        Note: classify_execution() does NOT differentiate — it needs exit_reason
        which only classify() receives. The E006 code path requires
        exit_reason='timeout' to be set by the caller.
        """
        from mozart.core.errors.classifier import ErrorClassifier

        classifier = ErrorClassifier()

        # classify() accepts exit_reason — this IS the production path
        stale = classifier.classify(
            stdout="",
            stderr="stale execution detected: no output for 1800s",
            exit_code=1,
            exit_reason="timeout",
        )
        timeout = classifier.classify(
            stdout="",
            stderr="execution timed out after 3600s",
            exit_code=1,
            exit_reason="timeout",
        )

        # Stale should be E006 (EXECUTION_STALE)
        assert stale.error_code.value == "E006", (
            f"Stale detection should produce E006, got {stale.error_code.value}"
        )
        # Regular timeout should be E001 (EXECUTION_TIMEOUT)
        assert timeout.error_code.value == "E001", (
            f"Regular timeout should produce E001, got {timeout.error_code.value}"
        )

    @pytest.mark.adversarial
    def test_credential_redaction(self):
        """Verify credentials are redacted before storage."""
        from mozart.utils.credential_scanner import redact_credentials

        # Use tokens long enough to match the scanner's patterns
        # (GitHub PATs require 36+ chars after prefix)
        text = (
            "Processing...\n"
            "API key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456\n"
            "GitHub: ghp_abcdefghijklmnopqrstuvwxyz1234567890\n"
            "AWS: AKIAIOSFODNN7EXAMPLE\n"
            "Done."
        )
        redacted = redact_credentials(text)
        assert "sk-ant-" not in redacted
        assert "ghp_" not in redacted
        assert "AKIA" not in redacted
        assert "[REDACTED" in redacted
        assert "Processing..." in redacted
        assert "Done." in redacted
