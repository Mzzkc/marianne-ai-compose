# MCP Pool Integration Guide

Status: conductor-integrated for stdio MCP servers across built-in CLI
instruments. `McpPoolManager` starts configured stdio MCP servers, exposes each
through a multiplexed Unix socket bridge, rewrites colliding client request IDs,
and generates MCP config files for sheet dispatch. `JobManager` owns pool
lifecycle. `BatonAdapter` now exposes active MCP techniques through two paths:
native `--mcp-config` injection when an instrument supports it, and generated
`techniques_rt.py` code-mode bindings for every CLI instrument.

## Overview

The shared MCP server pool is conductor-managed infrastructure for MCP tool
access across musicians. Instead of each agent spawning its own MCP servers
(which causes process growth, see F-271), the conductor runs one instance per
MCP server type and shares it through a local socket bridge.

Stdio MCP servers speak a single JSON-RPC session over stdin/stdout. Marianne's
bridge accepts multiple Unix socket clients, answers each client's initialize
handshake from the upstream server's cached initialize result, rewrites request
IDs to unique upstream IDs, and restores each client's original ID on response.

## Architecture

```
Conductor (DaemonManager)
  └── McpPoolManager
        ├── github-mcp-server (stdio process)
        │     └── McpSocketBridge → /tmp/mzt/mcp/github.sock
        ├── filesystem-mcp-server (stdio process)
        │     └── McpSocketBridge → /tmp/mzt/mcp/filesystem.sock
        └── symbols-python-server (stdio process)
              └── McpSocketBridge → /tmp/mzt/mcp/symbols.sock
```

The pool manager handles:
- **Process lifecycle**: start, health check, restart on failure
- **Socket bridge lifecycle**: bind/unbind per-server Unix sockets
- **Client multiplexing**: request-id rewrite/restore for concurrent clients
- **Config generation**: per-workspace MCP config files for MCP-native CLIs
- **Code bindings**: per-workspace `techniques_rt.py` runtime for code mode
- **Graceful shutdown**: SIGTERM with 10s timeout, then SIGKILL

## Configuration

MCP pool configuration lives in the daemon config (not in score YAML):

```yaml
# ~/.marianne/daemon.yaml
mcp_pool:
  servers:
    github:
      command: "github-mcp-server"
      transport: stdio
      socket: "/tmp/mzt/mcp/github.sock"
      restart_policy: on-failure
    filesystem:
      command: "fs-mcp-server"
      transport: stdio
      socket: "/tmp/mzt/mcp/filesystem.sock"
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | string | required | Command to start the MCP server |
| `transport` | string | "stdio" | Transport protocol (stdio, sse, http) |
| `socket` | string | required | Unix socket path exposed by the bridge for stdio servers |
| `restart_policy` | string | "on-failure" | When to restart: on-failure, always, never |
| `framing` | string | "newline" | Upstream stdio framing: `newline` or `content-length` |

## Score YAML Integration

Scores declare MCP techniques that reference pool servers:

```yaml
techniques:
  github:
    kind: mcp
    phases: [recon, work, integration]
    config:
      server: github
  filesystem:
    kind: mcp
    phases: [all]
    config:
      server: filesystem
```

At dispatch time, the baton's technique resolver:
1. Filters techniques to those active in the current sheet's phase
2. Generates a technique manifest (text describing available tools)
3. Injects the manifest into the musician's prompt as a skill-category item
4. Generates a workspace-local MCP config file for active running pool servers
5. Calls `backend.set_mcp_config(path)` for MCP-native CLI backends
6. Generates prompt stubs and `<workspace>/techniques_rt.py` for code-mode
   access to the same active servers
7. Clears the backend MCP config after the sheet releases the backend

## Instrument Support

### MCP-Native Instruments

Native support means the profile has either `mcp_config_flag` or
`mcp_config_workspace_path`:
- `mcp_config_flag`: use the profile's MCP config flag with a JSON config file
- `mcp_config_prefix_args`: add least-privilege flags before the active config
  flag, e.g. Claude Code's `--strict-mcp-config`
- `mcp_config_workspace_path`: temporarily copy the conductor-generated config
  to a workspace-relative file discovered by the CLI, then restore/delete it
  after execution
- `mcp_config_workspace_merge_key`: for broader JSON settings files, merge only
  the generated MCP server object under that key and restore the previous file
  after execution
- `PluginCliBackend.set_mcp_config(path)` causes the supported path to be used
- The baton adapter calls `set_mcp_config()` when active MCP techniques resolve

Built-in direct support as of 2026-06-21:
- `claude-code`: `--strict-mcp-config --mcp-config <file>`
- `antigravity`: workspace `.agents/mcp_config.json`; live smoke proved
  Antigravity CLI 1.0.10 initializes servers from that file. Keep actual tool
  invocation claims tied to live smokes for the target server because the CLI
  does not expose a deterministic MCP-list command.
- `gemini-cli`: workspace `.gemini/settings.json`, merged under `mcpServers`.
  Local Gemini CLI 0.46.0 proved the project config shape with `gemini mcp add`;
  live dispatch on this machine is currently blocked before MCP startup by
  Google's `UNSUPPORTED_CLIENT` / `IneligibleTierError` for the individual tier.
  Prefer `antigravity` for current Google CLI live runs unless Gemini API/gcloud
  auth is configured.

### Non-MCP-Native Instruments

All CLI instruments also receive a generated programmatic interface:

```python
from techniques_rt import filesystem

tools = filesystem.list_tools()
result = filesystem.call("read_file", {"path": "README.md"})
```

At dispatch, Marianne writes the implementation to
`<workspace>/techniques_rt.py`. When the musician returns a Python code block,
code mode runs it with the workspace as CWD, imports that runtime, and proxies
JSON-RPC calls through the shared MCP pool socket. This is the spec-aligned
fallback for instruments without direct MCP configuration and is covered for all
built-in profiles by `tests/test_mcp_conductor_dispatch.py`.

## API Reference

### McpPoolManager

```python
from marianne.daemon.mcp_pool import McpPoolManager
from marianne.daemon.config import McpPoolConfig

# Create from config
manager = McpPoolManager(config.mcp_pool)

# Lifecycle
await manager.start_all()       # Start all configured servers
await manager.stop_all()        # Gracefully terminate all servers

# Status
manager.server_names()          # -> ["github", "filesystem"]
manager.is_running("github")    # -> True
manager.get_status()            # -> {"github": RUNNING, "filesystem": STOPPED}

# Socket paths
manager.get_socket_path("github")     # -> Path("/tmp/mzt/mcp/github.sock")
manager.get_all_socket_paths()        # -> {"github": Path(...), ...}

# Health
await manager.health_check("github")  # -> True if process alive
```

### MCPProxyService

```python
from marianne.bridge.mcp_proxy import MCPProxyService

# Create and start (manages its own server processes)
async with MCPProxyService(servers=[config]) as proxy:
    tools = await proxy.list_tools()
    result = await proxy.execute_tool("read_file", {"path": "/tmp/test"})
```

## Lifecycle Integration

The MCP pool integrates with the conductor lifecycle:

1. **Daemon start**: `JobManager.start()` creates and starts `McpPoolManager`
   when daemon config declares servers.
2. **Pool start**: each stdio server is initialized once and exposed through
   `McpSocketBridge`.
3. **Sheet dispatch**: resolved MCP techniques select running pool servers,
   generate native MCP config when possible, and write code-mode bindings for
   every CLI instrument.
4. **Daemon shutdown**: `JobManager.shutdown()` stops the pool after baton
   shutdown.

## Troubleshooting

### Server won't start
- Check the `command` is installed and on PATH
- Verify socket parent directory permissions
- Check daemon logs: `mcp_pool.server_start_failed`

### Server dies during execution
- The pool tracks server state via `health_check()`
- `restart_policy: on-failure` will restart crashed servers
- Agent sheets will see technique unavailability in their next dispatch

### MCP config not applied to instrument
- Only instruments with `mcp_config_flag` or `mcp_config_workspace_path` in
  their profile support direct MCP config
- Check instrument profile: one of those fields must be set
- Verify `set_mcp_config()` was called with the correct path
- Instruments without a direct MCP config path should still get
  `<workspace>/techniques_rt.py`; check that the MCP technique resolved for the
  current sheet phase and that the pool server is running

### Shared pool socket unavailable
- Check that daemon config declares the server under `mcp_pool.servers`.
- Check the MCP server completed initialize; bridge startup fails if the stdio
  server never answers the initialize request.
- Check daemon logs for `mcp_pool.server_start_failed` or `mcp_bridge.*`.
