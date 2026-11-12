"""Slice 3: the baton adapter's three memory call sites, driven through _dispatch_callback."""

from __future__ import annotations

import asyncio
import json
import os
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

SRC_ROOT = Path(__file__).resolve().parents[2]
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

    async def teardown(
        self, job_id: str, dispatch_id: str, *, only: Any = None,
    ) -> Any:
        type(self).teardowns += 1
        return await super().teardown(job_id, dispatch_id, only=only)


class _RecordingGate(MemoryDispatchGate):
    """A real gate that records every public call made to it."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.calls: list[str] = []

    def register_job(self, *a: Any, **k: Any) -> None:
        self.calls.append("register_job")
        super().register_job(*a, **k)

    def deregister_job(self, *a: Any, **k: Any) -> None:
        self.calls.append("deregister_job")
        super().deregister_job(*a, **k)

    async def deliver(self, *a: Any, **k: Any) -> Any:
        self.calls.append("deliver")
        return await super().deliver(*a, **k)

    async def prepare(self, *a: Any, **k: Any) -> Any:
        self.calls.append("prepare")
        return await super().prepare(*a, **k)

    def arm_deadline(self, *a: Any, **k: Any) -> None:
        self.calls.append("arm_deadline")
        super().arm_deadline(*a, **k)

    async def teardown(
        self, job_id: str, dispatch_id: str, *, only: Any = None,
    ) -> Any:
        self.calls.append("teardown")
        return await super().teardown(job_id, dispatch_id, only=only)

    def schedule_teardown(self, *a: Any, **k: Any) -> None:
        self.calls.append("schedule_teardown")
        super().schedule_teardown(*a, **k)


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
        # The musician's cleanup (release, then teardown) finishes after the result is
        # reported; wait for those tasks rather than racing them.
        pending = list(adapter._active_tasks.values())
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 10.0)
    finally:
        await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
        await asyncio.wait_for(run_task, timeout=10.0)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_job_without_memory_never_touches_the_gate(short_tmp: Path) -> None:
    gate = _RecordingGate(
        run_dir=short_tmp / "run", memory_root=short_tmp / "files",
        audit_dir=short_tmp / "audit", user="ada",
    )
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, gate, short_tmp, memory=False)
    await _complete(adapter, "mem-job")
    adapter.deregister_job("mem-job")
    assert backend.prompt is not None and "Memory Available" not in backend.prompt
    assert backend.set_calls == [None]  # only the existing cleanup detach; nothing attached
    assert gate.calls == []  # not one call, including register/deregister
    assert gate._gateway is None and gate._endpoint is None and gate._audit is None
    assert not (short_tmp / "audit").exists() and not (short_tmp / "run" / "s").exists()


def test_no_memory_dispatch_imports_no_memory_module() -> None:
    """In a fresh interpreter, a dispatch for a job without `memory:` imports no memory code."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent('''
        import asyncio, sys, tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, MagicMock
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.core.sheet import Sheet
        before = {m for m in sys.modules if m.startswith("marianne.memory")
                  or m == "marianne.daemon.baton.memory_gate"}
        ws = Path(tempfile.mkdtemp())
        pool = MagicMock(); pool.acquire = AsyncMock(side_effect=RuntimeError("stop"))
        a = BatonAdapter(max_concurrent_sheets=1); a.set_backend_pool(pool)
        sheet = Sheet(num=1, movement=1, voice=None, voice_count=1, instrument_name="x",
                      instrument_config={}, workspace=ws, prompt_template="p", timeout_seconds=5)
        a.register_job("j", [sheet], {1: []})
        state = a.baton._jobs["j"].sheets[1]
        asyncio.run(a._dispatch_callback("j", 1, state))
        a.deregister_job("j")
        after = {m for m in sys.modules if m.startswith("marianne.memory")
                 or m == "marianne.daemon.baton.memory_gate"}
        print(sorted(after - before))
        assert "marianne.daemon.baton.memory_gate" not in sys.modules
        assert "marianne.memory.gateway.endpoint" not in sys.modules
    ''')
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC_ROOT / "src"), str(SRC_ROOT)])
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr[-2000:]


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
        failure: Any = adapter.baton.inbox.get_nowait()
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
    failure: Any = adapter.baton.inbox.get_nowait()
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
        failure: Any = adapter.baton.inbox.get_nowait()
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


async def _cancel_running_musician(
    short_tmp: Path, *, finally_teardown: bool, backstop: bool,
) -> tuple[MemoryDispatchGate, int]:
    gate = make(short_tmp)
    hold = asyncio.Event()
    backend = _Recording(hold)
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    if not backstop:
        adapter._memory_backstop = lambda *a, **k: None  # type: ignore[method-assign]
    if not finally_teardown:
        async def no_teardown(*a: Any, **k: Any) -> None:
            return None

        adapter._memory_teardown = no_teardown  # type: ignore[method-assign]
    state = adapter.baton._jobs["mem-job"].sheets[1]
    assert await adapter._dispatch_callback("mem-job", 1, state) is True
    task = adapter._active_tasks[("mem-job", 1)]
    await asyncio.wait_for(backend.started.wait(), 10)
    assert gate.open_dispatches == 1 and len(_sockets(short_tmp)) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    await gate.drain()
    return gate, len(pool.release_calls)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_cancelled_musician_finally_path_alone_tears_down(short_tmp: Path) -> None:
    gate, releases = await _cancel_running_musician(
        short_tmp, finally_teardown=True, backstop=False,
    )
    try:
        assert gate.open_dispatches == 0 and _sockets(short_tmp) == []
        assert releases == 1
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_cancelled_musician_backstop_alone_tears_down(short_tmp: Path) -> None:
    gate, _ = await _cancel_running_musician(short_tmp, finally_teardown=False, backstop=True)
    try:
        await gate.drain()
        assert gate.open_dispatches == 0 and _sockets(short_tmp) == []
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_cancelled_musician_with_neither_path_leaks(short_tmp: Path) -> None:
    """Control: proves the two tests above can fail."""
    gate, _ = await _cancel_running_musician(short_tmp, finally_teardown=False, backstop=False)
    try:
        assert gate.open_dispatches == 1
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


# ------------------------------------------------------------------ F4: own prompt channel


def _renderer_sheet(tmp: Path, template: str) -> Any:
    from marianne.core.sheet import Sheet

    return Sheet(
        num=1, movement=1, voice=None, voice_count=1, instrument_name="claude-code",
        instrument_config={}, workspace=tmp, prompt_template=template, timeout_seconds=30.0,
    )


def test_renderer_delivers_memory_block_once_even_with_techniques_template(
    tmp_path: Path,
) -> None:
    from marianne.daemon.baton.prompt import PromptRenderer
    from marianne.daemon.baton.state import AttemptContext, AttemptMode

    renderer = PromptRenderer(
        prompt_config=PromptConfig(), total_sheets=1, total_stages=1, parallel_enabled=False,
    )
    sheet = _renderer_sheet(tmp_path, "## Techniques Available\nUse them.")
    ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
    block = "## Memory Available This Phase\n- notes"
    out = renderer.render(
        sheet, ctx, technique_manifest="TECHNIQUE-MANIFEST", memory_manifest=block,
    )
    assert out.prompt.count("## Memory Available This Phase") == 1
    assert "TECHNIQUE-MANIFEST" not in out.prompt  # existing suppression is unchanged
    assert any(
        i["source"] == "memory:manifest" for i in out.context_manifest
    )
    plain = renderer.render(sheet, ctx)
    assert "Memory Available" not in plain.prompt
    assert not any(
        i["source"] == "memory:manifest" for i in plain.context_manifest
    )


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_memory_block_reaches_prompt_with_techniques_template(short_tmp: Path) -> None:
    gate = make(short_tmp)
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(pool)  # type: ignore[arg-type]
    adapter.set_memory_gate(gate)
    sheet = _sheet(short_tmp).model_copy(update={"prompt_template": "## Techniques Available\nx"})
    adapter.register_job(
        "mem-job", [sheet], {1: []}, prompt_config=PromptConfig(template="x"), memory=MEMORY,
    )
    try:
        await _complete(adapter, "mem-job")
        assert backend.prompt is not None
        assert backend.prompt.count("Memory Available This Phase") == 1
    finally:
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_memory_block_reaches_prompt_without_a_renderer(short_tmp: Path) -> None:
    gate = make(short_tmp)
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    adapter._job_renderers.pop("mem-job", None)
    try:
        await _complete(adapter, "mem-job")
        assert backend.prompt is not None
        assert backend.prompt.count("Memory Available This Phase") == 1
    finally:
        await gate.shutdown()


# ------------------------------------------------------------------ F6: cancel at the release await


class _BlockingReleasePool(_BackendPool):
    def __init__(self, backend: Any, registry: Any) -> None:
        super().__init__(backend, registry)
        self.release_started = asyncio.Event()
        self.release_gate = asyncio.Event()

    async def release(self, *args: Any, **kwargs: Any) -> None:
        self.release_started.set()
        await self.release_gate.wait()
        self.release_calls.append((args, kwargs))


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_cancel_at_release_await_still_releases_once_and_tears_down(
    short_tmp: Path,
) -> None:
    gate = make(short_tmp)
    backend = _Recording()
    pool = _BlockingReleasePool(backend, _Registry(profile("cli")))  # cli: delivery refused
    adapter = _adapter(pool, gate, short_tmp, memory=True)
    state = adapter.baton._jobs["mem-job"].sheets[1]
    try:
        call = asyncio.create_task(adapter._dispatch_callback("mem-job", 1, state))
        await asyncio.wait_for(pool.release_started.wait(), 10)
        assert gate.open_dispatches == 1
        call.cancel()
        await asyncio.sleep(0)
        pool.release_gate.set()  # the shielded release may now finish
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 10)
        for _ in range(200):  # the shielded task finishes on its own; bounded, event-driven
            if gate.open_dispatches == 0 and pool.release_calls:
                break
            await asyncio.sleep(0.01)
        assert len(pool.release_calls) == 1
        assert gate.open_dispatches == 0 and _sockets(short_tmp) == []
    finally:
        await gate.shutdown()


# ------------------------------------------------------------------ techniques AND memory


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_job_with_technique_mcp_and_memory_gets_both_in_one_config(
    short_tmp: Path,
) -> None:
    from marianne.core.config.techniques import TechniqueConfig, TechniqueKind
    from tests.test_mcp_conductor_dispatch import _McpPool

    gate = make(short_tmp)
    backend = _Recording()
    pool = _BackendPool(backend, _Registry(profile("claude-code")))
    mcp_pool = _McpPool()
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(pool)  # type: ignore[arg-type]
    adapter.set_mcp_pool(mcp_pool)  # type: ignore[arg-type]
    adapter.set_memory_gate(gate)
    ws = short_tmp / "ws"
    ws.mkdir()
    adapter.register_job(
        "mem-job", [_sheet(ws)], {1: []}, prompt_config=PromptConfig(template="x"),
        techniques={"filesystem": TechniqueConfig(
            kind=TechniqueKind.MCP, phases=["all"], config={"server": "filesystem"},
        )},
        memory=MEMORY,
    )
    try:
        await _complete(adapter, "mem-job")
        assert backend.config_json is not None
        assert set(backend.config_json["mcpServers"]) == {"filesystem", "memory-notes"}
        assert mcp_pool.calls == [(ws, ["filesystem"])]  # the technique path still ran
        assert backend.config_json["mcpServers"]["filesystem"]["command"] == "proxy"
        assert (ws / ".mcp-pool-config.json").exists()  # the technique's own file is untouched
        assert gate.open_dispatches == 0
    finally:
        await gate.shutdown()
