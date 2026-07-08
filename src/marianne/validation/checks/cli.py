"""Static checks for raw shell prompt contracts."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import jinja2

from marianne.core.config import JobConfig, PromptConfig
from marianne.core.config.instruments import InstrumentProfile
from marianne.core.sheet import Sheet, build_sheets
from marianne.prompts.templating import PromptBuilder, SheetContext
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml, resolve_path


class CliRawPromptBashCheck:
    """Render raw-prompt shell sheets and check the result before execution."""

    _SHELLS = frozenset({"bash", "sh", "dash", "zsh", "ksh"})
    _FENCE_RE = re.compile(r"^\s*```")
    _BULLET_RE = re.compile(r"^\s*[-*]\s+[`A-Za-z0-9]")
    _MARKDOWN_HEADING_RE = re.compile(r"^\s*#{2,6}\s+\S")
    _ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*[+]?=")
    _HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")

    @property
    def check_id(self) -> str:
        return "V307"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Renders raw CLI bash prompts and validates shell syntax"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check every resolved raw-prompt shell sheet."""
        try:
            from marianne.instruments.loader import load_all_profiles

            profiles = load_all_profiles()
        except Exception:
            return []

        issues: list[ValidationIssue] = []
        for sheet in build_sheets(config):
            profile = profiles.get(sheet.instrument_name)
            if not self._is_raw_shell_profile(profile):
                continue

            issues.extend(self._check_fallback_chain(sheet, profiles, raw_yaml))

            rendered, render_issue = self._render_raw_prompt(
                config,
                config_path,
                raw_yaml,
                sheet,
            )
            if render_issue is not None:
                issues.append(render_issue)
                continue
            if rendered is None:
                continue

            markdown_issues = self._check_markdown_markers(rendered, sheet, raw_yaml)
            issues.extend(markdown_issues)
            if markdown_issues:
                continue
            assert profile is not None
            issues.extend(self._check_bash_syntax(rendered, sheet, profile, raw_yaml))

        return issues

    @classmethod
    def _is_raw_shell_profile(cls, profile: InstrumentProfile | None) -> bool:
        if profile is None or not profile.raw_prompt or profile.cli is None:
            return False
        executable = Path(profile.cli.command.executable).name
        return executable in cls._SHELLS

    def _check_fallback_chain(
        self,
        sheet: Sheet,
        profiles: dict[str, InstrumentProfile],
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        non_raw_fallbacks: list[str] = []
        seen: set[str] = set()
        for fallback in sheet.instrument_fallbacks:
            if fallback in seen:
                continue
            seen.add(fallback)
            fallback_profile = profiles.get(fallback)
            if fallback_profile is None:
                continue
            if self._is_raw_shell_profile(fallback_profile):
                continue
            non_raw_fallbacks.append(fallback)
        if not non_raw_fallbacks:
            return []

        fallback_list = ", ".join(f"'{name}'" for name in non_raw_fallbacks)
        return [
            ValidationIssue(
                check_id=self.check_id,
                severity=ValidationSeverity.WARNING,
                message=(
                    f"Sheet {sheet.num} uses raw shell instrument "
                    f"'{sheet.instrument_name}' but can fall back to "
                    f"non-raw instruments {fallback_list}. A rendered bash "
                    "script must not be silently handed to an LLM-style "
                    "instrument."
                ),
                line=(
                    find_line_in_yaml(raw_yaml, "per_sheet_fallbacks:")
                    or find_line_in_yaml(raw_yaml, "instrument_fallbacks:")
                ),
                suggestion=(
                    "Set this sheet's fallback chain to [] or to another "
                    "raw shell instrument. Use an explicit later AI sheet "
                    "for interpretation or repair."
                ),
                metadata={
                    "sheet": str(sheet.num),
                    "instrument": sheet.instrument_name,
                    "fallbacks": ",".join(non_raw_fallbacks),
                },
            )
        ]

    def _render_raw_prompt(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
        sheet: Sheet,
    ) -> tuple[str | None, ValidationIssue | None]:
        prompt_config = config.prompt
        if config.prompt.template_file is not None:
            template_path = resolve_path(config.prompt.template_file, config_path)
            if not template_path.exists() or not template_path.is_file():
                return None, None
            prompt_config = PromptConfig(
                template=template_path.read_text(),
                variables=dict(config.prompt.variables),
                stakes=config.prompt.stakes,
                thinking_method=config.prompt.thinking_method,
                prompt_extensions=list(config.prompt.prompt_extensions),
            )

        total_sheets = config.sheet.total_sheets
        total_movements = config.sheet.total_stages
        start_item = (sheet.num - 1) * config.sheet.size + config.sheet.start_item
        end_item = min(start_item + config.sheet.size - 1, config.sheet.total_items)
        context = SheetContext(
            sheet_num=sheet.num,
            total_sheets=total_sheets,
            start_item=start_item,
            end_item=end_item,
            workspace=config.workspace,
            instrument_name=sheet.instrument_name,
            stage=sheet.movement,
            instance=sheet.voice or 1,
            fan_count=sheet.voice_count,
            total_stages=total_movements,
            previous_outputs=dict.fromkeys(range(1, sheet.num), ""),
            previous_files={},
        )

        try:
            return (
                PromptBuilder(prompt_config).build_sheet_prompt(
                    context,
                    raw_prompt=True,
                ),
                None,
            )
        except jinja2.TemplateError as exc:
            return None, ValidationIssue(
                check_id=self.check_id,
                severity=self.severity,
                message=f"Sheet {sheet.num} raw shell prompt could not render: {exc}",
                line=self._template_line(raw_yaml, config),
                suggestion=(
                    "Fix the Jinja template so the raw CLI prompt can be "
                    "rendered and statically checked before execution."
                ),
                metadata={"sheet": str(sheet.num), "error": str(exc)},
            )

    def _check_markdown_markers(
        self,
        rendered: str,
        sheet: Sheet,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for line_num, line in self._iter_non_heredoc_lines(rendered):
            kind = self._markdown_marker_kind(line)
            if kind is None:
                continue
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"Sheet {sheet.num} raw CLI prompt renders {kind} on "
                        f"line {line_num}; the cli instrument passes this text "
                        "to a shell, not to an LLM."
                    ),
                    line=find_line_in_yaml(raw_yaml, "template:"),
                    context=line.strip()[:120],
                    suggestion=(
                        "Replace markdown/prose with executable bash, or move "
                        "the prose instructions to a non-cli sheet."
                    ),
                    metadata={
                        "sheet": str(sheet.num),
                        "rendered_line": str(line_num),
                        "marker": kind,
                    },
                )
            )
            break
        return issues

    def _check_bash_syntax(
        self,
        rendered: str,
        sheet: Sheet,
        profile: InstrumentProfile,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        executable = profile.cli.command.executable if profile.cli else "bash"
        command = shutil.which(executable) or executable
        try:
            result = subprocess.run(
                [command, "-n"],
                input=rendered,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"Could not run '{executable} -n' for sheet "
                        f"{sheet.num}: {exc}"
                    ),
                    line=find_line_in_yaml(raw_yaml, "instrument:"),
                    suggestion="Install the shell named by the raw CLI profile.",
                    metadata={"sheet": str(sheet.num), "shell": executable},
                )
            ]

        if result.returncode == 0:
            return []

        stderr = result.stderr.strip() or result.stdout.strip()
        first_error = stderr.splitlines()[0] if stderr else "shell syntax error"
        return [
            ValidationIssue(
                check_id=self.check_id,
                severity=self.severity,
                message=(
                    f"Sheet {sheet.num} raw CLI prompt does not parse with "
                    f"'{executable} -n': {first_error}"
                ),
                line=find_line_in_yaml(raw_yaml, "template:"),
                suggestion=(
                    "Fix the rendered bash template. Run the rendered command "
                    "through 'bash -n' before execution."
                ),
                metadata={
                    "sheet": str(sheet.num),
                    "shell": executable,
                    "returncode": str(result.returncode),
                    "stderr": stderr[:500],
                },
            )
        ]

    def _markdown_marker_kind(self, line: str) -> str | None:
        stripped = line.strip()
        if not stripped:
            return None
        if self._FENCE_RE.match(line):
            return "a markdown code fence"
        if self._BULLET_RE.match(line):
            return "a markdown/prose bullet"
        if self._MARKDOWN_HEADING_RE.match(line):
            return "a markdown heading"
        if self._looks_like_prose(stripped):
            return "plain prose"
        return None

    def _looks_like_prose(self, stripped: str) -> bool:
        if stripped.startswith(("#", ":", "}", "{", "|", "&")):
            return False
        if self._ASSIGNMENT_RE.match(stripped):
            return False
        if stripped.startswith(("if ", "then", "elif ", "else", "fi", "for ", "while ")):
            return False
        if stripped.startswith(("case ", "esac", "do", "done", "function ")):
            return False
        if stripped.startswith(("echo ", "printf ", "cat ", "grep ", "sed ", "awk ")):
            return False
        if stripped.startswith(("python ", "python3 ", "pytest ", "ruff ", "uv ", "mzt ")):
            return False
        return bool(re.match(r"^[A-Z][A-Za-z0-9 ,;:'\"()/.-]{8,}[.!?:]?$", stripped))

    def _iter_non_heredoc_lines(self, rendered: str) -> list[tuple[int, str]]:
        lines: list[tuple[int, str]] = []
        delimiter: str | None = None
        allow_tabs = False
        for line_num, line in enumerate(rendered.splitlines(), 1):
            compare = line.lstrip("\t") if allow_tabs else line
            if delimiter is not None:
                if compare.strip() == delimiter:
                    delimiter = None
                    allow_tabs = False
                continue
            lines.append((line_num, line))
            match = self._HEREDOC_RE.search(line)
            if match:
                delimiter = match.group(1)
                allow_tabs = "<<-" in match.group(0)
        return lines

    @staticmethod
    def _template_line(raw_yaml: str, config: JobConfig) -> int | None:
        if config.prompt.template_file is not None:
            return find_line_in_yaml(raw_yaml, "template_file:")
        return find_line_in_yaml(raw_yaml, "template:")


class FanOutAssignmentCoverageCheck:
    """Warn when fan-out voices get only partial concrete instrument coverage."""

    @property
    def check_id(self) -> str:
        return "V308"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns on partial concrete instrument coverage inside fan-out movements"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        if not config.sheet.fan_out_stage_map:
            return []

        assigned_sheets = set(config.sheet.per_sheet_instruments)
        for sheet_nums in config.sheet.instrument_map.values():
            assigned_sheets.update(sheet_nums)
        if not assigned_sheets:
            return []

        stages: dict[int, set[int]] = {}
        for sheet_num, meta in config.sheet.fan_out_stage_map.items():
            if meta["fan_count"] <= 1:
                continue
            stages.setdefault(meta["stage"], set()).add(sheet_num)

        issues: list[ValidationIssue] = []
        for stage, sheets in sorted(stages.items()):
            covered = sheets & assigned_sheets
            if not covered or covered == sheets:
                continue
            missing = sheets - covered
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"Fan-out movement {stage} has partial concrete "
                        f"instrument coverage: assigned sheets "
                        f"{self._format_sheets(covered)}, while sibling "
                        f"sheets {self._format_sheets(missing)} inherit a "
                        "different assignment path."
                    ),
                    line=(
                        find_line_in_yaml(raw_yaml, "per_sheet_instruments:")
                        or find_line_in_yaml(raw_yaml, "instrument_map:")
                    ),
                    suggestion=(
                        "If one instrument should cover the whole fan-out "
                        "movement, assign every expanded sheet or use a "
                        "movement-level instrument. If mixed voices are "
                        "intentional, keep the warning as documented intent."
                    ),
                    metadata={
                        "movement": str(stage),
                        "assigned_sheets": ",".join(str(s) for s in sorted(covered)),
                        "missing_sheets": ",".join(str(s) for s in sorted(missing)),
                    },
                )
            )
        return issues

    @staticmethod
    def _format_sheets(sheets: set[int]) -> str:
        return ", ".join(str(sheet) for sheet in sorted(sheets))


class PromptValidationContractCheck:
    """Warn when exact section-label validations are absent from the prompt."""

    _SECTION_LABEL_RE = re.compile(r"^(?:#{1,6}\s+\S.+|[A-Z][A-Z0-9 _/-]{1,80}:)$")

    @property
    def check_id(self) -> str:
        return "V309"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks exact section-label validations are taught in the prompt"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        template = self._template_source(config, config_path)
        if template is None:
            return []

        issues: list[ValidationIssue] = []
        for index, validation in enumerate(config.validations, 1):
            if validation.type != "content_contains" or not validation.pattern:
                continue
            pattern = validation.pattern.strip()
            if not self._looks_like_section_label(pattern):
                continue
            if pattern in template:
                continue
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"Validation rule {index} requires exact section "
                        f"label '{pattern}', but the prompt template does not "
                        "contain that literal label."
                    ),
                    line=find_line_in_yaml(raw_yaml, "validations:"),
                    suggestion=(
                        "Add the exact required section label to the prompt, "
                        "or adjust the validation pattern to match the artifact "
                        "contract the sheet actually gives the agent."
                    ),
                    metadata={
                        "validation_index": str(index),
                        "pattern": pattern,
                    },
                )
            )
        return issues

    @classmethod
    def _looks_like_section_label(cls, pattern: str) -> bool:
        if "\n" in pattern or "{{" in pattern or "}}" in pattern:
            return False
        return bool(cls._SECTION_LABEL_RE.match(pattern))

    @staticmethod
    def _template_source(config: JobConfig, config_path: Path) -> str | None:
        if config.prompt.template is not None:
            return config.prompt.template
        if config.prompt.template_file is None:
            return None
        template_path = resolve_path(config.prompt.template_file, config_path)
        if not template_path.exists() or not template_path.is_file():
            return None
        try:
            return template_path.read_text()
        except OSError:
            return None
