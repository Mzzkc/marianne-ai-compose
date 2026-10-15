"""Reserved and deprecated score-key warnings (V327/V328).

P1 of the score-language design (GH #436): a schema field either has a
runtime reader, or its description says ``Reserved:`` and a warning fires
when a score sets it. Silence is the defect — an author who writes a key
the runtime ignores must be told, without breaking the score (M-005).

D9 adds the deprecated-spelling layer: renamed keys keep their old
spelling for one release and warn (V328) so the author can move early.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import AliasChoices, BaseModel

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml

_MARKERS = ("Reserved:", "Loader-owned:")
_DEPRECATED_ROOT = "Deprecated:"


def _alias_names(field_info: Any) -> tuple[str, ...]:
    names: list[str] = []
    for attr in ("alias", "validation_alias"):
        value = getattr(field_info, attr, None)
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, AliasChoices):
            names.extend(c for c in value.choices if isinstance(c, str))
    return tuple(names)


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
def _scored_paths() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Reserved and deprecated key paths, derived from the schema itself.

    Returns ``(reserved, deprecated)`` maps of dotted path -> the spellings
    that trigger the warning at that path (the deprecated map only lists
    OLD spellings; the new spelling never warns).
    """
    reserved: dict[str, tuple[str, ...]] = {}
    deprecated: dict[str, tuple[str, ...]] = {}
    expanded: set[type[BaseModel]] = set()

    def walk(model: type[BaseModel], prefix: str) -> None:
        if model in expanded:
            return
        expanded.add(model)
        for name, info in model.model_fields.items():
            key = info.alias if isinstance(info.alias, str) else name
            path = f"{prefix}.{key}" if prefix else key
            description = (info.description or "").strip()
            aliases = _alias_names(info)
            choices = tuple(aliases) if aliases else (key,)
            if description.startswith(_MARKERS):
                reserved[path] = choices
            elif description.startswith(_DEPRECATED_ROOT) and "runtime reader" in description:
                deprecated[path] = choices
            for child in _models_in(info.annotation):
                walk(child, path + _container_suffix(info.annotation))

    walk(JobConfig, "")
    return reserved, deprecated


def _navigate(data: dict[Any, Any], path: str) -> list[tuple[Any, str]]:
    """Yield ``(subtree, consumed_path)`` for every place a path matches."""
    segments = path.replace("[]", ".[]").split(".")
    nodes: list[tuple[Any, str]] = [(data, "")]
    for segment in segments:
        next_nodes: list[tuple[Any, str]] = []
        for node, consumed in nodes:
            if segment in ("[]", "<k>"):
                child_values: list[Any]
                if isinstance(node, list):
                    child_values = list(node)
                elif isinstance(node, dict):
                    child_values = list(node.values())
                else:
                    continue
                for item in child_values:
                    next_nodes.append((item, consumed))
            else:
                if isinstance(node, dict) and segment in node:
                    label = f"{consumed}.{segment}" if consumed else segment
                    next_nodes.append((node[segment], label))
        nodes = next_nodes
    return nodes


def _line_of(raw_yaml: str, key: str) -> int | None:
    return find_line_in_yaml(raw_yaml, key)


def _parent_path(path: str) -> str:
    """The path of the dict that directly holds this key."""
    parent, _, _leaf = path.rpartition(".")
    return parent


def _set_spellings(data: dict[Any, Any], path: str, spellings: tuple[str, ...]) -> list[str]:
    """Spellings from ``spellings`` the score actually sets at ``path``."""
    found: list[str] = []
    parent = _parent_path(path)
    parents: list[dict[Any, Any]]
    if parent:
        parents = [node for node, _c in _navigate(data, parent) if isinstance(node, dict)]
    else:
        parents = [data]
    for node in parents:
        for spelling in spellings:
            if spelling in node and spelling not in found:
                found.append(spelling)
    return found


class ReservedKeyUseCheck:
    """V327: a score sets a key no runtime code reads."""

    @property
    def check_id(self) -> str:
        return "V327"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns when a score sets a reserved (unread) key"

    def check(
        self, config: JobConfig, config_path: Path, raw_yaml: str
    ) -> list[ValidationIssue]:
        import yaml as _yaml

        try:
            data = _yaml.safe_load(raw_yaml)
        except _yaml.YAMLError:
            return []
        if not isinstance(data, dict):
            return []
        reserved, _deprecated = _scored_paths()
        issues: list[ValidationIssue] = []
        for path in sorted(reserved):
            # A wholly reserved section is one author action and one warning.
            # Its child fields carry honest schema descriptions, but repeating
            # the same V327 for each nested key obscures the fix.
            root = path.split(".", 1)[0]
            if path != root and root in reserved:
                continue
            for spelling in _set_spellings(data, path, reserved[path]):
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Reserved key `{path}` (`{spelling}`) is accepted "
                            "but no runtime code reads it; it will be ignored"
                        ),
                        line=_line_of(raw_yaml, spelling),
                        suggestion=(
                            "Remove the key, or check the configuration "
                            "reference — reserved keys are waiting for a "
                            "runtime reader"
                        ),
                        metadata={"field": path},
                    )
                )
        return issues


class DeprecatedSpellingCheck:
    """V328: a score uses a renamed key's old spelling, or a deprecated key."""

    @property
    def check_id(self) -> str:
        return "V328"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns on deprecated key spellings and deprecated keys"

    def check(
        self, config: JobConfig, config_path: Path, raw_yaml: str
    ) -> list[ValidationIssue]:
        import yaml as _yaml

        try:
            data = _yaml.safe_load(raw_yaml)
        except _yaml.YAMLError:
            return []
        if not isinstance(data, dict):
            return []
        _reserved, deprecated = _scored_paths()
        issues: list[ValidationIssue] = []
        for path in sorted(deprecated):
            if _set_spellings(data, path, deprecated[path]):
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=self.severity,
                        message=(
                            f"Deprecated key `{path}`: " + _deprecation_advice(path)
                        ),
                        line=_line_of(raw_yaml, path.rsplit(".", 1)[-1]),
                        suggestion=_deprecation_advice(path),
                        metadata={"field": path},
                    )
                )
        # Renamed spellings that stay valid one release: checkpoints ->
        # approvals, on_success[].on_failure -> if_hook_fails.
        if "checkpoints" in data:
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message="`checkpoints:` is now `approvals:` — "
                    "the old spelling works this release",
                    line=_line_of(raw_yaml, "checkpoints"),
                    suggestion="Rename the key to `approvals:`",
                    metadata={"field": "checkpoints"},
                )
            )
        for section in ("on_success", "on_failure"):
            hooks = data.get(section)
            if not isinstance(hooks, list):
                continue
            for hook in hooks:
                if isinstance(hook, dict) and "on_failure" in hook:
                    issues.append(
                        ValidationIssue(
                            check_id=self.check_id,
                            severity=self.severity,
                            message=(
                                f"`{section}[].on_failure` is now `if_hook_fails` — at the "
                                "sheet-trigger level `on_failure` means something else"
                            ),
                            line=_line_of(raw_yaml, "on_failure"),
                            suggestion="Rename the key to `if_hook_fails:`",
                            metadata={"field": f"{section}[].on_failure"},
                        )
                    )
        return issues


def _deprecation_advice(path: str) -> str:
    if path == "conductor":
        return (
            "the root `conductor:` block is identity metadata with no runtime "
            "reader; conductor behaviour lives in ~/.marianne/conductor.yaml"
        )
    return "the key is deprecated; see the configuration reference for the replacement"
