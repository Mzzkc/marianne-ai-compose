"""Comprehensive tests for mozart.execution.validation.engine.ValidationEngine.

Covers initialization, path expansion, display paths, file reading with
encoding fallback, missing field results, validate_sheet (run_validations),
and all individual validation types: file_exists, file_modified,
content_contains, content_regex, and command_succeeds.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from marianne.core.config import ValidationRule
from marianne.execution.validation.engine import ValidationEngine
from marianne.execution.validation.models import (
    FileModificationTracker,
    SheetValidationResult,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(
    workspace: Path,
    sheet_context: dict[str, Any] | None = None,
) -> ValidationEngine:
    """Create a ValidationEngine with default sheet context."""
    ctx = sheet_context or {"sheet_num": 1, "start_item": 1, "end_item": 10}
    return ValidationEngine(workspace=workspace, sheet_context=ctx)


def _rule_no_retry(**kwargs: Any) -> ValidationRule:
    """Create a ValidationRule with retry disabled for faster tests."""
    kwargs.setdefault("retry_count", 0)
    return ValidationRule(**kwargs)


# ===========================================================================
# 1. __init__
# ===========================================================================


class TestInit:
    """Tests for ValidationEngine.__init__."""

    def test_workspace_resolved(self, tmp_path: Path) -> None:
        """Workspace is stored as an absolute resolved path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Pass a relative-style path by adding a '.' component
        non_canonical = workspace / "." / "subdir" / ".."
        engine = _make_engine(non_canonical)
        assert engine.workspace == workspace.resolve()

    def test_sheet_context_stored(self, tmp_path: Path) -> None:
        """Sheet context dict is accessible on the engine."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        ctx = {"sheet_num": 5, "start_item": 41, "end_item": 50}
        engine = ValidationEngine(workspace=workspace, sheet_context=ctx)
        assert engine.sheet_context is ctx
        assert engine.sheet_context["sheet_num"] == 5

    def test_mtime_tracker_created(self, tmp_path: Path) -> None:
        """Engine creates a FileModificationTracker on init."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        engine = _make_engine(workspace)
        assert isinstance(engine._mtime_tracker, FileModificationTracker)


# ===========================================================================
# 2. expand_path()
# ===========================================================================


class TestExpandPath:
    """Tests for path template expansion and traversal blocking."""

    def test_expands_sheet_num(self, temp_workspace: Path) -> None:
        """Template {sheet_num} is expanded correctly."""
        engine = _make_engine(temp_workspace, {"sheet_num": 3})
        result = engine.expand_path("{workspace}/output-{sheet_num}.txt")
        assert result == Path(str(temp_workspace) + "/output-3.txt")

    def test_expands_start_end_item(self, temp_workspace: Path) -> None:
        """Templates {start_item} and {end_item} expand."""
        ctx = {"sheet_num": 1, "start_item": 10, "end_item": 20}
        engine = _make_engine(temp_workspace, ctx)
        result = engine.expand_path("{workspace}/items-{start_item}-{end_item}.txt")
        expected = Path(str(temp_workspace) + "/items-10-20.txt")
        assert result == expected

    def test_expands_workspace(self, temp_workspace: Path) -> None:
        """{workspace} is injected automatically from self.workspace."""
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        result = engine.expand_path("{workspace}/file.txt")
        assert result == Path(str(temp_workspace) + "/file.txt")

    def test_path_outside_workspace_allowed(self, temp_workspace: Path) -> None:
        """Absolute paths outside workspace are allowed (agents work in working_directory)."""
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        result = engine.expand_path("/etc/passwd")
        assert result == Path("/etc/passwd")

    def test_path_traversal_resolves(self, temp_workspace: Path) -> None:
        """Paths with traversal components resolve correctly."""
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        result = engine.expand_path("{workspace}/../sibling.txt")
        assert result == temp_workspace.parent / "sibling.txt"

    def test_path_with_traversal_resolves_correctly(self, temp_workspace: Path) -> None:
        """Paths with '..' resolve to their canonical location."""
        subdir = temp_workspace / "a" / "b"
        subdir.mkdir(parents=True)
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        # Goes up from a/b back to workspace root — resolves to workspace/file.txt
        result = engine.expand_path("{workspace}/a/b/../../file.txt")
        assert result == (temp_workspace / "file.txt").resolve()


# ===========================================================================
# 3. _display_path()
# ===========================================================================


class TestDisplayPath:
    """Tests for short path display."""

    def test_short_path_unchanged(self) -> None:
        """Short paths are returned as-is."""
        path = Path("/home/user/file.txt")
        assert ValidationEngine._display_path(path) == str(path)

    def test_long_path_shows_name_only(self) -> None:
        """Paths longer than 50 characters show only the filename."""
        path = Path("/a" * 30 + "/really_long_deep_nested_file.txt")
        assert len(str(path)) > 50
        assert ValidationEngine._display_path(path) == path.name

    def test_exactly_50_chars_unchanged(self) -> None:
        """Path of exactly 50 characters is returned as-is (not truncated)."""
        # Build a path that is exactly 50 chars
        base = "/tmp/" + "x" * 41 + ".txt"  # /tmp/ = 5 + 41 + 4 = 50
        path = Path(base)
        assert len(str(path)) == 50
        assert ValidationEngine._display_path(path) == str(path)


# ===========================================================================
# 4. _read_file_text()
# ===========================================================================


class TestReadFileText:
    """Tests for file reading with encoding fallback."""

    def test_reads_utf8(self, tmp_path: Path) -> None:
        """Reads standard UTF-8 text."""
        f = tmp_path / "utf8.txt"
        f.write_text("Hello, world!", encoding="utf-8")
        assert ValidationEngine._read_file_text(f) == "Hello, world!"

    def test_reads_utf8_with_unicode(self, tmp_path: Path) -> None:
        """Reads UTF-8 text containing non-ASCII characters."""
        f = tmp_path / "unicode.txt"
        content = "Cafe\u0301 \u2603 \u2764"
        f.write_text(content, encoding="utf-8")
        assert ValidationEngine._read_file_text(f) == content

    def test_encoding_fallback_on_invalid_bytes(self, tmp_path: Path) -> None:
        """Falls back to replacement chars on invalid UTF-8 bytes."""
        f = tmp_path / "binary.txt"
        # Write bytes that are not valid UTF-8
        f.write_bytes(b"Hello \xff\xfe World")
        with pytest.warns(UnicodeWarning, match="encoding issues"):
            text = ValidationEngine._read_file_text(f)
        assert "Hello" in text
        assert "World" in text
        # The invalid bytes should be replaced with the replacement character
        assert "\ufffd" in text


# ===========================================================================
# 5. _missing_field_result()
# ===========================================================================


class TestMissingFieldResult:
    """Tests for missing field error results."""

    def test_returns_failed_result(self) -> None:
        """Missing field produces a failed ValidationResult."""
        rule = _rule_no_retry(type="command_succeeds", command="echo ok")
        result = ValidationEngine._missing_field_result(rule, "path")
        assert result.passed is False
        assert result.failure_category == "error"

    def test_error_message_references_type_and_field(self) -> None:
        """Error message includes the rule type and missing field name."""
        rule = _rule_no_retry(type="command_succeeds", command="echo ok")
        result = ValidationEngine._missing_field_result(rule, "path")
        assert "command_succeeds" in result.error_message
        assert "'path'" in result.error_message

    def test_suggested_fix_present(self) -> None:
        """Suggested fix tells user to add the missing field."""
        rule = _rule_no_retry(type="command_succeeds", command="echo ok")
        result = ValidationEngine._missing_field_result(rule, "command")
        assert "Add 'command'" in result.suggested_fix

    def test_failure_reason_present(self) -> None:
        """Failure reason mentions the missing field."""
        rule = _rule_no_retry(type="command_succeeds", command="echo ok")
        result = ValidationEngine._missing_field_result(rule, "pattern")
        assert "'pattern'" in result.failure_reason


# ===========================================================================
# 6. validate_sheet() — run_validations()
# ===========================================================================


class TestRunValidations:
    """Tests for running all validations for a sheet."""

    async def test_empty_rules_returns_empty_result(
        self, temp_workspace: Path,
    ) -> None:
        """No rules returns an empty SheetValidationResult."""
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([])
        assert isinstance(result, SheetValidationResult)
        assert result.all_passed is True
        assert len(result.results) == 0

    async def test_multiple_rules_all_pass(self, temp_workspace: Path) -> None:
        """Multiple passing rules produce all_passed=True."""
        (temp_workspace / "a.txt").write_text("hello")
        (temp_workspace / "b.txt").write_text("world")
        rules = [
            _rule_no_retry(
                type="file_exists",
                path=str(temp_workspace / "a.txt"),
            ),
            _rule_no_retry(
                type="file_exists",
                path=str(temp_workspace / "b.txt"),
            ),
        ]
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations(rules)
        assert result.all_passed is True
        assert len(result.results) == 2

    async def test_mixed_pass_fail(self, temp_workspace: Path) -> None:
        """Mix of passing and failing rules."""
        (temp_workspace / "exists.txt").write_text("content")
        rules = [
            _rule_no_retry(
                type="file_exists",
                path=str(temp_workspace / "exists.txt"),
            ),
            _rule_no_retry(
                type="file_exists",
                path=str(temp_workspace / "missing.txt"),
            ),
        ]
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations(rules)
        assert result.all_passed is False
        assert result.passed_count == 1
        assert result.failed_count == 1

    async def test_rules_checked_count(self, temp_workspace: Path) -> None:
        """rules_checked reflects how many rules were actually evaluated."""
        (temp_workspace / "f.txt").write_text("ok")
        rules = [
            _rule_no_retry(
                type="file_exists",
                path=str(temp_workspace / "f.txt"),
            ),
        ]
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations(rules)
        assert result.rules_checked == 1

    async def test_condition_filters_rules(self, temp_workspace: Path) -> None:
        """Rules with unmet conditions are not executed."""
        (temp_workspace / "f.txt").write_text("ok")
        rules = [
            _rule_no_retry(
                type="file_exists",
                path=str(temp_workspace / "f.txt"),
                condition="sheet_num >= 5",  # current sheet_num is 1
            ),
        ]
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        result = await engine.run_validations(rules)
        assert len(result.results) == 0
        assert result.rules_checked == 0

    async def test_condition_met_executes_rule(self, temp_workspace: Path) -> None:
        """Rules with met conditions are executed."""
        (temp_workspace / "f.txt").write_text("ok")
        rules = [
            _rule_no_retry(
                type="file_exists",
                path=str(temp_workspace / "f.txt"),
                condition="sheet_num >= 1",  # current sheet_num is 1
            ),
        ]
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        result = await engine.run_validations(rules)
        assert len(result.results) == 1
        assert result.results[0].passed is True

    async def test_sheet_num_in_result(self, temp_workspace: Path) -> None:
        """SheetValidationResult captures the correct sheet_num."""
        engine = _make_engine(temp_workspace, {"sheet_num": 7})
        result = await engine.run_validations([])
        assert result.sheet_num == 7

    async def test_unknown_validation_type(self, temp_workspace: Path) -> None:
        """Unknown validation type produces a failed result."""
        # Bypass Pydantic validation to create a rule with bogus type
        rule = ValidationRule.model_construct(
            type="bogus_type",
            path=None,
            pattern=None,
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "Unknown validation type" in result.results[0].error_message


# ===========================================================================
# 7a. file_exists validation
# ===========================================================================


class TestFileExistsValidation:
    """Tests for file_exists validation type."""

    async def test_file_exists_pass(self, temp_workspace: Path) -> None:
        """Passes when the file exists and is a regular file."""
        f = temp_workspace / "output.txt"
        f.write_text("data")
        rule = _rule_no_retry(type="file_exists", path=str(f))
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is True
        assert result.results[0].actual_value == str(f)

    async def test_file_exists_fail_missing(self, temp_workspace: Path) -> None:
        """Fails when the file does not exist."""
        rule = _rule_no_retry(
            type="file_exists",
            path=str(temp_workspace / "nope.txt"),
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert result.results[0].failure_category == "missing"
        assert "File not found" in result.results[0].error_message

    async def test_file_exists_fail_directory(self, temp_workspace: Path) -> None:
        """Fails when the path is a directory, not a file."""
        d = temp_workspace / "subdir"
        d.mkdir()
        rule = _rule_no_retry(type="file_exists", path=str(d))
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False

    async def test_file_exists_template_expansion(
        self, temp_workspace: Path,
    ) -> None:
        """Path templates are expanded before checking."""
        f = temp_workspace / "sheet-2-out.txt"
        f.write_text("done")
        rule = _rule_no_retry(
            type="file_exists",
            path="{workspace}/sheet-{sheet_num}-out.txt",
        )
        engine = _make_engine(temp_workspace, {"sheet_num": 2})
        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_file_exists_missing_path_field(
        self, temp_workspace: Path,
    ) -> None:
        """Missing path field returns a missing-field error."""
        rule = ValidationRule.model_construct(
            type="file_exists",
            path=None,
            pattern=None,
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "'path'" in result.results[0].error_message

    async def test_file_exists_suggested_fix(self, temp_workspace: Path) -> None:
        """Failed file_exists provides a suggested fix."""
        path = temp_workspace / "missing.txt"
        rule = _rule_no_retry(type="file_exists", path=str(path))
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.suggested_fix is not None
        assert "Create file at" in r.suggested_fix


# ===========================================================================
# 7b. file_modified validation
# ===========================================================================


class TestFileModifiedValidation:
    """Tests for file_modified validation type."""

    async def test_file_modified_pass(self, temp_workspace: Path) -> None:
        """Passes when the file mtime changes after snapshot."""
        f = temp_workspace / "tracked.txt"
        f.write_text("initial")

        rule = _rule_no_retry(type="file_modified", path=str(f))
        engine = _make_engine(temp_workspace)

        # Snapshot before modification
        engine.snapshot_mtime_files([rule])

        # Simulate modification (ensure mtime advances)
        time.sleep(0.05)
        f.write_text("modified content")

        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_file_modified_fail_not_modified(
        self, temp_workspace: Path,
    ) -> None:
        """Fails when the file mtime has not changed."""
        f = temp_workspace / "stale.txt"
        f.write_text("content")

        rule = _rule_no_retry(type="file_modified", path=str(f))
        engine = _make_engine(temp_workspace)
        engine.snapshot_mtime_files([rule])

        # Do NOT modify the file
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert result.results[0].failure_category == "stale"

    async def test_file_modified_fail_file_missing(
        self, temp_workspace: Path,
    ) -> None:
        """Fails with 'missing' category if file does not exist at check time."""
        path = temp_workspace / "ghost.txt"
        rule = ValidationRule.model_construct(
            type="file_modified",
            path=str(path),
            pattern=None,
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert result.results[0].failure_category == "missing"

    async def test_file_modified_new_file_after_snapshot(
        self, temp_workspace: Path,
    ) -> None:
        """A file created after snapshot (mtime > 0.0 snapshot) passes."""
        path = temp_workspace / "new_file.txt"

        rule = _rule_no_retry(type="file_modified", path=str(path))
        engine = _make_engine(temp_workspace)

        # Snapshot when file does not exist (records mtime as 0.0)
        engine.snapshot_mtime_files([rule])

        # Now create the file
        path.write_text("newly created")

        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_file_modified_missing_path_field(
        self, temp_workspace: Path,
    ) -> None:
        """Missing path field returns a missing-field error."""
        rule = ValidationRule.model_construct(
            type="file_modified",
            path=None,
            pattern=None,
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "'path'" in result.results[0].error_message


# ===========================================================================
# 7c. content_contains validation
# ===========================================================================


class TestContentContainsValidation:
    """Tests for content_contains validation type."""

    async def test_content_contains_pass(self, temp_workspace: Path) -> None:
        """Passes when the file contains the pattern."""
        f = temp_workspace / "log.txt"
        f.write_text("Operation completed: SUCCESS marker here")
        rule = _rule_no_retry(
            type="content_contains", path=str(f), pattern="SUCCESS",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_content_contains_fail(self, temp_workspace: Path) -> None:
        """Fails when the file does not contain the pattern."""
        f = temp_workspace / "log.txt"
        f.write_text("Operation failed: ERROR")
        rule = _rule_no_retry(
            type="content_contains", path=str(f), pattern="SUCCESS",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.passed is False
        assert r.failure_category == "incomplete"
        assert r.suggested_fix is not None

    async def test_content_contains_file_missing(
        self, temp_workspace: Path,
    ) -> None:
        """Fails with 'missing' category when the file does not exist."""
        rule = _rule_no_retry(
            type="content_contains",
            path=str(temp_workspace / "nope.txt"),
            pattern="X",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert result.results[0].failure_category == "missing"

    async def test_content_contains_case_sensitive(
        self, temp_workspace: Path,
    ) -> None:
        """Pattern matching is case-sensitive."""
        f = temp_workspace / "case.txt"
        f.write_text("success")
        rule = _rule_no_retry(
            type="content_contains", path=str(f), pattern="SUCCESS",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False

    async def test_content_contains_multiline(
        self, temp_workspace: Path,
    ) -> None:
        """Pattern is found in multiline content."""
        f = temp_workspace / "multi.txt"
        f.write_text("line 1\nline 2\nTARGET\nline 4\n")
        rule = _rule_no_retry(
            type="content_contains", path=str(f), pattern="TARGET",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_content_contains_missing_pattern_field(
        self, temp_workspace: Path,
    ) -> None:
        """Missing pattern field produces a missing-field error."""
        f = temp_workspace / "file.txt"
        f.write_text("content")
        rule = ValidationRule.model_construct(
            type="content_contains",
            path=str(f),
            pattern=None,
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "'pattern'" in result.results[0].error_message

    async def test_content_contains_missing_path_field(
        self, temp_workspace: Path,
    ) -> None:
        """Missing path field produces a missing-field error."""
        rule = ValidationRule.model_construct(
            type="content_contains",
            path=None,
            pattern="X",
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "'path'" in result.results[0].error_message

    async def test_content_contains_long_pattern_truncated_in_failure(
        self, temp_workspace: Path,
    ) -> None:
        """Long patterns are truncated in the failure reason display."""
        f = temp_workspace / "file.txt"
        f.write_text("short")
        long_pattern = "A" * 100
        rule = _rule_no_retry(
            type="content_contains", path=str(f), pattern=long_pattern,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.passed is False
        # The failure_reason should contain a truncated version
        assert "..." in r.failure_reason


# ===========================================================================
# 7d. content_regex validation
# ===========================================================================


class TestContentRegexValidation:
    """Tests for content_regex validation type."""

    async def test_content_regex_pass(self, temp_workspace: Path) -> None:
        """Passes when regex matches content."""
        f = temp_workspace / "log.txt"
        f.write_text("Version: 2.3.1")
        rule = _rule_no_retry(
            type="content_regex",
            path=str(f),
            pattern=r"Version:\s+\d+\.\d+\.\d+",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.passed is True
        assert r.actual_value == "Version: 2.3.1"

    async def test_content_regex_fail_no_match(
        self, temp_workspace: Path,
    ) -> None:
        """Fails when regex does not match."""
        f = temp_workspace / "log.txt"
        f.write_text("No version info here")
        rule = _rule_no_retry(
            type="content_regex",
            path=str(f),
            pattern=r"Version:\s+\d+\.\d+",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert result.results[0].failure_category == "malformed"

    async def test_content_regex_invalid_pattern(
        self, temp_workspace: Path,
    ) -> None:
        """Invalid regex pattern returns a clear error."""
        f = temp_workspace / "file.txt"
        f.write_text("content")
        # Construct rule bypassing Pydantic regex validation
        rule = ValidationRule.model_construct(
            type="content_regex",
            path=str(f),
            pattern="[invalid(",
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.passed is False
        assert r.failure_category == "error"
        assert "Invalid regex" in r.error_message

    async def test_content_regex_file_missing(
        self, temp_workspace: Path,
    ) -> None:
        """Fails with 'missing' when file does not exist."""
        rule = _rule_no_retry(
            type="content_regex",
            path=str(temp_workspace / "nope.txt"),
            pattern=r"\d+",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert result.results[0].failure_category == "missing"

    async def test_content_regex_captures_match_group(
        self, temp_workspace: Path,
    ) -> None:
        """actual_value contains the matched group(0)."""
        f = temp_workspace / "nums.txt"
        f.write_text("count=42 and more")
        rule = _rule_no_retry(
            type="content_regex",
            path=str(f),
            pattern=r"count=\d+",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].actual_value == "count=42"

    async def test_content_regex_missing_path_field(
        self, temp_workspace: Path,
    ) -> None:
        """Missing path field produces a missing-field error."""
        rule = ValidationRule.model_construct(
            type="content_regex",
            path=None,
            pattern=r"\d+",
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "'path'" in result.results[0].error_message

    async def test_content_regex_missing_pattern_field(
        self, temp_workspace: Path,
    ) -> None:
        """Missing pattern field produces a missing-field error."""
        f = temp_workspace / "file.txt"
        f.write_text("content")
        rule = ValidationRule.model_construct(
            type="content_regex",
            path=str(f),
            pattern=None,
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "'pattern'" in result.results[0].error_message


# ===========================================================================
# 7e. command_succeeds validation (mock subprocess)
# ===========================================================================


class TestCommandSucceedsValidation:
    """Tests for command_succeeds validation type."""

    async def test_command_succeeds_pass(self, temp_workspace: Path) -> None:
        """Passes when command exits with 0."""
        rule = _rule_no_retry(
            type="command_succeeds", command="echo hello",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.passed is True
        assert r.actual_value == "exit_code=0"
        assert r.expected_value == "exit_code=0"

    async def test_command_succeeds_fail(self, temp_workspace: Path) -> None:
        """Fails when command exits with non-zero."""
        rule = _rule_no_retry(
            type="command_succeeds", command="exit 1",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.passed is False
        assert r.actual_value == "exit_code=1"
        assert r.failure_category == "error"

    async def test_command_succeeds_working_directory(
        self, temp_workspace: Path,
    ) -> None:
        """Command runs in the specified working directory."""
        subdir = temp_workspace / "sub"
        subdir.mkdir()
        (subdir / "marker.txt").write_text("found")
        rule = _rule_no_retry(
            type="command_succeeds",
            command="cat marker.txt",
            working_directory=str(subdir),
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_command_succeeds_default_cwd_is_workspace(
        self, temp_workspace: Path,
    ) -> None:
        """Without working_directory, command runs in workspace."""
        (temp_workspace / "ws_marker.txt").write_text("here")
        rule = _rule_no_retry(
            type="command_succeeds",
            command="cat ws_marker.txt",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_command_template_expansion(
        self, temp_workspace: Path,
    ) -> None:
        """Command template variables are expanded and shell-quoted."""
        rule = _rule_no_retry(
            type="command_succeeds",
            command="echo {sheet_num}",
        )
        engine = _make_engine(temp_workspace, {"sheet_num": 5})
        result = await engine.run_validations([rule])
        assert result.results[0].passed is True

    async def test_command_timeout_mock(self, temp_workspace: Path) -> None:
        """Timeout produces a clear failure result (mocked for speed)."""
        rule = _rule_no_retry(
            type="command_succeeds", command="sleep 999",
        )
        engine = _make_engine(temp_workspace)

        # Mock create_subprocess_exec to simulate a timeout
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await engine.run_validations([rule])

        r = result.results[0]
        assert r.passed is False
        assert "timed out" in r.error_message

    async def test_command_missing_command_field(
        self, temp_workspace: Path,
    ) -> None:
        """Missing command field returns a missing-field error."""
        rule = ValidationRule.model_construct(
            type="command_succeeds",
            path=None,
            pattern=None,
            command=None,
            description=None,
            working_directory=None,
            stage=1,
            condition=None,
            retry_count=0,
            retry_delay_ms=0,
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].passed is False
        assert "'command'" in result.results[0].error_message

    async def test_command_execution_error(self, temp_workspace: Path) -> None:
        """Subprocess execution error is caught and reported."""
        rule = _rule_no_retry(
            type="command_succeeds", command="echo test",
        )
        engine = _make_engine(temp_workspace)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("/bin/sh not found"),
        ):
            result = await engine.run_validations([rule])

        r = result.results[0]
        assert r.passed is False
        assert "Command execution error" in r.error_message
        assert r.error_type == "internal_error"

    async def test_command_stderr_captured(self, temp_workspace: Path) -> None:
        """stderr output is included in the failure message."""
        rule = _rule_no_retry(
            type="command_succeeds",
            command="echo 'error details' >&2; exit 1",
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.passed is False
        assert "error details" in r.error_message

    async def test_command_confidence_on_success(
        self, temp_workspace: Path,
    ) -> None:
        """Successful command has confidence=1.0."""
        rule = _rule_no_retry(type="command_succeeds", command="true")
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.confidence == 1.0
        assert r.confidence_factors == {"exit_code": 1.0}

    async def test_command_confidence_on_failure(
        self, temp_workspace: Path,
    ) -> None:
        """Failed command has lower confidence."""
        rule = _rule_no_retry(type="command_succeeds", command="false")
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        r = result.results[0]
        assert r.confidence == 0.8
        assert r.confidence_factors == {"exit_code": 0.5}

    async def test_high_risk_command_warning(
        self, temp_workspace: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """High-risk command patterns trigger a warning log."""
        rule = _rule_no_retry(
            type="command_succeeds",
            command="sudo echo hi",
        )
        engine = _make_engine(temp_workspace)
        with caplog.at_level("WARNING", logger="marianne.execution.validation"):
            # The command will fail (sudo without tty) but we're testing the warning
            await engine.run_validations([rule])
        assert any("high-risk" in r.message.lower() for r in caplog.records)


# ===========================================================================
# 8. Condition checking
# ===========================================================================


class TestConditionChecking:
    """Tests for _check_condition and conditional rule filtering."""

    def test_none_condition_always_true(self, temp_workspace: Path) -> None:
        """None condition means rule always applies."""
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        assert engine._check_condition(None) is True

    def test_simple_ge_condition(self, temp_workspace: Path) -> None:
        """Greater-than-or-equal condition works."""
        engine = _make_engine(temp_workspace, {"sheet_num": 5})
        assert engine._check_condition("sheet_num >= 5") is True
        assert engine._check_condition("sheet_num >= 6") is False

    def test_simple_eq_condition(self, temp_workspace: Path) -> None:
        """Equality condition works."""
        engine = _make_engine(temp_workspace, {"sheet_num": 3})
        assert engine._check_condition("sheet_num == 3") is True
        assert engine._check_condition("sheet_num == 4") is False

    def test_compound_and_condition(self, temp_workspace: Path) -> None:
        """Compound 'and' conditions work."""
        engine = _make_engine(temp_workspace, {"sheet_num": 5})
        assert engine._check_condition("sheet_num >= 3 and sheet_num <= 7") is True
        assert engine._check_condition("sheet_num >= 3 and sheet_num <= 4") is False

    def test_invalid_condition_returns_true(self, temp_workspace: Path) -> None:
        """Unparseable conditions default to True (rule applies)."""
        engine = _make_engine(temp_workspace, {"sheet_num": 1})
        assert engine._check_condition("not_a_valid_condition") is True

    def test_ne_condition(self, temp_workspace: Path) -> None:
        """Not-equal condition works."""
        engine = _make_engine(temp_workspace, {"sheet_num": 3})
        assert engine._check_condition("sheet_num != 5") is True
        assert engine._check_condition("sheet_num != 3") is False

    def test_lt_condition(self, temp_workspace: Path) -> None:
        """Less-than condition works."""
        engine = _make_engine(temp_workspace, {"sheet_num": 2})
        assert engine._check_condition("sheet_num < 3") is True
        assert engine._check_condition("sheet_num < 2") is False


# ===========================================================================
# 9. snapshot_mtime_files
# ===========================================================================


class TestSnapshotMtimeFiles:
    """Tests for mtime snapshotting of file_modified rules."""

    def test_snapshots_file_modified_rules_only(
        self, temp_workspace: Path,
    ) -> None:
        """Only file_modified rules trigger mtime snapshot."""
        f = temp_workspace / "tracked.txt"
        f.write_text("initial")
        rules = [
            _rule_no_retry(type="file_exists", path=str(f)),
            _rule_no_retry(type="file_modified", path=str(f)),
        ]
        engine = _make_engine(temp_workspace)
        engine.snapshot_mtime_files(rules)
        # The tracker should have an entry for the tracked file
        original = engine._mtime_tracker.get_original_mtime(f)
        assert original is not None
        assert original > 0

    def test_snapshot_missing_file_records_zero(
        self, temp_workspace: Path,
    ) -> None:
        """Non-existent files get mtime 0.0 in snapshot."""
        path = temp_workspace / "missing.txt"
        rules = [
            _rule_no_retry(type="file_modified", path=str(path)),
        ]
        engine = _make_engine(temp_workspace)
        engine.snapshot_mtime_files(rules)
        assert engine._mtime_tracker.get_original_mtime(path) == 0.0


# ===========================================================================
# 10. check_duration_ms tracking
# ===========================================================================


class TestCheckDuration:
    """Tests that check_duration_ms is populated."""

    async def test_duration_populated_on_pass(
        self, temp_workspace: Path,
    ) -> None:
        """check_duration_ms is set on passing validation."""
        f = temp_workspace / "f.txt"
        f.write_text("ok")
        rule = _rule_no_retry(type="file_exists", path=str(f))
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].check_duration_ms >= 0

    async def test_duration_populated_on_fail(
        self, temp_workspace: Path,
    ) -> None:
        """check_duration_ms is set on failing validation."""
        rule = _rule_no_retry(
            type="file_exists",
            path=str(temp_workspace / "nope.txt"),
        )
        engine = _make_engine(temp_workspace)
        result = await engine.run_validations([rule])
        assert result.results[0].check_duration_ms >= 0


# ===========================================================================
# 11. Exception handling in _run_single_validation
# ===========================================================================


class TestExceptionHandling:
    """Tests for exception handling during validation dispatch."""

    async def test_os_error_caught(self, temp_workspace: Path) -> None:
        """OSError during validation is caught and classified."""
        rule = _rule_no_retry(type="file_exists", path=str(temp_workspace / "f.txt"))
        engine = _make_engine(temp_workspace)

        with patch.object(
            engine, "_check_file_exists", side_effect=OSError("disk error"),
        ):
            result = await engine.run_validations([rule])

        r = result.results[0]
        assert r.passed is False
        assert r.error_type == "io_error"
        assert "I/O error" in r.error_message

    async def test_generic_exception_caught(self, temp_workspace: Path) -> None:
        """Unexpected exceptions are caught as internal_error."""
        rule = _rule_no_retry(type="file_exists", path=str(temp_workspace / "f.txt"))
        engine = _make_engine(temp_workspace)

        with patch.object(
            engine, "_check_file_exists", side_effect=RuntimeError("unexpected"),
        ):
            result = await engine.run_validations([rule])

        r = result.results[0]
        assert r.passed is False
        assert r.error_type == "internal_error"
        assert "Validation error" in r.error_message


# ===========================================================================
# 12. Bug #105 — prompt.variables available in validation paths/commands
# ===========================================================================


class TestPromptVariablesInValidation105:
    """Tests that prompt.variables are merged into validation context (Bug #105).

    All tests use the merged-context pattern from sheet.py:395-396:
        user_vars = {str(k): v for k, v in config.prompt.variables.items()}
        validation_context = {**user_vars, **sheet_context.to_dict()}

    Tests A–B verify the engine correctly expands user vars when they are
    present in the context.  Tests C–E are regression gates.
    """

    def _make_engine_with_user_vars(
        self,
        workspace: Path,
        user_vars: dict,
        base_context: dict | None = None,
    ) -> "ValidationEngine":
        """Simulate the sheet.py construction site merge pattern."""
        if base_context is None:
            base_context = {"sheet_num": 1, "workspace": str(workspace)}
        coerced = {str(k): v for k, v in user_vars.items()}
        merged = {**coerced, **base_context}
        return ValidationEngine(workspace=workspace, sheet_context=merged)

    def test_path_rule_expands_user_variable(self, tmp_path: Path) -> None:
        """Path template with a user var expands correctly (TEST-105-A).

        Simulates: config.prompt.variables = {"my_output_dir": "analysis_results"}
        path rule:  "{workspace}/{my_output_dir}/report.txt"
        """
        engine = self._make_engine_with_user_vars(
            tmp_path,
            user_vars={"my_output_dir": "analysis_results"},
        )
        # Key must be present in engine.sheet_context
        assert "my_output_dir" in engine.sheet_context
        assert engine.sheet_context["my_output_dir"] == "analysis_results"

        # Expansion must succeed without KeyError
        result = engine.expand_path("{workspace}/{my_output_dir}/report.txt")
        assert "analysis_results" in result.parts

    def test_command_rule_expands_user_variable(self, tmp_path: Path) -> None:
        """Command rule with user var replaces token — no silent residue (TEST-105-B).

        Simulates: config.prompt.variables = {"my_var": "production"}
        command rule: "check_env.sh {my_var}"
        """
        engine = self._make_engine_with_user_vars(
            tmp_path,
            user_vars={"my_var": "production"},
        )
        assert "my_var" in engine.sheet_context

        # Manually simulate _run_command_rule expansion loop
        import shlex
        context = dict(engine.sheet_context)
        context["workspace"] = str(engine.workspace)
        command = "check_env.sh {my_var}"
        for key, value in context.items():
            command = command.replace("{" + key + "}", shlex.quote(str(value)))

        # The literal token must NOT remain (silent failure gate)
        assert "{my_var}" not in command
        assert "production" in command

    def test_builtin_wins_over_user_variable_on_collision(
        self, tmp_path: Path
    ) -> None:
        """Built-in workspace variable wins over user-defined one (TEST-105-C).

        Merge order: {**user_vars, **sheet_context.to_dict()}
        If user defines workspace=/user/custom, built-in workspace must win.
        """
        builtin_workspace = str(tmp_path)
        user_workspace = "/user/custom/path"

        coerced = {"workspace": user_workspace}
        base = {"sheet_num": 1, "workspace": builtin_workspace}
        merged = {**coerced, **base}  # built-in wins

        engine = ValidationEngine(workspace=tmp_path, sheet_context=merged)
        result = engine.expand_path("{workspace}/output.txt")
        assert str(tmp_path) in str(result)
        assert user_workspace not in str(result)

    def test_missing_variable_still_raises_key_error(self, tmp_path: Path) -> None:
        """Undefined variable in path template raises KeyError (TEST-105-D).

        The fix must NOT swallow expansion errors for genuinely missing vars.
        """
        # Regression test for #105 — missing vars must still fail loudly
        engine = _make_engine(tmp_path, {"sheet_num": 1})
        with pytest.raises(KeyError):
            engine.expand_path("{workspace}/output-{missing_var}.txt")

    def test_integer_yaml_keys_coerced_to_str(self, tmp_path: Path) -> None:
        """YAML integer keys (e.g., variables: {1: foo}) are coerced to str."""
        # str() coercion is mandatory: str.format() requires string keys
        user_vars = {1: "sheet_one_content"}  # integer key from YAML
        engine = self._make_engine_with_user_vars(tmp_path, user_vars=user_vars)

        # Key accessible as string "1", not integer 1
        assert "1" in engine.sheet_context
        assert engine.sheet_context["1"] == "sheet_one_content"
