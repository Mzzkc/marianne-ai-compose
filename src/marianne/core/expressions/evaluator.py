"""Pure AST evaluator. All I/O is performed by the context producer."""

from __future__ import annotations

import re
from typing import Any, TypeAlias, cast

from .ast import Binary, FileCall, Literal, LoopRef, Node, ReservedCall, SheetRef, Unary, VarRef
from .context import ExpressionContext
from .errors import ExpressionTypeError, ReservedSyntaxError, UndefinedReferenceError
from .parser import Expression

Value: TypeAlias = object


def evaluate(expr: Expression, ctx: ExpressionContext) -> bool:
    value = evaluate_value(expr, ctx)
    if not isinstance(value, bool):
        raise ExpressionTypeError(expr.source, 0, "condition must evaluate to a boolean")
    return value


def evaluate_value(expr: Expression, ctx: ExpressionContext) -> Value:
    def walk(node: Node) -> Value:
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, VarRef):
            try:
                return ctx.var(node.path)
            except (KeyError, LookupError) as exc:
                raise UndefinedReferenceError(
                    expr.source, node.offset, f"undefined var.{'.'.join(node.path)}"
                ) from exc
        if isinstance(node, LoopRef):
            try:
                return ctx.loop_index(node.name)
            except (KeyError, LookupError) as exc:
                raise UndefinedReferenceError(
                    expr.source, node.offset, f"undefined loop.{node.name}"
                ) from exc
        if isinstance(node, SheetRef):
            try:
                facts = ctx.current_sheet() if node.num is None else ctx.sheet(node.num)
            except (KeyError, LookupError) as exc:
                raise UndefinedReferenceError(
                    expr.source, node.offset, f"undefined sheet({node.num})"
                ) from exc
            return getattr(facts, node.field)
        if isinstance(node, FileCall):
            file_facts = ctx.file(node.path)
            if node.member == "exists":
                return file_facts.exists
            if node.member == "modified":
                return file_facts.modified
            if file_facts.text is None:
                return False
            if node.member == "contains":
                return (node.argument or "") in file_facts.text
            return re.search(node.argument or "", file_facts.text) is not None
        if isinstance(node, ReservedCall):
            raise ReservedSyntaxError(
                expr.source,
                node.offset,
                f"{node.name}() is reserved for a future release and cannot be evaluated yet",
            )
        if isinstance(node, Unary):
            value = walk(node.value)
            if node.operator == "NOT":
                if not isinstance(value, bool):
                    raise ExpressionTypeError(expr.source, node.offset, "NOT requires a boolean")
                return not value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExpressionTypeError(expr.source, node.offset, "negative requires a number")
            return -value
        if isinstance(node, Binary):
            left = walk(node.left)
            if node.operator == "AND":
                if not isinstance(left, bool):
                    raise ExpressionTypeError(expr.source, node.offset, "AND requires booleans")
                if not left:
                    return False
                right = walk(node.right)
                if not isinstance(right, bool):
                    raise ExpressionTypeError(expr.source, node.offset, "AND requires booleans")
                return right
            if node.operator == "OR":
                if not isinstance(left, bool):
                    raise ExpressionTypeError(expr.source, node.offset, "OR requires booleans")
                if left:
                    return True
                right = walk(node.right)
                if not isinstance(right, bool):
                    raise ExpressionTypeError(expr.source, node.offset, "OR requires booleans")
                return right
            right = walk(node.right)
            if node.operator in {"==", "!=", "<", "<=", ">", ">="}:
                if (
                    isinstance(left, (int, float))
                    and not isinstance(left, bool)
                    and isinstance(right, str)
                ) or (
                    isinstance(right, (int, float))
                    and not isinstance(right, bool)
                    and isinstance(left, str)
                ):
                    raise ExpressionTypeError(
                        expr.source, node.offset, "cannot compare a number with a string"
                    )
                left_ordered = cast(Any, left)
                right_ordered = cast(Any, right)
                try:
                    if node.operator == "==":
                        return left == right
                    if node.operator == "!=":
                        return left != right
                    if node.operator == "<":
                        return left_ordered < right_ordered
                    if node.operator == "<=":
                        return left_ordered <= right_ordered
                    if node.operator == ">":
                        return left_ordered > right_ordered
                    return left_ordered >= right_ordered
                except TypeError as exc:
                    raise ExpressionTypeError(
                        expr.source, node.offset, "incompatible comparison"
                    ) from exc
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in (left, right)
            ):
                raise ExpressionTypeError(expr.source, node.offset, "arithmetic requires numbers")
            left_num = cast(Any, left)
            right_num = cast(Any, right)
            try:
                if node.operator == "+":
                    return left_num + right_num
                if node.operator == "-":
                    return left_num - right_num
                if node.operator == "*":
                    return left_num * right_num
                if node.operator == "/":
                    return left_num / right_num
                if isinstance(left, float) or isinstance(right, float):
                    raise ExpressionTypeError(expr.source, node.offset, "% requires integers")
                return left_num % right_num
            except ZeroDivisionError as exc:
                raise ExpressionTypeError(expr.source, node.offset, "division by zero") from exc
        raise AssertionError(f"unknown expression node: {node!r}")

    return walk(expr.root)
