"""MCP pool integration at baton dispatch."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from marianne.core.config.execution import ValidationRule
from marianne.core.config.instruments import InstrumentProfile
from marianne.core.config.job import PromptConfig
from marianne.core.config.techniques import TechniqueConfig, TechniqueKind
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import ShutdownRequested
from marianne.execution.base import ExecutionResult

BUILTINS_DIR = Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"


def _sheet(
    workspace: Path,
    *,
    instrument_name: str = "claude-code",
    instrument_config: dict[str, Any] | None = None,
    validations: list[Any] | None = None,
) -> Sheet:
    return Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        instrument_name=instrument_name,
        instrument_config=instrument_config or {},
        workspace=workspace,
        prompt_template="Use MCP if available",
        validations=validations or [],
        timeout_seconds=30.0,
    )


class _BackendWithMcpConfig:
    def __init__(self) -> None:
        self.config_at_execute: Path | None = None
        self.config_seen_during_execute: Path | None = None
        self.set_calls: list[Path | None] = []

    def set_preamble(self, preamble: str | None) -> None:
        pass

    def set_output_callback(self, callback: Any) -> None:
        pass

    def clear_overrides(self) -> None:
        pass

    def set_mcp_config(self, config_path: Path | None) -> None:
        self.set_calls.append(config_path)
        self.config_at_execute = config_path

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        self.config_seen_during_execute = self.config_at_execute
        return ExecutionResult(
            success=True,
            stdout="done",
            stderr="",
            duration_seconds=0.01,
            exit_code=0,
            model="fake",
        )


class _BackendWithoutMcpConfig:
    def __init__(self) -> None:
        self.prompt_seen: str | None = None

    def set_preamble(self, preamble: str | None) -> None:
        pass

    def set_output_callback(self, callback: Any) -> None:
        pass

    def clear_overrides(self) -> None:
        pass

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        self.prompt_seen = prompt
        stdout = (
            "```python\n"
            "from pathlib import Path\n"
            "from techniques_rt import filesystem\n"
            "result = filesystem.call('echo', {'message': 'MCP_BRIDGE_OK'})\n"
            "Path('mcp-marker.txt').write_text(result['content'][0]['text'])\n"
            "print(result['content'][0]['text'])\n"
            "```\n"
        )
        return ExecutionResult(
            success=True,
            stdout=stdout,
            stderr="",
            duration_seconds=0.01,
            exit_code=0,
            model="fake",
        )


class _BackendPool:
    def __init__(self, backend: Any, registry: Any | None = None) -> None:
        self.backend = backend
        self._registry = registry
        self.release_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def acquire(self, *args: Any, **kwargs: Any) -> Any:
        return self.backend

    async def release(self, *args: Any, **kwargs: Any) -> None:
        self.release_calls.append((args, kwargs))

    async def close_all(self) -> None:
        pass


class _McpPool:
    def __init__(self, socket_path: Path | None = None) -> None:
        self.calls: list[tuple[Path, list[str] | None]] = []
        self.socket_path = socket_path

    def generate_mcp_config_file(
        self,
        workspace: Path,
        *,
        server_names: list[str] | None = None,
    ) -> Path:
        self.calls.append((workspace, server_names))
        path = workspace / ".mcp-pool-config.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        name: {"command": "proxy", "args": []}
                        for name in (server_names or [])
                    }
                }
            )
        )
        return path

    def is_running(self, name: str) -> bool:
        return self.socket_path is not None

    def get_socket_path(self, name: str) -> Path | None:
        return self.socket_path


class _FailingMcpPool(_McpPool):
    def generate_mcp_config_file(
        self,
        workspace: Path,
        *,
        server_names: list[str] | None = None,
    ) -> Path:
        raise OSError("injected MCP setup failure")


class _Registry:
    def __init__(self, profile: InstrumentProfile) -> None:
        self.profile = profile

    def get(self, name: str) -> InstrumentProfile:
        return self.profile


def _load_builtin_profiles() -> list[InstrumentProfile]:
    profiles: list[InstrumentProfile] = []
    for path in sorted(BUILTINS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        profiles.append(InstrumentProfile.model_validate(data))
    return profiles


@pytest.mark.asyncio
async def test_mcp_setup_failure_happens_before_backend_acquisition(
    tmp_path: Path,
) -> None:
    from marianne.daemon.baton.adapter import BatonAdapter

    backend_pool = MagicMock()
    backend_pool.acquire = AsyncMock()
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(backend_pool)
    adapter.set_mcp_pool(_FailingMcpPool())  # type: ignore[arg-type]
    adapter.register_job(
        "mcp-setup-failure",
        [_sheet(tmp_path)],
        {1: []},
        techniques={
            "filesystem": TechniqueConfig(
                kind=TechniqueKind.MCP,
                phases=["all"],
                config={"server": "filesystem"},
            )
        },
    )
    while not adapter.baton.inbox.empty():
        adapter.baton.inbox.get_nowait()
    state = adapter.baton._jobs["mcp-setup-failure"].sheets[1]

    await adapter._dispatch_callback("mcp-setup-failure", 1, state)

    backend_pool.acquire.assert_not_awaited()
    failure = adapter.baton.inbox.get_nowait()
    assert failure.execution_success is False
    assert "MCP setup failed before backend acquisition" in failure.error_message


@pytest.mark.asyncio
async def test_post_acquire_interactive_setup_failure_releases_backend(
    tmp_path: Path,
) -> None:
    from marianne.daemon.baton.adapter import BatonAdapter
    from marianne.execution.instruments.interactive import InteractiveCliBackend

    profile_path = BUILTINS_DIR / "claude-code.yaml"
    profile = InstrumentProfile.model_validate(yaml.safe_load(profile_path.read_text()))
    backend = InteractiveCliBackend(profile, working_directory=tmp_path, tmux=MagicMock())
    pool = _BackendPool(backend)
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(pool)  # type: ignore[arg-type]
    sheet = _sheet(
        tmp_path,
        instrument_config={
            "interactive": True,
            "interactive_max_nudges": "not-an-int",
        },
    )
    adapter.register_job("interactive-setup-failure", [sheet], {1: []})
    while not adapter.baton.inbox.empty():
        adapter.baton.inbox.get_nowait()
    state = adapter.baton._jobs["interactive-setup-failure"].sheets[1]

    await adapter._dispatch_callback("interactive-setup-failure", 1, state)

    assert len(pool.release_calls) == 1
    failure = adapter.baton.inbox.get_nowait()
    assert failure.execution_success is False
    assert "Post-acquisition dispatch setup failed" in failure.error_message


@pytest.mark.asyncio
async def test_baton_dispatch_sets_mcp_config_for_resolved_mcp_technique(
    tmp_path: Path,
) -> None:
    from marianne.daemon.baton.adapter import BatonAdapter

    backend = _BackendWithMcpConfig()
    mcp_pool = _McpPool()
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(_BackendPool(backend))
    adapter.set_mcp_pool(mcp_pool)  # type: ignore[arg-type]

    adapter.register_job(
        "mcp-job",
        [_sheet(tmp_path)],
        {1: []},
        prompt_config=PromptConfig(template="Do MCP work"),
        techniques={
            "filesystem": TechniqueConfig(
                kind=TechniqueKind.MCP,
                phases=["all"],
                config={"server": "filesystem"},
            )
        },
    )

    run_task = asyncio.create_task(adapter.run())
    try:
        assert await asyncio.wait_for(
            adapter.wait_for_completion("mcp-job"),
            timeout=3.0,
        )
    finally:
        await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
        await asyncio.wait_for(run_task, timeout=3.0)

    assert mcp_pool.calls == [(tmp_path, ["filesystem"])]
    config_at_execute = backend.set_calls[0]
    assert config_at_execute is not None
    assert config_at_execute.exists()
    assert backend.config_seen_during_execute == config_at_execute
    assert backend.set_calls[-1] is None


@pytest.mark.asyncio
async def test_baton_dispatch_generates_code_bridge_for_non_native_backend(
    tmp_path: Path,
) -> None:
    from marianne.daemon.baton.adapter import BatonAdapter

    socket_path = tmp_path / "filesystem.sock"

    async def handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                message = json.loads(line.decode("utf-8"))
                method = message.get("method")
                if method == "notifications/initialized":
                    continue
                if method == "initialize":
                    result: dict[str, Any] = {"serverInfo": {"name": "fake"}}
                elif method == "tools/call":
                    params = message.get("params", {})
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": params.get("arguments", {}).get("message", ""),
                            }
                        ]
                    }
                else:
                    result = {"ok": True}
                writer.write(
                    (
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": message.get("id"),
                                "result": result,
                            }
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    backend = _BackendWithoutMcpConfig()
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(_BackendPool(backend))
    adapter.set_mcp_pool(_McpPool(socket_path))  # type: ignore[arg-type]

    adapter.register_job(
        "mcp-code-bridge-job",
        [
            _sheet(
                tmp_path,
                instrument_name="opencode",
                validations=[
                    ValidationRule(
                        type="content_contains",
                        path="{workspace}/mcp-marker.txt",
                        pattern="MCP_BRIDGE_OK",
                    )
                ],
            )
        ],
        {1: []},
        prompt_config=PromptConfig(template="Use the shared MCP bridge"),
        techniques={
            "filesystem": TechniqueConfig(
                kind=TechniqueKind.MCP,
                phases=["all"],
                config={"server": "filesystem"},
            )
        },
    )

    run_task = asyncio.create_task(adapter.run())
    try:
        assert await asyncio.wait_for(
            adapter.wait_for_completion("mcp-code-bridge-job"),
            timeout=5.0,
        )
    finally:
        await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
        await asyncio.wait_for(run_task, timeout=3.0)
        server.close()
        await server.wait_closed()
        socket_path.unlink(missing_ok=True)

    runtime_path = tmp_path / "techniques_rt.py"
    assert runtime_path.exists()
    assert "class _MCPServer" in runtime_path.read_text(encoding="utf-8")
    assert backend.prompt_seen is not None
    assert "from techniques_rt import filesystem" in backend.prompt_seen
    assert (tmp_path / "mcp-marker.txt").read_text() == "MCP_BRIDGE_OK"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    _load_builtin_profiles(),
    ids=lambda profile: profile.name,
)
async def test_all_builtin_profiles_can_use_shared_mcp_code_bridge(
    profile: InstrumentProfile,
    tmp_path: Path,
) -> None:
    from marianne.daemon.baton.adapter import BatonAdapter

    socket_path = tmp_path / f"{profile.name}.sock"

    async def handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                message = json.loads(line.decode("utf-8"))
                method = message.get("method")
                if method == "notifications/initialized":
                    continue
                if method == "initialize":
                    result: dict[str, Any] = {"serverInfo": {"name": "fake"}}
                else:
                    params = message.get("params", {})
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": params.get("arguments", {}).get("message", ""),
                            }
                        ]
                    }
                writer.write(
                    (
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": message.get("id"),
                                "result": result,
                            }
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    backend = _BackendWithoutMcpConfig()
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.set_backend_pool(_BackendPool(backend, registry=_Registry(profile)))
    adapter.set_mcp_pool(_McpPool(socket_path))  # type: ignore[arg-type]

    job_id = f"mcp-code-bridge-{profile.name}"
    adapter.register_job(
        job_id,
        [
            _sheet(
                tmp_path,
                instrument_name=profile.name,
                validations=[
                    ValidationRule(
                        type="content_contains",
                        path="{workspace}/mcp-marker.txt",
                        pattern="MCP_BRIDGE_OK",
                    )
                ],
            )
        ],
        {1: []},
        prompt_config=PromptConfig(template="Use the shared MCP bridge"),
        techniques={
            "filesystem": TechniqueConfig(
                kind=TechniqueKind.MCP,
                phases=["all"],
                config={"server": "filesystem"},
            )
        },
    )

    run_task = asyncio.create_task(adapter.run())
    try:
        assert await asyncio.wait_for(adapter.wait_for_completion(job_id), timeout=5.0)
    finally:
        await adapter.baton.inbox.put(ShutdownRequested(graceful=True))
        await asyncio.wait_for(run_task, timeout=3.0)
        server.close()
        await server.wait_closed()
        socket_path.unlink(missing_ok=True)

    assert (tmp_path / "techniques_rt.py").exists()
    assert (tmp_path / "mcp-marker.txt").read_text() == "MCP_BRIDGE_OK"
