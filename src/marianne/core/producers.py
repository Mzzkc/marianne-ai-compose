"""Producer signal: which sheet a score declares to write which file.

Movement ``produces`` declarations, sheet ``stdout_to`` targets and gated
``file_exists`` validation proxies share one producer map. Explicit ownership
takes precedence over stdout capture, then the validation proxy, for the same
path. V108/V109 and the runtime foreign-artifact guard consume this map.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from marianne.core.config.execution import ValidationRule

if TYPE_CHECKING:
    from marianne.core.config import JobConfig

_GATE = re.compile(r"sheet_num\s*(==|>=)\s*(\d+)")


def gated_producer_sheet(condition: str | None) -> int | None:
    """The sheet a validation is gated to (``sheet_num == N`` / ``>= N``),
    or None for any other condition shape."""
    if not condition:
        return None
    m = _GATE.search(condition)
    return int(m.group(2)) if m else None


def declared_products(validations: list[ValidationRule]) -> dict[int, list[str]]:
    """Sheet number -> the path templates its sheet-gated ``file_exists``
    validations declare it produces (unexpanded: ``{workspace}/x.md``)."""
    products: dict[int, list[str]] = {}
    for rule in validations:
        if rule.type != "file_exists" or not rule.path:
            continue
        sheet = gated_producer_sheet(rule.condition)
        if sheet is None:
            continue
        products.setdefault(sheet, []).append(str(rule.path))
    return products


def has_declared_outputs(config: JobConfig) -> bool:
    """Whether any movement declares ``produces`` or any sheet captures
    ``stdout_to`` — the two signals beyond the legacy proxy, in one
    predicate shared by the merge and ``build_sheets`` so their maps
    never disagree on which call shape is in play (#384)."""
    return bool(config.sheet.stdout_to) or any(
        movement.produces for movement in config.movements.values()
    )


def _stdout_capturable(workspace: Path, raw: str) -> bool:
    """Whether the real writer would accept this stdout target — the
    workspace-confinement rule ``resolve_stdout_to`` applies at runtime.
    A refused target produces nothing, so it must not own anything here."""
    raw = raw.replace("{workspace}", str(workspace))
    target = Path(raw)
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    return workspace == target or workspace in target.parents


def declared_products_from_config(config: JobConfig) -> dict[int, list[str]]:
    """Merge output signals in ownership priority order (GH #384).

    Movement declarations use concrete sheet numbers, including after fan-out
    expansion or checkpoint reload. Bind ``{sheet_num}`` to the PRODUCER now,
    leaving ``{workspace}`` for the runtime validation engine. Stdout targets
    are already expanded by SheetConfig, exactly as the musician writes them,
    and a target the runtime writer would refuse (it escapes the workspace)
    owns nothing. Proxy-only scores retain their original paths, ordering
    and duplicates.

    Identity: precedence and co-ownership compare resolved path identity, but
    the map advertises the authored spelling (symlinks unresolved). One
    spelling is kept per resolved identity so V108's basename lookup, V109,
    the ordering checks and the foreign-artifact guard all agree on the same
    name for one file.
    """
    if not has_declared_outputs(config):
        return declared_products(config.validations)

    workspace = config.workspace.expanduser().resolve()

    def path_key(raw: str) -> Path:
        path = Path(raw.replace("{workspace}", str(workspace)))
        return (path if path.is_absolute() else workspace / path).resolve()

    def workspace_path(raw: str) -> str | None:
        # Anchored, authored spelling: {workspace}/... for anything inside,
        # preserving the name consumers reference (symlinks stay unresolved).
        # None means the path escapes the workspace entirely.
        path = Path(raw.replace("{workspace}", str(workspace)))
        if not path.is_absolute():
            path = workspace / path
        normalized = Path(os.path.normpath(path))
        if normalized.is_relative_to(workspace):
            return "{workspace}/" + normalized.relative_to(workspace).as_posix()
        return None

    explicit: dict[int, list[str]] = {}
    for sheet in range(1, config.sheet.total_sheets + 1):
        stage = config.sheet.get_fan_out_metadata(sheet).stage
        movement = config.movements.get(stage)
        if movement and movement.produces:
            expanded = [
                workspace_path(path.replace("{sheet_num}", str(sheet)))
                for path in movement.produces
            ]
            explicit[sheet] = [path for path in expanded if path is not None]
    captured: dict[int, list[str]] = {}
    for sheet, path in config.sheet.stdout_to.items():
        if path and _stdout_capturable(workspace, path):
            declared = workspace_path(path)
            if declared is not None:
                captured[sheet] = [declared]
    products: dict[int, list[str]] = {}
    claimed: set[Path] = set()
    names: dict[Path, str] = {}
    for source in (explicit, captured, declared_products(config.validations)):
        keys: set[Path] = set()
        seen: dict[int, set[Path]] = {}
        for sheet, paths in source.items():
            for path in paths:
                key = path_key(path)
                keys.add(key)
                if key in claimed:
                    continue
                if key in seen.get(sheet, set()):
                    continue  # a duplicate spelling of a path this sheet already owns
                # One advertised spelling per file identity, shared by every
                # owner: string-equality consumers (guard suppression, lookup)
                # must see co-owners agree on the name.
                path = names.setdefault(key, path)
                products.setdefault(sheet, []).append(path)
                seen.setdefault(sheet, set()).add(key)
        # Multiple producers in the same source remain valid owners. Only a
        # stronger source can supersede them, never another sheet's ordering.
        claimed.update(keys)
    return products


def producer_basenames(
    validations: list[ValidationRule] | dict[int, list[str]],
) -> dict[str, set[int]]:
    """Produced file basename -> the sheets declared to produce it."""
    producers: dict[str, set[int]] = {}
    products = validations if isinstance(validations, dict) else declared_products(validations)
    for sheet, paths in products.items():
        for path in paths:
            producers.setdefault(Path(path).name, set()).add(sheet)
    return producers


def dag_ancestors(sheet: int, deps: dict[int, list[int]]) -> set[int]:
    """Transitive dependency ancestors of ``sheet`` — the sheets guaranteed
    to have completed before it runs."""
    seen: set[int] = set()
    stack = list(deps.get(sheet, []))
    while stack:
        s = stack.pop()
        if s in seen:
            continue
        seen.add(s)
        stack.extend(deps.get(s, []))
    return seen


def foreign_artifacts(
    sheet: int,
    validations: list[ValidationRule] | dict[int, list[str]],
    deps: dict[int, list[int]],
) -> list[str]:
    """Path templates declared as produced by sheets that are neither this
    sheet nor its DAG ancestors — files this sheet has no business writing.
    A sheet that writes one is fabricating a downstream artifact before the
    movement that owns it runs (GH #386)."""
    ancestors = dag_ancestors(sheet, deps)
    out: list[str] = []
    products = validations if isinstance(validations, dict) else declared_products(validations)
    # A shared output is not foreign to one of its owners. Keep the legacy
    # validation-list call shape's behavior; merged maps can declare co-owners.
    owned = {
        path for producer, paths in products.items()
        if producer == sheet or producer in ancestors
        for path in paths
    } if isinstance(validations, dict) else set()
    for producer, paths in sorted(products.items()):
        if producer == sheet or producer in ancestors:
            continue
        for path in paths:
            if path not in owned and path not in out:
                out.append(path)
    return out
