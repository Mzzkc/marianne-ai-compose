"""Checks that reason over the parsed, expanded score structure."""

from __future__ import annotations

import re
from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.constants import SHEET_NUM_KEY, TERMINOLOGY_ALIASES
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks.paths import WorkspaceParentExistsCheck

_IDENTITY_VARIABLES = frozenset(
    {
        # Sentinel's 102 persistent-agent scores receive these at engagement setup.
        "agent_name",
        "role",
        "focus",
        "agent_voice",
        "agent_identity_dir",
    }
)
_BUILTINS = frozenset(
    {
        "workspace",
        SHEET_NUM_KEY,
        "total_sheets",
        "score_dir",
        "instance",
        "stage",
        "movement",
        "voice",
        "start_item",
        "end_item",
        "instrument_name",
        *TERMINOLOGY_ALIASES.keys(),
        *TERMINOLOGY_ALIASES.values(),
    }
)
_TOKEN = re.compile(r"(?<![\{$])\{([a-z][a-z0-9_]*)\}(?!\})")


class DependencyCycleCheck:
    check_id = "V209"
    severity = ValidationSeverity.ERROR
    description = "Dependency graph is acyclic"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        deps = config.sheet.dependencies
        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(node: int, path: list[int]) -> list[int] | None:
            if node in visiting:
                return path[path.index(node) :] + [node]
            if node in visited:
                return None
            visiting.add(node)
            for target in deps.get(node, []):
                cycle = visit(target, path + [target])
                if cycle:
                    return cycle
            visiting.remove(node)
            visited.add(node)
            return None

        for node in range(1, config.sheet.total_sheets + 1):
            cycle = visit(node, [node])
            if cycle:
                return [
                    ValidationIssue(
                        self.check_id,
                        self.severity,
                        f"Dependency cycle: {' -> '.join(map(str, cycle))}",
                    )
                ]
        return []


class FanOutCoherenceCheck:
    check_id = "V214"
    severity = ValidationSeverity.ERROR
    description = "Post-expansion sheet references are coherent"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        sheet = config.sheet
        total = sheet.total_sheets
        mappings: dict[str, set[int]] = {
            "dependencies": set(sheet.dependencies),
            "skip_when": set(sheet.skip_when),
            "prompt_extensions": set(sheet.prompt_extensions),
            "per_sheet_instruments": set(sheet.per_sheet_instruments),
            "per_sheet_fallbacks": set(sheet.per_sheet_fallbacks),
        }
        issues = []
        for name, mapping in mappings.items():
            for number in mapping:
                if number < 1 or number > total:
                    issues.append(
                        ValidationIssue(
                            self.check_id,
                            self.severity,
                            f"{name} sheet {number} is outside expanded range 1-{total}",
                        )
                    )
        for number, prerequisites in sheet.dependencies.items():
            for prerequisite in prerequisites:
                if prerequisite < 1 or prerequisite > total:
                    issues.append(
                        ValidationIssue(
                            self.check_id,
                            self.severity,
                            f"sheet {number} depends on missing sheet {prerequisite}",
                        )
                    )
        return issues


class CadenzaTargetCheck:
    check_id = "V216"
    severity = ValidationSeverity.ERROR
    description = "Cadenza targets exist after fan-out expansion"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        total = config.sheet.total_sheets
        return [
            ValidationIssue(
                self.check_id,
                self.severity,
                f"Cadenza targets sheet {number} outside expanded range 1-{total}",
            )
            for number in config.sheet.cadenzas
            if number < 1 or number > total
        ]


class ConcertTargetCheck:
    check_id = "V217"
    severity = ValidationSeverity.ERROR
    description = "Static concert targets exist and parse at depth one"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for hook in (*config.on_success, *config.on_failure):
            if hook.type != "run_job" or hook.job_path is None:
                continue
            source = str(hook.job_path)
            if "{" in source or "}" in source:
                continue  # runtime-produced target: cannot judge its existence now
            target = Path(source)
            if not target.is_absolute():
                target = config_path.resolve().parent / target
            if not target.is_file():
                issues.append(
                    ValidationIssue(
                        self.check_id, self.severity, f"Concert target does not exist: {target}"
                    )
                )
                continue
            try:
                child = JobConfig.from_yaml(target)  # depth one: never recurse through hooks
                for issue in WorkspaceParentExistsCheck().check(child, target, target.read_text()):
                    issues.append(ValidationIssue(
                        check_id=issue.check_id,
                        severity=ValidationSeverity.WARNING,
                        message=f"Concert target {target}: {issue.message} "
                                "(chain may create the workspace ancestor first)",
                    ))
            except (ValueError, OSError) as exc:
                issues.append(
                    ValidationIssue(
                        self.check_id,
                        self.severity,
                        f"Concert target is not a score: {target}: {exc}",
                    )
                )
        return issues


class VariableCoverageCheck:
    check_id = "V105"
    severity = ValidationSeverity.ERROR
    description = "Validation placeholders refer to declared variables or built-ins"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        declared = set(config.prompt.variables)
        referenced: set[str] = set()
        for rule in config.validations:
            values = [rule.path, rule.pattern, rule.working_directory]
            # Python f-strings and heredocs use the same brace shape. The
            # renderer cannot safely classify their local names as score vars.
            if rule.command and "<<" not in rule.command and "python" not in rule.command:
                values.append(rule.command)
            for value in values:
                if value:
                    referenced.update(_TOKEN.findall(value))
        unknown = referenced - declared - _BUILTINS - _IDENTITY_VARIABLES
        return [
            ValidationIssue(
                self.check_id, self.severity, f"Validation references undefined variable {{{name}}}"
            )
            for name in sorted(unknown)
        ]


class UnusedVariableCheck:
    check_id = "V110"
    severity = ValidationSeverity.INFO
    description = "Declared variables unused by score text"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        declared = {
            name
            for name in config.prompt.variables
            if name != "marianne_agent" and not name.startswith("_")
        }
        unused = [
            name
            for name in declared
            if not re.search(
                rf"(?<![a-zA-Z0-9_]){re.escape(name)}(?![a-zA-Z0-9_])",
                raw_yaml.replace(f"{name}:", "", 1),
            )
        ]
        return [
            ValidationIssue(
                self.check_id, self.severity, f"Declared variable '{name}' is not referenced"
            )
            for name in sorted(unused)
        ]


class AmbiguousFileReferenceCheck:
    check_id = "V218"
    severity = ValidationSeverity.INFO
    description = "Shell commands contain paths whose role is not statically known"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        # A shell command can read or write the same path. Emit only for an
        # explicit path token; ordinary prose and variable braces are ignored.
        return [
            ValidationIssue(
                self.check_id,
                self.severity,
                "command_succeeds contains a file path; check whether it is an input or output",
            )
            for rule in config.validations
            if rule.type == "command_succeeds"
            and rule.command
                and re.search(
                    r"(?:^|\s)(?:\./|\.\./)[\w./-]+\.(?:md|txt|yaml|yml|json|csv|py|sh)\b",
                    rule.command,
                )
        ]


class UnreachableSheetCheck:
    check_id = "V219"
    severity = ValidationSeverity.WARNING
    description = "Sheets blocked by impossible dependencies"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        total = config.sheet.total_sheets
        return [
            ValidationIssue(
                self.check_id,
                self.severity,
                f"Sheet {sheet} cannot run: dependency {dep} does not exist",
            )
            for sheet, prerequisites in config.sheet.dependencies.items()
            for dep in prerequisites
            if dep < 1 or dep > total
        ]
