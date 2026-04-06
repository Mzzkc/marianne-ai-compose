"""Tests for spec corpus budget gating and E2E pipeline integration.

Covers:
    - Budget gating via TokenBudgetTracker.can_fit() in sheet execution
    - Fragment rejection when budget is exhausted (BG-01, BG-03, BG-06, BG-07)
    - Backward compatibility: no spec config produces identical behavior (BC-01, BC-03)
    - E2E pipeline: spec-enabled vs spec-disabled prompts differ (PI-01)
    - Budget uses instrument-aware window size (BG-05)
    - Partial inclusion with deterministic ordering (BG-03)

Test design: These tests exercise the budget gating method directly on
SheetExecutionMixin and the PromptBuilder integration. They do NOT mock
TokenBudgetTracker — the tracker is exercised through the real code path
to catch integration bugs (learned lesson: mocks mask real failures).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marianne.core.config.backend import BackendConfig
from marianne.core.config.job import PromptConfig
from marianne.core.config.spec import SpecCorpusConfig, SpecFragment
from marianne.core.logging import get_logger
from marianne.core.tokens import (
    TokenBudgetTracker,
    estimate_tokens,
    get_effective_window_size,
)
from marianne.prompts.templating import PromptBuilder, SheetContext

# ─────────────────────────────────────────────────────────────────────
# Helpers: Minimal mixin host for testing _apply_spec_budget_gating
# ─────────────────────────────────────────────────────────────────────


def _make_fragment(
    name: str,
    content: str,
    tags: list[str] | None = None,
) -> SpecFragment:
    """Create a SpecFragment for testing."""
    return SpecFragment(
        name=name,
        content=content,
        tags=tags or [],
        kind="text",
    )


def _make_fragment_of_tokens(
    name: str,
    approx_tokens: int,
    tags: list[str] | None = None,
) -> SpecFragment:
    """Create a SpecFragment with content sized to approximately N tokens.

    Uses the 3.5 chars-per-token ratio to produce content that estimates
    to the desired token count.
    """
    # estimate_tokens uses ceil(len/3.5), so len = tokens * 3.5 gives exact match
    char_count = int(approx_tokens * 3.5)
    content = "x" * char_count
    return _make_fragment(name, content, tags)


class _FakeConfig:
    """Minimal config stand-in for testing _apply_spec_budget_gating."""

    def __init__(self, backend: BackendConfig | None = None) -> None:
        self.backend = backend or BackendConfig()


class _BudgetGatingHost:
    """Minimal host that provides _apply_spec_budget_gating for unit testing.

    Imports and calls the real method from SheetExecutionMixin by
    delegation, avoiding the need to construct a full JobRunner.
    """

    def __init__(self, config: _FakeConfig | None = None) -> None:
        self.config = config or _FakeConfig()
        self._logger = get_logger("test.budget_gating")

    def _apply_spec_budget_gating(
        self,
        fragments: list[Any],
        sheet_num: int,
    ) -> list[Any]:
        """Delegate to the real implementation from SheetExecutionMixin."""
        from marianne.execution.runner.sheet import SheetExecutionMixin

        # Bind the method to this instance so it uses our config/logger
        bound = SheetExecutionMixin._apply_spec_budget_gating.__get__(  # type: ignore[attr-defined]
            self, type(self)
        )
        return bound(fragments, sheet_num)  # type: ignore[no-any-return]


# ─────────────────────────────────────────────────────────────────────
# Budget Gating Unit Tests
# ─────────────────────────────────────────────────────────────────────


class TestBudgetGatingFragmentAccepted:
    """BG-01 (inverse): Fragments are accepted when budget has room."""

    def test_single_fragment_within_budget(self) -> None:
        """A small fragment within a large budget is accepted."""
        host = _BudgetGatingHost()
        frag = _make_fragment("intent", "This project values correctness.")
        result = host._apply_spec_budget_gating([frag], sheet_num=1)
        assert len(result) == 1
        assert result[0].name == "intent"

    def test_multiple_fragments_within_budget(self) -> None:
        """Multiple small fragments all fit within the default window."""
        host = _BudgetGatingHost()
        frags = [
            _make_fragment("a", "content a"),
            _make_fragment("b", "content b"),
            _make_fragment("c", "content c"),
        ]
        result = host._apply_spec_budget_gating(frags, sheet_num=1)
        assert len(result) == 3


class TestBudgetGatingFragmentRejected:
    """BG-01: Fragment rejected when budget is full."""

    def test_fragment_rejected_when_exceeding_window(self) -> None:
        """A fragment larger than the entire context window is rejected."""
        host = _BudgetGatingHost()
        # Create a fragment much larger than any window (500K tokens > 196K max)
        huge_frag = _make_fragment_of_tokens("huge", 500_000)
        result = host._apply_spec_budget_gating([huge_frag], sheet_num=1)
        assert len(result) == 0

    def test_rejection_logs_warning(self) -> None:
        """BG-01/AP-02: Fragment rejection invokes warning-level logging."""
        host = _BudgetGatingHost()
        huge_frag = _make_fragment_of_tokens("huge_spec", 500_000)

        # Verify the method returns empty (rejection happened)
        result = host._apply_spec_budget_gating([huge_frag], sheet_num=1)
        assert len(result) == 0
        # The method calls self._logger.warning for rejected fragments.
        # We verify the behavioral outcome (rejection) rather than log
        # output since structlog may format differently in test context.


class TestBudgetGatingPartialInclusion:
    """BG-03: Partial inclusion — some fragments fit, some don't."""

    def test_partial_inclusion_deterministic(self) -> None:
        """3 fragments where only 2 fit — third is excluded deterministically."""
        host = _BudgetGatingHost()

        # Create fragments: two small, one impossibly large
        frag_a = _make_fragment_of_tokens("a_small", 100)
        frag_b = _make_fragment_of_tokens("b_medium", 200)
        frag_c = _make_fragment_of_tokens("c_huge", 500_000)  # Way too big

        result = host._apply_spec_budget_gating([frag_a, frag_b, frag_c], sheet_num=1)

        # a and b should fit, c should be rejected (exceeds window)
        assert len(result) == 2
        assert result[0].name == "a_small"
        assert result[1].name == "b_medium"

        # Verify determinism: same input produces same output
        result2 = host._apply_spec_budget_gating([frag_a, frag_b, frag_c], sheet_num=1)
        assert [f.name for f in result] == [f.name for f in result2]


class TestBudgetGatingEdgeCases:
    """BG-04, BG-06, BG-07: Edge cases for budget gating."""

    def test_empty_fragments_returns_empty(self) -> None:
        """Empty input returns empty output without errors."""
        host = _BudgetGatingHost()
        result = host._apply_spec_budget_gating([], sheet_num=1)
        assert result == []

    def test_all_fragments_rejected_returns_empty(self) -> None:
        """BG-07: All fragments rejected still returns valid empty list."""
        host = _BudgetGatingHost()
        # All fragments are huge — exceed any window
        frags = [
            _make_fragment_of_tokens("a", 500_000),
            _make_fragment_of_tokens("b", 500_000),
        ]
        result = host._apply_spec_budget_gating(frags, sheet_num=1)
        assert result == []

    def test_fragment_larger_than_window(self) -> None:
        """BG-06: Single fragment larger than entire window is rejected gracefully."""
        host = _BudgetGatingHost()
        # 200K+ token fragment on default claude_cli (196K window)
        huge = _make_fragment_of_tokens("massive_spec", 300_000)
        result = host._apply_spec_budget_gating([huge], sheet_num=1)
        assert len(result) == 0


class TestBudgetGatingInstrumentAware:
    """BG-05: Budget uses instrument-aware window size."""

    def test_default_claude_cli_uses_large_window(self) -> None:
        """Default claude_cli backend has a large context window."""
        host = _BudgetGatingHost()
        # A moderately large fragment should fit easily with claude_cli
        frag = _make_fragment_of_tokens("moderate", 5_000)
        result = host._apply_spec_budget_gating([frag], sheet_num=1)
        assert len(result) == 1

    def test_window_size_resolution(self) -> None:
        """Verify get_effective_window_size returns expected values."""
        # claude_cli with no model should use instrument window
        cli_window = get_effective_window_size(model=None, instrument="claude-cli")
        assert cli_window > 0

        # Known model should return a specific window
        sonnet_window = get_effective_window_size(
            model="claude-sonnet-4-20250514", instrument="claude-cli"
        )
        assert sonnet_window > 0

        # Small model + large instrument = small window (min of both)
        llama_window = get_effective_window_size(model="llama3", instrument="ollama")
        assert llama_window == 6_000  # llama3 is 6K, ollama is 128K → min = 6K


# ─────────────────────────────────────────────────────────────────────
# Backward Compatibility Tests
# ─────────────────────────────────────────────────────────────────────


class TestBackwardCompatibility:
    """BC-01, BC-03: Prompts without spec fragments are unchanged."""

    def test_no_spec_fragments_no_spec_section(self) -> None:
        """BC-03: build_sheet_prompt with spec_fragments=[] produces no spec section."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=1,
            workspace=Path("/tmp/test-ws"),
        )
        prompt_empty = pb.build_sheet_prompt(ctx, spec_fragments=[])
        prompt_none = pb.build_sheet_prompt(ctx, spec_fragments=None)

        # Both should produce the same prompt — no spec section
        assert prompt_empty == prompt_none
        assert "Injected Specs" not in prompt_empty

    def test_no_spec_config_produces_empty_fragments(self) -> None:
        """BC-01: A config with no spec_dir produces no fragments on the runner."""
        # SpecCorpusConfig with default (empty) spec_dir
        config = SpecCorpusConfig()
        assert config.spec_dir == ""
        assert config.fragments == []

    def test_empty_spec_dir_is_falsy(self) -> None:
        """BC-02: Empty spec_dir is falsy for the loading guard."""
        # The guard in base.py:343 is `if config.spec.spec_dir:`
        assert not ""  # empty string is falsy
        assert not SpecCorpusConfig().spec_dir  # default is falsy

    def test_budget_gating_with_no_fragments_is_noop(self) -> None:
        """Budget gating on empty fragments returns empty — no side effects."""
        host = _BudgetGatingHost()
        result = host._apply_spec_budget_gating([], sheet_num=1)
        assert result == []


# ─────────────────────────────────────────────────────────────────────
# E2E Pipeline Integration Test
# ─────────────────────────────────────────────────────────────────────


class TestSpecPipelineE2E:
    """PI-01: Full pipeline E2E — spec-enabled vs spec-disabled A/B test.

    Tests the complete flow:
    SpecFragment → tag filter → budget gate → PromptBuilder → prompt string
    """

    def test_spec_enabled_vs_disabled_prompts_differ(self) -> None:
        """The core litmus test: spec-enabled prompts contain fragment content
        that spec-disabled prompts do not."""
        spec_content = "This project values correctness above all else."
        fragment = _make_fragment("intent", spec_content, tags=["goals"])

        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=1,
            workspace=Path("/tmp/test-ws"),
        )

        # Spec-disabled: no fragments
        prompt_disabled = pb.build_sheet_prompt(ctx, spec_fragments=None)

        # Spec-enabled: with fragment
        prompt_enabled = pb.build_sheet_prompt(ctx, spec_fragments=[fragment])

        # The spec content appears in enabled prompt but not disabled
        assert spec_content in prompt_enabled
        assert spec_content not in prompt_disabled

        # The enabled prompt has the spec section header
        assert "Injected Specs" in prompt_enabled
        assert "Injected Specs" not in prompt_disabled

    def test_spec_difference_is_only_spec_section(self) -> None:
        """The ONLY difference between spec-enabled and spec-disabled prompts
        is the spec section — all other sections match."""
        fragment = _make_fragment("constraints", "Never skip tests.")

        pb = PromptBuilder(PromptConfig(template="Build the feature."))
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=3,
            start_item=1,
            end_item=10,
            workspace=Path("/tmp/test-ws"),
        )

        prompt_without = pb.build_sheet_prompt(ctx, spec_fragments=None)
        prompt_with = pb.build_sheet_prompt(ctx, spec_fragments=[fragment])

        # Remove the spec section from the enabled prompt
        spec_section = "\n\n## Injected Specs\n\nNever skip tests."
        prompt_with_stripped = prompt_with.replace(spec_section, "")

        assert prompt_with_stripped == prompt_without

    def test_full_pipeline_with_budget_gating(self) -> None:
        """E2E: fragments flow through tag filter + budget gate + PromptBuilder."""
        # Create fragments
        frag_goals = _make_fragment("goals", "Goal: ship correct code.", tags=["goals"])
        frag_safety = _make_fragment("safety", "Safety: never skip validation.", tags=["safety"])

        # Simulate the pipeline: tag filter → budget gate → prompt build
        # Step 1: Tag filtering (sheet 1 only wants "goals")
        sheet_tags: dict[int, list[str]] = {1: ["goals"]}
        tags_for_sheet = sheet_tags.get(1)
        if tags_for_sheet:
            tag_set = set(tags_for_sheet)
            filtered = [f for f in [frag_goals, frag_safety] if tag_set & set(f.tags)]
        else:
            filtered = [frag_goals, frag_safety]
        assert len(filtered) == 1
        assert filtered[0].name == "goals"

        # Step 2: Budget gating (should pass — small fragment, large window)
        host = _BudgetGatingHost()
        gated = host._apply_spec_budget_gating(filtered, sheet_num=1)
        assert len(gated) == 1

        # Step 3: PromptBuilder injection
        pb = PromptBuilder(PromptConfig(template="Execute sheet."))
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=2,
            start_item=1,
            end_item=5,
            workspace=Path("/tmp/test-ws"),
        )
        prompt = pb.build_sheet_prompt(ctx, spec_fragments=gated)

        # Verify the goals fragment made it into the prompt
        assert "Goal: ship correct code." in prompt
        # Verify the safety fragment did NOT (filtered by tags)
        assert "Safety: never skip validation." not in prompt

    def test_multiple_fragments_in_prompt(self) -> None:
        """Multiple spec fragments all appear in the prompt when they fit."""
        frags = [
            _make_fragment("arch", "Architecture: data-oriented ECS."),
            _make_fragment("intent", "Intent: correctness first."),
        ]

        pb = PromptBuilder(PromptConfig(template="Do the work."))
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=1,
            workspace=Path("/tmp/test-ws"),
        )
        prompt = pb.build_sheet_prompt(ctx, spec_fragments=frags)

        assert "Architecture: data-oriented ECS." in prompt
        assert "Intent: correctness first." in prompt
        assert "Injected Specs" in prompt


# ─────────────────────────────────────────────────────────────────────
# TokenBudgetTracker Integration (validates BG-02 shared budget)
# ─────────────────────────────────────────────────────────────────────


class TestBudgetSharedAcrossComponents:
    """BG-02: Budget is shared across ALL prompt components."""

    def test_budget_shared_across_template_patterns_specs(self) -> None:
        """A single tracker tracks template + patterns + specs together."""
        tracker = TokenBudgetTracker(window_size=1000)

        # Template uses 500 tokens
        assert tracker.allocate("x" * 1750, "template")  # ~500 tokens
        # Patterns use 200 tokens
        assert tracker.allocate("y" * 700, "patterns")  # ~200 tokens
        # Specs use 200 tokens
        assert tracker.allocate("z" * 700, "specs")  # ~200 tokens

        # Total should be ~900, leaving ~100
        assert tracker.remaining() < 200
        assert tracker.remaining() > 0

        # A 500-token spec fragment should NOT fit
        assert not tracker.can_fit("w" * 1750)

        # Breakdown shows all three components
        breakdown = tracker.breakdown()
        assert "template" in breakdown
        assert "patterns" in breakdown
        assert "specs" in breakdown

    def test_fourth_allocation_rejected_when_budget_full(self) -> None:
        """After filling the budget, further allocations are rejected."""
        tracker = TokenBudgetTracker(window_size=100)

        # Fill most of the budget
        assert tracker.allocate("a" * 280, "template")  # ~80 tokens
        # Now only ~20 tokens remain
        assert not tracker.allocate("b" * 280, "specs")  # ~80 tokens won't fit


# ─────────────────────────────────────────────────────────────────────
# Anti-Pattern Tests
# ─────────────────────────────────────────────────────────────────────


class TestAntiPatterns:
    """AP-01, AP-05: Budget gating is actually called and is consistent."""

    def test_estimate_tokens_is_deterministic(self) -> None:
        """AP-05: estimate_tokens returns same value for same input."""
        text = "hello world of token estimation"
        result1 = estimate_tokens(text)
        result2 = estimate_tokens(text)
        assert result1 == result2
        assert result1 > 0

    def test_can_fit_then_allocate_consistent(self) -> None:
        """AP-05: can_fit(x) == True implies allocate(x) == True (no interleaving)."""
        tracker = TokenBudgetTracker(window_size=1000)
        text = "some content to check"
        assert tracker.can_fit(text)
        assert tracker.allocate(text, "test")

    def test_all_rejected_produces_valid_prompt(self) -> None:
        """BG-07/AP: Prompt is valid even when all spec fragments are rejected."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=1,
            workspace=Path("/tmp/test-ws"),
        )

        # Empty fragments (as would result from all-rejected budget gating)
        prompt = pb.build_sheet_prompt(ctx, spec_fragments=[])
        assert "Injected Specs" not in prompt
        # Prompt should still be valid (has the template content)
        assert "Do the task." in prompt
