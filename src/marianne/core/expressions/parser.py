"""Recursive descent parser; no expression source is ever evaluated as Python."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .ast import Binary, FileCall, Literal, LoopRef, Node, ReservedCall, SheetRef, Unary, VarRef
from .errors import ExpressionSyntaxError
from .lexer import Token, TokenKind, tokenize


@dataclass(frozen=True)
class ExpressionReferences:
    files: frozenset[str]
    variables: frozenset[tuple[str, ...]]
    loops: frozenset[str]
    sheets: frozenset[int]
    reserved: frozenset[str]


@dataclass(frozen=True)
class Expression:
    source: str
    root: Node

    def references(self) -> ExpressionReferences:
        files: set[str] = set()
        variables: set[tuple[str, ...]] = set()
        loops: set[str] = set()
        sheets: set[int] = set()
        reserved: set[str] = set()

        def visit(node: Node) -> None:
            if isinstance(node, FileCall):
                files.add(node.path)
            elif isinstance(node, VarRef):
                variables.add(node.path)
            elif isinstance(node, LoopRef):
                loops.add(node.name)
            elif isinstance(node, SheetRef) and node.num is not None:
                sheets.add(node.num)
            elif isinstance(node, ReservedCall):
                reserved.add(node.name)
            elif isinstance(node, Unary):
                visit(node.value)
            elif isinstance(node, Binary):
                visit(node.left)
                visit(node.right)

        visit(self.root)
        return ExpressionReferences(
            frozenset(files),
            frozenset(variables),
            frozenset(loops),
            frozenset(sheets),
            frozenset(reserved),
        )


class Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.position = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def take(self, value: str) -> Token | None:
        if self.current.value == value:
            token = self.current
            self.position += 1
            return token
        return None

    def need(self, value: str) -> Token:
        token = self.take(value)
        if token is None:
            raise ExpressionSyntaxError(self.source, self.current.offset, f"expected {value!r}")
        return token

    def ident(self) -> Token:
        token = self.current
        if token.kind != TokenKind.IDENT:
            raise ExpressionSyntaxError(self.source, token.offset, "expected identifier")
        self.position += 1
        return token

    def parse(self) -> Expression:
        root = self.or_expr()
        if self.current.kind != TokenKind.EOF:
            hint = (
                "use AND / OR / NOT"
                if self.current.value in {"and", "or", "not"}
                else "remove trailing syntax"
            )
            raise ExpressionSyntaxError(self.source, self.current.offset, "unexpected token", hint)
        return Expression(self.source, root)

    def or_expr(self) -> Node:
        left = self.and_expr()
        while (token := self.take("OR")) is not None:
            left = Binary("OR", left, self.and_expr(), token.offset)
        return left

    def and_expr(self) -> Node:
        left = self.not_expr()
        while (token := self.take("AND")) is not None:
            left = Binary("AND", left, self.not_expr(), token.offset)
        return left

    def not_expr(self) -> Node:
        token = self.take("NOT")
        return Unary("NOT", self.not_expr(), token.offset) if token else self.comparison()

    def comparison(self) -> Node:
        left = self.additive()
        if self.current.value in {"==", "!=", "<", "<=", ">", ">="}:
            token = self.current
            self.position += 1
            left = Binary(token.value, left, self.additive(), token.offset)
            if self.current.value in {"==", "!=", "<", "<=", ">", ">="}:
                raise ExpressionSyntaxError(
                    self.source, self.current.offset, "chained comparisons are unsupported"
                )
        return left

    def additive(self) -> Node:
        left = self.term()
        while self.current.value in {"+", "-"}:
            token = self.current
            self.position += 1
            left = Binary(token.value, left, self.term(), token.offset)
        return left

    def term(self) -> Node:
        left = self.unary()
        while self.current.value in {"*", "/", "%"}:
            token = self.current
            self.position += 1
            left = Binary(token.value, left, self.unary(), token.offset)
        return left

    def unary(self) -> Node:
        token = self.take("-")
        return Unary("-", self.unary(), token.offset) if token else self.primary()

    def primary(self) -> Node:
        token = self.current
        if self.take("("):
            node = self.or_expr()
            self.need(")")
            return node
        if token.kind == TokenKind.NUMBER:
            self.position += 1
            return Literal(
                float(token.value) if "." in token.value else int(token.value), token.offset
            )
        if token.kind == TokenKind.STRING:
            self.position += 1
            return Literal(self.string_value(token), token.offset)
        if token.value in {"true", "false", "null"}:
            self.position += 1
            return Literal({"true": True, "false": False, "null": None}[token.value], token.offset)
        if token.value in {"var", "loop", "sheet", "file", "validation", "output"}:
            self.position += 1
            if token.value == "var":
                self.need(".")
                parts = [self.ident().value]
                while self.take("."):
                    parts.append(self.ident().value)
                return VarRef(tuple(parts), token.offset)
            if token.value == "loop":
                self.need(".")
                return LoopRef(self.ident().value, token.offset)
            if token.value == "sheet":
                if self.take("."):
                    self.need("current")
                    field = self.ident().value if self.take(".") else "num"
                    self.sheet_field(field)
                    return SheetRef(None, field, token.offset)
                self.need("(")
                number = self.current
                if number.kind != TokenKind.NUMBER or "." in number.value:
                    raise ExpressionSyntaxError(
                        self.source, number.offset, "sheet() needs an integer"
                    )
                self.position += 1
                self.need(")")
                self.need(".")
                field = self.ident().value
                self.sheet_field(field)
                return SheetRef(int(number.value), field, token.offset)
            if token.value == "file":
                self.need("(")
                path = self.current
                if path.kind != TokenKind.STRING:
                    raise ExpressionSyntaxError(
                        self.source, path.offset, "file() needs a quoted path"
                    )
                self.position += 1
                self.need(")")
                self.need(".")
                member = self.ident().value
                if member not in {"exists", "modified", "contains", "matches"}:
                    raise ExpressionSyntaxError(
                        self.source, self.current.offset, "unknown file member"
                    )
                argument = None
                if member in {"contains", "matches"}:
                    self.need("(")
                    arg = self.current
                    kind = TokenKind.STRING if member == "contains" else TokenKind.REGEX
                    if arg.kind != kind:
                        raise ExpressionSyntaxError(
                            self.source, arg.offset, f"{member}() needs a {kind.value}"
                        )
                    self.position += 1
                    argument = self.string_value(arg) if member == "contains" else arg.value
                    if member == "matches":
                        try:
                            re.compile(argument)
                        except re.error as exc:
                            raise ExpressionSyntaxError(
                                self.source, arg.offset, f"invalid regex: {exc}"
                            ) from exc
                    self.need(")")
                return FileCall(self.string_value(path), member, argument, token.offset)
            self.need("(")
            depth = 1
            while depth and self.current.kind != TokenKind.EOF:
                if self.take("("):
                    depth += 1
                elif self.take(")"):
                    depth -= 1
                else:
                    self.position += 1
            if depth:
                raise ExpressionSyntaxError(
                    self.source, self.current.offset, "unclosed reserved call"
                )
            while self.take("."):
                self.ident()
                if self.take("("):
                    while self.current.value != ")" and self.current.kind != TokenKind.EOF:
                        self.position += 1
                    self.need(")")
            return ReservedCall(token.value, token.offset)
        hint = "use AND / OR / NOT" if token.value in {"and", "or", "not"} else "expected a value"
        raise ExpressionSyntaxError(self.source, token.offset, "unexpected token", hint)

    def sheet_field(self, field: str) -> None:
        if field not in {"num", "status", "attempts"}:
            raise ExpressionSyntaxError(
                self.source, self.current.offset, f"unknown sheet field {field!r}"
            )

    def string_value(self, token: Token) -> str:
        try:
            value = json.loads(token.value)
        except json.JSONDecodeError as exc:
            raise ExpressionSyntaxError(self.source, token.offset, "invalid string escape") from exc
        if not isinstance(value, str):
            raise ExpressionSyntaxError(self.source, token.offset, "expected a string")
        return value


def parse_expression(source: str) -> Expression:
    if len(source) > 4096:
        raise ExpressionSyntaxError(source, 4096, "expression exceeds 4096 characters")
    try:
        return Parser(source).parse()
    except RecursionError as exc:
        raise ExpressionSyntaxError(source, 0, "expression nesting is too deep") from exc
