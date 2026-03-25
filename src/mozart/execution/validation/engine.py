"""Validation engine — executes validation rules against sheet outputs.

Dispatches to type-specific check methods: file_exists, file_modified,
content_contains, content_regex, and command_succeeds.
"""

from __future__ import annotations

import asyncio
import logging
import operator
import re
import shlex
import time
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

from mozart.core.config import ValidationRule
from mozart.core.constants import (
    VALIDATION_COMMAND_TIMEOUT_SECONDS,
    VALIDATION_OUTPUT_TRUNCATE_CHARS,
)

from .models import (
    FileModificationTracker,
    SheetValidationResult,
    ValidationResult,
)

_logger = logging.getLogger("mozart.execution.validation")


class ValidationEngine:
    """Executes validation rules against sheet outputs.

    Handles path template expansion and dispatches to type-specific
    validation methods.
    """

    def __init__(self, workspace: Path, sheet_context: dict[str, Any]) -> None:
        """Initialize validation engine."""
        self.workspace = workspace.resolve()
        self.sheet_context = sheet_context
        self._mtime_tracker = FileModificationTracker()

    @staticmethod
    def _display_path(path: Path) -> str:
        """Return a short display version of a path."""
        full = str(path)
        return path.name if len(full) > 50 else full

    @staticmethod
    def _read_file_text(path: Path) -> str:
        """Read file text with fallback for encoding issues."""
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            warnings.warn(
                f"File has encoding issues, using replacement chars: {path}",
                UnicodeWarning, stacklevel=3,
            )
            return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _missing_field_result(
        rule: ValidationRule, field_name: str,
    ) -> ValidationResult:
        """Return a validation result for a missing required field."""
        return ValidationResult(
            rule=rule, passed=False,
            error_message=f"{rule.type} rule requires '{field_name}' field",
            failure_reason=f"Validation rule is missing required '{field_name}' field",
            failure_category="error",
            suggested_fix=f"Add '{field_name}' field to the validation rule configuration",
        )

    def expand_path(self, path_template: str) -> Path:
        """Expand path template with sheet context variables.

        Supports: {sheet_num}, {workspace}, {start_item}, {end_item}

        Both workspace-relative and absolute paths are allowed. Agents work
        in ``backend.working_directory`` (typically the project root) and
        create files there — restricting validations to the workspace
        directory would prevent checking those files.
        """
        context = dict(self.sheet_context)
        context["workspace"] = str(self.workspace)

        try:
            expanded = path_template.format(**context)
        except IndexError as exc:
            raise ValueError(
                f"Invalid path template '{path_template}': {exc}. "
                "Use named placeholders like {{workspace}}, not bare {{}}."
            ) from exc
        return Path(expanded).resolve()

    def snapshot_mtime_files(self, rules: list[ValidationRule]) -> None:
        """Snapshot mtimes for all file_modified rules before sheet execution."""
        paths = [
            self.expand_path(r.path)
            for r in rules
            if r.type == "file_modified" and r.path
        ]
        self._mtime_tracker.snapshot(paths)

    def _check_condition(self, condition: str | None) -> bool:
        """Check if a validation condition is satisfied."""
        if condition is None:
            return True

        condition = condition.strip()

        if " and " in condition:
            parts = condition.split(" and ")
            return all(self._check_single_condition(p.strip()) for p in parts)

        return self._check_single_condition(condition)

    _CONDITION_OPS: dict[str, Any] = {
        ">=": operator.ge,
        "<=": operator.le,
        "==": operator.eq,
        "!=": operator.ne,
        ">": operator.gt,
        "<": operator.lt,
    }

    def _check_single_condition(self, condition: str) -> bool:
        """Check a single comparison condition."""
        match = re.match(r"(\w+)\s*(>=|<=|==|!=|>|<)\s*(\d+)", condition)
        if not match:
            return True

        var_name, op_str, value_str = match.groups()
        value = int(value_str)

        if var_name == "sheet_num":
            var_value = self.sheet_context.get("sheet_num", 0)
        else:
            ctx_value = self.sheet_context.get(var_name)
            if ctx_value is None or not isinstance(ctx_value, int):
                return True
            var_value = ctx_value

        op_fn = self._CONDITION_OPS.get(op_str)
        if op_fn is None:
            return True
        return bool(op_fn(var_value, value))

    def get_applicable_rules(
        self, rules: list[ValidationRule]
    ) -> list[ValidationRule]:
        """Get rules that apply to the current sheet context."""
        return [r for r in rules if self._check_condition(r.condition)]

    async def run_validations(self, rules: list[ValidationRule]) -> SheetValidationResult:
        """Execute all validation rules and return aggregate result."""
        applicable_rules = self.get_applicable_rules(rules)
        results: list[ValidationResult] = []

        for rule in applicable_rules:
            result = await self._run_single_validation(rule)
            results.append(result)

        return SheetValidationResult(
            sheet_num=self.sheet_context.get("sheet_num", 0),
            results=results,
            rules_checked=len(applicable_rules),
        )

    async def run_staged_validations(
        self, rules: list[ValidationRule]
    ) -> tuple[SheetValidationResult, int | None]:
        """Execute validations in stage order with fail-fast behavior."""
        applicable_rules = self.get_applicable_rules(rules)

        if not applicable_rules:
            return SheetValidationResult(
                sheet_num=self.sheet_context.get("sheet_num", 0),
                results=[],
                rules_checked=0,
            ), None

        stages: dict[int, list[ValidationRule]] = defaultdict(list)
        for rule in applicable_rules:
            stages[rule.stage].append(rule)

        all_results: list[ValidationResult] = []
        failed_stage: int | None = None

        for stage_num in sorted(stages.keys()):
            stage_rules = stages[stage_num]
            stage_passed = True

            for rule in stage_rules:
                result = await self._run_single_validation(rule)
                all_results.append(result)
                if not result.passed:
                    stage_passed = False

            if not stage_passed:
                failed_stage = stage_num
                self._mark_remaining_stages_skipped(
                    stages, stage_num, all_results
                )
                break

        return SheetValidationResult(
            sheet_num=self.sheet_context.get("sheet_num", 0),
            results=all_results,
            rules_checked=len(applicable_rules),
        ), failed_stage

    @staticmethod
    def _mark_remaining_stages_skipped(
        stages: dict[int, list[ValidationRule]],
        failed_stage: int,
        results: list[ValidationResult],
    ) -> None:
        """Mark all rules in stages after the failed stage as skipped."""
        for remaining_stage in sorted(stages.keys()):
            if remaining_stage > failed_stage:
                for rule in stages[remaining_stage]:
                    results.append(
                        ValidationResult(
                            rule=rule,
                            passed=False,
                            error_message=f"Skipped: Stage {failed_stage} failed",
                            failure_reason=f"Skipped due to failure in stage {failed_stage}",
                            failure_category="skipped",
                            confidence=0.0,
                        )
                    )

    _RETRYABLE_VALIDATION_TYPES = frozenset({
        "file_exists",
        "file_modified",
        "content_contains",
        "content_regex",
        "command_succeeds",
    })

    _HIGH_RISK_COMMAND_PATTERNS = (
        "sudo ", "chmod 777", "rm -rf /", "curl ", "wget ",
        "eval ", "> /etc/", "| sh", "| bash",
    )

    # Maps validation type → checker method name. `command_succeeds` is async.
    _VALIDATION_DISPATCH: dict[str, str] = {
        "file_exists": "_check_file_exists",
        "file_modified": "_check_file_modified",
        "content_contains": "_check_content_contains",
        "content_regex": "_check_content_regex",
        "command_succeeds": "_check_command_succeeds",
    }

    _ERROR_TYPE_MAP: dict[type, tuple[str, str]] = {
        OSError: ("I/O error", "io_error"),
        re.error: ("Regex error", "regex_error"),
    }

    async def _dispatch_validation(self, rule: ValidationRule) -> ValidationResult:
        """Dispatch a validation rule to the appropriate checker method."""
        method_name = self._VALIDATION_DISPATCH.get(rule.type)
        if method_name is None:
            return ValidationResult(
                rule=rule,
                passed=False,
                error_message=f"Unknown validation type: {rule.type}",
            )
        method = getattr(self, method_name)
        result = method(rule)
        if asyncio.iscoroutine(result):
            result = await result
        return result  # type: ignore[no-any-return]

    async def _run_single_validation(self, rule: ValidationRule) -> ValidationResult:
        """Execute a single validation rule with optional retry logic."""
        start = time.monotonic()

        should_retry = (
            rule.type in self._RETRYABLE_VALIDATION_TYPES
            and rule.retry_count > 0
        )
        max_attempts = rule.retry_count + 1 if should_retry else 1
        delay_seconds = rule.retry_delay_ms / 1000.0

        last_result: ValidationResult | None = None

        for attempt in range(max_attempts):
            try:
                result = await self._dispatch_validation(rule)

                if result.passed:
                    result.check_duration_ms = (time.monotonic() - start) * 1000
                    return result

                last_result = result
            except Exception as e:
                # Classify exception into a known error type, else internal_error
                for exc_type, (label, error_type) in self._ERROR_TYPE_MAP.items():
                    if isinstance(e, exc_type):
                        msg, etype = f"{label}: {e}", error_type
                        break
                else:
                    msg, etype = f"Validation error: {e}", "internal_error"

                last_result = ValidationResult(
                    rule=rule,
                    passed=False,
                    expected_value=rule.path or rule.pattern,
                    error_message=msg,
                    error_type=etype,
                )

            if attempt < max_attempts - 1:
                await asyncio.sleep(delay_seconds)

        if last_result:
            last_result.check_duration_ms = (time.monotonic() - start) * 1000
            return last_result

        return ValidationResult(
            rule=rule,
            passed=False,
            error_message="Validation failed after all attempts",
            check_duration_ms=(time.monotonic() - start) * 1000,
        )

    def _check_file_exists(self, rule: ValidationRule) -> ValidationResult:
        """Check if a file exists."""
        if not rule.path:
            return self._missing_field_result(rule, "path")

        path = self.expand_path(rule.path)

        if path.exists() and path.is_file():
            return ValidationResult(
                rule=rule, passed=True,
                actual_value=str(path), expected_value=str(path),
            )

        return ValidationResult(
            rule=rule, passed=False,
            actual_value=None, expected_value=str(path),
            error_message=f"File not found: {path}",
            failure_reason=f"File '{self._display_path(path)}' does not exist",
            failure_category="missing",
            suggested_fix=f"Create file at: {path}",
        )

    def _check_file_modified(self, rule: ValidationRule) -> ValidationResult:
        """Check if a file was modified after sheet started."""
        if not rule.path:
            return self._missing_field_result(rule, "path")

        path = self.expand_path(rule.path)
        display_path = self._display_path(path)

        if not path.exists():
            return ValidationResult(
                rule=rule, passed=False,
                actual_value=None, expected_value=str(path),
                error_message=f"File does not exist: {path}",
                failure_reason=f"File '{display_path}' does not exist (cannot check modification)",
                failure_category="missing",
                suggested_fix="Create the file first, then modify it",
            )

        was_modified = self._mtime_tracker.was_modified(path)
        original_mtime = self._mtime_tracker.get_original_mtime(path)

        if was_modified:
            return ValidationResult(
                rule=rule, passed=True,
                actual_value=f"mtime={path.stat().st_mtime:.6f}",
                expected_value=f"mtime>{original_mtime:.6f}" if original_mtime else "modified",
            )

        return ValidationResult(
            rule=rule, passed=False,
            actual_value=f"mtime={path.stat().st_mtime:.6f}",
            expected_value=f"mtime>{original_mtime:.6f}" if original_mtime else "modified",
            error_message=f"File not modified: {path}",
            failure_reason=f"File '{display_path}' was not modified during execution",
            failure_category="stale",
            suggested_fix="Verify the task updates this file with new content",
        )

    def _check_content_contains(self, rule: ValidationRule) -> ValidationResult:
        """Check if file contains expected content."""
        if not rule.path:
            return self._missing_field_result(rule, "path")
        if not rule.pattern:
            return self._missing_field_result(rule, "pattern")

        path = self.expand_path(rule.path)
        display_path = self._display_path(path)

        if not path.exists():
            return ValidationResult(
                rule=rule, passed=False,
                actual_value=None, expected_value=rule.pattern,
                error_message=f"File not found: {path}",
                failure_reason=f"File '{display_path}' does not exist (cannot check content)",
                failure_category="missing",
                suggested_fix=f"Create file '{display_path}' containing '{rule.pattern}'",
            )

        content = self._read_file_text(path)
        contains = rule.pattern in content

        if contains:
            return ValidationResult(
                rule=rule, passed=True,
                actual_value=f"contains={contains}", expected_value=rule.pattern,
            )

        display_pattern = (
            rule.pattern[:50] + "..." if len(rule.pattern) > 50 else rule.pattern
        )

        return ValidationResult(
            rule=rule, passed=False,
            actual_value=f"contains={contains}", expected_value=rule.pattern,
            error_message=f"Pattern not found in {path}: {rule.pattern}",
            failure_reason=f"File '{display_path}' missing expected content: '{display_pattern}'",
            failure_category="incomplete",
            suggested_fix=(
                f"Add exactly '{rule.pattern}' to the file"
                f" (this exact text is validated)"
            ),
        )

    def _check_content_regex(self, rule: ValidationRule) -> ValidationResult:
        """Check if file content matches regex pattern."""
        if not rule.path:
            return self._missing_field_result(rule, "path")
        if not rule.pattern:
            return self._missing_field_result(rule, "pattern")

        path = self.expand_path(rule.path)
        display_path = self._display_path(path)

        if not path.exists():
            return ValidationResult(
                rule=rule, passed=False,
                actual_value=None, expected_value=rule.pattern,
                error_message=f"File not found: {path}",
                failure_reason=f"File '{display_path}' does not exist (cannot check content)",
                failure_category="missing",
                suggested_fix="Create the file with content matching the pattern",
            )

        content = self._read_file_text(path)

        try:
            regex_match = re.search(rule.pattern, content, re.MULTILINE)
        except re.error as e:
            return ValidationResult(
                rule=rule, passed=False,
                error_message=f"Invalid regex pattern: {e}",
                failure_reason=f"Regex pattern is invalid: {e}",
                failure_category="error",
                suggested_fix="Fix the regex pattern syntax in the validation rule",
            )

        if regex_match:
            return ValidationResult(
                rule=rule, passed=True,
                actual_value=regex_match.group(0), expected_value=rule.pattern,
            )

        display_pattern = (
            rule.pattern[:50] + "..." if len(rule.pattern) > 50 else rule.pattern
        )

        return ValidationResult(
            rule=rule, passed=False,
            actual_value=None, expected_value=rule.pattern,
            error_message=f"Regex not matched in {path}: {rule.pattern}",
            failure_reason=(
                f"File '{display_path}' doesn't match pattern: {display_pattern}"
            ),
            failure_category="malformed",
            suggested_fix="Check the file format matches expectations",
        )

    async def _check_command_succeeds(self, rule: ValidationRule) -> ValidationResult:
        """Check if a shell command succeeds (exit code 0).

        Uses asyncio.create_subprocess for non-blocking execution.
        Commands are executed via ["bash", "-c", command] so that
        bash-specific syntax (PIPESTATUS, arrays, etc.) works reliably.
        Context values are shell-quoted via shlex.quote().
        """
        if not rule.command:
            return self._missing_field_result(rule, "command")

        cwd = (
            self.expand_path(rule.working_directory)
            if rule.working_directory
            else self.workspace
        )

        context = dict(self.sheet_context)
        context["workspace"] = str(self.workspace)
        expanded_command = rule.command
        for key, value in context.items():
            expanded_command = expanded_command.replace(
                "{" + key + "}", shlex.quote(str(value))
            )

        display_command = (
            expanded_command[:50] + "..."
            if len(expanded_command) > 50
            else expanded_command
        )

        cmd_lower = expanded_command.lower()
        for pattern in self._HIGH_RISK_COMMAND_PATTERNS:
            if pattern in cmd_lower:
                _logger.warning(
                    "Validation command contains high-risk pattern '%s': %s",
                    pattern.strip(),
                    display_command,
                )
                break

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", expanded_command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            cmd_timeout = rule.timeout_seconds or VALIDATION_COMMAND_TIMEOUT_SECONDS
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=cmd_timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return ValidationResult(
                    rule=rule, passed=False,
                    expected_value="exit_code=0",
                    error_message=(
                        f"Command timed out after"
                        f" {cmd_timeout} seconds"
                    ),
                    failure_reason=(
                        f"Command '{display_command}' timed out after"
                        f" {cmd_timeout} seconds"
                    ),
                    failure_category="error",
                    suggested_fix="Increase timeout or optimize the command",
                    error_type="internal_error",
                )

            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            returncode = proc.returncode or 0

            success = returncode == 0

            output = stdout_text + stderr_text
            if len(output) > VALIDATION_OUTPUT_TRUNCATE_CHARS:
                output_summary = (
                    output[:VALIDATION_OUTPUT_TRUNCATE_CHARS]
                    + f"\n... ({len(output)} chars total)"
                )
            else:
                output_summary = output

            if success:
                return ValidationResult(
                    rule=rule, passed=True,
                    actual_value=f"exit_code={returncode}",
                    expected_value="exit_code=0",
                    confidence=1.0,
                    confidence_factors={"exit_code": 1.0},
                )

            first_error_line = output.strip().split("\n")[0] if output.strip() else ""
            if len(first_error_line) > 80:
                first_error_line = first_error_line[:80] + "..."

            return ValidationResult(
                rule=rule, passed=False,
                actual_value=f"exit_code={returncode}",
                expected_value="exit_code=0",
                error_message=f"Command failed: {output_summary}",
                failure_reason=(
                    f"Command failed (exit {returncode}): {first_error_line}"
                ),
                failure_category="error",
                suggested_fix="Review command output for error details",
                confidence=0.8,
                confidence_factors={"exit_code": 0.5},
            )

        except Exception as e:
            return ValidationResult(
                rule=rule, passed=False,
                expected_value="exit_code=0",
                error_message=f"Command execution error: {e}",
                failure_reason=f"Command execution failed: {e}",
                failure_category="error",
                suggested_fix="Check command syntax and permissions",
                error_type="internal_error",
            )
