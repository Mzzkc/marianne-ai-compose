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
