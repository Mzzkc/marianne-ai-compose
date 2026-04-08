"""Diagnostic-only remedies for self-healing.

These remedies cannot automatically fix issues but provide
detailed guidance for manual resolution:
- DiagnoseAuthErrorRemedy: Diagnoses authentication failures
- DiagnoseMissingCLIRemedy: Diagnoses missing Claude CLI
"""

import re
import shutil
from typing import TYPE_CHECKING

from marianne.healing.diagnosis import Diagnosis
from marianne.healing.remedies.base import BaseRemedy, RemedyCategory, RemedyResult, RiskLevel

if TYPE_CHECKING:
    from marianne.healing.context import ErrorContext


class DiagnoseAuthErrorRemedy(BaseRemedy):
    """Diagnoses authentication failures.

    Triggers when:
    - Error relates to API key or authentication
    - Error code indicates auth failure

    This is DIAGNOSTIC only because:
    - We cannot create API keys
    - User must configure authentication themselves
    """

    @property
    def name(self) -> str:
        return "diagnose_auth_error"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.DIAGNOSTIC

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW  # Diagnostic only

    @property
    def description(self) -> str:
        return "Diagnoses authentication and API key issues"

    def diagnose(self, context: "ErrorContext") -> Diagnosis | None:
        """Check for authentication-related errors."""
        # Check for auth-related error codes
        auth_codes = ("E101", "E102", "E401", "E403")  # Rate limit / auth codes
        if context.error_code in auth_codes:
            return self._diagnose_from_code(context)

        # Check message patterns
        auth_patterns = [
            r"api.?key",
            r"auth.*failed",
            r"authentication",
            r"unauthorized",
            r"invalid.*key",
            r"missing.*key",
            r"401",
            r"403",
            r"ANTHROPIC_API_KEY",
        ]

        message_lower = context.error_message.lower()
        if any(re.search(p, message_lower, re.IGNORECASE) for p in auth_patterns):
            return self._diagnose_from_message(context)

        return None

    def _diagnose_from_code(self, context: "ErrorContext") -> Diagnosis:
        """Diagnose based on error code."""
        import os

        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

        if context.error_code == "E101":
            return Diagnosis(
                error_code=context.error_code,
                issue="Rate limit exceeded",
                explanation="The Anthropic API rate limit has been reached. "
                "This typically happens with high usage.",
                suggestion="Wait for the rate limit to reset (usually 1 minute) and retry.",
                confidence=0.90,
                remedy_name=self.name,
                context={"auth_type": "rate_limit"},
            )
        elif context.error_code in ("E401", "E102"):
            return Diagnosis(
                error_code=context.error_code,
                issue="Authentication failed" if has_key else "API key not set",
                explanation="API key is invalid or not configured." if has_key
                else "The ANTHROPIC_API_KEY environment variable is not set.",
                suggestion="Verify your API key at https://console.anthropic.com" if has_key
                else "Set ANTHROPIC_API_KEY environment variable.",
                confidence=0.95,
                remedy_name=self.name,
                context={"auth_type": "invalid_key" if has_key else "missing_key"},
            )
        else:
            return Diagnosis(
                error_code=context.error_code,
                issue="Authorization failed",
                explanation="Access denied to the Anthropic API.",
                suggestion="Check your API key permissions and account status.",
                confidence=0.80,
                remedy_name=self.name,
                context={"auth_type": "forbidden"},
            )

    def _diagnose_from_message(self, context: "ErrorContext") -> Diagnosis:
        """Diagnose based on error message patterns."""
        import os

        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        message_lower = context.error_message.lower()

        if "rate" in message_lower or "limit" in message_lower:
            return Diagnosis(
                error_code=context.error_code,
                issue="Rate limit exceeded",
                explanation="API rate limit reached.",
                suggestion="Wait and retry. Consider reducing request frequency.",
                confidence=0.85,
                remedy_name=self.name,
                context={"auth_type": "rate_limit"},
            )
        elif not has_key:
            return Diagnosis(
                error_code=context.error_code,
                issue="API key not configured",
                explanation="ANTHROPIC_API_KEY environment variable not found.",
                suggestion="Set your API key: export ANTHROPIC_API_KEY='your-key'",
                confidence=0.90,
                remedy_name=self.name,
                context={"auth_type": "missing_key"},
            )
        else:
            return Diagnosis(
                error_code=context.error_code,
                issue="Authentication error",
                explanation="API authentication failed.",
                suggestion="Verify your API key is valid and active.",
                confidence=0.75,
                remedy_name=self.name,
                context={"auth_type": "unknown"},
            )

    def preview(self, context: "ErrorContext") -> str:
        return "Diagnose authentication issue (no automatic fix available)"

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """Diagnostic only - no automatic fix."""
        self.diagnose(context)
        return RemedyResult(
            success=True,
            message="Diagnostic information provided",
            action_taken="diagnosis only",
        )

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if not diagnosis:
            return "An authentication error occurred but could not be diagnosed."

        lines = [
            f"Authentication Error: {diagnosis.issue}",
            "",
            f"Explanation: {diagnosis.explanation}",
            "",
            f"Suggested Fix: {diagnosis.suggestion}",
            "",
        ]

        auth_type = diagnosis.context.get("auth_type")

        if auth_type == "missing_key":
            lines.extend([
                "To set up your API key:",
                "",
                "1. Get an API key from: https://console.anthropic.com/settings/keys",
                "",
                "2. Set the environment variable:",
                "   Linux/macOS: export ANTHROPIC_API_KEY='sk-ant-...'",
                "   Windows:     set ANTHROPIC_API_KEY=sk-ant-...",
                "",
                "3. For persistent setup, add to your shell profile (~/.bashrc, ~/.zshrc)",
            ])
        elif auth_type == "invalid_key":
            lines.extend([
                "Troubleshooting steps:",
                "",
                "1. Verify your key at: https://console.anthropic.com/settings/keys",
                "2. Check the key is complete (no truncation)",
                "3. Ensure no extra whitespace in the key",
                "4. Try regenerating the key if issues persist",
            ])
        elif auth_type == "rate_limit":
            lines.extend([
                "Rate limit information:",
                "",
                "- Marianne will automatically wait and retry",
                "- Default wait time is configured in job settings",
                "- Consider reducing request frequency",
                "- Check your API tier limits at console.anthropic.com",
            ])

        return "\n".join(lines)


class DiagnoseMissingCLIRemedy(BaseRemedy):
    """Diagnoses missing Claude CLI.

    Triggers when:
    - Error indicates Claude CLI not found
    - Backend is configured for CLI but binary missing

    This is DIAGNOSTIC only because:
    - Installation requires system-level changes
    - User should verify installation method for their system
    """

    @property
    def name(self) -> str:
        return "diagnose_missing_cli"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.DIAGNOSTIC

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW  # Diagnostic only

    @property
    def description(self) -> str:
        return "Diagnoses missing Claude CLI binary"

    def diagnose(self, context: "ErrorContext") -> Diagnosis | None:
        """Check for missing CLI errors."""
        # Check for CLI-related error codes
        # Check if it's specifically about CLI
        if context.error_code in ("E601", "E901") and self._is_cli_error(context.error_message):
            return self._create_diagnosis(context)

        # Check message patterns
        cli_patterns = [
            r"claude.*not found",
            r"command.*claude.*not found",
            r"executable.*claude.*not found",
            r"cannot find.*claude",
            r"cli.*not.*installed",
        ]

        message_lower = context.error_message.lower()
        if any(re.search(p, message_lower, re.IGNORECASE) for p in cli_patterns):
            return self._create_diagnosis(context)

        return None

    def _is_cli_error(self, message: str) -> bool:
        """Check if error is CLI-related."""
        cli_keywords = ["claude", "cli", "command", "executable", "binary"]
        message_lower = message.lower()
        return any(kw in message_lower for kw in cli_keywords)

    def _create_diagnosis(self, context: "ErrorContext") -> Diagnosis:
        """Create diagnosis for missing CLI."""
        # Check if claude is actually in PATH
        claude_path = shutil.which("claude")

        if claude_path:
            return Diagnosis(
                error_code=context.error_code,
                issue="Claude CLI found but may have issues",
                explanation=f"The Claude CLI was found at {claude_path} but may not be "
                "working correctly.",
                suggestion="Try running 'claude --version' to verify the installation.",
                confidence=0.70,
                remedy_name=self.name,
                context={"cli_path": claude_path, "cli_found": True},
            )
        else:
            return Diagnosis(
                error_code=context.error_code,
                issue="Claude CLI not found",
                explanation="The Claude CLI binary is not installed or not in PATH.",
                suggestion="Install Claude CLI using npm: npm install -g @anthropic-ai/claude-cli",
                confidence=0.95,
                remedy_name=self.name,
                context={"cli_found": False},
            )

    def preview(self, context: "ErrorContext") -> str:
        return "Diagnose Claude CLI installation (no automatic fix available)"

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """Diagnostic only - no automatic fix."""
        return RemedyResult(
            success=True,
            message="Diagnostic information provided",
            action_taken="diagnosis only",
        )

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if not diagnosis:
            return "A CLI error occurred but could not be diagnosed."

        lines = [
            f"CLI Issue: {diagnosis.issue}",
            "",
            f"Explanation: {diagnosis.explanation}",
            "",
            f"Suggested Fix: {diagnosis.suggestion}",
            "",
        ]

        if not diagnosis.context.get("cli_found"):
            lines.extend([
                "Installation options:",
                "",
                "Using npm (recommended):",
                "  npm install -g @anthropic-ai/claude-cli",
                "",
                "After installation:",
                "1. Verify with: claude --version",
                "2. Configure with: claude auth login",
                "",
                "Troubleshooting:",
                "- Ensure Node.js 18+ is installed",
                "- Check npm global bin is in PATH",
                "- Try: npx @anthropic-ai/claude-cli --version",
            ])
        else:
            cli_path = diagnosis.context.get("cli_path", "unknown")
            lines.extend([
                f"CLI found at: {cli_path}",
                "",
                "Troubleshooting steps:",
                "1. Run: claude --version",
                "2. Run: claude auth status",
                "3. Try: claude --help",
                "",
                "If issues persist:",
                "- Reinstall: npm uninstall -g @anthropic-ai/claude-cli"
                " && npm install -g @anthropic-ai/claude-cli",
                "- Check permissions on the binary",
            ])

        return "\n".join(lines)
