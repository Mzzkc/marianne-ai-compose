"""Positioned expression errors."""

from __future__ import annotations


class ExpressionError(ValueError):
    """A parse or evaluation failure tied to expression source."""

    def __init__(self, source: str, offset: int, message: str, hint: str = "") -> None:
        self.source = source
        self.offset = offset
        self.line = source.count("\n", 0, offset) + 1
        self.col = offset - source.rfind("\n", 0, offset)
        self.hint = hint or message
        super().__init__(f"{message} at line {self.line}, column {self.col}")


class ExpressionSyntaxError(ExpressionError):
    """Expression is not in the supported grammar."""


class ExpressionTypeError(ExpressionError):
    """Operands cannot be used together."""


class UndefinedReferenceError(ExpressionError):
    """A referenced value is absent at evaluation time."""


class ReservedSyntaxError(ExpressionError):
    """Syntax is recognized but deliberately unavailable."""
