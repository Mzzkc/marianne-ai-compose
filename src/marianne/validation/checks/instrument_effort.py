"""Reasoning-effort check (V335, GH #535).

``instrument_config.effort`` names a level of Marianne's vocabulary (the
schema refuses any other word); whether it can run depends on the RESOLVED
profile's ``effort:`` mapping and, when a model row carries
``effort_levels``, on the model. The schema cannot see either, so this check
resolves every sheet (and every fallback entry that carries its own
``effort``) to its profile and model and refuses what dispatch would refuse,
in the same words (``InstrumentProfile.effort_refusal``), before a run is
paid for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marianne.core.config import JobConfig
from marianne.core.config.instruments import InstrumentProfile
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml
from marianne.validation.checks.capability_classes import _load_context


def _mapped_profiles_hint(profiles: dict[str, InstrumentProfile]) -> str:
    """Registered profiles a sheet CAN set effort on, each with its levels."""
    rows: list[str] = []
    for name in sorted(profiles):
        profile = profiles[name]
        if profile.effort is None:
            continue
        levels = profile.accepted_effort_levels(model=None, schema_mode=False)
        per_model = any(r.effort_levels is not None for r in profile.models)
        shown = ", ".join(levels) if levels else "none at the default model"
        rows.append(f"{name} ({shown}{'; varies by model' if per_model else ''})")
    return "; ".join(rows) if rows else "none registered on this machine"


def _interactive(profile: InstrumentProfile, entry_config: Any) -> bool:
    """Whether dispatch would launch this entry interactively."""
    raw = entry_config.get("interactive")
    if raw is not None:
        return bool(raw)
    cli = profile.cli
    return bool(cli is not None and cli.interactive is not None
                and cli.interactive.enabled_by_default)


class SheetEffortCheck:
    """V335 (ERROR): ``instrument_config.effort`` the resolved profile cannot apply.

    Refusals, one code: the profile declares no ``effort:`` mapping; the level
    is not mapped by the profile (or by the resolved model's
    ``effort_levels``); the sheet is a schema sheet (``response_format``) and
    the level is outside ``effort.schema_levels`` (claude-code refuses ``max``
    there: with ``--json-schema`` it emits nothing until the sheet timeout); or
    the sheet runs interactively on a mapping marked ``interactive: false``.
    Dispatch refuses the same sheets, so this is an ERROR, not advice. An
    unregistered instrument name is V210's finding and is skipped here.
    """

    @property
    def check_id(self) -> str:
        return "V335"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Refuses instrument_config.effort the resolved instrument profile cannot apply"

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

        # (profile, role, reason) -> sheet numbers, in first-seen order.
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
                level = entry_config.get("effort")
                if level is None:
                    continue
                profile = ctx.profiles.get(profile_name)
                if profile is None:
                    continue
                raw_model = entry_config.get("model")
                reason = profile.effort_refusal(
                    str(level),
                    model=str(raw_model) if raw_model is not None else None,
                    schema_mode=isinstance(entry_config.get("response_format"), dict),
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
            unmapped = profile.effort is None
            seat = (
                f"instrument '{profile_name}'" if role == "primary"
                else f"fallback instrument '{profile_name}'"
            )
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"{seat} {reason} (instrument_config.effort for sheet(s) "
                        f"{shown}); dispatch refuses those sheets"
                    ),
                    line=find_line_in_yaml(raw_yaml, "effort:"),
                    context=profile_name,
                    suggestion=(
                        (
                            "remove `effort` from the scope that reaches this "
                            "instrument (a per-sheet `effort: null` restores the "
                            "profile default), or bind those sheets to a profile "
                            "that declares the mapping. A user-layer or venue "
                            "copy of a builtin replaces it whole: it needs the "
                            "builtin's effort: block (and any model row "
                            "effort_levels). "
                            if unmapped else
                            "use one of the accepted levels, choose a model whose "
                            "row maps the level, run the sheet headless when the "
                            "mapping is not interactive, or remove `effort` from "
                            "the scope that reaches this instrument (a per-sheet "
                            "`effort: null` restores the profile default). "
                        )
                        + f"Profiles that declare a reasoning-effort mapping here: {hint}"
                    ),
                    metadata={
                        "instrument": profile_name,
                        "role": role,
                        "sheets": ",".join(map(str, nums)),
                    },
                ),
            )
        return issues
