"""GH #428 / #429: two seams Circuit's independent verification found between
the flow engine and the baton's recovery branches (real BatonCore, no mocks).

#428: a trigger `skip` / forward `goto` queued against an in-flight sheet was
applied only at the terminal hook; every NON-terminal failure (default
max_retries=3, completion mode, auth fallback, #418 unavailable fallback)
re-dispatched the sheet the author had skipped. Now the queued skip settles
the sheet as clean SKIPPED as soon as the in-flight attempt ends without full
success. Exactly one dispatch.

#429: `has_on_fail` preceded the INSTRUMENT_UNAVAILABLE branch, so a sheet with
`on_fail` and a missing primary fired on_fail and never tried its chain.
Availability is pre-semantic: fall back first; on_fail only once exhausted.
"""

from __future__ import annotations

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import RetryDue, SheetAttemptResult


def _job(chain: list[str], retries: int) -> CheckpointState:
    sheets = {n: SheetState(sheet_num=n, instrument_name="cli") for n in (1, 2, 3)}
    sheets[2].fallback_chain = chain
    sheets[2].max_retries = retries
    return CheckpointState(job_id="j", job_name="j", total_sheets=3, sheets=sheets)


def _result(
    num: int, instrument: str = "cli", *, success: bool, cls: str | None = None,
    partial: bool = False,
) -> SheetAttemptResult:
    if partial:
        return SheetAttemptResult(
            job_id="j", sheet_num=num, instrument_name=instrument, attempt=1,
            dispatch_epoch=0, execution_success=True, validations_passed=1,
            validations_total=2, validation_pass_rate=50.0, cost_usd=0.0,
        )
    return SheetAttemptResult(
        job_id="j", sheet_num=num, instrument_name=instrument, attempt=1,
        dispatch_epoch=0, execution_success=success,
        validation_pass_rate=100.0 if success else 0.0, cost_usd=0.0,
        error_classification=cls, error_message=None if success else str(cls),
    )


class TestQueuedSkipAppliesWhenAttemptEnds428:
    @pytest.mark.parametrize(
        ("label", "chain", "retries", "cls", "partial", "goto"),
        [
            ("control max_retries=0", [], 0, "TRANSIENT", False, False),
            ("default retries transient", [], 3, "TRANSIENT", False, False),
            ("unavailable with fallback", ["cli2"], 3, "INSTRUMENT_UNAVAILABLE", False, False),
            ("auth with fallback", ["cli2"], 3, "AUTH_FAILURE", False, False),
            ("completion mode", [], 3, None, True, False),
            ("forward goto over in-flight", [], 3, "TRANSIENT", False, True),
        ],
    )
    async def test_skipped_sheet_is_never_redispatched(
        self, label: str, chain: list[str], retries: int, cls: str | None,
        partial: bool, goto: bool,
    ) -> None:
        cp = _job(chain, retries)
        baton = BatonCore()
        action = TriggerAction(goto=3) if goto else TriggerAction(skip="2")
        baton.register_job(
            "j", cp.sheets, {3: [2]}, flow_state=cp.flow,
            triggers={"1": SheetTriggerConfig(on_success=[action])},
        )
        cp.sheets[2].status = SheetStatus.DISPATCHED
        await baton.handle_event(_result(1, success=True))
        assert 2 in cp.flow.queued_skips
        await baton.handle_event(_result(2, success=False, cls=cls, partial=partial))
        s2 = cp.sheets[2]
        if s2.status == SheetStatus.RETRY_SCHEDULED:
            await baton.handle_event(
                RetryDue(job_id="j", sheet_num=2, dispatch_epoch=s2.dispatch_epoch)
            )
        ready = [x.sheet_num for x in baton.get_ready_sheets("j")]
        assert s2.status == SheetStatus.SKIPPED, label
        assert s2.error_code is None, label
        assert cp.flow.queued_skips == {}, label
        assert 2 not in ready, f"{label}: skipped sheet offered for dispatch again"
        assert 3 in ready, f"{label}: dependent not released"

    async def test_fully_successful_attempt_still_drops_the_skip(self) -> None:
        cp = _job([], 3)
        baton = BatonCore()
        baton.register_job(
            "j", cp.sheets, {3: [2]}, flow_state=cp.flow,
            triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(skip="2")])},
        )
        cp.sheets[2].status = SheetStatus.DISPATCHED
        await baton.handle_event(_result(1, success=True))
        await baton.handle_event(_result(2, success=True))
        assert cp.sheets[2].status == SheetStatus.COMPLETED
        assert cp.flow.queued_skips == {}


class TestUnavailableFallsBackBeforeOnFail429:
    @pytest.mark.parametrize("with_on_fail", [False, True])
    async def test_missing_primary_takes_chain_then_on_fail_only_when_exhausted(
        self, with_on_fail: bool,
    ) -> None:
        sheets = {n: SheetState(sheet_num=n, instrument_name="missing-cli") for n in (1, 2)}
        sheets[1].fallback_chain = ["present-cli"]
        sheets[1].max_retries = 0
        cp = CheckpointState(job_id="j", job_name="j", total_sheets=2, sheets=sheets)
        triggers = (
            {"1": SheetTriggerConfig(on_fail=[TriggerAction(skip="2")])} if with_on_fail else {}
        )
        baton = BatonCore()
        baton.register_job("j", cp.sheets, {}, flow_state=cp.flow, triggers=triggers)

        await baton.handle_event(
            _result(1, "missing-cli", success=False, cls="INSTRUMENT_UNAVAILABLE")
        )
        s1, s2 = cp.sheets[1], cp.sheets[2]
        # First: the chain advances regardless of on_fail; on_fail did NOT fire.
        assert s1.status == SheetStatus.PENDING
        assert s1.instrument_name == "present-cli"
        assert [h["reason"] for h in s1.instrument_fallback_history] == ["unavailable"]
        assert s2.status == SheetStatus.PENDING

        # Then the present entry fails the WORK: on_fail (if any) governs.
        await baton.handle_event(_result(1, "present-cli", success=False, cls="EXECUTION_ERROR"))
        if with_on_fail:
            assert s1.status == SheetStatus.FAILED
            assert s2.status == SheetStatus.SKIPPED  # the on_fail action ran
        else:
            assert s1.status == SheetStatus.FAILED  # max_retries=0, no chain left

    async def test_auth_failure_takes_chain_before_on_fail_d_i2(self) -> None:
        """D-I2 (Blueprint classes Integration C-I2): credentials are per
        route; a logged-out first entry advances the chain, on_fail only once
        exhausted. Falsifier: Blueprint I1 case D."""
        sheets = {n: SheetState(sheet_num=n, instrument_name="logged-out") for n in (1, 2)}
        sheets[1].fallback_chain = ["present-cli"]
        sheets[1].max_retries = 0
        cp = CheckpointState(job_id="j", job_name="j", total_sheets=2, sheets=sheets)
        baton = BatonCore()
        baton.register_job(
            "j", cp.sheets, {}, flow_state=cp.flow,
            triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(skip="2")])},
        )
        await baton.handle_event(_result(1, "logged-out", success=False, cls="AUTH_FAILURE"))
        s1, s2 = cp.sheets[1], cp.sheets[2]
        assert s1.status == SheetStatus.PENDING
        assert s1.instrument_name == "present-cli"
        assert [h["reason"] for h in s1.instrument_fallback_history] == ["auth_failure"]
        assert s2.status == SheetStatus.PENDING  # on_fail did not fire
        await baton.handle_event(_result(1, "present-cli", success=False, cls="AUTH_FAILURE"))
        assert s1.status == SheetStatus.FAILED
        assert s2.status == SheetStatus.SKIPPED  # chain exhausted → on_fail

    async def test_unavailable_with_no_chain_left_fires_on_fail(self) -> None:
        sheets = {n: SheetState(sheet_num=n, instrument_name="missing-cli") for n in (1, 2)}
        sheets[1].max_retries = 0
        cp = CheckpointState(job_id="j", job_name="j", total_sheets=2, sheets=sheets)
        baton = BatonCore()
        baton.register_job(
            "j", cp.sheets, {}, flow_state=cp.flow,
            triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(skip="2")])},
        )
        await baton.handle_event(
            _result(1, "missing-cli", success=False, cls="INSTRUMENT_UNAVAILABLE")
        )
        assert cp.sheets[1].status == SheetStatus.FAILED
        assert cp.sheets[2].status == SheetStatus.SKIPPED
