"""JSON file-based state backend.

Simple state storage using JSON files, similar to the .review-state
approach in the original bash script.
"""

import json
import os
from pathlib import Path
from typing import Any

from marianne.core.checkpoint import CheckpointState, SheetStatus
from marianne.core.logging import get_logger
from marianne.state.base import StateBackend
from marianne.utils.time import utc_now

# Module-level logger for state operations
_logger = get_logger("state.json")


class StateCorruptionError(Exception):
    """Raised when a state file exists but contains corrupt or invalid data.

    Distinguishes corrupt state (data loss) from missing state (normal for new jobs).
    """

    def __init__(self, job_id: str, path: str, error_type: str, detail: str) -> None:
        self.job_id = job_id
        self.path = path
        self.error_type = error_type
        super().__init__(
            f"Corrupt state file for job '{job_id}' at {path}: "
            f"{error_type} - {detail}"
        )


class JsonStateBackend(StateBackend):
    """JSON file-based state storage.

    Stores each job's state in a separate JSON file within the state directory.
    File naming: {state_dir}/{job_id}.json
    """

    def __init__(self, state_dir: Path):
        """Initialize JSON backend.

        Args:
            state_dir: Directory to store state files
        """
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _get_state_file(self, job_id: str) -> Path:
        """Get the state file path for a job."""
        # Sanitize job_id for filesystem
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)
        return self.state_dir / f"{safe_id}.json"

    async def load(self, job_id: str) -> CheckpointState | None:
        """Load state from JSON file.

        Automatically detects and recovers zombie jobs (RUNNING status but
        process dead). When a zombie is detected, the state is updated to
        PAUSED and saved before returning.
        """
        state_file = self._get_state_file(job_id)
        if not state_file.exists():
            _logger.debug("state_file_not_found", job_id=job_id, path=str(state_file))
            return None

        try:
            with open(state_file) as f:
                data = json.load(f)
            state = CheckpointState.model_validate(data)

            # Detect zombie state (running but process dead) without saving.
            # Callers that need persistence (e.g. runner lifecycle) save
            # explicitly — load() remains read-only for dashboard/CLI callers.
            if state.is_zombie():
                _logger.warning(
                    "zombie_detected_on_load",
                    job_id=job_id,
                    pid=state.pid,
                    status=state.status.value,
                )
                state.mark_zombie_detected(
                    reason="Detected on state load - process no longer running"
                )

            _logger.debug(
                "checkpoint_loaded",
                job_id=job_id,
                status=state.status.value,
                last_completed_sheet=state.last_completed_sheet,
                total_sheets=state.total_sheets,
            )
            return state
        except (json.JSONDecodeError, ValueError) as e:
            error_type = "json_decode" if isinstance(e, json.JSONDecodeError) else "validation"
            _logger.error(
                "checkpoint_corruption_detected",
                job_id=job_id,
                path=str(state_file),
                error_type=error_type,
                error=str(e),
            )
            raise StateCorruptionError(
                job_id=job_id,
                path=str(state_file),
                error_type=error_type,
                detail=str(e),
            ) from e

    async def save(self, state: CheckpointState) -> None:
        """Save state to JSON file."""
        state.updated_at = utc_now()
        state_file = self._get_state_file(state.job_id)

        # Write atomically using temp file + rename
        temp_file = state_file.with_suffix(".json.tmp")
        with open(temp_file, "w") as f:
            json.dump(
                state.model_dump(mode="json"),
                f,
                indent=2,
                default=str,  # Handle datetime serialization
            )
            f.flush()
            os.fsync(f.fileno())
        temp_file.replace(state_file)

        _logger.info(
            "checkpoint_saved",
            job_id=state.job_id,
            status=state.status.value,
            last_completed_sheet=state.last_completed_sheet,
            total_sheets=state.total_sheets,
            path=str(state_file),
        )

    async def delete(self, job_id: str) -> bool:
        """Delete state file."""
        state_file = self._get_state_file(job_id)
        if state_file.exists():
            state_file.unlink()
            return True
        return False

    async def list_jobs(self) -> list[CheckpointState]:
        """List all jobs with state files.

        Optimized: reads raw JSON and sorts by updated_at before doing full
        Pydantic validation, avoiding expensive model_validate on every file
        just to determine sort order.
        """
        # Phase 1: Read raw JSON and extract sort key (no Pydantic overhead)
        # (updated_at_str, data, path) — path is carried so a later validation
        # failure can name the offending file (#246).
        raw_entries: list[tuple[str, dict[str, Any], str]] = []
        for state_file in self.state_dir.glob("*.json"):
            if state_file.suffix == ".json" and not state_file.name.endswith(".tmp"):
                try:
                    with open(state_file) as f:
                        data = json.load(f)
                    # Extract updated_at for sorting without full validation
                    updated_at = data.get("updated_at", "")
                    raw_entries.append((updated_at, data, str(state_file)))
                except (json.JSONDecodeError, OSError) as exc:
                    _logger.warning("corrupt_state_file", path=str(state_file), error=str(exc))
                    continue

        # Phase 2: Sort by raw updated_at string (ISO format sorts lexicographically)
        raw_entries.sort(key=lambda e: e[0], reverse=True)

        # Phase 3: Validate sorted entries
        states: list[CheckpointState] = []
        for _, data, path in raw_entries:
            try:
                states.append(CheckpointState.model_validate(data))
            except ValueError as exc:
                # #246: a corrupt job is dropped from the listing — name the
                # job_id and file so it doesn't silently vanish with no trace.
                _logger.warning(
                    "invalid_state_data",
                    path=path,
                    job_id=data.get("job_id"),
                    error=str(exc),
                )
                continue
        return states

    async def get_next_sheet(self, job_id: str) -> int | None:
        """Get next sheet from state."""
        state = await self.load(job_id)
        if state is None:
            return 1  # Start from beginning if no state
        return state.get_next_sheet()

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        """Update sheet status in state."""
        state = await self.load(job_id)
        if state is None:
            raise ValueError(f"No state found for job {job_id}")

        if status == SheetStatus.COMPLETED:
            state.mark_sheet_completed(sheet_num)
        elif status == SheetStatus.FAILED:
            state.mark_sheet_failed(sheet_num, error_message or "Unknown error")
        elif status == SheetStatus.IN_PROGRESS:
            state.mark_sheet_started(sheet_num)

        await self.save(state)
