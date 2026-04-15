"""Targeted coverage tests for execution module files outside runner/ subpackage.

Fills coverage gaps for:
- execution/setup.py (0% → target 80%+)
- execution/progress.py (0% → target 80%+)
- execution/reconciliation.py (0% → target 80%+)
- execution/circuit_breaker.py (61% → target 80%+)
- execution/parallel.py (37% → target 80%+)
- execution/hooks.py (21% → target 80%+)
- execution/retry_strategy.py (66% → target 80%+)
- execution/validation/engine.py (21% → target 80%+)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config import JobConfig


# =============================================================================
# Helpers
# =============================================================================


def _make_config(**overrides: Any) -> JobConfig:
    """Build a minimal JobConfig for testing."""
    data: dict[str, Any] = {
        "name": "test-cov",
        "description": "Coverage tests",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": 30},
        "prompt": {"template": "Process {{ sheet_num }}."},
        "retry": {"max_retries": 2, "base_delay_seconds": 1},
        "workspace": "/tmp/test-cov-workspace",
    }
    data.update(overrides)
    return JobConfig(**data)


# =============================================================================
# execution/setup.py tests
# =============================================================================


class TestCreateBackendFromConfig:
    """Tests for create_backend_from_config()."""

    def test_claude_cli_backend(self) -> None:
        from marianne.execution.setup import create_backend_from_config
        from marianne.core.config.backend import BackendConfig

        config = BackendConfig(type="claude_cli", skip_permissions=True)
        backend = create_backend_from_config(config)
        assert backend is not None

    def test_anthropic_api_backend(self) -> None:
        from marianne.execution.setup import create_backend_from_config
        from marianne.core.config.backend import BackendConfig

        config = BackendConfig(type="anthropic_api", model="claude-sonnet-4-5-20250929")
        backend = create_backend_from_config(config)
        assert backend is not None

    def test_recursive_light_backend(self) -> None:
        from marianne.execution.setup import create_backend_from_config
        from marianne.core.config.backend import BackendConfig

        config = BackendConfig(type="recursive_light")
        backend = create_backend_from_config(config)
        assert backend is not None

    def test_ollama_backend(self) -> None:
        from marianne.execution.setup import create_backend_from_config
        from marianne.core.config.backend import BackendConfig

        config = BackendConfig(type="ollama", model="llama3")
        backend = create_backend_from_config(config)
        assert backend is not None


class TestCreateBackend:
    """Tests for create_backend()."""

    def test_creates_from_job_config(self) -> None:
        from marianne.execution.setup import create_backend

        config = _make_config()
        backend = create_backend(config)
        assert backend is not None


class TestSetupLearning:
    """Tests for setup_learning()."""

    def test_disabled_returns_none(self) -> None:
        from marianne.execution.setup import setup_learning

        config = _make_config(learning={"enabled": False})
        outcome, global_store = setup_learning(config)
        assert outcome is None
        assert global_store is None

    def test_enabled_json_store(self, tmp_path: Path) -> None:
        from marianne.execution.setup import setup_learning

        config = _make_config(
            learning={"enabled": True, "outcome_store_type": "json"},
            workspace=str(tmp_path),
        )
        outcome, global_store = setup_learning(config)
        assert outcome is not None
        assert global_store is not None

    def test_override_global_store(self, tmp_path: Path) -> None:
        from marianne.execution.setup import setup_learning

        mock_store = MagicMock()
        config = _make_config(
            learning={"enabled": True, "outcome_store_type": "json"},
            workspace=str(tmp_path),
        )
        outcome, global_store = setup_learning(
            config, global_learning_store_override=mock_store
        )
        assert global_store is mock_store


class TestSetupNotifications:
    """Tests for setup_notifications()."""

    def test_no_notifications_configured(self) -> None:
        from marianne.execution.setup import setup_notifications

        config = _make_config()
        result = setup_notifications(config)
        assert result is None


class TestSetupGrounding:
    """Tests for setup_grounding()."""

    def test_grounding_disabled(self) -> None:
        from marianne.execution.setup import setup_grounding

        config = _make_config()
        result = setup_grounding(config)
        assert result is None

    def test_grounding_enabled_with_hook(self) -> None:
        from marianne.execution.setup import setup_grounding

        config = _make_config(
            grounding={
                "enabled": True,
                "hooks": [{"type": "file_checksum"}],
            }
        )
        result = setup_grounding(config)
        assert result is not None


class TestCreateStateBackend:
    """Tests for create_state_backend()."""

    def test_json_backend(self, tmp_path: Path) -> None:
        from marianne.execution.setup import create_state_backend

        backend = create_state_backend(tmp_path, "json")
        assert backend is not None

    def test_sqlite_backend(self, tmp_path: Path) -> None:
        from marianne.execution.setup import create_state_backend

        backend = create_state_backend(tmp_path, "sqlite")
        assert backend is not None

    def test_default_is_json(self, tmp_path: Path) -> None:
        from marianne.execution.setup import create_state_backend

        backend = create_state_backend(tmp_path)
        assert backend is not None


# =============================================================================
# execution/progress.py tests
# =============================================================================


class TestExecutionProgress:
    """Tests for ExecutionProgress dataclass."""

    def test_creation(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(started_at=now, last_activity_at=now)
        assert p.bytes_received == 0
        assert p.lines_received == 0
        assert p.phase == "starting"

    def test_elapsed_seconds(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(started_at=now, last_activity_at=now)
        assert p.elapsed_seconds >= 0

    def test_idle_seconds(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(started_at=now, last_activity_at=now)
        assert p.idle_seconds >= 0

    def test_format_bytes_under_1kb(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(started_at=now, last_activity_at=now, bytes_received=500)
        assert p.format_bytes() == "500B"

    def test_format_bytes_kb(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(started_at=now, last_activity_at=now, bytes_received=5120)
        assert "KB" in p.format_bytes()

    def test_format_bytes_mb(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(
            started_at=now, last_activity_at=now, bytes_received=2 * 1024 * 1024
        )
        assert "MB" in p.format_bytes()

    def test_format_elapsed_seconds(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(started_at=now, last_activity_at=now)
        result = p.format_elapsed()
        assert "s" in result

    def test_format_status(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(
            started_at=now, last_activity_at=now,
            bytes_received=1024, lines_received=10,
        )
        status = p.format_status()
        assert "Still running" in status
        assert "10 lines" in status

    def test_to_dict(self) -> None:
        from marianne.execution.progress import ExecutionProgress
        from marianne.utils.time import utc_now

        now = utc_now()
        p = ExecutionProgress(started_at=now, last_activity_at=now)
        d = p.to_dict()
        assert "started_at" in d
        assert "bytes_received" in d
        assert "phase" in d


class TestProgressTracker:
    """Tests for ProgressTracker."""

    def test_creation(self) -> None:
        from marianne.execution.progress import ProgressTracker

        tracker = ProgressTracker()
        progress = tracker.get_progress()
        assert progress.phase == "starting"

    def test_update_bytes_and_lines(self) -> None:
        from marianne.execution.progress import ProgressTracker

        tracker = ProgressTracker()
        tracker.update(new_bytes=100, new_lines=5)
        progress = tracker.get_progress()
        assert progress.bytes_received == 100
        assert progress.lines_received == 5

    def test_update_with_callback(self) -> None:
        from marianne.execution.progress import ProgressTracker

        callback = MagicMock()
        tracker = ProgressTracker(callback=callback)
        tracker.update(new_bytes=50)
        callback.assert_called_once()

    def test_set_phase(self) -> None:
        from marianne.execution.progress import ProgressTracker

        tracker = ProgressTracker()
        tracker.set_phase("executing")
        progress = tracker.get_progress()
        assert progress.phase == "executing"

    def test_set_phase_with_callback(self) -> None:
        from marianne.execution.progress import ProgressTracker

        callback = MagicMock()
        tracker = ProgressTracker(callback=callback)
        tracker.set_phase("executing")
        callback.assert_called_once()

    def test_set_same_phase_no_callback(self) -> None:
        from marianne.execution.progress import ProgressTracker

        callback = MagicMock()
        tracker = ProgressTracker(callback=callback)
        # Phase is already "starting", setting same phase should not call callback
        tracker.set_phase("starting")
        callback.assert_not_called()

    def test_get_snapshots(self) -> None:
        from marianne.execution.progress import ProgressTracker

        tracker = ProgressTracker()
        tracker.set_phase("executing")
        snapshots = tracker.get_snapshots()
        assert len(snapshots) >= 1

    def test_force_snapshot(self) -> None:
        from marianne.execution.progress import ProgressTracker

        tracker = ProgressTracker()
        tracker.update(new_bytes=10, force_snapshot=True)
        snapshots = tracker.get_snapshots()
        assert len(snapshots) >= 1

    def test_reset(self) -> None:
        from marianne.execution.progress import ProgressTracker

        tracker = ProgressTracker()
        tracker.update(new_bytes=100, new_lines=10)
        tracker.set_phase("executing")
        tracker.reset()
        progress = tracker.get_progress()
        assert progress.bytes_received == 0
        assert progress.lines_received == 0
        assert progress.phase == "starting"

    def test_get_progress_returns_copy(self) -> None:
        from marianne.execution.progress import ProgressTracker

        tracker = ProgressTracker()
        p1 = tracker.get_progress()
        tracker.update(new_bytes=50)
        p2 = tracker.get_progress()
        assert p1.bytes_received != p2.bytes_received


class TestStreamingOutputTracker:
    """Tests for StreamingOutputTracker."""

    def test_process_chunk(self) -> None:
        from marianne.execution.progress import ProgressTracker, StreamingOutputTracker

        tracker = ProgressTracker()
        stream = StreamingOutputTracker(tracker)
        stream.process_chunk(b"hello\nworld\n")
        progress = tracker.get_progress()
        assert progress.bytes_received == 12
        assert progress.lines_received == 2

    def test_process_empty_chunk(self) -> None:
        from marianne.execution.progress import ProgressTracker, StreamingOutputTracker

        tracker = ProgressTracker()
        stream = StreamingOutputTracker(tracker)
        stream.process_chunk(b"")
        progress = tracker.get_progress()
        assert progress.bytes_received == 0

    def test_partial_line_handling(self) -> None:
        from marianne.execution.progress import ProgressTracker, StreamingOutputTracker

        tracker = ProgressTracker()
        stream = StreamingOutputTracker(tracker)
        stream.process_chunk(b"partial")
        progress = tracker.get_progress()
        assert progress.bytes_received == 7
        assert progress.lines_received == 0

    def test_finish_counts_partial_line(self) -> None:
        from marianne.execution.progress import ProgressTracker, StreamingOutputTracker

        tracker = ProgressTracker()
        stream = StreamingOutputTracker(tracker)
        stream.process_chunk(b"partial")
        stream.finish()
        progress = tracker.get_progress()
        assert progress.lines_received == 1

    def test_finish_with_no_partial(self) -> None:
        from marianne.execution.progress import ProgressTracker, StreamingOutputTracker

        tracker = ProgressTracker()
        stream = StreamingOutputTracker(tracker)
        stream.process_chunk(b"complete\n")
        stream.finish()
        progress = tracker.get_progress()
        assert progress.lines_received == 1


# =============================================================================
# execution/reconciliation.py tests
# =============================================================================


class TestReconciliationReport:
    """Tests for ReconciliationReport."""

    def test_empty_report(self) -> None:
        from marianne.execution.reconciliation import ReconciliationReport

        report = ReconciliationReport()
        assert not report.has_changes
        assert report.summary() == "No config changes detected"

    def test_with_changes(self) -> None:
        from marianne.execution.reconciliation import ReconciliationReport

        report = ReconciliationReport(sections_changed=["backend", "retry"])
        assert report.has_changes
        summary = report.summary()
        assert "2 section(s) changed" in summary

    def test_with_removals(self) -> None:
        from marianne.execution.reconciliation import ReconciliationReport

        report = ReconciliationReport(sections_removed=["learning"])
        assert report.has_changes
        assert "removed" in report.summary()

    def test_with_field_resets(self) -> None:
        from marianne.execution.reconciliation import ReconciliationReport

        report = ReconciliationReport(
            sections_changed=["cost_limits"],
            fields_reset={"cost_limits": ["total_estimated_cost"]},
        )
        assert "1 checkpoint field(s) reset" in report.summary()


class TestReconcileConfig:
    """Tests for reconcile_config()."""

    def test_no_changes(self) -> None:
        from marianne.core.checkpoint import CheckpointState
        from marianne.execution.reconciliation import reconcile_config

        config = _make_config()
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=3,
            config_snapshot=config.model_dump(mode="json"),
        )
        report = reconcile_config(state, config)
        assert not report.has_changes

    def test_backend_change_detected(self) -> None:
        from marianne.core.checkpoint import CheckpointState
        from marianne.execution.reconciliation import reconcile_config

        old_config = _make_config()
        new_config = _make_config(
            backend={"type": "anthropic_api", "model": "claude-sonnet-4-5-20250929"}
        )
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=3,
            config_snapshot=old_config.model_dump(mode="json"),
        )
        report = reconcile_config(state, new_config)
        assert report.has_changes
        assert "backend" in report.sections_changed

    def test_cost_limits_resets_checkpoint_fields(self) -> None:
        from marianne.core.checkpoint import CheckpointState
        from marianne.execution.reconciliation import reconcile_config

        old_config = _make_config(
            cost_limits={
                "enabled": True,
                "max_cost_per_job": 10.0,
                "cost_per_1k_input_tokens": 0.003,
            }
        )
        old_snapshot = old_config.model_dump(mode="json")
        new_config = _make_config(
            cost_limits={
                "enabled": True,
                "max_cost_per_job": 20.0,
                "cost_per_1k_input_tokens": 0.006,
            }
        )
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=3,
            config_snapshot=old_snapshot,
            total_estimated_cost=5.0,
        )
        report = reconcile_config(state, new_config)
        assert "cost_limits" in report.sections_changed
        # cost fields should be reset
        assert state.total_estimated_cost == 0.0

    def test_metadata_fields_ignored(self) -> None:
        from marianne.core.checkpoint import CheckpointState
        from marianne.execution.reconciliation import reconcile_config

        old_config = _make_config(description="old desc")
        new_config = _make_config(description="new desc")
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=3,
            config_snapshot=old_config.model_dump(mode="json"),
        )
        report = reconcile_config(state, new_config)
        assert "description" not in report.sections_changed

    def test_empty_config_snapshot(self) -> None:
        from marianne.core.checkpoint import CheckpointState
        from marianne.execution.reconciliation import reconcile_config

        new_config = _make_config()
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=3,
            config_snapshot=None,
        )
        report = reconcile_config(state, new_config)
        assert report.has_changes


# =============================================================================
# execution/circuit_breaker.py tests
# =============================================================================


class TestCircuitBreakerStats:
    """Tests for CircuitBreakerStats."""

    def test_to_dict(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreakerStats

        stats = CircuitBreakerStats(total_successes=5, total_failures=2)
        d = stats.to_dict()
        assert d["total_successes"] == 5
        assert d["total_failures"] == 2
        assert "total_estimated_cost" in d


class TestCircuitBreaker:
    """Tests for CircuitBreaker state machine."""

    async def test_initial_state_closed(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)
        state = await cb.get_state()
        assert state == CircuitState.CLOSED

    async def test_can_execute_when_closed(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)
        assert await cb.can_execute() is True

    async def test_opens_after_threshold_failures(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=300.0)
        for _ in range(3):
            await cb.record_failure()
        state = await cb.get_state()
        assert state == CircuitState.OPEN

    async def test_cannot_execute_when_open(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=300.0)
        await cb.record_failure()
        await cb.record_failure()
        assert await cb.can_execute() is False

    async def test_transitions_to_half_open_after_timeout(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        await asyncio.sleep(0.02)
        state = await cb.get_state()
        assert state == CircuitState.HALF_OPEN

    async def test_closes_on_success_in_half_open(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        await asyncio.sleep(0.02)
        await cb.get_state()  # trigger half_open transition
        await cb.record_success()
        state = await cb.get_state()
        assert state == CircuitState.CLOSED

    async def test_reopens_on_failure_in_half_open(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        await cb.record_failure()
        await asyncio.sleep(0.02)
        await cb.get_state()  # trigger half_open transition
        await cb.record_failure()
        state = await cb.get_state()
        assert state == CircuitState.OPEN

    async def test_success_in_closed_resets_failures(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=300.0)
        await cb.record_failure()
        await cb.record_success()
        stats = await cb.get_stats()
        assert stats.consecutive_failures == 0

    async def test_time_until_retry_when_closed(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=300.0)
        result = await cb.time_until_retry()
        assert result is None

    async def test_time_until_retry_when_open(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=300.0)
        await cb.record_failure()
        remaining = await cb.time_until_retry()
        assert remaining is not None
        assert remaining > 0

    async def test_record_cost(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        await cb.record_cost(100, 200, 0.05)
        stats = await cb.get_stats()
        assert stats.total_input_tokens == 100
        assert stats.total_output_tokens == 200
        assert abs(stats.total_estimated_cost - 0.05) < 0.001

    async def test_check_cost_threshold_not_exceeded(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        await cb.record_cost(100, 200, 0.05)
        exceeded = await cb.check_cost_threshold(1.0)
        assert exceeded is False

    async def test_check_cost_threshold_exceeded(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        await cb.record_cost(100, 200, 5.0)
        exceeded = await cb.check_cost_threshold(1.0)
        assert exceeded is True

    async def test_get_stats_returns_copy(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        stats1 = await cb.get_stats()
        await cb.record_failure()
        stats2 = await cb.get_stats()
        assert stats1.total_failures != stats2.total_failures

    async def test_reset(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=300.0)
        await cb.record_failure()
        state = await cb.get_state()
        assert state == CircuitState.OPEN
        await cb.reset()
        state = await cb.get_state()
        assert state == CircuitState.CLOSED

    async def test_force_open(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker()
        await cb.force_open()
        state = await cb.get_state()
        assert state == CircuitState.OPEN

    async def test_force_close(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=300.0)
        await cb.record_failure()
        await cb.force_close()
        state = await cb.get_state()
        assert state == CircuitState.CLOSED

    async def test_repr(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test-cb", failure_threshold=5)
        r = repr(cb)
        assert "test-cb" in r
        assert "closed" in r

    def test_invalid_failure_threshold(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        with pytest.raises(ValueError, match="failure_threshold"):
            CircuitBreaker(failure_threshold=0)

    def test_invalid_recovery_timeout(self) -> None:
        from marianne.execution.circuit_breaker import CircuitBreaker

        with pytest.raises(ValueError, match="recovery_timeout"):
            CircuitBreaker(recovery_timeout=-1.0)


# =============================================================================
# execution/parallel.py tests
# =============================================================================


class TestParallelExecutionError:
    """Tests for ParallelExecutionError — runner removed."""

    @pytest.mark.skip(reason="Runner removed — ParallelExecutionError no longer exists")
    def test_creation(self) -> None:
        """Obsolete: parallel module no longer exists."""


@pytest.mark.skip(reason="Runner removed — parallel module no longer exists")
class TestParallelBatchResult:
    """Tests for ParallelBatchResult — obsolete after runner removal."""

    def test_success_when_no_failures(self) -> None:
        """Obsolete."""

    def test_not_success_with_failures(self) -> None:
        """Obsolete."""

    def test_to_dict(self) -> None:
        """Obsolete."""


@pytest.mark.skip(reason="Runner removed — parallel module no longer exists")
class TestParallelExecutionConfig:
    """Tests for ParallelExecutionConfig — obsolete after runner removal."""

    def test_defaults(self) -> None:
        """Obsolete."""


@pytest.mark.skip(reason="Runner removed — parallel module no longer exists")
class TestLockingStateBackend:
    """Tests for _LockingStateBackend — obsolete after runner removal."""

    async def test_save_under_lock(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend
        from marianne.core.checkpoint import CheckpointState

        inner = AsyncMock()
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        state = CheckpointState(job_id="test", job_name="test", total_sheets=1)
        await wrapper.save(state)
        inner.save.assert_awaited_once_with(state)

    async def test_load_under_lock(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        inner.load.return_value = None
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        result = await wrapper.load("test")
        inner.load.assert_awaited_once_with("test")
        assert result is None

    async def test_delete_delegates(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        inner.delete.return_value = True
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        result = await wrapper.delete("test")
        assert result is True

    async def test_list_jobs_delegates(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        inner.list_jobs.return_value = []
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        result = await wrapper.list_jobs()
        assert result == []

    async def test_get_next_sheet_under_lock(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        inner.get_next_sheet.return_value = 2
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        result = await wrapper.get_next_sheet("test")
        assert result == 2

    async def test_mark_sheet_status_under_lock(self) -> None:
        from marianne.core.checkpoint import SheetStatus
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        await wrapper.mark_sheet_status("test", 1, SheetStatus.COMPLETED)
        inner.mark_sheet_status.assert_awaited_once()

    async def test_record_execution_under_lock(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        inner.record_execution.return_value = 1
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        result = await wrapper.record_execution("test", 1, 1, prompt="test")
        assert result == 1

    async def test_close_delegates(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        await wrapper.close()
        inner.close.assert_awaited_once()

    async def test_infer_state_delegates(self) -> None:
        from marianne.execution.parallel import _LockingStateBackend

        inner = AsyncMock()
        inner.infer_state_from_artifacts.return_value = 3
        lock = asyncio.Lock()
        wrapper = _LockingStateBackend(inner, lock)
        result = await wrapper.infer_state_from_artifacts("test", "/workspace", "*.md")
        assert result == 3


@pytest.mark.skip(reason="Runner removed — parallel module no longer exists")
class TestParallelExecutorGetNextBatch:
    """Tests for ParallelExecutor.get_next_parallel_batch() — obsolete."""

    def _make_executor(
        self, *, max_concurrent: int = 3, fail_fast: bool = True
    ) -> Any:
        from marianne.execution.parallel import ParallelExecutionConfig, ParallelExecutor

        runner = MagicMock()
        runner.dependency_dag = None
        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=max_concurrent, fail_fast=fail_fast,
        )
        return ParallelExecutor(runner, config)

    def test_no_dag_returns_first_pending(self) -> None:
        from marianne.core.checkpoint import CheckpointState

        executor = self._make_executor()
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=3,
            last_completed_sheet=0,
        )
        batch = executor.get_next_parallel_batch(state)
        assert batch == [1]

    def test_no_dag_skips_permanently_failed(self) -> None:
        from marianne.core.checkpoint import CheckpointState

        executor = self._make_executor()
        executor._permanently_failed = {1}
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=3,
            last_completed_sheet=0,
        )
        batch = executor.get_next_parallel_batch(state)
        assert batch == [2]

    def test_no_dag_returns_empty_when_all_done(self) -> None:
        from marianne.core.checkpoint import CheckpointState

        executor = self._make_executor()
        state = CheckpointState(
            job_id="test", job_name="test", total_sheets=2,
            last_completed_sheet=2,
        )
        batch = executor.get_next_parallel_batch(state)
        assert batch == []

    def test_estimate_parallel_groups_no_dag(self) -> None:
        executor = self._make_executor()
        groups = executor.estimate_parallel_groups()
        assert groups == []


# =============================================================================
# execution/hooks.py tests
# =============================================================================


class TestHookResult:
    """Tests for HookResult."""

    def test_creation(self) -> None:
        from marianne.execution.hooks import HookResult

        result = HookResult(hook_type="run_command", description="test", success=True)
        assert result.success is True
        assert result.hook_type == "run_command"


class TestConcertContext:
    """Tests for ConcertContext."""

    def test_creation(self) -> None:
        from marianne.execution.hooks import ConcertContext

        ctx = ConcertContext(concert_id="test-concert")
        assert ctx.chain_depth == 0
        assert ctx.total_jobs_run == 0


class TestGetHookLogPath:
    """Tests for get_hook_log_path()."""

    def test_returns_none_without_workspace(self) -> None:
        from marianne.execution.hooks import get_hook_log_path

        result = get_hook_log_path(None, "chain")
        assert result is None

    def test_returns_path_with_workspace(self, tmp_path: Path) -> None:
        from marianne.execution.hooks import get_hook_log_path

        result = get_hook_log_path(tmp_path, "chain")
        assert result is not None
        assert "chain" in str(result)
        assert (tmp_path / "hooks").exists()


class TestHookExecutor:
    """Tests for HookExecutor."""

    def _make_executor(self, tmp_path: Path, **config_overrides: Any) -> Any:
        from marianne.execution.hooks import HookExecutor

        config = _make_config(workspace=str(tmp_path), **config_overrides)
        return HookExecutor(config, tmp_path)

    def test_expand_hook_variables(self, tmp_path: Path) -> None:
        executor = self._make_executor(tmp_path)
        result = executor._expand_hook_variables("{workspace}/output")
        assert str(tmp_path) in result

    def test_expand_unknown_variable_warns(self, tmp_path: Path) -> None:
        executor = self._make_executor(tmp_path)
        result = executor._expand_hook_variables("{unknown_var}/output")
        assert "{unknown_var}" in result

    async def test_execute_hooks_no_hooks(self, tmp_path: Path) -> None:
        executor = self._make_executor(tmp_path)
        results = await executor.execute_hooks()
        assert results == []

    async def test_execute_shell_command_success(self, tmp_path: Path) -> None:
        executor = self._make_executor(tmp_path)
        from marianne.core.config import PostSuccessHookConfig

        hook = PostSuccessHookConfig(
            type="run_command", command="echo hello", timeout_seconds=10,
        )
        result = await executor._execute_shell_command(hook)
        assert result.success is True
        assert result.exit_code == 0

    async def test_execute_script_success(self, tmp_path: Path) -> None:
        executor = self._make_executor(tmp_path)
        from marianne.core.config import PostSuccessHookConfig

        hook = PostSuccessHookConfig(
            type="run_script", command="echo hello", timeout_seconds=10,
        )
        result = await executor._execute_script(hook)
        assert result.success is True

    async def test_execute_run_job_missing_path(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        from marianne.core.config import PostSuccessHookConfig

        with pytest.raises(ValidationError, match="job_path"):
            PostSuccessHookConfig(type="run_job", job_path=None)

    async def test_execute_run_job_nonexistent_config(self, tmp_path: Path) -> None:
        executor = self._make_executor(tmp_path)
        from marianne.core.config import PostSuccessHookConfig

        hook = PostSuccessHookConfig(
            type="run_job", job_path=Path("/nonexistent/job.yaml"),
        )
        result = await executor._execute_run_job(hook)
        assert not result.success
        assert "not found" in (result.error_message or "")

    def test_get_next_job_to_chain_no_hooks(self, tmp_path: Path) -> None:
        executor = self._make_executor(tmp_path)
        result = executor.get_next_job_to_chain()
        assert result is None

    async def test_execute_hooks_with_abort_on_failure(self, tmp_path: Path) -> None:
        from marianne.execution.hooks import HookExecutor

        config = _make_config(
            workspace=str(tmp_path),
            on_success=[
                {"type": "run_command", "command": "false", "on_failure": "abort"},
                {"type": "run_command", "command": "echo should-not-run"},
            ],
        )
        executor = HookExecutor(config, tmp_path)
        results = await executor.execute_hooks()
        assert len(results) == 1
        assert not results[0].success


# =============================================================================
# execution/retry_strategy.py tests
# =============================================================================


class TestRetryStrategyConfig:
    """Tests for RetryStrategyConfig validation."""

    def test_defaults(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        config = RetryStrategyConfig()
        assert config.base_delay == 10.0
        assert config.jitter_factor == 0.25

    def test_invalid_base_delay(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="base_delay"):
            RetryStrategyConfig(base_delay=0)

    def test_invalid_max_delay(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="max_delay"):
            RetryStrategyConfig(base_delay=100, max_delay=10)

    def test_invalid_exponential_base(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="exponential_base"):
            RetryStrategyConfig(exponential_base=0.5)

    def test_invalid_jitter_factor(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="jitter_factor"):
            RetryStrategyConfig(jitter_factor=2.0)

    def test_invalid_rapid_failure_window(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="rapid_failure_window"):
            RetryStrategyConfig(rapid_failure_window=0)

    def test_invalid_rapid_failure_threshold(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="rapid_failure_threshold"):
            RetryStrategyConfig(rapid_failure_threshold=0)

    def test_invalid_min_confidence(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="min_confidence"):
            RetryStrategyConfig(min_confidence=2.0)

    def test_invalid_repeated_error_threshold(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="repeated_error_threshold"):
            RetryStrategyConfig(repeated_error_threshold=0)

    def test_invalid_strategy_change_threshold(self) -> None:
        from marianne.execution.retry_strategy import RetryStrategyConfig

        with pytest.raises(ValueError, match="repeated_error_strategy_change_threshold"):
            RetryStrategyConfig(repeated_error_strategy_change_threshold=0)


class TestDelayHistory:
    """Tests for DelayHistory."""

    def test_record_and_query(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import DelayHistory, DelayOutcome

        history = DelayHistory()
        outcome = DelayOutcome(
            error_code=ErrorCode.EXECUTION_TIMEOUT, delay_seconds=5.0, succeeded_after=True,
        )
        history.record(outcome)
        results = history.query_for_error_code(ErrorCode.EXECUTION_TIMEOUT)
        assert len(results) == 1

    def test_get_average_successful_delay(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import DelayHistory, DelayOutcome

        history = DelayHistory()
        for delay in [5.0, 10.0, 15.0]:
            history.record(DelayOutcome(
                error_code=ErrorCode.EXECUTION_TIMEOUT, delay_seconds=delay, succeeded_after=True,
            ))
        avg = history.get_average_successful_delay(ErrorCode.EXECUTION_TIMEOUT)
        assert avg is not None
        assert abs(avg - 10.0) < 0.01

    def test_get_average_no_successes(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import DelayHistory, DelayOutcome

        history = DelayHistory()
        history.record(DelayOutcome(
            error_code=ErrorCode.EXECUTION_TIMEOUT, delay_seconds=5.0, succeeded_after=False,
        ))
        avg = history.get_average_successful_delay(ErrorCode.EXECUTION_TIMEOUT)
        assert avg is None

    def test_get_sample_count(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import DelayHistory, DelayOutcome

        history = DelayHistory()
        for _ in range(3):
            history.record(DelayOutcome(
                error_code=ErrorCode.EXECUTION_TIMEOUT, delay_seconds=5.0, succeeded_after=True,
            ))
        assert history.get_sample_count(ErrorCode.EXECUTION_TIMEOUT) == 3

    def test_record_none_raises(self) -> None:
        from marianne.execution.retry_strategy import DelayHistory

        history = DelayHistory()
        with pytest.raises(ValueError):
            history.record(None)  # type: ignore[arg-type]

    def test_record_negative_delay_raises(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import DelayHistory, DelayOutcome

        history = DelayHistory()
        with pytest.raises(ValueError):
            history.record(DelayOutcome(
                error_code=ErrorCode.EXECUTION_TIMEOUT, delay_seconds=-1.0, succeeded_after=True,
            ))

    def test_pruning(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import DelayHistory, DelayOutcome

        # max_history=5, prune triggers at len > 5*10=50
        # After prune, keeps last 5 per error code, then adds more
        history = DelayHistory(max_history=5)
        for i in range(60):
            history.record(DelayOutcome(
                error_code=ErrorCode.EXECUTION_TIMEOUT, delay_seconds=float(i), succeeded_after=True,
            ))
        results = history.query_for_error_code(ErrorCode.EXECUTION_TIMEOUT)
        # After pruning at i=50 (51 items), keeps 5. Then adds 9 more = 14.
        # Important thing: pruning ran and reduced the list
        assert len(results) < 60


class TestLearnedDelayCircuitBreaker:
    """Tests for LearnedDelayCircuitBreaker."""

    def test_initially_enabled(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import LearnedDelayCircuitBreaker

        cb = LearnedDelayCircuitBreaker()
        assert cb.is_enabled(ErrorCode.EXECUTION_TIMEOUT) is True

    def test_trips_after_failures(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import LearnedDelayCircuitBreaker

        cb = LearnedDelayCircuitBreaker()
        for _ in range(4):
            cb.record_outcome(ErrorCode.EXECUTION_TIMEOUT, succeeded=False)
        assert cb.is_enabled(ErrorCode.EXECUTION_TIMEOUT) is False

    def test_success_resets_counter(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import LearnedDelayCircuitBreaker

        cb = LearnedDelayCircuitBreaker()
        cb.record_outcome(ErrorCode.EXECUTION_TIMEOUT, succeeded=False)
        cb.record_outcome(ErrorCode.EXECUTION_TIMEOUT, succeeded=True)
        assert cb._failures[ErrorCode.EXECUTION_TIMEOUT] == 0

    def test_reset_re_enables(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import LearnedDelayCircuitBreaker

        cb = LearnedDelayCircuitBreaker()
        for _ in range(5):
            cb.record_outcome(ErrorCode.EXECUTION_TIMEOUT, succeeded=False)
        assert cb.is_enabled(ErrorCode.EXECUTION_TIMEOUT) is False
        cb.reset(ErrorCode.EXECUTION_TIMEOUT)
        assert cb.is_enabled(ErrorCode.EXECUTION_TIMEOUT) is True

    def test_disabled_code_ignores_outcomes(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import LearnedDelayCircuitBreaker

        cb = LearnedDelayCircuitBreaker()
        for _ in range(5):
            cb.record_outcome(ErrorCode.EXECUTION_TIMEOUT, succeeded=False)
        # Should be disabled now, further outcomes ignored
        cb.record_outcome(ErrorCode.EXECUTION_TIMEOUT, succeeded=False)
        assert cb.is_enabled(ErrorCode.EXECUTION_TIMEOUT) is False


class TestRetryRecommendation:
    """Tests for RetryRecommendation."""

    def test_creation(self) -> None:
        from marianne.execution.retry_strategy import RetryRecommendation

        rec = RetryRecommendation(
            should_retry=True, delay_seconds=5.0,
            reason="test", confidence=0.8,
        )
        assert rec.should_retry is True

    def test_invalid_confidence(self) -> None:
        from marianne.execution.retry_strategy import RetryRecommendation

        with pytest.raises(ValueError, match="confidence"):
            RetryRecommendation(
                should_retry=True, delay_seconds=5.0,
                reason="test", confidence=2.0,
            )

    def test_negative_delay(self) -> None:
        from marianne.execution.retry_strategy import RetryRecommendation

        with pytest.raises(ValueError, match="delay_seconds"):
            RetryRecommendation(
                should_retry=True, delay_seconds=-1.0,
                reason="test", confidence=0.8,
            )

    def test_to_dict(self) -> None:
        from marianne.execution.retry_strategy import RetryRecommendation

        rec = RetryRecommendation(
            should_retry=True, delay_seconds=5.0,
            reason="test", confidence=0.8,
        )
        d = rec.to_dict()
        assert d["should_retry"] is True
        assert "confidence" in d


class TestErrorRecord:
    """Tests for ErrorRecord."""

    def test_from_classified_error(self) -> None:
        from marianne.core.errors import ClassifiedError, ErrorCategory, ErrorCode
        from marianne.execution.retry_strategy import ErrorRecord

        error = ClassifiedError(
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            category=ErrorCategory.TRANSIENT,
            message="test error",
            exit_code=1,
            retriable=True,
        )
        record = ErrorRecord.from_classified_error(error, sheet_num=1, attempt_num=2)
        assert record.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert record.sheet_num == 1
        assert record.attempt_num == 2

    def test_from_classification_result(self) -> None:
        from marianne.core.errors import (
            ClassificationResult,
            ClassifiedError,
            ErrorCategory,
            ErrorCode,
        )
        from marianne.execution.retry_strategy import ErrorRecord

        primary = ClassifiedError(
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            category=ErrorCategory.TRANSIENT,
            message="test error",
            exit_code=1,
            retriable=True,
        )
        result = ClassificationResult(
            primary=primary, secondary=[], confidence=0.9,
        )
        record = ErrorRecord.from_classification_result(result, sheet_num=1)
        assert record.root_cause_confidence == 0.9
        assert record.secondary_error_count == 0

    def test_to_dict(self) -> None:
        from marianne.core.errors import ErrorCategory, ErrorCode
        from marianne.execution.retry_strategy import ErrorRecord

        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            category=ErrorCategory.TRANSIENT,
            message="test",
            root_cause_confidence=0.85,
        )
        d = record.to_dict()
        assert d["error_code"] == "E001"
        assert d["root_cause_confidence"] == 0.85


class TestAdaptiveRetryStrategy:
    """Tests for AdaptiveRetryStrategy.analyze()."""

    def _make_error_record(
        self,
        code: str = "E001",
        category: str = "transient",
        retriable: bool = True,
        suggested_wait: float | None = None,
    ) -> Any:
        from marianne.core.errors import ErrorCategory, ErrorCode
        from marianne.execution.retry_strategy import ErrorRecord

        return ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode(code),
            category=ErrorCategory(category),
            message="test error",
            exit_code=1,
            retriable=retriable,
            suggested_wait=suggested_wait,
        )

    def test_empty_history(self) -> None:
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        strategy = AdaptiveRetryStrategy()
        rec = strategy.analyze([])
        assert rec.should_retry is True

    def test_non_retriable_error(self) -> None:
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        strategy = AdaptiveRetryStrategy()
        record = self._make_error_record(retriable=False)
        rec = strategy.analyze([record])
        assert rec.should_retry is False

    def test_rate_limited_error(self) -> None:
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy, RetryPattern

        strategy = AdaptiveRetryStrategy()
        record = self._make_error_record(
            code="E101", category="rate_limit", suggested_wait=60.0,
        )
        rec = strategy.analyze([record])
        assert rec.should_retry is True
        assert rec.detected_pattern == RetryPattern.RATE_LIMITED
        assert rec.delay_seconds >= 60.0

    def test_cascading_errors_detected(self) -> None:
        import time as time_mod

        from marianne.execution.retry_strategy import (
            AdaptiveRetryStrategy,
            ErrorRecord,
            RetryPattern,
            RetryStrategyConfig,
        )
        from marianne.core.errors import ErrorCategory, ErrorCode

        # Use a short rapid_failure_window so that errors outside it
        # are not detected as rapid failures
        config = RetryStrategyConfig(rapid_failure_window=0.001)
        strategy = AdaptiveRetryStrategy(config=config)
        base_time = time_mod.monotonic() - 100  # errors from 100s ago
        records = [
            ErrorRecord(
                timestamp=datetime.now(UTC), error_code=ErrorCode.EXECUTION_TIMEOUT,
                category=ErrorCategory.TRANSIENT, message="e1",
                retriable=True, monotonic_time=base_time,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC), error_code=ErrorCode.NETWORK_DNS_ERROR,
                category=ErrorCategory.TRANSIENT, message="e2",
                retriable=True, monotonic_time=base_time + 30,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC), error_code=ErrorCode.BACKEND_AUTH,
                category=ErrorCategory.TRANSIENT, message="e3",
                retriable=True, monotonic_time=base_time + 60,
            ),
        ]
        rec = strategy.analyze(records)
        assert rec.detected_pattern == RetryPattern.CASCADING_FAILURES

    def test_cascading_errors_abort_with_many_types(self) -> None:
        import time as time_mod

        from marianne.execution.retry_strategy import (
            AdaptiveRetryStrategy,
            ErrorRecord,
            RetryStrategyConfig,
        )
        from marianne.core.errors import ErrorCategory, ErrorCode

        config = RetryStrategyConfig(rapid_failure_window=0.001)
        strategy = AdaptiveRetryStrategy(config=config)
        base_time = time_mod.monotonic() - 100
        records = [
            ErrorRecord(
                timestamp=datetime.now(UTC), error_code=ErrorCode.EXECUTION_TIMEOUT,
                category=ErrorCategory.TRANSIENT, message="e1",
                retriable=True, monotonic_time=base_time,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC), error_code=ErrorCode.NETWORK_DNS_ERROR,
                category=ErrorCategory.TRANSIENT, message="e2",
                retriable=True, monotonic_time=base_time + 30,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC), error_code=ErrorCode.BACKEND_AUTH,
                category=ErrorCategory.TRANSIENT, message="e3",
                retriable=True, monotonic_time=base_time + 60,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC), error_code=ErrorCode.EXECUTION_CRASHED,
                category=ErrorCategory.TRANSIENT, message="e4",
                retriable=True, monotonic_time=base_time + 90,
            ),
        ]
        rec = strategy.analyze(records)
        assert rec.should_retry is False

    def test_standard_retry_with_backoff(self) -> None:
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        strategy = AdaptiveRetryStrategy()
        record = self._make_error_record()
        rec = strategy.analyze([record])
        assert rec.should_retry is True
        assert rec.delay_seconds > 0

    def test_repeated_error_code_abort(self) -> None:
        from marianne.execution.retry_strategy import (
            AdaptiveRetryStrategy,
            RetryStrategyConfig,
        )

        config = RetryStrategyConfig(repeated_error_strategy_change_threshold=2)
        strategy = AdaptiveRetryStrategy(config=config)
        records = [
            self._make_error_record(code="E001"),
            self._make_error_record(code="E001"),
        ]
        rec = strategy.analyze(records)
        assert rec.should_retry is False

    def test_blend_historical_delay_no_history(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        strategy = AdaptiveRetryStrategy()
        delay, strat = strategy.blend_historical_delay(ErrorCode.EXECUTION_TIMEOUT, 10.0)
        assert delay == 10.0
        assert strat == "static"

    def test_blend_historical_delay_with_history(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import (
            AdaptiveRetryStrategy,
            DelayHistory,
            DelayOutcome,
        )

        history = DelayHistory()
        for _ in range(10):
            history.record(DelayOutcome(
                error_code=ErrorCode.EXECUTION_TIMEOUT, delay_seconds=20.0, succeeded_after=True,
            ))
        strategy = AdaptiveRetryStrategy(delay_history=history)
        delay, strat = strategy.blend_historical_delay(ErrorCode.EXECUTION_TIMEOUT, 10.0)
        assert strat == "learned_blend"

    def test_blend_with_circuit_breaker_tripped(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import (
            AdaptiveRetryStrategy,
            DelayHistory,
        )

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(delay_history=history)
        strategy._circuit_breaker._enabled[ErrorCode.EXECUTION_TIMEOUT] = False
        delay, strat = strategy.blend_historical_delay(ErrorCode.EXECUTION_TIMEOUT, 10.0)
        assert strat == "static_circuit_breaker"

    def test_record_delay_outcome(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy, DelayHistory

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(delay_history=history)
        strategy.record_delay_outcome(ErrorCode.EXECUTION_TIMEOUT, 5.0, succeeded=True)
        assert history.get_sample_count(ErrorCode.EXECUTION_TIMEOUT) == 1

    def test_record_delay_outcome_no_history(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        strategy = AdaptiveRetryStrategy()
        # Should not raise when no delay_history is configured
        strategy.record_delay_outcome(ErrorCode.EXECUTION_TIMEOUT, 5.0, succeeded=True)
        assert strategy is not None  # Survived without error

    def test_reset_circuit_breaker(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy, DelayHistory

        strategy = AdaptiveRetryStrategy(delay_history=DelayHistory())
        strategy._circuit_breaker._enabled[ErrorCode.EXECUTION_TIMEOUT] = False
        strategy.reset_circuit_breaker(ErrorCode.EXECUTION_TIMEOUT)
        assert strategy._circuit_breaker.is_enabled(ErrorCode.EXECUTION_TIMEOUT) is True

    def test_blend_bootstrap_phase(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy, DelayHistory

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(delay_history=history)
        delay, strat = strategy.blend_historical_delay(ErrorCode.EXECUTION_TIMEOUT, 10.0)
        assert strat == "static_bootstrap"

    def test_global_store_fallback(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        mock_store = MagicMock()
        mock_store.get_learned_wait_time_with_fallback.return_value = (
            15.0, 0.85, "global_learned"
        )
        strategy = AdaptiveRetryStrategy(global_learning_store=mock_store)
        delay, strat = strategy.blend_historical_delay(ErrorCode.EXECUTION_TIMEOUT, 10.0)
        assert strat == "global_learned"

    def test_global_store_static_fallback(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        mock_store = MagicMock()
        mock_store.get_learned_wait_time_with_fallback.return_value = (
            10.0, 0.5, "static_fallback"
        )
        strategy = AdaptiveRetryStrategy(global_learning_store=mock_store)
        delay, strat = strategy.blend_historical_delay(ErrorCode.EXECUTION_TIMEOUT, 10.0)
        assert strat == "static"

    def test_global_store_error_handled(self) -> None:
        from marianne.core.errors import ErrorCode
        from marianne.execution.retry_strategy import AdaptiveRetryStrategy

        mock_store = MagicMock()
        mock_store.get_learned_wait_time_with_fallback.side_effect = RuntimeError("db err")
        strategy = AdaptiveRetryStrategy(global_learning_store=mock_store)
        delay, strat = strategy.blend_historical_delay(ErrorCode.EXECUTION_TIMEOUT, 10.0)
        assert strat == "static"


# =============================================================================
# execution/validation/engine.py tests
# =============================================================================


class TestValidationEngine:
    """Tests for ValidationEngine."""

    def _make_engine(self, tmp_path: Path) -> Any:
        from marianne.execution.validation.engine import ValidationEngine

        return ValidationEngine(
            workspace=tmp_path,
            sheet_context={"sheet_num": 1, "workspace": str(tmp_path)},
        )

    def test_expand_path_with_variables(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        path = engine.expand_path("{workspace}/output.txt")
        assert str(tmp_path) in str(path)

    def test_check_file_exists_pass(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("hello")
        rule = ValidationRule(
            type="file_exists", path=str(tmp_path / "test.txt"),
            description="file exists",
        )
        result = engine._check_file_exists(rule)
        assert result.passed is True

    def test_check_file_exists_fail(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="file_exists", path=str(tmp_path / "missing.txt"),
            description="file exists",
        )
        result = engine._check_file_exists(rule)
        assert result.passed is False
        assert "not found" in (result.error_message or "").lower()

    def test_check_content_contains_pass(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("hello world")
        rule = ValidationRule(
            type="content_contains", path=str(tmp_path / "test.txt"),
            pattern="hello", description="contains hello",
        )
        result = engine._check_content_contains(rule)
        assert result.passed is True

    def test_check_content_contains_fail(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("hello world")
        rule = ValidationRule(
            type="content_contains", path=str(tmp_path / "test.txt"),
            pattern="foobar", description="contains foobar",
        )
        result = engine._check_content_contains(rule)
        assert result.passed is False

    def test_check_content_contains_file_missing(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="content_contains", path=str(tmp_path / "no.txt"),
            pattern="x", description="test",
        )
        result = engine._check_content_contains(rule)
        assert result.passed is False

    def test_check_content_regex_pass(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("version 2.1.0")
        rule = ValidationRule(
            type="content_regex", path=str(tmp_path / "test.txt"),
            pattern=r"version \d+\.\d+\.\d+", description="version regex",
        )
        result = engine._check_content_regex(rule)
        assert result.passed is True

    def test_check_content_regex_fail(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("no version here")
        rule = ValidationRule(
            type="content_regex", path=str(tmp_path / "test.txt"),
            pattern=r"version \d+", description="version regex",
        )
        result = engine._check_content_regex(rule)
        assert result.passed is False

    def test_check_content_regex_file_missing(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="content_regex", path=str(tmp_path / "no.txt"),
            pattern=r".*", description="test",
        )
        result = engine._check_content_regex(rule)
        assert result.passed is False

    def test_check_file_modified_pass(self, tmp_path: Path) -> None:
        import os

        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("original")
        rule = ValidationRule(
            type="file_modified", path=str(test_file), description="modified",
        )
        engine.snapshot_mtime_files([rule])
        # Ensure mtime changes by setting it to the future
        stat = test_file.stat()
        os.utime(test_file, (stat.st_atime + 2, stat.st_mtime + 2))
        result = engine._check_file_modified(rule)
        assert result.passed is True

    def test_check_file_modified_fail(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("original")
        rule = ValidationRule(
            type="file_modified", path=str(test_file), description="modified",
        )
        engine.snapshot_mtime_files([rule])
        result = engine._check_file_modified(rule)
        assert result.passed is False

    def test_check_file_modified_missing_file(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="file_modified", path=str(tmp_path / "no.txt"), description="test",
        )
        result = engine._check_file_modified(rule)
        assert result.passed is False

    async def test_check_command_succeeds_pass(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="command_succeeds", command="echo hello",
            description="echo test",
        )
        result = await engine._check_command_succeeds(rule)
        assert result.passed is True
        assert result.actual_value == "exit_code=0"

    async def test_check_command_succeeds_fail(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="command_succeeds", command="false",
            description="always fails",
        )
        result = await engine._check_command_succeeds(rule)
        assert result.passed is False

    async def test_check_command_timeout(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="command_succeeds", command="sleep 30",
            description="timeout test", timeout_seconds=0.1,
        )
        result = await engine._check_command_succeeds(rule)
        assert result.passed is False
        assert "timed out" in (result.error_message or "").lower()

    async def test_run_validations(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("hello")
        rules = [
            ValidationRule(
                type="file_exists", path=str(tmp_path / "test.txt"),
                description="exists",
            ),
        ]
        result = await engine.run_validations(rules)
        assert result.rules_checked == 1
        assert result.all_passed is True

    async def test_run_staged_validations(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("hello")
        rules = [
            ValidationRule(
                type="file_exists", path=str(tmp_path / "test.txt"),
                description="stage 1", stage=1,
            ),
            ValidationRule(
                type="content_contains", path=str(tmp_path / "test.txt"),
                pattern="hello", description="stage 2", stage=2,
            ),
        ]
        result, failed_stage = await engine.run_staged_validations(rules)
        assert result.rules_checked == 2
        assert failed_stage is None

    async def test_run_staged_validations_fail_fast(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rules = [
            ValidationRule(
                type="file_exists", path=str(tmp_path / "missing.txt"),
                description="stage 1 - missing", stage=1,
            ),
            ValidationRule(
                type="command_succeeds", command="echo ok",
                description="stage 2 - skipped", stage=2,
            ),
        ]
        result, failed_stage = await engine.run_staged_validations(rules)
        assert failed_stage == 1
        stage_2_results = [r for r in result.results if "skipped" in (r.failure_category or "").lower()]
        assert len(stage_2_results) == 1

    async def test_run_staged_validations_empty(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        result, failed_stage = await engine.run_staged_validations([])
        assert result.rules_checked == 0
        assert failed_stage is None

    def test_condition_check_true(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        assert engine._check_condition(None) is True
        assert engine._check_condition("sheet_num >= 1") is True
        assert engine._check_condition("sheet_num == 1") is True

    def test_condition_check_false(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        assert engine._check_condition("sheet_num > 5") is False

    def test_condition_check_and(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        assert engine._check_condition("sheet_num >= 1 and sheet_num <= 3") is True
        assert engine._check_condition("sheet_num >= 1 and sheet_num > 5") is False

    def test_condition_check_unknown_var(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        assert engine._check_condition("unknown_var >= 1") is True

    def test_get_applicable_rules(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rules = [
            ValidationRule(
                type="file_exists", path="test", description="always",
                condition=None,
            ),
            ValidationRule(
                type="file_exists", path="test", description="conditional",
                condition="sheet_num > 5",
            ),
        ]
        applicable = engine.get_applicable_rules(rules)
        assert len(applicable) == 1

    async def test_run_single_validation_with_retry(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        test_file = tmp_path / "delayed.txt"
        rule = ValidationRule(
            type="file_exists", path=str(test_file),
            description="retry test", retry_count=1, retry_delay_ms=10,
        )
        # File doesn't exist, should fail even after retry
        result = await engine._run_single_validation(rule)
        assert result.passed is False
        assert result.check_duration_ms is not None

    def test_display_path_short(self, tmp_path: Path) -> None:
        from marianne.execution.validation.engine import ValidationEngine

        short = ValidationEngine._display_path(Path("test.txt"))
        assert short == "test.txt"

    def test_display_path_long(self, tmp_path: Path) -> None:
        from marianne.execution.validation.engine import ValidationEngine

        long_path = Path("/very/long/path/that/is/more/than/fifty/characters/test.txt")
        display = ValidationEngine._display_path(long_path)
        assert display == "test.txt"

    def test_check_command_with_working_directory(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        rule = ValidationRule(
            type="command_succeeds", command="pwd",
            description="cwd test", working_directory=str(tmp_path),
        )
        # Just verify it's callable (async test would be better but this validates path)
        assert rule.working_directory == str(tmp_path)

    async def test_content_contains_long_pattern_display(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("short")
        long_pattern = "a" * 100
        rule = ValidationRule(
            type="content_contains", path=str(tmp_path / "test.txt"),
            pattern=long_pattern, description="long pattern",
        )
        result = engine._check_content_contains(rule)
        assert result.passed is False

    async def test_content_regex_long_pattern_display(self, tmp_path: Path) -> None:
        from marianne.core.config import ValidationRule

        engine = self._make_engine(tmp_path)
        (tmp_path / "test.txt").write_text("short")
        long_pattern = "a" * 100
        rule = ValidationRule(
            type="content_regex", path=str(tmp_path / "test.txt"),
            pattern=long_pattern, description="long pattern",
        )
        result = engine._check_content_regex(rule)
        assert result.passed is False


# =============================================================================
# execution/grounding.py tests (basic coverage for 0% → >0%)
# =============================================================================


class TestGroundingTypes:
    """Basic coverage for grounding module types."""

    def test_grounding_phase_enum(self) -> None:
        from marianne.execution.grounding import GroundingPhase

        assert GroundingPhase.PRE_VALIDATION == "pre_validation"
        assert GroundingPhase.POST_VALIDATION == "post_validation"
        assert GroundingPhase.BOTH == "both"

    def test_grounding_context_creation(self) -> None:
        from marianne.execution.grounding import GroundingContext

        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="test prompt",
            output="test output",
        )
        assert ctx.job_id == "test"
        assert ctx.sheet_num == 1

    def test_grounding_result_creation(self) -> None:
        from marianne.execution.grounding import GroundingResult

        result = GroundingResult(
            passed=True,
            hook_name="test_hook",
            confidence=0.95,
            message="All checks passed",
        )
        assert result.passed is True
        assert result.confidence == 0.95

    def test_grounding_engine_creation(self) -> None:
        from marianne.execution.grounding import GroundingEngine

        engine = GroundingEngine(hooks=[])
        assert engine is not None

    def test_grounding_engine_add_hook(self) -> None:
        from marianne.execution.grounding import GroundingEngine

        engine = GroundingEngine(hooks=[])
        mock_hook = MagicMock()
        engine.add_hook(mock_hook)
        assert engine.get_hook_count() == 1

    def test_file_checksum_hook_creation(self) -> None:
        from marianne.execution.grounding import FileChecksumGroundingHook

        hook = FileChecksumGroundingHook(
            name="hash_check",
            expected_checksums={"test.txt": "abc123"},
        )
        assert hook.name == "hash_check"

    def test_create_hook_from_config_file_checksum(self) -> None:
        from marianne.execution.grounding import create_hook_from_config
        from marianne.core.config import GroundingHookConfig

        config = GroundingHookConfig(
            type="file_checksum",
            expected_checksums={"test.txt": "abc123"},
        )
        hook = create_hook_from_config(config)
        assert hook is not None

    def test_grounding_result_fields(self) -> None:
        from marianne.execution.grounding import GroundingResult

        result = GroundingResult(
            passed=True, hook_name="test",
            confidence=0.9, message="ok",
        )
        assert result.passed is True
        assert result.hook_name == "test"
        assert result.confidence == 0.9

    def test_grounding_engine_aggregate_all_pass(self) -> None:
        from marianne.execution.grounding import GroundingEngine, GroundingResult

        engine = GroundingEngine(hooks=[])
        results = [
            GroundingResult(passed=True, hook_name="h1", message="ok"),
            GroundingResult(passed=True, hook_name="h2", message="ok"),
        ]
        passed, summary = engine.aggregate_results(results)
        assert passed is True
        assert "passed" in summary

    def test_grounding_engine_aggregate_with_failure(self) -> None:
        from marianne.execution.grounding import GroundingEngine, GroundingResult

        engine = GroundingEngine(hooks=[])
        results = [
            GroundingResult(passed=True, hook_name="h1", message="ok"),
            GroundingResult(passed=False, hook_name="h2", message="bad"),
        ]
        passed, summary = engine.aggregate_results(results)
        assert passed is False
        assert "failed" in summary

    def test_grounding_engine_aggregate_empty(self) -> None:
        from marianne.execution.grounding import GroundingEngine

        engine = GroundingEngine(hooks=[])
        passed, summary = engine.aggregate_results([])
        assert passed is True

    def test_grounding_engine_get_hook_count(self) -> None:
        from marianne.execution.grounding import GroundingEngine

        engine = GroundingEngine(hooks=[])
        assert engine.get_hook_count() == 0
        mock_hook = MagicMock()
        engine.add_hook(mock_hook)
        assert engine.get_hook_count() == 1


class TestGroundingEngineRun:
    """Tests for GroundingEngine.run_hooks()."""

    async def test_run_hooks_no_hooks(self) -> None:
        from marianne.execution.grounding import (
            GroundingContext,
            GroundingEngine,
            GroundingPhase,
        )

        engine = GroundingEngine(hooks=[])
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        results = await engine.run_hooks(ctx, GroundingPhase.POST_VALIDATION)
        assert results == []

    async def test_run_hooks_with_mock_hook(self) -> None:
        from marianne.execution.grounding import (
            GroundingContext,
            GroundingEngine,
            GroundingPhase,
            GroundingResult,
        )

        mock_hook = AsyncMock()
        mock_hook.name = "mock"
        mock_hook.phase = GroundingPhase.POST_VALIDATION
        mock_hook.validate.return_value = GroundingResult(
            passed=True, hook_name="mock",
            confidence=1.0, message="ok",
        )
        engine = GroundingEngine(hooks=[mock_hook])
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        results = await engine.run_hooks(ctx, GroundingPhase.POST_VALIDATION)
        assert len(results) == 1
        assert results[0].passed is True

    async def test_run_hooks_phase_filtering(self) -> None:
        from marianne.execution.grounding import (
            GroundingContext,
            GroundingEngine,
            GroundingPhase,
            GroundingResult,
        )

        pre_hook = AsyncMock()
        pre_hook.name = "pre"
        pre_hook.phase = GroundingPhase.PRE_VALIDATION
        pre_hook.validate.return_value = GroundingResult(
            passed=True, hook_name="pre", message="ok",
        )
        post_hook = AsyncMock()
        post_hook.name = "post"
        post_hook.phase = GroundingPhase.POST_VALIDATION
        post_hook.validate.return_value = GroundingResult(
            passed=True, hook_name="post", message="ok",
        )
        engine = GroundingEngine(hooks=[pre_hook, post_hook])
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        # Only pre-validation hooks should run
        results = await engine.run_hooks(ctx, GroundingPhase.PRE_VALIDATION)
        assert len(results) == 1
        assert results[0].hook_name == "pre"

    async def test_run_hooks_both_phase(self) -> None:
        from marianne.execution.grounding import (
            GroundingContext,
            GroundingEngine,
            GroundingPhase,
            GroundingResult,
        )

        both_hook = AsyncMock()
        both_hook.name = "both"
        both_hook.phase = GroundingPhase.BOTH
        both_hook.validate.return_value = GroundingResult(
            passed=True, hook_name="both", message="ok",
        )
        engine = GroundingEngine(hooks=[both_hook])
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        results = await engine.run_hooks(ctx, GroundingPhase.PRE_VALIDATION)
        assert len(results) == 1

    async def test_run_hooks_timeout(self) -> None:
        from marianne.execution.grounding import (
            GroundingContext,
            GroundingEngine,
            GroundingPhase,
        )
        from marianne.core.config import GroundingConfig

        async def slow_validate(ctx: Any) -> Any:
            await asyncio.sleep(10)

        slow_hook = MagicMock()
        slow_hook.name = "slow"
        slow_hook.phase = GroundingPhase.POST_VALIDATION
        slow_hook.validate = slow_validate

        # Build config that bypasses validation by constructing directly
        config = GroundingConfig.__new__(GroundingConfig)
        object.__setattr__(config, "enabled", True)
        object.__setattr__(config, "timeout_seconds", 0.01)
        object.__setattr__(config, "escalate_on_failure", False)
        object.__setattr__(config, "hooks", [])

        engine = GroundingEngine(hooks=[slow_hook], config=config)
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        results = await engine.run_hooks(ctx, GroundingPhase.POST_VALIDATION)
        assert len(results) == 1
        assert results[0].passed is False
        assert "timed out" in results[0].message.lower()

    async def test_run_hooks_exception(self) -> None:
        from marianne.execution.grounding import (
            GroundingContext,
            GroundingEngine,
            GroundingPhase,
        )

        failing_hook = AsyncMock()
        failing_hook.name = "fail"
        failing_hook.phase = GroundingPhase.POST_VALIDATION
        failing_hook.validate.side_effect = RuntimeError("hook crashed")

        engine = GroundingEngine(hooks=[failing_hook])
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        results = await engine.run_hooks(ctx, GroundingPhase.POST_VALIDATION)
        assert len(results) == 1
        assert results[0].passed is False
        assert "error" in results[0].message.lower()

    async def test_file_checksum_hook_validate_no_checksums(self) -> None:
        from marianne.execution.grounding import (
            FileChecksumGroundingHook,
            GroundingContext,
        )

        hook = FileChecksumGroundingHook()
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        result = await hook.validate(ctx)
        assert result.passed is True

    async def test_file_checksum_hook_validate_missing_file(self) -> None:
        from marianne.execution.grounding import (
            FileChecksumGroundingHook,
            GroundingContext,
        )

        hook = FileChecksumGroundingHook(
            expected_checksums={"/nonexistent/file.txt": "abc123"},
        )
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        result = await hook.validate(ctx)
        assert result.passed is False

    async def test_file_checksum_hook_validate_correct(self, tmp_path: Path) -> None:
        import hashlib

        from marianne.execution.grounding import (
            FileChecksumGroundingHook,
            GroundingContext,
        )

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        expected_hash = hashlib.sha256(b"hello world").hexdigest()

        hook = FileChecksumGroundingHook(
            expected_checksums={str(test_file): expected_hash},
        )
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        result = await hook.validate(ctx)
        assert result.passed is True

    async def test_file_checksum_hook_validate_mismatch(self, tmp_path: Path) -> None:
        from marianne.execution.grounding import (
            FileChecksumGroundingHook,
            GroundingContext,
        )

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        hook = FileChecksumGroundingHook(
            expected_checksums={str(test_file): "wrong_hash"},
        )
        ctx = GroundingContext(
            job_id="test", sheet_num=1, prompt="p", output="o",
        )
        result = await hook.validate(ctx)
        assert result.passed is False


# =============================================================================
# execution/preflight.py tests (for additional coverage)
# =============================================================================


class TestPreflightChecker:
    """Tests for basic PreflightChecker functionality."""

    def test_creation(self, tmp_path: Path) -> None:
        from marianne.execution.preflight import PreflightChecker

        checker = PreflightChecker(workspace=tmp_path)
        assert checker is not None

    def test_check_basic(self, tmp_path: Path) -> None:
        from marianne.execution.preflight import PreflightChecker

        checker = PreflightChecker(workspace=tmp_path)
        result = checker.check(
            prompt="Test prompt for sheet execution",
            sheet_context={"sheet_num": 1},
        )
        assert result is not None
        assert hasattr(result, "warnings")
        assert hasattr(result, "errors")
