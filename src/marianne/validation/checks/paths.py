"""Path validation checks.

Validates that paths referenced in the configuration exist and are accessible.
"""

import re
from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.job import InjectionCategory, InjectionItem
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml, resolve_path


def _gated_producer_sheet(condition: str | None) -> int | None:
    """The sheet a ``file_exists`` validation is gated to, parsed from its
    condition (``sheet_num == N`` / ``sheet_num >= N``). None for other shapes.

    A ``file_exists`` validation gated to a sheet is the score author
    declaring that sheet produces the file — the producer PROXY that #137's
    ordering check and V108's suppression both reason about.
    """
    if not condition:
        return None
    m = re.search(r"sheet_num\s*(==|>=)\s*(\d+)", condition)
    return int(m.group(2)) if m else None


def _infer_producers(config: JobConfig) -> dict[str, set[int]]:
    """Map a produced file's basename to the set of sheets declared (via a
    sheet-gated ``file_exists`` validation) to produce it."""
    producers: dict[str, set[int]] = {}
    for rule in config.validations:
        if rule.type != "file_exists" or not rule.path:
            continue
        sheet = _gated_producer_sheet(rule.condition)
        if sheet is None:
            continue
        producers.setdefault(Path(str(rule.path)).name, set()).add(sheet)
    return producers


def _dag_ancestors(sheet: int, deps: dict[int, list[int]]) -> set[int]:
    """Transitive dependency ancestors of ``sheet`` — the sheets guaranteed
    to run before it."""
    seen: set[int] = set()
    stack = list(deps.get(sheet, []))
    while stack:
        s = stack.pop()
        if s in seen:
            continue
        seen.add(s)
        stack.extend(deps.get(s, []))
    return seen


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

        # Files a stage is declared to produce at runtime (via a sheet-gated
        # file_exists validation) are not expected on disk now — suppress the
        # not-found warning for them on cadenzas (#137 point 5). Whether they
        # are produced EARLY ENOUGH is V109's job, not this disk check's.
        produced = set(_infer_producers(config))

        for item in config.sheet.prelude:
            # Prelude injects before any sheet runs — no producer can precede
            # it, so no suppression applies.
            issues.extend(self._check_item(item, "prelude", config, config_path, raw_yaml, set()))
        for sheet_num, items in config.sheet.cadenzas.items():
            for item in items:
                issues.extend(
                    self._check_item(
                        item,
                        f"cadenza (sheet {sheet_num})",
                        config,
                        config_path,
                        raw_yaml,
                        produced,
                    )
                )

        return issues

    def _check_item(
        self,
        item: InjectionItem,
        source: str,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
        produced_basenames: set[str],
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
            if Path(file_val).name in produced_basenames:
                # A prior (or later) stage is declared to produce this file at
                # runtime, so its absence on disk now is expected, not a defect.
                # V109 judges whether the producer runs early enough.
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


class CadenzaOrderingCheck:
    """V109: a cadenza reads a file whose producer runs later (#137).

    No new config — uses three existing signals:
    - CONSUMER: ``cadenzas`` (which sheet injects/reads which file).
    - PRODUCER (proxy): a ``file_exists`` validation gated to a sheet is
      the author declaring that sheet produces the file.
    - ORDER: the dependency DAG (which sheet is guaranteed to run first).

    Warns when a cadenza on sheet M reads a file F that some sheet P is
    declared (via file_exists) to produce, but P is NOT a DAG-ancestor of
    M — so F is not guaranteed to exist when M's cadenza fires. WARNING
    only: file_exists is a proxy, so we never false-ERROR a valid score.
    """

    @property
    def check_id(self) -> str:
        return "V109"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Detects a cadenza reading a file produced by a later stage"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        producers = _infer_producers(config)
        if not producers:
            return issues

        deps = config.sheet.dependencies

        for reader_sheet, items in config.sheet.cadenzas.items():
            for item in items:
                if item.file is None:  # directory cadenzas have no single file
                    continue
                name = Path(item.file).name
                producing_sheets = producers.get(name)
                if not producing_sheets:
                    continue  # no producer signal — V108 covers disk existence
                ancestors = _dag_ancestors(reader_sheet, deps)
                # Safe iff SOME producer is guaranteed to run before the
                # reader (a strict DAG ancestor).
                if producing_sheets & ancestors:
                    continue
                latest = max(producing_sheets)
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Sheet {reader_sheet}'s cadenza reads "
                            f"'{item.file}', but the stage that produces it "
                            f"(sheet {latest}, per its file_exists check) is "
                            f"not guaranteed to run first — the file may not "
                            f"exist yet when this cadenza fires."
                        ),
                        line=find_line_in_yaml(raw_yaml, item.file),
                        context=item.file,
                        suggestion=(
                            f"Add a dependency so the producing stage runs "
                            f"before sheet {reader_sheet}, or move the "
                            f"producer earlier."
                        ),
                    )
                )

        return issues
