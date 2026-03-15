"""Validation runner that orchestrates all checks.

The ValidationRunner collects all registered checks, runs them against
a configuration, and aggregates the results.
"""

from pathlib import Path

from mozart.core.config import JobConfig
from mozart.validation.base import ValidationCheck, ValidationIssue, ValidationSeverity
from mozart.validation.checks import (
    EmptyPatternCheck,
    FanOutWithoutDependenciesCheck,
    FanOutWithoutParallelCheck,
    FileExistsOnlyCheck,
    FormatSyntaxInTemplateCheck,
    JinjaInValidationPathCheck,
    JinjaSyntaxCheck,
    JinjaUndefinedVariableCheck,
    MissingDisableMcpCheck,
    MissingSkipPermissionsCheck,
    NoValidationsCheck,
    PreludeCadenzaFileCheck,
    RegexPatternCheck,
    SkillFilesExistCheck,
    SkipWhenSheetRangeCheck,
    SystemPromptFileCheck,
    TemplateFileExistsCheck,
    TimeoutRangeCheck,
    ValidationTypeCheck,
    VariableShadowingCheck,
    VersionReferenceCheck,
    WorkingDirectoryCheck,
    WorkspaceParentExistsCheck,
)


class ValidationRunner:
    """Orchestrates validation checks against a configuration.

    The runner collects issues from all checks, sorts them by severity,
    and provides summary information for reporting.
    """

    def __init__(self, checks: list[ValidationCheck] | None = None):
        """Initialize with a list of checks.

        Args:
            checks: List of validation checks to run. If None, uses default checks.
        """
        self._checks: list[ValidationCheck] = checks or []

    def add_check(self, check: ValidationCheck) -> None:
        """Add a check to the runner."""
        self._checks.append(check)

    def validate(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Run all validation checks against the configuration.

        Args:
            config: Parsed JobConfig object
            config_path: Path to the config file
            raw_yaml: Raw YAML text for line number extraction

        Returns:
            List of all issues found, sorted by severity (errors first)
        """
        all_issues: list[ValidationIssue] = []

        for check in self._checks:
            try:
                issues = check.check(config, config_path, raw_yaml)
                all_issues.extend(issues)
            except Exception as e:
                # Don't let one check failure break all validation
                all_issues.append(
                    ValidationIssue(
                        check_id=check.check_id,
                        severity=ValidationSeverity.ERROR,
                        message=f"Check {check.check_id} failed to execute: {e}",
                        suggestion="This may be a bug in Mozart validation",
                    )
                )

        # Sort by severity (ERROR > WARNING > INFO)
        severity_order = {
            ValidationSeverity.ERROR: 0,
            ValidationSeverity.WARNING: 1,
            ValidationSeverity.INFO: 2,
        }
        all_issues.sort(key=lambda i: severity_order.get(i.severity, 3))

        return all_issues

    def get_exit_code(self, issues: list[ValidationIssue]) -> int:
        """Determine exit code based on issues found.

        Returns:
            0: No errors (warnings/info OK)
            1: One or more ERROR-severity issues
        """
        return 1 if self.has_errors(issues) else 0

    def has_errors(self, issues: list[ValidationIssue]) -> bool:
        """Check if any issues are errors."""
        return any(i.severity == ValidationSeverity.ERROR for i in issues)

    def count_by_severity(
        self, issues: list[ValidationIssue]
    ) -> dict[ValidationSeverity, int]:
        """Count issues by severity level."""
        counts: dict[ValidationSeverity, int] = {
            ValidationSeverity.ERROR: 0,
            ValidationSeverity.WARNING: 0,
            ValidationSeverity.INFO: 0,
        }
        for issue in issues:
            counts[issue.severity] += 1
        return counts


def create_default_checks() -> list[ValidationCheck]:
    """Create the default set of validation checks.

    Returns all built-in checks in recommended execution order.
    """
    # Note: We return instances, not classes
    # The type checker sees these as ValidationCheck protocol implementations
    checks: list[ValidationCheck] = [
        # Jinja checks (most common issues)
        JinjaSyntaxCheck(),
        JinjaUndefinedVariableCheck(),
        # Path checks
        WorkspaceParentExistsCheck(),
        TemplateFileExistsCheck(),
        SystemPromptFileCheck(),
        WorkingDirectoryCheck(),
        SkillFilesExistCheck(),
        PreludeCadenzaFileCheck(),
        # Config checks
        RegexPatternCheck(),
        ValidationTypeCheck(),
        TimeoutRangeCheck(),
        EmptyPatternCheck(),
        VersionReferenceCheck(),
        # Best-practice checks
        JinjaInValidationPathCheck(),
        FormatSyntaxInTemplateCheck(),
        NoValidationsCheck(),
        MissingSkipPermissionsCheck(),
        FileExistsOnlyCheck(),
        FanOutWithoutDependenciesCheck(),
        FanOutWithoutParallelCheck(),
        VariableShadowingCheck(),
        MissingDisableMcpCheck(),
        SkipWhenSheetRangeCheck(),
    ]
    return checks
