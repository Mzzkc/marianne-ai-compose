"""GH #267: both template-context emitters build the positional built-ins
from ONE shared table, so adding a name under one vocabulary cannot leave the
other vocabulary (or the validator's built-in list) without it."""

from __future__ import annotations

from pathlib import Path

from marianne.core.constants import TERMINOLOGY_ALIASES, positional_template_variables
from marianne.core.sheet import Sheet
from marianne.prompts.templating import SheetContext
from marianne.validation.checks.best_practices import _BUILTIN_NAMES


def test_helper_emits_every_name_under_both_vocabularies() -> None:
    out = positional_template_variables(movement=2, voice=3, voice_count=4, total_movements=5)
    for new_name, old_name in TERMINOLOGY_ALIASES.items():
        assert out[new_name] == out[old_name], (new_name, old_name)
    assert set(out) == set(TERMINOLOGY_ALIASES) | set(TERMINOLOGY_ALIASES.values())


def test_sheet_and_sheet_context_agree(tmp_path: Path) -> None:
    sheet = Sheet(
        num=4, workspace=tmp_path, instrument_name="cli",
        movement=2, voice=3, voice_count=4,
    )
    tv = sheet.template_variables(total_sheets=9, total_movements=5)
    ctx = SheetContext(
        sheet_num=4, total_sheets=9, start_item=1, end_item=1, workspace=tmp_path,
        stage=2, instance=3, fan_count=4, total_stages=5,
    ).to_dict()
    keys = set(TERMINOLOGY_ALIASES) | set(TERMINOLOGY_ALIASES.values())
    assert {k: tv[k] for k in keys} == {k: ctx[k] for k in keys}


def test_validator_builtin_list_covers_both_vocabularies() -> None:
    for name in list(TERMINOLOGY_ALIASES) + list(TERMINOLOGY_ALIASES.values()):
        assert name in _BUILTIN_NAMES, name
