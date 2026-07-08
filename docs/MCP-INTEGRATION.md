# Marianne MCP Integration Guide

This guide explains how to integrate Marianne AI Compose with a Model Context
Protocol (MCP) client such as Claude Desktop.

MCP uses machine identifiers such as `job_id` because it talks to the daemon
API. In composer-facing terms, that ID is the runtime handle for a submitted
score, and the conductor remains the execution authority.

## Overview

Marianne's MCP server exposes these current capabilities:

- Submitted-score lifecycle: list, inspect, and submit scores through the
  conductor-facing service layer.
- Conductor controls: pause, resume, and cancel submitted scores.
- Workspace access: list files, read files, inspect artifacts, and read bounded
  log tails.
- Score references: read the current score schema, score example, instrument
  options, validation types, learning options, score templates, and conductor
  records.

The advertised tool list does not include `validate_score` or `generate_score`.
Those names remain hidden compatibility stubs in code until quality-score
integration is implemented, so MCP clients should not build workflows around
them.

## Claude Desktop Configuration

### Prerequisites

Confirm Marianne is installed in the Python environment that will run the MCP
server:

```bash
mzt --version
mzt --help
```

Validate any score before submitting it through MCP:

```bash
mzt validate /path/to/score.yaml
```

### Configure Claude Desktop

Add the Marianne MCP server to your Claude Desktop configuration file.

Configuration locations:

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Example:

```json
{
  "mcpServers": {
    "marianne": {
      "command": "python",
      "args": ["-m", "marianne.mcp.server"],
      "env": {
        "MZT_WORKSPACE_ROOT": "/path/to/workspaces"
      }
    }
  }
}
```

Environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `MZT_WORKSPACE_ROOT` | Root directory for workspace file operations | Current working directory |
| `MZT_LOG_LEVEL` | MCP server logging level | `INFO` |

## Available Tools

### Submitted-Score Tools

#### `list_jobs`

List conductor records for submitted Marianne scores.

Parameters:

- `status_filter` optional: `running`, `paused`, `completed`, `failed`, or
  `cancelled`.
- `limit` optional: maximum records to return. Default `50`, maximum `500`.

#### `get_job`

Read detailed conductor state for one submitted score.

Parameters:

- `job_id` required: conductor runtime identifier for the submitted score.

#### `start_job`

Submit a Marianne score YAML file.

Parameters:

- `config_path` required: path to the score YAML file.
- `workspace` optional: workspace directory for score execution.
- `client_cwd` optional: client working directory for resolving relative score
  paths.
- `start_sheet` optional: first sheet number, default `1`.
- `self_healing` optional: enable self-healing mode.
- `self_healing_auto_confirm` optional: auto-confirm self-healing fixes.
- `escalation` optional: pause for composer decision on exhaustion.
- `dry_run` optional: validate without executing when supported.
- `runtime_variables` optional: per-run variables for score templates.
- `fresh` optional: clear existing score state before submitting.
- `confirm_fresh` required when `fresh` is true. Fresh submissions are
  destructive because they clear existing score state.

### Conductor Control Tools

#### `pause_job`

Ask the conductor to pause a submitted score at the next sheet boundary.

Parameters:

- `job_id` required.

#### `resume_job`

Ask the conductor to resume a paused score.

Parameters:

- `job_id` required.

#### `cancel_job`

Ask the conductor to cancel a submitted score.

Parameters:

- `job_id` required.

### Artifact Tools

#### `marianne_artifact_list`

List files in a workspace.

Parameters:

- `workspace` required.
- `path` optional: subdirectory inside the workspace.
- `include_hidden` optional: include dotfiles and hidden directories.

#### `marianne_artifact_read`

Read a file inside a workspace.

Parameters:

- `workspace` required.
- `file_path` required.
- `max_size` optional: default `50000`, maximum `100000`.
- `encoding` optional: default `utf-8`.

#### `marianne_artifact_get_logs`

Read bounded log tails for a submitted score.

Parameters:

- `job_id` required.
- `workspace` optional: auto-detected when possible.
- `lines` optional: default `100`, maximum `10000`.
- `level` optional: `debug`, `info`, `warning`, `error`, or `all`.

The log result names its state explicitly:

- `unavailable`: the workspace was not found.
- `no-sources`: no log source was found.
- `no-lines`: a log source exists and is empty.
- `available`: bounded log lines were read.
- `stream-only`: source logs are large; MCP returns bounded tails only.

#### `marianne_artifact_list_artifacts`

List artifacts created in a submitted score workspace.

Parameters:

- `job_id` required.
- `workspace` optional.
- `sheet_filter` optional.
- `artifact_type` optional: `output`, `error`, `log`, `state`, or `all`.

#### `marianne_artifact_get_artifact`

Read one artifact from a submitted score workspace.

Parameters:

- `job_id` required.
- `artifact_path` required.
- `workspace` optional.
- `max_size` optional: default `100000`, maximum `1000000`.

## Available Resources

### Score References

- `config://schema`: JSON schema for current score YAML.
- `config://example`: current score YAML example using `instrument`.
- `config://instrument-options`: instrument profiles and configuration options.
- `config://backend-options`: compatibility alias for
  `config://instrument-options`; new score YAML should use `instrument` and
  `instrument_config`.
- `config://validation-types`: current validation types.
- `config://learning-options`: learning configuration reference.

### Conductor Records

- `marianne://jobs`: overview of submitted scores.
- `marianne://jobs/{job_id}`: detailed conductor record for one submitted score
  when a state backend is available.
- `marianne://templates`: current score templates.

## Usage Examples

In Claude Desktop you normally ask in natural language. The snippets below show
the MCP call shape a client sends internally.

### Submit A Score

```javascript
await call_tool("start_job", {
  config_path: "/workspaces/my-project/scores/review.yaml",
  workspace: "/workspaces/my-project/workspace",
  client_cwd: "/workspaces/my-project",
  runtime_variables: {
    run_label: "review-2026-07-08"
  }
});
```

### Submit With Fresh State

```javascript
await call_tool("start_job", {
  config_path: "/workspaces/my-project/scores/review.yaml",
  workspace: "/workspaces/my-project/workspace",
  fresh: true,
  confirm_fresh: true
});
```

### Monitor A Submitted Score

```javascript
const status = await call_tool("get_job", {
  job_id: "review-2026-07-08"
});

const logs = await call_tool("marianne_artifact_get_logs", {
  job_id: "review-2026-07-08",
  lines: 50,
  level: "error"
});
```

### Read Artifacts

```javascript
const artifacts = await call_tool("marianne_artifact_list_artifacts", {
  job_id: "review-2026-07-08",
  workspace: "/workspaces/my-project/workspace"
});

const report = await call_tool("marianne_artifact_get_artifact", {
  job_id: "review-2026-07-08",
  workspace: "/workspaces/my-project/workspace",
  artifact_path: "analysis-summary.md"
});
```

### Read Score References

```javascript
const schema = await read_resource("config://schema");
const instruments = await read_resource("config://instrument-options");
const validationTypes = await read_resource("config://validation-types");
const templates = await read_resource("marianne://templates");
```

## Security And Operational Boundaries

All MCP tools require client-side user consent because they can read workspace
files or send conductor control requests.

File operations are restricted to the configured workspace root. Paths are
validated to prevent directory traversal and workspace escape.

Sensitive configuration values are masked where Marianne surfaces them. Log and
artifact reads are size-bounded.

MCP clients should submit through the conductor-facing `start_job` tool and then
poll conductor records. A short-lived CLI process is not proof that score
execution has finished.

Do not restart or stop the conductor from an embedded MCP workflow while scores
are active. Use pause, resume, or cancel for score-level control.

## Troubleshooting

### MCP Server Not Starting

```bash
mzt --version
python -m marianne.mcp.server --help
```

### Permission Errors

Confirm `MZT_WORKSPACE_ROOT` points at a directory the MCP server can read:

```bash
mkdir -p /path/to/workspaces
ls -la /path/to/workspaces
```

### Submit Failures

Validate the score and check the conductor:

```bash
mzt validate /path/to/score.yaml
mzt status --json
```

If `fresh` is true, include `confirm_fresh: true` only after the composer has
confirmed that existing score state may be cleared.

## Related Commands

- `mzt mcp`: start the MCP server.
- `mzt start`: start the conductor through the CLI.
- `mzt status`: inspect conductor and submitted-score state.
- `mzt logs <job_id>`: inspect CLI log output for a submitted score.

See [CLI Reference](cli-reference.md) for command details.
