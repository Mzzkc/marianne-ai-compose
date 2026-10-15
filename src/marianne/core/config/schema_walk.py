"""Tolerant score YAML walk before the strict Pydantic parse.

Only score loaders call this module. Instrument profile files keep their own
strict schemas. Dictionary keys are user data; only model fields are checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import BaseModel


@dataclass(frozen=True)
class UnknownScoreField:
    path: str
    key: str
    candidates: tuple[str, ...]


def _model_for(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin in (UnionType, __import__("typing").Union):
        models = [model for arg in get_args(annotation) if (model := _model_for(arg))]
        return models[0] if len(models) == 1 else None
    return None


def _walk_value(value: Any, annotation: Any, path: str, found: list[UnknownScoreField]) -> Any:
    model = _model_for(annotation)
    if model is not None and isinstance(value, dict):
        return _walk_model(value, model, path, found)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (UnionType, __import__("typing").Union):
        for arg in args:
            arg_origin = get_origin(arg)
            if isinstance(value, dict) and (_model_for(arg) is not None or arg_origin is dict):
                return _walk_value(value, arg, path, found)
            if isinstance(value, list) and arg_origin in (list, tuple):
                return _walk_value(value, arg, path, found)
        return value
    if origin in (list, tuple) and args and isinstance(value, list):
        return [
            _walk_value(item, args[0], f"{path}[{index}]", found)
            for index, item in enumerate(value)
        ]
    if origin is dict and len(args) == 2 and isinstance(value, dict):
        return {
            key: _walk_value(item, args[1], f"{path}.{key}", found) for key, item in value.items()
        }
    return value


def _alias_names(field_info: Any) -> list[str]:
    """Every spelling the strict parser accepts for this field.

    Covers ``alias`` and ``validation_alias`` (a bare string or
    ``AliasChoices``), so the tolerant walk never strips a legal spelling.
    """
    from pydantic import AliasChoices

    names: list[str] = []
    for attr in ("alias", "validation_alias"):
        value = getattr(field_info, attr, None)
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, AliasChoices):
            names.extend(choice for choice in value.choices if isinstance(choice, str))
    return names


def _walk_model(
    value: dict[Any, Any],
    model: type[BaseModel],
    path: str,
    found: list[UnknownScoreField],
) -> dict[Any, Any]:
    fields: dict[str, Any] = {}
    for name, field_info in model.model_fields.items():
        aliases = _alias_names(field_info)
        # The field's own name is accepted when there is no alias, when
        # populate_by_name is set, or when an AliasChoices names it.
        if not aliases or model.model_config.get("populate_by_name") or name in aliases:
            fields[name] = field_info
        for alias in aliases:
            fields[alias] = field_info
    cleaned: dict[Any, Any] = {}
    for key, item in value.items():
        matched_field = fields.get(key) if isinstance(key, str) else None
        if matched_field is None:
            found.append(UnknownScoreField(path, str(key), tuple(sorted(fields))))
            continue
        child = f"{path}.{key}" if path else str(key)
        cleaned[key] = _walk_value(item, matched_field.annotation, child, found)
    return cleaned


def strip_unknown_score_fields(
    data: dict[Any, Any],
    model: type[BaseModel],
) -> tuple[dict[Any, Any], list[UnknownScoreField]]:
    """Return a clean score mapping and every unknown model field."""
    found: list[UnknownScoreField] = []
    return _walk_model(data, model, "", found), found
