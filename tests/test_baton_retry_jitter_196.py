"""#196: adaptive retry — wire RetryConfig into the baton + equal jitter.

The baton already had exponential backoff (`BatonCore.calculate_retry_delay`)
but HARDCODED the params (ignoring a parsed `RetryConfig`) and ignored the
`jitter` flag entirely. Per the 4-model lab (unanimous Option A; divergences
resolved against the code):

- A `BatonCore.configure_retry(...)` setter threads base_delay / exponential_base
  / max_delay / jitter from `RetryConfig` (per-job/BatonCore-level — distinct
  from the per-sheet `max_retries`). The manager calls it once at registration
  AND recovery.
- `calculate_retry_delay` applies EQUAL jitter when enabled:
  `raw/2 + jitter_fn(0, raw/2)` — floor = raw/2 (never collapses backoff to ~0
  like full jitter), ceiling = raw. Spreads a thundering herd of simultaneously
  -failing fan-out sheets across [raw/2, raw] without firing sooner than half
  the intended backoff.
- Two-layer default: `BatonCore` defaults `_jitter=False` (= today's behaviour,
  so every existing bare-`BatonCore()` exact-value test stays green), while
  production honours `RetryConfig.jitter=True` via the setter (3-of-4 reviewers +
  the config's own declared default; jitter is a strict reliability win for the
  storm failure mode, with `jitter=False` as the deterministic escape hatch).
- `jitter_fn` is injectable for deterministic boundary tests (default
  `random.uniform`). Jitter NEVER touches the authoritative rate-limit wait path
  (`calculate_retry_delay` has exactly one caller, `_schedule_retry`).

Deferred (need a learning-store retry-outcomes schema that doesn't exist):
learned wait times, retry-outcome tracking, error-type-specific delays.
"""

from __future__ import annotations

from marianne.daemon.baton.core import BatonCore


def _raw(base: float, exp: float, attempt: int, max_delay: float) -> float:
    return min(base * (exp**attempt), max_delay)


# ── configure_retry threads RetryConfig into BatonCore ────────────────────


class TestConfigureRetry:
    def test_sets_backoff_params(self) -> None:
        baton = BatonCore()
        baton.configure_retry(
            base_delay=30.0, exponential_base=1.5, max_delay=600.0, jitter=False
        )
        assert baton._base_retry_delay == 30.0
        assert baton._retry_exponential_base == 1.5
        assert baton._max_retry_delay == 600.0
        # attempt 2 = 30 * 1.5^2 = 67.5
        assert baton.calculate_retry_delay(2) == 67.5

    def test_partial_update_leaves_others(self) -> None:
        baton = BatonCore()
        before_base = baton._base_retry_delay
        baton.configure_retry(max_delay=99.0)
        assert baton._max_retry_delay == 99.0
        assert baton._base_retry_delay == before_base

    def test_default_no_jitter_matches_today(self) -> None:
        # A bare BatonCore (no configure_retry) must behave exactly as before:
        # pure exponential backoff, no jitter.
        baton = BatonCore()
        assert baton._jitter is False
        assert baton.calculate_retry_delay(0) == baton._base_retry_delay
        assert baton.calculate_retry_delay(1) == baton._base_retry_delay * 2
        assert baton.calculate_retry_delay(2) == baton._base_retry_delay * 4


# ── equal jitter math (deterministic via injected jitter_fn) ──────────────


class TestEqualJitter:
    def test_ceiling_when_fn_returns_high(self) -> None:
        baton = BatonCore()
        baton.configure_retry(jitter=True, jitter_fn=lambda lo, hi: hi)
        raw = _raw(10.0, 2.0, 3, 3600.0)  # 80
        # raw/2 + (raw/2) == raw
        assert baton.calculate_retry_delay(3) == raw

    def test_floor_when_fn_returns_low(self) -> None:
        baton = BatonCore()
        baton.configure_retry(jitter=True, jitter_fn=lambda lo, hi: lo)
        raw = _raw(10.0, 2.0, 3, 3600.0)  # 80
        assert baton.calculate_retry_delay(3) == raw / 2

    def test_midpoint(self) -> None:
        baton = BatonCore()
        baton.configure_retry(jitter=True, jitter_fn=lambda lo, hi: (lo + hi) / 2)
        raw = _raw(10.0, 2.0, 2, 3600.0)  # 40
        # raw/2 + (raw/2)/2 == 0.75 * raw
        assert baton.calculate_retry_delay(2) == 0.75 * raw

    def test_jitter_range_over_samples(self) -> None:
        # With real randomness, every sample stays in [raw/2, raw] — this also
        # catches an accidental FULL-jitter impl (which could return < raw/2).
        baton = BatonCore()
        baton.configure_retry(jitter=True)  # default random.uniform
        for attempt in range(6):
            raw = _raw(
                baton._base_retry_delay,
                baton._retry_exponential_base,
                attempt,
                baton._max_retry_delay,
            )
            for _ in range(50):
                d = baton.calculate_retry_delay(attempt)
                assert raw / 2 <= d <= raw

    def test_jittered_never_exceeds_max(self) -> None:
        baton = BatonCore()
        baton.configure_retry(jitter=True, jitter_fn=lambda lo, hi: hi)
        for attempt in range(60):
            assert baton.calculate_retry_delay(attempt) <= baton._max_retry_delay

    def test_floor_is_monotonic_non_decreasing(self) -> None:
        # The minimum possible delay (raw/2) grows with each attempt — the
        # surviving (weakened) form of invariant #57 under equal jitter.
        baton = BatonCore()
        baton.configure_retry(jitter=True)
        floors = [
            _raw(
                baton._base_retry_delay,
                baton._retry_exponential_base,
                a,
                baton._max_retry_delay,
            )
            / 2
            for a in range(30)
        ]
        for i in range(1, len(floors)):
            assert floors[i] >= floors[i - 1]


# ── jitter=False is a bit-exact escape hatch ──────────────────────────────


class TestJitterDisabledIsExact:
    def test_exact_values_when_disabled(self) -> None:
        baton = BatonCore()
        baton.configure_retry(
            base_delay=10.0, exponential_base=2.0, max_delay=3600.0, jitter=False
        )
        assert baton.calculate_retry_delay(0) == 10.0
        assert baton.calculate_retry_delay(1) == 20.0
        assert baton.calculate_retry_delay(2) == 40.0
        assert baton.calculate_retry_delay(99) == 3600.0  # clamped

    def test_disabled_does_not_consult_jitter_fn(self) -> None:
        calls: list[tuple[float, float]] = []

        def spy(lo: float, hi: float) -> float:
            calls.append((lo, hi))
            return hi

        baton = BatonCore()
        baton.configure_retry(jitter=False, jitter_fn=spy)
        baton.calculate_retry_delay(3)
        assert calls == []
