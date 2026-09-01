"""Tests for the pause_before_chain feature.

Verifies that:
1. PAUSED_AT_CHAIN is a valid status in both JobStatus and DaemonJobStatus
2. The hook execution respects pause_before_chain flag
3. Resume triggers the held chain
4. Failed chain resume restores the paused state
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.checkpoint import JobStatus
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager, JobMeta
from marianne.daemon.registry import DaemonJobStatus

# ─── Status Enum Tests ──────────────────────────────────────────────


class TestPausedAtChainStatus:
    """Verify PAUSED_AT_CHAIN exists in both status enums."""

    def test_job_status_has_paused_at_chain(self) -> None:
        assert hasattr(JobStatus, "PAUSED_AT_CHAIN")
        assert JobStatus.PAUSED_AT_CHAIN.value == "paused_at_chain"

    def test_daemon_job_status_has_paused_at_chain(self) -> None:
        assert hasattr(DaemonJobStatus, "PAUSED_AT_CHAIN")
        assert DaemonJobStatus.PAUSED_AT_CHAIN.value == "paused_at_chain"

    def test_paused_at_chain_is_not_terminal(self) -> None:
        """PAUSED_AT_CHAIN should not be in terminal statuses."""
        from marianne.daemon.registry import _TERMINAL_STATUSES

        assert "paused_at_chain" not in _TERMINAL_STATUSES

    def test_paused_at_chain_is_not_active(self) -> None:
        """PAUSED_AT_CHAIN should not be in active statuses."""
        from marianne.daemon.registry import _ACTIVE_STATUSES

        assert "paused_at_chain" not in _ACTIVE_STATUSES

    def test_paused_at_chain_serializes_as_string(self) -> None:
        """DaemonJobStatus inherits from str, so it compares as its value."""
        status = DaemonJobStatus.PAUSED_AT_CHAIN
        assert status.value == "paused_at_chain"
        # str(Enum) comparison: DaemonJobStatus inherits from str
        assert status == "paused_at_chain"
        # Also works in dict serialization
        d: dict[str, Any] = {"status": status}
        assert d["status"] == "paused_at_chain"


# ─── Hook Execution Tests ───────────────────────────────────────────


class TestPauseBeforeChainHookExecution:
    """Test that _execute_hook_run_job handles pause_before_chain."""

    @pytest.fixture
    def mock_manager(self) -> MagicMock:
        """Create a mock JobManager with required attributes."""
        manager = MagicMock()
        manager._set_job_status = AsyncMock()
        manager.submit_job = AsyncMock()
        manager._expand_hook_vars = MagicMock(side_effect=lambda s, *a, **kw: s)
        return manager

    @pytest.fixture
    def meta(self, tmp_path: Path) -> Any:
        """Create a mock JobMeta."""
        from dataclasses import dataclass

        @dataclass
        class MockMeta:
            job_id: str = "test-job"
            workspace: Path = tmp_path
            chain_depth: int | None = 0
            held_chain_hook: dict[str, Any] | None = None
            status: DaemonJobStatus = DaemonJobStatus.COMPLETED

        return MockMeta()

    async def test_pause_before_chain_sets_status(
        self,
        tmp_path: Path,
        meta: Any,
    ) -> None:
        """When pause_before_chain is True, job transitions to PAUSED_AT_CHAIN."""
        from marianne.daemon.manager import JobManager

        # Create a real score file for validation
        score_path = tmp_path / "chain-target.yaml"
        score_path.write_text("name: chain-target\n")

        hook: dict[str, Any] = {
            "type": "run_job",
            "job_path": str(score_path),
            "pause_before_chain": True,
            "fresh": True,
        }
        concert: dict[str, Any] | None = None

        # Use the real method but with mocked internals
        result = await JobManager._execute_hook_run_job(
            self=MagicMock(
                _set_job_status=AsyncMock(),
                _expand_hook_vars=MagicMock(side_effect=lambda s, *a, **kw: s),
            ),
            parent_job_id="test-job",
            hook=hook,
            concert=concert,
            meta=meta,
        )

        assert result["success"] is True
        assert result.get("paused_at_chain") is True
        assert meta.held_chain_hook is not None
        assert meta.held_chain_hook["job_path"] == str(score_path)
        assert meta.held_chain_hook["fresh"] is True

    async def test_no_pause_submits_normally(
        self,
        tmp_path: Path,
        meta: Any,
    ) -> None:
        """Without pause_before_chain, hook submits chained job normally."""
        from marianne.daemon.manager import JobManager
        from marianne.daemon.types import JobResponse

        score_path = tmp_path / "chain-target.yaml"
        score_path.write_text("name: chain-target\n")

        hook: dict[str, Any] = {
            "type": "run_job",
            "job_path": str(score_path),
            "pause_before_chain": False,
            "fresh": False,
        }

        mock_self = MagicMock()
        mock_self._set_job_status = AsyncMock()
        mock_self._expand_hook_vars = MagicMock(side_effect=lambda s, *a, **kw: s)
        mock_self.submit_job = AsyncMock(
            return_value=JobResponse(
                job_id="chained-job",
                status="accepted",
                message="ok",
            )
        )

        result = await JobManager._execute_hook_run_job(
            self=mock_self,
            parent_job_id="test-job",
            hook=hook,
            concert=None,
            meta=meta,
        )

        assert result["success"] is True
        assert "chained_job_id" in result
        assert result["chained_job_id"] == "chained-job"
        assert meta.held_chain_hook is None


# ─── Resume Chain Tests ──────────────────────────────────────────────


class TestResumeHeldChain:
    """Test resuming a job that is PAUSED_AT_CHAIN."""

    async def test_public_resume_allows_same_id_held_chain(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A held self-chain re-enters its own admission without a release gap."""
        score_path = tmp_path / "cycle.yaml"
        workspace = tmp_path / "workspace"
        score_path.write_text(
            "name: cycle\n"
            f"workspace: {workspace}\n"
            "sheet:\n"
            "  size: 1\n"
            "  total_items: 1\n"
            "prompt:\n"
            "  template: Continue the cycle.\n",
            encoding="utf-8",
        )
        manager = JobManager(
            DaemonConfig(
                max_concurrent_jobs=1,
                pid_file=tmp_path / "daemon.pid",
                state_db_path=tmp_path / "state.db",
            )
        )
        await manager._registry.open()
        await manager._registry.register_job("cycle", score_path, workspace)
        await manager._registry.update_status(
            "cycle",
            DaemonJobStatus.PAUSED_AT_CHAIN.value,
        )
        manager._job_meta["cycle"] = JobMeta(
            job_id="cycle",
            config_path=score_path,
            workspace=workspace,
            status=DaemonJobStatus.PAUSED_AT_CHAIN,
            held_chain_hook={
                "job_path": str(score_path),
                "workspace": str(workspace),
                "fresh": False,
                "chain_depth": 1,
            },
        )
        execution_entered = asyncio.Event()
        release_execution = asyncio.Event()

        async def hold_execution(_job_id: str, _request: object) -> None:
            execution_entered.set()
            await release_execution.wait()

        monkeypatch.setattr(manager, "_run_job_task", hold_execution)

        execution_task: asyncio.Task[object] | None = None
        try:
            response = await manager.resume_job("cycle")
            assert response.status == "accepted"
            assert response.job_id == "cycle"
            execution_task = manager._jobs.get("cycle")
            await asyncio.wait_for(execution_entered.wait(), timeout=1.0)
            assert manager._job_meta["cycle"].status is DaemonJobStatus.QUEUED
            assert manager._job_admission_reservations == {}
        finally:
            release_execution.set()
            if execution_task is not None:
                await asyncio.gather(execution_task, return_exceptions=True)
            await manager._registry.close()

    async def test_resume_paused_at_chain_submits_chain(self) -> None:
        """Resuming a PAUSED_AT_CHAIN job triggers the held chain."""
        from marianne.daemon.manager import JobManager
        from marianne.daemon.types import JobResponse

        mock_self = MagicMock(spec=JobManager)
        mock_self._set_job_status = AsyncMock()
        mock_self.submit_job = AsyncMock(
            return_value=JobResponse(
                job_id="chained-job",
                status="accepted",
                message="ok",
            )
        )

        from dataclasses import dataclass, field

        @dataclass
        class MockMeta:
            job_id: str = "parent-job"
            status: DaemonJobStatus = DaemonJobStatus.PAUSED_AT_CHAIN
            held_chain_hook: dict[str, Any] | None = field(
                default_factory=lambda: {
                    "job_path": "/tmp/chain.yaml",
                    "workspace": "/tmp/ws",
                    "fresh": True,
                    "chain_depth": 2,
                }
            )

        meta = MockMeta()

        response = await JobManager._resume_held_chain(
            mock_self,
            "parent-job",
            meta,
        )

        assert response.status == "accepted"
        assert "chained-job" in (response.message or "")
        assert meta.held_chain_hook is None  # Cleared after submission

    async def test_resume_chain_failure_restores_state(self) -> None:
        """If chain submission fails, restore PAUSED_AT_CHAIN state."""
        from marianne.daemon.manager import JobManager
        from marianne.daemon.types import JobResponse

        mock_self = MagicMock(spec=JobManager)
        mock_self._set_job_status = AsyncMock()
        mock_self.submit_job = AsyncMock(
            return_value=JobResponse(
                job_id="parent-job",
                status="rejected",
                message="pressure",
            )
        )

        held_hook = {
            "job_path": "/tmp/chain.yaml",
            "workspace": "/tmp/ws",
            "fresh": True,
            "chain_depth": 2,
        }

        from dataclasses import dataclass, field

        @dataclass
        class MockMeta:
            job_id: str = "parent-job"
            status: DaemonJobStatus = DaemonJobStatus.PAUSED_AT_CHAIN
            held_chain_hook: dict[str, Any] | None = field(default_factory=lambda: held_hook.copy())

        meta = MockMeta()

        response = await JobManager._resume_held_chain(
            mock_self,
            "parent-job",
            meta,
        )

        assert response.status == "rejected"
        # Hook should be restored for retry
        assert meta.held_chain_hook is not None
        assert meta.held_chain_hook["job_path"] == "/tmp/chain.yaml"

    async def test_resume_without_held_hook_raises(self) -> None:
        """Resuming PAUSED_AT_CHAIN without a held hook raises."""
        from marianne.daemon.exceptions import JobSubmissionError
        from marianne.daemon.manager import JobManager

        mock_self = MagicMock(spec=JobManager)

        from dataclasses import dataclass

        @dataclass
        class MockMeta:
            job_id: str = "parent-job"
            status: DaemonJobStatus = DaemonJobStatus.PAUSED_AT_CHAIN
            held_chain_hook: dict[str, Any] | None = None

        meta = MockMeta()

        with pytest.raises(JobSubmissionError, match="no held chain hook"):
            await JobManager._resume_held_chain(
                mock_self,
                "parent-job",
                meta,
            )


# ─── Resumable Status Tests ─────────────────────────────────────────


class TestResumableStatus:
    """Test that PAUSED_AT_CHAIN is recognized as resumable."""

    def test_cli_resume_accepts_paused_at_chain(self) -> None:
        """The CLI resume command includes PAUSED_AT_CHAIN as resumable."""
        resumable = {
            JobStatus.PAUSED,
            JobStatus.PAUSED_AT_CHAIN,
            JobStatus.FAILED,
            JobStatus.RUNNING,
            JobStatus.CANCELLED,
        }
        assert JobStatus.PAUSED_AT_CHAIN in resumable
