"""#201: a remedy that raises during apply() must not crash heal().

When the baton wires the SelfHealingCoordinator into its exhaustion path,
`heal()` runs on the conductor's critical path. Previously `remedy.apply()` was
called un-guarded inside `heal()`'s loop — a remedy that raised would crash the
entire heal (and, where backends already call heal, mask the original failure).
The crash is now caught and recorded as a failed action, so the report stays
truthful and `should_retry` (which keys off *successful* remedies) is unaffected
by a crashing remedy.
"""

from __future__ import annotations

import pytest

from marianne.healing.context import ErrorContext
from marianne.healing.coordinator import SelfHealingCoordinator
from marianne.healing.registry import RemedyRegistry
from marianne.healing.remedies.base import RemedyCategory, RemedyResult


class _Diag:
    def __init__(self, name: str) -> None:
        self.remedy_name = name
        self.confidence = "high"
        self.suggestion = "x"


class _CrashingRemedy:
    name = "crashing"
    category = RemedyCategory.AUTOMATIC

    def diagnose(self, context: ErrorContext) -> object | None:
        return _Diag(self.name)

    def apply(self, context: ErrorContext) -> RemedyResult:
        raise RuntimeError("boom — remedy blew up")

    def preview(self, context: ErrorContext) -> str:
        return "would crash"

    def generate_diagnostic(self, context: ErrorContext) -> str:
        return "diag"


class _GoodRemedy:
    name = "good"
    category = RemedyCategory.AUTOMATIC

    def diagnose(self, context: ErrorContext) -> object | None:
        return _Diag(self.name)

    def apply(self, context: ErrorContext) -> RemedyResult:
        return RemedyResult(success=True, message="fixed", action_taken="fixed it")

    def preview(self, context: ErrorContext) -> str:
        return "would fix"

    def generate_diagnostic(self, context: ErrorContext) -> str:
        return "diag"


def _ctx() -> ErrorContext:
    return ErrorContext(
        error_code="E999",
        error_message="boom",
        error_category="execution",
    )


@pytest.fixture
def _registry_with():
    def _make(*remedies):
        reg = RemedyRegistry()
        for r in remedies:
            reg.register(r)
        return reg
    return _make


class TestApplyGuard:
    async def test_crashing_remedy_does_not_crash_heal(self, _registry_with) -> None:
        reg = _registry_with(_CrashingRemedy())
        coordinator = SelfHealingCoordinator(reg)
        report = await coordinator.heal(_ctx())  # must NOT raise
        # The crash is recorded as a failed action, not propagated.
        assert any(
            name == "crashing" and not result.success
            for name, result in report.actions_taken
        )
        assert report.should_retry is False  # no remedy succeeded

    async def test_crashing_remedy_does_not_block_a_good_one(self, _registry_with) -> None:
        reg = _registry_with(_CrashingRemedy(), _GoodRemedy())
        coordinator = SelfHealingCoordinator(reg)
        report = await coordinator.heal(_ctx())
        results = {name: r.success for name, r in report.actions_taken}
        assert results.get("crashing") is False
        assert results.get("good") is True
        assert report.should_retry is True  # the good remedy succeeded
