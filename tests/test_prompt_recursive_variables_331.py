"""#331: variables that reference other variables (or context) now resolve.

`PromptConfig.variables` values containing ``{{ other }}`` previously rendered
literally — Jinja's `template.render(**ctx)` is single-pass, so a value that is
itself a template string was inserted verbatim, not re-evaluated. Scores using
variables-within-variables for functional content (e.g. a `preamble` var that
embeds `{{ dj_name }}`) were silently broken.

Fix: pre-resolve `config.variables` string values against the full template
context to a fixpoint before the main render. Conservative by design —

- resolution uses a ``DebugUndefined`` env, so a reference to something NOT in
  context stays **literal** (``{{ x }}``) exactly as today — no regression, no
  new StrictUndefined crash;
- only string values from `config.variables` are pre-resolved (base-context
  values and non-string vars are untouched);
- iteration is capped, so a reference cycle terminates instead of hanging;
- ``{% raw %}`` still escapes intentional literal braces.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config.job import PromptConfig
from marianne.prompts.templating import PromptBuilder, SheetContext


def _ctx(sheet_num: int = 1) -> SheetContext:
    return SheetContext(
        sheet_num=sheet_num,
        total_sheets=3,
        start_item=1,
        end_item=10,
        workspace=Path("/tmp/test-ws"),
    )


def _render(template: str, variables: dict, sheet_num: int = 1) -> str:
    config = PromptConfig(template=template, variables=variables)
    return PromptBuilder(config).build_sheet_prompt(_ctx(sheet_num), raw_prompt=True)


class TestRecursiveVariableResolution:
    def test_variable_references_variable(self) -> None:
        # The exact example from the issue.
        out = _render(
            "{{ preamble }}",
            {"name": "DJ GestAIt", "preamble": "Hello, I am {{ name }}."},
        )
        assert out == "Hello, I am DJ GestAIt."

    def test_variable_references_context_var(self) -> None:
        out = _render("{{ greeting }}", {"greeting": "sheet {{ sheet_num }}"}, sheet_num=3)
        assert out == "sheet 3"

    def test_deep_chain_resolves(self) -> None:
        out = _render(
            "{{ a }}",
            {"c": "deep", "b": "{{ c }}", "a": "{{ b }}"},
        )
        assert out == "deep"


class TestBackwardCompat:
    def test_plain_variable_unchanged(self) -> None:
        out = _render("{{ x }}", {"x": "plain text"})
        assert out == "plain text"

    def test_undefined_ref_stays_literal(self) -> None:
        # A ref to something NOT in context must NOT crash (StrictUndefined) and
        # must NOT silently blank — it stays literal, exactly as before the fix.
        out = _render("{{ a }}", {"a": "{{ nonexistent_var }}"})
        assert out == "{{ nonexistent_var }}"

    def test_raw_block_preserves_literal(self) -> None:
        out = _render(
            "{{ lit }}",
            {"name": "X", "lit": "{% raw %}{{ name }}{% endraw %}"},
        )
        assert out == "{{ name }}"

    def test_reference_cycle_terminates(self) -> None:
        # a -> b -> a must not hang; the pass cap guarantees termination.
        out = _render("{{ a }}", {"a": "{{ b }}", "b": "{{ a }}"})
        assert isinstance(out, str)  # completed without hanging

    def test_no_variables_no_change(self) -> None:
        out = _render("static prompt", {})
        assert out == "static prompt"
