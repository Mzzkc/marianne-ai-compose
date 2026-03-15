"""Best-practice validation checks.

Detects common configuration pitfalls and suggests improvements
for reliability, correctness, and performance.
"""

import re
from pathlib import Path

from mozart.core.config import JobConfig
from mozart.validation.base import ValidationIssue, ValidationSeverity
from mozart.validation.checks._helpers import find_line_in_yaml

# Built-in template variable names used by Mozart's rendering engine.
_BUILTIN_NAMES: frozenset[str] = frozenset({
    "workspace",
    "sheet_num",
    "total_sheets",
    "start_item",
    "end_item",
    "stage",
    "instance",
    "fan_count",
    "total_stages",
})


class JinjaInValidationPathCheck:
    """Check for Jinja syntax in validation paths (V201).

    Validation paths use Python ``.format()`` syntax (``{workspace}``),
    not Jinja (``{{ workspace }}``).  Using Jinja syntax causes paths to
    render literally with braces intact.
    """

    @property
    def check_id(self) -> str:
        return "V201"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Detects Jinja syntax in validation paths"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check validation paths for ``{{`` markers."""
        issues: list[ValidationIssue] = []

        for i, validation in enumerate(config.validations):
            path_val = getattr(validation, "path", None)
            if path_val and "{{" in str(path_val):
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Validation rule {i + 1} uses Jinja syntax"
                            f" in path: {path_val}"
                        ),
                        line=find_line_in_yaml(raw_yaml, str(path_val)),
                        context=str(path_val),
                        suggestion=(
                            "Use {workspace} not {{ workspace }}"
                            " in validation paths"
                        ),
                        metadata={
                            "validation_index": str(i),
                            "path": str(path_val),
                        },
                    )
                )

        return issues


class FormatSyntaxInTemplateCheck:
    """Check for format-string syntax in Jinja templates (V202).

    Prompt templates use Jinja (``{{ name }}``), not Python format strings
    (``{name}``).  Only flags known built-in names to avoid false positives.
    """

    # Matches {name} but NOT {{name}} — single-brace references to built-ins.
    _PATTERN = re.compile(
        r"(?<!\{)\{(" + "|".join(_BUILTIN_NAMES) + r")\}(?!\})"
    )

    @property
    def check_id(self) -> str:
        return "V202"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Detects format-string syntax in Jinja templates"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Scan prompt template for ``{builtin_name}`` patterns."""
        issues: list[ValidationIssue] = []

        template = config.prompt.template
        if not template:
            return issues

        for match in self._PATTERN.finditer(template):
            name = match.group(1)
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"Format-string syntax '{{{name}}}' found in"
                        f" prompt template — use Jinja '{{{{ {name} }}}}'"
                    ),
                    line=find_line_in_yaml(raw_yaml, f"{{{name}}}"),
                    context=match.group(0),
                    suggestion=(
                        f"Use {{{{ {name} }}}} not {{{name}}}"
                        f" in Jinja templates"
                    ),
                    metadata={
                        "variable": name,
                    },
                )
            )

        return issues


class NoValidationsCheck:
    """Check that at least one validation rule exists (V203).

    Without validations, sheets always "pass" and you learn nothing
    about execution quality.
    """

    @property
    def check_id(self) -> str:
        return "V203"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks that at least one validation rule exists"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Fire when no validations are configured."""
        if len(config.validations) == 0:
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message="No validation rules configured",
                    suggestion=(
                        "Add at least one validation rule"
                        " — see docs/score-writing-guide.md"
                    ),
                    metadata={},
                )
            ]
        return []


class MissingSkipPermissionsCheck:
    """Check that skip_permissions is enabled for Claude CLI (V204).

    Claude CLI will prompt for permission approval, which hangs in
    unattended mode.
    """

    @property
    def check_id(self) -> str:
        return "V204"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks skip_permissions is set for Claude CLI backend"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Fire when Claude CLI backend lacks skip_permissions."""
        if (
            config.backend.type == "claude_cli"
            and config.backend.skip_permissions is False
        ):
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        "Claude CLI backend without skip_permissions"
                        " will hang waiting for permission approval"
                    ),
                    line=find_line_in_yaml(raw_yaml, "skip_permissions"),
                    suggestion=(
                        "Set backend.skip_permissions: true"
                        " for unattended execution"
                    ),
                    metadata={
                        "backend_type": config.backend.type,
                    },
                )
            ]
        return []


class FileExistsOnlyCheck:
    """Check for file_exists-only validations (V205).

    ``file_exists`` alone cannot detect stale files left from a
    previous run.  Adding ``file_modified`` or content checks is
    recommended.
    """

    @property
    def check_id(self) -> str:
        return "V205"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.INFO

    @property
    def description(self) -> str:
        return "Warns when all validations are file_exists only"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Fire when every validation is file_exists."""
        if not config.validations:
            return []

        all_file_exists = all(
            v.type == "file_exists" for v in config.validations
        )
        if all_file_exists:
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        "All validations are file_exists"
                        " — stale files from previous runs will pass"
                    ),
                    suggestion=(
                        "Consider adding file_modified or content checks"
                        " to detect stale files"
                    ),
                    metadata={
                        "validation_count": str(len(config.validations)),
                    },
                )
            ]
        return []


class FanOutWithoutDependenciesCheck:
    """Check fan-out configs have dependencies (V206).

    When fan-out stages are configured without dependencies, stages
    may execute out of order.
    """

    @property
    def check_id(self) -> str:
        return "V206"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks fan-out configs have stage dependencies"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Fire when fan-out is configured without dependencies."""
        if (
            config.sheet.fan_out_stage_map is not None
            and not config.sheet.dependencies
        ):
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        "Fan-out is configured but no sheet dependencies"
                        " are declared — stages may run out of order"
                    ),
                    line=find_line_in_yaml(raw_yaml, "fan_out"),
                    suggestion=(
                        "Add sheet.dependencies to control"
                        " stage execution order"
                    ),
                    metadata={},
                )
            ]
        return []


class FanOutWithoutParallelCheck:
    """Check fan-out configs enable parallel execution (V207).

    Fan-out instances are designed to run concurrently, but will
    execute sequentially if parallel mode is disabled.
    """

    @property
    def check_id(self) -> str:
        return "V207"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.INFO

    @property
    def description(self) -> str:
        return "Checks fan-out configs enable parallel execution"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Fire when fan-out is configured without parallel."""
        if (
            config.sheet.fan_out_stage_map is not None
            and config.parallel.enabled is False
        ):
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        "Fan-out is configured but parallel execution is"
                        " disabled — instances will run sequentially"
                    ),
                    line=find_line_in_yaml(raw_yaml, "parallel"),
                    suggestion=(
                        "Enable parallel.enabled: true to run"
                        " fan-out instances concurrently"
                    ),
                    metadata={},
                )
            ]
        return []


class VariableShadowingCheck:
    """Check for user variables that shadow built-ins (V208).

    User-defined prompt variables that share names with Mozart's
    built-in variables will override the real values, causing
    unexpected behavior.
    """

    @property
    def check_id(self) -> str:
        return "V208"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Detects user variables that shadow built-in names"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Fire when prompt.variables shadows a built-in name."""
        issues: list[ValidationIssue] = []

        for name in config.prompt.variables:
            if name in _BUILTIN_NAMES:
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Variable '{name}' shadows the"
                            f" built-in {name}"
                        ),
                        line=find_line_in_yaml(raw_yaml, f"{name}:"),
                        suggestion=(
                            f"Rename variable '{name}'"
                            f" — it shadows the built-in {name}"
                        ),
                        metadata={
                            "variable": name,
                        },
                    )
                )

        return issues


class SkipWhenSheetRangeCheck:
    """Check that skip_when and skip_when_command keys are in-range (V212).

    Each key in skip_when/skip_when_command must satisfy 1 ≤ k ≤ total_sheets.
    Out-of-range keys are silently ignored at runtime — they will never fire.
    This is a WARNING (non-blocking) because the config is executable, just
    suspicious.
    """

    @property
    def check_id(self) -> str:
        return "V212"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks skip_when and skip_when_command keys are within sheet range"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Warn when skip_when or skip_when_command keys are out of range."""
        issues: list[ValidationIssue] = []
        total = config.sheet.total_sheets

        for k in config.sheet.skip_when:
            if not (1 <= k <= total):
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"skip_when key {k} is out of range "
                            f"(valid: 1\u2013{total}); this rule will never fire"
                        ),
                        suggestion=(
                            f"Remove sheet {k} or adjust total_sheets / fan-out"
                        ),
                        metadata={"sheet_num": str(k), "source": "skip_when"},
                    )
                )

        for k in config.sheet.skip_when_command:
            if not (1 <= k <= total):
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"skip_when_command key {k} is out of range "
                            f"(valid: 1\u2013{total}); this rule will never fire"
                        ),
                        suggestion=(
                            f"Remove sheet {k} or adjust total_sheets / fan-out"
                        ),
                        metadata={
                            "sheet_num": str(k),
                            "source": "skip_when_command",
                        },
                    )
                )

        return issues


class MissingDisableMcpCheck:
    """Check that disable_mcp is enabled for Claude CLI (V209).

    MCP servers can cause deadlocks in unattended orchestration.
    Disabling MCP provides faster, more reliable execution.
    """

    @property
    def check_id(self) -> str:
        return "V209"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.INFO

    @property
    def description(self) -> str:
        return "Checks disable_mcp is set for Claude CLI backend"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Fire when Claude CLI backend lacks disable_mcp."""
        if (
            config.backend.type == "claude_cli"
            and config.backend.disable_mcp is False
        ):
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        "Claude CLI backend without disable_mcp"
                        " may experience MCP deadlocks"
                    ),
                    line=find_line_in_yaml(raw_yaml, "disable_mcp"),
                    suggestion=(
                        "Set backend.disable_mcp: true"
                        " to prevent MCP deadlocks"
                    ),
                    metadata={
                        "backend_type": config.backend.type,
                    },
                )
            ]
        return []
