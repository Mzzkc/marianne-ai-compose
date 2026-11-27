"""GH #572: a memory configuration refusal fails the sheet at once.

A ``cam`` attachment naming an endpoint the conductor does not have, or setting a key
only the operator may set, is a deterministic fact about the score and the conductor's
configuration: no retry and no other instrument can deliver it. Before the fix the
refusal travelled the ordinary retry path (``E505``, no code) and then, at exhaustion,
the fallback chain. These tests pin the typed class end to end: the gate raises
``MemoryConfigError``, the adapter classifies the failure ``MEMORY_CONFIG_ERROR`` /
``E301``, and the baton ends the sheet on the first attempt without charging the
instrument or moving the fallback.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config.job import PromptConfig
from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import (
    MEMORY_CONFIG_ERROR,
    SheetAttemptResult,
    ShutdownRequested,
)
from marianne.daemon.baton.state import BatonSheetStatus
from tests.memory.test_memory_gate_adapter import (
    _Recording,
    _RecordingGate,
    profile,
)
from tests.test_mcp_conductor_dispatch import (
    _BackendPool,
    _Registry,
    _sheet,
)

ENDPOINTS = {"default": {"base_url": "http://127.0.0.1:9", "api_key_env": "MZT_TEST_CAM_KEY"}}


def _cam_memory(settings: dict[str, Any]) -> MemoryConfig:
    return MemoryConfig.model_validate({"backends": {
        "shared": {"system": "cam", "scope": "project/b", "settings": settings},
    }})


def _gate(root: Path) -> _RecordingGate:
    (root / "run").mkdir(mode=0o700, exist_ok=True)
    return _RecordingGate(
        run_dir=root / "run", memory_root=root / "files",
        audit_dir=root / "audit", user="ada", cam_endpoints=ENDPOINTS,
    )


def _adapter(
    pool: Any, gate: Any, tmp: Path, memory: MemoryConfig, *, job: str = "mem-job",
    sheet: Any = None,
) -> BatonAdapter:
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(pool)
    adapter.set_memory_gate(gate)
    adapter.register_job(
        job, [sheet if sheet is not None else _sheet(tmp)], {1: []},
        prompt_config=PromptConfig(template="Do the work"), memory=memory,
    )
    while not adapter.baton.inbox.empty():
        adapter.baton.inbox.get_nowait()
    return adapter


# ------------------------------------------------------------------ Phase 2: the event


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_unknown_endpoint_is_one_typed_failure_event(short_tmp: Path) -> None:
    """The adapter puts the refusal on the inbox as MEMORY_CONFIG_ERROR / E301, not E505."""
    gate = _gate(short_tmp)
    pool = MagicMock()
    pool.acquire = AsyncMock()
    adapter = _adapter(pool, gate, short_tmp, _cam_memory({"endpoint": "nowhere"}))
    state = adapter.baton._jobs["mem-job"].sheets[1]
    try:
        await adapter._dispatch_callback("mem-job", 1, state)
        pool.acquire.assert_not_awaited()  # refused before backend acquisition
        items: list[Any] = []
        while not adapter.baton.inbox.empty():
            items.append(adapter.baton.inbox.get_nowait())
        assert len(items) == 1
        failure = items[0]
        assert isinstance(failure, SheetAttemptResult)
        assert failure.execution_success is False
        assert failure.error_classification == MEMORY_CONFIG_ERROR
        assert failure.error_code == "E301"
        assert failure.error_message is not None
        assert "'shared'" in failure.error_message
        assert "memory_backends.cam.nowhere" in failure.error_message
        assert gate.open_dispatches == 0
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_non_configuration_gate_failure_keeps_the_retry_class(short_tmp: Path) -> None:
    """Boundary: a gate failure that is not a configuration refusal (an unsafe run
    directory) keeps the retry class — the environment, not the score, is at fault."""
    gate = _gate(short_tmp)
    os.chmod(short_tmp / "run", 0o777)  # the gateway refuses an unsafe run directory
    pool = MagicMock()
    pool.acquire = AsyncMock()
    adapter = _adapter(pool, gate, short_tmp, _cam_memory({"endpoint": "default"}))
    state = adapter.baton._jobs["mem-job"].sheets[1]
    try:
        await adapter._dispatch_callback("mem-job", 1, state)
        pool.acquire.assert_not_awaited()
        failure = adapter.baton.inbox.get_nowait()
        assert failure.execution_success is False
        assert failure.error_classification == "E505"
        assert failure.error_code is None
        assert "Memory setup failed before backend acquisition" in failure.error_message
        assert "run_dir_unsafe" in failure.error_message
        assert gate.open_dispatches == 0
    finally:
        await gate.shutdown()


# ------------------------------------------------------------------ Phase 3: the sheet


@pytest.mark.parametrize(("label", "settings", "key"), [
    ("unknown-endpoint", {"endpoint": "nowhere"}, "memory_backends.cam.nowhere"),
    ("operator-only", {"endpoint": "default", "base_url": "http://127.0.0.1:1"},
     "memory_backends.cam.default"),
])
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_config_refusal_fails_the_sheet_at_once(
    short_tmp: Path, label: str, settings: dict[str, Any], key: str,
) -> None:
    """Done-when (GH #572): one attempt, no fallback move, the named key, E301.

    Before the fix the refusal was ``E505`` with no code: the sheet sat behind the
    120 s startup-failure floor for each of ``max_retries`` retries on every instrument
    in the chain (about 8 minutes of timer waits with the default chain), and each
    instrument was charged its failures. The helper's 10 s bound turns that into a
    failure; after the fix this completes in well under a second.
    """
    gate = _gate(short_tmp)
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    sheet = _sheet(short_tmp).model_copy(update={
        "instrument_fallbacks": ["secondary-cli"], "instrument_fallback_configs": [{}],
    })
    adapter = _adapter(pool, gate, short_tmp, _cam_memory(settings), sheet=sheet)
    try:
        run_task = asyncio.create_task(adapter.run())
        try:
            # The sheet fails; it does not sit RETRY_SCHEDULED behind the 120 s floor.
            done = await asyncio.wait_for(
                adapter.wait_for_completion("mem-job"), timeout=10.0,
            )
            assert done is False  # the sheet failed — on the first attempt
        finally:
            await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
            await asyncio.wait_for(run_task, timeout=10.0)
        await gate.drain()
        state = adapter.baton.get_sheet_state("mem-job", 1)
        assert state is not None
        assert state.status is BatonSheetStatus.FAILED
        assert state.normal_attempts + state.completion_attempts == 1
        assert gate.calls.count("deliver") == 1
        assert state.instrument_name == "claude-code"  # the chain was not entered
        assert adapter.baton.drain_fallback_events() == []
        assert state.error_code == "E301"
        assert state.error_message is not None
        assert "'shared'" in state.error_message and key in state.error_message
        assert backend.prompt is None  # the sheet never ran
        assert pool.release_calls == []  # refused before backend acquisition
        assert adapter.baton._instruments["claude-code"].consecutive_failures == 0
        assert adapter.baton._instruments["secondary-cli"].consecutive_failures == 0
        assert gate.open_dispatches == 0
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
async def test_config_refusal_event_ends_the_sheet_without_fallback_or_health_charge(
    tmp_path: Path,
) -> None:
    """One MEMORY_CONFIG_ERROR event on the baton: FAILED now, chain not entered,
    the instrument's health untouched (it never ran)."""
    from tests.memory.test_memory_gate_adapter import _core_for_memory_refusal

    core = _core_for_memory_refusal(tmp_path, ["secondary"])
    st = core._jobs["j"].sheets[1]
    await core.handle_event(SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="primary", attempt=1,
        execution_success=False, error_classification=MEMORY_CONFIG_ERROR,
        error_code="E301",
        error_message="memory attachment 'shared': memory_delivery_unsupported: "
        "cam endpoint 'nowhere' is not configured on this conductor",
    ))
    assert st.status is BatonSheetStatus.FAILED
    assert st.instrument_name == "primary"  # no fallback move
    assert core.drain_fallback_events() == []
    assert st.normal_attempts == 1
    assert st.error_code == "E301"
    assert core._instruments["primary"].consecutive_failures == 0


def test_config_classification_carries_no_fallback_reason_label() -> None:
    """``_derive_fallback_reason`` labels by substring on the upper-cased
    classification; a labelled substring in the class would mislabel it."""
    for label in ("TIMEOUT", "CRASH", "STALE", "AUTH", "UNAVAILABLE"):
        assert label not in MEMORY_CONFIG_ERROR.upper()


@pytest.mark.parametrize("prior_failures", [0, 4])
@pytest.mark.asyncio
async def test_config_refusal_in_a_flow_job_still_fires_on_fail(
    tmp_path: Path, prior_failures: int,
) -> None:
    """Decision 1: the branch sits below the flow block, so an ``on_fail`` handler keeps
    its documented contract (``on_fail`` replaces retry and fallback for that sheet) and
    the sheet still ends FAILED with E301 — ``record_attempt`` already wrote it.

    The flow block must not charge the instrument either (review F1/Q1): it never ran, so
    neither its failure count nor its breaker may move — at 4 the next charge would open
    the shared instrument's circuit for unrelated jobs."""
    from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
    from marianne.core.sheet import Sheet
    from marianne.daemon.baton.adapter import sheets_to_execution_states
    from marianne.daemon.baton.core import BatonCore
    from marianne.daemon.baton.events import SheetTriggerFired

    sheet = Sheet(
        num=1, movement=1, voice_count=1, workspace=tmp_path, instrument_name="primary",
        instrument_fallbacks=["secondary"], instrument_fallback_configs=[{}],
    )
    core = BatonCore()
    core.register_job(
        "j", sheets_to_execution_states([sheet], max_retries=3), {},
        triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(continue_=True)])},
    )
    st = core._jobs["j"].sheets[1]
    instrument = core._instruments["primary"]
    instrument.consecutive_failures = prior_failures
    breaker_before = instrument.circuit_breaker
    await core.handle_event(SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="primary", attempt=1,
        execution_success=False, error_classification=MEMORY_CONFIG_ERROR,
        error_code="E301",
        error_message="memory attachment 'shared': memory_delivery_unsupported: "
        "cam endpoint 'nowhere' is not configured on this conductor",
    ))
    assert st.status is BatonSheetStatus.FAILED
    assert st.normal_attempts == 1 and st.error_code == "E301"
    assert st.instrument_name == "primary"  # on_fail replaces the fallback move
    flow_events = core.drain_flow_events()
    assert [type(e).__name__ for e in flow_events] == ["SheetTriggerFired"]
    assert isinstance(flow_events[0], SheetTriggerFired)
    assert core.drain_fallback_events() == []
    assert instrument.consecutive_failures == prior_failures
    assert instrument.circuit_breaker == breaker_before


@pytest.mark.parametrize("prior_failures", [0, 4])
@pytest.mark.asyncio
async def test_config_refusal_under_a_queued_skip_charges_no_instrument_health(
    tmp_path: Path, prior_failures: int,
) -> None:
    """The other earlier return that consumes the event (GH #428): a trigger ``skip``
    queued against the in-flight sheet settles it as SKIPPED when the attempt ends. The
    author's skip stands, and the sheet is neither retried nor moved — but that
    settlement must not charge the instrument for a configuration refusal either."""
    from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
    from marianne.core.sheet import Sheet
    from marianne.daemon.baton.adapter import sheets_to_execution_states
    from marianne.daemon.baton.core import BatonCore

    sheets = [
        Sheet(
            num=n, movement=1, voice_count=1, workspace=tmp_path, instrument_name="primary",
            instrument_fallbacks=["secondary"], instrument_fallback_configs=[{}],
        )
        for n in (1, 2)
    ]
    core = BatonCore()
    core.register_job(
        "j", sheets_to_execution_states(sheets, max_retries=3), {1: [], 2: []},
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(skip="2")])},
    )
    states = core._jobs["j"].sheets
    states[2].status = BatonSheetStatus.DISPATCHED  # in flight while sheet 1 succeeds
    await core.handle_event(SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="primary", attempt=1,
        execution_success=True, validation_pass_rate=100.0,
    ))
    job = core._jobs["j"]
    assert job.flow is not None and 2 in job.flow.state.queued_skips
    instrument = core._instruments["primary"]
    instrument.consecutive_failures = prior_failures
    breaker_before = instrument.circuit_breaker
    await core.handle_event(SheetAttemptResult(
        job_id="j", sheet_num=2, instrument_name="primary", attempt=1,
        execution_success=False, error_classification=MEMORY_CONFIG_ERROR,
        error_code="E301",
        error_message="memory attachment 'shared': memory_delivery_unsupported: "
        "cam endpoint 'nowhere' is not configured on this conductor",
    ))
    assert states[2].status is BatonSheetStatus.SKIPPED
    assert states[2].normal_attempts == 1
    assert states[2].instrument_name == "primary"
    assert core.drain_fallback_events() == []
    assert instrument.consecutive_failures == prior_failures
    assert instrument.circuit_breaker == breaker_before
