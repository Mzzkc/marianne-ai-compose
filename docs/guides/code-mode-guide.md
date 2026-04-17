# Code Mode Guide

## Overview

**Code mode** is the execution path that lets non-MCP-native instruments
(OpenRouter free-tier models, Ollama, etc.) use techniques. The agent
produces Python/bash code blocks instead of tool calls; the musician
classifies the output, extracts the blocks, and runs them through a
sandboxed executor. The sandbox's stdout is appended to the sheet's
output so validations and downstream sheets see both the agent's
reasoning and the real execution result.

This document covers the runtime side: how code mode activates, what
it does with agent output, how failures feed back into retries, and
how to configure it per score. The programmatic interface surface the
agent writes code against is documented separately in
[interface-reference.md](./interface-reference.md).

## When Code Mode Activates

Code mode runs when **three conditions** all hold:

1. **The job declares techniques.** `JobConfig.techniques` is non-empty,
   so the baton creates a `TechniqueRouter` and a `CodeModeExecutor`
   for the job.
2. **The backend execution succeeded.** A failed backend run is not
   classified — running the agent's "code" from a failed call would
   compound the original failure with garbage input.
3. **The classifier returns `CODE_BLOCK`.** The router's priority
   order is A2A → tool calls → code blocks → prose. Mixed outputs
   that contain A2A or tool calls route elsewhere; pure code blocks
   route to the executor.

If any condition fails, code mode stays dormant. Prose output, missing
techniques, or a failed backend all skip code mode with byte-identical
behavior to pre-technique sheets.

## Execution Flow

```
backend.execute() ──► ExecutionResult(stdout=agent_output)
                                │
                                ▼
                   TechniqueRouter.classify(stdout)
                                │
                   ┌────────────┼────────────┐
                 PROSE     CODE_BLOCK    TOOL_CALL / A2A
                   │            │              │
            no code run    extract code    log only
                   │            │           (Phase 3/5)
                   │            ▼
                   │    CodeModeExecutor
                   │    .execute_all(blocks)
                   │            │
                   │            ▼
                   │    ┌──────────────────┐
                   │    │ For each block:  │
                   │    │ 1. Write to      │
                   │    │    temp file     │
                   │    │ 2. Wrap in bwrap │
                   │    │    (if enabled)  │
                   │    │ 3. Spawn subproc │
                   │    │ 4. Capture I/O   │
                   │    └──────────────────┘
                   │            │
                   │            ▼
                   │    merge into stdout:
                   │    ## Sandbox Output
                   │    ### Block 1
                   │    [stdout + stderr]
                   │            │
                   └────────────┤
                                ▼
                       _validate()
                                │
                                ▼
                       SheetAttemptResult → baton
```

The executor runs each block sequentially. A block that fails does
**not** abort subsequent blocks — each is independent — but the
presence of a failure is surfaced in the merged output so validations
or the agent's next attempt can see it.

## Merging Sandbox Output into Sheet Output

The musician appends a `## Sandbox Output` section to
`exec_result.stdout` after code execution. Each executed block gets
a `### Block N` subheader. The merged stdout is then passed to:

- **Validations** (`content_contains`, `content_regex`) — so you can
  validate sandbox output just like CLI output.
- **`_capture_output()`** — which crops `stdout_tail` / `stderr_tail`
  to the last 10 KB and redacts secrets.
- **The final `SheetAttemptResult`** — where `output_kind` records the
  router's classification (`code_block`, etc.).

This keeps the agent's reasoning visible alongside its execution
result. A dashboard tailing `stdout_tail` sees both: what the agent
said it was doing, and what actually happened.

## Failure Handling

Code execution failures are **first-class information**, not silent
errors. Four distinct failure modes are captured:

| Mode | Trigger | Surface |
|------|---------|---------|
| `SUCCESS` | Exit code 0 | `stdout` rendered under `### Block N` |
| `FAILURE` | Non-zero exit | `## Code Execution Failed` section with exit code, stderr, the block source, and a retry hint |
| `TIMEOUT` | Wall-clock exceeded | Same failure section, error message calls out the timeout |
| `SANDBOX_ERROR` | bwrap setup/teardown failed | Same failure section, error message explains the sandbox error |

None of these abort the musician. The sheet completes normally with
the failure diagnostic embedded in the output. The baton's existing
retry policy decides whether to retry — from its perspective, this
is just a sheet whose validation may or may not pass.

### Executor Exceptions

If the executor itself raises (a bug, not a sandbox failure), the
musician catches the exception and appends an `## executor_error`
section with the traceback. This is the "soft fail" contract: a
classifier or executor bug degrades to "no code mode on this sheet"
rather than crashing the baton. A dedicated test
(`test_executor_exception_does_not_crash_musician`) verifies this
contract end-to-end.

### Retry Context

On retry, the agent's next prompt includes the failure diagnostic via
the existing `completion_prompt_suffix` / failure history path. The
agent sees exactly what it generated, what exit code it produced, and
what stderr it wrote — enough to adjust and try again. This mirrors
how backend execution failures feed retries today.

## Configuration

### Score-Level

```yaml
techniques:
  workspace:
    kind: mcp
    phases: [all]
  github:
    kind: mcp
    phases: [work]

sheet:
  # per-sheet instrument chosen from your instrument_fallbacks
  per_sheet_instruments:
    1: openrouter
    2: claude-code

backend:
  # Instruments that lack native tool use trigger code mode;
  # MCP-native instruments (claude-code, gemini-cli) do not
  # need code mode because they call MCP directly.
  name: openrouter
```

When the job registers, the baton creates one router and one executor
per job (not per sheet) and threads them into `_musician_wrapper` →
`sheet_task` for every dispatched sheet.

### Executor Defaults

`CodeModeExecutor` accepts:

- `workspace: Path` — the agent's workspace directory. All code runs
  with this as `cwd`. Files written by the code persist here.
- `timeout_seconds: float = 30.0` — wall-clock cap per block. 30s is
  generous for API-free code; I/O-heavy blocks can override.
- `use_sandbox: bool = True` — wrap each subprocess in `bwrap`. Set
  `False` only for tests where bwrap isn't available; production
  runs should always sandbox.
- `sandbox_config: SandboxConfig | None = None` — explicit bwrap
  configuration (bind mounts, env, resource limits). Defaults to a
  minimal profile that bind-mounts the workspace read-write.

### Language Support

The executor dispatches by the code fence language tag. Supported
interpreters:

| Tag | Interpreter |
|-----|-------------|
| `python` | `python3 <file>` |
| `bash`, `sh` | `bash <file>` |
| `javascript`, `node` | `node <file>` |
| `typescript`, `tsx` | `npx -q tsx <file>` |

Unsupported language tags (e.g. `rust`) produce a `FAILURE` result
with an explanatory error message rather than crashing.

## Backward Compatibility

Code mode is **opt-in per job** via the presence of the `techniques`
field. Three invariants protect existing scores:

1. **Empty or missing `techniques`** → no router, no executor. Sheet
   execution is byte-identical to the pre-technique pipeline.
2. **No router provided to `sheet_task`** → classification skipped,
   `output_kind` stays `None`. Any `code_executor` argument passed
   alongside is silently ignored (no router ⇒ no classification ⇒
   no code to execute).
3. **No executor provided** → router still classifies and records
   `output_kind` for observability, but no code runs. This is the
   "detection-only" mode used during Stage 2a rollout.

Each of these contracts has a dedicated test in
`tests/test_code_mode_wiring.py` to prevent regressions.

## Security Considerations

Code mode executes **LLM-generated code**. This is not optional risk
— the whole point is to let free-tier models drive execution — but
the bwrap sandbox mitigates it:

- **Filesystem isolation.** Only the workspace and explicit
  bind-mounts are visible. The host's home directory, SSH keys, and
  unrelated projects are not reachable.
- **Network isolation.** Default sandbox config unshares the network
  namespace. The code cannot phone home unless you explicitly enable
  a network path for it.
- **Resource limits.** Optional `ResourceLimits` cap memory and CPU
  time so runaway loops get killed before they harm the host.
- **No privilege escalation.** bwrap runs unprivileged. There is no
  path from agent code to root.

What bwrap does **not** protect against:

- **Workspace mutation.** The code can write anything into the
  workspace. If a downstream sheet reads that workspace, it inherits
  whatever the prior sheet produced. This is the intended contract
  — agents produce artifacts — but be mindful when designing scores
  where code output flows into later work.
- **API-key leakage via bind-mounts.** If you bind-mount a secret
  directory into the sandbox, the code can read it. Keep secrets in
  `$SECRETS_DIR/` and inject them through the MCP pool, not through
  workspace access.

The `CodeModeExecutor.__init__` logs a warning at start time if
`use_sandbox=False` is set in production, making it visible in
conductor logs if someone accidentally disables the sandbox.

## Observability

Every code mode run emits events through the baton's existing event
bus. Subscribers can observe:

- `sheet.output_classified` — fires when the router classifies output.
  Payload includes `output_kind` and extracted counts (`n_blocks`,
  `n_tool_calls`, `n_a2a_requests`).
- `sheet.code_executed` — fires after the executor returns. Payload
  includes per-block status and duration.

`mzt status <job>` reflects the classification in the sheet's status
line. `mzt diagnose <job>` includes the merged stdout (truncated)
so failures show both agent reasoning and sandbox output.

## Testing

The wiring contract has three test surfaces:

1. **Technique router** (`tests/test_technique_router.py`, 31 tests):
   classification correctness for prose, code, tool calls, A2A.
2. **Router wiring** (`tests/test_technique_router_wiring.py`, 13
   tests): router is threaded through adapter → musician, classifies
   output, records `output_kind`.
3. **Code mode wiring** (`tests/test_code_mode_wiring.py`, 12
   tests): executor is invoked when and only when both a router and
   executor are present; its output is merged into the sheet result;
   failures don't crash the musician; MCP sockets inside the
   workspace are reachable from sandbox code.

The sandbox itself has its own test suite in
`tests/test_sandbox_wrapper.py`. Code mode wiring tests disable
sandboxing (`use_sandbox=False`) for portability — a machine without
bwrap can still verify the wiring contract.

## Implementation Hook Points

For readers maintaining the musician:

| Where | File | Line region | What happens |
|-------|------|-------------|--------------|
| Router instantiation | `daemon/baton/adapter.py` | `register_job` | `TechniqueRouter` created per job with techniques |
| Executor instantiation | `daemon/baton/adapter.py` | `_dispatch_callback` | `CodeModeExecutor` created with sheet workspace |
| Router threading | `daemon/baton/adapter.py` | `_musician_wrapper` | router + executor passed to `sheet_task` kwargs |
| Classification | `daemon/baton/musician.py` | after `_execute` call | `_classify_output(router, result)` returns `OutputKind` |
| Code invocation | `daemon/baton/musician.py` | after classify | `_run_code_blocks(executor, blocks)` returns merged stdout |
| Stdout merging | `daemon/baton/musician.py` | before `_validate` | appended to `exec_result.stdout` in place |

See `workspaces/score2-completion/hook-point-map.md` for the full
dispatch-to-CLI execution trace with exact file/line references.

## Related Documents

- [Technique System Guide](./technique-guide.md) — skill/MCP/protocol
  kinds, config schema, phase filtering.
- [Interface Reference](./interface-reference.md) — programmatic
  interface stubs and runtime contract for agent-generated code.
- [MCP Pool Guide](./mcp-pool-guide.md) — shared MCP server pool
  wiring that code mode proxies through.
- [A2A Guide](./a2a-guide.md) — agent-to-agent protocol (Phase 5).
