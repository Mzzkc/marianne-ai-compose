"""Immutable expression syntax tree."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True)
class Literal:
    value: object
    offset: int


@dataclass(frozen=True)
class VarRef:
    path: tuple[str, ...]
    offset: int


@dataclass(frozen=True)
class LoopRef:
    name: str
    offset: int


@dataclass(frozen=True)
class SheetRef:
    num: int | None
    field: str
    offset: int


@dataclass(frozen=True)
class FileCall:
    path: str
    member: str
    argument: str | None
    offset: int


@dataclass(frozen=True)
class ReservedCall:
    name: str
    offset: int


@dataclass(frozen=True)
class Unary:
    operator: str
    value: Node
    offset: int


@dataclass(frozen=True)
class Binary:
    operator: str
    left: Node
    right: Node
    offset: int


Node: TypeAlias = Literal | VarRef | LoopRef | SheetRef | FileCall | ReservedCall | Unary | Binary
