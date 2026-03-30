"""Jinja template validation checks.

Validates Jinja2 template syntax and detects undefined variables that
would cause runtime errors.
"""

import difflib
from pathlib import Path

from mozart.core.logging import get_logger

_logger = get_logger("validation.jinja")

import jinja2
from jinja2 import meta as jinja2_meta
from jinja2 import nodes as jinja2_nodes

from mozart.core.config import JobConfig
from mozart.validation.base import ValidationIssue, ValidationSeverity
from mozart.validation.checks._helpers import find_line_in_yaml, resolve_path


class JinjaSyntaxCheck:
    """Check for Jinja template syntax errors (V001).

    Attempts to parse templates and reports syntax errors with
    line numbers and context. This catches issues like:
    - Unclosed blocks ({% if ... without {% endif %})
    - Unclosed expressions ({{ ... without }})
    - Invalid syntax inside blocks
    """

    @property
    def check_id(self) -> str:
        return "V001"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Validates Jinja template syntax"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check Jinja syntax in templates."""
        issues: list[ValidationIssue] = []
        env = jinja2.Environment()

        # Check inline template
        if config.prompt.template:
            template_issues = self._check_template(
                config.prompt.template,
                "prompt.template",
                find_line_in_yaml(raw_yaml, "template:"),
                env,
            )
            issues.extend(template_issues)

        # Check external template file
        if config.prompt.template_file:
            template_path = resolve_path(config.prompt.template_file, config_path)
            if template_path.exists():
                try:
                    template_content = template_path.read_text()
                    template_issues = self._check_template(
                        template_content,
                        f"template_file ({template_path.name})",
                        None,
                        env,
                    )
                    issues.extend(template_issues)
                except Exception as e:
                    issues.append(
                        ValidationIssue(
                            check_id=self.check_id,
                            severity=self.severity,
                            message=f"Could not read template file: {e}",
                            suggestion=f"Check file permissions for {template_path}",
                        )
                    )

        return issues

    def _check_template(
        self,
        template_str: str,
        source_name: str,
        yaml_line: int | None,
        env: jinja2.Environment,
    ) -> list[ValidationIssue]:
        """Check a single template string for syntax errors."""
        issues: list[ValidationIssue] = []

        try:
            env.parse(template_str)
        except jinja2.TemplateSyntaxError as e:
            # Extract context around the error
            context = self._extract_context(template_str, e.lineno or 1)

            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=f"Jinja syntax error in {source_name}: {e.message}",
                    line=yaml_line,
                    context=context,
                    suggestion=self._suggest_fix(e.message or "", context),
                    metadata={
                        "template_line": str(e.lineno or 1),
                        "source": source_name,
                    },
                )
            )

        return issues

    def _extract_context(self, template: str, line_num: int) -> str:
        """Extract context around the error line."""
        lines = template.split("\n")
        if 0 < line_num <= len(lines):
            line = lines[line_num - 1]
            # Truncate long lines
            if len(line) > 80:
                return line[:77] + "..."
            return line
        return ""

    def _suggest_fix(self, error_message: str, context: str) -> str:
        """Generate a fix suggestion based on the error."""
        error_lower = error_message.lower()

        if "unexpected end of template" in error_lower:
            if "{{" in context and "}}" not in context:
                return "Add closing '}}' to the expression"
            if "{%" in context and "%}" not in context:
                return "Add closing '%}' to the block"
            return "Check for unclosed blocks or expressions"

        if "expected token" in error_lower:
            return f"Check syntax near: {context[:40]}..."

        if "unexpected" in error_lower:
            return "Review Jinja syntax - possible typo or invalid expression"

        return "Check Jinja2 template syntax"


class JinjaUndefinedVariableCheck:
    """Check for undefined template variables (V101).

    Warns about variables used in templates that aren't defined in
    the config's variables section or the built-in sheet context.
    Uses fuzzy matching to suggest corrections for typos.
    """

    # Built-in variables always available in sheet context
    BUILTIN_VARIABLES = frozenset({
        "sheet_num",
        "total_sheets",
        "start_item",
        "end_item",
        "workspace",
        "stakes",
        "thinking_method",
        # Fan-out variables (populated when fan_out is configured)
        "stage",
        "instance",
        "fan_count",
        "total_stages",
        # Cross-sheet context variables
        "previous_outputs",
        "previous_files",
        # Jinja built-ins
        "loop",
        "self",
        "range",
        "dict",
        "lipsum",
        "cycler",
        "joiner",
        "namespace",
    })

    @property
    def check_id(self) -> str:
        return "V101"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Detects undefined template variables"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check for undefined variables in templates."""
        issues: list[ValidationIssue] = []
        env = jinja2.Environment()

        # Collect all defined variables
        defined_vars = set(self.BUILTIN_VARIABLES)
        defined_vars.update(config.prompt.variables.keys())

        # Check inline template
        if config.prompt.template:
            var_issues = self._check_undefined_vars(
                config.prompt.template,
                "prompt.template",
                defined_vars,
                env,
            )
            issues.extend(var_issues)

        # Check external template file
        if config.prompt.template_file:
            template_path = resolve_path(config.prompt.template_file, config_path)
            if template_path.exists():
                try:
                    template_content = template_path.read_text()
                    var_issues = self._check_undefined_vars(
                        template_content,
                        f"template_file ({template_path.name})",
                        defined_vars,
                        env,
                    )
                    issues.extend(var_issues)
                except Exception as exc:
                    issues.append(ValidationIssue(
                        check_id="V101",
                        severity=ValidationSeverity.WARNING,
                        message=f"Cannot read template file '{template_path.name}' "
                                f"for undefined variable check: {exc}",
                        suggestion="Ensure the template file is readable.",
                    ))

        return issues

    @staticmethod
    def _extract_template_declared_vars(
        ast: jinja2_nodes.Template,
    ) -> set[str]:
        """Extract variables declared within the template via {% set %} and {% for %}.

        jinja2_meta.find_undeclared_variables has a known limitation: it doesn't
        properly track variables declared in conditional branches ({% if %}/{% elif %}).
        A variable declared via {% set char = ... %} inside an {% elif %} block
        may still be reported as undeclared. This method walks the AST to find
        all template-local variable declarations, supplementing the meta module.
        """
        declared: set[str] = set()

        def _extract_names(target: jinja2_nodes.Node) -> None:
            if isinstance(target, jinja2_nodes.Name):
                declared.add(target.name)
            elif isinstance(target, jinja2_nodes.Tuple):
                for item in target.items:
                    _extract_names(item)

        for node in ast.find_all((jinja2_nodes.Assign, jinja2_nodes.For)):
            if isinstance(node, (jinja2_nodes.Assign, jinja2_nodes.For)):
                _extract_names(node.target)

        return declared

    def _check_undefined_vars(
        self,
        template_str: str,
        source_name: str,
        defined_vars: set[str],
        env: jinja2.Environment,
    ) -> list[ValidationIssue]:
        """Check a template for undefined variables."""
        issues: list[ValidationIssue] = []

        try:
            ast = env.parse(template_str)
            used_vars = jinja2_meta.find_undeclared_variables(ast)

            # F-069: Supplement find_undeclared_variables with AST-based
            # detection of template-local declarations ({% set %}, {% for %}).
            template_declared = self._extract_template_declared_vars(ast)

            for var in used_vars:
                if var not in defined_vars and var not in template_declared:
                    suggestion = self._suggest_similar_var(var, defined_vars)
                    issues.append(
                        ValidationIssue(
                            check_id=self.check_id,
                            severity=self.severity,
                            message=f"Undefined variable '{var}' in {source_name}",
                            suggestion=suggestion,
                            metadata={
                                "variable": var,
                                "source": source_name,
                            },
                        )
                    )

        except jinja2.TemplateSyntaxError:
            pass  # Syntax errors handled by V001

        return issues

    def _suggest_similar_var(
        self,
        var: str,
        defined_vars: set[str],
    ) -> str:
        """Suggest a similar variable name using fuzzy matching."""
        # Find close matches
        matches = difflib.get_close_matches(var, defined_vars, n=1, cutoff=0.6)

        if matches:
            return f"Did you mean '{matches[0]}'?"

        # List available variables
        available = sorted(defined_vars - self.BUILTIN_VARIABLES)
        if available:
            if len(available) <= 5:
                return f"Available variables: {', '.join(available)}"
            return f"Define '{var}' in prompt.variables section"

        return f"Define '{var}' in prompt.variables section"
