"""Diagnostic-only remedies for self-healing.

These remedies cannot automatically fix issues but provide
detailed guidance for manual resolution:
- DiagnoseAuthErrorRemedy: Diagnoses authentication failures
- DiagnoseMissingCLIRemedy: Diagnoses a missing CLI binary for whichever
  CLI instrument the job is configured to use

Phase 5 note — both remedies are now instrument-aware. They honor the
active instrument profile's CLI executable name and auth environment
variable where available, and fall back to the Claude-specific
messaging only when the job is configured for the legacy
``claude_cli``/``anthropic_api`` instruments.
"""

import re
import shutil
from typing import TYPE_CHECKING

from marianne.healing.diagnosis import Diagnosis
from marianne.healing.remedies.base import BaseRemedy, RemedyCategory, RemedyResult, RiskLevel

if TYPE_CHECKING:
    from marianne.healing.context import ErrorContext


# Instruments known to authenticate via ANTHROPIC_API_KEY. The list is
# intentionally small — unknown instruments drop to generic guidance that
# names the instrument rather than hardcoding Anthropic environment
# variables.
_ANTHROPIC_AUTH_INSTRUMENTS: frozenset[str] = frozenset(
    {"claude_cli", "claude-code", "anthropic_api", "claude"},
)


def _effective_instrument(context: "ErrorContext") -> str:
    """Return the effective instrument name for ``context``.

    Falls back to ``"claude_cli"`` when no config is available — this
    preserves historical behavior for the paths that do not carry a
    ``JobConfig`` (raw preflight diagnostics).
    """
    cfg = context.config
    if cfg is None:
        return "claude_cli"
    try:
        return cfg.effective_instrument_name
    except AttributeError:
        # Defensive: some preflight paths may pass a non-JobConfig-shaped
        # stub. The Claude-family fallback is the safest default because
        # the remedies were written against it.
        return "claude_cli"


def _cli_binary_for(instrument: str) -> str:
    """Return the expected CLI binary name for an instrument.

    Consults the instrument registry (built-in profiles) when available.
    Falls back to a heuristic based on the instrument name, and ultimately
    to ``claude`` to preserve historical behavior for the ``claude_cli``
    instrument.
    """
    # Best-effort registry lookup — registry instantiation is cheap and
    # does not require the daemon to be running.
    try:
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        profile = registry.get(instrument)
        cli = getattr(profile, "cli", None) if profile is not None else None
        executable = getattr(getattr(cli, "command", None), "executable", None)
        if executable:
            return str(executable)
    except Exception:
        # Registry lookup is best-effort; heuristic fallback below.
        pass

    normalized = instrument.lower().replace("_", "-")
    if "claude" in normalized:
        return "claude"
    if "codex" in normalized:
        return "codex"
    if "gemini" in normalized:
        return "gemini"
    if "goose" in normalized:
        return "goose"
    if "opencode" in normalized:
        return "opencode"
    if "crush" in normalized:
        return "crush"
    # Final fallback preserves pre-Phase-5 behavior.
    return "claude"


def _install_hint_for(instrument: str, binary: str) -> str:
    """Return an instrument-aware install suggestion.

    Keeps the Claude-specific npm command for the Claude family, and
    offers a generic "install the <binary> binary" hint for everything
    else — the healer cannot know every instrument's install path.
    """
    normalized = instrument.lower().replace("_", "-")
    if "claude" in normalized:
        return "Install Claude CLI using npm: npm install -g @anthropic-ai/claude-cli"
    return (
        f"Install the '{binary}' binary for instrument '{instrument}' "
        "and ensure it is available on PATH."
    )


def _auth_env_var_for(instrument: str) -> str:
    """Return the auth environment variable for an instrument, or a generic hint."""
    normalized = instrument.lower().replace("-", "_")
    if normalized in _ANTHROPIC_AUTH_INSTRUMENTS or "anthropic" in normalized or "claude" in normalized:
        return "ANTHROPIC_API_KEY"
    if "openai" in normalized or "openrouter" in normalized:
        return "OPENROUTER_API_KEY" if "openrouter" in normalized else "OPENAI_API_KEY"
    if "gemini" in normalized or "google" in normalized:
        return "GEMINI_API_KEY"
    # Unknown instrument — use the instrument name itself so the operator
    # sees which instrument's credentials are missing.
    return f"<{instrument.upper()}_API_KEY>"


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

        instrument = _effective_instrument(context)
        env_var = _auth_env_var_for(instrument)
        has_key = bool(os.environ.get(env_var)) if env_var.isupper() else False

        if context.error_code == "E101":
            return Diagnosis(
                error_code=context.error_code,
                issue="Rate limit exceeded",
                explanation=f"The {instrument} API rate limit has been reached. "
                "This typically happens with high usage.",
                suggestion="Wait for the rate limit to reset (usually 1 minute) and retry.",
                confidence=0.90,
                remedy_name=self.name,
                context={"auth_type": "rate_limit", "instrument": instrument},
            )
        elif context.error_code in ("E401", "E102"):
            return Diagnosis(
                error_code=context.error_code,
                issue="Authentication failed" if has_key else "API key not set",
                explanation=f"API key for {instrument} is invalid or not configured."
                if has_key
                else f"The {env_var} environment variable is not set.",
                suggestion=f"Verify your {instrument} API key credentials"
                if has_key
                else f"Set {env_var} environment variable.",
                confidence=0.95,
                remedy_name=self.name,
                context={
                    "auth_type": "invalid_key" if has_key else "missing_key",
                    "instrument": instrument,
                    "env_var": env_var,
                },
            )
        else:
            return Diagnosis(
                error_code=context.error_code,
                issue="Authorization failed",
                explanation=f"Access denied to the {instrument} API.",
                suggestion="Check your API key permissions and account status.",
                confidence=0.80,
                remedy_name=self.name,
                context={"auth_type": "forbidden", "instrument": instrument},
            )

    def _diagnose_from_message(self, context: "ErrorContext") -> Diagnosis:
        """Diagnose based on error message patterns."""
        import os

        instrument = _effective_instrument(context)
        env_var = _auth_env_var_for(instrument)
        has_key = bool(os.environ.get(env_var)) if env_var.isupper() else False
        message_lower = context.error_message.lower()

        if "rate" in message_lower or "limit" in message_lower:
            return Diagnosis(
                error_code=context.error_code,
                issue="Rate limit exceeded",
                explanation=f"{instrument} API rate limit reached.",
                suggestion="Wait and retry. Consider reducing request frequency.",
                confidence=0.85,
                remedy_name=self.name,
                context={"auth_type": "rate_limit", "instrument": instrument},
            )
        elif not has_key:
            return Diagnosis(
                error_code=context.error_code,
                issue="API key not configured",
                explanation=f"{env_var} environment variable not found.",
                suggestion=f"Set your API key: export {env_var}='your-key'",
                confidence=0.90,
                remedy_name=self.name,
                context={
                    "auth_type": "missing_key",
                    "instrument": instrument,
                    "env_var": env_var,
                },
            )
        else:
            return Diagnosis(
                error_code=context.error_code,
                issue="Authentication error",
                explanation=f"{instrument} API authentication failed.",
                suggestion="Verify your API key is valid and active.",
                confidence=0.75,
                remedy_name=self.name,
                context={"auth_type": "unknown", "instrument": instrument},
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
        instrument = diagnosis.context.get("instrument", _effective_instrument(context))
        env_var = diagnosis.context.get("env_var", _auth_env_var_for(instrument))
        is_anthropic = instrument.lower().replace("-", "_") in _ANTHROPIC_AUTH_INSTRUMENTS

        if auth_type == "missing_key":
            lines.append("To set up your API key:")
            lines.append("")
            if is_anthropic:
                lines.append("1. Get an API key from: https://console.anthropic.com/settings/keys")
            else:
                lines.append(
                    f"1. Get an API key from the {instrument} provider's dashboard"
                )
            lines.extend([
                "",
                "2. Set the environment variable:",
                f"   Linux/macOS: export {env_var}='your-key'",
                f"   Windows:     set {env_var}=your-key",
                "",
                "3. For persistent setup, add to your shell profile (~/.bashrc, ~/.zshrc)",
            ])
        elif auth_type == "invalid_key":
            lines.append("Troubleshooting steps:")
            lines.append("")
            if is_anthropic:
                lines.extend([
                    "1. Verify your key at: https://console.anthropic.com/settings/keys",
                    "2. Check the key is complete (no truncation)",
                    "3. Ensure no extra whitespace in the key",
                    "4. Try regenerating the key if issues persist",
                ])
            else:
                lines.extend([
                    f"1. Verify your key in the {instrument} provider's dashboard",
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
                f"- Check your {instrument} API tier limits",
            ])

        return "\n".join(lines)


class DiagnoseMissingCLIRemedy(BaseRemedy):
    """Diagnoses a missing CLI binary for the job's configured instrument.

    Phase 5: previously hardcoded the ``claude`` binary. The remedy now
    reads the executable name from the instrument registry (via
    ``_cli_binary_for``) and falls back to ``claude`` only when the
    registry entry cannot be resolved (preserves behaviour for the
    legacy ``claude_cli`` instrument).

    Triggers when:
    - Error indicates the configured CLI is not found
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
        return "Diagnoses a missing CLI binary for the configured instrument"

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
        instrument = _effective_instrument(context)
        binary = _cli_binary_for(instrument)

        # Check if the configured binary is actually in PATH
        binary_path = shutil.which(binary)

        if binary_path:
            return Diagnosis(
                error_code=context.error_code,
                issue=f"{instrument} CLI found but may have issues",
                explanation=f"The {instrument} CLI was found at {binary_path} but "
                "may not be working correctly.",
                suggestion=f"Try running '{binary} --version' to verify the installation.",
                confidence=0.70,
                remedy_name=self.name,
                context={
                    "cli_path": binary_path,
                    "cli_found": True,
                    "instrument": instrument,
                    "binary": binary,
                },
            )
        else:
            install_hint = _install_hint_for(instrument, binary)
            return Diagnosis(
                error_code=context.error_code,
                issue=f"{instrument} CLI not found",
                explanation=f"The '{binary}' binary for instrument '{instrument}' "
                "is not installed or not in PATH.",
                suggestion=install_hint,
                confidence=0.95,
                remedy_name=self.name,
                context={
                    "cli_found": False,
                    "instrument": instrument,
                    "binary": binary,
                },
            )

    def preview(self, context: "ErrorContext") -> str:
        instrument = _effective_instrument(context)
        return (
            f"Diagnose {instrument} CLI installation (no automatic fix available)"
        )

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

        instrument = diagnosis.context.get("instrument", _effective_instrument(context))
        binary = diagnosis.context.get("binary", _cli_binary_for(instrument))
        is_claude = "claude" in instrument.lower()

        if not diagnosis.context.get("cli_found"):
            lines.append("Installation options:")
            lines.append("")
            if is_claude:
                lines.extend([
                    "Using npm (recommended):",
                    "  npm install -g @anthropic-ai/claude-cli",
                    "",
                    "After installation:",
                    f"1. Verify with: {binary} --version",
                    f"2. Configure with: {binary} auth login",
                    "",
                    "Troubleshooting:",
                    "- Ensure Node.js 18+ is installed",
                    "- Check npm global bin is in PATH",
                    "- Try: npx @anthropic-ai/claude-cli --version",
                ])
            else:
                lines.extend([
                    f"Install the '{binary}' binary for instrument '{instrument}' "
                    f"following the provider's installation instructions.",
                    "",
                    "After installation:",
                    f"1. Verify with: {binary} --version",
                    f"2. Ensure {binary} is authenticated per its documentation.",
                    "",
                    "Troubleshooting:",
                    f"- Check that '{binary}' is on PATH (shutil.which returned None)",
                    "- Check that the installation completed without errors",
                ])
        else:
            cli_path = diagnosis.context.get("cli_path", "unknown")
            lines.append(f"CLI found at: {cli_path}")
            lines.append("")
            lines.append("Troubleshooting steps:")
            lines.extend([
                f"1. Run: {binary} --version",
                f"2. Run: {binary} --help",
            ])
            if is_claude:
                lines.extend([
                    f"3. Run: {binary} auth status",
                    "",
                    "If issues persist:",
                    "- Reinstall: npm uninstall -g @anthropic-ai/claude-cli"
                    " && npm install -g @anthropic-ai/claude-cli",
                    "- Check permissions on the binary",
                ])
            else:
                lines.extend([
                    "",
                    "If issues persist:",
                    f"- Check the {instrument} installation instructions",
                    "- Check permissions on the binary",
                ])

        return "\n".join(lines)
