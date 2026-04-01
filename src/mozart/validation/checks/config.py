"""Configuration structure validation checks.

Validates configuration values like regex patterns, timeout ranges,
validation rule completeness, and instrument name resolution.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from mozart.core.config import JobConfig
from mozart.validation.base import ValidationIssue, ValidationSeverity
from mozart.validation.checks._helpers import find_line_in_yaml

_logger = logging.getLogger(__name__)


class RegexPatternCheck:
    """Check that regex patterns in validations compile (V007).

    Invalid regex patterns will cause runtime errors during validation.
    """

    @property
    def check_id(self) -> str:
        return "V007"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Validates regex patterns compile correctly"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check regex patterns in validations and rate limit detection."""
        issues: list[ValidationIssue] = []

        # Check validation rule patterns
        for i, validation in enumerate(config.validations):
            if validation.type == "content_regex" and validation.pattern:
                issue = self._check_pattern(
                    validation.pattern,
                    f"validation[{i}].pattern",
                    self._find_pattern_line(raw_yaml, validation.pattern),
                )
                if issue:
                    issues.append(issue)

        # Check rate limit detection patterns
        for i, pattern in enumerate(config.rate_limit.detection_patterns):
            issue = self._check_pattern(
                pattern,
                f"rate_limit.detection_patterns[{i}]",
                None,
            )
            if issue:
                issues.append(issue)

        return issues

    def _check_pattern(
        self,
        pattern: str,
        location: str,
        line: int | None,
    ) -> ValidationIssue | None:
        """Check if a single regex pattern compiles."""
        try:
            re.compile(pattern)
            return None
        except re.error as e:
            # Extract position from error if available
            pos_info = ""
            if hasattr(e, "pos") and e.pos is not None:
                pos_info = f" at position {e.pos}"

            return ValidationIssue(
                check_id=self.check_id,
                severity=self.severity,
                message=f"Invalid regex pattern in {location}: {e.msg}{pos_info}",
                line=line,
                context=pattern[:60] + "..." if len(pattern) > 60 else pattern,
                suggestion=self._suggest_regex_fix(str(e.msg), pattern),
                metadata={
                    "pattern": pattern,
                    "location": location,
                    "error": str(e),
                },
            )

    def _suggest_regex_fix(self, error_msg: str, pattern: str) -> str:
        """Suggest fixes for common regex errors."""
        error_lower = error_msg.lower()

        if "nothing to repeat" in error_lower:
            return "Escape special characters like *, +, ? with backslash: \\* \\+ \\?"

        if "unbalanced parenthesis" in error_lower or "missing )" in error_lower:
            return "Check parentheses are balanced or escape them: \\( \\)"

        if "unterminated character class" in error_lower:
            return "Close the character class with ] or escape the opening [: \\["

        if "bad escape" in error_lower:
            return "Invalid escape sequence - use raw string r'pattern' or double backslash"

        # Check for common mistake: unescaped special chars
        special_chars = [".", "*", "+", "?", "[", "]", "(", ")", "{", "}", "^", "$", "|"]
        for char in special_chars:
            if char in pattern and f"\\{char}" not in pattern:
                return f"Consider escaping '{char}' with backslash if literal match intended"

        return "Review regex syntax - see Python re module documentation"

    def _find_pattern_line(self, yaml_str: str, pattern: str) -> int | None:
        """Find the line number of a pattern in the YAML."""
        # Escape regex special chars for searching
        search_pattern = re.escape(pattern[:30])
        for i, line in enumerate(yaml_str.split("\n"), 1):
            if search_pattern[:20] in line:
                return i
        return None


class ValidationTypeCheck:
    """Check that validation rules have required fields (V008).

    Ensures validations have the fields needed for their type.
    """

    @property
    def check_id(self) -> str:
        return "V008"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Checks validation rules have required fields"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check validation rules have required fields."""
        issues: list[ValidationIssue] = []

        required_fields = {
            "file_exists": ["path"],
            "file_modified": ["path"],
            "content_contains": ["path", "pattern"],
            "content_regex": ["path", "pattern"],
            "command_succeeds": ["command"],
        }

        for i, validation in enumerate(config.validations):
            required = required_fields.get(validation.type, [])
            missing = []

            for field in required:
                value = getattr(validation, field, None)
                if value is None or (isinstance(value, str) and not value.strip()):
                    missing.append(field)

            if missing:
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Validation rule {i + 1} ({validation.type})"
                            f" missing required fields: {', '.join(missing)}"
                        ),
                        suggestion=f"Add {', '.join(missing)} to the validation rule",
                        metadata={
                            "validation_index": str(i),
                            "validation_type": validation.type,
                            "missing_fields": ",".join(missing),
                        },
                    )
                )

        return issues


class TimeoutRangeCheck:
    """Check timeout values are reasonable (V103/V104).

    Warns about very short timeouts (may cause failures) or
    very long timeouts (may waste resources).
    """

    # Thresholds in seconds
    MIN_REASONABLE_TIMEOUT = 60  # 1 minute
    MAX_REASONABLE_TIMEOUT = 7200  # 2 hours

    @property
    def check_id(self) -> str:
        return "V103"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks timeout values are reasonable"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check timeout values."""
        issues: list[ValidationIssue] = []

        timeout = config.backend.timeout_seconds

        if timeout < self.MIN_REASONABLE_TIMEOUT:
            issues.append(
                ValidationIssue(
                    check_id="V103",
                    severity=ValidationSeverity.WARNING,
                    message=f"Very short timeout ({timeout}s) may cause premature failures",
                    line=find_line_in_yaml(raw_yaml, "timeout_seconds:"),
                    suggestion=(
                        f"Consider timeout_seconds >="
                        f" {self.MIN_REASONABLE_TIMEOUT}"
                        f" for Claude CLI operations"
                    ),
                    metadata={
                        "timeout": str(timeout),
                        "threshold": str(self.MIN_REASONABLE_TIMEOUT),
                    },
                )
            )

        if timeout > self.MAX_REASONABLE_TIMEOUT:
            issues.append(
                ValidationIssue(
                    check_id="V104",
                    severity=ValidationSeverity.INFO,
                    message=(
                        f"Very long timeout ({timeout}s = {timeout / 3600:.1f}h)"
                        f" - consider if this is necessary"
                    ),
                    line=find_line_in_yaml(raw_yaml, "timeout_seconds:"),
                    suggestion=(
                        "Long timeouts can tie up resources;"
                        " consider breaking into smaller tasks"
                    ),
                    metadata={
                        "timeout": str(timeout),
                        "threshold": str(self.MAX_REASONABLE_TIMEOUT),
                    },
                )
            )

        return issues


class VersionReferenceCheck:
    """Check that evolved scores don't reference previous version paths (V009).

    When a score evolves (e.g., v20 → v21), the new score file should have
    all references updated to the new version. This catches cases where the
    name, workspace, or other paths still reference the old version.
    """

    @property
    def check_id(self) -> str:
        return "V009"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Checks evolved scores don't reference previous version paths"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check for stale version references in evolved scores."""
        issues: list[ValidationIssue] = []

        # Extract version from filename (e.g., v21 from mozart-opus-evolution-v21.yaml)
        filename = config_path.name
        version_match = re.search(r"-v(\d+)\.yaml$", filename)
        if not version_match:
            # Not a versioned score file, skip this check
            return issues

        current_version = int(version_match.group(1))
        if current_version <= 1:
            # v1 has no previous version to check against
            return issues

        previous_version = current_version - 1
        prev_patterns = [
            f"-v{previous_version}",  # e.g., -v20
            f"v{previous_version}.0",  # e.g., v20.0
            f"v{previous_version}/",  # e.g., workspace-v20/
            f"-v{previous_version}/",  # e.g., evolution-workspace-v20/
            f"evolution-v{previous_version}",  # e.g., evolution-v20
        ]

        # Check the job name
        if f"-v{previous_version}" in config.name:
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"Job name '{config.name}' references"
                        f" v{previous_version} but file is v{current_version}"
                    ),
                    line=find_line_in_yaml(raw_yaml, "name:"),
                    suggestion=f"Update name to use v{current_version}",
                    metadata={
                        "field": "name",
                        "current_version": str(current_version),
                        "previous_version": str(previous_version),
                    },
                )
            )

        # Check the workspace path
        workspace_str = str(config.workspace)
        if f"-v{previous_version}" in workspace_str or f"v{previous_version}/" in workspace_str:
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"Workspace path references v{previous_version}"
                        f" but file is v{current_version}"
                    ),
                    line=find_line_in_yaml(raw_yaml, "workspace:"),
                    context=workspace_str,
                    suggestion=f"Update workspace to use v{current_version}",
                    metadata={
                        "field": "workspace",
                        "current_version": str(current_version),
                        "previous_version": str(previous_version),
                    },
                )
            )

        # Scan raw YAML for other references (in comments, descriptions, etc.)
        for i, line in enumerate(raw_yaml.split("\n"), 1):
            # Skip lines that are defining the version progression history
            if "VERSION PROGRESSION:" in line or "→" in line or "->" in line:
                continue
            # Skip lines in comments that are documenting history
            if line.strip().startswith("#") and ("v" + str(previous_version - 1) in line):
                continue

            for pattern in prev_patterns:
                if pattern in line:
                    # Check if this is just historical documentation
                    hist_markers = [
                        "EVOLUTION FROM", "evolved from", "LEARNINGS",
                    ]
                    if any(hist in line for hist in hist_markers):
                        continue
                    # Check if it's in the version progression list
                    if (
                        f"v{previous_version}→v{current_version}" in line
                        or f"v{previous_version}->v{current_version}" in line
                    ):
                        continue

                    issues.append(
                        ValidationIssue(
                            check_id=self.check_id,
                            severity=ValidationSeverity.WARNING,
                            message=(
                                f"Line {i} references v{previous_version}"
                                f" - verify this is intentional"
                            ),
                            line=i,
                            context=line.strip()[:80],
                            suggestion=(
                        f"Update to v{current_version} if this should"
                        f" reference the current version"
                    ),
                            metadata={
                                "pattern": pattern,
                                "current_version": str(current_version),
                                "previous_version": str(previous_version),
                            },
                        )
                    )
                    break  # Only one issue per line

        return issues


class EmptyPatternCheck:
    """Check for empty patterns in validations (V106)."""

    @property
    def check_id(self) -> str:
        return "V106"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks for empty patterns in content validations"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check for empty patterns."""
        issues: list[ValidationIssue] = []

        for i, validation in enumerate(config.validations):
            if (
                validation.type in ("content_contains", "content_regex")
                and validation.pattern is not None
                and validation.pattern.strip() == ""
            ):
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Empty pattern in validation rule {i + 1}"
                            f" will match any content"
                        ),
                        suggestion="Add a meaningful pattern or remove this validation",
                        metadata={
                            "validation_index": str(i),
                        },
                    )
                )

        return issues


class InstrumentNameCheck:
    """Check that instrument names resolve to known profiles (V210).

    Warns when an instrument name doesn't match any loaded instrument profile.
    This catches typos (e.g., 'clause-code' instead of 'claude-code') at
    validation time rather than runtime.

    Severity is WARNING (not ERROR) because the conductor may have instruments
    the validator doesn't know about — profiles loaded from other directories,
    dynamic instruments, etc. The warning is informational, not blocking.

    Checks:
    - Top-level ``instrument:`` field
    - ``sheet.per_sheet_instruments`` per-sheet overrides
    - ``sheet.instrument_map`` batch assignments
    - ``movements.N.instrument`` per-movement overrides
    """

    @property
    def check_id(self) -> str:
        return "V210"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks instrument names resolve to known profiles"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check all instrument references against the loaded profile registry."""
        # Load known instruments — gracefully degrade on failure
        try:
            from mozart.instruments.loader import load_all_profiles

            known = set(load_all_profiles().keys())
        except Exception:
            _logger.debug("V210: could not load instrument profiles, skipping check")
            return []

        if not known:
            return []

        # Score-level instrument aliases are valid references — they resolve
        # to profile names at build time via config.instruments[name].profile.
        score_instruments = set(config.instruments.keys())
        all_valid = known | score_instruments

        issues: list[ValidationIssue] = []

        # 1. Top-level instrument: field
        # Must check against all_valid (profiles + score aliases), not just
        # known (profiles only). A score can define instrument: my-alias and
        # instruments: { my-alias: { profile: claude-code } } — that's valid.
        if config.instrument and config.instrument not in all_valid:
            issues.append(self._make_issue(
                config.instrument,
                "score-level instrument",
                find_line_in_yaml(raw_yaml, "instrument:"),
                all_valid,
            ))

        # 2. Per-sheet instruments
        if config.sheet.per_sheet_instruments:
            for sheet_num, instr_name in config.sheet.per_sheet_instruments.items():
                if instr_name not in all_valid:
                    issues.append(self._make_issue(
                        instr_name,
                        f"sheet {sheet_num} instrument",
                        find_line_in_yaml(raw_yaml, f"{sheet_num}:"),
                        all_valid,
                    ))

        # 3. Instrument map
        if config.sheet.instrument_map:
            for instr_name in config.sheet.instrument_map:
                if instr_name not in all_valid:
                    issues.append(self._make_issue(
                        instr_name,
                        "instrument_map entry",
                        find_line_in_yaml(raw_yaml, f"{instr_name}:"),
                        all_valid,
                    ))

        # 4. Movement-level instruments
        if config.movements:
            for mov_num, mov_def in config.movements.items():
                if mov_def.instrument and mov_def.instrument not in all_valid:
                    issues.append(self._make_issue(
                        mov_def.instrument,
                        f"movement {mov_num} instrument",
                        find_line_in_yaml(raw_yaml, f"{mov_num}:"),
                        all_valid,
                    ))

        return issues

    def _make_issue(
        self,
        name: str,
        location: str,
        line: int | None,
        known: set[str],
    ) -> ValidationIssue:
        """Create a ValidationIssue for an unknown instrument name."""
        available = sorted(known)
        suggestion = (
            f"Available instruments: {', '.join(available)}. "
            f"Run 'mozart instruments list' to see all instruments."
        )
        return ValidationIssue(
            check_id=self.check_id,
            severity=self.severity,
            message=f"Unknown {location}: '{name}' not found in instrument registry",
            line=line,
            suggestion=suggestion,
            metadata={
                "instrument_name": name,
                "location": location,
            },
        )
