# MCP Pool Integration Guide

## Overview

The shared MCP server pool is conductor-managed infrastructure that provides
MCP tool access to all musicians (agents) executing sheets. Instead of each
agent spawning its own MCP servers (which causes process explosion — see
F-271), the conductor runs one instance per MCP server type and shares them.

## Architecture

```
Conductor (DaemonManager)
  └── McpPoolManager
        ├── github-mcp-server (process) → /tmp/mzt/mcp/github.sock
        ├── filesystem-mcp-server (process) → /tmp/mzt/mcp/filesystem.sock
        └── symbols-python-server (process) → /tmp/mzt/mcp/symbols.sock
```

The pool manager handles:
- **Process lifecycle**: start, health check, restart on failure
- **Socket path tracking**: each server has a Unix socket endpoint
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
| `socket` | string | required | Unix socket path for agent access |
| `restart_policy` | string | "on-failure" | When to restart: on-failure, always, never |

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
4. For MCP-native instruments (claude-code), generates a config file pointing
   to the pool's socket paths

## Instrument Support

### MCP-Native Instruments

**Claude Code** is the only instrument with direct MCP config support:
- Uses `--mcp-config` flag with a JSON config file
- The conductor generates a temporary config file mapping pool server names
  to their Unix socket endpoints

### Non-MCP-Native Instruments

All other instruments (opencode, gemini-cli, goose, etc.) receive MCP
technique information as prompt text only. The technique manifest describes
available tools so the agent can reference them in its output. Tool execution
goes through the code mode / proxy bridge path (future implementation).

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

The MCP pool integrates with the conductor's lifecycle:

1. **Daemon start**: After baton initialization, start the pool if configured
2. **Sheet dispatch**: Technique resolver checks pool status, generates config
3. **Health monitoring**: Periodic health checks, restart failed servers
4. **Daemon shutdown**: Stop all pool servers before shutting down the baton

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
- Only instruments with `mcp_config_flag` in their profile support direct MCP
  config (currently only claude-code)
- Check instrument profile: `mcp_config_flag` must be set
- Verify `set_mcp_config()` was called with the correct path
