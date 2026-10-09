"""Abstract base for state backends."""

from abc import ABC, abstractmethod

from marianne.core.checkpoint import CheckpointState, SheetStatus
from marianne.core.constants import SHEET_NUM_KEY
from marianne.core.logging import get_logger

_logger = get_logger("state")
class StateBackend(ABC):
    """Abstract base class for state storage backends.

    Implementations handle persistence of job state for resumable execution.
    """

    @abstractmethod
    async def load(self, job_id: str) -> CheckpointState | None:
        """Load state for a job.

        Args:
            job_id: Unique job identifier

        Returns:
            CheckpointState if found, None otherwise
        """
        ...

    @abstractmethod
    async def save(self, state: CheckpointState) -> None:
        """Save job state.

        Args:
            state: Checkpoint state to persist
        """
        ...

    @abstractmethod
    async def delete(self, job_id: str) -> bool:
        """Delete state for a job.

        Args:
            job_id: Unique job identifier

        Returns:
            True if deleted, False if not found
        """
        ...

    @abstractmethod
    async def list_jobs(self) -> list[CheckpointState]:
        """List all jobs with state.

        Returns:
            List of all checkpoint states
        """
        ...

    @abstractmethod
    async def get_next_sheet(self, job_id: str) -> int | None:
        """Get the next sheet to process for a job.

        Args:
            job_id: Unique job identifier

        Returns:
            Next sheet number, or None if complete
        """
        ...

    @abstractmethod
    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        """Update status of a specific sheet.

        Args:
            job_id: Unique job identifier
            sheet_num: Sheet number to update
            status: New status
            error_message: Optional error message for failures
        """
        ...

    async def close(self) -> None:  # noqa: B027 — intentional concrete no-op default
        """Release any resources held by the backend (Q018/#37).

        Optional method — backends that hold persistent connections or file
        handles should override this. The default no-op is safe for backends
        that use per-operation connections (e.g. aiosqlite context managers).
        """

    @property
    def supports_execution_history(self) -> bool:
        """Whether this backend persists execution history.

        Backends that override ``record_execution`` should also override this
        to return ``True``.  Callers can check this to avoid silent data loss
        when a backend unexpectedly lacks execution recording.
        """
        return False

    async def record_execution(
        self,
        job_id: str,
        sheet_num: int,
        attempt_num: int,
        prompt: str | None = None,
        output: str | None = None,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
    ) -> int | None:
        """Record an execution attempt in history.

        Optional method -- backends that support execution recording (e.g. SQLite)
        override this. The default logs at WARNING level so silent data
        loss is traceable, then returns None.

        Args:
            job_id: Job identifier.
            sheet_num: Sheet number.
            attempt_num: Attempt number within the sheet.
            prompt: The prompt sent to the backend.
            output: The output received.
            exit_code: Exit code from the execution.
            duration_seconds: Execution duration.

        Returns:
            The ID of the inserted record, or None if not supported.
        """
        _logger.warning("record_execution_noop", job_id=job_id, **{SHEET_NUM_KEY: sheet_num})
        return None

    async def infer_state_from_artifacts(
        self,
        job_id: str,
        workspace: str,
        artifact_pattern: str,
    ) -> int | None:
        """Infer last completed sheet from artifact files.

        Fallback when state file is missing - checks for output files.
        Based on the fallback logic in run-sheet-review.sh.

        Args:
            job_id: Unique job identifier
            workspace: Workspace directory path
            artifact_pattern: Glob pattern for sheet artifacts (e.g., "sheet*-security-report.md")

        Returns:
            Inferred last completed sheet number, or None
        """
        import re
        from pathlib import Path

        workspace_path = Path(workspace)
        if not workspace_path.exists():
            return None

        # Find all matching artifact files
        artifacts = list(workspace_path.glob(artifact_pattern))
        if not artifacts:
            return None

        # Extract sheet numbers and find max
        sheet_nums = []
        for artifact in artifacts:
            match = re.search(r"sheet(\d+)", artifact.name)
            if match:
                sheet_nums.append(int(match.group(1)))

        return max(sheet_nums) if sheet_nums else None
