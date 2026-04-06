"""Tests for CLI input validation utilities.

Validates job_id format, start_sheet range, and other CLI input surfaces.

TDD: These tests define the contract for CLI input validation.
"""

from __future__ import annotations

import pytest
import typer

from marianne.cli.commands._shared import validate_job_id, validate_start_sheet


# ---------------------------------------------------------------------------
# Job ID validation
# ---------------------------------------------------------------------------


class TestValidateJobId:
    """Tests for job ID validation."""

    def test_valid_simple_id(self) -> None:
        """Simple alphanumeric IDs are valid."""
        assert validate_job_id("my-job") == "my-job"

    def test_valid_with_underscores(self) -> None:
        """Underscores are allowed."""
        assert validate_job_id("my_job_123") == "my_job_123"

    def test_valid_with_dots(self) -> None:
        """Dots are allowed."""
        assert validate_job_id("v1.0.0-build") == "v1.0.0-build"

    def test_valid_single_char(self) -> None:
        """Single character IDs are valid."""
        assert validate_job_id("a") == "a"

    def test_valid_numeric_start(self) -> None:
        """IDs starting with a digit are valid."""
        assert validate_job_id("42-test") == "42-test"

    def test_valid_max_length(self) -> None:
        """IDs at exactly 100 chars are valid."""
        long_id = "a" * 100
        assert validate_job_id(long_id) == long_id

    def test_reject_empty(self) -> None:
        """Empty string is rejected."""
        with pytest.raises(typer.BadParameter, match="empty"):
            validate_job_id("")

    def test_reject_too_long(self) -> None:
        """IDs over 100 chars are rejected."""
        with pytest.raises(typer.BadParameter, match="too long"):
            validate_job_id("a" * 101)

    def test_reject_shell_metachar_semicolon(self) -> None:
        """Shell metacharacter ; is rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job;rm -rf /")

    def test_reject_shell_metachar_pipe(self) -> None:
        """Shell metacharacter | is rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job|cat")

    def test_reject_shell_metachar_ampersand(self) -> None:
        """Shell metacharacter & is rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job&bg")

    def test_reject_space(self) -> None:
        """Spaces are rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("my job")

    def test_reject_slash(self) -> None:
        """Path traversal characters are rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("../etc/passwd")

    def test_reject_starts_with_hyphen(self) -> None:
        """IDs starting with hyphen are rejected (could look like flags)."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("-job")

    def test_reject_starts_with_dot(self) -> None:
        """IDs starting with dot are rejected (hidden files)."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id(".hidden-job")

    def test_reject_backtick(self) -> None:
        """Backticks for command substitution are rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job`whoami`")

    def test_reject_dollar(self) -> None:
        """Dollar signs for variable expansion are rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job$HOME")

    def test_reject_null_byte(self) -> None:
        """Null bytes are rejected — can bypass C-level string operations."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job\x00evil")

    def test_reject_newline(self) -> None:
        """Newlines are rejected — can split log entries or HTTP headers."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job\nevil")

    def test_reject_tab(self) -> None:
        """Tab characters are rejected."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job\tevil")

    def test_reject_unicode(self) -> None:
        """Non-ASCII unicode is rejected — job IDs are identifiers."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("jöb-ümlaut")

    def test_reject_json_quote(self) -> None:
        """Double quotes are rejected — JSON injection prevention."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id('job"inject')

    def test_reject_curly_braces(self) -> None:
        """Curly braces are rejected — template injection prevention."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job{evil}")

    def test_reject_parentheses(self) -> None:
        """Parentheses are rejected — shell subshell prevention."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job(1)")

    def test_reject_backslash(self) -> None:
        """Backslashes are rejected — Windows path traversal prevention."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("..\\windows")

    def test_reject_hash(self) -> None:
        """Hash/pound is rejected — comment injection prevention."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job#comment")

    def test_reject_at_sign(self) -> None:
        """At sign is rejected — email/URL confusion prevention."""
        with pytest.raises(typer.BadParameter, match="Invalid score ID"):
            validate_job_id("job@host")


# ---------------------------------------------------------------------------
# Start sheet validation
# ---------------------------------------------------------------------------


class TestValidateStartSheet:
    """Tests for --start-sheet validation."""

    def test_none_passthrough(self) -> None:
        """None is returned unchanged (not provided)."""
        assert validate_start_sheet(None) is None

    def test_valid_sheet(self) -> None:
        """Valid sheet numbers pass through."""
        assert validate_start_sheet(5) == 5

    def test_valid_first_sheet(self) -> None:
        """Sheet 1 is valid."""
        assert validate_start_sheet(1) == 1

    def test_valid_with_total(self) -> None:
        """Sheet within total range is valid."""
        assert validate_start_sheet(5, total_sheets=10) == 5

    def test_valid_at_max(self) -> None:
        """Sheet at exactly total_sheets is valid."""
        assert validate_start_sheet(10, total_sheets=10) == 10

    def test_reject_zero(self) -> None:
        """Sheet 0 is rejected (1-indexed)."""
        with pytest.raises(typer.BadParameter, match="must be >= 1"):
            validate_start_sheet(0)

    def test_reject_negative(self) -> None:
        """Negative sheet numbers are rejected."""
        with pytest.raises(typer.BadParameter, match="must be >= 1"):
            validate_start_sheet(-1)

    def test_reject_exceeds_total(self) -> None:
        """Sheet beyond total_sheets is rejected."""
        with pytest.raises(typer.BadParameter, match="exceeds total"):
            validate_start_sheet(11, total_sheets=10)

    def test_no_total_allows_any_positive(self) -> None:
        """Without total_sheets, any positive integer is valid."""
        assert validate_start_sheet(999) == 999
