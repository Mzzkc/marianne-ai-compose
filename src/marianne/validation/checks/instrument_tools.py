"""Per-sheet toolset check (V337, GH #534).

``instrument_config.tools`` names harness tools; whether a sheet may set it
depends on the RESOLVED profile's ``tools:`` mapping and its optional
``known`` list. This resolves every sheet (and every fallback entry that
carries its own ``tools``) and refuses what dispatch would refuse, in the same
words (``InstrumentProfile.tools_refusal``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marianne.core.config import JobConfig
from marianne.core.config.instruments import InstrumentProfile
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml
from marianne.validation.checks.capability_classes import _load_context
from marianne.validation.checks.instrument_effort import (
    _interactive,
    shadowed_builtin_sentence,
)


def _mapped_profiles_hint(profiles: dict[str, InstrumentProfile]) -> str:
    rows = [
        f"{n} ({'any name' if p.tools.known is None else ', '.join(p.tools.known)})"
        for n, p in sorted(profiles.items()) if p.tools is not None
    ]
    return "; ".join(rows) if rows else "none registered on this machine"


class SheetToolsCheck:
    """V337 (ERROR): ``instrument_config.tools`` the resolved profile cannot apply.

    Refusals, one code: the profile declares no ``tools:`` mapping; a name is
    outside the profile's ``tools.known`` list; or the sheet runs interactively
    on a mapping marked ``interactive: false``. Dispatch refuses the same
    sheets. An unregistered instrument name is V210's finding and is skipped.
    """

    @property
    def check_id(self) -> str:
        return "V337"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Refuses instrument_config.tools the resolved instrument profile cannot apply"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context(self.check_id)
        if ctx is None:
            return []
        try:
            from marianne.core.sheet import build_sheets

            used = ctx.used_classes(config)
            snapshot = ctx.class_map.snapshot(set(used)) if used else None
            sheets = build_sheets(config, classes=snapshot)
        except Exception:  # noqa: BLE001 — an unbuildable score is another check's finding
            return []

        refused: dict[tuple[str, str, str], list[int]] = {}
        for sheet in sheets:
            entries: list[tuple[str, str, Any]] = [
                (sheet.instrument_name, "primary", sheet.instrument_config),
            ]
            entries.extend(
                (name, "fallback", entry_config)
                for name, entry_config in zip(
                    sheet.instrument_fallbacks, sheet.instrument_fallback_configs,
                    strict=False,
                )
            )
            for profile_name, role, entry_config in entries:
                tools = entry_config.get("tools")
                if tools is None:
                    continue
                profile = ctx.profiles.get(profile_name)
                if profile is None:
                    continue
                reason = profile.tools_refusal(
                    [str(t) for t in tools],
                    interactive=_interactive(profile, entry_config),
                )
                if reason is None:
                    continue
                nums = refused.setdefault((profile_name, role, reason), [])
                if sheet.num not in nums:
                    nums.append(sheet.num)

        issues: list[ValidationIssue] = []
        hint = _mapped_profiles_hint(ctx.profiles)
        for (profile_name, role, reason), nums in refused.items():
            shown = ", ".join(str(n) for n in nums[:8]) + (", ..." if len(nums) > 8 else "")
            profile = ctx.profiles[profile_name]
            unmapped = profile.tools is None
            seat = (
                f"instrument '{profile_name}'" if role == "primary"
                else f"fallback instrument '{profile_name}'"
            )
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"{seat} {reason} (instrument_config.tools for sheet(s) "
                        f"{shown}); dispatch refuses those sheets"
                        + (shadowed_builtin_sentence(profile, "tools") if unmapped else "")
                    ),
                    line=find_line_in_yaml(raw_yaml, "tools:"),
                    context=profile_name,
                    suggestion=(
                        (
                            "remove `tools` from the scope that reaches this "
                            "instrument (a per-sheet `tools: null` restores the "
                            "harness default), or bind those sheets to a profile "
                            "that declares a tools: block. A user-layer or venue "
                            "copy of a builtin replaces it whole: it needs the "
                            "builtin's tools: block. "
                            if unmapped else
                            "use names from the known list, run the sheet "
                            "headless when the mapping is not interactive, or "
                            "remove `tools` from the scope that reaches this "
                            "instrument. "
                        )
                        + f"Profiles that declare a tools mapping here: {hint}"
                    ),
                    metadata={
                        "instrument": profile_name,
                        "role": role,
                        "sheets": ",".join(map(str, nums)),
                    },
                ),
            )
        return issues
