"""Validation result models and data structures.

Contains the core result types used by the validation engine and
consumed by the runner, prompts, and escalation layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from mozart.core.checkpoint import ValidationDetailDict
from mozart.core.config import ValidationRule
from mozart.utils.time import utc_now


@dataclass
class ValidationResult:
    """Result of a single validation check."""

    rule: ValidationRule
    passed: bool
    actual_value: str | None = None
    expected_value: str | None = None
    error_message: str | None = None
    checked_at: datetime = field(default_factory=utc_now)
    check_duration_ms: float = 0.0
    confidence: float = 1.0
    """Confidence in this validation result (0.0-1.0). Default 1.0 = fully confident."""
    confidence_factors: dict[str, float] = field(default_factory=dict)
    """Factors affecting confidence, e.g., {'file_age': 0.9, 'pattern_specificity': 0.8}."""
    failure_reason: str | None = None
    """Semantic explanation of why validation failed."""
    failure_category: str | None = None
    """Category of failure: 'missing', 'malformed', 'incomplete', 'stale', 'error'."""
    suggested_fix: str | None = None
    """Hint for how to fix the issue."""
    error_type: str | None = None
    """Distinguishes validation failures from validation crashes.
    None or 'validation_failure' = output didn't meet the rule.
    'internal_error' = the validation check itself crashed."""

    def to_dict(self) -> ValidationDetailDict:
        """Convert to serializable dictionary."""
        return {
            "rule_type": self.rule.type,
            "description": self.rule.description,
            "path": self.rule.path,
            "pattern": self.rule.pattern,
            "passed": self.passed,
            "actual_value": self.actual_value,
            "expected_value": self.expected_value,
            "error_message": self.error_message,
            "checked_at": self.checked_at.isoformat(),
            "check_duration_ms": self.check_duration_ms,
            "confidence": self.confidence,
            "confidence_factors": self.confidence_factors,
            "failure_reason": self.failure_reason,
            "failure_category": self.failure_category,
            "suggested_fix": self.suggested_fix,
            "error_type": self.error_type,
        }

    def format_failure_summary(self) -> str:
        """Format failure information for prompt injection."""
        if self.passed:
            return ""

        parts: list[str] = []
        if self.failure_category:
            parts.append(f"[{self.failure_category.upper()}]")
        if self.failure_reason:
            parts.append(self.failure_reason)
        if self.suggested_fix:
            parts.append(f"Fix: {self.suggested_fix}")

        return " ".join(parts)


@dataclass
class SheetValidationResult:
    """Aggregate result of all validations for a sheet."""

    sheet_num: int
    results: list[ValidationResult]
    rules_checked: int = 0

    @property
    def all_passed(self) -> bool:
        """Check if all validations passed."""
        return all(result.passed for result in self.results)

    @property
    def passed_count(self) -> int:
        """Count of passed validations."""
        return sum(1 for result in self.results if result.passed)

    @property
    def failed_count(self) -> int:
        """Count of failed validations (excluding skipped)."""
        return sum(
            1 for result in self.results
            if not result.passed and result.failure_category != "skipped"
        )

    @property
    def skipped_count(self) -> int:
        """Count of skipped validations (due to staged fail-fast)."""
        return sum(
            1 for result in self.results
            if result.failure_category == "skipped"
        )

    @property
    def executed_count(self) -> int:
        """Count of validations that actually executed (not skipped)."""
        return len(self.results) - self.skipped_count

    @property
    def pass_percentage(self) -> float:
        """Percentage of validations that passed."""
        if not self.results:
            return 100.0
        return (self.passed_count / len(self.results)) * 100

    @property
    def executed_pass_percentage(self) -> float:
        """Percentage of EXECUTED validations that passed."""
        executed = self.executed_count
        if executed == 0:
            return 100.0
        return (self.passed_count / executed) * 100

    @property
    def majority_passed(self) -> bool:
        """Returns True if >50% of validations passed."""
        return self.pass_percentage > 50.0

    @property
    def aggregate_confidence(self) -> float:
        """Calculate weighted aggregate confidence across all validation results."""
        if not self.results:
            return 1.0

        total_weight = 0.0
        weighted_sum = 0.0

        for result in self.results:
            weight = 1.0 if result.passed else 0.5
            weighted_sum += result.confidence * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 1.0

    def get_passed_rules(self) -> list[ValidationRule]:
        """Get rules that passed."""
        return [result.rule for result in self.results if result.passed]

    def get_failed_rules(self) -> list[ValidationRule]:
        """Get rules that failed."""
        return [result.rule for result in self.results if not result.passed]

    def get_passed_results(self) -> list[ValidationResult]:
        """Get results that passed."""
        return [result for result in self.results if result.passed]

    def get_failed_results(self) -> list[ValidationResult]:
        """Get results that failed."""
        return [result for result in self.results if not result.passed]

    def to_dict_list(self) -> list[ValidationDetailDict]:
        """Convert all results to serializable list."""
        return [result.to_dict() for result in self.results]

    def get_semantic_summary(self) -> dict[str, Any]:
        """Aggregate semantic information from failed validations."""
        category_counts: dict[str, int] = {}
        has_semantic_info = False

        for result in self.results:
            if not result.passed and result.failure_category:
                has_semantic_info = True
                category = result.failure_category
                category_counts[category] = category_counts.get(category, 0) + 1

        dominant_category: str | None = None
        if category_counts:
            dominant_category = max(category_counts, key=lambda k: category_counts[k])

        return {
            "category_counts": category_counts,
            "dominant_category": dominant_category,
            "has_semantic_info": has_semantic_info,
            "total_failures": self.failed_count,
        }

    def get_actionable_hints(self, limit: int = 3) -> list[str]:
        """Extract actionable hints from failed validations."""
        hints: list[str] = []
        seen: set[str] = set()

        for result in self.results:
            if not result.passed and result.suggested_fix:
                hint = result.suggested_fix
                if len(hint) > 100:
                    hint = hint[:97] + "..."

                if hint not in seen:
                    seen.add(hint)
                    hints.append(hint)

                if len(hints) >= limit:
                    break

        return hints


class FileModificationTracker:
    """Tracks file mtimes before sheet execution for file_modified checks."""

    def __init__(self) -> None:
        self._mtimes: dict[str, float] = {}

    def snapshot(self, paths: list[Path]) -> None:
        """Capture mtimes of files before sheet execution."""
        for path in paths:
            path_str = str(path.resolve())
            if path.exists():
                self._mtimes[path_str] = path.stat().st_mtime
            else:
                self._mtimes[path_str] = 0.0

    def was_modified(self, path: Path) -> bool:
        """Check if file was modified (or created) after snapshot."""
        resolved = path.resolve()
        try:
            current_mtime = resolved.stat().st_mtime
        except (OSError, ValueError):
            return False
        original_mtime = self._mtimes.get(str(resolved), 0.0)
        return current_mtime > original_mtime

    def get_original_mtime(self, path: Path) -> float | None:
        """Get the original mtime from snapshot."""
        path_str = str(path.resolve())
        return self._mtimes.get(path_str)

    def clear(self) -> None:
        """Clear all tracked mtimes."""
        self._mtimes.clear()
