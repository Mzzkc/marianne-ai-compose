"""Tests for mozart.validation.reporter module.

Covers the ValidationReporter class: terminal formatting, JSON output,
plain text formatting, and edge cases (empty issues, long context,
optional fields in _issue_to_dict).

GH#82 — Validation reporter at 61% coverage.
"""

from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.reporter import ValidationReporter


def _make_issue(
    check_id: str = "V001",
    severity: ValidationSeverity = ValidationSeverity.ERROR,
    message: str = "Test issue",
    line: int | None = None,
    column: int | None = None,
    context: str | None = None,
    suggestion: str | None = None,
    auto_fixable: bool = False,
    metadata: dict[str, str] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        check_id=check_id,
        severity=severity,
        message=message,
        line=line,
        column=column,
        context=context,
        suggestion=suggestion,
        auto_fixable=auto_fixable,
        metadata=metadata or {},
    )


def _capture_terminal(
    issues: list[ValidationIssue],
    config_name: str = "test.yaml",
    width: int = 120,
) -> str:
    """Run report_terminal and return plain-text output (no ANSI escapes)."""
    buf = StringIO()
    console = Console(file=buf, color_system=None, width=width)
    reporter = ValidationReporter(console=console)
    reporter.report_terminal(issues, config_name)
    return buf.getvalue()


class TestReportTerminal:
    """Tests for report_terminal output."""

    def test_no_issues_shows_green_valid(self) -> None:
        """Empty issues list -> 'Configuration valid' panel."""
        output = _capture_terminal([], "test-config.yaml")
        assert "Configuration valid" in output
        assert "test-config.yaml" in output

    def test_errors_only(self) -> None:
        """Only errors -> FAILED status."""
        issues = [_make_issue(severity=ValidationSeverity.ERROR, message="Bad syntax")]
        output = _capture_terminal(issues, "broken.yaml")
        assert "FAILED" in output

    def test_warnings_only_passed(self) -> None:
        """Only warnings -> PASSED (with warnings)."""
        issues = [_make_issue(severity=ValidationSeverity.WARNING, message="Suspicious")]
        output = _capture_terminal(issues, "warn.yaml")
        assert "PASSED" in output
        assert "with warnings" in output

    def test_mixed_severities(self) -> None:
        """Errors + warnings + info -> FAILED and summary with all counts."""
        issues = [
            _make_issue(severity=ValidationSeverity.ERROR, message="e1"),
            _make_issue(severity=ValidationSeverity.ERROR, message="e2"),
            _make_issue(severity=ValidationSeverity.WARNING, message="w1"),
            _make_issue(severity=ValidationSeverity.INFO, message="i1"),
        ]
        output = _capture_terminal(issues, "mixed.yaml")
        assert "FAILED" in output
        assert "2 errors" in output
        assert "1 warning" in output  # singular (no trailing s)
        assert "1 info note" in output  # singular (no trailing s)

    def test_singular_error_count(self) -> None:
        """Single error -> '1 error' (no trailing s)."""
        issues = [_make_issue(severity=ValidationSeverity.ERROR)]
        output = _capture_terminal(issues, "single.yaml")
        assert "1 error" in output
        assert "1 errors" not in output

    def test_plural_error_count(self) -> None:
        """Two errors -> '2 errors' (with trailing s)."""
        issues = [
            _make_issue(severity=ValidationSeverity.ERROR, message="e1"),
            _make_issue(severity=ValidationSeverity.ERROR, message="e2"),
        ]
        output = _capture_terminal(issues, "plural.yaml")
        assert "2 errors" in output

    def test_plural_warning_count(self) -> None:
        """Two warnings -> '2 warnings' (with trailing s)."""
        issues = [
            _make_issue(severity=ValidationSeverity.WARNING, message="w1"),
            _make_issue(severity=ValidationSeverity.WARNING, message="w2"),
        ]
        output = _capture_terminal(issues, "warns.yaml")
        assert "2 warnings" in output

    def test_plural_info_count(self) -> None:
        """Two info notes -> '2 info notes' (with trailing s)."""
        issues = [
            _make_issue(severity=ValidationSeverity.INFO, message="i1"),
            _make_issue(severity=ValidationSeverity.INFO, message="i2"),
        ]
        output = _capture_terminal(issues, "infos.yaml")
        assert "2 info notes" in output

    def test_context_truncation(self) -> None:
        """Long context string (>70 chars) is truncated with ellipsis."""
        long_ctx = "x" * 100
        issues = [_make_issue(context=long_ctx)]
        output = _capture_terminal(issues, "ctx.yaml", width=200)
        # Context >70 chars is truncated to 67 chars + "..."
        assert "..." in output
        # The full 100-char string should NOT appear
        assert long_ctx not in output

    def test_short_context_not_truncated(self) -> None:
        """Short context string (<=70 chars) appears in full."""
        short_ctx = "y" * 50
        issues = [_make_issue(context=short_ctx)]
        output = _capture_terminal(issues, "ctx.yaml", width=200)
        assert short_ctx in output

    def test_suggestion_shown(self) -> None:
        """Suggestion text appears in terminal output."""
        issues = [_make_issue(suggestion="Fix it by doing X")]
        output = _capture_terminal(issues, "suggest.yaml")
        assert "Fix it by doing X" in output
        assert "Suggestion:" in output

    def test_config_name_in_valid_output(self) -> None:
        """Config name appears in the valid panel."""
        output = _capture_terminal([], "my-fancy-config.yaml")
        assert "my-fancy-config.yaml" in output

    def test_no_issues_returns_early_no_summary(self) -> None:
        """No issues -> only the valid panel, no FAILED/Summary lines."""
        output = _capture_terminal([], "clean.yaml")
        assert "FAILED" not in output
        assert "Summary:" not in output

    def test_severity_icons_in_output(self) -> None:
        """Severity icons appear for each severity level."""
        issues = [
            _make_issue(severity=ValidationSeverity.ERROR, message="err"),
            _make_issue(severity=ValidationSeverity.WARNING, message="warn"),
            _make_issue(severity=ValidationSeverity.INFO, message="inf"),
        ]
        output = _capture_terminal(issues)
        # Check icons from SEVERITY_ICONS
        assert "✗" in output  # error
        assert "!" in output  # warning
        # 'i' icon for info may appear in other text, just check sections exist
        assert "ERRORS" in output
        assert "WARNINGS" in output
        assert "INFO" in output

    def test_section_headers(self) -> None:
        """Section headers match expected titles."""
        issues = [
            _make_issue(severity=ValidationSeverity.ERROR, message="e1"),
            _make_issue(severity=ValidationSeverity.WARNING, message="w1"),
            _make_issue(severity=ValidationSeverity.INFO, message="i1"),
        ]
        output = _capture_terminal(issues)
        assert "ERRORS (must fix before running):" in output
        assert "WARNINGS (may cause issues):" in output
        assert "INFO (consider reviewing):" in output


class TestReportJson:
    """Tests for report_json output."""

    def test_empty_issues_valid(self) -> None:
        """No issues -> valid=True, all counts 0."""
        reporter = ValidationReporter()
        result = json.loads(reporter.report_json([]))
        assert result["valid"] is True
        assert result["error_count"] == 0
        assert result["warning_count"] == 0
        assert result["info_count"] == 0
        assert result["issues"] == []

    def test_errors_make_invalid(self) -> None:
        """Presence of errors -> valid=False."""
        reporter = ValidationReporter()
        issues = [_make_issue(severity=ValidationSeverity.ERROR)]
        result = json.loads(reporter.report_json(issues))
        assert result["valid"] is False
        assert result["error_count"] == 1

    def test_warnings_only_still_valid(self) -> None:
        """Only warnings -> valid=True."""
        reporter = ValidationReporter()
        issues = [_make_issue(severity=ValidationSeverity.WARNING)]
        result = json.loads(reporter.report_json(issues))
        assert result["valid"] is True
        assert result["warning_count"] == 1

    def test_info_only_still_valid(self) -> None:
        """Only info -> valid=True."""
        reporter = ValidationReporter()
        issues = [_make_issue(severity=ValidationSeverity.INFO)]
        result = json.loads(reporter.report_json(issues))
        assert result["valid"] is True
        assert result["info_count"] == 1

    def test_multiple_issues_counted(self) -> None:
        """Multiple issues of each severity are counted correctly."""
        reporter = ValidationReporter()
        issues = [
            _make_issue(severity=ValidationSeverity.ERROR, message="e1"),
            _make_issue(severity=ValidationSeverity.ERROR, message="e2"),
            _make_issue(severity=ValidationSeverity.WARNING, message="w1"),
            _make_issue(severity=ValidationSeverity.WARNING, message="w2"),
            _make_issue(severity=ValidationSeverity.WARNING, message="w3"),
            _make_issue(severity=ValidationSeverity.INFO, message="i1"),
        ]
        result = json.loads(reporter.report_json(issues))
        assert result["valid"] is False
        assert result["error_count"] == 2
        assert result["warning_count"] == 3
        assert result["info_count"] == 1
        assert len(result["issues"]) == 6

    def test_json_is_valid_json(self) -> None:
        """Output is valid parseable JSON."""
        reporter = ValidationReporter()
        issues = [_make_issue()]
        raw = reporter.report_json(issues)
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_issue_dict_all_optional_fields(self) -> None:
        """_issue_to_dict includes optional fields when set."""
        reporter = ValidationReporter()
        issue = _make_issue(
            check_id="V007",
            severity=ValidationSeverity.WARNING,
            message="Invalid regex",
            line=10,
            column=5,
            context="some context",
            suggestion="do this",
            auto_fixable=True,
            metadata={"key": "val"},
        )
        d = reporter._issue_to_dict(issue)
        assert d["check_id"] == "V007"
        assert d["severity"] == "warning"
        assert d["message"] == "Invalid regex"
        assert d["line"] == 10
        assert d["column"] == 5
        assert d["context"] == "some context"
        assert d["suggestion"] == "do this"
        assert d["auto_fixable"] is True
        assert d["metadata"] == {"key": "val"}

    def test_issue_dict_no_optional_fields(self) -> None:
        """_issue_to_dict omits optional fields when not set."""
        reporter = ValidationReporter()
        issue = _make_issue()
        d = reporter._issue_to_dict(issue)
        # Required fields always present
        assert d["check_id"] == "V001"
        assert d["severity"] == "error"
        assert d["message"] == "Test issue"
        # Optional fields omitted
        assert "line" not in d
        assert "column" not in d
        assert "context" not in d
        assert "suggestion" not in d
        assert "auto_fixable" not in d
        # metadata is empty dict -> falsy -> not included
        assert "metadata" not in d

    def test_issue_dict_partial_optional_fields(self) -> None:
        """_issue_to_dict includes only the fields that are set."""
        reporter = ValidationReporter()
        issue = _make_issue(line=5, suggestion="fix it")
        d = reporter._issue_to_dict(issue)
        assert d["line"] == 5
        assert d["suggestion"] == "fix it"
        assert "column" not in d
        assert "context" not in d
        assert "auto_fixable" not in d

    def test_issue_dict_severity_value(self) -> None:
        """_issue_to_dict uses severity enum value (lowercase string)."""
        reporter = ValidationReporter()
        for sev in ValidationSeverity:
            issue = _make_issue(severity=sev)
            d = reporter._issue_to_dict(issue)
            assert d["severity"] == sev.value

    def test_issue_dict_auto_fixable_false_omitted(self) -> None:
        """auto_fixable=False is falsy, so it should be omitted."""
        reporter = ValidationReporter()
        issue = _make_issue(auto_fixable=False)
        d = reporter._issue_to_dict(issue)
        assert "auto_fixable" not in d

    def test_json_output_has_indent(self) -> None:
        """JSON output is indented (pretty-printed)."""
        reporter = ValidationReporter()
        raw = reporter.report_json([])
        # Indented JSON has newlines and spaces
        assert "\n" in raw
        assert "  " in raw


class TestFormatPlain:
    """Tests for format_plain output."""

    def test_no_issues(self) -> None:
        """Empty issues -> 'no issues found' message."""
        reporter = ValidationReporter()
        result = reporter.format_plain([])
        assert "no issues found" in result.lower()

    def test_no_issues_mentions_passed(self) -> None:
        """Empty issues -> message contains 'passed'."""
        reporter = ValidationReporter()
        result = reporter.format_plain([])
        assert "passed" in result.lower()

    def test_with_line_numbers(self) -> None:
        """Issues with line numbers include (line N) in output."""
        reporter = ValidationReporter()
        issues = [_make_issue(line=42)]
        result = reporter.format_plain(issues)
        assert "(line 42)" in result

    def test_without_line_numbers(self) -> None:
        """Issues without line numbers omit (line N)."""
        reporter = ValidationReporter()
        issues = [_make_issue()]
        result = reporter.format_plain(issues)
        assert "(line" not in result

    def test_suggestion_in_plain(self) -> None:
        """Suggestions are included in plain text output."""
        reporter = ValidationReporter()
        issues = [_make_issue(suggestion="Try this")]
        result = reporter.format_plain(issues)
        assert "Suggestion: Try this" in result

    def test_no_suggestion_omitted(self) -> None:
        """No suggestion -> no 'Suggestion:' line."""
        reporter = ValidationReporter()
        issues = [_make_issue(suggestion=None)]
        result = reporter.format_plain(issues)
        assert "Suggestion:" not in result

    def test_check_id_in_plain(self) -> None:
        """Check ID appears in plain output."""
        reporter = ValidationReporter()
        issues = [_make_issue(check_id="V007")]
        result = reporter.format_plain(issues)
        assert "V007" in result

    def test_severity_uppercase_in_plain(self) -> None:
        """Severity appears in uppercase brackets."""
        reporter = ValidationReporter()
        for sev in ValidationSeverity:
            issues = [_make_issue(severity=sev)]
            result = reporter.format_plain(issues)
            assert f"[{sev.value.upper()}]" in result

    def test_summary_line_with_errors_failed(self) -> None:
        """Errors present -> FAILED in summary."""
        reporter = ValidationReporter()
        issues = [
            _make_issue(severity=ValidationSeverity.ERROR),
            _make_issue(severity=ValidationSeverity.WARNING),
        ]
        result = reporter.format_plain(issues)
        assert "1 errors" in result
        assert "1 warnings" in result
        assert "FAILED" in result

    def test_summary_line_no_errors_passed(self) -> None:
        """No errors -> PASSED."""
        reporter = ValidationReporter()
        issues = [_make_issue(severity=ValidationSeverity.WARNING)]
        result = reporter.format_plain(issues)
        assert "PASSED" in result
        assert "FAILED" not in result

    def test_summary_counts_all_severities(self) -> None:
        """Summary includes counts for all three severity levels."""
        reporter = ValidationReporter()
        issues = [
            _make_issue(severity=ValidationSeverity.ERROR, message="e1"),
            _make_issue(severity=ValidationSeverity.ERROR, message="e2"),
            _make_issue(severity=ValidationSeverity.WARNING, message="w1"),
            _make_issue(severity=ValidationSeverity.INFO, message="i1"),
            _make_issue(severity=ValidationSeverity.INFO, message="i2"),
            _make_issue(severity=ValidationSeverity.INFO, message="i3"),
        ]
        result = reporter.format_plain(issues)
        assert "2 errors" in result
        assert "1 warnings" in result
        assert "3 info" in result

    def test_message_in_plain(self) -> None:
        """Issue message appears in plain output."""
        reporter = ValidationReporter()
        issues = [_make_issue(message="Jinja syntax error in template")]
        result = reporter.format_plain(issues)
        assert "Jinja syntax error in template" in result

    def test_format_plain_returns_string(self) -> None:
        """format_plain always returns a string."""
        reporter = ValidationReporter()
        assert isinstance(reporter.format_plain([]), str)
        assert isinstance(reporter.format_plain([_make_issue()]), str)
