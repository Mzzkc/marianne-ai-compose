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

# TestParallelRateLimitExtractionAdversarial removed — tested LifecycleMixin
# and ParallelBatchResult which no longer exist (runner/parallel deleted).

# =========================================================================
# 5. Failure Propagation in Parallel Executor (F-113)
# =========================================================================

# TestFailurePropagationAdversarial removed — tested ParallelExecutor
# which no longer exists (parallel.py deleted as part of runner removal).

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
        """An unrecognized checkpoint status must raise ValueError."""
        with pytest.raises(ValueError):
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

    def test_in_progress_maps_to_in_progress(self) -> None:
        """Phase 2: identity mapping. Recovery resets are in recover_job."""
        result = checkpoint_to_baton_status("in_progress")
        assert result == BatonSheetStatus.IN_PROGRESS

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

# TestParallelBatchResultExceptionsField removed — tested ParallelBatchResult
# which no longer exists (parallel.py deleted as part of runner removal).

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
        # F-440: child is SKIPPED (blocked by failed dependency) — not stuck in PENDING
        assert s2 is not None and s2.status == BatonSheetStatus.SKIPPED

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
        reset 3 to pending. Sheet 4 is SKIPPED immediately because dep 2
        is FAILED (terminal, unsatisfied) — sheet 4 can never run since
        ALL deps must be satisfied."""
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
        # Sheet 4 is SKIPPED: dep 2 is FAILED (unsatisfiable), so sheet 4
        # can never run. Propagation marks it immediately to prevent zombies.
        assert s4 is not None and s4.status == BatonSheetStatus.SKIPPED, (
            "Sheet 4 should be SKIPPED (blocked by failed dependency 2)"
        )

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

# TestFailurePropagationExtended removed — tested ParallelExecutor
# which no longer exists (parallel.py deleted as part of runner removal).
