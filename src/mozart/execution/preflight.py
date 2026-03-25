"""Pre-flight checks and prompt analysis before sheet execution.

Analyzes prompts before execution to estimate token usage, extract file
references, and detect potential issues early.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mozart.core.checkpoint import PromptMetricsDict

# Common file path patterns to extract from prompts
FILE_PATH_PATTERNS = [
    # Unix-style absolute paths: /path/to/file.txt
    r"(?<![a-zA-Z0-9])(/(?:[a-zA-Z0-9._-]+/)*[a-zA-Z0-9._-]+\.[a-zA-Z0-9]+)",
    # Relative paths: ./path/to/file.txt or ../path/to/file.txt
    r"(?<![a-zA-Z0-9])(\.\.?/(?:[a-zA-Z0-9._-]+/)*[a-zA-Z0-9._-]+(?:\.[a-zA-Z0-9]+)?)",
    # Template paths with {workspace} or similar
    r"\{workspace\}/[a-zA-Z0-9._/-]+",
    # Paths in quotes: "path/to/file.txt" or 'path/to/file.txt'
    # Exclude newlines and cap length to avoid matching across lines of injected content
    r'["\']([^"\'\n]{1,250}/[^"\'\n]{1,250}\.[a-zA-Z0-9]+)["\']',
]

# Characters per token rough estimate (typical for English text with code)
CHARS_PER_TOKEN = 4

# Warning thresholds
TOKEN_WARNING_THRESHOLD = 50_000  # Warn if > 50K estimated tokens
TOKEN_ERROR_THRESHOLD = 150_000  # Error if > 150K estimated tokens (near context limits)
LINE_WARNING_THRESHOLD = 5_000  # Warn if > 5K lines


@dataclass
class PromptMetrics:
    """Metrics about a prompt for monitoring and optimization.

    Provides visibility into prompt size and complexity before execution.
    """

    character_count: int
    """Total character count in the prompt."""

    estimated_tokens: int
    """Estimated token count (chars / 4 as rough approximation)."""

    line_count: int
    """Number of lines in the prompt."""

    has_file_references: bool
    """Whether the prompt contains file path references."""

    referenced_paths: list[str] = field(default_factory=list)
    """List of file paths extracted from the prompt."""

    word_count: int = 0
    """Approximate word count for additional context."""

    @classmethod
    def from_prompt(cls, prompt: str) -> "PromptMetrics":
        """Analyze a prompt and extract metrics.

        Args:
            prompt: The prompt text to analyze.

        Returns:
            PromptMetrics with extracted data.
        """
        character_count = len(prompt)
        line_count = prompt.count("\n") + 1
        estimated_tokens = character_count // CHARS_PER_TOKEN
        word_count = len(prompt.split())

        # Extract file references
        referenced_paths = cls._extract_file_paths(prompt)
        has_file_references = len(referenced_paths) > 0

        return cls(
            character_count=character_count,
            estimated_tokens=estimated_tokens,
            line_count=line_count,
            has_file_references=has_file_references,
            referenced_paths=referenced_paths,
            word_count=word_count,
        )

    @staticmethod
    def _extract_file_paths(text: str) -> list[str]:
        """Extract file paths from text using common patterns.

        Args:
            text: Text to search for file paths.

        Returns:
            Deduplicated list of file paths found.
        """
        paths: set[str] = set()

        for pattern in FILE_PATH_PATTERNS:
            try:
                matches = re.findall(pattern, text)
                for match in matches:
                    # Handle both string matches and tuple matches from groups
                    path = match if isinstance(match, str) else match[0] if match else ""
                    if path and _is_plausible_path(path):
                        paths.add(path)
            except re.error:
                # Skip invalid patterns
                continue

        return sorted(paths)

    def to_dict(self) -> PromptMetricsDict:
        """Convert to serializable dictionary."""
        # Suppress return-value: mypy/pyright cannot infer a dict literal
        # as matching a total=False TypedDict — all keys verified via schema
        return {
            "character_count": self.character_count,
            "estimated_tokens": self.estimated_tokens,
            "line_count": self.line_count,
            "word_count": self.word_count,
            "has_file_references": self.has_file_references,
            "referenced_paths": self.referenced_paths,
        }


@dataclass
class PreflightResult:
    """Result of pre-flight checks before sheet execution.

    Captures both prompt metrics and any warnings/errors that
    should be addressed before or during execution.
    """

    prompt_metrics: PromptMetrics
    """Metrics about the prompt being executed."""

    warnings: list[str] = field(default_factory=list)
    """Non-fatal warnings that should be logged but don't block execution."""

    errors: list[str] = field(default_factory=list)
    """Fatal issues that should prevent execution."""

    paths_accessible: dict[str, bool] = field(default_factory=dict)
    """Mapping of referenced paths to their accessibility status."""

    working_directory_valid: bool = True
    """Whether the working directory exists and is accessible."""

    @property
    def has_errors(self) -> bool:
        """Check if there are any fatal errors."""
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        """Check if there are any warnings."""
        return len(self.warnings) > 0

    @property
    def can_proceed(self) -> bool:
        """Check if execution can proceed (no fatal errors)."""
        return not self.has_errors

    @property
    def inaccessible_paths(self) -> list[str]:
        """Get list of referenced paths that don't exist."""
        return [
            path for path, accessible in self.paths_accessible.items()
            if not accessible
        ]

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dictionary."""
        return {
            "prompt_metrics": self.prompt_metrics.to_dict(),
            "warnings": self.warnings,
            "errors": self.errors,
            "paths_accessible": self.paths_accessible,
            "working_directory_valid": self.working_directory_valid,
            "can_proceed": self.can_proceed,
        }


class PreflightChecker:
    """Performs pre-flight checks before sheet execution.

    Analyzes prompts for potential issues, verifies file references,
    and provides warnings for resource-intensive prompts.
    """

    def __init__(
        self,
        workspace: Path,
        working_directory: Path | None = None,
        token_warning_threshold: int = TOKEN_WARNING_THRESHOLD,
        token_error_threshold: int = TOKEN_ERROR_THRESHOLD,
    ) -> None:
        """Initialize preflight checker.

        Args:
            workspace: Base workspace directory for resolving relative paths.
            working_directory: Working directory for execution (defaults to workspace).
            token_warning_threshold: Token count above which to warn.
            token_error_threshold: Token count above which to error.
        """
        self.workspace = workspace.resolve()
        self.working_directory = (working_directory or workspace).resolve()
        self.token_warning_threshold = token_warning_threshold
        self.token_error_threshold = token_error_threshold

    def check(
        self,
        prompt: str,
        sheet_context: dict[str, Any] | None = None,
    ) -> PreflightResult:
        """Perform pre-flight checks on a prompt.

        Args:
            prompt: The prompt text to analyze.
            sheet_context: Optional context for template variable expansion.

        Returns:
            PreflightResult with metrics, warnings, and errors.
        """
        # Analyze prompt metrics
        metrics = PromptMetrics.from_prompt(prompt)

        warnings: list[str] = []
        errors: list[str] = []
        paths_accessible: dict[str, bool] = {}

        # Check token count thresholds
        if metrics.estimated_tokens > self.token_error_threshold:
            errors.append(
                f"Prompt exceeds token limit: ~{metrics.estimated_tokens:,} tokens "
                f"(threshold: {self.token_error_threshold:,}). "
                "Consider reducing prompt size to avoid context window issues."
            )
        elif metrics.estimated_tokens > self.token_warning_threshold:
            warnings.append(
                f"Large prompt detected: ~{metrics.estimated_tokens:,} tokens "
                f"(warning threshold: {self.token_warning_threshold:,}). "
                "This may impact response quality or cause truncation."
            )

        # Check line count
        if metrics.line_count > LINE_WARNING_THRESHOLD:
            warnings.append(
                f"Prompt has many lines: {metrics.line_count:,} lines. "
                "Very long prompts may reduce output quality."
            )

        # Verify working directory
        working_directory_valid = (
            self.working_directory.exists()
            and self.working_directory.is_dir()
        )
        if not working_directory_valid:
            errors.append(
                f"Working directory does not exist or is not accessible: "
                f"{self.working_directory}"
            )

        # Verify referenced file paths
        if metrics.has_file_references:
            paths_accessible = self._check_paths(
                metrics.referenced_paths,
                sheet_context or {},
            )
            inaccessible = [p for p, exists in paths_accessible.items() if not exists]
            if inaccessible:
                # This is a warning, not an error - files might be created by the prompt
                warnings.append(
                    f"Some referenced paths do not exist: {', '.join(inaccessible[:5])}"
                    + (f" (+{len(inaccessible) - 5} more)" if len(inaccessible) > 5 else "")
                )

        return PreflightResult(
            prompt_metrics=metrics,
            warnings=warnings,
            errors=errors,
            paths_accessible=paths_accessible,
            working_directory_valid=working_directory_valid,
        )

    def _check_paths(
        self,
        paths: list[str],
        sheet_context: dict[str, Any],
    ) -> dict[str, bool]:
        """Check if referenced paths exist.

        Args:
            paths: List of paths to check.
            sheet_context: Context for template expansion.

        Returns:
            Dictionary mapping paths to their existence status.
        """
        result: dict[str, bool] = {}

        for path_str in paths:
            # Skip template paths that can't be resolved
            if "{" in path_str and "}" in path_str:
                try:
                    # Try to expand template
                    context = dict(sheet_context)
                    context["workspace"] = str(self.workspace)
                    expanded = path_str.format(**context)
                    path = Path(expanded)
                except (KeyError, ValueError, IndexError):
                    # Can't expand template, skip
                    # IndexError: bare {} from non-Python format strings
                    # (e.g., Rust format!("{}") in injected content)
                    continue
            else:
                path = Path(path_str)

            # Resolve relative paths against workspace
            if not path.is_absolute():
                path = self.workspace / path

            try:
                result[path_str] = path.exists()
            except OSError:
                # path.exists() raises OSError for paths with components
                # exceeding filesystem limits (e.g., ENAMETOOLONG)
                result[path_str] = False

        return result


def _is_plausible_path(path: str) -> bool:
    """Check if a string looks like a plausible file path.

    Filters out false positives from pattern matching.

    Args:
        path: Potential file path string.

    Returns:
        True if the path seems plausible.
    """
    # Too short to be a real path
    if len(path) < 3:
        return False

    # Common false positives
    false_positives = {
        "/s",  # regex escapes
        "/n",
        "/t",
        "/r",
        "/d",
        "/w",
        "1.0",  # version numbers
        "2.0",
        "3.0",
    }
    if path.lower() in false_positives:
        return False

    # Too long to be a real path (filesystem component limit is 255)
    if len(path) > 500:
        return False

    # Should have at least one path separator or extension
    return "/" in path or "." in path


def run_preflight_check(
    prompt: str,
    workspace: Path,
    working_directory: Path | None = None,
    sheet_context: dict[str, Any] | None = None,
) -> PreflightResult:
    """Convenience function to run preflight checks.

    Args:
        prompt: The prompt text to analyze.
        workspace: Base workspace directory.
        working_directory: Optional working directory for execution.
        sheet_context: Optional context for template expansion.

    Returns:
        PreflightResult with metrics and any warnings/errors.
    """
    checker = PreflightChecker(
        workspace=workspace,
        working_directory=working_directory,
    )
    return checker.check(prompt, sheet_context)
