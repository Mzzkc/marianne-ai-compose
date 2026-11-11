"""Memory score-key checks (V340, V342, V344-V347; GH #496, spec 03).

The ``memory:`` key validates but the conductor does not read it yet; V327
reports that. These checks catch values that could never work once it is
read. Scope and access grammars come from :mod:`marianne.memory` so the
rules live in one place.

Check IDs:
- V340: ``system`` is not a backend this release ships
- V342: a ``phases`` entry is not ``all``, a movement number, or a movement name
- V344: ``scope`` fails the scope grammar
- V345: caps out of range or unknown provenance
- V349: INFO note when the deprecated ``off`` was read as ``optional`` (its own
  code: one code carries one check and one severity, so it can be suppressed)
- V346: ``access`` fails the access grammar
- V347: ``access`` names a grant, and no grant store exists yet
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.memory import MemoryBackendDecl
from marianne.memory.access import parse_access
from marianne.memory.errors import MemoryBackendError
from marianne.memory.model import SCOPE_FAMILIES, parse_scope
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml, parsed_document

#: Backends that exist in this release (the files backend ships as a library).
SHIPPED_SYSTEMS: tuple[str, ...] = ("files",)
RECALL_CAP_MIN = 1024
RECALL_CAP_MAX = 1024 * 1024
_PROVENANCE = ("required", "optional")


def _declared(config: JobConfig) -> Iterator[tuple[str, MemoryBackendDecl]]:
    if config.memory is not None:
        yield from config.memory.backends.items()


def _is_off(value: object) -> bool:
    # Unquoted `off` is the boolean False under YAML 1.1.
    return value is False or value == "off"


def _nearest(value: str, options: tuple[str, ...]) -> str:
    import difflib

    close = difflib.get_close_matches(value, options, n=1)
    return close[0] if close else options[0]


class _MemoryCheck:
    """Shared plumbing: one subclass per validator code."""

    code = ""
    sev = ValidationSeverity.ERROR
    summary = ""

    @property
    def check_id(self) -> str:
        return self.code

    @property
    def severity(self) -> ValidationSeverity:
        return self.sev

    @property
    def description(self) -> str:
        return self.summary

    def _issue(
        self, raw_yaml: str, marker: str, field: str, message: str, suggestion: str
    ) -> ValidationIssue:
        return ValidationIssue(
            check_id=self.code,
            severity=self.sev,
            message=message,
            line=find_line_in_yaml(raw_yaml, marker),
            suggestion=suggestion,
            metadata={"field": field},
        )

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        raise NotImplementedError


class MemorySystemUnknownCheck(_MemoryCheck):
    """V340: ``system`` names a backend this release does not ship."""

    code = "V340"
    summary = "Memory backend `system` is not one this release ships"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for name, decl in _declared(config):
            if decl.system in SHIPPED_SYSTEMS:
                continue
            issues.append(
                self._issue(
                    raw_yaml,
                    decl.system,
                    f"memory.backends.{name}.system",
                    f"memory.backends.{name}.system `{decl.system}` is not a known memory "
                    f"backend; accepted: {', '.join(SHIPPED_SYSTEMS)}",
                    f"Use `system: {_nearest(decl.system, SHIPPED_SYSTEMS)}`",
                )
            )
        return issues


class MemoryPhasesInvalidCheck(_MemoryCheck):
    """V342: a ``phases`` entry matches no phase rule."""

    code = "V342"
    summary = "Memory `phases` entry is not `all`, a movement number, or a movement name"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        names = {m.name for m in config.movements.values() if m.name}
        issues = []
        for name, decl in _declared(config):
            for entry in decl.phases:
                if entry == "all" or entry.isdigit() or entry in names:
                    continue
                known = ", ".join(sorted(names)) or "none defined"
                issues.append(
                    self._issue(
                        raw_yaml,
                        entry,
                        f"memory.backends.{name}.phases",
                        f"memory.backends.{name}.phases entry `{entry}` is not accepted; "
                        f"accepted: `all`, a movement number such as `2`, or a movement "
                        f"name this score defines (defined names: {known})",
                        'Write `phases: ["all"]`, movement numbers, or movement names',
                    )
                )
        return issues


class MemoryScopeInvalidCheck(_MemoryCheck):
    """V344: ``scope`` fails the scope grammar."""

    code = "V344"
    summary = "Memory `scope` fails the scope grammar"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for name, decl in _declared(config):
            if decl.scope is None:
                continue
            try:
                parse_scope(decl.scope)
            except MemoryBackendError:
                issues.append(
                    self._issue(
                        raw_yaml,
                        decl.scope,
                        f"memory.backends.{name}.scope",
                        f"memory.backends.{name}.scope `{decl.scope}` is not a valid scope; "
                        f"accepted: `<family>/<id>` or `<family>:<id>` with family one of "
                        f"{', '.join(SCOPE_FAMILIES)} and id of a-z 0-9 _ . -",
                        "Write a scope like `project/my-project` or `local:default`",
                    )
                )
        return issues


class MemoryCapsInvalidCheck(_MemoryCheck):
    """V345: caps out of range, a cap above the score default, or unknown provenance."""

    code = "V345"
    summary = "Memory caps out of range or unknown provenance value"

    def _cap(self, raw_yaml: str, where: str, value: int) -> list[ValidationIssue]:
        if RECALL_CAP_MIN <= value <= RECALL_CAP_MAX:
            return []
        return [
            self._issue(
                raw_yaml,
                "recall_cap_bytes",
                f"{where}.recall_cap_bytes",
                f"{where}.recall_cap_bytes {value} is out of range; accepted: "
                f"{RECALL_CAP_MIN} to {RECALL_CAP_MAX} bytes",
                f"Use a whole number of bytes from {RECALL_CAP_MIN} to {RECALL_CAP_MAX}",
            )
        ]

    def _prov(self, raw_yaml: str, where: str, value: str) -> list[ValidationIssue]:
        if value in _PROVENANCE:
            return []
        return [
            self._issue(
                raw_yaml,
                "provenance",
                f"{where}.provenance",
                f"memory_caps_invalid: unknown provenance value '{value}' at {where} "
                "(legal values after alias normalization: 'required', 'optional')",
                "Use `provenance: required` or `provenance: optional`",
            )
        ]

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        if config.memory is None:
            return []
        defaults = config.memory.defaults
        issues = self._cap(raw_yaml, "memory.defaults", defaults.recall_cap_bytes)
        issues += self._prov(raw_yaml, "memory.defaults", defaults.provenance)
        for name, decl in _declared(config):
            where = f"memory.backends.{name}.caps"
            issues += self._cap(raw_yaml, where, decl.caps.recall_cap_bytes)
            issues += self._prov(raw_yaml, where, decl.caps.provenance)
            explicit = "recall_cap_bytes" in decl.caps.model_fields_set
            if explicit and decl.caps.recall_cap_bytes > defaults.recall_cap_bytes:
                issues.append(
                    self._issue(
                        raw_yaml,
                        "recall_cap_bytes",
                        f"{where}.recall_cap_bytes",
                        f"{where}.recall_cap_bytes {decl.caps.recall_cap_bytes} exceeds "
                        f"memory.defaults.recall_cap_bytes {defaults.recall_cap_bytes}; "
                        "an attachment cap may only lower the score default",
                        "Lower the attachment cap or raise memory.defaults.recall_cap_bytes",
                    )
                )
        return issues


class MemoryProvenanceOffNote(_MemoryCheck):
    """V349 (INFO): ``provenance: off`` was read as ``optional``."""

    code = "V349"
    sev = ValidationSeverity.INFO
    summary = "Notes that deprecated `provenance: off` was read as `optional`"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        data = parsed_document(raw_yaml)
        memory = data.get("memory") if data else None
        if not isinstance(memory, dict):
            return []
        spots: list[str] = []
        defaults = memory.get("defaults")
        if isinstance(defaults, dict) and _is_off(defaults.get("provenance")):
            spots.append("memory.defaults")
        backends = memory.get("backends")
        if isinstance(backends, dict):
            for name, decl in backends.items():
                caps = decl.get("caps") if isinstance(decl, dict) else None
                if isinstance(caps, dict) and _is_off(caps.get("provenance")):
                    spots.append(f"memory.backends.{name}.caps")
        return [
            self._issue(
                raw_yaml,
                "provenance",
                f"{where}.provenance",
                f"provenance 'off' is deprecated; normalized to 'optional' at {where}",
                "Write `provenance: optional`",
            )
            for where in spots
        ]


class MemoryAccessInvalidCheck(_MemoryCheck):
    """V346: ``access`` is neither ``inherit`` nor a grant reference."""

    code = "V346"
    summary = "Memory `access` is neither `inherit` nor a grant reference"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for name, decl in _declared(config):
            try:
                parse_access(decl.access)
            except MemoryBackendError:
                issues.append(
                    self._issue(
                        raw_yaml,
                        decl.access,
                        f"memory.backends.{name}.access",
                        f"memory.backends.{name}.access `{decl.access}` is not valid; "
                        "accepted: `inherit` or `grant:<name>[@<version>]` "
                        "(name: a-z 0-9 _ -; version: a positive integer)",
                        "Write `access: inherit` or `access: grant:<name>`",
                    )
                )
        return issues


class MemoryGrantUnknownCheck(_MemoryCheck):
    """V347: a grant reference cannot resolve because no grant store exists yet."""

    code = "V347"
    summary = "Memory `access` names a grant, and no grant store exists yet"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for name, decl in _declared(config):
            try:
                ref = parse_access(decl.access)
            except MemoryBackendError:
                continue  # V346 reports the malformed value
            if ref.kind != "grant":
                continue
            issues.append(
                self._issue(
                    raw_yaml,
                    decl.access,
                    f"memory.backends.{name}.access",
                    f"memory.backends.{name}.access `{decl.access}` names a grant that does "
                    "not exist: this release has no grant store, so only `access: inherit` "
                    "is accepted",
                    "Use `access: inherit`",
                )
            )
        return issues
