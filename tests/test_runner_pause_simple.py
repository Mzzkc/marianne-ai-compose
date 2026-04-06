"""Simplified test for runner pause functionality."""
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import JobConfig
from marianne.execution.runner import JobRunner


def test_pause_signal_detection():
    """Test basic pause signal detection."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_workspace = Path(temp_dir)

        # Create a simple state
        state = CheckpointState(
            job_id="test-123",
            job_name="test",
            total_sheets=3,
            last_completed_sheet=0,
            status=JobStatus.RUNNING,
        )

        # Create a minimal config with workspace set to temp dir
        config = JobConfig.model_validate({
            "name": "test-pause",
            "description": "Test pause signal detection",
            "backend": {"type": "claude_cli", "skip_permissions": True},
            "sheet": {"size": 10, "total_items": 30},
            "prompt": {"template": "Process sheet {{ sheet_num }}."},
            "workspace": str(temp_workspace),
        })

        runner = JobRunner(
            config=config,
            backend=AsyncMock(),
            state_backend=AsyncMock(),
        )

        # Test 1: No pause signal file - should return False
        assert not runner._check_pause_signal(state)

        # Test 2: Create pause signal file - should return True
        pause_signal_file = temp_workspace / f".mozart-pause-{state.job_id}"
        pause_signal_file.touch()
        assert runner._check_pause_signal(state)

        # Test 3: Clear pause signal - should remove file
        runner._clear_pause_signal(state)
        assert not pause_signal_file.exists()

        # Test 4: After clearing, detection should return False again
        assert not runner._check_pause_signal(state)


if __name__ == "__main__":
    test_pause_signal_detection()
