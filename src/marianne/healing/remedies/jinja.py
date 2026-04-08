"""Jinja template remedies for self-healing.

Provides suggested fixes for Jinja template errors:
- SuggestJinjaFixRemedy: Suggests fixes for common template issues
"""

import difflib
import re
from typing import TYPE_CHECKING

from marianne.healing.diagnosis import Diagnosis
from marianne.healing.remedies.base import BaseRemedy, RemedyCategory, RemedyResult, RiskLevel

if TYPE_CHECKING:
    from marianne.healing.context import ErrorContext


# Known Jinja template variables in Mzt configs
KNOWN_VARIABLES = [
    "sheet_num",
    "total_sheets",
    "sheet_size",
    "start_item",
    "end_item",
    "total_items",
    "workspace",
    "job_name",
    "job_id",
    "timestamp",
]


class SuggestJinjaFixRemedy(BaseRemedy):
    """Suggests fixes for Jinja template errors.

    Triggers when:
    - Error relates to Jinja template rendering
    - Error message contains template syntax or undefined variable info

    This is a SUGGESTED remedy because:
    - Fixing templates requires modifying config files
    - User should verify the suggested fix is correct
    """

    @property
    def name(self) -> str:
        return "suggest_jinja_fix"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.SUGGESTED

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.MEDIUM

    @property
    def description(self) -> str:
        return "Suggests fixes for Jinja template syntax errors and typos"

    def diagnose(self, context: "ErrorContext") -> Diagnosis | None:
        """Check for Jinja template issues."""
        # Check for Jinja-related error codes
        jinja_codes = ("E304", "E305", "E201")  # Template errors
        # Also check message patterns
        if (
            context.error_code not in jinja_codes
            and not self._is_jinja_error(context.error_message)
        ):
            return None

        # Try to identify specific Jinja issues
        diagnosis = self._diagnose_syntax_error(context)
        if diagnosis:
            return diagnosis

        diagnosis = self._diagnose_undefined_variable(context)
        if diagnosis:
            return diagnosis

        diagnosis = self._diagnose_unclosed_block(context)
        if diagnosis:
            return diagnosis

        return None

    def _is_jinja_error(self, message: str) -> bool:
        """Check if error message indicates a Jinja problem."""
        patterns = [
            r"jinja",
            r"template.*error",
            r"template.*syntax",
            r"undefined.*variable",
            r"unexpected.*end.*template",
            r"expected.*token",
            r"\{\{.*\}\}",
            r"\{%.*%\}",
        ]
        message_lower = message.lower()
        return any(re.search(p, message_lower, re.IGNORECASE) for p in patterns)

    def _diagnose_syntax_error(self, context: "ErrorContext") -> Diagnosis | None:
        """Diagnose Jinja syntax errors."""
        # Look for syntax error patterns
        syntax_patterns = [
            r"unexpected.*'([^']+)'.*expected.*'([^']+)'",
            r"expected.*token.*'([^']+)'.*got.*'([^']+)'",
        ]

        for pattern in syntax_patterns:
            match = re.search(pattern, context.error_message, re.IGNORECASE)
            if match:
                got, expected = match.groups()
                return Diagnosis(
                    error_code=context.error_code,
                    issue=f"Jinja syntax error: unexpected '{got}'",
                    explanation=f"The template parser expected '{expected}' but found '{got}'.",
                    suggestion=f"Check the template near '{got}' and ensure proper syntax.",
                    confidence=0.80,
                    remedy_name=self.name,
                    requires_confirmation=True,
                    context={
                        "error_type": "syntax",
                        "got": got,
                        "expected": expected,
                    },
                )

        return None

    def _diagnose_undefined_variable(self, context: "ErrorContext") -> Diagnosis | None:
        """Diagnose undefined variable errors with typo suggestions."""
        # Look for undefined variable pattern
        patterns = [
            r"'([a-zA-Z_][a-zA-Z0-9_]*)' is undefined",
            r"undefined.*variable.*'([a-zA-Z_][a-zA-Z0-9_]*)'",
            r"variable '([a-zA-Z_][a-zA-Z0-9_]*)' not found",
        ]

        undefined_var = None
        for pattern in patterns:
            match = re.search(pattern, context.error_message, re.IGNORECASE)
            if match:
                undefined_var = match.group(1)
                break

        if not undefined_var:
            return None

        # Try to find a close match in known variables
        close_matches = difflib.get_close_matches(
            undefined_var, KNOWN_VARIABLES, n=1, cutoff=0.6
        )

        if close_matches:
            suggested = close_matches[0]
            return Diagnosis(
                error_code=context.error_code,
                issue=f"Undefined variable: '{undefined_var}'",
                explanation=(
                    f"The variable '{undefined_var}' is not defined"
                    f" in the template context."
                ),
                suggestion=(
                    f"Did you mean '{suggested}'?"
                    f" Replace '{undefined_var}' with '{suggested}'."
                ),
                confidence=0.85,  # High confidence for typo matches
                remedy_name=self.name,
                requires_confirmation=True,
                context={
                    "error_type": "undefined_variable",
                    "undefined_var": undefined_var,
                    "suggested_var": suggested,
                },
            )
        else:
            return Diagnosis(
                error_code=context.error_code,
                issue=f"Undefined variable: '{undefined_var}'",
                explanation=f"The variable '{undefined_var}' is not defined. "
                f"Available variables: {', '.join(KNOWN_VARIABLES)}",
                suggestion="Check spelling or ensure the variable is defined.",
                confidence=0.70,
                remedy_name=self.name,
                requires_confirmation=True,
                context={
                    "error_type": "undefined_variable",
                    "undefined_var": undefined_var,
                    "available_vars": KNOWN_VARIABLES,
                },
            )

    def _diagnose_unclosed_block(self, context: "ErrorContext") -> Diagnosis | None:
        """Diagnose unclosed Jinja blocks."""
        # Look for unclosed block patterns
        patterns = [
            r"unexpected.*end.*template",
            r"expected.*'end(\w+)'",
            r"unclosed.*\{[{%]",
            r"missing.*closing.*([}%]\}?)",
        ]

        for pattern in patterns:
            match = re.search(pattern, context.error_message, re.IGNORECASE)
            if match:
                block_type = match.group(1) if match.lastindex else "block"
                return Diagnosis(
                    error_code=context.error_code,
                    issue=f"Unclosed Jinja {block_type}",
                    explanation="A Jinja block or expression is not properly closed.",
                    suggestion=(
                        f"Check for missing closing tags like"
                        f" {{% end{block_type} %}} or }}."
                    ),
                    confidence=0.75,
                    remedy_name=self.name,
                    requires_confirmation=True,
                    context={
                        "error_type": "unclosed_block",
                        "block_type": block_type,
                    },
                )

        return None

    def preview(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if diagnosis:
            return diagnosis.suggestion
        return "Analyze and suggest Jinja template fixes"

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """This remedy only suggests - user must manually fix.

        The actual fix requires modifying the config file, which
        should be done by the user or with their explicit approval.
        """
        diagnosis = self.diagnose(context)
        if not diagnosis:
            return RemedyResult(
                success=False,
                message="Could not diagnose Jinja error",
                action_taken="nothing",
            )

        # For typo fixes with high confidence, we could offer to fix
        # But for now, this is guidance-only
        return RemedyResult(
            success=True,
            message=f"Suggested fix: {diagnosis.suggestion}",
            action_taken="suggestion provided",
        )

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if not diagnosis:
            return "A Jinja template error occurred but could not be diagnosed."

        lines = [
            f"Jinja Template Error: {diagnosis.issue}",
            "",
            f"Explanation: {diagnosis.explanation}",
            "",
            f"Suggested Fix: {diagnosis.suggestion}",
            "",
        ]

        ctx = diagnosis.context
        if ctx.get("error_type") == "undefined_variable":
            if ctx.get("suggested_var"):
                lines.extend([
                    "To fix this in your config file:",
                    f"  Replace: {{{{ {ctx['undefined_var']} }}}}",
                    f"  With:    {{{{ {ctx['suggested_var']} }}}}",
                ])
            elif ctx.get("available_vars"):
                lines.extend([
                    "Available variables:",
                    *[f"  - {v}" for v in ctx["available_vars"]],
                ])
        elif ctx.get("error_type") == "unclosed_block":
            lines.extend([
                "Common fixes:",
                "  - Check for missing }} to close {{ expressions",
                "  - Check for missing %} to close {% statements",
                "  - Ensure {% for %} has {% endfor %}",
                "  - Ensure {% if %} has {% endif %}",
            ])

        return "\n".join(lines)
