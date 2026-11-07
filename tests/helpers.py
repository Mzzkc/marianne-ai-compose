"""Shared test helpers for Marianne tests."""

from __future__ import annotations

import math
import os
from typing import Any

from marianne.core.checkpoint import (
    CheckpointErrorRecord,
    ErrorType,
    SheetState,
)


def _parse_load_factor() -> float:
    """Read MARIANNE_TEST_LOAD_FACTOR, falling back to 1.0 on any bad input.

    Evaluated at collection time (marks are static), so the env var must be
    set before pytest starts. Garbage, empty, or non-float values must never
    raise: a bricked collection is worse than an unscaled timeout.
    """
    raw = os.environ.get("MARIANNE_TEST_LOAD_FACTOR", "1")
    try:
        factor = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(factor) or math.isinf(factor):
        return 1.0
    # Clamp: factors < 1 would shrink ceilings below the safety net's intent.
    return max(1.0, factor)


def load_scaled_timeout(base: float) -> int:
    """Scale a per-test timeout ceiling by the host-load factor.

    ``ceil(base * factor)`` where factor comes from MARIANNE_TEST_LOAD_FACTOR
    (default 1.0, clamped >= 1.0, garbage-safe). Used with
    ``@pytest.mark.timeout(load_scaled_timeout(N))`` on load-sensitive
    subprocess/IO tests whose internal budgets exceed the global 30s net
    (issue #501). The ceiling stays finite — a hung test still dies, just at
    a load-scaled bound.
    """
    return math.ceil(base * _parse_load_factor())


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
