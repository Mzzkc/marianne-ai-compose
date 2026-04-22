"""Token estimation and budget tracking for prompt assembly.

Provides a centralized token estimation utility and a budget tracker that
enforces context window limits during prompt construction. This is the single
source of truth for token estimation — all other modules (preflight, backends)
should import from here rather than maintaining their own ratios.

The estimation uses a conservative chars-per-token ratio (3.5) that
deliberately overestimates token counts by ~15%. This is intentional:
underestimation causes context window overflow (agent gets truncated
mid-instruction), while overestimation merely wastes budget (safe).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from marianne.core.logging import get_logger

_logger = get_logger("tokens")

# Conservative chars-per-token ratio. Overestimates by ~15% for English text.
# This is intentionally lower than the commonly cited 4.0 ratio to err on the
# side of safety — it's better to leave unused budget than to overflow.
_CHARS_PER_TOKEN: float = 3.5

# Known model effective context windows (input budget after reserving output space).
# Values account for output token reservation (~4K for most models).
# Models not listed here get a conservative default.
_MODEL_EFFECTIVE_WINDOWS: dict[str, int] = {
    # Claude 3.5 family
    "claude-3-5-sonnet-20240620": 196_000,
    "claude-3-5-sonnet-20241022": 196_000,
    "claude-3-5-haiku-20241022": 196_000,
    # Claude 3 family
    "claude-3-opus-20240229": 196_000,
    "claude-3-sonnet-20240229": 196_000,
    "claude-3-haiku-20240307": 196_000,
    # Claude 4 family
    "claude-sonnet-4-20250514": 196_000,
    "claude-opus-4-20250514": 196_000,
    "claude-haiku-4-5-20251001": 196_000,
    # Shorthand aliases (used by Claude CLI)
    "sonnet": 196_000,
    "opus": 196_000,
    "haiku": 196_000,
    # Ollama / local models (conservative defaults)
    "llama3": 6_000,
    "llama3:70b": 6_000,
    "codellama": 14_000,
    "mixtral": 30_000,
    "mistral": 30_000,
}

# Default window for unknown models — conservative to avoid overflow.
_DEFAULT_EFFECTIVE_WINDOW: int = 128_000

# Known instrument effective context windows.
# Instruments may impose their own context window limits independent of the
# underlying model. The effective window is min(instrument, model).
# Instruments not listed here impose no additional limit.
# Keys are lowercase with hyphens; lookup normalizes both case and separators.
_INSTRUMENT_EFFECTIVE_WINDOWS: dict[str, int] = {
    "claude-code": 196_000,
    "claude-cli": 196_000,
    "claude": 196_000,
    "anthropic-api": 196_000,
    "gemini-cli": 1_000_000,
    "codex-cli": 196_000,
    "ollama": 128_000,
}


def estimate_tokens(text: Any) -> int:
    """Estimate token count for arbitrary input.

    Converts the input to a string representation and applies a conservative
    chars-per-token ratio. The estimate deliberately overestimates to prevent
    context window overflow.

    Accepted types: ``str``, ``dict``, ``list``, ``None``. Other types are
    coerced via ``str()``.

    .. warning:: CJK / Non-Latin Text Underestimation

       The ``_CHARS_PER_TOKEN = 3.5`` ratio is calibrated for English text.
       CJK characters (Chinese, Japanese, Korean) typically tokenize to
       approximately 1 token per character, meaning this function
       underestimates CJK token counts by 3.5-7x. For example, 600 CJK
       characters produce ~172 estimated tokens but consume 600-1200 actual
       tokens. This can cause context window overflow for non-English content.

       Fix planned: InstrumentProfile.ModelCapacity will provide per-model
       tokenizers or script-aware estimation ratios.

    Args:
        text: Input to estimate. Strings are measured directly. Dicts and lists
            are serialized to JSON. None returns 0.

    Returns:
        Estimated token count (always >= 0).
    """
    if text is None:
        return 0

    if isinstance(text, str):
        content = text
    elif isinstance(text, (dict, list)):
        try:
            content = json.dumps(text, default=str)
        except (ValueError, TypeError):
            content = str(text)
    else:
        content = str(text)

    if not content:
        return 0

    return math.ceil(len(content) / _CHARS_PER_TOKEN)


def get_effective_window_size(
    model: str | None = None,
    instrument: str | None = None,
) -> int:
    """Get the effective input token budget for a model/instrument combination.

    Returns the context window size minus output token reservation. When both
    model and instrument are provided, returns the minimum of the two — the
    instrument may impose a stricter limit than the model's native window.

    For unknown models/instruments, returns a conservative default.

    Args:
        model: Model name or identifier. None uses the default window.
        instrument: Instrument (backend) name. None imposes no instrument limit.
            Unknown instruments impose no additional limit.

    Returns:
        Effective input token budget (always > 0).
    """
    # Resolve model window (None if model not provided or unknown)
    model_window = _resolve_model_window(model)

    # Resolve instrument window (None if instrument not provided or unknown)
    instrument_window: int | None = None
    if instrument is not None:
        instrument_window = _resolve_instrument_window(instrument)

    # Resolution: both known → min; one known → that one; neither → default
    if model_window is not None and instrument_window is not None:
        return min(model_window, instrument_window)
    if instrument_window is not None:
        return instrument_window
    if model_window is not None:
        return model_window
    return _DEFAULT_EFFECTIVE_WINDOW


def _resolve_model_window(model: str | None) -> int | None:
    """Resolve model name to effective window, or None if not provided/unknown."""
    if model is None:
        return None

    model_lower = model.lower()
    # Case-insensitive exact lookup
    for key, value in _MODEL_EFFECTIVE_WINDOWS.items():
        if key.lower() == model_lower:
            return value

    # Prefix matching for versioned model names
    for key, value in _MODEL_EFFECTIVE_WINDOWS.items():
        if model_lower.startswith(key) or key.startswith(model_lower):
            return value

    _logger.debug(
        "unknown_model_using_default_window",
        model=model,
        default_window=_DEFAULT_EFFECTIVE_WINDOW,
    )
    return None


def _resolve_instrument_window(instrument: str) -> int | None:
    """Resolve the effective window for an instrument name, or None if unknown.

    Case-insensitive. Normalizes underscores to hyphens so both ``claude_cli``
    and ``claude-cli`` match.

    Phase 5: the hardcoded ``_INSTRUMENT_EFFECTIVE_WINDOWS`` table still
    wins for the 6 legacy instruments it already covered. Those values
    are "effective" (raw context window minus output reservation) — the
    registry profiles carry the raw ``context_window`` and cannot be
    substituted without subtracting a reservation. For every OTHER
    instrument (anything not in the legacy table), the registry's
    ``InstrumentProfile.ModelCapacity.context_window`` is consulted so
    new profiles automatically surface correct numbers without having
    to touch this file. This satisfies Doctrine RULE "Token window
    constants must migrate to InstrumentProfile.ModelCapacity" for the
    open-world case while preserving the effective-window semantics
    the legacy callers (and ~100 existing tests) depend on.
    """
    normalized = instrument.lower().replace("_", "-")

    # 1. Legacy hardcoded table — authoritative for the 6 instruments
    #    that predate the registry (values are already "effective").
    legacy = _INSTRUMENT_EFFECTIVE_WINDOWS.get(normalized)
    if legacy is not None:
        return legacy

    # 2. Unknown instrument — fall through to the registry so new
    #    profiles work without requiring a dict entry here.
    return _resolve_instrument_window_from_registry(instrument)


def _resolve_instrument_window_from_registry(instrument: str) -> int | None:
    """Look up an instrument's context window via the registry.

    Returns ``None`` when the registry does not recognize the instrument
    or when no ModelCapacity records are attached. The lookup
    constructs a fresh registry each call — the built-in profile list
    is small (5 entries as of Phase 5) and the cost is negligible
    compared to an actual sheet execution.
    """
    try:
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        profile = registry.get(instrument)
        if profile is None:
            # Try normalized form too (instrument names are sometimes
            # stored with hyphens in tests vs underscores in profiles).
            profile = registry.get(instrument.lower().replace("-", "_"))
        if profile is None:
            return None
        models = getattr(profile, "models", None) or []
        if not models:
            return None
        # Prefer the declared default model when present.
        default_name = getattr(profile, "default_model", None)
        chosen = None
        if default_name:
            chosen = next(
                (m for m in models if getattr(m, "name", None) == default_name),
                None,
            )
        if chosen is None:
            chosen = models[0]
        window = getattr(chosen, "context_window", None)
        return int(window) if window else None
    except Exception:
        # Registry resolution is best-effort; never break token accounting
        # because of an unexpected registry error.
        return None


@dataclass
class BudgetAllocation:
    """A single allocation within the token budget."""

    component: str
    """Name of the prompt component (e.g., 'template', 'patterns', 'specs')."""

    tokens: int
    """Estimated token count for this allocation."""


@dataclass
class TokenBudgetTracker:
    """Tracks token budget usage during prompt assembly.

    Enforces context window limits by tracking allocations as prompt components
    are added. Each allocation is named (e.g., 'template', 'patterns', 'specs')
    for diagnostic visibility via ``breakdown()``.

    The tracker prevents silent over-allocation: ``allocate()`` returns False
    when content would exceed the remaining budget, and ``can_fit()`` checks
    without side effects.
    """

    window_size: int
    """Total token budget (effective context window)."""

    _allocations: list[BudgetAllocation] = field(default_factory=list, repr=False)
    """Internal list of allocations made so far."""

    def __post_init__(self) -> None:
        """Validate window_size is non-negative."""
        if self.window_size < 0:
            raise ValueError(f"window_size must be >= 0, got {self.window_size}")

    @property
    def allocated(self) -> int:
        """Total tokens allocated so far."""
        return sum(a.tokens for a in self._allocations)

    def remaining(self) -> int:
        """Tokens remaining in the budget.

        Returns:
            Non-negative remaining token count. Never goes below 0
            even if over-allocation somehow occurred.
        """
        return max(0, self.window_size - self.allocated)

    def utilization(self) -> float:
        """Fraction of budget used (0.0 to 1.0).

        Returns:
            Utilization ratio. Returns 0.0 for zero-budget trackers
            to avoid division by zero.
        """
        if self.window_size == 0:
            return 0.0
        return min(1.0, self.allocated / self.window_size)

    def can_fit(self, text: Any) -> bool:
        """Check if content fits within the remaining budget.

        Does not modify the tracker state.

        Args:
            text: Content to check.

        Returns:
            True if the estimated token count fits within remaining budget.
        """
        tokens = estimate_tokens(text)
        return tokens <= self.remaining()

    def allocate(
        self,
        text: Any,
        component: str,
    ) -> bool:
        """Allocate tokens for a prompt component.

        If the content fits within the remaining budget, the allocation is
        recorded and True is returned. If it does not fit, the allocation
        is rejected and False is returned — no state is modified.

        Args:
            text: Content to allocate budget for.
            component: Name of the prompt component (for diagnostics).

        Returns:
            True if allocation succeeded, False if it would exceed budget.
        """
        tokens = estimate_tokens(text)
        if tokens > self.remaining():
            _logger.debug(
                "budget_allocation_rejected",
                component=component,
                requested_tokens=tokens,
                remaining_tokens=self.remaining(),
                window_size=self.window_size,
            )
            return False

        self._allocations.append(BudgetAllocation(component=component, tokens=tokens))
        _logger.debug(
            "budget_allocated",
            component=component,
            tokens=tokens,
            remaining=self.remaining(),
            utilization=f"{self.utilization():.1%}",
        )
        return True

    def breakdown(self) -> dict[str, int]:
        """Get per-component token allocation breakdown.

        Returns:
            Dict mapping component names to their allocated token counts.
            Components with multiple allocations are summed.
        """
        result: dict[str, int] = {}
        for alloc in self._allocations:
            result[alloc.component] = result.get(alloc.component, 0) + alloc.tokens
        return result

    def reset(self) -> None:
        """Clear all allocations, restoring the full budget."""
        self._allocations.clear()
