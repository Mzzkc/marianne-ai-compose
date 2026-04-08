"""Movement 4 — Adversarial tests (Adversary).

Targets M4's F-441 config strictness, F-211 sync dedup cache lifecycle,
pending job persistence gaps, and cross-sheet edge cases. Focuses on
system boundary interactions that individual component tests miss.

Attack surfaces:
1. F-441 extra='forbid' — nested unknown fields, bridge config coexistence,
   strip_computed_fields interaction, InjectionItem alias vs forbid, multiple
   unknown fields reporting, deeply nested models
2. F-211 sync dedup — memory leak on deregister, rapid state transitions,
   cache integrity across multiple jobs
3. Pending jobs — not persisted (restart loses them), architectural gap
4. Cross-sheet context — all-SKIPPED upstream, lookback limits, truncation
   boundaries, mixed-status ordering
5. Auto-fresh — completed_at in the future, stat failures, tolerance boundary
6. Feature interactions — cross-sheet + instrument_map + movements
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from marianne.core.config.backend import BackendConfig
from marianne.core.config.execution import (
    StaleDetectionConfig,
    ValidationRule,
)
from marianne.core.config.job import (
    InjectionCategory,
    InjectionItem,
    InstrumentDef,
    JobConfig,
    MovementDef,
)
from marianne.core.config.orchestration import (
    ConcertConfig,
    NotificationConfig,
)
from marianne.core.config.workspace import CrossSheetConfig
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import (
    BatonJobState,
    BatonSheetStatus,
    SheetExecutionState,
)
from marianne.daemon.manager import _MTIME_TOLERANCE_SECONDS, _should_auto_fresh

# =============================================================================
# Helpers
# =============================================================================


def _minimal_job_config(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid JobConfig dict for testing."""
    base: dict[str, Any] = {
        "name": "test-job",
        "sheet": {"size": 1, "total_items": 3},
        "prompt": {"template": "Do the thing for sheet {{ sheet_num }}"},
    }
    base.update(overrides)
    return base


def _make_sheet_exec_state(
    sheet_num: int,
    status: BatonSheetStatus,
    instrument: str = "claude-code",
    stdout: str | None = None,
    exec_success: bool = True,
) -> SheetExecutionState:
    """Create a SheetExecutionState with optional attempt result."""
    s = SheetExecutionState(sheet_num=sheet_num, instrument_name=instrument)
    s.status = status
    if stdout is not None:
        s.attempt_results = [
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=sheet_num,
                instrument_name=instrument,
                attempt=1,
                execution_success=exec_success,
                stdout_tail=stdout,
                validation_pass_rate=100.0 if exec_success else 0.0,
            )
        ]
    return s


# =============================================================================
# 1. F-441: extra='forbid' Edge Cases
# =============================================================================


class TestF441StrictnessEdges:
    """Test extra='forbid' interactions with real-world YAML patterns."""

    def test_unknown_top_level_field_rejected(self) -> None:
        """Basic: unknown field at top level is rejected."""
        data = _minimal_job_config(bogus_field="surprise")
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_unknown_nested_sheet_field_rejected(self) -> None:
        """Unknown field in sheet config is caught by nested extra='forbid'."""
        data = _minimal_job_config()
        data["sheet"]["mystery_option"] = True
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_unknown_nested_retry_field_rejected(self) -> None:
        """Unknown field in retry config is caught."""
        data = _minimal_job_config(retry={"max_retries": 3, "turbo_mode": True})
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_unknown_nested_parallel_field_rejected(self) -> None:
        """Unknown field in parallel config is caught."""
        data = _minimal_job_config(
            parallel={"enabled": True, "auto_distribute": True}
        )
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_multiple_unknown_fields_all_reported(self) -> None:
        """Multiple unknown fields at top level all appear in the error."""
        data = _minimal_job_config(
            bogus_a="x", bogus_b="y", bogus_c="z"
        )
        with pytest.raises(ValidationError) as exc_info:
            JobConfig(**data)
        error_str = str(exc_info.value)
        assert "bogus_a" in error_str
        assert "bogus_b" in error_str
        assert "bogus_c" in error_str

    def test_strip_computed_total_sheets_still_works(self) -> None:
        """total_sheets is stripped by model_validator before forbid check.

        This is the backward compat path: old scores include total_sheets
        as a top-level sheet field. The strip_computed_fields validator
        removes it before extra='forbid' can reject it.
        """
        data = _minimal_job_config()
        data["sheet"]["total_sheets"] = 99  # Should be stripped silently
        config = JobConfig(**data)
        # total_sheets is computed, not from input
        assert config.sheet.total_sheets == 3

    def test_strip_computed_fields_only_strips_total_sheets(self) -> None:
        """Verify that strip_computed_fields doesn't accidentally strip
        other fields — only total_sheets gets special treatment."""
        data = _minimal_job_config()
        data["sheet"]["total_sheets"] = 99  # Stripped
        data["sheet"]["totally_real_field"] = True  # Should be rejected
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_injection_item_alias_with_forbid(self) -> None:
        """InjectionItem uses both populate_by_name=True and extra='forbid'.

        The 'as' alias for 'as_' must still work. Verify that the alias
        path doesn't conflict with the forbid check.
        """
        # Using the alias (user-facing YAML key)
        item = InjectionItem.model_validate(
            {"file": "path/to/file.md", "as": "context"}
        )
        assert item.as_ == InjectionCategory.CONTEXT

        # Using the field name directly (populate_by_name=True)
        item2 = InjectionItem.model_validate(
            {"file": "path/to/file.md", "as_": "skill"}
        )
        assert item2.as_ == InjectionCategory.SKILL

    def test_injection_item_unknown_field_rejected(self) -> None:
        """InjectionItem rejects unknown fields even with alias support."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            InjectionItem.model_validate(
                {"file": "path/to/file.md", "as": "context", "priority": 1}
            )

    def test_instrument_def_unknown_field_rejected(self) -> None:
        """InstrumentDef rejects unknown fields."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            InstrumentDef.model_validate(
                {"profile": "claude-code", "config": {}, "fallback": "gemini-cli"}
            )

    def test_movement_def_unknown_field_rejected(self) -> None:
        """MovementDef rejects unknown fields."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            MovementDef.model_validate(
                {"name": "Planning", "instrument": "claude-code", "description": "desc"}
            )

    def test_backend_config_still_accepted_alongside_instrument(self) -> None:
        """The backend: -> instrument: bridge coexistence works with forbid.

        Both backend and instrument are declared fields on JobConfig.
        A score using instrument: should not cause backend: (with its
        default value) to be rejected.
        """
        data = _minimal_job_config(instrument="claude-code")
        config = JobConfig(**data)
        assert config.instrument == "claude-code"
        assert isinstance(config.backend, BackendConfig)

    def test_deeply_nested_cost_limit_unknown_field(self) -> None:
        """Unknown field in deeply nested cost_limits config."""
        data = _minimal_job_config(
            cost_limits={"enabled": True, "max_total_usd": 10.0, "alert_threshold": 5.0}
        )
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_grounding_config_unknown_field(self) -> None:
        """Unknown field in grounding config (nested in learning)."""
        data = _minimal_job_config(
            learning={"grounding": {"enabled": True, "confidence_score": 0.9}}
        )
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_cross_sheet_config_unknown_field(self) -> None:
        """Unknown field in cross_sheet config."""
        data = _minimal_job_config(
            cross_sheet={
                "auto_capture_stdout": True,
                "lookback_sheets": 3,
                "include_metadata": True,
            }
        )
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_validation_rule_unknown_field(self) -> None:
        """ValidationRule rejects unknown fields."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            ValidationRule.model_validate(
                {"type": "file_exists", "path": "{workspace}/out.md", "weight": 1.0}
            )

    def test_stale_detection_unknown_field(self) -> None:
        """StaleDetectionConfig rejects unknown fields."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            StaleDetectionConfig.model_validate(
                {"idle_timeout_seconds": 3600, "check_interval": 30}
            )

    def test_concert_config_unknown_field(self) -> None:
        """ConcertConfig rejects unknown fields."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            ConcertConfig.model_validate(
                {"scores": ["a.yaml"], "auto_chain": True}
            )

    def test_notification_config_unknown_field(self) -> None:
        """NotificationConfig rejects unknown fields."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            NotificationConfig.model_validate(
                {"type": "desktop", "channel": "general"}
            )


# =============================================================================
# 2. F-211: Sync Dedup Cache Lifecycle
# =============================================================================


class TestSyncDedupCacheLifecycle:
    """Test _synced_status cache behavior across job lifecycle."""

    def test_synced_status_cleaned_on_deregister(self) -> None:
        """F-470 FIXED: _synced_status entries ARE removed when a job is
        deregistered. Previously a memory leak for long-running daemons.

        Regression test: after deregister_job(), the cache must NOT retain
        entries for the removed job's sheets.
        """
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
            state_sync_callback=MagicMock(),
        )
        adapter.set_backend_pool(MagicMock())

        # Manually populate _synced_status as if sheets were synced
        adapter._synced_status[("job-1", 1)] = "completed"
        adapter._synced_status[("job-1", 2)] = "failed"
        adapter._synced_status[("job-1", 3)] = "completed"
        adapter._synced_status[("job-2", 1)] = "in_progress"

        # Deregister job-1
        adapter.deregister_job("job-1")

        # FIX VERIFIED: job-1 entries are now cleaned up
        assert ("job-1", 1) not in adapter._synced_status
        assert ("job-1", 2) not in adapter._synced_status
        assert ("job-1", 3) not in adapter._synced_status
        # job-2 unaffected
        assert ("job-2", 1) in adapter._synced_status

        # Only 1 entry remains (job-2)
        assert len(adapter._synced_status) == 1

    def test_dedup_prevents_duplicate_callback(self) -> None:
        """Sync callback not invoked when status hasn't changed."""
        callback = MagicMock()
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
            state_sync_callback=callback,
        )

        # First sync — should fire
        adapter._synced_status.clear()
        adapter._synced_status[("job-1", 1)] = "completed"
        adapter._invoke_sync_callback("job-1", 1, "completed")
        assert callback.call_count == 1

        # Cache check: same status means _sync_single_sheet would skip
        key = ("job-1", 1)
        assert adapter._synced_status.get(key) == "completed"

    def test_dedup_cache_allows_status_progression(self) -> None:
        """Cache correctly detects status change and allows new sync."""
        callback = MagicMock()
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
            state_sync_callback=callback,
        )

        # Initial: pending
        adapter._synced_status[("job-1", 1)] = "pending"

        # Transition: pending -> in_progress — different, should fire
        key = ("job-1", 1)
        new_status = "in_progress"
        assert adapter._synced_status.get(key) != new_status
        adapter._synced_status[key] = new_status
        adapter._invoke_sync_callback("job-1", 1, new_status)
        assert callback.call_count == 1

    def test_dedup_cache_cleaned_across_multiple_jobs(self) -> None:
        """F-470 FIXED: Cache entries are cleaned on deregister.
        Memory is now O(active_sheets), not O(total_sheets_ever).
        """
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
            state_sync_callback=MagicMock(),
        )

        # Simulate 100 jobs, each with 10 sheets
        for job_num in range(100):
            for sheet_num in range(1, 11):
                adapter._synced_status[(f"job-{job_num}", sheet_num)] = "completed"

        assert len(adapter._synced_status) == 1000

        # Deregister all jobs — cache is now cleaned
        for job_num in range(100):
            adapter.deregister_job(f"job-{job_num}")

        # FIX VERIFIED: all entries cleaned after deregister
        assert len(adapter._synced_status) == 0


# =============================================================================
# 3. Auto-Fresh Edge Cases
# =============================================================================


class TestAutoFreshEdges:
    """Test _should_auto_fresh boundary conditions."""

    def test_completed_at_in_future(self) -> None:
        """If completed_at is in the future (clock skew), auto-fresh
        should NOT trigger — the score can't have been modified "after"
        a future completion time."""
        future_time = time.time() + 86400
        mock_path = MagicMock(spec=Path)
        mock_path.stat.return_value = MagicMock(st_mtime=time.time())

        result = _should_auto_fresh(mock_path, future_time)
        assert result is False

    def test_both_timestamps_equal(self) -> None:
        """When mtime == completed_at, auto-fresh should NOT trigger."""
        now = time.time()
        mock_path = MagicMock(spec=Path)
        mock_path.stat.return_value = MagicMock(st_mtime=now)

        result = _should_auto_fresh(mock_path, now)
        assert result is False

    def test_mtime_just_past_tolerance(self) -> None:
        """mtime == completed_at + tolerance + epsilon triggers fresh."""
        base = 1000000.0
        mock_path = MagicMock(spec=Path)
        mock_path.stat.return_value = MagicMock(
            st_mtime=base + _MTIME_TOLERANCE_SECONDS + 0.001
        )

        result = _should_auto_fresh(mock_path, base)
        assert result is True

    def test_mtime_at_exact_tolerance(self) -> None:
        """mtime == completed_at + tolerance does NOT trigger (strict >)."""
        base = 1000000.0
        mock_path = MagicMock(spec=Path)
        mock_path.stat.return_value = MagicMock(
            st_mtime=base + _MTIME_TOLERANCE_SECONDS
        )

        result = _should_auto_fresh(mock_path, base)
        assert result is False

    def test_stat_permission_error(self) -> None:
        """PermissionError from stat() returns False (not auto-fresh)."""
        mock_path = MagicMock(spec=Path)
        mock_path.stat.side_effect = PermissionError("no access")

        result = _should_auto_fresh(mock_path, time.time() - 100)
        assert result is False

    def test_stat_file_not_found(self) -> None:
        """FileNotFoundError from stat() returns False."""
        mock_path = MagicMock(spec=Path)
        mock_path.stat.side_effect = FileNotFoundError("gone")

        result = _should_auto_fresh(mock_path, time.time() - 100)
        assert result is False

    def test_completed_at_none(self) -> None:
        """None completed_at always returns False — never ran before."""
        mock_path = MagicMock(spec=Path)
        result = _should_auto_fresh(mock_path, None)
        assert result is False
        mock_path.stat.assert_not_called()

    def test_completed_at_zero(self) -> None:
        """completed_at=0.0 (epoch) — any recent mtime triggers."""
        mock_path = MagicMock(spec=Path)
        mock_path.stat.return_value = MagicMock(st_mtime=100.0)

        result = _should_auto_fresh(mock_path, 0.0)
        assert result is True

    def test_negative_completed_at(self) -> None:
        """Negative completed_at (shouldn't happen but defensive)."""
        mock_path = MagicMock(spec=Path)
        mock_path.stat.return_value = MagicMock(st_mtime=0.0)

        result = _should_auto_fresh(mock_path, -100.0)
        assert result is True


# =============================================================================
# 4. Cross-Sheet Context Edge Cases
# =============================================================================


class TestCrossSheetContextEdges:
    """Test baton adapter cross-sheet context collection edge cases."""

    def test_all_upstream_skipped_returns_skipped_markers(self) -> None:
        """When ALL upstream sheets are SKIPPED, previous_outputs should
        contain [SKIPPED] markers for each, not be empty."""
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
        )

        job_state = BatonJobState(job_id="test-job", total_sheets=4)
        for i in range(1, 4):
            job_state.register_sheet(
                _make_sheet_exec_state(i, BatonSheetStatus.SKIPPED)
            )
        job_state.register_sheet(
            _make_sheet_exec_state(4, BatonSheetStatus.IN_PROGRESS)
        )

        adapter._baton._jobs["test-job"] = job_state
        adapter._job_cross_sheet["test-job"] = CrossSheetConfig(
            auto_capture_stdout=True
        )

        previous_outputs, previous_files = adapter._collect_cross_sheet_context(
            "test-job", 4
        )

        assert previous_outputs == {1: "[SKIPPED]", 2: "[SKIPPED]", 3: "[SKIPPED]"}
        assert previous_files == {}

    def test_cross_sheet_mixed_status_ordering(self) -> None:
        """Cross-sheet context respects sheet ordering and status correctly."""
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
        )

        job_state = BatonJobState(job_id="test-job", total_sheets=4)
        # Sheet 1: COMPLETED with stdout
        job_state.register_sheet(
            _make_sheet_exec_state(1, BatonSheetStatus.COMPLETED, stdout="Output 1")
        )
        # Sheet 2: SKIPPED
        job_state.register_sheet(
            _make_sheet_exec_state(2, BatonSheetStatus.SKIPPED)
        )
        # Sheet 3: FAILED (stdout NOT included on baton path — F-202)
        job_state.register_sheet(
            _make_sheet_exec_state(
                3, BatonSheetStatus.FAILED, stdout="Error output", exec_success=False
            )
        )
        # Sheet 4: current
        job_state.register_sheet(
            _make_sheet_exec_state(4, BatonSheetStatus.IN_PROGRESS)
        )

        adapter._baton._jobs["test-job"] = job_state
        adapter._job_cross_sheet["test-job"] = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=0  # All sheets
        )

        previous_outputs, _ = adapter._collect_cross_sheet_context("test-job", 4)

        assert previous_outputs[1] == "Output 1"
        assert previous_outputs[2] == "[SKIPPED]"
        # FAILED sheets excluded on baton path (only COMPLETED collected)
        assert 3 not in previous_outputs

    def test_cross_sheet_disabled_returns_empty(self) -> None:
        """When cross_sheet is not configured, returns empty dicts."""
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
        )

        previous_outputs, previous_files = adapter._collect_cross_sheet_context(
            "nonexistent-job", 1
        )
        assert previous_outputs == {}
        assert previous_files == {}

    def test_cross_sheet_lookback_limits_collection(self) -> None:
        """lookback_sheets limits how far back we collect."""
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
        )

        job_state = BatonJobState(job_id="test-job", total_sheets=10)
        for i in range(1, 11):
            job_state.register_sheet(
                _make_sheet_exec_state(i, BatonSheetStatus.COMPLETED, stdout=f"Out {i}")
            )

        adapter._baton._jobs["test-job"] = job_state
        adapter._job_cross_sheet["test-job"] = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=3
        )

        # Current sheet 10, lookback=3 means sheets 7,8,9
        previous_outputs, _ = adapter._collect_cross_sheet_context("test-job", 10)
        assert set(previous_outputs.keys()) == {7, 8, 9}

    def test_cross_sheet_truncation_exact_boundary(self) -> None:
        """Content exactly at max_chars is NOT truncated."""
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
        )

        exact_content = "A" * 1000
        job_state = BatonJobState(job_id="test-job", total_sheets=2)
        job_state.register_sheet(
            _make_sheet_exec_state(1, BatonSheetStatus.COMPLETED, stdout=exact_content)
        )

        adapter._baton._jobs["test-job"] = job_state
        adapter._job_cross_sheet["test-job"] = CrossSheetConfig(
            auto_capture_stdout=True, max_output_chars=1000
        )

        previous_outputs, _ = adapter._collect_cross_sheet_context("test-job", 2)
        assert previous_outputs[1] == exact_content
        assert "[truncated]" not in previous_outputs[1]

    def test_cross_sheet_truncation_one_over(self) -> None:
        """Content one char over max_chars IS truncated."""
        adapter = BatonAdapter(
            event_bus=MagicMock(),
            max_concurrent_sheets=10,
        )

        over_content = "A" * 1001
        job_state = BatonJobState(job_id="test-job", total_sheets=2)
        job_state.register_sheet(
            _make_sheet_exec_state(1, BatonSheetStatus.COMPLETED, stdout=over_content)
        )

        adapter._baton._jobs["test-job"] = job_state
        adapter._job_cross_sheet["test-job"] = CrossSheetConfig(
            auto_capture_stdout=True, max_output_chars=1000
        )

        previous_outputs, _ = adapter._collect_cross_sheet_context("test-job", 2)
        assert "[truncated]" in previous_outputs[1]
        assert previous_outputs[1].startswith("A" * 1000)


# =============================================================================
# 5. Credential Redaction Defensive Pattern
# =============================================================================


class TestCredentialRedactionDefensivePattern:
    """Test the `redact_credentials(content) or content` pattern in adapter."""

    def test_redaction_of_full_credential_string(self) -> None:
        """A string that is entirely a credential gets fully redacted.
        The `or content` fallback does NOT leak because the replacement
        label is truthy.
        """
        from marianne.utils.credential_scanner import redact_credentials

        key = "sk-ant-api03" + "x" * 30
        result = redact_credentials(key)
        assert result is not None
        assert "sk-ant" not in result
        assert "REDACTED" in result

        safe_result = redact_credentials(key) or key
        assert "sk-ant" not in safe_result

    def test_redaction_returns_same_when_no_credentials(self) -> None:
        """Non-credential text passes through unchanged."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "Hello, this is normal output."
        result = redact_credentials(text)
        assert result == text

    def test_redaction_none_input_or_pattern(self) -> None:
        """None input: redact returns None, `or None` = None (safe)."""
        from marianne.utils.credential_scanner import redact_credentials

        result = redact_credentials(None)
        assert result is None
        safe = redact_credentials(None) or None
        assert safe is None

    def test_redaction_empty_string_or_pattern(self) -> None:
        """Empty string: `or ''` = '' (safe)."""
        from marianne.utils.credential_scanner import redact_credentials

        result = redact_credentials("")
        assert result == ""
        safe = redact_credentials("") or ""
        assert safe == ""

    def test_multiple_credentials_all_redacted(self) -> None:
        """Multiple credentials in one string are all replaced."""
        from marianne.utils.credential_scanner import redact_credentials

        text = (
            "Key1: sk-ant-api03" + "A" * 30
            + " Key2: AIzaSy" + "B" * 28
            + " Key3: AKIA" + "C" * 16
        )
        result = redact_credentials(text)
        assert "sk-ant" not in result
        assert "AIzaSy" not in result
        assert "AKIA" not in result
        assert result.count("REDACTED") == 3


# =============================================================================
# 6. F-441 Config Strictness with Real Score Patterns
# =============================================================================


class TestF441RealScorePatterns:
    """Test extra='forbid' against patterns that appear in real scores."""

    def test_yaml_dict_passthrough_in_instrument_config(self) -> None:
        """instrument_config is dict[str, Any] — arbitrary keys are allowed."""
        data = _minimal_job_config(
            instrument="claude-code",
            instrument_config={
                "model": "claude-opus-4-6",
                "timeout_seconds": 3600,
                "custom_flag": True,
            },
        )
        config = JobConfig(**data)
        assert config.instrument_config["custom_flag"] is True

    def test_prompt_variables_passthrough(self) -> None:
        """prompt.variables is dict[str, Any] — arbitrary user data."""
        data = _minimal_job_config()
        data["prompt"]["variables"] = {
            "project_name": "my-project",
            "version": 42,
            "nested": {"key": "value"},
        }
        config = JobConfig(**data)
        assert config.prompt.variables["project_name"] == "my-project"

    def test_movements_with_known_fields_only(self) -> None:
        """movements: key with only known MovementDef fields."""
        data = _minimal_job_config()
        data["sheet"]["size"] = 1
        data["sheet"]["total_items"] = 6
        data["movements"] = {
            1: {"name": "Planning", "instrument": "claude-code"},
            2: {"name": "Implementation", "voices": 3},
        }
        config = JobConfig(**data)
        assert config.movements[1].name == "Planning"
        assert config.movements[2].voices == 3

    def test_movements_with_unknown_field_rejected(self) -> None:
        """movements: key with unknown field in MovementDef."""
        data = _minimal_job_config()
        data["sheet"]["size"] = 1
        data["sheet"]["total_items"] = 6
        data["movements"] = {
            1: {"name": "Planning", "priority": "high"},
        }
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**data)

    def test_per_sheet_instruments_accepted(self) -> None:
        """per_sheet_instruments is a known field."""
        data = _minimal_job_config()
        data["sheet"]["per_sheet_instruments"] = {1: "gemini-cli", 2: "claude-code"}
        config = JobConfig(**data)
        assert config.sheet.per_sheet_instruments[1] == "gemini-cli"

    def test_fan_out_with_forbid(self) -> None:
        """fan_out is a known field. After expansion, it clears to {}."""
        data = _minimal_job_config()
        data["sheet"]["size"] = 1
        data["sheet"]["total_items"] = 3
        data["sheet"]["fan_out"] = {2: 3}
        config = JobConfig(**data)
        assert config.sheet.fan_out == {}
        assert config.sheet.total_items == 5


# =============================================================================
# 7. Baton State Mapping Completeness
# =============================================================================


class TestBatonStateMappingEdges:
    """Test baton_to_checkpoint_status edge cases."""

    def test_all_baton_statuses_map_to_checkpoint(self) -> None:
        """Every BatonSheetStatus has a mapping to a checkpoint status."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        # All 11 SheetStatus values as of M5 (SheetStatus expansion)
        valid_checkpoint_statuses = {
            "pending", "ready", "dispatched", "in_progress", "waiting",
            "retry_scheduled", "fermata", "completed", "failed", "skipped", "cancelled",
        }

        for status in BatonSheetStatus:
            result = baton_to_checkpoint_status(status)
            assert isinstance(result, str), f"No mapping for {status}"
            assert result in valid_checkpoint_statuses, f"Unexpected mapping {status} -> {result}"

    def test_terminal_statuses_map_to_terminal(self) -> None:
        """Terminal baton statuses map to terminal checkpoint statuses."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        terminal_baton = {
            BatonSheetStatus.COMPLETED,
            BatonSheetStatus.FAILED,
            BatonSheetStatus.SKIPPED,
            BatonSheetStatus.CANCELLED,
        }
        # All 4 terminal statuses as of M5
        terminal_checkpoint = {"completed", "failed", "skipped", "cancelled"}

        for status in terminal_baton:
            result = baton_to_checkpoint_status(status)
            assert result in terminal_checkpoint, (
                f"Terminal baton status {status} mapped to non-terminal {result}"
            )


# =============================================================================
# 8. Feature Interaction Tests
# =============================================================================


class TestFeatureInteractions:
    """Test interactions between M4 features designed independently."""

    def test_cross_sheet_with_instrument_map(self) -> None:
        """Cross-sheet context works when sheets use different instruments."""
        data = _minimal_job_config()
        data["sheet"]["instrument_map"] = {"gemini-cli": [1], "claude-code": [2, 3]}
        data["cross_sheet"] = {"auto_capture_stdout": True, "lookback_sheets": 2}
        data["instrument"] = "claude-code"

        config = JobConfig(**data)
        assert config.cross_sheet.auto_capture_stdout is True
        assert config.sheet.instrument_map["gemini-cli"] == [1]

    def test_movements_with_per_sheet_instruments_and_cross_sheet(self) -> None:
        """All three M4 features coexist in one score config."""
        data = _minimal_job_config()
        data["sheet"]["size"] = 1
        data["sheet"]["total_items"] = 6
        data["sheet"]["per_sheet_instruments"] = {3: "gemini-cli"}
        data["movements"] = {
            1: {"name": "Planning", "instrument": "claude-code"},
            2: {"name": "Execution", "voices": 2},
        }
        data["cross_sheet"] = {"auto_capture_stdout": True}
        data["instrument"] = "claude-code"

        config = JobConfig(**data)
        assert config.movements[1].instrument == "claude-code"
        assert config.sheet.per_sheet_instruments[3] == "gemini-cli"
        assert config.cross_sheet.auto_capture_stdout is True

    def test_forbid_with_concert_config(self) -> None:
        """Concert config (multi-score chaining) works with forbid."""
        data = _minimal_job_config()
        data["concert"] = {"enabled": True, "max_chain_depth": 10}
        config = JobConfig(**data)
        assert config.concert.enabled is True
        assert config.concert.max_chain_depth == 10

    def test_forbid_with_spec_corpus(self) -> None:
        """Spec corpus config works with forbid."""
        data = _minimal_job_config()
        data["spec"] = {"spec_dir": ".marianne/spec/"}
        config = JobConfig(**data)
        assert config.spec.spec_dir == ".marianne/spec/"
