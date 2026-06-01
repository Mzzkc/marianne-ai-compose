"""TDD tests for instrument fallback indicator in status display.

When a sheet has instrument_fallback_history, the status display
should show which instrument is currently being used and what the
original instrument was, including the fallback reason.

Format: "gemini-cli (was claude-code: rate_limit_exhausted)"

Tests:
1. No fallback history → plain instrument name
2. Single fallback → shows original and reason
3. Multiple fallbacks → shows last transition's original
4. Empty history list → plain instrument name
5. No instrument name → empty string

TDD: Red first, then green.
"""

from __future__ import annotations

from marianne.cli.commands.status import format_instrument_with_fallback
from marianne.core.checkpoint import SheetState, SheetStatus


def _make_sheet(
    instrument: str = "claude-code",
    fallback_history: list[dict[str, str]] | None = None,
    model: str | None = None,
) -> SheetState:
    return SheetState(
        sheet_num=1,
        status=SheetStatus.COMPLETED,
        instrument_name=instrument,
        instrument_model=model,
        instrument_fallback_history=fallback_history or [],
    )


class TestFormatInstrumentWithModel:
    """#334: the status table surfaces the per-sheet model override so two
    sheets sharing a profile but using different models are distinguishable.
    """

    def test_model_override_shown(self) -> None:
        """An explicit model override is appended to the profile name."""
        sheet = _make_sheet("claude-code", model="claude-sonnet-4-6")
        result = format_instrument_with_fallback(sheet)
        assert result == "claude-code (claude-sonnet-4-6)"

    def test_no_model_override_plain(self) -> None:
        """No model override → bare profile name (profile default, unchanged)."""
        sheet = _make_sheet("claude-code", model=None)
        result = format_instrument_with_fallback(sheet)
        assert result == "claude-code"

    def test_model_and_fallback_both_shown(self) -> None:
        """Model override and a fallback annotation coexist."""
        sheet = _make_sheet(
            "gemini-cli",
            fallback_history=[
                {"from": "claude-code", "to": "gemini-cli", "reason": "rate_limit"},
            ],
            model="gemini-3.1-pro-preview",
        )
        result = format_instrument_with_fallback(sheet)
        assert "gemini-cli (gemini-3.1-pro-preview)" in result
        assert "was claude-code" in result
        assert "rate_limit" in result


class TestFormatInstrumentWithFallback:
    """format_instrument_with_fallback shows fallback indicator."""

    def test_no_fallback_plain_instrument(self) -> None:
        """Sheet with no fallback history returns plain instrument name."""
        sheet = _make_sheet("claude-code", [])
        result = format_instrument_with_fallback(sheet)
        assert result == "claude-code"

    def test_single_fallback_shows_indicator(self) -> None:
        """Sheet with fallback history shows original and reason."""
        sheet = _make_sheet(
            "gemini-cli",
            [
                {
                    "from": "claude-code",
                    "to": "gemini-cli",
                    "reason": "rate_limit_exhausted",
                    "timestamp": "2026-04-06T00:00:00",
                },
            ],
        )
        result = format_instrument_with_fallback(sheet)
        assert "gemini-cli" in result
        assert "claude-code" in result
        assert "rate_limit_exhausted" in result
        assert "was" in result

    def test_multiple_fallbacks_shows_last(self) -> None:
        """With multiple fallbacks, show the last transition's original."""
        sheet = _make_sheet(
            "ollama",
            [
                {
                    "from": "claude-code",
                    "to": "gemini-cli",
                    "reason": "unavailable",
                    "timestamp": "2026-04-06T00:00:00",
                },
                {
                    "from": "gemini-cli",
                    "to": "ollama",
                    "reason": "rate_limit_exhausted",
                    "timestamp": "2026-04-06T00:01:00",
                },
            ],
        )
        result = format_instrument_with_fallback(sheet)
        assert "ollama" in result
        assert "gemini-cli" in result
        assert "rate_limit_exhausted" in result

    def test_empty_list_no_indicator(self) -> None:
        """Explicitly empty list → plain name, no indicator."""
        sheet = _make_sheet("claude-code", [])
        result = format_instrument_with_fallback(sheet)
        assert result == "claude-code"
        assert "was" not in result

    def test_no_instrument_name(self) -> None:
        """No instrument name → empty string."""
        sheet = _make_sheet("", [])
        result = format_instrument_with_fallback(sheet)
        assert result == ""
