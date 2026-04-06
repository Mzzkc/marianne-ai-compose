"""Tests for F-127: _classify_success_outcome uses cumulative attempt_count.

After a conductor restart + resume, session-local normal_attempts resets to 0
while the persisted attempt_count reflects the true cumulative count. The outcome
classification must use the persisted count to avoid labeling a sheet with 18
attempts as "success_first_try".

TDD: Tests written first, implementation follows.
"""

import pytest

from marianne.execution.runner.sheet import SheetExecutionMixin


class TestClassifySuccessOutcomeCumulative:
    """Verify _classify_success_outcome uses cumulative counts correctly."""

    def test_first_try_success(self) -> None:
        """Sheet that succeeds on first attempt."""
        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=1,
            completion_attempts=0,
        )
        assert outcome.value == "success_first_try"
        assert first_try is True

    def test_retry_success(self) -> None:
        """Sheet that took 3 attempts — NOT first try."""
        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=3,
            completion_attempts=0,
        )
        assert outcome.value == "success_retry"
        assert first_try is False

    def test_completion_mode_success(self) -> None:
        """Sheet that needed completion mode — 2 attempts, 1 completion."""
        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=2,
            completion_attempts=1,
        )
        assert outcome.value == "success_completion"
        assert first_try is False

    def test_resumed_sheet_18_attempts(self) -> None:
        """F-127 regression: 18 cumulative attempts should NOT be first_try."""
        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=18,
            completion_attempts=0,
        )
        assert outcome.value == "success_retry"
        assert first_try is False

    def test_resumed_sheet_with_completion(self) -> None:
        """Resumed sheet that used completion mode."""
        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=10,
            completion_attempts=3,
        )
        assert outcome.value == "success_completion"
        assert first_try is False

    def test_zero_attempts_edge_case(self) -> None:
        """Edge case: 0 cumulative attempts — should still classify as first try.

        This can happen if the sheet was not tracked via mark_sheet_started()
        (e.g., tests or legacy code paths).
        """
        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=0,
            completion_attempts=0,
        )
        assert outcome.value == "success_first_try"
        assert first_try is True

    def test_exactly_two_attempts(self) -> None:
        """Two attempts means one retry — not first try."""
        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=2,
            completion_attempts=0,
        )
        assert outcome.value == "success_retry"
        assert first_try is False
