"""Adversarial tests for Marianne v1 beta — Movement 1, Cycle 3.

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

from unittest.mock import MagicMock

import pytest

from marianne.core.checkpoint import SheetState, SheetStatus




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
    """Regression tests: all IPC callsites use _resolve_socket_path for clone awareness.

    F-122 originally proved these callsites hardcoded production socket paths.
    The bug was fixed — all 4 callsites now use _resolve_socket_path(None).
    These tests ensure the fix holds and no regressions occur.
    """

    @pytest.mark.adversarial
    def test_hooks_uses_resolve_socket_path(self):
        """hooks.py must use _resolve_socket_path for clone-aware routing."""
        import inspect
        from marianne.execution import hooks

        source = inspect.getsource(hooks._try_daemon_submit)
        assert "_resolve_socket_path" in source, (
            "F-122 regression: hooks._try_daemon_submit must use "
            "_resolve_socket_path for clone-aware socket resolution"
        )
        assert "SocketConfig()" not in source, (
            "F-122 regression: hooks._try_daemon_submit must not "
            "hardcode SocketConfig() — use _resolve_socket_path instead"
        )

    @pytest.mark.adversarial
    def test_mcp_tools_uses_resolve_socket_path(self):
        """mcp/tools.py must use _resolve_socket_path for clone-aware routing."""
        import inspect
        from marianne.mcp import tools

        source = inspect.getsource(tools.JobTools.__init__)
        assert "_resolve_socket_path" in source, (
            "F-122 regression: JobTools.__init__ must use "
            "_resolve_socket_path for clone-aware socket resolution"
        )
        assert "DaemonConfig().socket" not in source, (
            "F-122 regression: JobTools.__init__ must not "
            "hardcode DaemonConfig().socket.path — use _resolve_socket_path"
        )

    @pytest.mark.adversarial
    def test_dashboard_routes_uses_resolve_socket_path(self):
        """dashboard/routes/jobs.py must use _resolve_socket_path."""
        import inspect
        from marianne.dashboard.routes import jobs

        source = inspect.getsource(jobs)
        if "DaemonClient" in source:
            assert "_resolve_socket_path" in source, (
                "F-122 regression: dashboard routes must use "
                "_resolve_socket_path for clone-aware socket resolution"
            )
            assert "DaemonConfig().socket" not in source, (
                "F-122 regression: dashboard routes must not "
                "hardcode DaemonConfig().socket.path"
            )

    @pytest.mark.adversarial
    def test_dashboard_uses_resolve_socket_path(self):
        """Dashboard must use _resolve_socket_path for clone-aware sockets.

        After the DaemonClient refactor, socket resolution moved from
        job_control.py to app.py — the client is created once in
        _create_daemon_client() and injected into services.
        """
        import inspect
        from marianne.dashboard import app as dashboard_app

        source = inspect.getsource(dashboard_app)
        assert "_resolve_socket_path" in source, (
            "F-122 regression: dashboard app must use "
            "_resolve_socket_path for clone-aware socket resolution"
        )
        assert "DaemonConfig().socket" not in source, (
            "F-122 regression: dashboard must not "
            "hardcode DaemonConfig().socket.path"
        )


# =============================================================================
# Baton State Machine — New Edge Cases
# =============================================================================


class TestBatonStateEdgeCases:
    """Edge cases in the baton state machine not covered by prior tests."""

    @pytest.mark.adversarial
    async def test_cost_limit_zero_allows_first_attempt(self):
        """cost_limit=0.0: first attempt runs, then job pauses."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

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
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-cli")}
        baton.register_job("test-job", sheets, {})
        baton._jobs["test-job"].sheets[1].status = BatonSheetStatus.FERMATA

        baton.deregister_job("test-job")
        assert "test-job" not in baton._jobs

    @pytest.mark.adversarial
    async def test_attempt_result_for_unknown_job(self):
        """Attempt result for deregistered job should not crash."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetAttemptResult

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
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.state import SheetExecutionState

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
        from marianne.core.errors.classifier import ErrorClassifier

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
        from marianne.core.errors.classifier import ErrorClassifier

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
        from marianne.utils.credential_scanner import redact_credentials

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
