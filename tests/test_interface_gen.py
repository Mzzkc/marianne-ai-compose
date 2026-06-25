"""Programmatic MCP interface generation."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from marianne.execution.interface_gen import (
    InterfaceGenerator,
    MCPToolSpec,
    TechniqueDeclaration,
    declarations_from_mcp_servers,
    estimate_tokens,
    python_identifier,
)


def test_python_identifier_sanitizes_server_names() -> None:
    assert python_identifier("repo-symbols") == "repo_symbols"
    assert python_identifier("3d-tools") == "_3d_tools"
    assert python_identifier("class") == "class_"


def test_generate_stubs_are_compact_and_show_imports() -> None:
    generator = InterfaceGenerator()
    stubs = generator.generate_stubs(
        declarations_from_mcp_servers(
            {
                "filesystem": "filesystem",
                "repo-symbols": "repo-symbols",
            }
        )
    )

    assert "from techniques_rt import filesystem, repo_symbols" in stubs
    assert "def call(tool: str, arguments: dict | None = None) -> object" in stubs
    assert estimate_tokens(stubs) < 500


def test_generate_stubs_disambiguates_sanitized_name_collisions() -> None:
    generator = InterfaceGenerator()
    stubs = generator.generate_stubs(
        [
            TechniqueDeclaration(name="repo-symbols", server_name="repo-symbols"),
            TechniqueDeclaration(name="repo_symbols", server_name="repo_symbols"),
        ]
    )

    assert "from techniques_rt import repo_symbols, repo_symbols_2" in stubs
    assert "class repo_symbols:" in stubs
    assert "class repo_symbols_2:" in stubs


def test_generate_implementation_degrades_when_socket_paths_missing() -> None:
    generator = InterfaceGenerator()
    runtime_source = generator.generate_implementation(
        [TechniqueDeclaration(name="filesystem", server_name="filesystem")],
        socket_paths={},
    )

    assert runtime_source == ""


@pytest.mark.asyncio
async def test_generated_runtime_calls_mcp_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "mcp.sock"
    calls: list[dict[str, Any]] = []

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
                calls.append(message)
                method = message.get("method")
                if method == "notifications/initialized":
                    continue
                if method == "initialize":
                    result: dict[str, Any] = {"serverInfo": {"name": "fake"}}
                elif method == "tools/list":
                    result = {"tools": [{"name": "echo"}]}
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
    try:
        generator = InterfaceGenerator()
        runtime_source = generator.generate_implementation(
            [
                TechniqueDeclaration(
                    name="filesystem",
                    server_name="filesystem",
                    tools=[
                        MCPToolSpec(
                            name="echo",
                            parameters=[("message", "str", None)],
                        )
                    ],
                )
            ],
            socket_paths={"filesystem": str(socket_path)},
        )
        runtime_path = tmp_path / "techniques_rt.py"
        runtime_path.write_text(runtime_source, encoding="utf-8")

        def run_runtime() -> Any:
            module = _load_module(runtime_path)
            return module.filesystem.echo(message="MCP_OK")

        result = await asyncio.to_thread(run_runtime)
    finally:
        server.close()
        await server.wait_closed()
        socket_path.unlink(missing_ok=True)

    assert result["content"][0]["text"] == "MCP_OK"
    assert [call["method"] for call in calls] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("techniques_rt_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.pop("techniques_rt_test", None)
    spec.loader.exec_module(module)
    return module
