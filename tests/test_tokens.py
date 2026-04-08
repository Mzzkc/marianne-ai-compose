"""Tests for marianne.core.tokens module.

Covers token estimation (T1.1–T1.11), TokenBudgetTracker (T2.1–T2.9),
and divergent estimator reconciliation (T10.4) from the Litmus test specs.
"""

import math

import pytest

from marianne.core.tokens import (
    _CHARS_PER_TOKEN,
    _DEFAULT_EFFECTIVE_WINDOW,
    _INSTRUMENT_EFFECTIVE_WINDOWS,
    _MODEL_EFFECTIVE_WINDOWS,
    TokenBudgetTracker,
    estimate_tokens,
    get_effective_window_size,
)


class TestEstimateTokens:
    """Tests for the estimate_tokens() function (T1.*)."""

    # T1.1: Basic estimation accuracy
    def test_basic_estimation_accuracy(self) -> None:
        """3500-char string should produce ceil(3500/3.5) = 1000 tokens."""
        text = "a" * 3500
        assert estimate_tokens(text) == 1000

    def test_basic_estimation_scaling(self) -> None:
        """Token estimate scales linearly with input length."""
        text_1k = "x" * 1000
        text_2k = "x" * 2000
        assert estimate_tokens(text_2k) == 2 * estimate_tokens(text_1k)

    def test_estimation_uses_ceil(self) -> None:
        """Partial token results are rounded up (conservative)."""
        # 10 chars / 3.5 = 2.857... -> ceil = 3
        assert estimate_tokens("a" * 10) == math.ceil(10 / _CHARS_PER_TOKEN)

    # T1.2: None input
    def test_none_returns_zero(self) -> None:
        assert estimate_tokens(None) == 0

    # T1.3: Empty string
    def test_empty_string_returns_zero(self) -> None:
        assert estimate_tokens("") == 0

    # T1.4: Dict input (structured data)
    def test_dict_input(self) -> None:
        """Dict input is serialized to JSON before estimation."""
        data = {"key": "value", "nested": {"a": 1}}
        result = estimate_tokens(data)
        assert result > 0
        # Should be consistent with JSON serialization
        import json
        expected = estimate_tokens(json.dumps(data, default=str))
        assert result == expected

    def test_dict_with_non_serializable_values(self) -> None:
        """Dict with non-JSON-serializable values falls back to str()."""
        data = {"func": lambda x: x}  # lambdas aren't JSON serializable
        result = estimate_tokens(data)
        assert result > 0  # No crash

    # T1.5: List input
    def test_list_input(self) -> None:
        """List input is serialized to JSON before estimation."""
        data = ["fragment1", "fragment2", "fragment3"]
        result = estimate_tokens(data)
        assert result > 0
        import json
        expected = estimate_tokens(json.dumps(data, default=str))
        assert result == expected

    # T1.6: Conservative estimation (overestimates)
    @pytest.mark.parametrize(
        "text",
        [
            "The quick brown fox jumps over the lazy dog.",
            "def calculate_total(items: list[float]) -> float:\n    return sum(items)",
            "Marianne is an orchestration system for collaborative intelligence.",
            "import json\nimport math\nfrom pathlib import Path\n\ndef main():\n    pass",
            "a" * 1000,
        ],
        ids=["prose", "python", "domain", "imports", "repeated"],
    )
    def test_conservative_estimation(self, text: str) -> None:
        """Estimate should be >= actual tokens for typical content.

        We verify the 3.5 ratio produces a higher estimate than the commonly
        cited 4.0 ratio, which means it's more conservative.
        """
        conservative_estimate = estimate_tokens(text)
        # The 4.0 ratio gives fewer tokens (less conservative)
        less_conservative = len(text) // 4
        assert conservative_estimate >= less_conservative

    # T1.7: Unicode text
    def test_unicode_text(self) -> None:
        """Unicode text (CJK, emoji, mixed scripts) should not crash."""
        text = "你好世界 🎵 café naïve"
        result = estimate_tokens(text)
        assert result > 0
        assert isinstance(result, int)

    def test_emoji_heavy_text(self) -> None:
        """Emoji sequences should produce reasonable estimates."""
        text = "🎵🎶🎹🎻🎺" * 100
        result = estimate_tokens(text)
        assert result > 0

    # T1.8: Very large input
    def test_large_input_performance(self) -> None:
        """10M character input should complete without OOM."""
        text = "x" * 10_000_000
        result = estimate_tokens(text)
        assert result == math.ceil(10_000_000 / _CHARS_PER_TOKEN)

    # T1.9: get_effective_window_size with known model
    def test_known_model_window(self) -> None:
        """Known Claude model returns a positive window size."""
        result = get_effective_window_size("claude-sonnet-4-20250514")
        assert result > 0
        assert result == 196_000

    def test_known_model_shorthand(self) -> None:
        """Shorthand aliases return correct window."""
        assert get_effective_window_size("sonnet") == 196_000
        assert get_effective_window_size("opus") == 196_000
        assert get_effective_window_size("haiku") == 196_000

    # T1.10: Unknown model
    def test_unknown_model_returns_default(self) -> None:
        """Unknown model returns a conservative default, not zero or crash."""
        result = get_effective_window_size("gpt-99-turbo")
        assert result > 0
        assert result == _DEFAULT_EFFECTIVE_WINDOW

    # T1.11: None model
    def test_none_model_returns_default(self) -> None:
        result = get_effective_window_size(None)
        assert result > 0
        assert result == _DEFAULT_EFFECTIVE_WINDOW

    # Additional adversarial cases
    def test_non_string_non_dict_non_list_input(self) -> None:
        """Arbitrary objects are coerced via str()."""
        result = estimate_tokens(42)
        assert result > 0
        assert result == estimate_tokens("42")

    def test_single_char(self) -> None:
        """Single character should produce 1 token (ceil)."""
        assert estimate_tokens("x") == 1

    def test_whitespace_only(self) -> None:
        """Whitespace-only strings are not empty."""
        result = estimate_tokens("   \n\t  ")
        assert result > 0

    def test_model_window_all_entries_positive(self) -> None:
        """Every entry in the model window table is a positive integer."""
        for model, window in _MODEL_EFFECTIVE_WINDOWS.items():
            assert isinstance(window, int), f"Window for {model} is not int"
            assert window > 0, f"Window for {model} is not positive"


class TestTokenBudgetTracker:
    """Tests for the TokenBudgetTracker class (T2.*)."""

    # T2.1: Fresh tracker has full budget
    def test_fresh_tracker_full_budget(self) -> None:
        tracker = TokenBudgetTracker(window_size=200_000)
        assert tracker.remaining() == 200_000

    def test_fresh_tracker_zero_utilization(self) -> None:
        tracker = TokenBudgetTracker(window_size=200_000)
        assert tracker.utilization() == 0.0

    def test_fresh_tracker_empty_breakdown(self) -> None:
        tracker = TokenBudgetTracker(window_size=200_000)
        assert tracker.breakdown() == {}

    # T2.2: Allocation reduces remaining budget
    def test_allocation_reduces_remaining(self) -> None:
        tracker = TokenBudgetTracker(window_size=200_000)
        text = "some text"
        tokens = estimate_tokens(text)

        result = tracker.allocate(text, "template")
        assert result is True
        assert tracker.remaining() == 200_000 - tokens
        assert tracker.utilization() > 0.0

        breakdown = tracker.breakdown()
        assert "template" in breakdown
        assert breakdown["template"] == tokens

    # T2.3: can_fit respects remaining budget
    def test_can_fit_rejects_over_budget(self) -> None:
        tracker = TokenBudgetTracker(window_size=100)
        # 700 chars at 3.5 ratio = 200 tokens — won't fit in 100
        assert tracker.can_fit("a" * 700) is False

    # T2.4: Exact fit boundary
    def test_can_fit_exact_boundary(self) -> None:
        tracker = TokenBudgetTracker(window_size=100)
        # 350 chars at 3.5 ratio = exactly 100 tokens
        assert tracker.can_fit("a" * 350) is True

    # T2.5: Over-allocation is prevented
    def test_allocate_rejects_over_budget(self) -> None:
        tracker = TokenBudgetTracker(window_size=100)
        result = tracker.allocate("a" * 700, "overflow_component")
        assert result is False
        # State should be unchanged
        assert tracker.remaining() == 100
        assert tracker.breakdown() == {}

    # T2.6: Multiple allocations track correctly
    def test_multiple_allocations(self) -> None:
        tracker = TokenBudgetTracker(window_size=10_000)
        text_a = "a" * 350   # ~100 tokens
        text_b = "b" * 700   # ~200 tokens
        text_c = "c" * 1050  # ~300 tokens

        assert tracker.allocate(text_a, "template") is True
        assert tracker.allocate(text_b, "patterns") is True
        assert tracker.allocate(text_c, "specs") is True

        est_a = estimate_tokens(text_a)
        est_b = estimate_tokens(text_b)
        est_c = estimate_tokens(text_c)

        assert tracker.remaining() == 10_000 - est_a - est_b - est_c

        breakdown = tracker.breakdown()
        assert len(breakdown) == 3
        assert breakdown["template"] == est_a
        assert breakdown["patterns"] == est_b
        assert breakdown["specs"] == est_c

    # T2.7: Reset clears all allocations
    def test_reset_restores_budget(self) -> None:
        tracker = TokenBudgetTracker(window_size=10_000)
        tracker.allocate("some content", "template")
        tracker.allocate("more content", "patterns")

        assert tracker.remaining() < 10_000

        tracker.reset()

        assert tracker.remaining() == 10_000
        assert tracker.breakdown() == {}
        assert tracker.utilization() == 0.0

    # T2.8: Zero-budget tracker rejects everything
    def test_zero_budget_rejects_all(self) -> None:
        tracker = TokenBudgetTracker(window_size=0)
        assert tracker.can_fit("any text") is False
        assert tracker.allocate("any text", "component") is False

    def test_zero_budget_utilization(self) -> None:
        """Zero-budget tracker should not divide by zero."""
        tracker = TokenBudgetTracker(window_size=0)
        assert tracker.utilization() == 0.0

    # T2.9: Negative remaining is impossible
    def test_remaining_never_negative(self) -> None:
        tracker = TokenBudgetTracker(window_size=10)
        # Even if we somehow got into a bad state, remaining is clamped to 0
        assert tracker.remaining() >= 0
        # After failed allocation, still >= 0
        tracker.allocate("a" * 10000, "huge")
        assert tracker.remaining() >= 0

    def test_negative_window_raises(self) -> None:
        """Negative window_size should raise ValueError at construction."""
        with pytest.raises(ValueError, match="window_size must be >= 0"):
            TokenBudgetTracker(window_size=-1)

    # Additional adversarial tests
    def test_allocate_none_is_free(self) -> None:
        """Allocating None content uses zero tokens."""
        tracker = TokenBudgetTracker(window_size=1000)
        result = tracker.allocate(None, "empty")
        assert result is True
        assert tracker.remaining() == 1000

    def test_allocate_empty_string_is_free(self) -> None:
        """Allocating empty string uses zero tokens."""
        tracker = TokenBudgetTracker(window_size=1000)
        result = tracker.allocate("", "empty")
        assert result is True
        assert tracker.remaining() == 1000

    def test_can_fit_does_not_modify_state(self) -> None:
        """can_fit is a pure query — no side effects."""
        tracker = TokenBudgetTracker(window_size=1000)
        remaining_before = tracker.remaining()
        tracker.can_fit("some text")
        assert tracker.remaining() == remaining_before

    def test_breakdown_aggregates_same_component(self) -> None:
        """Multiple allocations to the same component are summed."""
        tracker = TokenBudgetTracker(window_size=100_000)
        tracker.allocate("part one", "template")
        tracker.allocate("part two", "template")

        breakdown = tracker.breakdown()
        expected = estimate_tokens("part one") + estimate_tokens("part two")
        assert breakdown["template"] == expected

    def test_utilization_capped_at_one(self) -> None:
        """Utilization never exceeds 1.0 even in edge cases."""
        tracker = TokenBudgetTracker(window_size=100_000)
        # Normal allocation
        tracker.allocate("x" * 50_000, "content")
        assert 0.0 <= tracker.utilization() <= 1.0


class TestEstimatorReconciliation:
    """T10.4: Verify divergent estimator behavior is documented and consistent."""

    def test_preflight_uses_canonical_estimator(self) -> None:
        """After reconciliation, preflight delegates to estimate_tokens().

        Both preflight and tokens.py must produce identical estimates,
        confirming a single source of truth for token estimation.
        """
        from marianne.execution.preflight import PromptMetrics

        text = "x" * 10_000

        # tokens.py canonical estimate
        centralized = estimate_tokens(text)
        # preflight estimate (should now delegate to estimate_tokens)
        preflight = PromptMetrics.from_prompt(text).estimated_tokens

        assert centralized == preflight, (
            f"tokens.py estimate ({centralized}) should equal preflight estimate ({preflight}) "
            "after reconciliation — both must use the same estimator"
        )

    def test_single_source_of_truth_exists(self) -> None:
        """tokens.py exports estimate_tokens as the canonical estimator."""
        from marianne.core.tokens import estimate_tokens as canonical
        assert callable(canonical)
        # The function is deterministic
        assert canonical("test") == canonical("test")


class TestInstrumentAwareWindows:
    """Tests for instrument-aware window sizing in get_effective_window_size()."""

    # Instrument table completeness
    def test_instrument_table_all_positive(self) -> None:
        """Every entry in the instrument window table is a positive integer."""
        for instrument, window in _INSTRUMENT_EFFECTIVE_WINDOWS.items():
            assert isinstance(window, int), f"Window for {instrument} is not int"
            assert window > 0, f"Window for {instrument} is not positive"

    # Known instrument lookups
    @pytest.mark.parametrize(
        "instrument,expected",
        [
            ("claude-code", 196_000),
            ("gemini-cli", 1_000_000),
            ("codex-cli", 196_000),
            ("ollama", 128_000),
            ("anthropic-api", 196_000),
            ("claude-cli", 196_000),
        ],
        ids=["claude-code", "gemini-cli", "codex-cli", "ollama", "anthropic-api", "claude-cli"],
    )
    def test_known_instrument_window(self, instrument: str, expected: int) -> None:
        """Known instruments return their configured window size."""
        result = get_effective_window_size(instrument=instrument)
        assert result == expected

    # Unknown instrument imposes no additional limit
    def test_unknown_instrument_no_limit(self) -> None:
        """Unknown instruments impose no additional constraint — model window wins."""
        result = get_effective_window_size(model="sonnet", instrument="unknown-tool")
        assert result == 196_000  # Model window, not affected by unknown instrument

    def test_unknown_instrument_alone_returns_default(self) -> None:
        """Unknown instrument with no model returns the default window."""
        result = get_effective_window_size(instrument="unknown-tool")
        assert result == _DEFAULT_EFFECTIVE_WINDOW

    # Instrument + model interaction: min(instrument, model)
    def test_instrument_constrains_model(self) -> None:
        """When instrument window < model window, instrument wins."""
        # ollama instrument = 128K, but model "sonnet" = 196K
        result = get_effective_window_size(model="sonnet", instrument="ollama")
        assert result == 128_000  # min(196K, 128K)

    def test_model_constrains_instrument(self) -> None:
        """When model window < instrument window, model wins."""
        # llama3 model = 6K, gemini-cli instrument = 1M
        result = get_effective_window_size(model="llama3", instrument="gemini-cli")
        assert result == 6_000  # min(6K, 1M)

    def test_both_equal_returns_that_value(self) -> None:
        """When model and instrument have the same window, returns that value."""
        result = get_effective_window_size(model="sonnet", instrument="claude-code")
        assert result == 196_000  # Both are 196K

    # None instrument = backward compatibility
    def test_none_instrument_backward_compatible(self) -> None:
        """instrument=None gives same result as old single-param behavior."""
        assert get_effective_window_size(model="sonnet") == 196_000
        assert get_effective_window_size(model="sonnet", instrument=None) == 196_000

    # Case insensitivity for instruments
    @pytest.mark.parametrize(
        "instrument",
        ["Claude-Code", "CLAUDE-CODE", "claude-code", "Claude-code"],
        ids=["mixed", "upper", "lower", "title"],
    )
    def test_instrument_case_insensitive(self, instrument: str) -> None:
        """Instrument lookup is case-insensitive."""
        result = get_effective_window_size(instrument=instrument)
        assert result == 196_000

    # Underscore/hyphen normalization for instruments
    @pytest.mark.parametrize(
        "instrument,expected",
        [
            ("claude_cli", 196_000),   # underscore → hyphen
            ("claude-cli", 196_000),   # already hyphenated
            ("gemini_cli", 1_000_000), # underscore variant
            ("codex_cli", 196_000),    # underscore variant
            ("anthropic_api", 196_000),  # underscore variant
        ],
        ids=["claude_cli", "claude-cli", "gemini_cli", "codex_cli", "anthropic_api"],
    )
    def test_instrument_underscore_hyphen_normalization(
        self, instrument: str, expected: int
    ) -> None:
        """Both underscore and hyphen variants resolve to the same instrument."""
        assert get_effective_window_size(instrument=instrument) == expected


class TestModelCaseInsensitivity:
    """Tests for case-insensitive model name lookup."""

    def test_exact_match_case_insensitive(self) -> None:
        """Model exact lookup is case-insensitive."""
        assert get_effective_window_size(model="Sonnet") == 196_000
        assert get_effective_window_size(model="OPUS") == 196_000
        assert get_effective_window_size(model="Haiku") == 196_000

    def test_versioned_model_case_insensitive(self) -> None:
        """Versioned model names work case-insensitively."""
        assert get_effective_window_size(model="Claude-Sonnet-4-20250514") == 196_000

    def test_ollama_model_case_insensitive(self) -> None:
        """Ollama model names work case-insensitively."""
        assert get_effective_window_size(model="LLama3") == 6_000
        assert get_effective_window_size(model="Mixtral") == 30_000


class TestEstimateTokensEdgeCases:
    """Edge case tests for estimate_tokens (F-001 investigation findings).

    Covers: null bytes, CJK text, bool/float/bytes inputs, empty
    collections, and max-int window. These were identified as gaps
    in the Foundation team's cycle 1 investigation.
    """

    def test_null_bytes_in_string(self) -> None:
        """Strings containing null bytes are handled without crash."""
        text = "hello\x00world\x00end"
        result = estimate_tokens(text)
        assert result > 0
        # Should count the full length including nulls
        assert result == math.ceil(len(text) / _CHARS_PER_TOKEN)

    def test_cjk_text_produces_estimate(self) -> None:
        """CJK text produces a token estimate (known to underestimate).

        F-001 documents that CJK text is underestimated by 3.5-7x because
        the ratio is calibrated for English. This test verifies the function
        doesn't crash and produces SOME estimate, not that it's accurate.
        """
        cjk = "你好世界" * 150  # 600 CJK characters
        result = estimate_tokens(cjk)
        assert result > 0
        # The estimate will be ~172 tokens, but actual is 600-1200
        # We document this as a known limitation, not fix it here
        assert result == math.ceil(len(cjk) / _CHARS_PER_TOKEN)

    def test_mixed_script_text(self) -> None:
        """Mixed English/CJK/Arabic text doesn't crash."""
        mixed = "Hello 你好 مرحبا Привет こんにちは"
        result = estimate_tokens(mixed)
        assert result > 0

    def test_bool_input(self) -> None:
        """Boolean input is coerced via str() and estimated."""
        assert estimate_tokens(True) > 0  # str(True) = "True" = 4 chars
        assert estimate_tokens(False) > 0  # str(False) = "False" = 5 chars

    def test_float_input(self) -> None:
        """Float input is coerced via str() and estimated."""
        result = estimate_tokens(3.14159)
        assert result > 0
        assert result == math.ceil(len(str(3.14159)) / _CHARS_PER_TOKEN)

    def test_bytes_input(self) -> None:
        """Bytes input is coerced via str() and estimated."""
        result = estimate_tokens(b"hello world")
        assert result > 0
        # str(b"hello world") = "b'hello world'" — includes the b'' wrapper
        assert result == math.ceil(len(str(b"hello world")) / _CHARS_PER_TOKEN)

    def test_empty_dict_returns_small_estimate(self) -> None:
        """Empty dict {} serializes to '{}' — 2 chars."""
        result = estimate_tokens({})
        assert result > 0
        assert result == math.ceil(2 / _CHARS_PER_TOKEN)  # "{}" = 2 chars

    def test_empty_list_returns_small_estimate(self) -> None:
        """Empty list [] serializes to '[]' — 2 chars."""
        result = estimate_tokens([])
        assert result > 0
        assert result == math.ceil(2 / _CHARS_PER_TOKEN)  # "[]" = 2 chars

    def test_integer_input(self) -> None:
        """Integer input is coerced via str() and estimated."""
        result = estimate_tokens(42)
        assert result > 0
        assert result == math.ceil(len("42") / _CHARS_PER_TOKEN)
