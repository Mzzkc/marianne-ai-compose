"""Snapshot-backed expression facts for baton loop decisions."""

from __future__ import annotations

from typing import Any

from marianne.core.checkpoint import SheetState
from marianne.core.expressions import FileFacts, SheetFacts
from marianne.core.flow_state import FlowState


class BatonExpressionContext:
    def __init__(
        self,
        sheets: dict[int, SheetState],
        flow: FlowState,
        variables: dict[str, Any],
        current: int,
        files: dict[str, FileFacts] | None = None,
    ) -> None:
        self._sheets = sheets
        self._flow = flow
        self._variables = variables
        self._current = current
        self._files = files or {}

    def var(self, path: tuple[str, ...]) -> object:
        value: Any = self._variables[path[0]]
        for part in path[1:]:
            if not isinstance(value, dict):
                raise KeyError(path)
            value = value[part]
        return value

    def loop_index(self, name: str) -> int:
        for loop in self._flow.loops.values():
            if loop.index_name == name:
                return loop.iteration
        raise KeyError(name)

    def sheet(self, num: int) -> SheetFacts:
        sheet = self._sheets[num]
        return SheetFacts(num, sheet.status.value, sheet.attempt_count)

    def current_sheet(self) -> SheetFacts:
        return self.sheet(self._current)

    def file(self, resolved_path: str) -> FileFacts:
        return self._files.get(resolved_path, FileFacts(False, False, None))
