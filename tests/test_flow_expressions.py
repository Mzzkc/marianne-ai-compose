"""Expression contracts at the core boundary."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

from marianne.core.expressions import (
    ExpressionContext,
    ExpressionError,
    ExpressionSyntaxError,
    ReservedSyntaxError,
    evaluate,
    parse_expression,
)
from marianne.core.expressions.context import FileFacts, SheetFacts


@dataclass
class Facts(ExpressionContext):
    values: dict[str, object]

    def var(self, path: tuple[str, ...]) -> object:
        return self.values[path[0]]

    def loop_index(self, name: str) -> int:
        return 2

    def sheet(self, num: int) -> SheetFacts:
        return SheetFacts(num=num, status="completed", attempts=3)

    def current_sheet(self) -> SheetFacts:
        return self.sheet(2)

    def file(self, resolved_path: str) -> FileFacts:
        present = resolved_path == "ok.txt"
        return FileFacts(exists=present, modified=False, text="ready" if present else None)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('var.target == 2 AND file("ok.txt").contains("ready")', True),
        ("sheet(2).attempts == 3 AND sheet.current == 2", True),
        ('loop.i == 2 OR file("missing").exists', True),
        ("NOT (1 + 2 * 3 == 7)", False),
        ('file("missing").matches(/a+/)', False),
    ],
)
def test_evaluation(source: str, expected: bool) -> None:
    assert evaluate(parse_expression(source), Facts({"target": 2})) is expected


def test_variable_values_do_not_change_ast() -> None:
    source = 'var.x == "1 OR 1"'
    parsed = parse_expression(source)
    assert evaluate(parsed, Facts({"x": "1 OR 1"}))
    assert parsed.source == source


@pytest.mark.parametrize("source", ['validation("x").passed', 'output(2).contains("y")'])
def test_reserved_parses_but_refuses_evaluation(source: str) -> None:
    parsed = parse_expression(source)
    with pytest.raises(ReservedSyntaxError) as exc:
        evaluate(parsed, Facts({}))
    assert exc.value.offset >= 0


@pytest.mark.parametrize("source", ["1 < 2 < 3", "true and false", 'file("x").matches(/[/)'])
def test_syntax_refusal_is_positioned(source: str) -> None:
    with pytest.raises(ExpressionSyntaxError) as exc:
        parse_expression(source)
    assert exc.value.line >= 1
    assert exc.value.col >= 1


@given(st.text(max_size=80))
def test_parser_never_raises_an_untyped_error(source: str) -> None:
    try:
        parse_expression(source)
    except ExpressionError:
        pass


@pytest.mark.parametrize("source", ['file("\\q").exists', 'file("\\uZZZZ").exists'])
def test_invalid_file_string_is_positioned(source: str) -> None:
    with pytest.raises(ExpressionSyntaxError):
        parse_expression(source)
