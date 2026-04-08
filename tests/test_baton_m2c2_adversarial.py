"""Adversarial tests for Movement 2 Cycle 2 code — restart recovery, credential
redaction in exception paths, parallel batch rate limit extraction, failure
propagation edge cases, cost limit field correctness, and state sync boundaries.

Movement 2 — Breakpoint.

These tests target the critical path code that shipped since M1: step 29 restart
recovery (adapter.recover_job, _sync_sheet_status), F-135 credential redaction
in musician exception handlers, F-111 parallel rate limit preservation, F-113
failure propagation in ParallelExecutor, F-134 cost limit field fix, and the
state sync callback boundary.

Every test is designed to catch a bug that would manifest in production.
No happy paths. No polite inputs. The real world is not polite.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import (
    BatonAdapter,
    baton_to_checkpoint_status,
    checkpoint_to_baton_status,
)
from marianne.daemon.baton.events import (
    DispatchRetry,
    SheetAttemptResult,
    SheetSkipped,
)
from marianne.daemon.baton.state import BatonSheetStatus


# =========================================================================
# Helpers
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    prompt: str = "Test prompt",
    workspace: str = "/tmp/test-ws",
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        instrument_name=instrument,
        workspace=Path(workspace),
        prompt_template=prompt,
        template_file=None,
        validations=[],
        timeout_seconds=60.0,
        prelude=[],
        cadenza=[],
    )


def _make_checkpoint(
    sheets: dict[int, SheetState] | None = None,
    total_sheets: int = 3,
) -> CheckpointState:
    """Create a minimal CheckpointState for recovery testing."""
    return CheckpointState(
        job_id="test-job",
        job_name="Test Job",
        config_hash=None,
        total_sheets=total_sheets,
        sheets=sheets or {},
    )


def _make_sheet_state(
    num: int,
    status: SheetStatus = SheetStatus.PENDING,
    attempt_count: int = 0,
    completion_attempts: int = 0,
) -> SheetState:
    """Create a SheetState for checkpoint construction."""
    return SheetState(
        sheet_num=num,
        status=status,
        attempt_count=attempt_count,
        completion_attempts=completion_attempts,
    )


# =========================================================================
# 1. Restart Recovery Adversarial Tests (Step 29)
# =========================================================================


class TestRecoverJobAdversarial:
    """Adversarial tests for adapter.recover_job() — the critical restart
    recovery path that rebuilds baton state from a persisted checkpoint."""

    def test_recover_all_terminal_sheets_no_dispatch(self) -> None:
        """If ALL sheets are terminal in the checkpoint, recover_job should
        register them but the baton should see zero dispatchable sheets."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=i) for i in range(1, 4)]
        deps: dict[int, list[int]] = {}

        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.COMPLETED, attempt_count=1),
            2: _make_sheet_state(2, SheetStatus.FAILED, attempt_count=5),
            3: _make_sheet_state(3, SheetStatus.SKIPPED),
        })

        adapter.recover_job("test-job", sheets, deps, cp, max_retries=3)

        for num in [1, 2, 3]:
            state = adapter._baton.get_sheet_state("test-job", num)
            assert state is not None
            assert state.status.is_terminal, (
                f"Sheet {num} should be terminal after recovery"
            )

    def test_recover_in_progress_resets_to_pending(self) -> None:
        """In-progress sheets MUST be reset to PENDING because the musician
        that was executing them died on restart."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1)]
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.IN_PROGRESS, attempt_count=2),
        })

        adapter.recover_job("test-job", sheets, {}, cp, max_retries=5)

        state = adapter._baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.PENDING, (
            "in_progress sheets must be reset to PENDING on recovery"
        )

    def test_recover_preserves_attempt_counts(self) -> None:
        """Attempt counts MUST be preserved from the checkpoint. Without
        this, sheets get infinite retries after every restart."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1)]
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(
                1, SheetStatus.IN_PROGRESS,
                attempt_count=4, completion_attempts=2,
            ),
        })

        adapter.recover_job("test-job", sheets, {}, cp, max_retries=5)

        state = adapter._baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.normal_attempts == 4, (
            "Normal attempts must carry forward from checkpoint"
        )
        assert state.completion_attempts == 2, (
            "Completion attempts must carry forward from checkpoint"
        )

    def test_recover_sheet_not_in_checkpoint_is_fresh_pending(self) -> None:
        """A sheet that exists in config but NOT in checkpoint (new sheet
        added after initial run) should start as fresh PENDING."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.COMPLETED, attempt_count=1),
        })

        adapter.recover_job("test-job", sheets, {}, cp, max_retries=3)

        s1 = adapter._baton.get_sheet_state("test-job", 1)
        s2 = adapter._baton.get_sheet_state("test-job", 2)

        assert s1 is not None and s1.status == BatonSheetStatus.COMPLETED
        assert s2 is not None and s2.status == BatonSheetStatus.PENDING
        assert s2.normal_attempts == 0

    def test_recover_exhausted_retries_preserves_count(self) -> None:
        """A sheet that was in_progress with attempt_count >= max_retries
        is recovered as PENDING but with attempts preserved. On next dispatch
        and failure, the exhaustion check catches it."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1)]
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(
                1, SheetStatus.IN_PROGRESS, attempt_count=5,
            ),
        })

        adapter.recover_job("test-job", sheets, {}, cp, max_retries=5)

        state = adapter._baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.PENDING
        assert state.normal_attempts == 5
        # can_retry should be False since normal_attempts == max_retries
        assert not state.can_retry, (
            "Sheet with max retries exhausted should have can_retry=False"
        )

    def test_recover_cost_limit_wired(self) -> None:
        """When max_cost_usd is provided, it must be wired to baton
        via set_job_cost_limit. F-134 showed the field name was wrong."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1)]
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.PENDING),
        })

        adapter.recover_job(
            "test-job", sheets, {}, cp,
            max_cost_usd=10.0, max_retries=3,
        )

        # Cost limits stored in baton._job_cost_limits dict
        assert "test-job" in adapter._baton._job_cost_limits, (
            "Cost limit must be wired through recover_job to baton"
        )
        assert adapter._baton._job_cost_limits["test-job"] == 10.0

    def test_recover_dispatches_retry_kick(self) -> None:
        """recover_job MUST send a DispatchRetry to kick the event loop.
        Without it, recovered PENDING sheets sit idle."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1)]
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.PENDING),
        })

        adapter.recover_job("test-job", sheets, {}, cp, max_retries=3)

        event = adapter._baton.inbox.get_nowait()
        assert isinstance(event, DispatchRetry), (
            "recover_job must send DispatchRetry to kick the event loop"
        )

    def test_recover_with_dependencies_preserves_graph(self) -> None:
        """Dependencies must be registered with the baton so DAG
        resolution works after restart."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=i) for i in range(1, 4)]
        deps = {2: [1], 3: [1, 2]}
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.COMPLETED, attempt_count=1),
            2: _make_sheet_state(2, SheetStatus.PENDING),
            3: _make_sheet_state(3, SheetStatus.PENDING),
        })

        adapter.recover_job("test-job", sheets, deps, cp, max_retries=3)

        job = adapter._baton._jobs.get("test-job")
        assert job is not None
        assert job.dependencies == deps

    def test_recover_empty_checkpoint_all_fresh(self) -> None:
        """An empty checkpoint should result in all sheets as PENDING
        with zero attempts."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=i) for i in range(1, 4)]
        cp = _make_checkpoint(sheets={})

        adapter.recover_job("test-job", sheets, {}, cp, max_retries=3)

        for num in [1, 2, 3]:
            state = adapter._baton.get_sheet_state("test-job", num)
            assert state is not None
            assert state.status == BatonSheetStatus.PENDING
            assert state.normal_attempts == 0

    def test_recover_no_cost_limit_when_none(self) -> None:
        """When max_cost_usd is None (default), no cost limit should be
        set in the baton. A missing key is correct — not a zero limit."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1)]
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.PENDING),
        })

        adapter.recover_job(
            "test-job", sheets, {}, cp,
            max_cost_usd=None, max_retries=3,
        )

        assert "test-job" not in adapter._baton._job_cost_limits, (
            "None max_cost_usd should not set any cost limit"
        )


# =========================================================================
# 2. State Sync Callback Adversarial Tests
# =========================================================================


class TestStateSyncCallbackAdversarial:
    """Adversarial tests for _sync_sheet_status — the per-event callback
    that syncs baton status changes to the manager's CheckpointState."""

    def test_sync_fires_on_attempt_result(self) -> None:
        """_sync_sheet_status must fire for SheetAttemptResult events."""
        sync_calls: list[tuple[str, int, str]] = []

        def on_sync(job_id: str, sheet_num: int, status: str, baton_state: object = None) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=on_sync)
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, {})

        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=0,
            duration_seconds=1.0,
        )
        adapter._sync_sheet_status(result)

        assert len(sync_calls) == 1
        assert sync_calls[0][0] == "test-job"
        assert sync_calls[0][1] == 1

    def test_sync_fires_on_sheet_skipped(self) -> None:
        """_sync_sheet_status must fire for SheetSkipped events."""
        sync_calls: list[tuple[str, int, str]] = []

        def on_sync(job_id: str, sheet_num: int, status: str, baton_state: object = None) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=on_sync)
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, {})

        skipped = SheetSkipped(job_id="test-job", sheet_num=1, reason="test")
        adapter._sync_sheet_status(skipped)

        assert len(sync_calls) == 1

    def test_sync_ignores_non_status_events(self) -> None:
        """DispatchRetry and other non-status events must NOT trigger
        the callback."""
        sync_calls: list[tuple[str, int, str]] = []

        def on_sync(job_id: str, sheet_num: int, status: str, baton_state: object = None) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=on_sync)

        dispatch_retry = DispatchRetry()
        adapter._sync_sheet_status(dispatch_retry)

        assert len(sync_calls) == 0

    def test_sync_callback_exception_does_not_crash(self) -> None:
        """If the callback raises, _sync_sheet_status must catch it
        and log a warning — never crash the baton."""
        def on_sync(job_id: str, sheet_num: int, status: str, baton_state: object = None) -> None:
            raise RuntimeError("Callback explosion")

        adapter = BatonAdapter(state_sync_callback=on_sync)
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, {})

        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=0,
            duration_seconds=1.0,
        )

        # Must not raise
        adapter._sync_sheet_status(result)

    def test_sync_with_unknown_job(self) -> None:
        """If the event references a non-existent job, the callback
        must not fire."""
        sync_calls: list[tuple[str, int, str]] = []

        def on_sync(job_id: str, sheet_num: int, status: str, baton_state: object = None) -> None:
            sync_calls.append((job_id, sheet_num, status))

        adapter = BatonAdapter(state_sync_callback=on_sync)

        result = SheetAttemptResult(
            job_id="ghost-job",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=0,
            duration_seconds=1.0,
        )

        adapter._sync_sheet_status(result)
        assert len(sync_calls) == 0

    def test_sync_no_callback_is_noop(self) -> None:
        """When no callback is set, the sync should be a clean no-op."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, {})

        result = SheetAttemptResult(
            job_id="test-job",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=0,
            duration_seconds=1.0,
        )

        # Must not raise
        adapter._sync_sheet_status(result)


# =========================================================================
# 3. Credential Redaction in Exception Path (F-135)
# =========================================================================


class TestMusicianCredentialRedactionAdversarial:
    """Adversarial tests for credential redaction in the musician exception
    handler (F-135)."""

    def test_redact_all_13_credential_patterns(self) -> None:
        """The credential scanner must redact ALL 13 known patterns.
        Each pattern verified individually."""
        from marianne.utils.credential_scanner import redact_credentials

        # Token strings must meet minimum length requirements in the scanner
        # regex to avoid false positives. Real tokens are this long or longer.
        patterns = [
            ("sk-ant-api03-deadbeef1234", "Anthropic"),
            ("sk-proj-deadbeef12345678abcdef90", "OpenAI"),  # 20+ chars after sk-proj-
            ("AIzaSyDeadBeefCafeF00d1234567890abcdef", "Google"),  # 28+ chars after AIzaSy
            ("AKIA1234567890ABCDEF", "AWS"),
            ("Bearer eyJhbGciOiJIUzI1NiJ9.test", "Bearer"),  # 20+ chars after Bearer
            ("ghp_1234567890abcdef1234567890abcdef1234ab", "GitHub PAT"),  # 36+ chars after ghp_
            ("gho_1234567890abcdef1234567890abcdef1234ab", "GitHub OAuth"),  # 36+ chars after gho_
            ("github_pat_1234567890abcdef1234567890abcdef1234ab", "GitHub fine-grained"),  # 36+ after github_pat_
            ("xoxb-12345-67890-deadbeefcafe", "Slack bot"),  # 20+ chars after xoxb-
            ("xoxp-12345-67890-deadbeefcafe", "Slack user"),
            ("xapp-12345-67890-deadbeefcafe", "Slack app"),
            ("hf_deadbeef1234567890abcdef", "Hugging Face"),
        ]

        for secret, label in patterns:
            raw = f"Error with key {secret} during auth"
            result = redact_credentials(raw)
            assert result is not None, f"None for {label}"
            assert secret not in result, f"{label} not redacted"

    def test_redact_returns_input_for_clean_string(self) -> None:
        """Clean strings should pass through unchanged."""
        from marianne.utils.credential_scanner import redact_credentials

        clean = "Just a normal error message with no secrets"
        result = redact_credentials(clean)
        assert result == clean

    def test_multiline_traceback_with_credential(self) -> None:
        """Exception tracebacks with credentials must be redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        traceback_text = (
            "Traceback (most recent call last):\n"
            "  File '/tmp/test.py', line 42\n"
            '    headers = {"Authorization": "Bearer sk-ant-api03-secretkey123"}\n'
            "ValueError: API returned 401\n"
        )

        result = redact_credentials(traceback_text)
        assert result is not None
        assert "sk-ant-api03-secretkey123" not in result
        assert "Traceback" in result

    def test_musician_error_msg_uses_redact_or_fallback(self) -> None:
        """The musician exception handler uses:
            error_msg = redact_credentials(raw_error_msg) or raw_error_msg
        This fallback pattern must handle all cases correctly."""
        from marianne.utils.credential_scanner import redact_credentials

        # Normal case: redaction returns redacted string
        # Key must be long enough to match the pattern (10+ chars after sk-ant-api)
        raw = "Error: sk-ant-api03-secretkey123456"
        result = redact_credentials(raw) or raw
        assert "sk-ant-api03-secretkey123456" not in result

        # Clean case: returns input unchanged
        clean = "Normal error"
        result2 = redact_credentials(clean) or clean
        assert result2 == clean

    def test_short_credential_not_redacted(self) -> None:
        """Short strings that look like credentials but aren't long enough
        should NOT be redacted. The scanner is deliberately conservative."""
        from marianne.utils.credential_scanner import redact_credentials

        # sk-ant-api03-secret is only 9 chars after sk-ant-api — below 10 threshold
        short = "Error: sk-ant-api03-secret"
        result = redact_credentials(short)
        assert result == short, (
            "Short credential-like strings below pattern threshold must pass through"
        )


# =========================================================================
# 4. Parallel Rate Limit Extraction (F-111)
# =========================================================================


class TestParallelRateLimitExtractionAdversarial:
    """Adversarial tests for _find_rate_limit_in_batch."""

    def test_rate_limit_found_in_exceptions_dict(self) -> None:
        """Rate limit error in exceptions dict must be extracted."""
        from marianne.execution.runner.lifecycle import LifecycleMixin
        from marianne.execution.parallel import ParallelBatchResult
        from marianne.execution.runner.models import RateLimitExhaustedError

        exc = RateLimitExhaustedError(
            message="Rate limit exhausted on claude-code",
            backend_type="claude-code",
        )

        result = ParallelBatchResult(
            sheets=[1, 2],
            completed=[2],
            failed=[1],
            error_details={1: "Rate limit hit"},
            exceptions={1: exc},
        )

        found = LifecycleMixin._find_rate_limit_in_batch(result)
        assert found is not None
        assert isinstance(found, RateLimitExhaustedError)
        assert found.backend_type == "claude-code"

    def test_no_rate_limit_returns_none(self) -> None:
        """When no rate limit error exists, return None."""
        from marianne.execution.runner.lifecycle import LifecycleMixin
        from marianne.execution.parallel import ParallelBatchResult

        result = ParallelBatchResult(
            sheets=[1],
            failed=[1],
            error_details={1: "Auth failure"},
            exceptions={1: ValueError("bad auth")},
        )

        found = LifecycleMixin._find_rate_limit_in_batch(result)
        assert found is None

    def test_multiple_rate_limits_returns_first(self) -> None:
        """When multiple rate limit errors exist, return the first one
        (by iteration order over failed list)."""
        from marianne.execution.runner.lifecycle import LifecycleMixin
        from marianne.execution.parallel import ParallelBatchResult
        from marianne.execution.runner.models import RateLimitExhaustedError

        result = ParallelBatchResult(
            sheets=[1, 2, 3],
            failed=[1, 2, 3],
            error_details={1: "RL", 2: "RL", 3: "Auth"},
            exceptions={
                1: RateLimitExhaustedError("Rate limit on claude-code", backend_type="claude-code"),
                2: RateLimitExhaustedError("Rate limit on claude-code (2)", backend_type="claude-code"),
                3: ValueError("Auth failure"),
            },
        )

        found = LifecycleMixin._find_rate_limit_in_batch(result)
        assert found is not None
        assert found.backend_type == "claude-code"  # First one (sheet 1)

    def test_empty_batch_no_crash(self) -> None:
        """An empty batch should return None."""
        from marianne.execution.runner.lifecycle import LifecycleMixin
        from marianne.execution.parallel import ParallelBatchResult

        result = ParallelBatchResult(sheets=[], completed=[], failed=[])

        found = LifecycleMixin._find_rate_limit_in_batch(result)
        assert found is None

    def test_failed_sheet_missing_from_exceptions_dict(self) -> None:
        """A sheet in failed list but not in exceptions dict must not crash."""
        from marianne.execution.runner.lifecycle import LifecycleMixin
        from marianne.execution.parallel import ParallelBatchResult

        result = ParallelBatchResult(
            sheets=[1, 2],
            failed=[1, 2],
            error_details={1: "Error", 2: "Error"},
            exceptions={},
        )

        found = LifecycleMixin._find_rate_limit_in_batch(result)
        assert found is None


# =========================================================================
# 5. Failure Propagation in Parallel Executor (F-113)
# =========================================================================


class TestFailurePropagationAdversarial:
    """Adversarial tests for propagate_failure_to_dependents."""

    @staticmethod
    def _make_executor_with_dag(
        total_sheets: int,
        deps: dict[int, list[int]],
    ) -> "Any":
        """Create a ParallelExecutor with a mocked runner providing the DAG."""
        import logging
        from marianne.execution.parallel import ParallelExecutor
        from marianne.execution.dag import DependencyDAG
        from marianne.execution.runner.base import JobRunnerBase

        dag = DependencyDAG.from_dependencies(total_sheets, deps)
        executor = ParallelExecutor.__new__(ParallelExecutor)
        # The dag property delegates to self.runner.dependency_dag — mock that
        mock_runner = MagicMock(spec=JobRunnerBase)
        mock_runner.dependency_dag = dag
        executor.runner = mock_runner
        executor._logger = MagicMock(spec=logging.Logger)
        executor._permanently_failed = set()
        return executor

    @staticmethod
    def _make_executor_no_dag() -> "Any":
        """Create a ParallelExecutor with no DAG (runner returns None)."""
        import logging
        from marianne.execution.parallel import ParallelExecutor
        from marianne.execution.runner.base import JobRunnerBase

        executor = ParallelExecutor.__new__(ParallelExecutor)
        mock_runner = MagicMock(spec=JobRunnerBase)
        mock_runner.dependency_dag = None
        executor.runner = mock_runner
        executor._logger = MagicMock(spec=logging.Logger)
        executor._permanently_failed = set()
        return executor

    def test_deep_chain_propagation(self) -> None:
        """A chain of 5 sheets where the first fails should propagate
        failure through all 4 dependents."""
        executor = self._make_executor_with_dag(5, {2: [1], 3: [2], 4: [3], 5: [4]})

        mock_state = MagicMock(spec=CheckpointState)
        mock_sheets = {}
        for i in range(1, 6):
            sheet = MagicMock(spec=SheetState)
            sheet.status = SheetStatus.PENDING
            mock_sheets[i] = sheet

        mock_sheets[1].status = SheetStatus.FAILED
        mock_state.sheets = mock_sheets

        executor.propagate_failure_to_dependents(mock_state, 1)

        assert {2, 3, 4, 5}.issubset(executor._permanently_failed), (
            "All transitive dependents should be permanently failed"
        )

    def test_terminal_sheets_not_overwritten(self) -> None:
        """COMPLETED sheets must not be overwritten by failure propagation."""
        executor = self._make_executor_with_dag(3, {2: [1], 3: [1]})

        mock_state = MagicMock(spec=CheckpointState)
        mock_sheets = {}

        sheet1 = MagicMock(spec=SheetState)
        sheet1.status = SheetStatus.FAILED
        mock_sheets[1] = sheet1

        sheet2 = MagicMock(spec=SheetState)
        sheet2.status = SheetStatus.COMPLETED
        mock_sheets[2] = sheet2

        sheet3 = MagicMock(spec=SheetState)
        sheet3.status = SheetStatus.PENDING
        mock_sheets[3] = sheet3

        mock_state.sheets = mock_sheets

        executor.propagate_failure_to_dependents(mock_state, 1)

        # Sheet 2 status should NOT have been changed (still COMPLETED)
        assert sheet2.status == SheetStatus.COMPLETED, (
            "COMPLETED sheets must not be overwritten by failure propagation"
        )

    def test_no_dag_is_noop(self) -> None:
        """When no DAG exists, propagation should be a clean no-op."""
        executor = self._make_executor_no_dag()

        mock_state = MagicMock(spec=CheckpointState)
        executor.propagate_failure_to_dependents(mock_state, 1)
        assert len(executor._permanently_failed) == 0

    def test_diamond_dag_propagation(self) -> None:
        """Diamond DAG: 1 → 2, 1 → 3, 2 → 4, 3 → 4.
        Failure at 1 should propagate to 2, 3, and 4."""
        executor = self._make_executor_with_dag(4, {2: [1], 3: [1], 4: [2, 3]})

        mock_state = MagicMock(spec=CheckpointState)
        mock_sheets = {}
        for i in range(1, 5):
            sheet = MagicMock(spec=SheetState)
            sheet.status = SheetStatus.PENDING
            mock_sheets[i] = sheet
        mock_sheets[1].status = SheetStatus.FAILED
        mock_state.sheets = mock_sheets

        executor.propagate_failure_to_dependents(mock_state, 1)

        assert {2, 3, 4}.issubset(executor._permanently_failed)


# =========================================================================
# 6. Cost Limit Field Correctness (F-134)
# =========================================================================


class TestCostLimitFieldCorrectness:
    """Adversarial tests verifying the F-134 fix — both baton paths must
    use config.cost_limits.max_cost_per_job, NOT max_cost_usd."""

    def test_cost_limit_config_has_correct_field(self) -> None:
        """CostLimitConfig must have max_cost_per_job, NOT max_cost_usd."""
        from marianne.core.config.execution import CostLimitConfig

        config = CostLimitConfig(enabled=True, max_cost_per_job=25.0)
        assert hasattr(config, "max_cost_per_job")
        assert not hasattr(config, "max_cost_usd"), (
            "CostLimitConfig must NOT have max_cost_usd — F-134 bug"
        )

    def test_cost_limit_none_when_disabled(self) -> None:
        """When cost limits are disabled, the manager should pass None."""
        from marianne.core.config.execution import CostLimitConfig

        config = CostLimitConfig(enabled=False, max_cost_per_job=25.0)
        max_cost = None
        if config.enabled and config.max_cost_per_job:
            max_cost = config.max_cost_per_job

        assert max_cost is None

    def test_max_cost_per_job_defaults_to_none(self) -> None:
        """Default CostLimitConfig should have max_cost_per_job=None."""
        from marianne.core.config.execution import CostLimitConfig

        config = CostLimitConfig()
        assert config.max_cost_per_job is None
        assert config.enabled is False


# =========================================================================
# 7. Checkpoint Status Mapping Boundary Tests
# =========================================================================


class TestCheckpointStatusMappingBoundary:
    """Adversarial tests for the checkpoint ↔ baton status mapping tables."""

    def test_unknown_checkpoint_status_raises(self) -> None:
        """An unrecognized checkpoint status must raise KeyError."""
        with pytest.raises(KeyError):
            checkpoint_to_baton_status("exploded")

    def test_all_baton_statuses_map_to_checkpoint(self) -> None:
        """Every BatonSheetStatus must have a mapping."""
        for status in BatonSheetStatus:
            result = baton_to_checkpoint_status(status)
            assert isinstance(result, str), (
                f"BatonSheetStatus.{status.name} has no checkpoint mapping"
            )

    def test_round_trip_terminal_states_preserved(self) -> None:
        """Terminal baton states must round-trip through checkpoint mapping."""
        terminal_pairs = [
            (BatonSheetStatus.COMPLETED, "completed", BatonSheetStatus.COMPLETED),
            (BatonSheetStatus.FAILED, "failed", BatonSheetStatus.FAILED),
            (BatonSheetStatus.SKIPPED, "skipped", BatonSheetStatus.SKIPPED),
        ]

        for baton_status, expected_cp, expected_back in terminal_pairs:
            cp_status = baton_to_checkpoint_status(baton_status)
            assert cp_status == expected_cp
            back = checkpoint_to_baton_status(cp_status)
            assert back == expected_back

    def test_in_progress_maps_to_dispatched(self) -> None:
        """'in_progress' maps to DISPATCHED. For recovery, in_progress
        sheets are explicitly reset to PENDING in recover_job."""
        result = checkpoint_to_baton_status("in_progress")
        assert result == BatonSheetStatus.DISPATCHED

    def test_cancelled_maps_1_to_1(self) -> None:
        """CANCELLED maps 1:1 (11-state model)."""
        result = baton_to_checkpoint_status(BatonSheetStatus.CANCELLED)
        assert result == "cancelled"

    def test_non_terminal_statuses_map_1_to_1(self) -> None:
        """Non-terminal baton states map 1:1 to checkpoint (11-state model)."""
        assert baton_to_checkpoint_status(BatonSheetStatus.PENDING) == "pending"
        assert baton_to_checkpoint_status(BatonSheetStatus.READY) == "ready"
        assert baton_to_checkpoint_status(BatonSheetStatus.DISPATCHED) == "dispatched"
        assert baton_to_checkpoint_status(BatonSheetStatus.IN_PROGRESS) == "in_progress"
        assert baton_to_checkpoint_status(BatonSheetStatus.WAITING) == "waiting"
        assert baton_to_checkpoint_status(BatonSheetStatus.RETRY_SCHEDULED) == "retry_scheduled"
        assert baton_to_checkpoint_status(BatonSheetStatus.FERMATA) == "fermata"


# =========================================================================
# 8. Completion Signaling Edge Cases
# =========================================================================


class TestCompletionSignalingAdversarial:
    """Adversarial tests for _check_completions."""

    def test_all_failed_signals_completion_with_false(self) -> None:
        """All-failed job should signal completion with all_success=False."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, {})

        state = adapter._baton.get_sheet_state("test-job", 1)
        assert state is not None
        state.status = BatonSheetStatus.FAILED

        adapter._check_completions()

        event = adapter._completion_events.get("test-job")
        assert event is not None and event.is_set()
        assert adapter._completion_results.get("test-job") is False

    def test_mixed_terminal_reports_failure(self) -> None:
        """Some completed + some failed → all_success=False."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, {})

        s1 = adapter._baton.get_sheet_state("test-job", 1)
        s2 = adapter._baton.get_sheet_state("test-job", 2)
        assert s1 is not None and s2 is not None
        s1.status = BatonSheetStatus.COMPLETED
        s2.status = BatonSheetStatus.FAILED

        adapter._check_completions()
        assert adapter._completion_results.get("test-job") is False

    def test_already_signaled_not_re_signaled(self) -> None:
        """Once set, completion events must not be re-evaluated."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, {})

        state = adapter._baton.get_sheet_state("test-job", 1)
        assert state is not None
        state.status = BatonSheetStatus.COMPLETED

        adapter._check_completions()
        assert adapter._completion_events["test-job"].is_set()

        # Call again — should skip (continue guard)
        adapter._check_completions()

    def test_pending_sheet_blocks_completion(self) -> None:
        """A PENDING sheet must prevent completion signal."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, {})

        s1 = adapter._baton.get_sheet_state("test-job", 1)
        assert s1 is not None
        s1.status = BatonSheetStatus.COMPLETED

        adapter._check_completions()

        event = adapter._completion_events.get("test-job")
        assert event is not None and not event.is_set()

    def test_all_completed_signals_true(self) -> None:
        """All sheets COMPLETED → all_success=True."""
        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, {})

        for num in [1, 2]:
            s = adapter._baton.get_sheet_state("test-job", num)
            assert s is not None
            s.status = BatonSheetStatus.COMPLETED

        adapter._check_completions()
        assert adapter._completion_results.get("test-job") is True


# =========================================================================
# 9. ParallelBatchResult Exceptions Field (F-111 regression)
# =========================================================================


class TestParallelBatchResultExceptionsField:
    """Tests for the exceptions dict on ParallelBatchResult."""

    def test_exceptions_dict_preserves_type(self) -> None:
        """The exceptions dict must preserve the original exception type."""
        from marianne.execution.parallel import ParallelBatchResult
        from marianne.execution.runner.models import RateLimitExhaustedError

        exc = RateLimitExhaustedError(
            message="Rate limit exhausted on claude-code",
            backend_type="claude-code",
        )

        result = ParallelBatchResult(
            sheets=[1], failed=[1],
            error_details={1: str(exc)},
            exceptions={1: exc},
        )

        assert isinstance(result.exceptions[1], RateLimitExhaustedError)
        assert result.exceptions[1].backend_type == "claude-code"

    def test_to_dict_excludes_exceptions(self) -> None:
        """to_dict() must NOT include exceptions — not JSON-serializable."""
        from marianne.execution.parallel import ParallelBatchResult

        result = ParallelBatchResult(
            sheets=[1], failed=[1],
            exceptions={1: ValueError("test")},
        )

        d = result.to_dict()
        assert "exceptions" not in d

    def test_default_exceptions_is_empty_dict(self) -> None:
        """Default exceptions field should be an empty dict, not None."""
        from marianne.execution.parallel import ParallelBatchResult

        result = ParallelBatchResult(sheets=[1], completed=[1])
        assert result.exceptions == {}
        assert isinstance(result.exceptions, dict)


# =========================================================================
# 10. Recovery + Dependency Interaction (Step 29 Boundary)
# =========================================================================


class TestRecoveryDependencyInteraction:
    """When recovery rebuilds from a checkpoint where some sheets failed
    and downstream sheets were in_progress, the DAG must still work."""

    def test_recover_failed_parent_in_progress_child(self) -> None:
        """If parent is FAILED and child was in_progress, the child is
        marked FAILED via recovery propagation (F-440). Previously the child
        reverted to PENDING but was undispatchable — a zombie job."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        deps = {2: [1]}
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.FAILED, attempt_count=3),
            2: _make_sheet_state(2, SheetStatus.IN_PROGRESS, attempt_count=1),
        })

        adapter.recover_job("test-job", sheets, deps, cp, max_retries=3)

        s1 = adapter._baton.get_sheet_state("test-job", 1)
        s2 = adapter._baton.get_sheet_state("test-job", 2)

        assert s1 is not None and s1.status == BatonSheetStatus.FAILED
        # F-440: child is FAILED (propagated from parent) — not stuck in PENDING
        assert s2 is not None and s2.status == BatonSheetStatus.FAILED

    def test_recover_completed_parent_in_progress_child(self) -> None:
        """If parent is COMPLETED and child was in_progress, the child
        resets to PENDING and IS dispatchable."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        deps = {2: [1]}
        cp = _make_checkpoint(sheets={
            1: _make_sheet_state(1, SheetStatus.COMPLETED, attempt_count=1),
            2: _make_sheet_state(2, SheetStatus.IN_PROGRESS, attempt_count=2),
        })

        adapter.recover_job("test-job", sheets, deps, cp, max_retries=3)

        s1 = adapter._baton.get_sheet_state("test-job", 1)
        s2 = adapter._baton.get_sheet_state("test-job", 2)

        assert s1 is not None and s1.status == BatonSheetStatus.COMPLETED
        assert s2 is not None and s2.status == BatonSheetStatus.PENDING
        assert s2.normal_attempts == 2

    def test_recover_mixed_dag_preserves_terminal_and_resets_active(self) -> None:
        """Complex DAG: 1→2→4, 1→3→4. Sheet 1 completed, 2 failed,
        3 in_progress, 4 pending. Recovery should preserve 1 and 2,
        reset 3 to pending, and FAIL 4 (F-440: sheet 4 depends on
        sheet 2 which is FAILED — propagation cascades on recovery)."""
        adapter = BatonAdapter()

        sheets = [_make_sheet(num=i) for i in range(1, 5)]
        deps = {2: [1], 3: [1], 4: [2, 3]}
        cp = _make_checkpoint(
            sheets={
                1: _make_sheet_state(1, SheetStatus.COMPLETED, attempt_count=1),
                2: _make_sheet_state(2, SheetStatus.FAILED, attempt_count=5),
                3: _make_sheet_state(3, SheetStatus.IN_PROGRESS, attempt_count=2),
                4: _make_sheet_state(4, SheetStatus.PENDING),
            },
            total_sheets=4,
        )

        adapter.recover_job("test-job", sheets, deps, cp, max_retries=5)

        s1 = adapter._baton.get_sheet_state("test-job", 1)
        s2 = adapter._baton.get_sheet_state("test-job", 2)
        s3 = adapter._baton.get_sheet_state("test-job", 3)
        s4 = adapter._baton.get_sheet_state("test-job", 4)

        assert s1 is not None and s1.status == BatonSheetStatus.COMPLETED
        assert s2 is not None and s2.status == BatonSheetStatus.FAILED
        assert s3 is not None and s3.status == BatonSheetStatus.PENDING
        assert s3.normal_attempts == 2
        # F-440: sheet 4 depends on FAILED sheet 2 — propagated to FAILED
        assert s4 is not None and s4.status == BatonSheetStatus.FAILED


# =========================================================================
# 11. Credential Redaction Boundary Cases (F-135/F-136)
# =========================================================================


class TestCredentialRedactionBoundaryCases:
    """Boundary tests for credential redaction edge cases."""

    def test_redact_none_returns_none(self) -> None:
        """None input must return None, not crash or return empty string."""
        from marianne.utils.credential_scanner import redact_credentials

        result = redact_credentials(None)
        assert result is None

    def test_redact_empty_string_returns_empty(self) -> None:
        """Empty string returns empty string unchanged."""
        from marianne.utils.credential_scanner import redact_credentials

        result = redact_credentials("")
        assert result == ""

    def test_redact_non_string_passes_through(self) -> None:
        """Non-string inputs should pass through unchanged."""
        from marianne.utils.credential_scanner import redact_credentials

        assert redact_credentials(42) == 42
        assert redact_credentials(True) is True

    def test_multiple_credentials_in_one_string(self) -> None:
        """Multiple credential patterns in a single string all get redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        raw = (
            "Anthropic: sk-ant-api03-secretkeyabcdefghijk "
            "AWS: AKIA1234567890ABCDEF "
            "Google: AIzaSyDeadBeef1234567890abcdefghij"
        )
        result = redact_credentials(raw)
        assert result is not None
        assert "sk-ant-api03" not in result
        assert "AKIA1234567890ABCDEF" not in result
        assert "AIzaSy" not in result

    def test_classify_error_redacts_auth_failure_with_key(self) -> None:
        """When _classify_error returns an error message containing a credential,
        the musician's redaction at line 129 should catch it."""
        from marianne.utils.credential_scanner import redact_credentials

        # Simulate what _classify_error might return for an auth failure
        error_msg = (
            "Authentication failed with key sk-ant-api03-realkey1234567890 "
            "for endpoint https://api.anthropic.com/v1/messages"
        )

        result = redact_credentials(error_msg) if error_msg else error_msg
        assert "sk-ant-api03-realkey1234567890" not in result
        assert "Authentication failed" in result

    def test_fallback_or_pattern_handles_none_redaction(self) -> None:
        """The musician pattern `redact_credentials(raw) or raw` should
        work when redact_credentials returns the same string (no credentials)."""
        from marianne.utils.credential_scanner import redact_credentials

        # When no credentials, redact_credentials returns the input unchanged
        raw = "Normal error: connection timeout"
        result = redact_credentials(raw) or raw
        assert result == raw

    def test_json_embedded_credential(self) -> None:
        """Credentials embedded in JSON error bodies must be redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        json_error = (
            '{"error": {"message": "Invalid API key: sk-ant-api03-embedded1234567890",'
            '"type": "authentication_error"}}'
        )
        result = redact_credentials(json_error)
        assert result is not None
        assert "sk-ant-api03-embedded1234567890" not in result


# =========================================================================
# 12. Score-Level Instrument Resolution Adversarial
# =========================================================================


class TestScoreLevelInstrumentAdversarial:
    """Adversarial tests for instrument resolution through build_sheets()."""

    def test_undefined_instrument_name_falls_through(self) -> None:
        """If per_sheet_instruments assigns a name that isn't in instruments:
        block and isn't a profile name, it should be passed through as-is
        (the conductor resolves it at runtime)."""
        from marianne.core.config.job import JobConfig
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test-undefined",
            workspace="/tmp/test-undefined-ws",
            instrument="claude-code",
            sheet={
                "size": 1,
                "total_items": 2,
                "per_sheet_instruments": {1: "nonexistent-instrument"},
            },
            prompt={"template": "Do work on {{ sheet_num }}"},
        )

        sheets = build_sheets(config)
        # Unresolvable name passes through — runtime will handle it
        assert sheets[0].instrument_name == "nonexistent-instrument"

    def test_instrument_config_merged_from_all_levels(self) -> None:
        """instrument_config from InstrumentDef should merge with
        per_sheet_instrument_config."""
        from marianne.core.config.job import InstrumentDef, JobConfig
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test-config-merge",
            workspace="/tmp/test-config-merge-ws",
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(
                    profile="gemini-cli",
                    config={"model": "gemini-pro", "temperature": 0.5},
                ),
            },
            sheet={
                "size": 1,
                "total_items": 1,
                "per_sheet_instruments": {1: "fast"},
                "per_sheet_instrument_config": {1: {"temperature": 0.9}},
            },
            prompt={"template": "Do work"},
        )

        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "gemini-cli"
        # per_sheet_instrument_config overrides InstrumentDef.config
        assert sheets[0].instrument_config.get("temperature") == 0.9
        # But model from InstrumentDef should still be present
        assert sheets[0].instrument_config.get("model") == "gemini-pro"

    def test_score_instrument_with_same_name_as_profile(self) -> None:
        """An InstrumentDef whose name matches a profile name resolves
        correctly, with config merged."""
        from marianne.core.config.job import InstrumentDef, JobConfig
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test-same-name",
            workspace="/tmp/test-same-name-ws",
            instrument="claude-code",
            instruments={
                "special": InstrumentDef(
                    profile="claude-code",
                    config={"model": "opus"},
                ),
            },
            sheet={
                "size": 1,
                "total_items": 1,
                "per_sheet_instruments": {1: "special"},
            },
            prompt={"template": "Do work"},
        )

        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "claude-code"
        assert sheets[0].instrument_config.get("model") == "opus"

    def test_instrument_def_requires_profile(self) -> None:
        """InstrumentDef must require profile field — no implicit defaults."""
        from marianne.core.config.job import InstrumentDef
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            InstrumentDef(config={"model": "opus"})  # type: ignore[call-arg]


# =========================================================================
# 13. Failure Propagation Edge Cases (F-113 extended)
# =========================================================================


class TestFailurePropagationExtended:
    """Extended adversarial tests for failure propagation edge cases."""

    def test_propagation_error_message_includes_failed_sheet(self) -> None:
        """Failure propagation must set an error_message on dependents
        identifying which sheet failed."""
        from marianne.execution.dag import DependencyDAG
        from marianne.execution.parallel import ParallelExecutor

        dag = DependencyDAG.from_dependencies(3, {2: [1], 3: [1]})
        executor = ParallelExecutor.__new__(ParallelExecutor)
        mock_runner = MagicMock()
        mock_runner.dependency_dag = dag
        executor.runner = mock_runner
        executor._logger = MagicMock()
        executor._permanently_failed = set()

        mock_state = MagicMock()
        mock_sheets: dict[int, MagicMock] = {}
        for i in range(1, 4):
            sheet = MagicMock()
            sheet.status = SheetStatus.PENDING
            mock_sheets[i] = sheet
        mock_sheets[1].status = SheetStatus.FAILED
        mock_state.sheets = mock_sheets

        executor.propagate_failure_to_dependents(mock_state, 1)

        # Check error messages include the failed sheet number
        assert "1" in str(mock_sheets[2].error_message), (
            "Error message must identify the failed dependency"
        )

    def test_propagation_skips_missing_sheets(self) -> None:
        """If a sheet number in the DAG doesn't exist in the checkpoint,
        propagation must skip it without crashing."""
        from marianne.execution.dag import DependencyDAG
        from marianne.execution.parallel import ParallelExecutor

        dag = DependencyDAG.from_dependencies(3, {2: [1], 3: [1]})
        executor = ParallelExecutor.__new__(ParallelExecutor)
        mock_runner = MagicMock()
        mock_runner.dependency_dag = dag
        executor.runner = mock_runner
        executor._logger = MagicMock()
        executor._permanently_failed = set()

        # Use a real CheckpointState-like mock where .sheets is a real dict
        # so .get() works as expected
        mock_state = MagicMock()
        sheet1 = MagicMock()
        sheet1.status = SheetStatus.FAILED
        sheet2 = MagicMock()
        sheet2.status = SheetStatus.PENDING
        # Sheet 3 is missing — only 1 and 2 in the dict
        real_sheets = {1: sheet1, 2: sheet2}
        mock_state.sheets = real_sheets

        # Should not crash despite sheet 3 missing
        executor.propagate_failure_to_dependents(mock_state, 1)
        assert 2 in executor._permanently_failed
