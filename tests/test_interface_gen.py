"""Tests for the programmatic interface generator.

The interface generator takes technique declarations (MCP servers + their
tool lists) and produces two artifacts:

1. **Typed stubs** — short Python stub classes agents see in prompts.
   These are the type signatures only, optimized for token efficiency.
2. **Implementations** — real Python code that runs in the sandbox and
   proxies method calls to the MCP pool via Unix socket.

See: compiler design spec Section 8.2 (Programmatic Interface Generation)
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from marianne.execution.interface_gen import (
    InterfaceGenerator,
    MCPToolSpec,
    TechniqueDeclaration,
    estimate_tokens,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace_technique() -> TechniqueDeclaration:
    """A typical filesystem/workspace technique declaration."""
    return TechniqueDeclaration(
        name="workspace",
        description="File operations in your workspace.",
        tools=[
            MCPToolSpec(
                name="read",
                description="Read a file's contents.",
                parameters=[("path", "str", None)],
                return_type="str",
            ),
            MCPToolSpec(
                name="write",
                description="Write content to a file.",
                parameters=[("path", "str", None), ("content", "str", None)],
                return_type="None",
            ),
            MCPToolSpec(
                name="list_dir",
                description="List files in a directory.",
                parameters=[("directory", "str", None)],
                return_type="list[str]",
            ),
        ],
    )


@pytest.fixture
def github_technique() -> TechniqueDeclaration:
    """A GitHub MCP server technique with defaults and complex types."""
    return TechniqueDeclaration(
        name="github",
        description="GitHub operations via shared MCP pool.",
        tools=[
            MCPToolSpec(
                name="list_issues",
                description="List issues in the repository.",
                parameters=[
                    ("state", "str", '"open"'),
                    ("labels", "list[str]", "None"),
                ],
                return_type="list[dict]",
            ),
            MCPToolSpec(
                name="get_issue",
                description="Get a single issue by number.",
                parameters=[("number", "int", None)],
                return_type="dict",
            ),
        ],
    )


def _load_module_from_source(source: str, tmp_path: Path, name: str) -> object:
    """Write ``source`` to a file and import it as a module.

    Used to load generated implementation code under test without relying on
    dynamic evaluation primitives.
    """
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# Stub generation — single technique
# =============================================================================


class TestSingleTechniqueStub:
    """A single technique produces a single class with correct structure."""

    def test_generates_single_class(
        self, workspace_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique])
        assert "class workspace:" in stubs

    def test_includes_class_docstring(
        self, workspace_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique])
        assert "File operations in your workspace." in stubs

    def test_includes_all_methods(
        self, workspace_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique])
        assert "def read(" in stubs
        assert "def write(" in stubs
        assert "def list_dir(" in stubs

    def test_method_type_hints(
        self, workspace_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique])
        # Type hints on parameters
        assert "path: str" in stubs
        # Return types
        assert "-> str:" in stubs
        assert "-> None:" in stubs
        assert "-> list[str]:" in stubs

    def test_method_default_values(
        self, github_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([github_technique])
        assert 'state: str = "open"' in stubs
        assert "labels: list[str] = None" in stubs


# =============================================================================
# Stub generation — multiple techniques
# =============================================================================


class TestMultipleTechniqueStubs:
    """Multiple techniques produce multiple classes, correctly separated."""

    def test_generates_all_classes(
        self,
        workspace_technique: TechniqueDeclaration,
        github_technique: TechniqueDeclaration,
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique, github_technique])
        assert "class workspace:" in stubs
        assert "class github:" in stubs

    def test_preserves_order(
        self,
        workspace_technique: TechniqueDeclaration,
        github_technique: TechniqueDeclaration,
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique, github_technique])
        ws_idx = stubs.index("class workspace:")
        gh_idx = stubs.index("class github:")
        assert ws_idx < gh_idx

    def test_empty_declarations_yields_empty_output(self) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([])
        assert "class " not in stubs


# =============================================================================
# Valid Python — compile check
# =============================================================================


class TestStubValidPython:
    """Generated stubs must be valid Python syntax."""

    def test_single_technique_parses(
        self, workspace_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique])
        ast.parse(stubs)

    def test_multi_technique_parses(
        self,
        workspace_technique: TechniqueDeclaration,
        github_technique: TechniqueDeclaration,
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique, github_technique])
        ast.parse(stubs)

    def test_defaults_parse(
        self, github_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([github_technique])
        tree = ast.parse(stubs)
        found_methods: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                found_methods.append(node.name)
        assert "list_issues" in found_methods
        assert "get_issue" in found_methods

    def test_implementation_parses(
        self, workspace_technique: TechniqueDeclaration, tmp_path: Path
    ) -> None:
        gen = InterfaceGenerator()
        impl = gen.generate_implementation(
            [workspace_technique],
            socket_paths={"workspace": tmp_path / "workspace.sock"},
        )
        ast.parse(impl)


# =============================================================================
# Implementation — proxies to MCP
# =============================================================================


class TestImplementation:
    """Implementation code proxies method calls to a mock MCP server."""

    def test_implementation_includes_class(
        self, workspace_technique: TechniqueDeclaration, tmp_path: Path
    ) -> None:
        gen = InterfaceGenerator()
        impl = gen.generate_implementation(
            [workspace_technique],
            socket_paths={"workspace": tmp_path / "workspace.sock"},
        )
        assert "class workspace:" in impl

    def test_implementation_references_socket(
        self, workspace_technique: TechniqueDeclaration, tmp_path: Path
    ) -> None:
        gen = InterfaceGenerator()
        sock = tmp_path / "workspace.sock"
        impl = gen.generate_implementation(
            [workspace_technique],
            socket_paths={"workspace": sock},
        )
        assert str(sock) in impl

    def test_implementation_has_method_bodies(
        self, workspace_technique: TechniqueDeclaration, tmp_path: Path
    ) -> None:
        gen = InterfaceGenerator()
        impl = gen.generate_implementation(
            [workspace_technique],
            socket_paths={"workspace": tmp_path / "workspace.sock"},
        )
        # Must send JSON-RPC
        assert "jsonrpc" in impl
        assert "tools/call" in impl

    def test_missing_socket_path_raises(
        self, workspace_technique: TechniqueDeclaration
    ) -> None:
        gen = InterfaceGenerator()
        with pytest.raises(ValueError, match="socket_path"):
            gen.generate_implementation([workspace_technique], socket_paths={})

    def test_implementation_proxies_to_mock_server(
        self,
        workspace_technique: TechniqueDeclaration,
        tmp_path: Path,
    ) -> None:
        """End-to-end: implementation invokes mock MCP server over Unix socket."""
        gen = InterfaceGenerator()
        sock = tmp_path / "mock.sock"

        async def run() -> tuple[object, dict]:
            server_got: dict = {}

            async def handle(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                data = await reader.readline()
                if not data:
                    writer.close()
                    return
                req = json.loads(data.decode("utf-8"))
                server_got["req"] = req
                resp = {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "result": {
                        "content": [{"type": "text", "text": "hello-world"}],
                    },
                }
                writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                await writer.drain()
                writer.close()

            server = await asyncio.start_unix_server(handle, path=str(sock))
            try:
                impl = gen.generate_implementation(
                    [workspace_technique],
                    socket_paths={"workspace": sock},
                )
                module = _load_module_from_source(impl, tmp_path, "gen_impl")
                cls = module.workspace  # type: ignore[attr-defined]
                instance = cls()
                result = await instance.read("hello.txt")
                return result, server_got
            finally:
                server.close()
                await server.wait_closed()

        result, got = asyncio.run(run())
        assert got["req"]["method"] == "tools/call"
        assert got["req"]["params"]["name"] == "read"
        assert got["req"]["params"]["arguments"] == {"path": "hello.txt"}
        assert result == "hello-world"


# =============================================================================
# Token count estimation
# =============================================================================


class TestTokenEstimation:
    """Estimated token counts stay within the token-efficiency target."""

    def test_estimate_tokens_returns_int(self) -> None:
        tokens = estimate_tokens("hello world")
        assert isinstance(tokens, int)
        assert tokens > 0

    def test_estimate_empty(self) -> None:
        assert estimate_tokens("") == 0

    def test_typical_config_under_500_tokens(
        self,
        workspace_technique: TechniqueDeclaration,
        github_technique: TechniqueDeclaration,
    ) -> None:
        """A realistic 4-technique config yields stubs under 500 tokens."""
        agents_tech = TechniqueDeclaration(
            name="agents",
            description="A2A — discover and delegate to other running agents.",
            tools=[
                MCPToolSpec(
                    name="who",
                    description="List running agents.",
                    parameters=[],
                    return_type="list[dict]",
                ),
                MCPToolSpec(
                    name="delegate",
                    description="Delegate a task to another agent.",
                    parameters=[
                        ("agent", "str", None),
                        ("task", "str", None),
                    ],
                    return_type="dict",
                ),
                MCPToolSpec(
                    name="inbox",
                    description="Read your A2A inbox.",
                    parameters=[],
                    return_type="list[dict]",
                ),
            ],
        )
        shared_tech = TechniqueDeclaration(
            name="shared",
            description="Shared coordination directories.",
            tools=[
                MCPToolSpec(
                    name="publish",
                    description="Publish a file to the shared workspace.",
                    parameters=[
                        ("directory", "str", None),
                        ("filename", "str", None),
                        ("content", "str", None),
                    ],
                    return_type="None",
                ),
                MCPToolSpec(
                    name="read_all",
                    description="Read all files in a shared directory.",
                    parameters=[("directory", "str", None)],
                    return_type="dict[str, str]",
                ),
            ],
        )
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs(
            [workspace_technique, github_technique, agents_tech, shared_tech]
        )
        tokens = estimate_tokens(stubs)
        assert tokens < 500, f"Stubs exceeded 500 token budget: {tokens} tokens"


# =============================================================================
# Round-trip — the stubs match the implementation's method signatures
# =============================================================================


class TestStubImplementationMatch:
    """Stubs and implementation share method names and classes."""

    def test_classes_match(
        self,
        workspace_technique: TechniqueDeclaration,
        tmp_path: Path,
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique])
        impl = gen.generate_implementation(
            [workspace_technique],
            socket_paths={"workspace": tmp_path / "workspace.sock"},
        )
        stub_tree = ast.parse(stubs)
        impl_tree = ast.parse(impl)

        stub_classes = {
            n.name for n in ast.walk(stub_tree) if isinstance(n, ast.ClassDef)
        }
        impl_classes = {
            n.name for n in ast.walk(impl_tree) if isinstance(n, ast.ClassDef)
        }
        assert stub_classes == impl_classes

    def test_method_names_match(
        self,
        workspace_technique: TechniqueDeclaration,
        tmp_path: Path,
    ) -> None:
        gen = InterfaceGenerator()
        stubs = gen.generate_stubs([workspace_technique])
        impl = gen.generate_implementation(
            [workspace_technique],
            socket_paths={"workspace": tmp_path / "workspace.sock"},
        )

        def collect_methods(src: str) -> set[str]:
            tree = ast.parse(src)
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        names.add(node.name)
            return names

        stub_methods = collect_methods(stubs)
        impl_methods = collect_methods(impl)
        assert stub_methods.issubset(impl_methods)
