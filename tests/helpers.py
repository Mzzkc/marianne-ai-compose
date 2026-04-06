"""Shared test helpers for Mozart tests."""

from typing import Any

from marianne.core.checkpoint import (
    CheckpointErrorRecord,
    ErrorType,
    SheetState,
)


def record_error_on_sheet(
    state: SheetState,
    error_type: ErrorType,
    error_code: str,
    error_message: str,
    attempt: int,
    *,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    stack_trace: str | None = None,
    **context: Any,
) -> None:
    """Test helper: record an error on a SheetState (replaces removed SheetState.record_error).

    Creates a CheckpointErrorRecord, appends to error_history, and trims to max.
    """
    record = CheckpointErrorRecord(
        error_type=error_type,
        error_code=error_code,
        error_message=error_message,
        attempt_number=attempt,
        context=context,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        stack_trace=stack_trace,
    )
    state.add_error_to_history(record)
