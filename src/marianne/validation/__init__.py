"""Enhanced validation module for Marianne job configurations.

This module provides comprehensive validation beyond Pydantic schema validation,
including Jinja template syntax checking, path existence validation, regex
pattern compilation, and cross-reference checks.

The validation system is designed for two use cases:
1. Pre-execution validation via `mzt validate` command
2. Self-healing diagnosis to identify fixable configuration issues

Example usage:
    from marianne.validation import ValidationRunner, create_default_checks

    runner = ValidationRunner(create_default_checks())
    issues = runner.validate(config, config_path, raw_yaml)

    for issue in issues:
        print(f"[{issue.severity}] {issue.check_id}: {issue.message}")
"""

from marianne.validation.base import (
    ValidationCheck,
    ValidationIssue,
    ValidationSeverity,
)
from marianne.validation.reporter import ValidationReporter
from marianne.validation.runner import ValidationRunner, create_default_checks

__all__ = [
    "ValidationCheck",
    "ValidationIssue",
    "ValidationSeverity",
    "ValidationRunner",
    "ValidationReporter",
    "create_default_checks",
]
