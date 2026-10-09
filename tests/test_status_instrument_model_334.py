"""#334: `mzt status` surfaces the per-sheet model override.

Two sheets sharing a profile (e.g. `instruments:` aliases over claude-code) but
running different models must be distinguishable at a glance. The status display
appends the explicit model override; a bare profile name means the profile
default.
"""

from __future__ import annotations

from marianne.cli.commands.status import format_instrument_with_fallback
from marianne.core.checkpoint import SheetState


def _sheet(**kw: object) -> SheetState:
    return SheetState(sheet_num=1, **kw)  # type: ignore[arg-type]


class TestInstrumentModelDisplay:
    def test_model_override_is_shown(self) -> None:
        sheet = _sheet(instrument_name="claude-code", instrument_model="claude-sonnet-4-6")
        assert format_instrument_with_fallback(sheet) == "claude-code (claude-sonnet-4-6)"

    def test_no_override_shows_bare_profile(self) -> None:
        sheet = _sheet(instrument_name="claude-code")
        assert format_instrument_with_fallback(sheet) == "claude-code"

    def test_two_aliases_same_profile_are_distinguishable(self) -> None:
        thinker = _sheet(instrument_name="claude-code")  # profile default (Opus)
        worker = _sheet(instrument_name="claude-code", instrument_model="claude-sonnet-4-6")
        assert format_instrument_with_fallback(thinker) != format_instrument_with_fallback(worker)

    def test_model_override_survives_with_fallback_annotation(self) -> None:
        sheet = _sheet(
            instrument_name="gemini-cli",
            instrument_model="gemini-3.1-pro-preview",
            instrument_fallback_history=[{"from": "claude-code", "reason": "rate_limit"}],
        )
        out = format_instrument_with_fallback(sheet)
        assert "gemini-cli (gemini-3.1-pro-preview)" in out
        assert "was claude-code: rate_limit" in out


class TestFallbackClearsStaleModel:
    """GH #377 / #399: after a fallback the display never shows the primary's model."""

    def test_fallback_without_own_model_shows_bare_fallback_profile(self) -> None:
        sheet = _sheet(
            instrument_name="antigravity",
            instrument_model="gemini-3.8-flash-high",
            model="gemini-3.8-flash-high",
            fallback_chain=["codex-cli"],
            fallback_configs=[{}],
        )
        assert sheet.advance_fallback("execution_failed") == "codex-cli"
        out = format_instrument_with_fallback(sheet)
        assert out.startswith("codex-cli [dim]"), out
        assert "gemini" not in out.split("[dim]")[0]

    def test_fallback_with_own_model_shows_that_model(self) -> None:
        sheet = _sheet(
            instrument_name="codex-cli",
            instrument_model="gpt-5.5",
            model="gpt-5.5",
            fallback_chain=["opencode"],
            fallback_configs=[{"model": "zai-coding-plan/glm-5.3"}],
        )
        sheet.advance_fallback("rate_limit_exhausted")
        out = format_instrument_with_fallback(sheet)
        assert out.startswith("opencode (zai-coding-plan/glm-5.3)")
