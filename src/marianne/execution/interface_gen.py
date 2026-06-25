"""Programmatic technique interface generation.

The dispatcher uses this module to expose resolved MCP techniques to code-mode
musicians. It emits two artifacts:

* compact Python stubs for prompt injection
* a self-contained ``techniques_rt.py`` runtime module for the sheet workspace

The runtime speaks JSON-RPC over the shared MCP pool's Unix sockets. It uses
only the standard library so sandboxed agent code can import it without
Marianne installed in the sandbox environment.
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MCPToolSpec:
    """Description of one MCP tool exposed through a technique."""

    name: str
    description: str = ""
    parameters: list[tuple[str, str, str | None]] = field(default_factory=list)
    return_type: str = "object"


@dataclass(frozen=True)
class TechniqueDeclaration:
    """Description of one generated technique interface."""

    name: str
    description: str = ""
    tools: list[MCPToolSpec] = field(default_factory=list)
    server_name: str | None = None


def estimate_tokens(text: str) -> int:
    """Return a cheap prompt-token estimate for generated stubs."""

    return max(1, (len(text) + 3) // 4) if text else 0


def python_identifier(name: str) -> str:
    """Convert an arbitrary technique/server name into a Python identifier."""

    cleaned = re.sub(r"\W+", "_", name.strip())
    cleaned = cleaned.strip("_") or "technique"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def declarations_from_mcp_servers(
    mcp_servers: Mapping[str, str],
) -> list[TechniqueDeclaration]:
    """Build generic declarations from resolved MCP technique/server names."""

    declarations: list[TechniqueDeclaration] = []
    for technique_name, server_name in mcp_servers.items():
        declarations.append(
            TechniqueDeclaration(
                name=technique_name,
                server_name=server_name,
                description=(
                    f"MCP server `{server_name}` exposed through the shared "
                    "Marianne MCP pool."
                ),
            )
        )
    return declarations


class InterfaceGenerator:
    """Generate prompt stubs and executable runtime code for techniques."""

    def generate_stubs(self, declarations: Sequence[TechniqueDeclaration]) -> str:
        """Generate compact prompt-injected Python stubs."""

        if not declarations:
            return ""

        names = _validated_python_names(declarations)
        lines: list[str] = [
            "## Programmatic MCP Interface",
            "",
            "When you need MCP tools from code mode, import these objects:",
            "```python",
            "from techniques_rt import " + ", ".join(names.values()),
            "```",
            "",
            "Each object supports:",
            "- `list_tools() -> object`",
            "- `call(tool: str, arguments: dict | None = None) -> object`",
            "- `raw(method: str, params: dict | None = None) -> object`",
            "",
            "```python",
        ]

        for declaration in declarations:
            py_name = names[declaration.name]
            server_name = declaration.server_name or declaration.name
            description = declaration.description or f"MCP server `{server_name}`."
            lines.extend(
                [
                    f"class {py_name}:",
                    f"    \"\"\"{_one_line_doc(description)}\"\"\"",
                    "    @staticmethod",
                    "    def list_tools() -> object: ...",
                    "    @staticmethod",
                    "    def call(tool: str, arguments: dict | None = None) -> object: ...",
                    "    @staticmethod",
                    "    def raw(method: str, params: dict | None = None) -> object: ...",
                ]
            )
            for tool in declaration.tools:
                method_name = python_identifier(tool.name)
                signature = _tool_signature(method_name, tool.parameters, tool.return_type)
                lines.extend(
                    [
                        "    @staticmethod",
                        f"    def {signature}: ...",
                    ]
                )
            lines.append("")

        lines.extend(["```", ""])
        return "\n".join(lines)

    def generate_implementation(
        self,
        declarations: Sequence[TechniqueDeclaration],
        *,
        socket_paths: Mapping[str, str],
    ) -> str:
        """Generate a self-contained ``techniques_rt.py`` runtime module.

        ``socket_paths`` may be keyed by technique name, sanitized Python name,
        or concrete MCP server name. This keeps the dispatcher tolerant of
        score declarations where the technique alias differs from the pool key.
        """

        if not declarations:
            return ""

        names = _validated_python_names(declarations)
        socket_map: dict[str, str] = {}
        server_map: dict[str, str] = {}
        for declaration in declarations:
            py_name = names[declaration.name]
            server_name = declaration.server_name or declaration.name
            path = (
                socket_paths.get(declaration.name)
                or socket_paths.get(py_name)
                or socket_paths.get(server_name)
            )
            if not path:
                continue
            socket_map[py_name] = path
            server_map[py_name] = server_name

        if not socket_map:
            return ""

        lines: list[str] = [
            '"""Generated by Marianne. Do not edit by hand."""',
            "",
            "from __future__ import annotations",
            "",
            "import json",
            "import socket",
            "from typing import Any",
            "",
            f"_SOCKET_PATHS = {_repr_string_map(socket_map)}",
            f"_SERVER_NAMES = {_repr_string_map(server_map)}",
            "_REQUEST_ID = 0",
            "",
            "",
            "def _next_id() -> int:",
            "    global _REQUEST_ID",
            "    _REQUEST_ID += 1",
            "    return _REQUEST_ID",
            "",
            "",
            "def _send(sock: socket.socket, message: dict[str, Any]) -> None:",
            "    body = json.dumps(message, separators=(',', ':')).encode('utf-8')",
            "    sock.sendall(body + b'\\n')",
            "",
            "",
            "def _read(reader: Any) -> dict[str, Any]:",
            "    line = reader.readline()",
            "    if not line:",
            "        raise RuntimeError('MCP socket closed before response')",
            "    if line.lower().startswith(b'content-length:'):",
            "        length = int(line.split(b':', 1)[1].strip())",
            "        while True:",
            "            header = reader.readline()",
            "            if not header:",
            "                raise RuntimeError('MCP socket closed in headers')",
            "            if header in (b'\\r\\n', b'\\n'):",
            "                break",
            "        body = reader.read(length)",
            "        return json.loads(body.decode('utf-8'))",
            "    return json.loads(line.decode('utf-8'))",
            "",
            "",
            "def _raise_for_error(response: dict[str, Any]) -> None:",
            "    error = response.get('error')",
            "    if error is None:",
            "        return",
            "    if isinstance(error, dict):",
            "        message = error.get('message') or repr(error)",
            "    else:",
            "        message = repr(error)",
            "    raise RuntimeError(f'MCP JSON-RPC error: {message}')",
            "",
            "",
            "def _request(socket_path: str, method: str, params: dict[str, Any] | None) -> Any:",
            "    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:",
            "        sock.connect(socket_path)",
            "        reader = sock.makefile('rb')",
            "        init_id = _next_id()",
            "        _send(sock, {",
            "            'jsonrpc': '2.0',",
            "            'id': init_id,",
            "            'method': 'initialize',",
            "            'params': {",
            "                'protocolVersion': '2025-11-25',",
            "                'capabilities': {'tools': {}},",
            "                'clientInfo': {'name': 'marianne-code-mode', 'version': '0.1.0'},",
            "            },",
            "        })",
            "        init_response = _read(reader)",
            "        _raise_for_error(init_response)",
            "        _send(sock, {",
            "            'jsonrpc': '2.0',",
            "            'method': 'notifications/initialized',",
            "            'params': {},",
            "        })",
            "        request_id = _next_id()",
            "        _send(sock, {",
            "            'jsonrpc': '2.0',",
            "            'id': request_id,",
            "            'method': method,",
            "            'params': params or {},",
            "        })",
            "        response = _read(reader)",
            "        _raise_for_error(response)",
            "        return response.get('result')",
            "",
            "",
            "class _MCPServer:",
            "    def __init__(self, server_name: str, socket_path: str) -> None:",
            "        self.server_name = server_name",
            "        self.socket_path = socket_path",
            "",
            "    def raw(self, method: str, params: dict[str, Any] | None = None) -> Any:",
            "        return _request(self.socket_path, method, params)",
            "",
            "    def list_tools(self) -> Any:",
            "        return self.raw('tools/list', {})",
            "",
            "    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:",
            "        return self.raw('tools/call', {'name': tool, 'arguments': arguments or {}})",
            "",
        ]

        for declaration in declarations:
            py_name = names[declaration.name]
            if py_name not in socket_map:
                continue
            for tool in declaration.tools:
                method_name = python_identifier(tool.name)
                params = [name for name, _typ, _default in tool.parameters]
                lines.extend(_generated_tool_method(method_name, tool.name, params))

        for py_name in socket_map:
            lines.append(
                f"{py_name} = _MCPServer(_SERVER_NAMES[{py_name!r}], _SOCKET_PATHS[{py_name!r}])"
            )
        lines.append("")
        return "\n".join(lines)


def _validated_python_names(
    declarations: Sequence[TechniqueDeclaration],
) -> dict[str, str]:
    names: dict[str, str] = {}
    seen: set[str] = set()
    for declaration in declarations:
        base = python_identifier(declaration.name)
        candidate = base
        index = 2
        while candidate in seen:
            candidate = f"{base}_{index}"
            index += 1
        seen.add(candidate)
        names[declaration.name] = candidate
    return names


def _one_line_doc(value: str) -> str:
    return " ".join(value.replace('"""', "'''").split())


def _tool_signature(
    method_name: str,
    parameters: Sequence[tuple[str, str, str | None]],
    return_type: str,
) -> str:
    rendered_params: list[str] = []
    for raw_name, type_hint, default in parameters:
        name = python_identifier(raw_name)
        param = f"{name}: {type_hint or 'object'}"
        if default is not None:
            param = f"{param} = {default}"
        rendered_params.append(param)
    joined = ", ".join(rendered_params)
    return f"{method_name}({joined}) -> {return_type or 'object'}"


def _generated_tool_method(
    method_name: str,
    tool_name: str,
    params: Sequence[str],
) -> list[str]:
    safe_params = [python_identifier(p) for p in params]
    signature = ", ".join([f"{name}: Any = None" for name in safe_params])
    if signature:
        signature = f", {signature}"
    lines = [
        f"def _method_{method_name}(self{signature}) -> Any:",
        "    arguments: dict[str, Any] = {}",
    ]
    for original, safe in zip(params, safe_params, strict=True):
        lines.append(f"    if {safe} is not None:")
        lines.append(f"        arguments[{original!r}] = {safe}")
    lines.append(f"    return self.call({tool_name!r}, arguments)")
    lines.append("")
    lines.append(f"setattr(_MCPServer, {method_name!r}, _method_{method_name})")
    lines.append("")
    return lines


def _repr_string_map(values: Mapping[str, str]) -> str:
    items = ", ".join(
        f"{key!r}: {value!r}" for key, value in sorted(values.items())
    )
    return "{" + items + "}"
