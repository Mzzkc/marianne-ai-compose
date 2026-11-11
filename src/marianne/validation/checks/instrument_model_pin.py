"""Unpinned-model check (V336, GH #538).

A profile with ``default_model: null`` and a ``model_flag`` hands the model
choice to the harness itself: the sheet runs on whatever the operator last
selected in that harness. The score does not control it, the cost system
cannot price it, and it can change between two runs. This check resolves every
sheet (and every fallback entry) through the one sheet builder, so a model set
at ANY scope (score, movement, per-sheet, alias config, chain entry, class
entry) counts, and names the seats where none is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marianne.core.config import JobConfig
from marianne.core.config.instruments import InstrumentProfile
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml
from marianne.validation.checks.capability_classes import _load_context


def _is_unpinned_model_seat(profile: InstrumentProfile, entry_config: Any) -> bool:
    """True when the harness, not the score, would choose the model."""
    if profile.default_model or entry_config.get("model"):
        return False
    cli = profile.cli
    return bool(cli is not None and cli.command.model_flag)


class UnpinnedModelCheck:
    """V336 (INFO): a model seat whose model nothing in the score names.

    INFO, not WARNING: 54 of the 65 shipped examples leave a codex-cli or goose
    seat unpinned, and a WARNING fails ``mzt validate --strict`` on each of
    them. It rises to WARNING once the builtins or the examples name a model.
    """

    @property
    def check_id(self) -> str:
        return "V336"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.INFO

    @property
    def description(self) -> str:
        return "Notes when a sheet runs on an instrument's own default model"

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

        unpinned: dict[tuple[str, str], list[int]] = {}
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
                profile = ctx.profiles.get(profile_name)
                if profile is None or not _is_unpinned_model_seat(profile, entry_config):
                    continue
                nums = unpinned.setdefault((profile_name, role), [])
                if sheet.num not in nums:
                    nums.append(sheet.num)

        issues: list[ValidationIssue] = []
        for (profile_name, role), nums in unpinned.items():
            shown = ", ".join(str(n) for n in nums[:8]) + (", ..." if len(nums) > 8 else "")
            seat = (
                f"instrument '{profile_name}'" if role == "primary"
                else f"fallback instrument '{profile_name}'"
            )
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"{seat} has no default_model and no model is set for "
                        f"sheet(s) {shown}: they run on the harness's own default "
                        "model, which this score does not control and which "
                        "changes when the operator changes it"
                    ),
                    line=find_line_in_yaml(raw_yaml, "instrument"),
                    context=profile_name,
                    suggestion=(
                        "pin the model: set `instrument_config.model: <model-id>` "
                        "(score, movement or per-sheet scope), give the alias a "
                        "`config: {model: <model-id>}`, or set `model` on the "
                        "chain/class entry"
                    ),
                    metadata={
                        "instrument": profile_name,
                        "role": role,
                        "sheets": ",".join(map(str, nums)),
                    },
                ),
            )
        return issues
