"""#201: the baton's exhaustion Path 2 self-healing.

The real healing for an agentic failure is enriching the retry prompt with what
specifically failed (done at dispatch — see test_failure_evidence /
test_preamble_module). Path 2's job is therefore to schedule ONE targeted retry
on the healing budget; that retry is enriched at dispatch. An enriched retry
always beats a blind one, so healing always retries when the budget allows —
the prior "retry only if a filesystem remedy succeeded" gating made healing a
no-op for the dominant validation-failure case.

The SelfHealingCoordinator still runs as a complementary, NON-GATING
environment-fix pass (e.g. recreate a missing workspace dir); its result is
logged but does not decide whether we retry. These tests pin:

- the coordinator is invoked on exhaustion, and healing schedules a retry;
- a retry is scheduled even when no env remedy applies (not a blind no-op);
- a coordinator timeout/error still schedules the (enriched) retry — the
  enrichment is independent of the env-fix pass;
- the healing-attempt cap prevents a second heal → escalation.
"""

from __future__ import annotations

import asyncio

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState
from marianne.healing.registry import RemedyRegistry
from marianne.healing.remedies.base import RemedyCategory, RemedyResult


class _Diag:
    def __init__(self, name: str) -> None:
        self.remedy_name = name
        self.confidence = "high"
        self.suggestion = "x"


class _StubRemedy:
    """An AUTOMATIC remedy whose apply() outcome is controllable."""

    category = RemedyCategory.AUTOMATIC

    def __init__(self, succeed: bool, name: str = "stub") -> None:
        self.name = name
        self._succeed = succeed
        self.applied = False

    def diagnose(self, context: object) -> object:
        return _Diag(self.name)

    def apply(self, context: object) -> RemedyResult:
        self.applied = True
        return RemedyResult(success=self._succeed, message="m", action_taken="a")

    def preview(self, context: object) -> str:
        return "p"

    def generate_diagnostic(self, context: object) -> str:
        return "d"


def _baton_with_remedy(
    remedy: _StubRemedy | None,
    *,
    escalation: bool = False,
    healing_attempts: int = 0,
) -> tuple[BatonCore, SheetExecutionState]:
    baton = BatonCore()
    if remedy is not None:
        reg = RemedyRegistry()
        reg.register(remedy)
        baton._healing_registry = reg
    sheet = SheetExecutionState(sheet_num=1, instrument_name="x", max_retries=0)
    sheet.healing_attempts = healing_attempts
    sheet.error_code = "E009"
    sheet.error_message = "boom"
    baton.register_job(
        "job", {1: sheet}, {},
        self_healing_enabled=True,
        escalation_enabled=escalation,
    )
    return baton, sheet


class TestHealingWire:
    async def test_coordinator_invoked_and_retry_on_success(self) -> None:
        remedy = _StubRemedy(succeed=True)
        baton, sheet = _baton_with_remedy(remedy)
        await baton._handle_exhaustion("job", 1, sheet)
        assert remedy.applied is True  # the coordinator ran the remedy
        assert sheet.healing_attempts == 1
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

    async def test_no_env_remedy_still_schedules_enriched_retry(self) -> None:
        # No environment remedy succeeds — but healing still schedules a retry,
        # because the real fix is the enriched retry prompt (built at dispatch),
        # not the coordinator. NOT a fall-through to escalation.
        remedy = _StubRemedy(succeed=False)
        baton, sheet = _baton_with_remedy(remedy, escalation=True)
        await baton._handle_exhaustion("job", 1, sheet)
        assert remedy.applied is True  # the env-fix pass was still tried
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.healing_attempts == 1
        assert baton._jobs["job"].paused is False  # not escalated

    async def test_no_double_heal_past_cap(self) -> None:
        # healing_attempts already at the cap → Path 2 is skipped entirely.
        remedy = _StubRemedy(succeed=True)
        baton, sheet = _baton_with_remedy(
            remedy, escalation=True, healing_attempts=baton_cap()
        )
        await baton._handle_exhaustion("job", 1, sheet)
        assert remedy.applied is False  # no second heal
        assert sheet.status == BatonSheetStatus.FERMATA  # straight to escalation

    async def test_heal_timeout_still_schedules_retry(self, monkeypatch) -> None:
        # A coordinator heal that exceeds the timeout → _run_healing returns
        # None (env-fix pass aborted). Healing STILL schedules the retry: the
        # enriched retry prompt is independent of the env-fix pass, so a slow
        # coordinator must never cost the agent its targeted retry.
        from marianne.healing import coordinator as coord_mod

        async def _slow_heal(self: object, ctx: object) -> object:
            await asyncio.sleep(1.0)
            raise AssertionError("should have timed out")

        monkeypatch.setattr(coord_mod.SelfHealingCoordinator, "heal", _slow_heal)
        remedy = _StubRemedy(succeed=True)
        baton, sheet = _baton_with_remedy(remedy, escalation=True)
        baton._HEALING_TIMEOUT_SECONDS = 0.05  # type: ignore[misc]
        await baton._handle_exhaustion("job", 1, sheet)
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.healing_attempts == 1


def baton_cap() -> int:
    return BatonCore._DEFAULT_MAX_HEALING


class TestErrorContextFromSheetState:
    def test_from_sheet_state_maps_fields(self) -> None:
        from pathlib import Path

        from marianne.healing.context import ErrorContext

        sheet = SheetExecutionState(sheet_num=3, instrument_name="x", max_retries=2)
        sheet.error_code = "E601"
        sheet.error_message = "kaboom"
        sheet.exit_code = 1
        sheet.stderr_tail = "model not found"
        sheet.normal_attempts = 2
        ctx = ErrorContext.from_sheet_state(
            sheet, 3, workspace=Path("/tmp/ws"), config_path=Path("/tmp/s.yaml")
        )
        assert ctx.error_code == "E601"
        assert ctx.error_message == "kaboom"
        assert ctx.exit_code == 1
        assert ctx.stderr_tail == "model not found"
        assert ctx.workspace == Path("/tmp/ws")
        assert ctx.config_path == Path("/tmp/s.yaml")
        assert ctx.sheet_number == 3
        assert ctx.retry_count == 2
        assert ctx.max_retries == 2
