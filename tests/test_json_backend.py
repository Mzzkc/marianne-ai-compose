"""Tests for JSON file-based state backend.

Verifies state persistence, atomic writes, corruption handling,
zombie detection, and listing/sorting behavior.
"""

import json
from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetStatus
from marianne.state.json_backend import JsonStateBackend, StateCorruptionError


# =============================================================================
# Helpers
# =============================================================================


def _make_state(
    job_id: str = "test-job",
    total_sheets: int = 3,
    status: JobStatus = JobStatus.PENDING,
) -> CheckpointState:
    """Create a minimal valid CheckpointState for testing."""
    return CheckpointState(
        job_id=job_id,
        job_name=f"Test Job {job_id}",
        total_sheets=total_sheets,
        status=status,
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for state files."""
    return tmp_path / "state"


@pytest.fixture
def backend(state_dir: Path) -> JsonStateBackend:
    """Create a JsonStateBackend in a temp directory."""
    return JsonStateBackend(state_dir)


# =============================================================================
# Initialization
# =============================================================================


class TestInit:
    """Tests for backend initialization."""

    def test_creates_state_dir(self, tmp_path: Path):
        """State directory is created if it doesn't exist."""
        d = tmp_path / "new" / "nested" / "state"
        assert not d.exists()
        JsonStateBackend(d)
        assert d.exists()

    def test_existing_dir_is_fine(self, tmp_path: Path):
        """Existing directory doesn't cause errors."""
        d = tmp_path / "state"
        d.mkdir()
        backend = JsonStateBackend(d)
        assert backend.state_dir == d


# =============================================================================
# Save and Load
# =============================================================================


@pytest.mark.asyncio
class TestSaveAndLoad:
    """Tests for save/load round-trip."""

    async def test_round_trip(self, backend: JsonStateBackend):
        """State survives save/load round-trip."""
        state = _make_state()
        await backend.save(state)

        loaded = await backend.load("test-job")
        assert loaded is not None
        assert loaded.job_id == "test-job"
        assert loaded.total_sheets == 3
        assert loaded.status == JobStatus.PENDING

    async def test_load_nonexistent_returns_none(self, backend: JsonStateBackend):
        """Loading a non-existent job returns None."""
        result = await backend.load("does-not-exist")
        assert result is None

    async def test_save_creates_file(self, backend: JsonStateBackend):
        """Save creates a JSON file on disk."""
        state = _make_state()
        await backend.save(state)

        state_file = backend._get_state_file("test-job")
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["job_id"] == "test-job"

    async def test_save_updates_updated_at(self, backend: JsonStateBackend):
        """Save updates the updated_at timestamp."""
        state = _make_state()
        original_updated = state.updated_at
        await backend.save(state)

        loaded = await backend.load("test-job")
        assert loaded is not None
        # updated_at should be refreshed (or at least not earlier)
        assert loaded.updated_at >= original_updated

    async def test_save_is_atomic(self, backend: JsonStateBackend):
        """No .tmp file remains after save."""
        state = _make_state()
        await backend.save(state)

        state_file = backend._get_state_file("test-job")
        tmp_file = state_file.with_suffix(".json.tmp")
        assert not tmp_file.exists()
        assert state_file.exists()

    async def test_overwrite_existing_state(self, backend: JsonStateBackend):
        """Saving again overwrites previous state."""
        state = _make_state(status=JobStatus.PENDING)
        await backend.save(state)

        state.status = JobStatus.COMPLETED
        await backend.save(state)

        loaded = await backend.load("test-job")
        assert loaded is not None
        assert loaded.status == JobStatus.COMPLETED


# =============================================================================
# Corruption Handling
# =============================================================================


@pytest.mark.asyncio
class TestCorruptionHandling:
    """Tests for corrupt state file detection."""

    async def test_json_decode_error(self, backend: JsonStateBackend):
        """Invalid JSON raises StateCorruptionError with json_decode type."""
        state_file = backend._get_state_file("corrupt-job")
        state_file.write_text("this is not json{{{")

        with pytest.raises(StateCorruptionError) as exc_info:
            await backend.load("corrupt-job")

        assert exc_info.value.error_type == "json_decode"
        assert exc_info.value.job_id == "corrupt-job"

    async def test_validation_error(self, backend: JsonStateBackend):
        """Valid JSON but invalid schema raises StateCorruptionError."""
        state_file = backend._get_state_file("bad-schema")
        # Missing required fields
        state_file.write_text(json.dumps({"job_id": "bad-schema"}))

        with pytest.raises(StateCorruptionError) as exc_info:
            await backend.load("bad-schema")

        assert exc_info.value.error_type == "validation"

    async def test_empty_file_raises_corruption(self, backend: JsonStateBackend):
        """Empty file raises StateCorruptionError."""
        state_file = backend._get_state_file("empty-job")
        state_file.write_text("")

        with pytest.raises(StateCorruptionError):
            await backend.load("empty-job")


# =============================================================================
# Job ID Sanitization
# =============================================================================


class TestSanitization:
    """Tests for job ID sanitization in file paths."""

    def test_normal_id(self, backend: JsonStateBackend):
        """Normal alphanumeric IDs are unchanged."""
        p = backend._get_state_file("my-job-123")
        assert p.name == "my-job-123.json"

    def test_special_chars_replaced(self, backend: JsonStateBackend):
        """Special characters are replaced with underscores."""
        p = backend._get_state_file("my/job:name")
        assert "/" not in p.name
        assert ":" not in p.name
        assert p.name == "my_job_name.json"

    def test_dots_replaced(self, backend: JsonStateBackend):
        """Dots are replaced to prevent path traversal."""
        p = backend._get_state_file("../../etc/passwd")
        assert ".." not in p.name


# =============================================================================
# Delete
# =============================================================================


@pytest.mark.asyncio
class TestDelete:
    """Tests for state deletion."""

    async def test_delete_existing(self, backend: JsonStateBackend):
        """Deleting existing state returns True."""
        await backend.save(_make_state())
        result = await backend.delete("test-job")
        assert result is True

        # Verify file is gone
        loaded = await backend.load("test-job")
        assert loaded is None

    async def test_delete_nonexistent(self, backend: JsonStateBackend):
        """Deleting non-existent state returns False."""
        result = await backend.delete("no-such-job")
        assert result is False


# =============================================================================
# List Jobs
# =============================================================================


@pytest.mark.asyncio
class TestListJobs:
    """Tests for listing all jobs."""

    async def test_empty_directory(self, backend: JsonStateBackend):
        """Empty state directory returns empty list."""
        jobs = await backend.list_jobs()
        assert jobs == []

    async def test_lists_multiple_jobs(self, backend: JsonStateBackend):
        """Multiple saved jobs are all returned."""
        for name in ["alpha", "bravo", "charlie"]:
            await backend.save(_make_state(job_id=name))

        jobs = await backend.list_jobs()
        assert len(jobs) == 3
        job_ids = {j.job_id for j in jobs}
        assert job_ids == {"alpha", "bravo", "charlie"}

    async def test_sorted_by_updated_at_descending(
        self, backend: JsonStateBackend
    ):
        """Jobs are sorted by updated_at (most recent first)."""
        import time

        for name in ["old", "mid", "new"]:
            await backend.save(_make_state(job_id=name))
            time.sleep(0.01)  # Ensure distinct timestamps

        jobs = await backend.list_jobs()
        assert jobs[0].job_id == "new"
        assert jobs[-1].job_id == "old"

    async def test_skips_corrupt_files(self, backend: JsonStateBackend):
        """Corrupt JSON files are silently skipped."""
        await backend.save(_make_state(job_id="good"))

        # Write a corrupt file
        corrupt = backend.state_dir / "corrupt.json"
        corrupt.write_text("not json!!!")

        jobs = await backend.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "good"

    async def test_skips_tmp_files(self, backend: JsonStateBackend):
        """Temp files from interrupted saves are ignored."""
        await backend.save(_make_state(job_id="real"))

        # Simulate an interrupted atomic write
        tmp = backend.state_dir / "interrupted.json.tmp"
        tmp.write_text(json.dumps({"partial": True}))

        jobs = await backend.list_jobs()
        assert len(jobs) == 1

    async def test_skips_invalid_schema_files(self, backend: JsonStateBackend):
        """Valid JSON but invalid schema files are skipped."""
        await backend.save(_make_state(job_id="valid"))

        bad = backend.state_dir / "bad-schema.json"
        bad.write_text(json.dumps({"foo": "bar"}))

        jobs = await backend.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "valid"


# =============================================================================
# Get Next Sheet
# =============================================================================


@pytest.mark.asyncio
class TestGetNextSheet:
    """Tests for get_next_sheet."""

    async def test_new_job_starts_at_1(self, backend: JsonStateBackend):
        """No existing state returns sheet 1."""
        result = await backend.get_next_sheet("nonexistent")
        assert result == 1

    async def test_existing_state_returns_next(self, backend: JsonStateBackend):
        """Existing state returns the correct next sheet."""
        state = _make_state(total_sheets=5)
        state.last_completed_sheet = 3
        await backend.save(state)

        result = await backend.get_next_sheet("test-job")
        assert result == 4


# =============================================================================
# Mark Sheet Status
# =============================================================================


@pytest.mark.asyncio
class TestMarkSheetStatus:
    """Tests for mark_sheet_status."""

    async def test_mark_completed(self, backend: JsonStateBackend):
        """Marking sheet as completed updates state."""
        state = _make_state()
        await backend.save(state)

        await backend.mark_sheet_status("test-job", 1, SheetStatus.COMPLETED)

        loaded = await backend.load("test-job")
        assert loaded is not None
        assert loaded.last_completed_sheet == 1

    async def test_mark_failed(self, backend: JsonStateBackend):
        """Marking sheet as failed records error message."""
        state = _make_state()
        await backend.save(state)

        await backend.mark_sheet_status(
            "test-job", 2, SheetStatus.FAILED, "Out of tokens"
        )

        loaded = await backend.load("test-job")
        assert loaded is not None
        assert loaded.sheets[2].status == SheetStatus.FAILED
        assert loaded.sheets[2].error_message == "Out of tokens"

    async def test_mark_in_progress(self, backend: JsonStateBackend):
        """Marking sheet as in-progress updates state."""
        state = _make_state()
        await backend.save(state)

        await backend.mark_sheet_status(
            "test-job", 1, SheetStatus.IN_PROGRESS
        )

        loaded = await backend.load("test-job")
        assert loaded is not None
        assert loaded.sheets[1].status == SheetStatus.IN_PROGRESS

    async def test_mark_nonexistent_job_raises(self, backend: JsonStateBackend):
        """Marking sheet on non-existent job raises ValueError."""
        with pytest.raises(ValueError, match="No state found"):
            await backend.mark_sheet_status(
                "no-such-job", 1, SheetStatus.COMPLETED
            )
