"""TDD tests for rate limit backpressure PENDING state (F-110).

When rate limits cause backpressure but system resources are healthy,
the conductor should accept jobs as PENDING instead of rejecting them.
Pending jobs start automatically when rate limits clear. Pending jobs
can be cancelled via ``mzt cancel``.

Red first, then green.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.backpressure import BackpressureController


# =============================================================================
# BackpressureController.rejection_reason — why was the job rejected?
# =============================================================================


class TestRejectionReason:
    """Test that backpressure exposes the reason for rejection."""

    def _make_controller(
        self,
        *,
        memory_pct: float = 0.3,
        accepting_work: bool = True,
        rate_limited: bool = False,
    ) -> BackpressureController:
        """Create a controller with configurable resource state."""
        monitor = MagicMock()
        monitor.current_memory_mb.return_value = memory_pct * 1000
        monitor.max_memory_mb = 1000
        monitor.is_accepting_work.return_value = accepting_work
        monitor.is_degraded = False

        coordinator = MagicMock()
        coordinator.active_limits = {"claude-cli": 9999.0} if rate_limited else {}

        return BackpressureController(monitor, coordinator)

    def test_no_pressure_returns_none(self) -> None:
        """No pressure -> no rejection reason."""
        ctrl = self._make_controller()
        assert ctrl.rejection_reason() is None

    def test_rate_limit_only_returns_none(self) -> None:
        """Rate limit active + resources healthy -> None (F-149).

        Rate limits alone no longer cause rejection. Per-instrument rate
        limits are handled at the sheet dispatch level.
        """
        ctrl = self._make_controller(rate_limited=True)
        assert ctrl.rejection_reason() is None

    def test_high_memory_returns_resource(self) -> None:
        """High memory (>85%) without rate limits -> 'resource'."""
        ctrl = self._make_controller(memory_pct=0.90)
        assert ctrl.rejection_reason() == "resource"

    def test_critical_memory_returns_resource(self) -> None:
        """Critical memory (>95%) -> 'resource' even with rate limits."""
        ctrl = self._make_controller(memory_pct=0.96, rate_limited=True)
        assert ctrl.rejection_reason() == "resource"

    def test_not_accepting_work_returns_resource(self) -> None:
        """Process limit exceeded -> 'resource'."""
        ctrl = self._make_controller(accepting_work=False, rate_limited=True)
        assert ctrl.rejection_reason() == "resource"

    def test_rate_limit_with_moderate_memory_returns_none(self) -> None:
        """Rate limit + moderate memory (< 85%) -> None (F-149).

        Rate limits no longer contribute to job rejection.
        """
        ctrl = self._make_controller(memory_pct=0.60, rate_limited=True)
        assert ctrl.rejection_reason() is None

    def test_high_memory_with_rate_limit_returns_resource(self) -> None:
        """High memory (>85%) + rate limit -> 'resource' (memory is the danger)."""
        ctrl = self._make_controller(memory_pct=0.90, rate_limited=True)
        assert ctrl.rejection_reason() == "resource"


# =============================================================================
# JobManager.submit_job — PENDING instead of rejected on rate limits
# =============================================================================


class TestSubmitJobPending:
    """Test that submit_job queues as PENDING during rate limit backpressure."""

    @pytest.fixture
    def manager_mocks(self) -> dict[str, MagicMock | BackpressureController]:
        """Create mock objects for a minimal JobManager test."""
        monitor = MagicMock()
        monitor.current_memory_mb.return_value = 300  # 30% of 1000
        monitor.max_memory_mb = 1000
        monitor.is_accepting_work.return_value = True
        monitor.is_degraded = False

        coordinator = MagicMock()
        coordinator.active_limits = {"claude-cli": 300.0}  # rate limited, 5m remaining

        backpressure = BackpressureController(monitor, coordinator)
        return {
            "monitor": monitor,
            "coordinator": coordinator,
            "backpressure": backpressure,
        }

    @pytest.mark.asyncio
    async def test_rate_limit_does_not_reject_or_pend(
        self,
        manager_mocks: dict[str, MagicMock | BackpressureController],
        tmp_path: Path,
    ) -> None:
        """F-149: Rate limits alone no longer cause PENDING or rejection.

        When rate limits are the only pressure, should_accept_job()
        returns True and the job proceeds through normal submission.
        Per-instrument rate limiting is handled at the sheet dispatch level.
        """
        backpressure = manager_mocks["backpressure"]
        # Verify backpressure accepts the job despite active rate limits
        assert backpressure.should_accept_job() is True  # type: ignore[union-attr]
        assert backpressure.rejection_reason() is None  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_resource_pressure_still_rejects(self, tmp_path: Path) -> None:
        """When resource pressure (not just rate limits), job is rejected."""
        from marianne.daemon.manager import JobManager
        from marianne.daemon.types import JobRequest

        monitor = MagicMock()
        monitor.current_memory_mb.return_value = 960  # 96% — CRITICAL
        monitor.max_memory_mb = 1000
        monitor.is_accepting_work.return_value = True
        monitor.is_degraded = False

        coordinator = MagicMock()
        coordinator.active_limits = {}

        backpressure = BackpressureController(monitor, coordinator)

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)
            mgr._backpressure = backpressure
            mgr._shutting_down = False

            request = JobRequest(
                config_path=tmp_path / "test.yaml",
                fresh=False,
                self_healing=False,
                self_healing_auto_confirm=False,
            )

            response = await mgr.submit_job(request)
            assert response.status == "rejected"


# =============================================================================
# Cancel pending jobs
# =============================================================================


class TestCancelPendingJob:
    """Test that pending jobs can be cancelled before they start."""

    @pytest.mark.asyncio
    async def test_cancel_pending_job_removes_from_queue(self) -> None:
        """Cancelling a pending job removes it from _pending_jobs."""
        from marianne.daemon.manager import JobManager

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)
            mgr._pending_jobs = {"test-job": MagicMock()}
            mgr._jobs = {}
            mgr._job_meta = {}
            mgr._live_states = {}
            mgr._registry = AsyncMock()

            result = await mgr.cancel_job("test-job")
            assert result is True
            assert "test-job" not in mgr._pending_jobs

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_job_returns_false(self) -> None:
        """Cancelling a job that doesn't exist returns False."""
        from marianne.daemon.manager import JobManager

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)
            mgr._pending_jobs = {}
            mgr._jobs = {}
            mgr._job_meta = {}

            result = await mgr.cancel_job("no-such-job")
            assert result is False


# =============================================================================
# Pending job auto-start when rate limits clear
# =============================================================================


class TestPendingJobAutoStart:
    """Test that pending jobs start when rate limits clear."""

    @pytest.mark.asyncio
    async def test_start_pending_jobs_creates_task(self) -> None:
        """When rate limits clear, pending jobs get started."""
        from marianne.daemon.manager import JobManager

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)

            mock_request = MagicMock()
            mgr._pending_jobs = {"test-job": mock_request}
            mgr._jobs = {}
            mgr._job_meta = {}
            mgr._live_states = {}
            mgr._registry = AsyncMock()
            mgr._backpressure = MagicMock()
            mgr._backpressure.should_accept_job.return_value = True

            # Mock asyncio.create_task to capture calls
            created_tasks: list[str] = []

            async def mock_run_job_task(job_id: str, request: object) -> None:
                pass  # pragma: no cover

            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()

            with (
                patch.object(mgr, "_run_job_task", side_effect=mock_run_job_task),
                patch("asyncio.create_task", return_value=mock_task) as mock_create,
            ):
                await mgr._start_pending_jobs()

            assert mock_create.called
            assert "test-job" not in mgr._pending_jobs

    @pytest.mark.asyncio
    async def test_pending_jobs_stay_if_still_pressured(self) -> None:
        """Pending jobs remain queued if backpressure hasn't cleared."""
        from marianne.daemon.manager import JobManager

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)

            mock_request = MagicMock()
            mgr._pending_jobs = {"test-job": mock_request}
            mgr._jobs = {}
            mgr._backpressure = MagicMock()
            mgr._backpressure.should_accept_job.return_value = False

            await mgr._start_pending_jobs()

            assert "test-job" in mgr._pending_jobs


# =============================================================================
# CLI handles "pending" status
# =============================================================================


class TestCliPendingStatus:
    """Test that the CLI handles the 'pending' response from the conductor."""

    def test_pending_response_shows_info_not_error(self) -> None:
        """When conductor returns 'pending', CLI shows info message, not error."""
        from marianne.cli.commands.run import _handle_pending_response

        with patch("marianne.cli.commands.run.console") as mock_console:
            _handle_pending_response(
                job_id="test-job",
                message="Rate limit active (claude-cli clears in 5m 0s). "
                "Score queued — starts when limits clear.",
                json_output=False,
            )
            # Should have printed something with "pending" or "queued"
            assert mock_console.print.called
            all_output = " ".join(
                str(call[0][0]) for call in mock_console.print.call_args_list
            )
            assert "pending" in all_output.lower() or "queued" in all_output.lower()

    def test_pending_response_json_output(self) -> None:
        """When conductor returns 'pending' with --json, CLI outputs JSON."""
        from marianne.cli.commands.run import _handle_pending_response

        with patch("marianne.cli.commands.run.output_json") as mock_json:
            _handle_pending_response(
                job_id="test-job",
                message="Job queued as pending.",
                json_output=True,
            )
            mock_json.assert_called_once()
            call_data = mock_json.call_args[0][0]
            assert call_data["status"] == "pending"
            assert call_data["job_id"] == "test-job"


# =============================================================================
# JobResponse model supports "pending" status
# =============================================================================


class TestJobResponsePending:
    """Test that JobResponse model accepts 'pending' status."""

    def test_pending_status_valid(self) -> None:
        """JobResponse can be created with 'pending' status."""
        from marianne.daemon.types import JobResponse

        response = JobResponse(
            job_id="test-job",
            status="pending",
            message="Queued due to rate limits",
        )
        assert response.status == "pending"

    def test_accepted_status_still_valid(self) -> None:
        """Existing 'accepted' status still works."""
        from marianne.daemon.types import JobResponse

        response = JobResponse(
            job_id="test-job",
            status="accepted",
        )
        assert response.status == "accepted"


# =============================================================================
# Auto-start wiring: pending jobs start when rate limits clear
# =============================================================================


class TestPendingAutoStartWiring:
    """Test that _start_pending_jobs is called at the right times.

    Without explicit wiring, pending jobs would queue forever.
    The method must be called:
    1. After clear_rate_limits() — manual clearing via CLI
    2. After a deferred timer — when the rate limit is expected to expire
    """

    @pytest.mark.asyncio
    async def test_clear_rate_limits_triggers_pending_start(self) -> None:
        """clear_rate_limits() should call _start_pending_jobs() afterward."""
        from marianne.daemon.manager import JobManager

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)
            mgr._pending_jobs = {"queued-job": MagicMock()}
            mgr._jobs = {}
            mgr._baton_adapter = None
            mgr._rate_coordinator = AsyncMock()
            mgr._rate_coordinator.clear_limits = AsyncMock(return_value=1)

            # Mock _start_pending_jobs to track calls
            mgr._start_pending_jobs = AsyncMock()

            await mgr.clear_rate_limits(instrument=None)

            mgr._start_pending_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_rate_limits_for_specific_instrument(self) -> None:
        """Clearing a specific instrument still triggers pending check."""
        from marianne.daemon.manager import JobManager

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)
            mgr._pending_jobs = {}
            mgr._jobs = {}
            mgr._baton_adapter = None
            mgr._rate_coordinator = AsyncMock()
            mgr._rate_coordinator.clear_limits = AsyncMock(return_value=1)

            mgr._start_pending_jobs = AsyncMock()

            await mgr.clear_rate_limits(instrument="claude-cli")

            # Should still check, even if no pending jobs — cost is negligible
            mgr._start_pending_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_pending_schedules_deferred_start(
        self, tmp_path: Path,
    ) -> None:
        """Queuing a pending job should schedule a deferred auto-start check."""
        from marianne.daemon.manager import JobManager
        from marianne.daemon.types import JobRequest

        config_file = tmp_path / "test-score.yaml"
        config_file.write_text(
            "name: test\nworkspace: ../workspaces/test\ninstrument: claude-code\n"
            "sheet:\n  size: 1\nprompt:\n  template: 'hello'\n"
        )

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)
            mgr._pending_jobs = {}
            mgr._job_meta = {}
            mgr._jobs = {}
            mgr._id_gen_lock = asyncio.Lock()
            mgr._registry = AsyncMock()
            mgr._registry.get_job = AsyncMock(return_value=None)
            mgr._config = MagicMock()
            mgr._config.max_concurrent_jobs = 5
            mgr._config_name_to_conductor_id = {}

            # Rate limit with 10 seconds remaining
            mgr._rate_coordinator = MagicMock()
            mgr._rate_coordinator.active_limits = {"claude-cli": 10.0}

            request = JobRequest(
                config_path=config_file,
                fresh=False,
                self_healing=False,
                self_healing_auto_confirm=False,
            )

            with patch(
                "marianne.daemon.manager.asyncio.create_task",
            ) as mock_create_task:
                mock_task = MagicMock()
                mock_task.add_done_callback = MagicMock()
                mock_create_task.return_value = mock_task
                with patch.object(
                    mgr, "_resolve_workspace_from_config", return_value=None,
                ):
                    await mgr._queue_pending_job(request)

            # Should have scheduled a deferred start task
            assert any(
                "pending-autostart" in str(call)
                for call in mock_create_task.call_args_list
            ), f"Expected pending-autostart task, got: {mock_create_task.call_args_list}"


# =============================================================================
# Pending jobs visible in list/status
# =============================================================================


class TestPendingJobVisibility:
    """Pending jobs must be visible in mzt list and mzt status.

    A pending job that's invisible to the user is a UX bug — they were told
    their job was queued, but they can't see or manage it.
    """

    def test_pending_status_in_daemon_job_status_enum(self) -> None:
        """DaemonJobStatus should include PENDING."""
        from marianne.daemon.registry import DaemonJobStatus

        assert hasattr(DaemonJobStatus, "PENDING")
        assert DaemonJobStatus.PENDING.value == "pending"

    @pytest.mark.asyncio
    async def test_pending_job_appears_in_list(self, tmp_path: Path) -> None:
        """A pending job should appear in list_jobs() output."""
        from marianne.daemon.manager import JobManager
        from marianne.daemon.types import JobRequest

        config_file = tmp_path / "test-score.yaml"
        config_file.write_text(
            "name: test\nworkspace: ../workspaces/test\ninstrument: claude-code\n"
            "sheet:\n  size: 1\nprompt:\n  template: 'hello'\n"
        )

        with patch.object(JobManager, "__init__", lambda self, *a, **kw: None):
            mgr = JobManager.__new__(JobManager)
            mgr._pending_jobs = {}
            mgr._job_meta = {}
            mgr._jobs = {}
            mgr._id_gen_lock = asyncio.Lock()
            mgr._registry = AsyncMock()
            mgr._registry.get_job = AsyncMock(return_value=None)
            mgr._registry.list_jobs = AsyncMock(return_value=[])
            mgr._config = MagicMock()
            mgr._config.max_concurrent_jobs = 5
            mgr._config_name_to_conductor_id = {}
            mgr._rate_coordinator = MagicMock()
            mgr._rate_coordinator.active_limits = {"claude-cli": 60.0}
            mgr._live_states = {}
            mgr._baton_adapter = None

            request = JobRequest(
                config_path=config_file,
                fresh=False,
                self_healing=False,
                self_healing_auto_confirm=False,
            )

            with patch(
                "marianne.daemon.manager.asyncio.create_task",
                return_value=MagicMock(add_done_callback=MagicMock()),
            ):
                with patch.object(
                    mgr, "_resolve_workspace_from_config", return_value=tmp_path,
                ):
                    response = await mgr._queue_pending_job(request)

            assert response.status == "pending"

            # The job should now appear in list_jobs
            jobs = await mgr.list_jobs()
            assert len(jobs) >= 1
            pending_jobs = [j for j in jobs if j.get("status") == "pending"]
            assert len(pending_jobs) == 1, (
                f"Expected 1 pending job in list, got {len(pending_jobs)}. "
                f"All jobs: {jobs}"
            )

    @pytest.mark.asyncio
    async def test_pending_job_not_clearable(self) -> None:
        """Pending jobs should not be cleared by mzt clear."""
        from marianne.daemon.registry import DaemonJobStatus

        # "pending" is not in _TERMINAL_STATUSES
        from marianne.daemon.registry import _TERMINAL_STATUSES

        assert "pending" not in _TERMINAL_STATUSES
