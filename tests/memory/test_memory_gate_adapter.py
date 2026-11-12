"""Slice 3: the baton adapter's three memory call sites, driven through _dispatch_callback."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from marianne.core.config.instruments import InstrumentProfile
from marianne.core.config.job import PromptConfig
from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import ShutdownRequested
from marianne.daemon.baton.memory_gate import MemoryDispatchGate
from marianne.execution.base import ExecutionResult, SheetRequestState
from tests.memory.test_memory_gate import make
from tests.test_mcp_conductor_dispatch import (
    BUILTINS_DIR,
    _BackendPool,
    _BackendWithMcpConfig,
    _Registry,
    _sheet,
)

MEMORY = MemoryConfig.model_validate({"backends": {
    "notes": {"system": "files", "scope": "project/demo"}}})


def profile(name: str) -> InstrumentProfile:
    return InstrumentProfile.model_validate(
        yaml.safe_load((BUILTINS_DIR / f"{name}.yaml").read_text())
    )


class _Recording(_BackendWithMcpConfig):
    """Records the prompt and the config content at execute time."""

    def __init__(self, hold: asyncio.Event | None = None) -> None:
        super().__init__()
        self.prompt: str | None = None
        self.config_json: dict[str, Any] | None = None
        self.started = asyncio.Event()
        self.hold = hold

    async def execute(
        self, prompt: str, *, timeout_seconds: float | None = None,
        request: SheetRequestState | None = None,
    ) -> ExecutionResult:
        self.prompt = prompt
        if self.config_at_execute is not None:
            self.config_json = json.loads(self.config_at_execute.read_text())
        self.started.set()
        if self.hold is not None:
            await self.hold.wait()
        return await super().execute(prompt, timeout_seconds=timeout_seconds, request=request)


class _TrackedGate(MemoryDispatchGate):
    teardowns = 0

    async def teardown(self, job_id: str, dispatch_id: str) -> Any:
        type(self).teardowns += 1
        return await super().teardown(job_id, dispatch_id)


class _ForbiddenGate:
    """Any use at all fails the test."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"gate touched: {name}")


def _adapter(
    pool: Any, gate: Any, tmp: Path, *, memory: bool, job: str = "mem-job",
) -> BatonAdapter:
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(pool)
    adapter.set_memory_gate(gate)
    adapter.register_job(
        job, [_sheet(tmp)], {1: []}, prompt_config=PromptConfig(template="Do the work"),
        memory=MEMORY if memory else None,
    )
    while not adapter.baton.inbox.empty():
        adapter.baton.inbox.get_nowait()
    return adapter


def _sockets(root: Path) -> list[str]:
    d = root / "run" / "s"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


async def _complete(adapter: BatonAdapter, job: str) -> None:
    run_task = asyncio.create_task(adapter.run())
    try:
        assert await asyncio.wait_for(adapter.wait_for_completion(job), timeout=10.0)
    finally:
        await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
        await asyncio.wait_for(run_task, timeout=10.0)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_job_without_memory_never_touches_the_gate(short_tmp: Path) -> None:
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, _ForbiddenGate(), short_tmp, memory=False)
    adapter._memory_gate = _ForbiddenGate()  # type: ignore[assignment]
    await _complete(adapter, "mem-job")
    assert backend.prompt is not None and "Memory Available" not in backend.prompt
    assert backend.set_calls == [None]  # only the existing cleanup detach; nothing attached
    adapter.deregister_job("mem-job")  # also gate-free


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_job_with_memory_reaches_backend_with_merged_config_and_manifest(
    short_tmp: Path,
) -> None:
    gate = make(short_tmp)
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    try:
        await _complete(adapter, "mem-job")
        assert backend.prompt is not None and "Memory Available This Phase" in backend.prompt
        assert backend.config_json is not None
        assert set(backend.config_json["mcpServers"]) == {"memory-notes"}
        assert backend.set_calls[0] is not None and backend.set_calls[-1] is None
        assert not backend.set_calls[0].exists()  # config removed at teardown
        assert gate.open_dispatches == 0 and _sockets(short_tmp) == []
        assert len(pool.release_calls) == 1
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_prepare_failure_is_a_dispatch_failure_and_no_backend_is_acquired(
    short_tmp: Path,
) -> None:
    import os

    gate = make(short_tmp)
    os.chmod(short_tmp / "run", 0o777)  # the gateway refuses an unsafe run directory
    pool = MagicMock()
    pool.acquire = AsyncMock()
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    state = adapter.baton._jobs["mem-job"].sheets[1]
    try:
        await adapter._dispatch_callback("mem-job", 1, state)
        pool.acquire.assert_not_awaited()
        failure = adapter.baton.inbox.get_nowait()
        assert failure.execution_success is False
        assert "Memory setup failed before backend acquisition" in failure.error_message
        assert "notes" in failure.error_message and "run_dir_unsafe" in failure.error_message
        assert gate.open_dispatches == 0
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_memory_job_without_a_gate_fails_the_dispatch(tmp_path: Path) -> None:
    pool = MagicMock()
    pool.acquire = AsyncMock()
    adapter = _adapter(pool, None, tmp_path, memory=True)
    state = adapter.baton._jobs["mem-job"].sheets[1]
    await adapter._dispatch_callback("mem-job", 1, state)
    pool.acquire.assert_not_awaited()
    failure = adapter.baton.inbox.get_nowait()
    assert "no memory gate" in failure.error_message


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_delivery_refusal_releases_backend_and_closes_sessions(short_tmp: Path) -> None:
    gate = make(short_tmp)
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("cli")))
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    state = adapter.baton._jobs["mem-job"].sheets[1]
    try:
        await adapter._dispatch_callback("mem-job", 1, state)
        failure = adapter.baton.inbox.get_nowait()
        assert failure.execution_success is False
        assert failure.error_message.startswith("memory_delivery_unsupported: ")
        assert len(pool.release_calls) == 1  # released exactly once
        assert backend.prompt is None and backend.set_calls == []
        assert gate.open_dispatches == 0 and _sockets(short_tmp) == []
        assert "mem-job" not in {k[0] for k in adapter._active_tasks}
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_musician_finishing_tears_down_exactly_once(short_tmp: Path) -> None:
    _TrackedGate.teardowns = 0
    gate = _TrackedGate(
        run_dir=short_tmp / "run", memory_root=short_tmp / "files",
        audit_dir=short_tmp / "audit", user="ada",
    )
    (short_tmp / "run").mkdir(mode=0o700, exist_ok=True)
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    try:
        await _complete(adapter, "mem-job")
        await gate.drain()
        assert _TrackedGate.teardowns == 1
        assert gate.open_dispatches == 0
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_musician_cancelled_still_tears_down(short_tmp: Path) -> None:
    gate = make(short_tmp)
    hold = asyncio.Event()
    backend = _Recording(hold)
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    state = adapter.baton._jobs["mem-job"].sheets[1]
    try:
        assert await adapter._dispatch_callback("mem-job", 1, state) is True
        task = adapter._active_tasks[("mem-job", 1)]
        await asyncio.wait_for(backend.started.wait(), 10)
        assert gate.open_dispatches == 1 and len(_sockets(short_tmp)) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        await gate.drain()
        assert gate.open_dispatches == 0 and _sockets(short_tmp) == []
        assert len(pool.release_calls) == 1
        assert backend.set_calls[-1] is None
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_deregister_job_tears_down_a_running_dispatch(short_tmp: Path) -> None:
    gate = make(short_tmp)
    hold = asyncio.Event()
    backend = _Recording(hold)
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    state = adapter.baton._jobs["mem-job"].sheets[1]
    try:
        await adapter._dispatch_callback("mem-job", 1, state)
        await asyncio.wait_for(backend.started.wait(), 10)
        adapter.deregister_job("mem-job")
        await gate.drain()
        assert gate.open_dispatches == 0 and _sockets(short_tmp) == []
        hold.set()
        await asyncio.gather(*list(adapter._active_tasks.values()), return_exceptions=True)
    finally:
        await gate.shutdown()
