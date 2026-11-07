"""Unit tests for the load-scaled timeout helper (issue #501).

The helper backs ``@pytest.mark.timeout(load_scaled_timeout(N))`` marks on
load-sensitive subprocess/IO tests whose internal budgets exceed the global
30s safety net. These tests pin the scaling contract: default 1.0, linear
scaling, clamp against factors < 1, and fail-safe fallback on garbage input
(a bad env var must never brick collection).
"""

from __future__ import annotations

import pytest

from tests.helpers import load_scaled_timeout


@pytest.mark.parametrize("bad_value", ["garbage", "", "  ", "1,5", "1e", "None"])
def test_bad_factor_falls_back_to_base(bad_value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", bad_value)
    assert load_scaled_timeout(180) == 180


def test_default_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARIANNE_TEST_LOAD_FACTOR", raising=False)
    assert load_scaled_timeout(180) == 180


def test_factor_one_is_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", "1")
    assert load_scaled_timeout(120) == 120


def test_factor_below_one_clamps_to_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", "0.5")
    assert load_scaled_timeout(180) == 180


def test_fractional_factor_scales_and_ceils(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", "2.5")
    assert load_scaled_timeout(180) == 450  # ceil(180 * 2.5)


def test_factor_scaling_non_integral_result_ceils(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", "1.01")
    assert load_scaled_timeout(120) == 122  # ceil(121.2)


def test_factor_with_surrounding_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", " 2 ")
    assert load_scaled_timeout(120) == 240  # float(" 2 ") parses


def test_burst_base_dominates_internal_120s_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The outer mark must stay >= the test's internal 120s budget at factor 1."""
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", "1")
    assert load_scaled_timeout(180) >= 120
