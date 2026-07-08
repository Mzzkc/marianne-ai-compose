"""Validation engine — executes validation rules against sheet outputs.

Dispatches to type-specific check methods: file_exists, file_modified,
content_contains, content_regex, command_succeeds, path_in_scope,
field_match, file_sha256, and csv_unique_key.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import operator
import os
import re
import shlex
import signal
import time
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from marianne.core.config import ValidationRule
from marianne.core.constants import (
    SHEET_NUM_KEY,
    VALIDATION_COMMAND_TIMEOUT_SECONDS,
    VALIDATION_OUTPUT_TRUNCATE_CHARS,
)
from marianne.utils.process import safe_killpg as _safe_killpg

from .models import (
    FileModificationTracker,
    SheetValidationResult,
    ValidationResult,
)

_logger = logging.getLogger("marianne.execution.validation")


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

    _HASH_CHUNK_SIZE = 1024 * 1024

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
    def _format_value(value: Any, *, limit: int = 160) -> str:
        """Format a structured value for validation result details."""
        try:
            formatted = json.dumps(value, sort_keys=True)
        except TypeError:
            formatted = str(value)
        if len(formatted) > limit:
            return formatted[: limit - 3] + "..."
        return formatted

    def _load_structured_file(self, path: Path) -> Any:
        """Load JSON/YAML structured data from a file path."""
        text = self._read_file_text(path)
        suffix = path.suffix.lower()
        if suffix == ".json":
            return json.loads(text)
        if suffix in (".yaml", ".yml"):
            return yaml.safe_load(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return yaml.safe_load(text)

    @staticmethod
    def _parse_field_path(field_path: str) -> list[str | int]:
        """Parse dot/bracket field paths like ``a.b[0].c``."""
        tokens: list[str | int] = []
        for part in field_path.split("."):
            if not part:
                raise ValueError(f"Empty path segment in field_path: {field_path}")
            position = 0
            while position < len(part):
                if part[position] == "[":
                    end = part.find("]", position)
                    if end == -1:
                        raise ValueError(f"Unclosed list index in field_path: {field_path}")
                    raw_index = part[position + 1:end]
                    if not raw_index.isdigit():
                        raise ValueError(
                            f"List index must be a non-negative integer in field_path: {field_path}"
                        )
                    tokens.append(int(raw_index))
                    position = end + 1
                    continue

                end = part.find("[", position)
                if end == -1:
                    end = len(part)
                key = part[position:end]
                if not key:
                    raise ValueError(f"Empty path segment in field_path: {field_path}")
                tokens.append(key)
                position = end
        return tokens

    @classmethod
    def _resolve_field_path(cls, data: Any, field_path: str) -> tuple[bool, Any, str | None]:
        """Resolve a parsed field path against nested dict/list data."""
        try:
            tokens = cls._parse_field_path(field_path)
        except ValueError as exc:
            return False, None, str(exc)

        current = data
        traversed: list[str] = []
        for token in tokens:
            if isinstance(token, int):
                traversed.append(f"[{token}]")
            else:
                traversed.append(token)

            if isinstance(current, dict):
                if not isinstance(token, str) or token not in current:
                    return False, None, f"Missing field segment: {'.'.join(traversed)}"
                current = current[token]
                continue

            if isinstance(current, list):
                if isinstance(token, str) and token.isdigit():
                    token = int(token)
                if not isinstance(token, int) or token < 0 or token >= len(current):
                    return False, None, f"Missing list index: {'.'.join(traversed)}"
                current = current[token]
                continue

            return False, None, (
                f"Cannot descend into non-container at: {'.'.join(traversed[:-1])}"
            )

        return True, current, None

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

    def expand_scoped_path(self, path_template: str) -> Path:
        """Expand a path for scope checks, resolving relatives under workspace."""
        context = dict(self.sheet_context)
        context["workspace"] = str(self.workspace)

        try:
            expanded = path_template.format(**context)
        except IndexError as exc:
            raise ValueError(
                f"Invalid path template '{path_template}': {exc}. "
                "Use named placeholders like {{workspace}}, not bare {{}}."
            ) from exc

        path = Path(expanded).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        return path.resolve()

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
        match = re.match(r"(\w+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+)", condition)
        if not match:
            return True

        var_name, op_str, value_str = match.groups()
        value = int(value_str)

        ctx_value = self.sheet_context.get(
            SHEET_NUM_KEY if var_name == SHEET_NUM_KEY else var_name
        )
        if ctx_value is None:
            return False
        if isinstance(ctx_value, str) and ctx_value.strip().lstrip("-").isdigit():
            var_value = int(ctx_value.strip())
        elif type(ctx_value) is int:
            var_value = ctx_value
        else:
            return False

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
            sheet_num=self.sheet_context.get(SHEET_NUM_KEY, 0),
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
                sheet_num=self.sheet_context.get(SHEET_NUM_KEY, 0),
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
            sheet_num=self.sheet_context.get(SHEET_NUM_KEY, 0),
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
        "path_in_scope",
        "field_match",
        "file_sha256",
        "csv_unique_key",
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
        "path_in_scope": "_check_path_in_scope",
        "field_match": "_check_field_match",
        "file_sha256": "_check_file_sha256",
        "csv_unique_key": "_check_csv_unique_key",
    }

    _ERROR_TYPE_MAP: dict[type, tuple[str, str]] = {
        OSError: ("I/O error", "io_error"),
        re.error: ("Regex error", "regex_error"),
        json.JSONDecodeError: ("JSON parse error", "malformed_data"),
        yaml.YAMLError: ("YAML parse error", "malformed_data"),
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

    def _check_path_in_scope(self, rule: ValidationRule) -> ValidationResult:
        """Check that a path resolves inside an allowed root."""
        if not rule.path:
            return self._missing_field_result(rule, "path")

        path = self.expand_scoped_path(rule.path)
        scope = self.expand_scoped_path(rule.path_scope or "{workspace}")

        try:
            in_scope = path.is_relative_to(scope)
        except ValueError:
            in_scope = False

        if in_scope:
            return ValidationResult(
                rule=rule,
                passed=True,
                actual_value=str(path),
                expected_value=f"inside {scope}",
                confidence=1.0,
                confidence_factors={"canonical_path_scope": 1.0},
            )

        return ValidationResult(
            rule=rule,
            passed=False,
            actual_value=str(path),
            expected_value=f"inside {scope}",
            error_message=f"Path resolves outside allowed scope: {path}",
            failure_reason=(
                f"Path '{self._display_path(path)}' is outside "
                f"allowed scope '{self._display_path(scope)}'"
            ),
            failure_category="security",
            suggested_fix=(
                "Use a path under the workspace or narrow path_scope, "
                "and avoid symlinks or '..' segments that resolve outside it"
            ),
            confidence=1.0,
            confidence_factors={"canonical_path_scope": 1.0},
        )

    def _check_field_match(self, rule: ValidationRule) -> ValidationResult:
        """Check that a structured JSON/YAML field matches an expected value."""
        if not rule.path:
            return self._missing_field_result(rule, "path")
        if not rule.field_path:
            return self._missing_field_result(rule, "field_path")
        if not rule.has_expected_value_literal and not rule.source_path:
            return ValidationResult(
                rule=rule,
                passed=False,
                error_message="field_match requires expected_value or source_path",
                failure_reason=(
                    "Validation rule has no literal or source comparison value"
                ),
                failure_category="error",
                suggested_fix="Add expected_value or source_path to the field_match rule",
            )

        path = self.expand_path(rule.path)
        display_path = self._display_path(path)
        if not path.exists():
            return ValidationResult(
                rule=rule,
                passed=False,
                actual_value=None,
                expected_value=rule.field_path,
                error_message=f"File not found: {path}",
                failure_reason=f"File '{display_path}' does not exist (cannot read field)",
                failure_category="missing",
                suggested_fix=f"Create structured JSON/YAML file '{display_path}'",
            )

        try:
            data = self._load_structured_file(path)
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            return ValidationResult(
                rule=rule,
                passed=False,
                error_message=f"Could not parse structured file {path}: {exc}",
                failure_reason=f"File '{display_path}' is not valid JSON/YAML",
                failure_category="malformed",
                suggested_fix="Write valid JSON or YAML before field_match runs",
                error_type="malformed_data",
            )

        found, actual, error = self._resolve_field_path(data, rule.field_path)
        if not found:
            return ValidationResult(
                rule=rule,
                passed=False,
                actual_value=None,
                expected_value=rule.field_path,
                error_message=f"Field not found in {path}: {rule.field_path}",
                failure_reason=error or f"Missing field '{rule.field_path}'",
                failure_category="missing",
                suggested_fix=f"Populate '{rule.field_path}' in '{display_path}'",
            )

        expected = rule.expected_value
        expected_label = "expected_value"
        if rule.source_path:
            source_path = self.expand_path(rule.source_path)
            source_field_path = rule.source_field_path or rule.field_path
            if not source_path.exists():
                return ValidationResult(
                    rule=rule,
                    passed=False,
                    actual_value=self._format_value(actual),
                    expected_value=f"{source_path}:{source_field_path}",
                    error_message=f"Source file not found: {source_path}",
                    failure_reason=(
                        f"Reference file '{self._display_path(source_path)}' "
                        "does not exist"
                    ),
                    failure_category="missing",
                    suggested_fix="Create the reference file before field_match runs",
                )
            try:
                source_data = self._load_structured_file(source_path)
            except (json.JSONDecodeError, yaml.YAMLError) as exc:
                return ValidationResult(
                    rule=rule,
                    passed=False,
                    actual_value=self._format_value(actual),
                    expected_value=f"{source_path}:{source_field_path}",
                    error_message=f"Could not parse source file {source_path}: {exc}",
                    failure_reason=(
                        f"Reference file '{self._display_path(source_path)}' "
                        "is not valid JSON/YAML"
                    ),
                    failure_category="malformed",
                    suggested_fix="Write valid JSON or YAML in the source file",
                    error_type="malformed_data",
                )
            source_found, expected, source_error = self._resolve_field_path(
                source_data, source_field_path
            )
            if not source_found:
                return ValidationResult(
                    rule=rule,
                    passed=False,
                    actual_value=self._format_value(actual),
                    expected_value=f"{source_path}:{source_field_path}",
                    error_message=(
                        f"Source field not found in {source_path}: {source_field_path}"
                    ),
                    failure_reason=source_error or (
                        f"Missing source field '{source_field_path}'"
                    ),
                    failure_category="missing",
                    suggested_fix=f"Populate '{source_field_path}' in the source file",
                )
            expected_label = f"{self._display_path(source_path)}:{source_field_path}"

        actual_text = self._format_value(actual)
        expected_text = self._format_value(expected)
        if actual == expected:
            return ValidationResult(
                rule=rule,
                passed=True,
                actual_value=actual_text,
                expected_value=expected_text,
                confidence_factors={"structured_field_equality": 1.0},
            )

        return ValidationResult(
            rule=rule,
            passed=False,
            actual_value=actual_text,
            expected_value=expected_text,
            error_message=(
                f"Field mismatch in {path}: {rule.field_path} "
                f"was {actual_text}, expected {expected_text}"
            ),
            failure_reason=(
                f"Field '{rule.field_path}' in '{display_path}' does not match "
                f"{expected_label}"
            ),
            failure_category="mismatch",
            suggested_fix="Update the artifact or reference data so the structured values match",
            confidence_factors={"structured_field_equality": 1.0},
        )

    def _check_file_sha256(self, rule: ValidationRule) -> ValidationResult:
        """Check that a file's SHA-256 digest matches a pinned value."""
        if not rule.path:
            return self._missing_field_result(rule, "path")
        if not rule.sha256:
            return self._missing_field_result(rule, "sha256")

        path = self.expand_path(rule.path)
        display_path = self._display_path(path)
        if not path.exists():
            return ValidationResult(
                rule=rule,
                passed=False,
                actual_value=None,
                expected_value=rule.sha256.lower(),
                error_message=f"File not found: {path}",
                failure_reason=f"File '{display_path}' does not exist (cannot hash)",
                failure_category="missing",
                suggested_fix=f"Create the file '{display_path}' before hash validation",
            )
        if not path.is_file():
            return ValidationResult(
                rule=rule,
                passed=False,
                actual_value="not a regular file",
                expected_value=rule.sha256.lower(),
                error_message=f"Path is not a regular file: {path}",
                failure_reason=f"Path '{display_path}' cannot be hashed as a file",
                failure_category="malformed",
                suggested_fix="Point file_sha256 at a regular file",
            )

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(self._HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        actual_digest = digest.hexdigest()
        expected_digest = rule.sha256.lower()
        if actual_digest == expected_digest:
            return ValidationResult(
                rule=rule,
                passed=True,
                actual_value=actual_digest,
                expected_value=expected_digest,
                confidence_factors={"sha256_digest": 1.0},
            )

        return ValidationResult(
            rule=rule,
            passed=False,
            actual_value=actual_digest,
            expected_value=expected_digest,
            error_message=f"SHA-256 mismatch for {path}",
            failure_reason=f"File '{display_path}' does not match the pinned digest",
            failure_category="integrity",
            suggested_fix=(
                "Restore the expected file content or update the pinned sha256 "
                "after reviewing the change"
            ),
            confidence_factors={"sha256_digest": 1.0},
        )

    def _check_csv_unique_key(self, rule: ValidationRule) -> ValidationResult:
        """Check that a CSV column has no duplicate values."""
        if not rule.path:
            return self._missing_field_result(rule, "path")
        if not rule.key_field:
            return self._missing_field_result(rule, "key_field")

        path = self.expand_path(rule.path)
        display_path = self._display_path(path)
        if not path.exists():
            return ValidationResult(
                rule=rule,
                passed=False,
                actual_value=None,
                expected_value=f"unique {rule.key_field}",
                error_message=f"File not found: {path}",
                failure_reason=f"CSV file '{display_path}' does not exist",
                failure_category="missing",
                suggested_fix=f"Create CSV file '{display_path}'",
            )

        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    return ValidationResult(
                        rule=rule,
                        passed=False,
                        actual_value="no header",
                        expected_value=f"column {rule.key_field}",
                        error_message=f"CSV file has no header: {path}",
                        failure_reason=f"CSV file '{display_path}' has no header row",
                        failure_category="malformed",
                        suggested_fix="Write a header row before data rows",
                    )
                if rule.key_field not in reader.fieldnames:
                    return ValidationResult(
                        rule=rule,
                        passed=False,
                        actual_value=",".join(reader.fieldnames),
                        expected_value=rule.key_field,
                        error_message=(
                            f"CSV key column '{rule.key_field}' not found in {path}"
                        ),
                        failure_reason=(
                            f"CSV file '{display_path}' is missing key column "
                            f"'{rule.key_field}'"
                        ),
                        failure_category="missing",
                        suggested_fix=f"Add a '{rule.key_field}' column to the CSV header",
                    )

                seen: dict[str, int] = {}
                duplicate: tuple[str, int, int] | None = None
                row_count = 0
                for row_number, row in enumerate(reader, start=2):
                    row_count += 1
                    key_value = row.get(rule.key_field, "")
                    if key_value in seen:
                        duplicate = (key_value, seen[key_value], row_number)
                        break
                    seen[key_value] = row_number
        except csv.Error as exc:
            return ValidationResult(
                rule=rule,
                passed=False,
                error_message=f"Could not parse CSV file {path}: {exc}",
                failure_reason=f"CSV file '{display_path}' is malformed",
                failure_category="malformed",
                suggested_fix="Write a valid CSV file with a header row",
                error_type="malformed_data",
            )

        if duplicate is None:
            return ValidationResult(
                rule=rule,
                passed=True,
                actual_value=f"rows={row_count}, unique_keys={len(seen)}",
                expected_value=f"unique {rule.key_field}",
                confidence_factors={"csv_key_uniqueness": 1.0},
            )

        key_value, first_row, duplicate_row = duplicate
        return ValidationResult(
            rule=rule,
            passed=False,
            actual_value=(
                f"duplicate {rule.key_field}={key_value!r} "
                f"at rows {first_row} and {duplicate_row}"
            ),
            expected_value=f"unique {rule.key_field}",
            error_message=(
                f"Duplicate CSV key '{key_value}' in {path} "
                f"(rows {first_row} and {duplicate_row})"
            ),
            failure_reason=(
                f"CSV file '{display_path}' has duplicate '{rule.key_field}' "
                f"value '{key_value}'"
            ),
            failure_category="duplicate",
            suggested_fix="Remove duplicate rows or make each key value unique",
            confidence_factors={"csv_key_uniqueness": 1.0},
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

        # Process Lifecycle Phase 1: spawn with start_new_session=True so
        # bash -> pytest -> xdist workers share a killable group. SIGTERM
        # then 2s grace then SIGKILL runs in the finally on every exit path.
        # See docs/specs/2026-04-16-process-lifecycle-design.md (Change 1, 2).
        proc = None
        pgid: int | None = None
        timed_out = False
        cmd_timeout = rule.timeout_seconds or VALIDATION_COMMAND_TIMEOUT_SECONDS
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", expanded_command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

            if proc.pid is not None:
                try:
                    pgid = os.getpgid(proc.pid)
                except ProcessLookupError:
                    pgid = None

            # Daemon-own-group safety: abort if start_new_session failed.
            if pgid is not None:
                try:
                    daemon_pgid = os.getpgid(0)
                except ProcessLookupError:
                    daemon_pgid = None
                if daemon_pgid is not None and pgid == daemon_pgid:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await proc.wait()
                    except ProcessLookupError:
                        pass
                    raise RuntimeError(
                        f"Validation command shares daemon pgid ({pgid}); "
                        "refusing to continue"
                    )

            stdout_bytes = b""
            stderr_bytes = b""
            try:
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=cmd_timeout,
                    )
                except TimeoutError:
                    timed_out = True
            finally:
                # SIGTERM -> 2s grace -> SIGKILL of the process group on
                # every exit path. Idempotent when process already exited.
                if proc is not None and proc.returncode is None:
                    if pgid is not None:
                        try:
                            _safe_killpg(pgid, signal.SIGTERM, context="validation.kill_grace")
                        except (ProcessLookupError, PermissionError):
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                        except TimeoutError:
                            try:
                                _safe_killpg(pgid, signal.SIGKILL, context="validation.kill_force")
                            except (ProcessLookupError, PermissionError):
                                pass
                            try:
                                await proc.wait()
                            except ProcessLookupError:
                                pass
                    else:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        try:
                            await proc.wait()
                        except ProcessLookupError:
                            pass

            if timed_out:
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
