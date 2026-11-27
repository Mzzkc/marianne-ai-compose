"""GH #586: the claude-code ready pattern must accept the empty input box as Claude Code draws it.

Panes below are captured from Claude Code 2.1.332 (2026-11-27) and from the trust dialog the
profile comment warns about.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PROFILE = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins/claude-code.yaml"

PLACEHOLDER_PANE = """\
 ▐▛███▛█   Claude Code v2.1.332
▝▜██████▀  GLM 5.4 with high effort · Claude Max
──────────────────────────────────────────────────
❯ Try "edit <filepath> to…"
──────────────────────────────────────────────────
  -- INSERT -- ⏵⏵ auto mode on (shift+tab to cycle)
"""

BARE_PANE = """\
──────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────
"""

TRUST_PANE = """\
 Is this a project you created or one you trust?

 ❯ 1. Yes, I trust this folder
   2. No, exit
"""

TYPED_PANE = """\
──────────────────────────────────────────────────
❯ write the report
──────────────────────────────────────────────────
"""


def _pattern() -> re.Pattern[str]:
    data = yaml.safe_load(PROFILE.read_text())
    return re.compile(data["cli"]["interactive"]["ready_pattern"])


def test_placeholder_prompt_is_ready() -> None:
    assert _pattern().search(PLACEHOLDER_PANE)


def test_bare_prompt_is_ready() -> None:
    assert _pattern().search(BARE_PANE)


def test_trust_dialog_cursor_is_not_ready() -> None:
    assert not _pattern().search(TRUST_PANE)


def test_typed_input_is_not_ready() -> None:
    assert not _pattern().search(TYPED_PANE)
