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


@pytest.mark.parametrize(
    "bad_value",
    ["garbage", "", "  ", "1,5", "1e", "None", "nan", "NaN", "inf", "-inf", "+inf", "Infinity"],
)
def test_bad_factor_falls_back_to_base(bad_value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", bad_value)
    assert load_scaled_timeout(180) == 180


def test_default_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARIANNE_TEST_LOAD_FACTOR", raising=False)
    assert load_scaled_timeout(180) == 180


def test_factor_one_is_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", "1")
    assert load_scaled_timeout(120) == 120


@pytest.mark.parametrize("non_positive", ["0", "0.0", "-0.1", "-1", "-5.5"])
def test_factor_below_one_clamps_to_base(
    non_positive: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", non_positive)
    assert load_scaled_timeout(180) == 180


def test_return_type_is_always_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIANNE_TEST_LOAD_FACTOR", "2.5")
    res = load_scaled_timeout(180)
    assert isinstance(res, int)
    assert res == 450


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


def test_marked_tests_have_valid_timeout_and_load_sensitive_markers() -> None:
    """Regression test: verify all four target tests carry load_sensitive and timeout marks."""
    from tests.test_clone_socket_ownership_repairs import (
        test_cancelled_start_reaps_upstream_and_releases_socket,
    )
    from tests.test_daemon_e2e import TestDaemonLifecycle
    from tests.test_drain_swap_352 import TestDrainDeadlockBattery

    target_cases = [
        ("test_large_stdout_burst", TestDrainDeadlockBattery.test_large_stdout_burst, 180),
        ("test_one_pipe_closed_early", TestDrainDeadlockBattery.test_one_pipe_closed_early, 120),
        (
            "test_cancelled_start_reaps_upstream_and_releases_socket",
            test_cancelled_start_reaps_upstream_and_releases_socket,
            120,
        ),
        ("test_graceful_shutdown", TestDaemonLifecycle.test_graceful_shutdown, 120),
    ]

    for name, func, expected_base in target_cases:
        marks = {m.name: m for m in getattr(func, "pytestmark", [])}
        assert "load_sensitive" in marks, f"{name} missing load_sensitive mark"
        assert "timeout" in marks, f"{name} missing timeout mark"
        assert marks["timeout"].args[0] == expected_base, (
            f"{name} timeout expected base {expected_base}, got {marks['timeout'].args[0]}"
        )

