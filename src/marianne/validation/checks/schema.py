"""Unknown fields retained by the tolerant score loader (V010/V011)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import AliasChoices, BaseModel

from marianne.core.config import JobConfig
from marianne.core.config.schema_walk import UnknownScoreField
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import edit_distance

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
    # The sheet timeout is a declared knob; the idle timer is separate.
    "timeout": "instrument_config.timeout_seconds",
    "preamble": "prompt.template",
    "task": "prompt.template",
    # Stagger only takes effect with parallel enabled, so the hint names the
    # switch (D7) instead of leaving the author a silent no-op.
    "stager_delay_ms": "parallel.stagger_delay_ms (requires parallel.enabled: true)",
    "stagger_delay": "parallel.stagger_delay_ms (requires parallel.enabled: true)",
    "backend_type": "instrument",
}


def _container_suffix(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (UnionType, __import__("typing").Union):
        for arg in get_args(annotation):
            suffix = _container_suffix(arg)
            if suffix:
                return suffix
        return ""
    if origin in (list, tuple):
        return "[]"
    if origin is dict:
        return ".<k>"
    return ""


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    out: list[type[BaseModel]] = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        out.append(annotation)
    for arg in get_args(annotation):
        out.extend(_models_in(arg))
    return out


@lru_cache(maxsize=1)
def _known_leaf_locations() -> dict[str, tuple[str, ...]]:
    """Bare key name -> the real paths it lives at (D6 wrong-place map)."""
    locations: dict[str, set[str]] = {}
    expanded: set[type[BaseModel]] = set()

    def walk(model: type[BaseModel], prefix: str) -> None:
        if model in expanded:
            return
        expanded.add(model)
        for name, info in model.model_fields.items():
            key = info.alias if isinstance(info.alias, str) else name
            for attr in ("alias", "validation_alias"):
                value = getattr(info, attr, None)
                if isinstance(value, AliasChoices) and isinstance(value.choices[0], str):
                    key = value.choices[0]
            path = f"{prefix}.{key}" if prefix else key
            if len(key) >= 4:  # short names (size, type) match too much prose
                locations.setdefault(key, set()).add(
                    path.replace("[]", "").replace(".<k>", "")
                )
            for child in _models_in(info.annotation):
                walk(child, path + _container_suffix(info.annotation))

    walk(JobConfig, "")
    return {name: tuple(sorted(paths)) for name, paths in locations.items()}


def _relative_hint(field: Any, target: str) -> str:
    """Render a curated hint relative to the surface the unknown key sits in.

    A bare ``timeout`` inside ``instruments.<alias>.config`` means that alias's
    ``timeout_seconds``; inside a ``run`` trigger action it means the action's
    own ``timeout_seconds``. Naming the ROOT key there silently widens the
    author's intent from one seat or one action to the whole score (GH #451).
    The root form is only right at the root.
    """
    if not field.path:
        return target
    if target == "instrument_config.timeout_seconds":
        if "timeout_seconds" in field.candidates:
            return f"{field.path}.timeout_seconds"
        if field.path == "instrument_config" or field.path.endswith(
            (".instrument_config", ".config")
        ) or field.path.startswith("sheet.per_sheet_instrument_config."):
            return f"{field.path}.timeout_seconds"
        return target
    return target


def unknown_field_issue(field: UnknownScoreField, raw_yaml: str) -> ValidationIssue:
    """Classify one unknown key without rejecting the rest of the score."""
    nearest = min(
        field.candidates, key=lambda key: (edit_distance(field.key, key), key), default=None
    )
    near = nearest is not None and edit_distance(field.key, nearest) <= 2
    # Curated onboarding hints fire at ANY depth (a `timeout:` next to a
    # trigger action is the same author mistake as a root `timeout:`); the
    # walk only emits keys that are unknown AT that location, so a curated
    # name can never collide with a legitimately known nested key.
    override = _KNOWN_TYPOS.get(field.key)
    if override is not None:
        override = _relative_hint(field, override)
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
    # D6: a correctly spelled key in the wrong place is a WARNING that names
    # its real path, not a hidden INFO. The bare name matching a known leaf
    # elsewhere (the walk already proved this spot unknown) means the author
    # moved a real key, not misspelled one.
    real_paths = _known_leaf_locations().get(field.key)
    if real_paths and not suggested:
        shown = " or ".join(f"`{p}`" for p in real_paths[:2])
        return ValidationIssue(
            check_id="V010",
            severity=ValidationSeverity.WARNING,
            message=f"Unknown field `{location}` will be ignored; "
            f"`{field.key}` is a score key that lives at {shown}",
            line=line,
            suggestion=f"Move `{field.key}` under {shown}",
            metadata={"field": location},
        )
    # A whole block at the root (`deliver: {1: {to: ...}}`) is not a typo
    # the author can be nudged on: it is a feature the score relies on and
    # the runtime does not implement (or a key from a branch that never
    # merged). INFO is hidden without --verbose, and a silently dropped
    # block is how a product learned at runtime that nothing wrote its
    # output file. A root block therefore warns.
    if field.block and not field.path and not suggested:
        return ValidationIssue(
            check_id="V010",
            severity=ValidationSeverity.WARNING,
            message=f"Unknown field `{location}` will be ignored — the runtime "
            "implements no such block, so nothing it configures will happen",
            line=line,
            suggestion=f"Remove `{field.key}` or move its intent to a key the "
            "runtime reads (see docs/configuration-reference.md)",
            metadata={"field": location},
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
