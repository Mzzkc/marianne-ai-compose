"""Independent functional review of GH #572's terminal and cleanup boundaries."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
from marianne.core.config.memory import MemoryConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import sheets_to_execution_states
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import MEMORY_CONFIG_ERROR, SheetAttemptResult
from marianne.daemon.baton.memory_gate import MemoryGateError
from marianne.daemon.baton.state import BatonSheetStatus
from tests.memory.test_memory_config_refusal_572 import _adapter, _cam_memory, _gate


def _core(tmp_path: Path, *, flow: bool = False) -> BatonCore:
    sheets = [
        Sheet(num=n, movement=1, voice_count=1, workspace=tmp_path,
              instrument_name="primary", instrument_fallbacks=["secondary"],
              instrument_fallback_configs=[{}])
        for n in (1, 2)
    ]
    core = BatonCore()
    core.register_job(
        "j", sheets_to_execution_states(sheets, max_retries=5), {1: [], 2: [1]},
        triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(continue_=True)])}
        if flow else None,
    )
    return core


def _event(
    *, code: str | None = "E301", message: str | None = "bad CAM endpoint",
) -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="primary", attempt=1,
        execution_success=False, error_classification=MEMORY_CONFIG_ERROR,
        error_code=code, error_message=message,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_failures", [0, 4])
async def test_flow_refusal_preserves_instrument_health(
    tmp_path: Path, prior_failures: int,
) -> None:
    """A score error must not charge or trip the shared instrument via on_fail."""
    core = _core(tmp_path, flow=True)
    instrument = core._instruments["primary"]
    instrument.consecutive_failures = prior_failures
    breaker_before = instrument.circuit_breaker
    await core.handle_event(_event())
    state = core.get_sheet_state("j", 1)
    assert state is not None and state.status is BatonSheetStatus.FAILED
    assert state.error_code == "E301" and state.normal_attempts == 1
    assert len(core.drain_flow_events()) == 1  # preserve the documented on_fail action
    assert core.drain_fallback_events() == []
    assert instrument.consecutive_failures == prior_failures
    assert instrument.circuit_breaker == breaker_before


@pytest.mark.asyncio
async def test_refusal_supplies_default_error_and_blocks_dependents(tmp_path: Path) -> None:
    core = _core(tmp_path)
    await core.handle_event(_event(code=None, message=None))
    state = core.get_sheet_state("j", 1)
    dependent = core.get_sheet_state("j", 2)
    assert state is not None and dependent is not None
    assert state.status is BatonSheetStatus.FAILED
    assert state.error_code == "E301"
    assert state.error_message == "memory configuration refused"
    assert dependent.status is BatonSheetStatus.SKIPPED
    assert dependent.normal_attempts == 0
    assert dependent.error_code is not None  # cascade skip cannot satisfy downstream deps
    assert core.is_job_complete("j")
    await core.handle_event(_event())  # late duplicate must not charge another attempt
    assert state.normal_attempts == 1
    assert core._instruments["primary"].consecutive_failures == 0


@pytest.mark.asyncio
async def test_shared_refusal_text_does_not_reclassify_an_untyped_gate_error(
    short_tmp: Path,
) -> None:
    gate = _gate(short_tmp)
    pool = MagicMock()
    pool.acquire = AsyncMock()
    adapter = _adapter(pool, gate, short_tmp, _cam_memory({"endpoint": "default"}))
    gate.deliver = AsyncMock(side_effect=MemoryGateError(
        "shared", "memory_delivery_unsupported: transient backend failure",
    ))
    try:
        await adapter._dispatch_callback("mem-job", 1, adapter.baton._jobs["mem-job"].sheets[1])
        failure = adapter.baton.inbox.get_nowait()
        assert failure.error_classification == "E505" and failure.error_code is None
        pool.acquire.assert_not_awaited()
        assert adapter.baton.inbox.empty()
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
async def test_refused_second_attachment_cleans_up_first_before_failure_event(
    short_tmp: Path,
) -> None:
    gate = _gate(short_tmp)
    pool = MagicMock()
    pool.acquire = AsyncMock()
    memory = MemoryConfig.model_validate({"backends": {
        "notes": {"system": "files", "scope": "project/a"},
        "shared": {"system": "cam", "scope": "project/b",
                   "settings": {"endpoint": "nowhere"}},
    }})
    adapter = _adapter(pool, gate, short_tmp, memory)
    try:
        await adapter._dispatch_callback("mem-job", 1, adapter.baton._jobs["mem-job"].sheets[1])
        failure = adapter.baton.inbox.get_nowait()
        assert failure.error_classification == MEMORY_CONFIG_ERROR
        assert failure.error_code == "E301"
        assert "memory_backends.cam.nowhere" in failure.error_message
        assert gate.open_dispatches == 0
        assert gate._gateway is not None and not gate._gateway._backends
        pool.acquire.assert_not_awaited()
    finally:
        await gate.shutdown()
