"""Facts supplied to the pure expression evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SheetFacts:
    num: int
    status: str
    attempts: int


@dataclass(frozen=True)
class FileFacts:
    exists: bool
    modified: bool
    text: str | None


class ExpressionContext(Protocol):
    def var(self, path: tuple[str, ...]) -> object: ...

    def loop_index(self, name: str) -> int: ...

    def sheet(self, num: int) -> SheetFacts: ...

    def current_sheet(self) -> SheetFacts: ...

    def file(self, resolved_path: str) -> FileFacts: ...
