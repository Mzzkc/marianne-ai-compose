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


def class_summary(config: JobConfig) -> dict[str, Any]:
    """Class-map provenance and per-sheet class resolution (design §2.1, §8 V-CLS-11).

    Every layer path the validator read is listed with its sha256 — the
    provenance contract — whether or not the score uses a class. ``used`` and
    ``per_sheet`` are populated only when a class is named. All state comes
    from the one loader; this never re-resolves layers.
    """
    summary: dict[str, Any] = {"layers": [], "used": [], "per_sheet": [], "failures": []}
    try:
        from marianne.instruments.classes import (
            _instrument_positions,
            load_class_map,
        )
        from marianne.instruments.loader import load_all_profiles

        profiles = load_all_profiles()
        class_map = load_class_map(profile_names=set(profiles))
    except Exception:
        return summary

    for record in class_map.layers:
        summary["layers"].append(
            {
                "layer": record.layer,
                "path": str(record.path),
                "sha256": record.sha256,
                "present": record.sha256 is not None,
            }
        )
    summary["failures"] = [
        {"layer": failure.layer, "path": str(failure.path), "reason": failure.reason}
        for failure in class_map.failures
    ]

    used: dict[str, str] = {}
    for location, name in _instrument_positions(config):
        if name in config.instruments or name in profiles:
            continue
        if name in class_map.classes:
            used.setdefault(name, location)
    if not used:
        return summary

    for name in sorted(used):
        entry = class_map.classes[name]
        layer = next((r for r in class_map.layers if r.layer == entry.source_layer), None)
        summary["used"].append(
            {
                "class": name,
                "chain": [
                    f"{step.profile}/{step.config.model}" if step.config.model else step.profile
                    for step in entry.chain
                ],
                "layer": entry.source_layer,
                "layer_path": str(layer.path) if layer is not None else None,
                "layer_sha256": layer.sha256 if layer is not None else None,
                "first_named_at": used[name],
            }
        )

    try:
        from marianne.core.sheet import build_sheets

        for sheet in build_sheets(config, classes=class_map.snapshot(set(used))):
            resolution = sheet.instrument_resolution
            if resolution is None:
                continue
            summary["per_sheet"].append(
                {
                    "sheet": sheet.num,
                    "requested": resolution.requested,
                    "chain": list(resolution.chain),
                    "dropped_duplicates": list(resolution.dropped_duplicates),
                    "snapshot_digest": resolution.snapshot_digest,
                }
            )
    except Exception:
        pass  # unresolvable chains already carry V310 rows
    return summary


def _known_suppression_severities() -> dict[str, ValidationSeverity]:
    """Every code that can be suppressed, with its tier (V012 truth invariant).

    Primary check ids come from the registry; the explicit additions cover
    codes emitted outside registered checks (the tolerant walk, the flow load
    renderer, V012 itself).
    """
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
            # Rendered by the flow load renderer (_FLOW_CODES → V230); no
            # registered check owns it (my own S3 Inspect advisory), but the
            # emission exists, so suppression must be able to judge it.
            "V230": ValidationSeverity.ERROR,
        }
    )
    return known


def apply_suppression(
    config: JobConfig,
    issues: list[ValidationIssue],
) -> tuple[list[ValidationIssue], list[dict[str, int | str]]]:
    """Remove only valid advisory codes; invalid requests become V012 errors."""
    known = _known_suppression_severities()
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
