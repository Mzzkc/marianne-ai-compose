"""Programmatic interface generator — typed Python stubs for agent code mode.

Free-tier LLM instruments (most OpenRouter models) lack native tool-use
support. The traditional workaround — describing each tool as a distinct
prompt block — burns tokens and produces unreliable sequential round-trips.
This module implements the Cloudflare Dynamic Workers pattern: expose
capabilities as a compact, typed Python interface. Agents write code
against the interface; the code runs in a sandbox with real implementations
that proxy to the shared MCP pool over Unix sockets.

Two artifacts are produced per technique set:

1. **Stubs** (:meth:`InterfaceGenerator.generate_stubs`) — Python class
   signatures with type hints, injected into the prompt. Optimized for
   token efficiency (~500 tokens for a typical 4-technique config).

2. **Implementations** (:meth:`InterfaceGenerator.generate_implementation`)
   — Python classes with real method bodies that connect to a Unix socket
   and issue MCP JSON-RPC ``tools/call`` requests. Loaded into the
   sandbox alongside the agent's generated code.

The stubs and implementations share class and method names, so the
agent's generated code works unchanged when executed in the sandbox.

See: compiler design spec Section 8.2 (Programmatic Interface Generation)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Characters-per-token estimate for budget checks. This tracks tiktoken's
# observed ratio for Python source (~3.7-4.2 chars/token). Using 4 gives a
# conservative estimate that errs slightly high — exactly what we want for
# a prompt-budget assertion.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class MCPToolSpec:
    """One tool exposed by an MCP server.

    Attributes:
        name: Tool name as exposed by the MCP server (becomes the
            Python method name).
        description: Human-readable description of the tool, used as
            the method docstring.
        parameters: Ordered list of ``(name, type_hint, default)`` tuples.
            ``default`` is a Python literal as source text (e.g.
            ``'"open"'`` or ``"None"``) or ``None`` for no default.
        return_type: Python type annotation for the return value
            (e.g. ``"str"``, ``"list[dict]"``).
    """

    name: str
    description: str
    parameters: list[tuple[str, str, str | None]] = field(default_factory=list)
    return_type: str = "None"


@dataclass(frozen=True)
class TechniqueDeclaration:
    """A technique exposed to the agent as a single class.

    Attributes:
        name: Class name the agent sees (e.g. ``"workspace"``, ``"github"``).
            Used as the MCP server identifier for socket lookup.
        description: Class-level docstring describing the technique.
        tools: Tools provided by this technique. Each becomes a method.
    """

    name: str
    description: str
    tools: list[MCPToolSpec] = field(default_factory=list)


class InterfaceGenerator:
    """Generates typed stubs and MCP-proxy implementations.

    The stub output is designed to be injected directly into an LLM prompt
    as a technique manifest. The implementation output is meant to be
    executed inside a sandbox, where the generated classes are importable
    as real Python at runtime.

    Usage::

        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_tech, github_tech])
        # Inject ``stubs`` into prompt as part of technique manifest

        impl = gen.generate_implementation(
            [workspace_tech, github_tech],
            socket_paths={
                "workspace": Path("/tmp/mzt/mcp/workspace.sock"),
                "github": Path("/tmp/mzt/mcp/github.sock"),
            },
        )
        # Persist ``impl`` to the sandbox, loadable by agent-generated code
    """

    def generate_stubs(self, declarations: list[TechniqueDeclaration]) -> str:
        """Render typed Python stubs for the given techniques.

        Each declaration produces a single class with one method per tool.
        Method bodies are ``...`` — only the type signatures matter, since
        the stubs are for prompt injection, not execution.

        Args:
            declarations: Techniques to render.

        Returns:
            Python source text. Empty string if ``declarations`` is empty.
        """
        if not declarations:
            return ""

        class_blocks: list[str] = []
        for decl in declarations:
            class_blocks.append(self._render_stub_class(decl))

        return "\n\n".join(class_blocks) + "\n"

    def generate_implementation(
        self,
        declarations: list[TechniqueDeclaration],
        *,
        socket_paths: dict[str, Path],
    ) -> str:
        """Render the real implementations that proxy to the MCP pool.

        Each declaration produces a class with real method bodies that
        open a Unix socket connection to the technique's MCP server and
        issue an MCP JSON-RPC ``tools/call`` request per method. The
        response's text content (or raw result) is returned.

        Args:
            declarations: Techniques to render implementations for.
            socket_paths: Mapping from technique name to the Unix socket
                path exposed by the shared MCP pool.

        Returns:
            Python source text. Always includes a minimal async JSON-RPC
            proxy helper that the generated methods call into.

        Raises:
            ValueError: If ``declarations`` references a technique with no
                entry in ``socket_paths`` — missing socket paths would
                produce code that fails at runtime, so we surface the
                misconfiguration up front.
        """
        for decl in declarations:
            if decl.name not in socket_paths:
                raise ValueError(
                    f"Missing socket_path for technique '{decl.name}'. "
                    "Every declaration must have a corresponding socket "
                    "path in the socket_paths mapping."
                )

        if not declarations:
            return _PROXY_HELPER + "\n"

        class_blocks: list[str] = []
        for decl in declarations:
            class_blocks.append(
                self._render_impl_class(decl, socket_paths[decl.name])
            )

        return _PROXY_HELPER + "\n\n" + "\n\n".join(class_blocks) + "\n"

    # -- Internal rendering helpers -------------------------------------------

    def _render_stub_class(self, decl: TechniqueDeclaration) -> str:
        """Render a single stub class for a technique."""
        lines = [f"class {decl.name}:"]
        lines.append(f'    """{decl.description}"""')
        if not decl.tools:
            lines.append("    pass")
            return "\n".join(lines)
        for tool in decl.tools:
            sig = self._format_signature(tool, async_def=False)
            lines.append(f"    {sig}: ...")
        return "\n".join(lines)

    def _render_impl_class(
        self, decl: TechniqueDeclaration, socket_path: Path
    ) -> str:
        """Render a single implementation class for a technique.

        Methods are ``async`` because the MCP proxy helper is async. Agent
        code awaits these methods from within an event loop managed by
        the sandbox harness.
        """
        lines = [f"class {decl.name}:"]
        lines.append(f'    """{decl.description}"""')
        lines.append("")
        lines.append(f"    _SOCKET_PATH = {str(socket_path)!r}")
        if not decl.tools:
            lines.append("    pass")
            return "\n".join(lines)

        for tool in decl.tools:
            sig = self._format_signature(tool, async_def=True)
            lines.append("")
            lines.append(f"    {sig}:")
            lines.append(f'        """{tool.description}"""')
            arg_names = [p[0] for p in tool.parameters]
            dict_body = ", ".join(f'"{n}": {n}' for n in arg_names)
            lines.append(f"        arguments = {{{dict_body}}}")
            lines.append(
                f'        return await _mcp_call(self._SOCKET_PATH, "{tool.name}", arguments)'
            )
        return "\n".join(lines)

    def _format_signature(self, tool: MCPToolSpec, *, async_def: bool) -> str:
        """Format a method signature for either a stub or an impl.

        ``async_def=True`` marks the method as ``async def`` — used for the
        implementation where each method awaits a socket round-trip.
        """
        prefix = "async def" if async_def else "def"
        parts: list[str] = ["self"]
        for name, type_hint, default in tool.parameters:
            if default is None:
                parts.append(f"{name}: {type_hint}")
            else:
                parts.append(f"{name}: {type_hint} = {default}")
        params = ", ".join(parts)
        return f"{prefix} {tool.name}({params}) -> {tool.return_type}"


def estimate_tokens(text: str) -> int:
    """Rough token count estimate for prompt budget checks.

    Uses a fixed ``chars / 4`` heuristic consistent with observed tokenizer
    output for Python source code. This is intentionally simple — we only
    need order-of-magnitude accuracy for ``<500 token`` assertions, not a
    real tokenizer dependency in the hot path.

    Args:
        text: Source text to estimate.

    Returns:
        Estimated token count (0 for empty string).
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


# The proxy helper is embedded in every generated implementation so the
# sandbox can run the code standalone — the agent's environment does not
# need Marianne installed. It speaks minimal line-delimited MCP JSON-RPC
# over a Unix stream socket, matching the shape used by the shared MCP
# pool's proxy sockets.
_PROXY_HELPER = '''"""Auto-generated MCP proxy bindings. Do not edit by hand."""
from __future__ import annotations

import asyncio
import json
from typing import Any

_next_id = 0


def _new_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


async def _mcp_call(socket_path: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    """Invoke an MCP ``tools/call`` request and return the unpacked result.

    Connects to the Unix socket at ``socket_path``, sends a newline-framed
    JSON-RPC 2.0 request, reads one line of response, and returns the
    server-provided content.
    """
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        request = {
            "jsonrpc": "2.0",
            "id": _new_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        payload = (json.dumps(request) + "\\n").encode("utf-8")
        writer.write(payload)
        await writer.drain()

        line = await reader.readline()
        if not line:
            raise RuntimeError(
                f"MCP server at {socket_path} closed connection without response"
            )
        response = json.loads(line.decode("utf-8"))
        if "error" in response:
            raise RuntimeError(f"MCP error: {response[\'error\']}")

        result = response.get("result", {})
        # MCP tools/call responses typically wrap output in a content list
        # of ``{type: "text", text: ...}`` blocks. Unwrap single text blocks
        # so agent code sees a string, not a nested dict.
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and len(content) == 1:
            block = content[0]
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        return result
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
'''
