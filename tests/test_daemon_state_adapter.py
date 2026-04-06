"""Tests for DaemonStateAdapter — StateBackend over DaemonClient."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.daemon.exceptions import DaemonError
from marianne.daemon.ipc.client import DaemonClient
from marianne.dashboard.state.daemon_adapter import DaemonStateAdapter


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock DaemonClient."""
    return AsyncMock(spec=DaemonClient)


@pytest.fixture
def adapter(mock_client: AsyncMock) -> DaemonStateAdapter:
    return DaemonStateAdapter(client=mock_client)


def _make_checkpoint_data(
    job_id: str = "test-job",
    status: str = "running",
    total_sheets: int = 3,
) -> dict:
    """Build a dict that can be unpacked into CheckpointState."""
    return {
        "job_id": job_id,
        "job_name": job_id,
        "total_sheets": total_sheets,
        "status": status,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    }


# ------------------------------------------------------------------
# load()
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_returns_checkpoint_for_known_job(
    adapter: DaemonStateAdapter, mock_client: AsyncMock
) -> None:
    mock_client.get_job_status.return_value = _make_checkpoint_data("my-job")
    result = await adapter.load("my-job")
    assert result is not None
    assert isinstance(result, CheckpointState)
    assert result.job_id == "my-job"
    mock_client.get_job_status.assert_awaited_once_with("my-job", "")


@pytest.mark.asyncio
async def test_load_returns_none_for_unknown_job(
    adapter: DaemonStateAdapter, mock_client: AsyncMock
) -> None:
    mock_client.get_job_status.side_effect = DaemonError("Job not found")
    result = await adapter.load("nonexistent")
    assert result is None


# ------------------------------------------------------------------
# list_jobs()
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_returns_checkpoint_list(
    adapter: DaemonStateAdapter, mock_client: AsyncMock
) -> None:
    mock_client.list_jobs.return_value = [
        {"job_id": "job-a", "status": "running", "submitted_at": None},
        {"job_id": "job-b", "status": "completed", "submitted_at": None},
    ]
    mock_client.get_job_status.side_effect = [
        _make_checkpoint_data("job-a", "running"),
        _make_checkpoint_data("job-b", "completed"),
    ]

    results = await adapter.list_jobs()
    assert len(results) == 2
    assert all(isinstance(r, CheckpointState) for r in results)
    assert results[0].job_id == "job-a"
    assert results[1].status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_list_jobs_handles_individual_failure(
    adapter: DaemonStateAdapter, mock_client: AsyncMock
) -> None:
    """If get_job_status fails for one job, a minimal fallback is used."""
    mock_client.list_jobs.return_value = [
        {"job_id": "good-job", "status": "running", "submitted_at": None},
        {"job_id": "bad-job", "status": "failed", "submitted_at": None},
    ]
    mock_client.get_job_status.side_effect = [
        _make_checkpoint_data("good-job", "running"),
        DaemonError("timeout"),
    ]

    results = await adapter.list_jobs()
    assert len(results) == 2
    assert results[0].job_id == "good-job"
    # Fallback entry
    assert results[1].job_id == "bad-job"
    assert results[1].status == JobStatus.FAILED
    assert results[1].total_sheets == 1  # minimal fallback


# ------------------------------------------------------------------
# Write methods — must raise NotImplementedError
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_raises(adapter: DaemonStateAdapter) -> None:
    state = CheckpointState(
        job_id="x", job_name="x", total_sheets=1
    )
    with pytest.raises(NotImplementedError, match="read-only"):
        await adapter.save(state)


@pytest.mark.asyncio
async def test_delete_raises(adapter: DaemonStateAdapter) -> None:
    with pytest.raises(NotImplementedError, match="read-only"):
        await adapter.delete("x")


@pytest.mark.asyncio
async def test_get_next_sheet_raises(adapter: DaemonStateAdapter) -> None:
    with pytest.raises(NotImplementedError, match="read-only"):
        await adapter.get_next_sheet("x")


@pytest.mark.asyncio
async def test_mark_sheet_status_raises(adapter: DaemonStateAdapter) -> None:
    from marianne.core.checkpoint import SheetStatus

    with pytest.raises(NotImplementedError, match="read-only"):
        await adapter.mark_sheet_status("x", 1, SheetStatus.COMPLETED)


# ------------------------------------------------------------------
# close()
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_is_noop(adapter: DaemonStateAdapter) -> None:
    await adapter.close()  # Should not raise
