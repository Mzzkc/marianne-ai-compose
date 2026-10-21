"""Producer signal: which sheet a score declares to write which file.

Marianne has a consumer signal (cadenzas), an ordering signal (the
dependency DAG) and, as a proxy, a producer signal: a ``file_exists``
validation gated to a sheet is the author declaring that sheet produces
the file. V108/V109 reason about it at validate time; the baton uses it at
run time to notice a sheet writing another sheet's declared artifact
(GH #386). No new config key: the proxy is the existing validations.
"""

from __future__ import annotations

import re
from pathlib import Path

from marianne.core.config.execution import ValidationRule

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


def producer_basenames(validations: list[ValidationRule]) -> dict[str, set[int]]:
    """Produced file basename -> the sheets declared to produce it."""
    producers: dict[str, set[int]] = {}
    for sheet, paths in declared_products(validations).items():
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
    sheet: int, validations: list[ValidationRule], deps: dict[int, list[int]],
) -> list[str]:
    """Path templates declared as produced by sheets that are neither this
    sheet nor its DAG ancestors — files this sheet has no business writing.
    A sheet that writes one is fabricating a downstream artifact before the
    movement that owns it runs (GH #386)."""
    ancestors = dag_ancestors(sheet, deps)
    out: list[str] = []
    for producer, paths in sorted(declared_products(validations).items()):
        if producer == sheet or producer in ancestors:
            continue
        for path in paths:
            if path not in out:
                out.append(path)
    return out
