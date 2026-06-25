# Technique System Guide

## What Are Techniques?

Techniques are composable components attached to agent entities in Marianne.
They follow an Entity Component System (ECS) pattern: each technique is
independently reusable across projects, agents, and scores. An agent's
capabilities are determined by which techniques are attached to it.

The metaphor comes from music: a technique is how you play the instrument.
Vibrato, staccato, pizzicato — these are ways of playing, not instruments
themselves. In Marianne, techniques are tools, communication protocols,
and methodologies that work with any instrument that supports them.

## Three Technique Kinds

### Skill

Text-based methodology injected as cadenza context. A skill tells the
musician how to approach work — memory management, mateship protocols,
coordination patterns, identity persistence, voice consistency.

Skills are injected into the prompt as skill-category content. The
musician reads them as instructions that shape how they work, not what
they know.

```yaml
techniques:
  memory-protocol:
    kind: skill
    phases: [consolidate, reflect, resurrect]
  mateship:
    kind: skill
    phases: [recon, work, inspect, aar]
  coordination:
    kind: skill
    phases: [recon, plan, integration]
```

### MCP (Model Context Protocol)

MCP techniques declare tool access that can be exposed to musicians through
technique manifests, native MCP config files where supported, and generated
code-mode bindings for other CLI instruments. They represent external
capabilities such as GitHub operations, filesystem access, and code symbol
analysis.

Stdio MCP servers declared in daemon config are shared through
`McpPoolManager` and `McpSocketBridge`. The bridge supports multiple socket
clients through request-id rewriting. Baton dispatch passes generated MCP config
files to compatible CLI backends and writes `<workspace>/techniques_rt.py` for
code-mode access through the same shared sockets.

```yaml
techniques:
  github:
    kind: mcp
    phases: [recon, work, integration]
    config:
      server: github
      transport: stdio
  filesystem:
    kind: mcp
    phases: [all]
    config:
      server: filesystem
  code-symbols:
    kind: mcp
    phases: [work, inspect]
    config:
      server: code-symbols
```

### Protocol

Communication protocols enabling inter-agent interaction. Currently,
A2A (Agent-to-Agent) is an optional live delegation protocol. Marianne can
register agent cards, route `@delegate target: task` output into a target
job's in-memory inbox, and inject pending inbox tasks into A2A-enabled sheets.
Durable coordination for shipped fleets is still handled by cadenza-backed
shared workspace artifacts; A2A does not replace that record.

A2A obeys technique phase resolution on both sides. A source sheet only has its
`@delegate` output parsed when the `a2a` protocol is active for that sheet.
A target job only checks and consumes its inbox when an A2A-enabled sheet is
dispatched. Use explicit A2A check sheets when review cadence matters; use
`phases: ["all"]` only when every sheet should check.

```yaml
techniques:
  a2a:
    kind: protocol
    phases: [recon, plan, work, integration, inspect, aar]
```

## Phase Filtering

Each technique declares which phases of the agent cycle it is available
in. At dispatch time, the conductor filters techniques to those active
in the current sheet's phase. This means:

- A technique declared for `[work, inspect]` is only available during
  work and inspect sheets
- A technique declared for `[all]` is available in every phase
- A technique with an empty phases list is declared but never active

Phase names correspond to the agent lifecycle:
- `recon` — Reconnaissance (gathering information)
- `plan` — Planning (deciding what to do)
- `work` — Implementation (doing the work)
- `integration` — Combining results from parallel work
- `play` — Creative/exploratory work
- `inspect` — Quality review
- `aar` — After-action review
- `consolidate` — Memory consolidation
- `reflect` — Developmental reflection
- `resurrect` — Identity persistence
- `all` — Wildcard, active in every phase

## Declaring Techniques in Score YAML

Techniques are declared in the `techniques` section of a score YAML file:

```yaml
name: agent-canyon
workspace: workspaces/canyon

techniques:
  github:
    kind: mcp
    phases: [recon, work, integration]
    config:
      server: github
  mateship:
    kind: skill
    phases: [recon, work, inspect, aar]
  a2a:
    kind: protocol
    phases: [recon, plan, work, integration, inspect, aar]

sheet:
  size: 1
  total_items: 12
prompt:
  template: |
    You are {{ agent_name }}, focused on {{ focus }}.
```

The `techniques` field is optional and defaults to an empty dict. Existing
scores without technique declarations continue to work unchanged.

## Technique Configuration

Each technique can carry kind-specific configuration in its `config` dict:

### MCP Config

```yaml
techniques:
  github:
    kind: mcp
    phases: [work]
    config:
      server: github        # Server name in the shared pool
      transport: stdio       # Transport protocol (stdio, sse, http)
```

### Skill Config

```yaml
techniques:
  memory-protocol:
    kind: skill
    phases: [consolidate]
    config:
      path: "~/.marianne/techniques/memory-protocol.md"  # Skill document path
```

### Protocol Config

```yaml
techniques:
  a2a:
    kind: protocol
    phases: [all]
    config:
      discover: true         # Auto-discover running agents
      delegate: true         # Allow task delegation
```

## Technique Manifest

At dispatch time, the conductor generates a technique manifest — a text
description of available capabilities for the current phase. This manifest
is injected into the musician's prompt as a skill-category item.

Example manifest for a work phase:

```markdown
## Techniques Available This Phase

### MCP Tools
- **github**: MCP server `github`

### Protocols
- **a2a**: Communication protocol

### Skills
- **mateship**: Methodology skill
```

The manifest tells the musician what capabilities they have without
requiring them to parse configuration. It appears in the prompt's skills
section, after the task description and before context injections.

## Compiler Integration

The composition compiler (`mzt compile`) reads technique declarations from
the agent config and emits runtime `techniques:` declarations plus per-phase
manifest text. The baton runtime then resolves the active techniques for the
sheet, injects skill documents once, and materializes MCP/A2A support for that
dispatch. The TechniqueWirer module generates:

1. **Technique manifests** per phase
2. **A2A agent cards** for protocol techniques
3. **Runtime technique declarations** with skill document paths for dispatch-time injection

Compiler-generated scores should not also add the same skill documents as
static sheet cadenzas; that duplicates the runtime technique injection path.

See the [Compile Reference](compile-reference.md) for compiler usage.

## Implementation Status

| Component | Status |
|-----------|--------|
| TechniqueConfig model | Complete |
| TechniqueKind enum (skill/mcp/protocol) | Complete |
| JobConfig.techniques field | Complete |
| Phase filtering | Complete |
| Manifest generation | Complete |
| Compiler TechniqueWirer | Complete |
| BatonAdapter technique resolution | Complete |
| PromptRenderer technique injection | Complete |
| Shared MCP pool | Complete for stdio native config and code-mode bridge |
| Programmatic MCP interface | Complete |
| Code mode execution | Complete; auto-wired for active MCP bridge sheets |
| A2A protocol | Internal live delegation support; not durable coordination |

## Testing

Technique functionality is covered by:

- `tests/test_technique_config.py` — TechniqueConfig model and JobConfig integration
- `tests/test_technique_resolution.py` — Phase filtering and manifest generation
- `tests/test_a2a_protocol.py` and `tests/test_a2a_wiring.py` — current A2A model and wiring behavior
- `tests/test_mcp_proxy_subprocess.py` — live MCPProxyService subprocess JSON-RPC behavior
- `tests/test_interface_gen.py` — generated MCP stubs/runtime JSON-RPC behavior
- `tests/test_mcp_conductor_dispatch.py` — baton dispatch MCP config injection
  and all built-in profile code-mode bridge coverage
- `tests/test_mcp_config_injection.py`, `tests/test_mcp_pool_wiring.py`, and
  `tests/test_mcp_pool_manager.py` — MCP config, lifecycle, and live socket multiplexing
- `tests/test_technique_router.py` — Output classification (prose, code, tool calls, A2A)
- `compiler/tests/test_compose_techniques.py` — Compiler TechniqueWirer

Run technique tests:

```bash
python -m pytest \
  tests/test_technique_config.py \
  tests/test_technique_resolution.py \
  tests/test_a2a_protocol.py \
  tests/test_a2a_wiring.py \
  tests/test_mcp_conductor_dispatch.py \
  tests/test_mcp_proxy_subprocess.py \
  tests/test_mcp_config_injection.py \
  tests/test_mcp_pool_wiring.py \
  tests/test_mcp_pool_manager.py \
  compiler/tests/test_compose_techniques.py \
  -v
```
