"""Independent functional checks for issue #378 and its asynchronous seams."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from marianne.core.config.instrument_settings import InstrumentSettings
from marianne.execution.base import ExecutionResult
from marianne.execution.instruments.interactive import (
    ContinuationContext,
    InteractiveSessionDriver,
    ModelNudgePolicy,
)
from tests.test_interactive_mode import READY_SCREEN, FakeTmux, make_interactive_config


def make_policy(output: str = "NUDGE: inspect the failing test", **kwargs):
    backend = MagicMock()
    backend.execute = AsyncMock(return_value=ExecutionResult(
        success=True, stdout=output, stderr="", duration_seconds=0,
    ))
    backend.close = AsyncMock()
    policy = ModelNudgePolicy(
        MagicMock(return_value=backend), "cheap", fallback_message="static fallback", **kwargs,
    )
    return policy, backend


async def drive(fake: FakeTmux, tmp_path: Path, policy: ModelNudgePolicy, **kwargs):
    config = make_interactive_config(**kwargs.pop("config_overrides", {}))
    return await InteractiveSessionDriver(fake, config).run(
        session="mzt-functional-review", command=["fake"], cwd=tmp_path,
        prompt="original task", marker_path=tmp_path / "complete",
        timeout_seconds=kwargs.pop("timeout_seconds", 5),
        policy=policy, max_nudges=kwargs.pop("max_nudges", 1), **kwargs,
    )


async def drive_poll_loop(fake: FakeTmux, tmp_path: Path, policy: ModelNudgePolicy):
    """Exercise the policy deadline without charging launch/paste setup to it."""
    driver = InteractiveSessionDriver(fake, make_interactive_config(quiet_seconds=0.001))
    now = asyncio.get_running_loop().time()
    return await driver._drive(
        session="mzt-functional-review", marker_path=tmp_path / "complete",
        deadline=now + 1, submitted_at=now, policy=policy,
        max_nudges=1, transcript_path=None,
    )


async def test_contextual_message_is_submitted_and_counted_once(tmp_path: Path):
    fake = FakeTmux([READY_SCREEN])
    policy, backend = make_policy()
    result = await drive(fake, tmp_path, policy)
    assert result.outcome == "nudges_exhausted"
    assert result.nudges_sent == 1
    assert fake.pasted == ["original task", "inspect the failing test"]
    backend.execute.assert_awaited_once()
    backend.close.assert_awaited_once()
    assert READY_SCREEN.splitlines()[0] in backend.execute.call_args.args[0]


async def test_policy_timeout_sends_fallback_and_spends_only_one_nudge(tmp_path: Path):
    policy, backend = make_policy(timeout_seconds=0.02)

    async def wait_forever(*args, **kwargs):
        await asyncio.Event().wait()

    backend.execute.side_effect = wait_forever
    fake = FakeTmux([READY_SCREEN])
    result = await drive(fake, tmp_path, policy)
    assert result.outcome == "nudges_exhausted"
    assert result.nudges_sent == 1
    assert fake.pasted == ["original task", "static fallback"]
    backend.close.assert_awaited_once()


@pytest.mark.parametrize("model_output", ["NUDGE: next step", "NUDGE: UNRECOVERABLE lost task"])
async def test_completion_during_model_call_wins_over_decision(tmp_path: Path, model_output: str):
    policy, backend = make_policy(model_output)

    async def complete_then_return(*args, **kwargs):
        (tmp_path / "complete").touch()
        return ExecutionResult(success=True, stdout=model_output, stderr="", duration_seconds=0)

    backend.execute.side_effect = complete_then_return
    fake = FakeTmux([READY_SCREEN])
    result = await drive(fake, tmp_path, policy)
    assert result.outcome == "completed"
    assert result.nudges_sent == 0
    assert fake.pasted == ["original task"]
    backend.close.assert_awaited_once()


async def test_sheet_deadline_cancels_longer_model_call_and_closes(tmp_path: Path):
    policy, backend = make_policy(timeout_seconds=30)
    entered = asyncio.Event()

    async def wait_forever(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    backend.execute.side_effect = wait_forever
    fake = FakeTmux([READY_SCREEN])
    outcome, _, nudges_sent, _ = await drive_poll_loop(fake, tmp_path, policy)
    assert entered.is_set()
    assert outcome == "timeout"
    assert nudges_sent == 0
    assert fake.pasted == []
    backend.close.assert_awaited_once()


async def test_completion_during_hung_policy_is_not_lost_at_deadline(tmp_path: Path):
    policy, backend = make_policy(timeout_seconds=30)

    async def complete_but_model_hangs(*args, **kwargs):
        (tmp_path / "complete").touch()
        await asyncio.Event().wait()

    backend.execute.side_effect = complete_but_model_hangs
    fake = FakeTmux([READY_SCREEN])
    outcome, _, nudges_sent, _ = await drive_poll_loop(fake, tmp_path, policy)
    assert outcome == "completed"
    assert nudges_sent == 0
    assert fake.pasted == []
    backend.close.assert_awaited_once()


@pytest.mark.parametrize("model_output", ["NUDGE: next step", "NUDGE: UNRECOVERABLE lost task"])
async def test_provider_error_arriving_during_model_call_takes_precedence(
    tmp_path: Path, model_output: str,
):
    quota_screen = "API Error: Request rejected (429)\nUsage limit reached\n❯ \nstatus\n0 tokens"
    fake = FakeTmux([READY_SCREEN])
    policy, backend = make_policy(model_output)

    async def quota_arrives_then_model_returns(*args, **kwargs):
        fake.screens[:] = [quota_screen]
        return ExecutionResult(success=True, stdout=model_output, stderr="", duration_seconds=0)

    backend.execute.side_effect = quota_arrives_then_model_returns
    result = await drive(fake, tmp_path, policy, config_overrides={
        "rate_limit_screen_patterns": [r"Usage limit reached"],
    })
    assert result.outcome == "provider_error"
    assert result.nudges_sent == 0
    assert fake.pasted == ["original task"]
    assert "Usage limit reached" in result.final_screen


@pytest.mark.parametrize("output,expected", [
    ("NUDGE: earlier\nNUDGE:", "static fallback"),
    ("NUDGE: earlier\nNUDGE: " + "x" * 2049, "static fallback"),
    ("NUDGE: " + "x" * 2048, "x" * 2048),
])
async def test_last_marker_is_authoritative_and_length_boundary_is_enforced(output, expected):
    policy, backend = make_policy(output)
    decision = await policy.next_nudge(ContinuationContext(0, 1, "idle"))
    assert decision.message == expected
    backend.close.assert_awaited_once()


async def test_backend_cleanup_failure_keeps_valid_model_decision():
    policy, backend = make_policy()
    backend.close.side_effect = RuntimeError("close failed")
    decision = await policy.next_nudge(ContinuationContext(0, 1, "idle"))
    assert decision.message == "inspect the failing test"
    backend.close.assert_awaited_once()


async def test_external_cancellation_propagates_after_backend_cleanup():
    policy, backend = make_policy()
    entered = asyncio.Event()

    async def wait_forever(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    backend.execute.side_effect = wait_forever
    task = asyncio.create_task(policy.next_nudge(ContinuationContext(0, 1, "idle")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        backend.close.assert_awaited_once()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_policy_timeout_rejected_before_dispatch(timeout):
    with pytest.raises(ValidationError):
        InstrumentSettings(interactive_nudge_timeout_seconds=timeout)


async def test_zero_budget_never_invokes_model(tmp_path: Path):
    policy, backend = make_policy()
    fake = FakeTmux([READY_SCREEN])
    result = await drive(fake, tmp_path, policy, max_nudges=0)
    assert result.outcome == "nudges_exhausted"
    assert result.nudges_sent == 0
    backend.execute.assert_not_called()
    backend.close.assert_not_called()
