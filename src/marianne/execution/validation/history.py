"""Failure history store for history-aware prompt generation.

Queries past validation failures from checkpoint state to help
avoid repeating the same mistakes across sheets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marianne.core.checkpoint import SheetState, ValidationDetailDict


@dataclass
class HistoricalFailure:
    """A single historical validation failure for prompt injection."""

    sheet_num: int
    rule_type: str
    description: str
    failure_reason: str | None = None
    failure_category: str | None = None
    suggested_fix: str | None = None


class FailureHistoryStore:
    """Queries past validation failures from checkpoint state.

    Enables history-aware prompt generation by extracting validation
    failures from previous sheets and finding similar failures.
    """

    def __init__(self, sheets: Mapping[int, SheetState]) -> None:
        """Initialize failure history store.

        Takes the per-sheet execution-state map directly (#207): the store only
        reads sheet ``validation_details``, so it needs the sheets, not a full
        CheckpointState. The baton feeds it ``self._baton._jobs[job_id].sheets``
        (the live SheetState objects carrying validation_details).
        """
        self._sheets = sheets

    @staticmethod
    def _detail_to_failure(
        sheet_num: int, detail: ValidationDetailDict,
    ) -> HistoricalFailure:
        """Convert a validation detail dict to a HistoricalFailure."""
        return HistoricalFailure(
            sheet_num=sheet_num,
            rule_type=detail.get("rule_type") or "",
            description=detail.get("description") or "",
            failure_reason=detail.get("failure_reason"),
            failure_category=detail.get("failure_category"),
            suggested_fix=detail.get("suggested_fix"),
        )

    def query_similar_failures(
        self,
        current_sheet: int,
        rule_types: list[str] | None = None,
        failure_categories: list[str] | None = None,
        limit: int = 3,
    ) -> list[HistoricalFailure]:
        """Query past validation failures similar to expected patterns."""
        failures: list[HistoricalFailure] = []

        for sheet_num in sorted(self._sheets.keys(), reverse=True):
            if sheet_num >= current_sheet:
                continue

            sheet = self._sheets.get(sheet_num)
            if not sheet or not sheet.validation_details:
                continue

            for detail in sheet.validation_details:
                if detail.get("passed", False):
                    continue

                rule_type = detail.get("rule_type", "")
                failure_category = detail.get("failure_category")

                if rule_types and rule_type not in rule_types:
                    continue
                if failure_categories and failure_category not in failure_categories:
                    continue

                failures.append(self._detail_to_failure(sheet_num, detail))

                if len(failures) >= limit:
                    return failures

        return failures

    def query_recent_failures(
        self,
        current_sheet: int,
        lookback_sheets: int = 3,
        limit: int = 3,
    ) -> list[HistoricalFailure]:
        """Query recent validation failures from nearby sheets."""
        failures: list[HistoricalFailure] = []

        for offset in range(1, lookback_sheets + 1):
            sheet_num = current_sheet - offset
            if sheet_num <= 0:
                break

            sheet = self._sheets.get(sheet_num)
            if not sheet or not sheet.validation_details:
                continue

            for detail in sheet.validation_details:
                if detail.get("passed", False):
                    continue

                failures.append(self._detail_to_failure(sheet_num, detail))

                if len(failures) >= limit:
                    return failures

        return failures

    def has_failures(self, current_sheet: int) -> bool:
        """Check if there are any historical failures to query."""
        for sheet_num, sheet in self._sheets.items():
            if sheet_num >= current_sheet:
                continue

            if not sheet.validation_details:
                continue

            if any(not d.get("passed", False) for d in sheet.validation_details):
                return True

        return False
