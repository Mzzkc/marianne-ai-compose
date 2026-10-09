"""Restricted variable expansion for validation and flow file templates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_NAME = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_known(template: str, context: Mapping[str, Any]) -> str:
    """Substitute declared keys; preserve unknown braces and regex quantifiers."""
    return _NAME.sub(
        lambda match: str(context[match.group(1)])
        if match.group(1) in context else match.group(0),
        template,
    )
