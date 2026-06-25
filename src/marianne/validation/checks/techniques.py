"""Technique system validation checks.

Validates that technique declarations in score YAML are consistent with
the available instrument profiles and skill documents.

Check IDs:
- V301: Skill technique references a document path that doesn't exist
- V302: MCP technique targets an instrument with no MCP access path
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.techniques import TechniqueKind
from marianne.instruments.loader import InstrumentProfileLoader
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml


class TechniqueSkillPathCheck:
    """Check that skill techniques reference existing documents (V301).

    When a skill technique declares a ``path`` in its config, the referenced
    file should exist. Missing skill docs mean the agent won't receive the
    methodology the score author intended.
    """

    @property
    def check_id(self) -> str:
        return "V301"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks that skill technique documents exist"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check skill technique paths exist."""
        if not config.techniques:
            return []

        issues: list[ValidationIssue] = []
        for name, tc in config.techniques.items():
            if tc.kind != TechniqueKind.SKILL:
                continue
            skill_path_str = tc.config.get("path")
            if skill_path_str is None:
                continue
            skill_path = Path(skill_path_str)
            if not skill_path.is_absolute():
                skill_path = config_path.parent / skill_path
            if not skill_path.exists():
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Skill technique '{name}' references "
                            f"'{skill_path_str}' which does not exist"
                        ),
                        line=find_line_in_yaml(raw_yaml, name),
                        suggestion=(
                            f"Create the skill document at '{skill_path_str}' "
                            "or remove the path from the technique config"
                        ),
                    ),
                )
        return issues


class TechniqueMcpInstrumentCheck:
    """Check MCP techniques target MCP-capable instruments (V302).

    When a score declares MCP techniques, the primary instrument needs an MCP
    access path. Native MCP configuration is one path; the generated code-mode
    ``techniques_rt.py`` bridge is the portable fallback for CLI instruments.
    """

    @property
    def check_id(self) -> str:
        return "V302"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Checks MCP techniques target MCP-capable instruments"

    def check(
        self,
        config: JobConfig,
        config_path: Path,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Check MCP techniques against instrument capabilities."""
        if not config.techniques:
            return []

        mcp_techniques = [
            name for name, tc in config.techniques.items()
            if tc.kind == TechniqueKind.MCP
        ]
        if not mcp_techniques:
            return []

        instrument_name = config.effective_instrument_name
        if not instrument_name:
            return []

        # Try loading the instrument profile to check for direct MCP config.
        try:
            loader = InstrumentProfileLoader()
            builtins_dir = (
                Path(__file__).parent.parent.parent
                / "instruments" / "builtins"
            )
            profiles = loader.load_directory(builtins_dir)
            profile = profiles.get(instrument_name)
        except Exception:
            # Can't load profiles — skip the check rather than
            # blocking validation on a profile loading issue
            return []

        if profile is None:
            # Unknown instrument — don't emit V302 since we can't verify
            return []

        has_native_mcp_config = bool(
            profile.cli
            and profile.cli.command
            and (
                profile.cli.command.mcp_config_flag
                or profile.cli.command.mcp_config_workspace_path
            )
        )
        has_code_bridge = bool(profile.kind == "cli" and profile.cli)

        if not (has_native_mcp_config or has_code_bridge):
            names_str = ", ".join(mcp_techniques)
            return [
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"MCP techniques ({names_str}) declared but "
                        f"instrument '{instrument_name}' has no native MCP "
                        "config path or generated code-mode bridge support"
                    ),
                    line=find_line_in_yaml(raw_yaml, "techniques"),
                    suggestion=(
                        "Use a CLI instrument supported by Marianne's shared "
                        "MCP bridge or remove the MCP techniques"
                    ),
                ),
            ]
        return []
