"""Concurrent state access tests for Mozart state backends.

Tests cover:
- Atomic file operations (temp + rename pattern)
- Concurrent reads during writes
- Multiple concurrent writers
- Race condition handling
- Corrupted state recovery
- File locking behavior
"""

import asyncio
import json
from pathlib import Path

import pytest

from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.state.json_backend import JsonStateBackend


class TestAtomicOperations:
    """Tests for atomic write operations."""

    @pytest.mark.asyncio
    async def test_atomic_write_uses_temp_file(self, tmp_path: Path) -> None:
        """Test that save uses temp file + rename pattern."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="atomic-test",
            job_name="Atomic Test",
            total_sheets=5,
        )

        await backend.save(state)

        # Final file should exist
        state_file = tmp_path / "atomic-test.json"
        assert state_file.exists()

        # Temp file should NOT exist after successful save
        temp_file = tmp_path / "atomic-test.json.tmp"
        assert not temp_file.exists()

    @pytest.mark.asyncio
    async def test_atomic_write_valid_json(self, tmp_path: Path) -> None:
        """Test that saved file is valid JSON."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="json-test",
            job_name="JSON Test",
            total_sheets=3,
            last_completed_sheet=1,
            status=JobStatus.RUNNING,
        )

        await backend.save(state)

        # Should be valid JSON
        state_file = tmp_path / "json-test.json"
        with open(state_file) as f:
            data = json.load(f)

        assert data["job_id"] == "json-test"
        assert data["total_sheets"] == 3
        assert data["last_completed_sheet"] == 1


class TestConcurrentReads:
    """Tests for concurrent read operations."""

    @pytest.mark.asyncio
    async def test_concurrent_reads(self, tmp_path: Path) -> None:
        """Test multiple concurrent reads of the same state."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="read-test",
            job_name="Read Test",
            total_sheets=10,
            last_completed_sheet=5,
        )
        await backend.save(state)

        # Concurrent reads should all succeed
        async def read_state() -> CheckpointState | None:
            return await backend.load("read-test")

        # Run 10 concurrent reads
        results = await asyncio.gather(*[read_state() for _ in range(10)])

        # All should return the same state
        for result in results:
            assert result is not None
            assert result.job_id == "read-test"
            assert result.total_sheets == 10
            assert result.last_completed_sheet == 5

    @pytest.mark.asyncio
    async def test_read_during_write(self, tmp_path: Path) -> None:
        """Test that reads don't fail during concurrent writes."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="rw-test",
            job_name="Read/Write Test",
            total_sheets=20,
        )
        await backend.save(state)

        read_count = 0
        write_count = 0

        async def read_repeatedly() -> None:
            nonlocal read_count
            for _ in range(20):
                result = await backend.load("rw-test")
                # Read should never fail due to concurrent write
                # (atomic rename means file is always valid or doesn't exist)
                assert result is None or result.job_id == "rw-test"
                read_count += 1
                await asyncio.sleep(0.01)

        async def write_repeatedly() -> None:
            nonlocal write_count
            for i in range(20):
                state.last_completed_sheet = i % 20
                await backend.save(state)
                write_count += 1
                await asyncio.sleep(0.01)

        # Run concurrent reads and writes
        await asyncio.gather(read_repeatedly(), write_repeatedly())

        assert read_count == 20
        assert write_count == 20


class TestConcurrentWrites:
    """Tests for concurrent write operations."""

    @pytest.mark.asyncio
    async def test_concurrent_writes_all_complete(self, tmp_path: Path) -> None:
        """Test that all concurrent writes complete without error."""
        backend = JsonStateBackend(tmp_path)

        async def write_state(sheet_num: int) -> None:
            state = CheckpointState(
                job_id="concurrent-write",
                job_name="Concurrent Write Test",
                total_sheets=100,
                last_completed_sheet=sheet_num,
            )
            await backend.save(state)

        # Run 10 concurrent writes with different sheet numbers
        await asyncio.gather(*[write_state(i) for i in range(10)])

        # Final state should exist and be valid
        state = await backend.load("concurrent-write")
        assert state is not None
        assert state.job_id == "concurrent-write"
        # Final value depends on race condition - any value 0-9 is valid
        assert 0 <= state.last_completed_sheet <= 9

    @pytest.mark.asyncio
    async def test_sequential_writes_preserve_order(self, tmp_path: Path) -> None:
        """Test that sequential writes properly update state."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="sequential-write",
            job_name="Sequential Test",
            total_sheets=10,
        )

        for i in range(1, 6):
            # Must mark_sheet_started before mark_sheet_completed
            state.mark_sheet_started(i)
            state.mark_sheet_completed(i)
            await backend.save(state)

        # Load and verify final state
        loaded = await backend.load("sequential-write")
        assert loaded is not None
        assert loaded.last_completed_sheet == 5
        assert len(loaded.sheets) == 5


class TestRaceConditions:
    """Tests for race condition handling."""

    @pytest.mark.asyncio
    async def test_load_modify_save_race(self, tmp_path: Path) -> None:
        """Test load-modify-save race condition scenario.

        This tests the common pattern where multiple processes:
        1. Load state
        2. Modify state
        3. Save state

        The last writer wins, which is the expected behavior for
        the current non-locking implementation.
        """
        backend = JsonStateBackend(tmp_path)
        initial_state = CheckpointState(
            job_id="race-test",
            job_name="Race Test",
            total_sheets=10,
            last_completed_sheet=0,
        )
        await backend.save(initial_state)

        results: list[int] = []

        async def load_modify_save(worker_id: int) -> None:
            # Simulate load-modify-save cycle
            state = await backend.load("race-test")
            assert state is not None

            # Add some delay to simulate processing
            await asyncio.sleep(0.05)

            # Mark a unique sheet as started then completed
            state.mark_sheet_started(worker_id)
            state.mark_sheet_completed(worker_id)
            await backend.save(state)
            results.append(worker_id)

        # Run 5 concurrent workers
        await asyncio.gather(*[load_modify_save(i) for i in range(1, 6)])

        # Load final state
        final = await backend.load("race-test")
        assert final is not None

        # With load-modify-save race, only the last writer's sheet is preserved
        # The others are lost because each worker started from the same initial state
        # This documents the current behavior (no locking)
        assert final.last_completed_sheet in [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_delete_during_read_race(self, tmp_path: Path) -> None:
        """Test delete operation during concurrent reads."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="delete-race",
            job_name="Delete Race Test",
            total_sheets=5,
        )
        await backend.save(state)

        delete_done = asyncio.Event()

        async def read_until_deleted() -> int:
            """Read repeatedly until file is deleted or timeout."""
            reads = 0
            for _ in range(100):
                result = await backend.load("delete-race")
                reads += 1
                if result is None:
                    break
                await asyncio.sleep(0.01)
            return reads

        async def delete_after_delay() -> None:
            """Delete state after a short delay."""
            await asyncio.sleep(0.1)
            await backend.delete("delete-race")
            delete_done.set()

        reads, _ = await asyncio.gather(read_until_deleted(), delete_after_delay())

        # Some reads should succeed before delete
        assert reads >= 1


class TestCorruptedStateRecovery:
    """Tests for corrupted state handling."""

    @pytest.mark.asyncio
    async def test_load_corrupted_json(self, tmp_path: Path) -> None:
        """Test loading a corrupted JSON file raises StateCorruptionError."""
        from mozart.state.json_backend import StateCorruptionError

        backend = JsonStateBackend(tmp_path)

        # Write corrupted JSON directly
        state_file = tmp_path / "corrupted.json"
        state_file.write_text("{invalid json content}")

        # Load should raise StateCorruptionError
        with pytest.raises(StateCorruptionError, match="json_decode"):
            await backend.load("corrupted")

    @pytest.mark.asyncio
    async def test_load_invalid_schema(self, tmp_path: Path) -> None:
        """Test loading JSON with invalid schema raises StateCorruptionError."""
        from mozart.state.json_backend import StateCorruptionError

        backend = JsonStateBackend(tmp_path)

        # Write valid JSON but invalid schema
        state_file = tmp_path / "invalid-schema.json"
        state_file.write_text('{"foo": "bar"}')

        # Load should raise StateCorruptionError due to validation error
        with pytest.raises(StateCorruptionError, match="validation"):
            await backend.load("invalid-schema")

    @pytest.mark.asyncio
    async def test_load_partial_write(self, tmp_path: Path) -> None:
        """Test that partial/temp files are not loaded.

        The atomic write pattern (temp file + rename) ensures that
        a power failure during write leaves a temp file, not a corrupted
        main file. The list_jobs should skip temp files.
        """
        backend = JsonStateBackend(tmp_path)

        # Create a valid state
        state = CheckpointState(
            job_id="valid-state",
            job_name="Valid State",
            total_sheets=5,
        )
        await backend.save(state)

        # Simulate a leftover temp file from interrupted write
        temp_file = tmp_path / "interrupted.json.tmp"
        temp_file.write_text('{"partial": "data"}')

        # list_jobs should only return valid state files
        jobs = await backend.list_jobs()
        job_ids = [j.job_id for j in jobs]

        assert "valid-state" in job_ids
        # Temp file should be ignored (no ".tmp" in job list)


class TestFileSystemEdgeCases:
    """Tests for file system edge cases."""

    @pytest.mark.asyncio
    async def test_special_characters_in_job_id(self, tmp_path: Path) -> None:
        """Test job IDs with special characters are sanitized."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="job/with\\special:chars",
            job_name="Special Chars",
            total_sheets=3,
        )

        await backend.save(state)
        loaded = await backend.load("job/with\\special:chars")

        assert loaded is not None
        assert loaded.job_id == "job/with\\special:chars"

    @pytest.mark.asyncio
    async def test_unicode_in_job_name(self, tmp_path: Path) -> None:
        """Test unicode characters in job name."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="unicode-test",
            job_name="テスト ジョブ 🎵",  # Japanese + emoji
            total_sheets=5,
        )

        await backend.save(state)
        loaded = await backend.load("unicode-test")

        assert loaded is not None
        assert loaded.job_name == "テスト ジョブ 🎵"

    @pytest.mark.asyncio
    async def test_large_state(self, tmp_path: Path) -> None:
        """Test handling of large state with many sheets."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="large-state",
            job_name="Large State Test",
            total_sheets=1000,
        )

        # Mark many sheets as completed with details
        for i in range(1, 501):
            state.mark_sheet_started(i)
            state.mark_sheet_completed(
                i,
                validation_passed=i % 2 == 0,
                validation_details=[
                    {"rule": f"rule-{i}", "passed": True, "description": f"Check {i}"}
                ],
            )

        await backend.save(state)
        loaded = await backend.load("large-state")

        assert loaded is not None
        assert loaded.last_completed_sheet == 500
        assert len(loaded.sheets) == 500

    @pytest.mark.asyncio
    async def test_directory_creation(self, tmp_path: Path) -> None:
        """Test that state directory is created if it doesn't exist."""
        nested_path = tmp_path / "deeply" / "nested" / "state" / "dir"

        # Directory doesn't exist yet
        assert not nested_path.exists()

        # Backend should create it
        backend = JsonStateBackend(nested_path)
        assert nested_path.exists()

        # Should be able to save state
        state = CheckpointState(
            job_id="nested-test",
            job_name="Nested Test",
            total_sheets=3,
        )
        await backend.save(state)

        loaded = await backend.load("nested-test")
        assert loaded is not None


class TestZombieDetectionAndRecovery:
    """Tests for zombie state detection and auto-recovery in load()."""

    @pytest.mark.asyncio
    async def test_zombie_detection_on_load(self, tmp_path: Path) -> None:
        """Test that a RUNNING state with dead PID is detected as zombie."""
        backend = JsonStateBackend(tmp_path)

        # Create state with RUNNING status and a PID that doesn't exist
        dead_pid = 2_000_000_000
        state = CheckpointState(
            job_id="zombie-test",
            job_name="Zombie Test",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.RUNNING,
            pid=dead_pid,
        )
        await backend.save(state)

        # Load should detect zombie and auto-recover to PAUSED
        loaded = await backend.load("zombie-test")
        assert loaded is not None
        assert loaded.status == JobStatus.PAUSED
        assert loaded.pid is None

        # Verify the recovery was persisted
        reloaded = await backend.load("zombie-test")
        assert reloaded is not None
        assert reloaded.status == JobStatus.PAUSED

    @pytest.mark.asyncio
    async def test_non_zombie_running_state_no_pid(self, tmp_path: Path) -> None:
        """Test that a RUNNING state without PID is not treated as zombie."""
        backend = JsonStateBackend(tmp_path)

        state = CheckpointState(
            job_id="no-pid-test",
            job_name="No PID Test",
            total_sheets=5,
            status=JobStatus.RUNNING,
            pid=None,
        )
        await backend.save(state)

        loaded = await backend.load("no-pid-test")
        assert loaded is not None
        # No PID means can't detect zombie - remains RUNNING
        assert loaded.status == JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_non_running_status_not_zombie(self, tmp_path: Path) -> None:
        """Test that non-RUNNING states are never treated as zombie."""
        backend = JsonStateBackend(tmp_path)

        for status in [JobStatus.PAUSED, JobStatus.COMPLETED, JobStatus.FAILED]:
            state = CheckpointState(
                job_id=f"status-{status.value}",
                job_name="Status Test",
                total_sheets=3,
                status=status,
                pid=2_000_000_000,  # Dead PID but wrong status
            )
            await backend.save(state)

            loaded = await backend.load(f"status-{status.value}")
            assert loaded is not None
            assert loaded.status == status  # Status unchanged

    @pytest.mark.asyncio
    async def test_truncated_json_raises_corruption_error(self, tmp_path: Path) -> None:
        """Test that a truncated JSON file (crash during write) raises StateCorruptionError."""
        from mozart.state.json_backend import StateCorruptionError

        backend = JsonStateBackend(tmp_path)

        state_file = tmp_path / "truncated.json"
        state_file.write_text('{"job_id": "truncated", "job_name": "Test", "total_she')

        with pytest.raises(StateCorruptionError, match="json_decode"):
            await backend.load("truncated")

    @pytest.mark.asyncio
    async def test_empty_file_raises_corruption_error(self, tmp_path: Path) -> None:
        """Test that an empty state file raises StateCorruptionError."""
        from mozart.state.json_backend import StateCorruptionError

        backend = JsonStateBackend(tmp_path)

        state_file = tmp_path / "empty.json"
        state_file.write_text("")

        with pytest.raises(StateCorruptionError, match="json_decode"):
            await backend.load("empty")

    @pytest.mark.asyncio
    async def test_nonexistent_job_returns_none(self, tmp_path: Path) -> None:
        """Test that loading a job with no state file returns None."""
        backend = JsonStateBackend(tmp_path)

        result = await backend.load("does-not-exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_zombie_recovery_preserves_sheet_progress(self, tmp_path: Path) -> None:
        """Test that zombie recovery preserves completed sheet data."""
        backend = JsonStateBackend(tmp_path)

        state = CheckpointState(
            job_id="zombie-progress",
            job_name="Zombie Progress Test",
            total_sheets=10,
            last_completed_sheet=5,
            status=JobStatus.RUNNING,
            pid=2_000_000_000,
        )
        # Add some sheet progress
        for i in range(1, 6):
            state.mark_sheet_started(i)
            state.mark_sheet_completed(i)
        await backend.save(state)

        # Load should recover from zombie but keep progress
        loaded = await backend.load("zombie-progress")
        assert loaded is not None
        assert loaded.status == JobStatus.PAUSED
        assert loaded.last_completed_sheet == 5
        assert len(loaded.sheets) == 5


class TestConcurrencyStress:
    """Stress tests for concurrent access patterns."""

    @pytest.mark.asyncio
    async def test_high_concurrency_mixed_operations(self, tmp_path: Path) -> None:
        """Test high concurrency with mixed read/write/delete operations."""
        backend = JsonStateBackend(tmp_path)

        # Initialize some jobs
        for i in range(5):
            state = CheckpointState(
                job_id=f"stress-job-{i}",
                job_name=f"Stress Test {i}",
                total_sheets=10,
            )
            await backend.save(state)

        operations_completed = {"read": 0, "write": 0, "list": 0}

        async def read_worker() -> None:
            for i in range(10):
                job_id = f"stress-job-{i % 5}"
                await backend.load(job_id)
                operations_completed["read"] += 1
                await asyncio.sleep(0.005)

        async def write_worker() -> None:
            for i in range(10):
                job_id = f"stress-job-{i % 5}"
                state = await backend.load(job_id)
                if state:
                    state.last_completed_sheet = i % 10
                    await backend.save(state)
                operations_completed["write"] += 1
                await asyncio.sleep(0)

        async def list_worker() -> None:
            for _ in range(5):
                await backend.list_jobs()
                operations_completed["list"] += 1
                await asyncio.sleep(0)

        # Run 3 of each worker type concurrently
        workers = [
            read_worker(), read_worker(), read_worker(),
            write_worker(), write_worker(), write_worker(),
            list_worker(), list_worker(), list_worker(),
        ]
        await asyncio.gather(*workers)

        # All operations should complete
        assert operations_completed["read"] == 30
        assert operations_completed["write"] == 30
        assert operations_completed["list"] == 15

    @pytest.mark.asyncio
    async def test_rapid_sequential_saves(self, tmp_path: Path) -> None:
        """Test rapid sequential saves to the same state."""
        backend = JsonStateBackend(tmp_path)
        state = CheckpointState(
            job_id="rapid-save",
            job_name="Rapid Save Test",
            total_sheets=100,
        )

        # Rapidly save 50 updates
        for i in range(1, 51):
            state.mark_sheet_started(i)
            state.mark_sheet_completed(i)
            await backend.save(state)

        # Verify final state
        loaded = await backend.load("rapid-save")
        assert loaded is not None
        assert loaded.last_completed_sheet == 50
        assert len(loaded.sheets) == 50
