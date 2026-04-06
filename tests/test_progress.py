"""Tests for execution progress tracking (Task 4).

Tests the ExecutionProgress, ProgressTracker, and StreamingOutputTracker
classes that enable real-time visibility during long-running sheet executions.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.execution.progress import (
    ExecutionProgress,
    ProgressTracker,
    StreamingOutputTracker,
)


class TestExecutionProgress:
    """Tests for ExecutionProgress dataclass."""

    def test_creation_with_defaults(self) -> None:
        """Test creating progress with default values."""
        now = datetime.now(UTC)
        progress = ExecutionProgress(
            started_at=now,
            last_activity_at=now,
        )

        assert progress.bytes_received == 0
        assert progress.lines_received == 0
        assert progress.phase == "starting"
        assert progress.elapsed_seconds >= 0

    def test_elapsed_seconds_calculation(self) -> None:
        """Test elapsed_seconds property calculation."""
        past = datetime.now(UTC) - timedelta(seconds=60)
        now = datetime.now(UTC)
        progress = ExecutionProgress(
            started_at=past,
            last_activity_at=now,
        )

        # Should be approximately 60 seconds
        assert 59 <= progress.elapsed_seconds <= 61

    def test_idle_seconds_calculation(self) -> None:
        """Test idle_seconds property calculation."""
        now = datetime.now(UTC)
        past = now - timedelta(seconds=30)
        progress = ExecutionProgress(
            started_at=past,
            last_activity_at=past,
        )

        # Should be approximately 30 seconds since last activity
        assert 29 <= progress.idle_seconds <= 31

    def test_format_bytes_small(self) -> None:
        """Test formatting small byte counts."""
        now = datetime.now(UTC)
        progress = ExecutionProgress(
            started_at=now,
            last_activity_at=now,
            bytes_received=500,
        )

        assert progress.format_bytes() == "500B"

    def test_format_bytes_kilobytes(self) -> None:
        """Test formatting kilobyte counts."""
        now = datetime.now(UTC)
        progress = ExecutionProgress(
            started_at=now,
            last_activity_at=now,
            bytes_received=5120,  # 5KB
        )

        assert progress.format_bytes() == "5.0KB"

    def test_format_bytes_megabytes(self) -> None:
        """Test formatting megabyte counts."""
        now = datetime.now(UTC)
        progress = ExecutionProgress(
            started_at=now,
            last_activity_at=now,
            bytes_received=2 * 1024 * 1024,  # 2MB
        )

        assert progress.format_bytes() == "2.0MB"

    def test_format_elapsed_seconds(self) -> None:
        """Test formatting short durations."""
        now = datetime.now(UTC)
        past = now - timedelta(seconds=45)
        progress = ExecutionProgress(
            started_at=past,
            last_activity_at=now,
        )

        formatted = progress.format_elapsed()
        assert "45s" in formatted or "44s" in formatted or "46s" in formatted

    def test_format_elapsed_minutes(self) -> None:
        """Test formatting minute durations."""
        now = datetime.now(UTC)
        past = now - timedelta(minutes=3, seconds=30)
        progress = ExecutionProgress(
            started_at=past,
            last_activity_at=now,
        )

        formatted = progress.format_elapsed()
        assert "3m" in formatted
        assert "30s" in formatted or "29s" in formatted or "31s" in formatted

    def test_format_elapsed_hours(self) -> None:
        """Test formatting hour durations."""
        now = datetime.now(UTC)
        past = now - timedelta(hours=1, minutes=15)
        progress = ExecutionProgress(
            started_at=past,
            last_activity_at=now,
        )

        formatted = progress.format_elapsed()
        assert "1h" in formatted
        assert "15m" in formatted

    def test_format_status(self) -> None:
        """Test formatting complete status line."""
        now = datetime.now(UTC)
        past = now - timedelta(minutes=2)
        progress = ExecutionProgress(
            started_at=past,
            last_activity_at=now,
            bytes_received=5120,
            lines_received=50,
            phase="executing",
        )

        status = progress.format_status()
        assert "Still running" in status
        assert "5.0KB" in status
        assert "50 lines" in status

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        now = datetime.now(UTC)
        progress = ExecutionProgress(
            started_at=now,
            last_activity_at=now,
            bytes_received=1024,
            lines_received=10,
            phase="executing",
        )

        result = progress.to_dict()

        assert result["bytes_received"] == 1024
        assert result["lines_received"] == 10
        assert result["phase"] == "executing"
        assert "started_at" in result
        assert "last_activity_at" in result
        assert "elapsed_seconds" in result


class TestProgressTracker:
    """Tests for ProgressTracker class."""

    def test_initial_state(self) -> None:
        """Test initial tracker state."""
        tracker = ProgressTracker()
        progress = tracker.get_progress()

        assert progress.bytes_received == 0
        assert progress.lines_received == 0
        assert progress.phase == "starting"

    def test_update_increments_counters(self) -> None:
        """Test update increments byte and line counters."""
        tracker = ProgressTracker()

        tracker.update(new_bytes=100, new_lines=5)
        progress = tracker.get_progress()

        assert progress.bytes_received == 100
        assert progress.lines_received == 5

        tracker.update(new_bytes=50, new_lines=3)
        progress = tracker.get_progress()

        assert progress.bytes_received == 150
        assert progress.lines_received == 8

    def test_update_calls_callback(self) -> None:
        """Test update calls the callback with progress."""
        callback_called = []

        def callback(progress: ExecutionProgress) -> None:
            callback_called.append(progress)

        tracker = ProgressTracker(callback=callback)
        tracker.update(new_bytes=100)

        assert len(callback_called) == 1
        assert callback_called[0].bytes_received == 100

    def test_set_phase(self) -> None:
        """Test setting execution phase."""
        tracker = ProgressTracker()
        tracker.set_phase("executing")

        progress = tracker.get_progress()
        assert progress.phase == "executing"

    def test_set_phase_records_snapshot(self) -> None:
        """Test phase change records a snapshot."""
        tracker = ProgressTracker()
        tracker.set_phase("executing")

        snapshots = tracker.get_snapshots()
        assert len(snapshots) >= 1
        assert snapshots[-1]["phase"] == "executing"

    def test_get_snapshots(self) -> None:
        """Test getting recorded snapshots."""
        tracker = ProgressTracker()
        tracker.update(new_bytes=100, force_snapshot=True)
        tracker.update(new_bytes=100, force_snapshot=True)

        snapshots = tracker.get_snapshots()
        assert len(snapshots) >= 2

    def test_reset_clears_state(self) -> None:
        """Test reset clears all tracking state."""
        tracker = ProgressTracker()
        tracker.update(new_bytes=100, new_lines=10)
        tracker.set_phase("executing")

        tracker.reset()
        progress = tracker.get_progress()

        assert progress.bytes_received == 0
        assert progress.lines_received == 0
        assert progress.phase == "starting"
        assert len(tracker.get_snapshots()) == 0

    def test_get_progress_returns_copy(self) -> None:
        """Test get_progress returns independent copy."""
        tracker = ProgressTracker()
        tracker.update(new_bytes=100)

        progress1 = tracker.get_progress()
        tracker.update(new_bytes=50)
        progress2 = tracker.get_progress()

        # Modifications to tracker shouldn't affect returned progress
        assert progress1.bytes_received == 100
        assert progress2.bytes_received == 150


class TestStreamingOutputTracker:
    """Tests for StreamingOutputTracker class."""

    def test_process_chunk_counts_bytes(self) -> None:
        """Test processing chunk counts bytes correctly."""
        progress_tracker = ProgressTracker()
        stream_tracker = StreamingOutputTracker(progress_tracker)

        stream_tracker.process_chunk(b"Hello, World!")
        progress = progress_tracker.get_progress()

        assert progress.bytes_received == 13

    def test_process_chunk_counts_lines(self) -> None:
        """Test processing chunk counts lines correctly."""
        progress_tracker = ProgressTracker()
        stream_tracker = StreamingOutputTracker(progress_tracker)

        stream_tracker.process_chunk(b"Line 1\nLine 2\nLine 3\n")
        progress = progress_tracker.get_progress()

        assert progress.lines_received == 3

    def test_process_chunk_handles_partial_lines(self) -> None:
        """Test handling partial lines across chunks."""
        progress_tracker = ProgressTracker()
        stream_tracker = StreamingOutputTracker(progress_tracker)

        # First chunk ends mid-line
        stream_tracker.process_chunk(b"First line\nPartial")
        assert progress_tracker.get_progress().lines_received == 1

        # Second chunk completes the line
        stream_tracker.process_chunk(b" line complete\n")
        assert progress_tracker.get_progress().lines_received == 2

    def test_process_empty_chunk(self) -> None:
        """Test processing empty chunk does nothing."""
        progress_tracker = ProgressTracker()
        stream_tracker = StreamingOutputTracker(progress_tracker)

        stream_tracker.process_chunk(b"")
        progress = progress_tracker.get_progress()

        assert progress.bytes_received == 0
        assert progress.lines_received == 0

    def test_finish_counts_remaining_partial_line(self) -> None:
        """Test finish counts any remaining partial line."""
        progress_tracker = ProgressTracker()
        stream_tracker = StreamingOutputTracker(progress_tracker)

        # Chunk without trailing newline
        stream_tracker.process_chunk(b"Line 1\nPartial without newline")
        assert progress_tracker.get_progress().lines_received == 1

        # Finish should count the partial line
        stream_tracker.finish()
        assert progress_tracker.get_progress().lines_received == 2


class TestSheetStateProgressFields:
    """Tests for SheetState progress tracking fields."""

    def test_progress_snapshots_default(self) -> None:
        """Test progress_snapshots defaults to empty list."""
        from marianne.core.checkpoint import SheetState

        state = SheetState(sheet_num=1)
        assert state.progress_snapshots == []

    def test_last_activity_at_default(self) -> None:
        """Test last_activity_at defaults to None."""
        from marianne.core.checkpoint import SheetState

        state = SheetState(sheet_num=1)
        assert state.last_activity_at is None

    def test_progress_snapshots_serialization(self) -> None:
        """Test progress_snapshots serializes to JSON correctly."""
        from marianne.core.checkpoint import SheetState

        now = datetime.now(UTC)
        state = SheetState(
            sheet_num=1,
            progress_snapshots=[
                {"bytes_received": 1024, "phase": "executing"},
                {"bytes_received": 2048, "phase": "completed"},
            ],
            last_activity_at=now,
        )

        # Verify the data is stored correctly
        assert len(state.progress_snapshots) == 2
        assert state.progress_snapshots[0]["bytes_received"] == 1024
        assert state.last_activity_at == now

        # Verify JSON serialization works
        json_dict = state.model_dump(mode="json")
        assert "progress_snapshots" in json_dict
        assert len(json_dict["progress_snapshots"]) == 2


class TestClaudeCliBackendProgress:
    """Tests for progress tracking in ClaudeCliBackend."""

    @pytest.mark.asyncio
    async def test_progress_callback_called(self) -> None:
        """Test that progress callback is called during execution."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        progress_updates: list[dict[str, Any]] = []

        def progress_callback(info: dict[str, Any]) -> None:
            progress_updates.append(info)

        # Mock shutil.which to return a path
        with patch("shutil.which", return_value="/usr/bin/claude"):
            backend = ClaudeCliBackend(
                progress_callback=progress_callback,
                progress_interval_seconds=0.1,
            )

        # Create mock stream reader that returns data then EOF
        async def mock_read(n: int) -> bytes:
            return b""  # EOF immediately

        mock_stdout = MagicMock()
        mock_stdout.read = mock_read

        mock_stderr = MagicMock()
        mock_stderr.read = mock_read

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr
        mock_process.wait = AsyncMock()
        mock_process.stdin = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            await backend.execute("test prompt")

        # Should have at least starting and completed phases
        assert len(progress_updates) >= 2
        phases = [p["phase"] for p in progress_updates]
        assert "starting" in phases
        assert "completed" in phases

    @pytest.mark.asyncio
    async def test_progress_includes_bytes_and_lines(self) -> None:
        """Test that progress updates include byte and line counts."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        progress_updates: list[dict[str, Any]] = []

        def progress_callback(info: dict[str, Any]) -> None:
            progress_updates.append(info)

        with patch("shutil.which", return_value="/usr/bin/claude"):
            backend = ClaudeCliBackend(
                progress_callback=progress_callback,
            )

        # Create mock stream reader that returns data then EOF
        output_data = b"line1\nline2\n"
        returned_data = [False]  # Track if we've returned data

        async def mock_stdout_read(n: int) -> bytes:
            if not returned_data[0]:
                returned_data[0] = True
                return output_data
            return b""  # EOF

        async def mock_stderr_read(n: int) -> bytes:
            return b""  # EOF immediately

        mock_stdout = MagicMock()
        mock_stdout.read = mock_stdout_read

        mock_stderr = MagicMock()
        mock_stderr.read = mock_stderr_read

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr
        mock_process.wait = AsyncMock()
        mock_process.stdin = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            await backend.execute("test prompt")

        # Final update should have correct counts
        final = progress_updates[-1]
        assert final["bytes_received"] == 12  # "line1\nline2\n"
        assert final["lines_received"] == 2

    @pytest.mark.asyncio
    async def test_no_callback_still_works(self) -> None:
        """Test that backend works without progress callback."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        with patch("shutil.which", return_value="/usr/bin/claude"):
            backend = ClaudeCliBackend()

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.pid = 12345
        mock_process.wait = AsyncMock()

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_stream_with_progress", return_value=(b"output", b"")),
        ):
            result = await backend.execute("test prompt")

        assert result.success
        assert result.stdout == "output"
