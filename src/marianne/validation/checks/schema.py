"""Unknown fields retained by the tolerant score loader (V010/V011)."""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.schema_walk import UnknownScoreField
from marianne.validation.base import ValidationIssue, ValidationSeverity

_KNOWN_TYPOS = {
    "retries": "retry",
    "paralel": "parallel",
    "parralel": "parallel",
    "insturment": "instrument",
    "instrumnet": "instrument",
    "insturment_config": "instrument_config",
    "instrumnet_config": "instrument_config",
    "validation": "validations",
    "notification": "notifications",
    "sheets": "sheet",
    "prompts": "prompt",
    "max_retries": "retry.max_retries",
    "timeout": "stale_detection.idle_timeout_seconds",
    "preamble": "prompt.template",
    "task": "prompt.template",
    "stager_delay_ms": "parallel.stagger_delay_ms",
    "stagger_delay": "parallel.stagger_delay_ms",
    "backend_type": "instrument",
}


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        row = [i]
        for j, char_b in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (char_a != char_b)))
        previous = row
    return previous[-1]


def unknown_field_issue(field: UnknownScoreField, raw_yaml: str) -> ValidationIssue:
    """Classify one unknown key without rejecting the rest of the score."""
    nearest = min(field.candidates, key=lambda key: (_distance(field.key, key), key), default=None)
    near = nearest is not None and _distance(field.key, nearest) <= 2
    override = _KNOWN_TYPOS.get(field.key) if not field.path else None
    suggested = override or (nearest if near else None)
    # A curated onboarding hint is high-confidence even when the edit distance is high.
    severity = ValidationSeverity.WARNING if suggested else ValidationSeverity.INFO
    location = f"{field.path}.{field.key}" if field.path else field.key
    line = next(
        (
            i
            for i, text in enumerate(raw_yaml.splitlines(), 1)
            if text.lstrip().startswith(f"{field.key}:")
        ),
        None,
    )
    return ValidationIssue(
        check_id="V010" if severity == ValidationSeverity.WARNING else "V011",
        severity=severity,
        message=f"Unknown field `{location}` will be ignored"
        + (f"; did you mean `{suggested}`?" if suggested else ""),
        line=line,
        suggestion=f"Use `{suggested}` instead" if suggested else None,
        metadata={"field": location},
    )


class UnknownFieldCheck:
    @property
    def check_id(self) -> str:
        return "V010"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Reports unrecognized score YAML fields"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        return [unknown_field_issue(field, raw_yaml) for field in config.unknown_fields]
