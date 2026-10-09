"""The legacy validation condition grammar shared by load and execution."""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping
from typing import Any

_OPS = {
    ">=": operator.ge, "<=": operator.le, "==": operator.eq,
    "!=": operator.ne, ">": operator.gt, "<": operator.lt,
}


def check_validation_condition(condition: str | None, context: Mapping[str, Any]) -> bool:
    """Match runtime's fail-open comparison syntax without evaluating code."""
    if condition is None:
        return True
    return all(
        _check_single(part.strip(), context)
        for part in condition.strip().split(" and ")
    )


def _check_single(condition: str, context: Mapping[str, Any]) -> bool:
    match = re.match(r"(\w+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+)", condition)
    if not match:
        return True
    var_name, op, value_text = match.groups()
    value = context.get(var_name)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        actual = int(value.strip())
    elif type(value) is int:
        actual = value
    else:
        return False
    return bool(_OPS[op](actual, int(value_text)))
