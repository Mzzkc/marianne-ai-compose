"""Path validation checks.

Validates that paths referenced in the configuration exist and are accessible.
"""

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.job import InjectionCategory, InjectionItem
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml, resolve_path


class WorkspaceParentExistsCheck:
    """Check that workspace parent directory exists (V002).

    The workspace itself will be created, but its parent must exist.
    This is auto-fixable by creating the parent directories.
    """

    @property
    def check_id(self) -> str:
        return "V002"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Checks that workspace parent directory exists"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check workspace parent exists."""
        issues: list[ValidationIssue] = []

        workspace = resolve_path(config.workspace, config_path)
        parent = workspace.parent

        if not parent.exists():
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=f"Workspace parent directory does not exist: {parent}",
                    line=find_line_in_yaml(raw_yaml, "workspace:"),
                    suggestion=f"Create parent directory: mkdir -p {parent}",
                    auto_fixable=True,
                    metadata={
                        "path": str(parent),
                        "workspace": str(workspace),
                    },
                )
            )

        return issues


class TemplateFileExistsCheck:
    """Check that template_file exists if specified (V003)."""

    @property
    def check_id(self) -> str:
        return "V003"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Checks that template_file exists"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check template_file exists."""
        issues: list[ValidationIssue] = []

        if config.prompt.template_file:
            template_path = resolve_path(config.prompt.template_file, config_path)

            if not template_path.exists():
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=f"Template file not found: {template_path}",
                        line=find_line_in_yaml(raw_yaml, "template_file:"),
                        suggestion="Create the template file or fix the path",
                        metadata={
                            "expected_path": str(template_path),
                        },
                    )
                )
            elif not template_path.is_file():
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=f"Template path is not a file: {template_path}",
                        line=find_line_in_yaml(raw_yaml, "template_file:"),
                        suggestion="Ensure template_file points to a file, not a directory",
                    )
                )

        return issues


class SystemPromptFileCheck:
    """Check that system_prompt_file exists if specified (V004)."""

    @property
    def check_id(self) -> str:
        return "V004"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Checks that system_prompt_file exists"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check system_prompt_file exists."""
        issues: list[ValidationIssue] = []

        if config.backend.system_prompt_file:
            sys_prompt_path = resolve_path(
                config.backend.system_prompt_file, config_path
            )

            if not sys_prompt_path.exists():
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=f"System prompt file not found: {sys_prompt_path}",
                        line=find_line_in_yaml(raw_yaml, "system_prompt_file:"),
                        suggestion="Create the system prompt file or fix the path",
                        metadata={
                            "expected_path": str(sys_prompt_path),
                        },
                    )
                )

        return issues


class WorkingDirectoryCheck:
    """Check that working_directory is valid if specified (V005)."""

    @property
    def check_id(self) -> str:
        return "V005"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Checks that working_directory exists and is a directory"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check working_directory is valid."""
        issues: list[ValidationIssue] = []

        if config.backend.working_directory:
            working_dir = resolve_path(
                config.backend.working_directory, config_path
            )

            if not working_dir.exists():
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=f"Working directory does not exist: {working_dir}",
                        line=find_line_in_yaml(raw_yaml, "working_directory:"),
                        suggestion=f"Create directory: mkdir -p {working_dir}",
                        auto_fixable=True,
                        metadata={
                            "path": str(working_dir),
                        },
                    )
                )
            elif not working_dir.is_dir():
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=f"Working directory path is not a directory: {working_dir}",
                        line=find_line_in_yaml(raw_yaml, "working_directory:"),
                        suggestion="Ensure path points to a directory, not a file",
                    )
                )

        return issues


class PreludeCadenzaFileCheck:
    """Check that prelude/cadenza files exist when paths are static (V108).

    Only checks non-templated paths (no Jinja ``{{``). Templated paths
    are resolved at execution time and cannot be validated statically.
    This is a WARNING because files might be created before execution.
    """

    @property
    def check_id(self) -> str:
        return "V108"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks that prelude/cadenza files exist (static paths only)"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check prelude and cadenza file/directory paths."""
        issues: list[ValidationIssue] = []

        for item in config.sheet.prelude:
            issues.extend(self._check_item(item, "prelude", config, config_path, raw_yaml))
        for sheet_num, items in config.sheet.cadenzas.items():
            for item in items:
                issues.extend(
                    self._check_item(item, f"cadenza (sheet {sheet_num})", config, config_path, raw_yaml)
                )

        return issues

    def _check_item(
        self,
        item: InjectionItem,
        source: str,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check a single injection item (file or directory)."""
        issues: list[ValidationIssue] = []

        if item.directory is not None:
            # Directory cadenza — check directory exists
            directory = item.directory
            if "{{" in directory:
                return issues  # Skip templated paths
            dir_path = resolve_path(Path(directory), config_path)
            if not dir_path.is_dir():
                # INFO for context (demo mode), WARNING for skill/tool
                severity = (
                    ValidationSeverity.INFO
                    if item.as_ == InjectionCategory.CONTEXT
                    else ValidationSeverity.WARNING
                )
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=severity,
                        message=f"Injection directory from {source} not found: {dir_path}",
                        line=find_line_in_yaml(raw_yaml, directory),
                        suggestion="Create the directory or fix the path",
                        metadata={"source": source, "path": str(dir_path)},
                    )
                )
        else:
            # File injection — check file exists
            file_val = item.file
            assert file_val is not None  # guaranteed by Pydantic validator
            if "{{" in file_val:
                return issues
            file_path = resolve_path(Path(file_val), config_path)
            if not file_path.exists():
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=f"Injection file from {source} not found: {file_path}",
                        line=find_line_in_yaml(raw_yaml, file_val),
                        suggestion="Create the file or fix the path",
                        metadata={"source": source, "path": str(file_path)},
                    )
                )

        return issues


class SkillFilesExistCheck:
    """Check that files referenced in validation commands exist (V107).

    This is a WARNING because skill files might be optional.
    """

    @property
    def check_id(self) -> str:
        return "V107"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks for referenced files in validation paths"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check files referenced in validations."""
        issues: list[ValidationIssue] = []

        for i, validation in enumerate(config.validations):
            # Skip validations with template variables in path
            # Check file_exists validations - these are expected to be created
            # so we don't warn about them. Check other types.
            if (
                validation.path
                and "{" not in validation.path
                and validation.type in ("content_contains", "content_regex")
            ):
                file_path = resolve_path(Path(validation.path), config_path)

                # Only warn if it's an absolute path that doesn't exist
                # Relative paths might be created during execution
                if file_path.is_absolute() and not file_path.exists():
                    issues.append(
                        ValidationIssue(
                            check_id=self.check_id,
                            severity=self.severity,
                            message=(
                                f"File referenced in validation {i + 1}"
                                f" does not exist: {file_path}"
                            ),
                            suggestion="Ensure file will be created before this validation runs",
                            metadata={
                                "validation_index": str(i),
                                "path": str(file_path),
                            },
                        )
                    )

        return issues
