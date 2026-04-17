# Programmatic Interface Reference

## Purpose

The **programmatic interface generator** produces typed Python stubs
that agents use when writing code-mode output. Instead of expanding
every MCP tool into a verbose tool definition (the traditional
approach, ~3000+ tokens for a realistic agent), the generator emits
a compact typed surface (~500 tokens) that describes what the agent
can call.

This pattern — proven in Cloudflare's Dynamic Workers — reduces
prompt token cost by ~81% while letting the agent chain multiple
operations in a single generation rather than making N sequential
tool-call round-trips.

The generator lives in `src/marianne/execution/interface_gen.py`.
It is invoked by the baton's dispatch path when a sheet has
technique declarations active for the current phase.

## Two Halves: Stubs and Runtime

The generator produces two artifacts from a single source:

### 1. Stubs (~500 tokens, prompt-injected)

Type signatures with docstrings, no bodies. The agent reads these
and writes code against them.

```python
class workspace:
    """File operations in your workspace."""
    def read(path: str) -> str: ...
    def write(path: str, content: str) -> None: ...
    def list(directory: str) -> list[str]: ...

class github:
    """GitHub operations via shared MCP pool."""
    def list_issues(state: str = "open") -> list[Issue]: ...
    def get_issue(number: int) -> Issue: ...
    def create_issue(title: str, body: str) -> Issue: ...

class agents:
    """A2A — discover and delegate to other running agents."""
    def who() -> list[AgentCard]: ...
    def delegate(agent: str, task: str) -> TaskHandle: ...
    def inbox() -> list[Task]: ...
```

Stubs are injected into the prompt as an `injected_skills` entry so
the agent sees them alongside its task description.

### 2. Runtime (executable module, written to sandbox workspace)

Real implementations that proxy through the shared MCP pool via JSON-RPC
over Unix sockets. Written to `<workspace>/techniques_rt.py` so agent
code can `import techniques_rt` and call into it.

```python
# techniques_rt.py — emitted by the generator
import asyncio
import json
import socket

async def _mcp_call(socket_path, method, params):
    reader, writer = await asyncio.open_unix_connection(socket_path)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": method, "arguments": params}}
    writer.write((json.dumps(request) + "\n").encode())
    await writer.drain()
    response = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(response)

class workspace:
    @staticmethod
    async def read(path: str) -> str:
        return await _mcp_call("/tmp/mzt/mcp/workspace.sock", "read", {"path": path})
    # ... other methods
```

Stub signatures and runtime signatures match exactly so agent code
runs unmodified against the runtime.

## Module Surface

```python
from marianne.execution.interface_gen import (
    InterfaceGenerator,
    MCPToolSpec,
    TechniqueDeclaration,
    estimate_tokens,
)
```

### `MCPToolSpec`

Describes a single MCP tool.

```python
@dataclass(frozen=True)
class MCPToolSpec:
    name: str                     # tool method name
    description: str              # one-line docstring
    parameters: list[tuple[
        str,                      # parameter name
        str,                      # Python type hint as string
        str | None,               # default expression, or None if required
    ]]
    return_type: str              # Python type hint for return value
```

### `TechniqueDeclaration`

Describes one class the agent sees. `name` doubles as the MCP
server identifier for socket lookup.

```python
@dataclass(frozen=True)
class TechniqueDeclaration:
    name: str                     # class name + MCP server key
    description: str              # class-level docstring
    tools: list[MCPToolSpec]      # methods
```

### `InterfaceGenerator`

The main entry point.

```python
class InterfaceGenerator:
    def generate_stubs(
        self, declarations: list[TechniqueDeclaration]
    ) -> str: ...

    def generate_implementation(
        self,
        declarations: list[TechniqueDeclaration],
        *,
        socket_paths: dict[str, str],
    ) -> str: ...
```

- `generate_stubs` returns a prompt-safe Python module source string.
  Every method is `def name(...) -> T: ...` — type hints plus ellipsis.
- `generate_implementation` returns a runtime module source string
  suitable for `<workspace>/techniques_rt.py`. Requires `socket_paths`
  mapping each technique name to its Unix socket path in the sandbox.

### `estimate_tokens`

Heuristic (`chars / 4`) for prompt budget checks. Used in tests to
guard against generator regressions that bloat the stub module.

```python
def estimate_tokens(text: str) -> int: ...
```

## Usage in Dispatch

The generator runs inside `BatonAdapter._dispatch_callback` after
techniques are resolved for the current sheet's phase:

```python
# Inside _dispatch_callback, after backend acquisition:
active_techniques = resolve_techniques_for_phase(job_id, sheet.movement)
if active_techniques:
    generator = InterfaceGenerator()
    declarations = build_declarations(active_techniques)
    stubs = generator.generate_stubs(declarations)
    sheet.cadenza.append(inline_skill_injection(stubs))

    if sheet_uses_code_mode(sheet):
        impl = generator.generate_implementation(
            declarations, socket_paths=mcp_pool.socket_paths()
        )
        (sheet.workspace / "techniques_rt.py").write_text(impl)
```

The stubs flow into the prompt via the existing injection pipeline.
The runtime module is materialized on disk so the sandbox can import
it.

## MCP-Native vs. Non-Native Instruments

The generator produces the same stubs for every instrument, but the
runtime module is only **used** by non-MCP-native instruments:

- **MCP-native** (claude-code, gemini-cli): read the stubs from the
  prompt as documentation of what they can call, then call MCP
  directly via their native tool-use support. They never import
  `techniques_rt`.
- **Non-native** (OpenRouter free-tier models, Ollama): write code
  against the stubs. Code mode imports `techniques_rt` and proxies
  through the shared MCP pool.

The stubs are identical either way, so an agent generating against
the same technique set produces the same code regardless of which
instrument will actually run it.

## JSON-RPC Round-Trip Contract

The runtime module's `_mcp_call` helper speaks minimal JSON-RPC 2.0
over a Unix socket. One line in, one line out, newline-delimited:

```
→ {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read","arguments":{"path":"x.md"}}}\n
← {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"..."}]}}\n
```

The shared MCP pool (see `mcp-pool-guide.md`) bridges these Unix
sockets to the actual MCP server processes. Errors propagate via the
standard JSON-RPC `error` field — the runtime module raises
`RuntimeError` on error responses so agent code can `try/except` it
naturally.

Only stdlib `asyncio` + `json` + `socket` are required. The sandbox
does **not** need Marianne installed; the emitted runtime is
self-contained.

## Token Budget

A realistic four-technique configuration
(workspace + github + agents + shared, 11 methods total) renders to
well under the 500-token target. This is validated in
`tests/test_interface_gen.py::TestTokenEstimation::test_typical_config_under_500_tokens`.

The token-budget discipline matters: every token in the stub module
is a token not spent on the agent's task. Verbose MCP tool
definitions would consume ~3000 tokens for the same capability
surface — a 6× cost multiplier. The generator's terseness is what
makes free-tier code mode viable.

## Error Handling at Generation Time

Two error classes surface at generation time rather than runtime:

1. **Missing socket paths.** If `generate_implementation` is called
   with a `socket_paths` dict missing an entry for a declared
   technique, it raises `ValueError` immediately. The composer sees
   a clean error at compile time; the sandbox never sees broken
   imports.
2. **Name collisions.** Two declarations with the same `name` raise
   `ValueError`. This protects against copy-paste bugs in technique
   YAML.

Runtime errors — network failures, MCP server crashes, tool-level
exceptions — propagate through the JSON-RPC error path into the
agent's code, where they become ordinary Python exceptions.

## Testing

The generator has 22 tests in `tests/test_interface_gen.py`:

- Stub generation from simple and complex declarations
- Implementation generation with JSON-RPC round-trip against a mock
  MCP server
- Missing-socket-path validation
- Duplicate-name validation
- Token budget for a realistic four-technique config
- Stub-vs-implementation signature parity (method names, parameter
  names, type hints all match)

The signature-parity test is the one that prevents "the agent wrote
code that looks right but doesn't run." If stubs and runtime drift,
the test catches it before merge.

## Relationship to Code Mode

Code mode is the **execution** layer; the interface generator is the
**contract** layer. The agent sees stubs, writes code against them,
and the sandbox executes the code against the runtime module. Without
the generator, code mode has nothing to run against; without code
mode, the generator's output is documentation only.

See [code-mode-guide.md](./code-mode-guide.md) for the execution side.

## Related Documents

- [Code Mode Guide](./code-mode-guide.md) — sandbox execution of
  agent-generated code.
- [Technique System Guide](./technique-guide.md) — technique
  declarations and kinds (skill/MCP/protocol).
- [MCP Pool Guide](./mcp-pool-guide.md) — shared MCP server pool the
  runtime module proxies through.
