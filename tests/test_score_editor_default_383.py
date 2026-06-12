"""Goal-mode audit (GPT P2): the dashboard score editor's default
document must parse under the CURRENT JobConfig.

The editor shipped a default still emitting the removed ``backend:``
syntax (and runner-era sheet fields) — every first-run user got a
document the parser rejects. This test extracts ``defaultContent`` from
the shipped template and validates it for real, so the next breaking
config migration cannot silently strand the editor again.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from marianne.core.config import JobConfig

_TEMPLATE = (
    Path(__file__).parent.parent
    / "src/marianne/dashboard/templates/pages/score_editor.html"
)


def _extract_default_content() -> str:
    text = _TEMPLATE.read_text(encoding="utf-8")
    match = re.search(
        r"defaultContent:\s*\[(.*?)\]\.join", text, flags=re.DOTALL
    )
    assert match, "defaultContent block not found in score_editor.html"
    lines = re.findall(r"'((?:[^'\\]|\\.)*)'", match.group(1))
    return "\n".join(line.replace("\\'", "'") for line in lines)


class TestEditorDefaultIsValid:
    def test_default_content_parses_as_yaml(self) -> None:
        data = yaml.safe_load(_extract_default_content())
        assert isinstance(data, dict)

    def test_default_content_validates_as_job_config(self) -> None:
        data = yaml.safe_load(_extract_default_content())
        config = JobConfig.model_validate(data)
        assert config.name

    def test_default_content_never_mentions_backend(self) -> None:
        assert "backend" not in _extract_default_content()
