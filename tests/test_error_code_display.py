"""Tests for error code storage and display — F-097 fix.

The stale detection error display was showing 'Code: timeout' (the error
category) instead of 'E006' (the error code). Root cause: when error_code
is None (older state files), the fallback used error_category raw string
values like 'timeout' instead of mapping to proper error code prefixes.

Fix: format_error_code_for_display() maps categories to error codes when
the structured error_code is missing. Both status.py and diagnose.py
use this function for consistent error code display.

TDD: Red first, then implement.
"""

from __future__ import annotations

from marianne.cli.output import format_error_code_for_display, format_error_details
from marianne.core.checkpoint import (
    CheckpointErrorRecord,
    CheckpointState,
    JobStatus,
    SheetState,
)
from marianne.core.errors.codes import ErrorCategory, ErrorCode


class TestSheetStateErrorCode:
    """SheetState should store the structured error code, not just the category."""

    def test_sheet_state_has_error_code_field(self) -> None:
        """SheetState should have an error_code field."""
        sheet = SheetState(sheet_num=1)
        assert sheet.error_code is None

    def test_error_code_accepts_string(self) -> None:
        """error_code stores the ErrorCode value string (e.g., 'E006')."""
        sheet = SheetState(sheet_num=1, error_code="E006")
        assert sheet.error_code == "E006"

    def test_error_code_default_is_none(self) -> None:
        """error_code defaults to None for backward compatibility."""
        sheet = SheetState(sheet_num=1)
        assert sheet.error_code is None

    def test_error_code_persists_through_dict_roundtrip(self) -> None:
        """error_code survives serialization to dict and back."""
        sheet = SheetState(sheet_num=1, error_code="E006")
        data = sheet.model_dump()
        assert data["error_code"] == "E006"
        restored = SheetState.model_validate(data)
        assert restored.error_code == "E006"


class TestMarkSheetFailedErrorCode:
    """mark_sheet_failed should store both error_category AND error_code."""

    def _make_state(self) -> CheckpointState:
        """Create a minimal CheckpointState for testing."""
        return CheckpointState(
            job_id="test-job",
            job_name="test-job",
            status=JobStatus.RUNNING,
            total_sheets=1,
            sheets={1: SheetState(sheet_num=1)},
        )

    def test_mark_sheet_failed_stores_error_code(self) -> None:
        """When error_code is provided, it should be stored on the SheetState."""
        state = self._make_state()
        state.mark_sheet_failed(
            sheet_num=1,
            error_message="Stale execution: no output for 1800s",
            error_category="timeout",
            error_code="E006",
        )
        assert state.sheets[1].error_code == "E006"
        assert state.sheets[1].error_category == ErrorCategory.TIMEOUT

    def test_mark_sheet_failed_without_error_code(self) -> None:
        """When error_code is not provided, it should remain None (backward compat)."""
        state = self._make_state()
        state.mark_sheet_failed(
            sheet_num=1,
            error_message="Command timed out",
            error_category="timeout",
        )
        assert state.sheets[1].error_code is None
        assert state.sheets[1].error_category == ErrorCategory.TIMEOUT

    def test_stale_detection_gets_e006(self) -> None:
        """Stale detection should store E006, not E001."""
        state = self._make_state()
        state.mark_sheet_failed(
            sheet_num=1,
            error_message="Stale execution: no output for 1800s (limit: 1800s)",
            error_category="timeout",
            error_code=ErrorCode.EXECUTION_STALE.value,
            exit_reason="timeout",
        )
        assert state.sheets[1].error_code == "E006"
        assert state.sheets[1].error_category == ErrorCategory.TIMEOUT
        assert state.sheets[1].exit_reason == "timeout"

    def test_regular_timeout_gets_e001(self) -> None:
        """Regular timeout should store E001, distinct from stale."""
        state = self._make_state()
        state.mark_sheet_failed(
            sheet_num=1,
            error_message="Command timed out",
            error_category="timeout",
            error_code=ErrorCode.EXECUTION_TIMEOUT.value,
            exit_reason="timeout",
        )
        assert state.sheets[1].error_code == "E001"
        assert state.sheets[1].error_category == ErrorCategory.TIMEOUT


class TestFormatErrorCodeForDisplay:
    """format_error_code_for_display should produce proper error codes."""

    def test_prefers_error_code_when_present(self) -> None:
        """When error_code is set, use it directly."""
        result = format_error_code_for_display(
            error_code="E006",
            error_category=ErrorCategory.TIMEOUT,
        )
        assert result == "E006"

    def test_maps_timeout_category_to_e001(self) -> None:
        """When error_code is None and category is timeout, display E001."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.TIMEOUT,
        )
        assert result == "E001"

    def test_maps_rate_limit_category_to_e101(self) -> None:
        """When error_code is None and category is rate_limit, display E101."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.RATE_LIMIT,
        )
        assert result == "E101"

    def test_maps_auth_category_to_e502(self) -> None:
        """When error_code is None and category is auth, display E502 (BACKEND_AUTH)."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.AUTH,
        )
        assert result == "E502"

    def test_maps_signal_category_to_e002(self) -> None:
        """When error_code is None and category is signal, display E002."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.SIGNAL,
        )
        assert result == "E002"

    def test_maps_network_category_to_e003(self) -> None:
        """When error_code is None and category is network, display E003."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.NETWORK,
        )
        assert result == "E003"

    def test_maps_validation_category_to_e201(self) -> None:
        """When error_code is None and category is validation, display E201."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.VALIDATION,
        )
        assert result == "E201"

    def test_maps_configuration_category_to_e301(self) -> None:
        """When error_code is None and category is configuration, display E301."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.CONFIGURATION,
        )
        assert result == "E301"

    def test_maps_fatal_category_to_e999(self) -> None:
        """When error_code is None and category is fatal, display E999."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.FATAL,
        )
        assert result == "E999"

    def test_maps_transient_category_to_e004(self) -> None:
        """When error_code is None and category is transient, display E004."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=ErrorCategory.TRANSIENT,
        )
        assert result == "E004"

    def test_none_category_returns_e999(self) -> None:
        """When both error_code and category are None, display E999."""
        result = format_error_code_for_display(
            error_code=None,
            error_category=None,
        )
        assert result == "E999"

    def test_accepts_category_as_string(self) -> None:
        """Should handle category as a raw string (not enum)."""
        result = format_error_code_for_display(
            error_code=None,
            error_category="timeout",  # type: ignore[arg-type]
        )
        assert result == "E001"

    def test_unknown_category_string_returns_e999(self) -> None:
        """Unknown category strings should fallback to E999."""
        result = format_error_code_for_display(
            error_code=None,
            error_category="some_unknown_category",  # type: ignore[arg-type]
        )
        assert result == "E999"


class TestFormatErrorDetailsNormalization:
    """F-097: format_error_details should normalize raw category codes.

    The format_error_details function at output.py:791 displayed
    error.error_code directly. When error_code contained a raw category
    string like 'timeout', it showed 'Code: timeout' instead of 'Code: E001'.
    """

    def test_raw_timeout_code_normalized_in_details(self) -> None:
        """A raw 'timeout' in error_code displays as E001 in details."""
        error = CheckpointErrorRecord(
            error_type="transient",
            error_code="timeout",
            error_message="Stale execution detected",
            attempt_number=1,
        )
        details = format_error_details(error)
        assert "E001" in details
        # Must NOT show the raw category string as the code
        assert "[bold]Code:[/bold] timeout" not in details

    def test_raw_rate_limit_code_normalized_in_details(self) -> None:
        """A raw 'rate_limit' in error_code displays as E101 in details."""
        error = CheckpointErrorRecord(
            error_type="rate_limit",
            error_code="rate_limit",
            error_message="Rate limit reached",
            attempt_number=1,
        )
        details = format_error_details(error)
        assert "E101" in details
        assert "[bold]Code:[/bold] rate_limit" not in details

    def test_proper_e006_code_preserved_in_details(self) -> None:
        """A proper E006 code displays correctly."""
        error = CheckpointErrorRecord(
            error_type="transient",
            error_code="E006",
            error_message="Stale execution: no output for 1800s",
            attempt_number=3,
        )
        details = format_error_details(error)
        assert "[bold]Code:[/bold] E006" in details

    def test_raw_auth_code_normalized_in_details(self) -> None:
        """A raw 'auth' in error_code displays as E502 in details."""
        error = CheckpointErrorRecord(
            error_type="permanent",
            error_code="auth",
            error_message="Authentication failed",
            attempt_number=1,
        )
        details = format_error_details(error)
        assert "E502" in details
        assert "[bold]Code:[/bold] auth" not in details

    def test_raw_signal_code_normalized_in_details(self) -> None:
        """A raw 'signal' in error_code displays as E002 in details."""
        error = CheckpointErrorRecord(
            error_type="transient",
            error_code="signal",
            error_message="Process killed by signal 9",
            attempt_number=1,
        )
        details = format_error_details(error)
        assert "E002" in details
        assert "[bold]Code:[/bold] signal" not in details
