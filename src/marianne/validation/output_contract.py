"""Structural summary and truthful score-local warning suppression."""

from __future__ import annotations

from collections import Counter
from typing import Any

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.runner import create_default_checks


def structural_summary(config: JobConfig) -> dict[str, Any]:
    """Describe the parsed structure even when semantic checks find errors."""
    sheet = config.sheet
    declared = set(config.prompt.variables)
    return {
        "score": config.name,
        "sheets": sheet.total_sheets,
        "stages": (
            len({info["stage"] for info in sheet.fan_out_stage_map.values()})
            if sheet.fan_out_stage_map else sheet.total_sheets
        ),
        "fan_out": bool(sheet.fan_out_stage_map),
        "instruments": {
            "primary": config.effective_instrument_name,
            "fallbacks": list(config.instrument_fallbacks),
        },
        "loops": list(getattr(sheet, "loops", None) or {}),
        "triggers": list(getattr(sheet, "triggers", None) or {}),
        "variables": {"declared": len(declared)},
    }


def apply_suppression(
    config: JobConfig,
    issues: list[ValidationIssue],
) -> tuple[list[ValidationIssue], list[dict[str, int | str]]]:
    """Remove only valid advisory codes; invalid requests become V012 errors."""
    known = {check.check_id: check.severity for check in create_default_checks()}
    known.update(
        {
            "V011": ValidationSeverity.INFO,
            "V104": ValidationSeverity.INFO,
            "V012": ValidationSeverity.ERROR,
            "V221": ValidationSeverity.ERROR,
            "V222": ValidationSeverity.ERROR,
            "V223": ValidationSeverity.ERROR,
            "V312": ValidationSeverity.ERROR,
            "V313": ValidationSeverity.ERROR,
            "V314": ValidationSeverity.ERROR,
            "V316": ValidationSeverity.ERROR,
            "V317": ValidationSeverity.ERROR,
            "V320": ValidationSeverity.ERROR,
        }
    )
    visible = list(issues)
    allowed: set[str] = set()
    for code in config.validation_settings.suppress:
        severity = known.get(code)
        if severity is None or severity == ValidationSeverity.ERROR:
            visible.append(
                ValidationIssue(
                    check_id="V012",
                    severity=ValidationSeverity.ERROR,
                    message=f"validate.suppress cannot suppress {code}: "
                    + ("unknown code" if severity is None else "ERROR-tier code"),
                    suggestion="Remove this code from validate.suppress",
                )
            )
        else:
            allowed.add(code)
    suppressed = Counter(
        issue.check_id
        for issue in visible
        if issue.check_id in allowed and issue.severity != ValidationSeverity.ERROR
    )
    visible = [
        issue
        for issue in visible
        if not (issue.check_id in allowed and issue.severity != ValidationSeverity.ERROR)
    ]
    return visible, [
        {"check_id": code, "count": count} for code, count in sorted(suppressed.items())
    ]
