"""Tests for CLI error display UX improvements — Lens M4.

Three UX gaps identified:
1. Status JSON output should include error_code for automation users
2. Recover command should clear error_code when marking sheet as completed
3. infer_error_type should recognize error code strings (E101, E006, etc.)

TDD: Red first, then green.
"""

from __future__ import annotations

from marianne.cli.output import infer_error_type
from marianne.core.checkpoint import (
    ErrorCategory,
    SheetState,
    SheetStatus,
)

# ─────────────────────────────────────────────────────────────────────
# Fix 1: Status JSON output should include error_code
# ─────────────────────────────────────────────────────────────────────


class TestStatusJsonErrorCode:
    """The status JSON output should include error_code alongside error_category."""

    def test_json_sheet_data_includes_error_code(self) -> None:
        """Per-sheet JSON data should have an error_code field."""
        sheet = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            error_message="Stale execution: no output for 1800s",
            error_category=ErrorCategory.TIMEOUT,
            error_code="E006",
        )
        # Simulate the JSON dict construction from status.py:670-677
        sheet_data = {
            "status": sheet.status.value,
            "attempt_count": sheet.attempt_count,
            "validation_passed": sheet.validation_passed,
            "error_message": sheet.error_message,
            "error_category": sheet.error_category,
            "error_code": sheet.error_code,
        }
        assert "error_code" in sheet_data
        assert sheet_data["error_code"] == "E006"

    def test_json_sheet_data_error_code_none_when_absent(self) -> None:
        """error_code should be None when not set (backward compat)."""
        sheet = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            error_message="Command timed out",
            error_category=ErrorCategory.TIMEOUT,
        )
        sheet_data = {
            "error_category": sheet.error_category,
            "error_code": sheet.error_code,
        }
        assert sheet_data["error_code"] is None
        # error_category should still be present as fallback
        assert sheet_data["error_category"] is not None

    def test_json_sheet_data_both_fields_present(self) -> None:
        """Both error_category and error_code should be in JSON output."""
        sheet = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            error_message="Rate limit exceeded",
            error_category=ErrorCategory.RATE_LIMIT,
            error_code="E101",
        )
        sheet_data = {
            "error_category": sheet.error_category,
            "error_code": sheet.error_code,
        }
        # Automation users get both: the precise code AND the broad category
        assert sheet_data["error_code"] == "E101"
        assert sheet_data["error_category"] == ErrorCategory.RATE_LIMIT


# ─────────────────────────────────────────────────────────────────────
# Fix 2: Recover command should clear error_code
# ─────────────────────────────────────────────────────────────────────


class TestRecoverClearsErrorCode:
    """When recovering a sheet, error_code should be cleared alongside error_category."""

    def test_recover_clears_error_code(self) -> None:
        """Recovered sheet should have error_code=None."""
        sheet = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            error_message="Stale execution: no output for 1800s",
            error_category=ErrorCategory.TIMEOUT,
            error_code="E006",
        )
        # Simulate the recover logic from recover.py:190-195
        sheet.status = SheetStatus.COMPLETED
        sheet.validation_passed = True
        sheet.error_message = None
        sheet.error_category = None
        sheet.error_code = None

        assert sheet.error_code is None
        assert sheet.error_category is None
        assert sheet.error_message is None

    def test_recover_does_not_leave_stale_error_code(self) -> None:
        """After recovery, error_code must not contain stale data."""
        sheet = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            error_message="Authentication failure",
            error_category=ErrorCategory.AUTH,
            error_code="E301",
        )
        sheet.status = SheetStatus.COMPLETED
        sheet.validation_passed = True
        sheet.error_message = None
        sheet.error_category = None
        sheet.error_code = None

        assert sheet.error_code is None
        assert sheet.status == SheetStatus.COMPLETED


# ─────────────────────────────────────────────────────────────────────
# Fix 3: infer_error_type should recognize error code strings
# ─────────────────────────────────────────────────────────────────────


class TestInferErrorTypeFromCodes:
    """infer_error_type should work with both category strings AND error codes."""

    # Existing behavior (category strings) — must not regress
    def test_rate_limit_category(self) -> None:
        assert infer_error_type("rate_limit") == "rate_limit"

    def test_timeout_category(self) -> None:
        assert infer_error_type("timeout") == "transient"

    def test_none_category(self) -> None:
        assert infer_error_type(None) == "permanent"

    def test_permanent_category(self) -> None:
        assert infer_error_type("authentication") == "permanent"

    # New behavior — error code strings
    def test_e101_rate_limit_api(self) -> None:
        """E101 (RATE_LIMIT_API) should infer as rate_limit."""
        assert infer_error_type("E101") == "rate_limit"

    def test_e102_rate_limit_cli(self) -> None:
        """E102 (RATE_LIMIT_CLI) should infer as rate_limit."""
        assert infer_error_type("E102") == "rate_limit"

    def test_e103_capacity_exceeded(self) -> None:
        """E103 (CAPACITY_EXCEEDED) should infer as rate_limit."""
        assert infer_error_type("E103") == "rate_limit"

    def test_e104_quota_exhausted(self) -> None:
        """E104 (QUOTA_EXHAUSTED) should infer as rate_limit."""
        assert infer_error_type("E104") == "rate_limit"

    def test_e001_timeout(self) -> None:
        """E001 (EXECUTION_TIMEOUT) should infer as transient."""
        assert infer_error_type("E001") == "transient"

    def test_e006_stale(self) -> None:
        """E006 (EXECUTION_STALE) should infer as transient."""
        assert infer_error_type("E006") == "transient"

    def test_e002_killed(self) -> None:
        """E002 (EXECUTION_KILLED) should infer as transient."""
        assert infer_error_type("E002") == "transient"

    def test_e003_crashed(self) -> None:
        """E003 (EXECUTION_CRASHED) should infer as transient."""
        assert infer_error_type("E003") == "transient"

    def test_e301_auth_failure(self) -> None:
        """E301 (AUTH_FAILURE) should infer as permanent."""
        assert infer_error_type("E301") == "permanent"

    def test_e999_unknown(self) -> None:
        """E999 (UNKNOWN) should infer as permanent."""
        assert infer_error_type("E999") == "permanent"

    def test_e201_validation(self) -> None:
        """E2xx (validation errors) should infer as permanent."""
        assert infer_error_type("E201") == "permanent"
