"""GH #261: the legacy validation formatter never silently drops a rule type.

Every ``ValidationRule.type`` literal must produce prompt text through
``PromptBuilder._format_legacy_validation`` when no semantic failure info is
available; a type without a dedicated formatter falls back to ``_fmt_generic``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_args

import pytest

from marianne.core.config.execution import ValidationRule
from marianne.prompts.templating import _LEGACY_FORMATTERS, PromptBuilder


@dataclass
class _FakeResult:
    rule: ValidationRule
    passed: bool = False
    expected_value: str | None = "/ws/out.md"
    actual_value: str | None = None
    error_message: str | None = "boom"
    failure_category: str | None = None
    failure_reason: str | None = None
    suggested_fix: str | None = None


_ALL_TYPES: tuple[str, ...] = get_args(ValidationRule.model_fields["type"].annotation)


def _render(rule_type: str, desc: str) -> str:
    # model_construct skips per-type field validation: this test is about the
    # formatter's dispatch, not the rule schema.
    rule = ValidationRule.model_construct(type=rule_type, description=desc, path="/ws/out.md")
    lines: list[str] = []
    PromptBuilder._format_legacy_validation(lines, 1, desc, _FakeResult(rule=rule), rule)  # type: ignore[arg-type]
    return "\n".join(lines)


@pytest.mark.parametrize("rule_type", _ALL_TYPES)
def test_every_rule_type_renders_prompt_text(rule_type: str) -> None:
    text = _render(rule_type, f"desc-{rule_type}")
    assert f"desc-{rule_type}" in text, f"{rule_type} produced no prompt text"


def test_types_without_dedicated_formatter_use_generic_fallback() -> None:
    missing = [t for t in _ALL_TYPES if t not in _LEGACY_FORMATTERS]
    assert missing, "vacuous: every type has a formatter now; the fallback must still exist"
    text = _render(missing[0], "generic-desc")
    assert "generic-desc" in text and f"[FAILED:{missing[0]}]" in text
