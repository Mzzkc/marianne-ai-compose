"""Tests for mozart.state.memory.InMemoryStateBackend.

Covers all StateBackend interface methods including edge cases
for mark_sheet_status with different status types.
"""

from __future__ import annotations

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.state.memory import InMemoryStateBackend


def _make_state(job_id: str = "test-job", total_sheets: int = 3) -> CheckpointState:
    """Create a CheckpointState with pending sheets."""
    return CheckpointState(
        job_id=job_id,
        job_name=f"{job_id}-name",
        total_sheets=total_sheets,
        sheets={
            i: SheetState(sheet_num=i, status=SheetStatus.PENDING)
            for i in range(1, total_sheets + 1)
        },
    )


class TestInMemoryStateBackendLoad:
    """Tests for the load() method."""

    @pytest.mark.asyncio
    async def test_load_returns_none_for_missing_job(self) -> None:
        backend = InMemoryStateBackend()
        result = await backend.load("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_returns_saved_state(self) -> None:
        backend = InMemoryStateBackend()
        state = _make_state("job-1")
        await backend.save(state)
        loaded = await backend.load("job-1")
        assert loaded is not None
        assert loaded.job_id == "job-1"


class TestInMemoryStateBackendSave:
    """Tests for the save() method."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self) -> None:
        backend = InMemoryStateBackend()
        state = _make_state("my-job")
        await backend.save(state)
        assert "my-job" in backend.states

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self) -> None:
        backend = InMemoryStateBackend()
        state1 = _make_state("my-job", total_sheets=3)
        state2 = _make_state("my-job", total_sheets=5)
        await backend.save(state1)
        await backend.save(state2)
        loaded = await backend.load("my-job")
        assert loaded is not None
        assert loaded.total_sheets == 5


class TestInMemoryStateBackendDelete:
    """Tests for the delete() method."""

    @pytest.mark.asyncio
    async def test_delete_existing_returns_true(self) -> None:
        backend = InMemoryStateBackend()
        await backend.save(_make_state("to-delete"))
        result = await backend.delete("to-delete")
        assert result is True
        assert await backend.load("to-delete") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self) -> None:
        backend = InMemoryStateBackend()
        result = await backend.delete("no-such-job")
        assert result is False


class TestInMemoryStateBackendListJobs:
    """Tests for the list_jobs() method."""

    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        backend = InMemoryStateBackend()
        result = await backend.list_jobs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_multiple_jobs(self) -> None:
        backend = InMemoryStateBackend()
        await backend.save(_make_state("job-a"))
        await backend.save(_make_state("job-b"))
        jobs = await backend.list_jobs()
        assert len(jobs) == 2
        job_ids = {j.job_id for j in jobs}
        assert job_ids == {"job-a", "job-b"}


class TestInMemoryStateBackendGetNextSheet:
    """Tests for the get_next_sheet() method."""

    @pytest.mark.asyncio
    async def test_get_next_sheet_for_missing_job(self) -> None:
        backend = InMemoryStateBackend()
        result = await backend.get_next_sheet("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_next_sheet_returns_first_pending(self) -> None:
        backend = InMemoryStateBackend()
        state = _make_state("job-1", total_sheets=3)
        await backend.save(state)
        next_sheet = await backend.get_next_sheet("job-1")
        # Should return the first pending sheet
        assert next_sheet is not None
        assert next_sheet >= 1


class TestInMemoryStateBackendMarkSheetStatus:
    """Tests for the mark_sheet_status() method."""

    @pytest.mark.asyncio
    async def test_mark_completed(self) -> None:
        backend = InMemoryStateBackend()
        state = _make_state("job-1")
        await backend.save(state)
        await backend.mark_sheet_status("job-1", 1, SheetStatus.COMPLETED)
        loaded = await backend.load("job-1")
        assert loaded is not None
        assert loaded.sheets[1].status == SheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_mark_failed_with_message(self) -> None:
        backend = InMemoryStateBackend()
        state = _make_state("job-1")
        await backend.save(state)
        await backend.mark_sheet_status(
            "job-1", 2, SheetStatus.FAILED, error_message="timeout"
        )
        loaded = await backend.load("job-1")
        assert loaded is not None
        assert loaded.sheets[2].status == SheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_mark_in_progress(self) -> None:
        backend = InMemoryStateBackend()
        state = _make_state("job-1")
        await backend.save(state)
        await backend.mark_sheet_status("job-1", 1, SheetStatus.IN_PROGRESS)
        loaded = await backend.load("job-1")
        assert loaded is not None
        assert loaded.sheets[1].status == SheetStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_mark_status_on_missing_job_raises(self) -> None:
        backend = InMemoryStateBackend()
        with pytest.raises(ValueError, match="No state found"):
            await backend.mark_sheet_status("missing", 1, SheetStatus.COMPLETED)
