"""Tests for F-430: ValidationRule.sheet + condition precedence.

When both `sheet` and `condition` are set, `condition` takes precedence.
The `sheet` shorthand only sets `condition` when no explicit condition exists.

This test pins the behavior so the docstring/code stay in sync.
"""

from __future__ import annotations

from marianne.core.config.execution import ValidationRule


class TestSheetConditionPrecedence:
    """F-430: condition takes precedence over sheet shorthand."""

    def test_sheet_alone_generates_condition(self) -> None:
        """sheet: 3 → condition: 'sheet_num == 3' when no condition set."""
        rule = ValidationRule(type="file_exists", path="{workspace}/out.txt", sheet=3)
        assert rule.condition == "sheet_num == 3"

    def test_condition_alone_preserved(self) -> None:
        """Explicit condition is preserved as-is."""
        rule = ValidationRule(
            type="file_exists", path="{workspace}/out.txt",
            condition="sheet_num >= 5",
        )
        assert rule.condition == "sheet_num >= 5"

    def test_both_set_condition_wins(self) -> None:
        """When both sheet and condition are set, condition takes precedence."""
        rule = ValidationRule(
            type="file_exists", path="{workspace}/out.txt",
            sheet=3, condition="sheet_num >= 5",
        )
        # condition was already set, so sheet shorthand does NOT overwrite it
        assert rule.condition == "sheet_num >= 5"
        # sheet field is still stored for reference
        assert rule.sheet == 3

    def test_sheet_none_does_not_set_condition(self) -> None:
        """sheet: None → condition remains None (unconditional)."""
        rule = ValidationRule(type="file_exists", path="{workspace}/out.txt")
        assert rule.sheet is None
        assert rule.condition is None
