# Marianne CLI Reference

Complete reference for all Marianne CLI commands and options. Marianne provides a single entry point:

- **`mzt`** — Score orchestration CLI (run, monitor, diagnose, learn, conductor management)

---

## Global Options

These options apply to all `mzt` commands:

| Option | Short | Description | Env Var |
|--------|-------|-------------|---------|
| `--version` | `-V` | Show version and exit | |
| `--verbose` | `-v` | Show detailed output with additional information | |
| `--quiet` | `-q` | Show minimal output (errors only) | |
| `--conductor-clone` | | Route all daemon interactions to a clone conductor (see below) | |
| `--log-level` | `-L` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `MZT_LOG_LEVEL` |
| `--log-file` | | Path for log file output | `MZT_LOG_FILE` |
| `--log-format` | | Log format: `json`, `console`, or `both` | `MZT_LOG_FORMAT` |
| `--help` | | Show help message | |

### Conductor Clones

The `--conductor-clone` option starts or connects to a **clone conductor** — an
isolated conductor instance with its own socket, PID file, state database, and
log. This lets you test scores, CLI features, and conductor behavior without
risking your production conductor.

```bash
# Start a default clone conductor
mzt --conductor-clone start

# Submit a score to the clone
mzt --conductor-clone run my-score.yaml

# Check clone status
mzt --conductor-clone status

# Named clones for parallel testing
mzt --conductor-clone=staging start
mzt --conductor-clone=staging run staging-test.yaml
mzt --conductor-clone=staging conductor-status

# Stop the clone when done
mzt --conductor-clone stop
```

**Key behaviors:**
- The clone inherits your production `~/.marianne/conductor.yaml` config unless overridden.
- Clone paths: `~/.config/mzt/clone.sock` (socket), `~/.config/mzt/clone.pid` (PID file).
- Named clones use the name in the path: `~/.config/mzt/clone-staging.sock`.
- Clone names are sanitized (64 character limit, safe characters only).
- Commands that don't interact with the conductor (`validate`, `--help`) ignore this flag.

---

## Core Commands

### `mzt run`

Run a score from a YAML configuration file.

```
Usage: mzt run [OPTIONS] CONFIG_FILE
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `CONFIG_FILE` | Yes | Path to YAML score configuration file |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--dry-run` | `-n` | false | Show what would be executed without running. The preview renders with any `--var` overrides merged, exactly as the conductor would run the score |
| `--start-sheet` | `-s` | | Override starting sheet number |
| `--json` | `-j` | false | Output result as JSON for machine parsing |
| `--escalation` | `-e` | false | Pause sheets in FERMATA on retry exhaustion for a composer decision; resolve with `mzt resolve`. Works without `--self-healing` |
| `--self-healing` | `-H` | false | Enable automatic diagnosis and remediation when retries are exhausted |
| `--yes` | `-y` | false | Auto-confirm suggested fixes when using `--self-healing` |
| `--require-lock` | | _(none)_ | Admission gate (GH #403): path to the composition lock the composing skill's release gate wrote (`check_score_release.py --write-lock`). The conductor verifies the lock's `score_sha256` against the exact bytes it is about to parse; a missing, stale, unreadable or malformed lock rejects the job with that reason. Omit for ordinary unlocked submission. |
| `--fresh` | | false | Re-run a score from scratch: re-register all sheets and, when the score sets `workspace_lifecycle.archive_on_fresh`, archive non-preserved workspace files first. Auto-triggered for completed scores when the YAML changed. |
| `--var` | | _(none)_ | Per-invocation template variable as `key=value` (repeatable). Merges into the score's `prompt.variables`, CLI overriding YAML on collision — point one generic score at different inputs without editing it. Values are strings (typed vars belong in YAML). Persisted across resume and inherited by every job a concert trigger chains from this one. |

#### Auto-Fresh Detection

When you re-run a completed score after editing the YAML file, Marianne
automatically detects the change and starts fresh — no `--fresh` flag needed.
The conductor compares the score file's modification time against the
previous run's completion time. If the score is newer, it starts a fresh
run automatically.

This only applies to **completed** scores. Failed or paused scores are
always resumed from their checkpoint, regardless of file changes.

#### Backpressure and Rate Limits

The conductor rejects new score submissions only when the system is under
**resource pressure** (high memory usage or too many child processes). Rate
limits do not cause job rejection — they are per-instrument concerns handled
at the sheet dispatch level by the baton. A rate limit on one instrument
does not block scores targeting different instruments.

If the system is under resource pressure, `mzt run` exits with an error:

```
Error: System under high pressure — try again later.
```

#### Recurring-score lifecycle

For a score with `schedule:`, `mzt run score.yaml` is both trigger-now and
schedule registration/replacement. Use the existing lifecycle commands against
the stable schedule/score ID: `mzt status` and `mzt list` show next/last due
and outcome; `mzt pause ID` stops future ticks (and active lineage), `mzt
resume ID` re-arms one durable next due, and `mzt cancel ID` removes the
schedule. After a conductor restart, the registry restores one future timer or
one `latest` catch-up; it never replays every missed interval.

#### Examples

```bash
# Basic run (requires running conductor: mzt start)
mzt run job.yaml

# Dry run to preview (works without conductor)
mzt run job.yaml --dry-run

# Start from sheet 3
mzt run job.yaml --start-sheet 3

# With self-healing enabled
mzt run job.yaml --self-healing --yes

# Fresh start (ignores existing state)
mzt run job.yaml --fresh
```

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success or graceful shutdown |
| 1 | Configuration error or score failure |

---

### `mzt resume`

Resume a paused or failed score.

```
Usage: mzt resume [OPTIONS] JOB_ID
```

Loads the score state and continues execution from where it left off. By default, Marianne auto-reloads the config from the original YAML file if it still exists on disk. Falls back to the cached `config_snapshot` when the file is gone. Use `--no-reload` to force using the cached snapshot: the score exactly as it was admitted (or as the last reloading resume ran it), so edits made to the YAML on disk since then are ignored. The snapshot is written at submit and refreshed on every reloading resume.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score ID to resume |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to config file (optional if `config_snapshot` exists in state) |
| `--force` | `-f` | false | Force resume even if score appears completed |
| `--escalation` | `-e` | false | Pause sheets in FERMATA on retry exhaustion for a composer decision; resolve with `mzt resolve`. Works without `--self-healing` |
| `--no-reload` | | false | Use cached config snapshot instead of auto-reloading from YAML file |
| `--from-sheet` | | | Reset every sheet >= N to pending and re-run from there, including completed or skipped sheets |
| `--self-healing` | `-H` | false | Enable automatic diagnosis and remediation when retries are exhausted |
| `--yes` | `-y` | false | Auto-confirm suggested fixes when using `--self-healing` |

#### Examples

```bash
# Resume paused score
mzt resume my-job

# Resume with explicit config
mzt resume my-job --config job.yaml

# Resume with explicit config file (overrides auto-reload)
mzt resume my-job --config updated.yaml

# Resume using cached snapshot (skip auto-reload)
mzt resume my-job --no-reload

# Force resume a completed score
mzt resume my-job --force

# Re-run from sheet 5 onward
mzt resume my-job --from-sheet 5
```

#### Resumable States

| Status | Resumable | Notes |
|--------|-----------|-------|
| `paused` | Yes | Continues from last sheet |
| `failed` | Yes | Retries from failed sheet |
| `running` | Yes | Continues from last sheet |
| `completed` | With `--force` | Re-runs the completed score |
| `pending` | No | Use `run` instead |

---

### `mzt pause`

Pause a running Marianne score gracefully.

```
Usage: mzt pause [OPTIONS] JOB_ID
```

Creates a pause signal that the score detects at the next sheet boundary. The score saves its state and can be resumed with `mzt resume`.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score ID to pause |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--wait` | | false | Wait for the score to acknowledge the pause signal |
| `--timeout` | `-t` | 60 | Timeout in seconds when using `--wait` |
| `--json` | `-j` | false | Output result as JSON |
| `--force` | `-f` | false | Force-cancel the score immediately without waiting for a sheet boundary |

#### Examples

```bash
# Pause a running score
mzt pause my-job

# Pause and wait for acknowledgment
mzt pause my-job --wait --timeout 30

# Cancel immediately without waiting for a sheet boundary
mzt pause my-job --force
```

---

### `mzt resolve`

Resolve a sheet paused in FERMATA awaiting a composer decision (#361).

```
Usage: mzt resolve [OPTIONS] JOB_ID SHEET_NUM DECISION
```

When escalation is enabled (`--escalation` or `--self-healing`), a sheet that
exhausts its retries pauses in FERMATA instead of failing. `mzt status` lists
pending decisions; this command records yours and the score continues
immediately. (Equivalent to creating a marker file
`markers/fermata/<job_id>/sheet-<N>.<decision>` under the job workspace.)

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score ID with a sheet paused in FERMATA |
| `SHEET_NUM` | Yes | Sheet number awaiting the decision |
| `DECISION` | Yes | `retry` (re-run from scratch), `skip` (skip, continue dependents), `accept` (accept the last attempt as success), `fail` (fail it, propagate to dependents) |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Re-run a FERMATA'd sheet from scratch
mzt resolve my-score 3 retry

# Accept the sheet's last attempt as success
mzt resolve my-score 3 accept
```

---

### `mzt modify`

Modify a score's configuration and optionally resume execution.

```
Usage: mzt modify [OPTIONS] JOB_ID
```

Convenience command that combines pause + config validation. If the score is running, it will be paused first. Use `--resume` to immediately resume with the new configuration.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score ID to modify |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | **required** | New configuration file |
| `--resume` | `-r` | false | Immediately resume with new config after pausing |
| `--wait` | | false | Wait for job to pause before resuming (when `--resume`) |
| `--timeout` | `-t` | 60 | Timeout in seconds for pause acknowledgment |
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Modify config (pauses job if running)
mzt modify my-job --config updated.yaml

# Modify and immediately resume
mzt modify my-job -c new-config.yaml --resume

# Modify, wait for pause, then resume
mzt modify my-job -c updated.yaml --resume --wait
```

---

### `mzt status`

Show score status. With no arguments, shows an overview of all active scores. With a score ID, shows detailed status for that specific score.

```
Usage: mzt status [OPTIONS] [SCORE_ID]
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `SCORE_ID` | No | Score ID to check status for. Omit to see all active scores. |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output as JSON for machine parsing |
| `--watch` | `-W` | false | Continuously monitor status with live updates |
| `--interval` | `-i` | 5 | Refresh interval in seconds for `--watch` mode |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor and read job state from filesystem |

> **Note:** `mzt status` routes through the conductor by default. The `--workspace` flag is a hidden debug override for direct filesystem access when the conductor is unavailable.

#### Overview Mode (No Arguments)

When called without a score ID, shows a conductor overview:

- **Conductor status** — whether the conductor is running and its uptime
- **Active scores** — all running, queued, and paused scores with elapsed time
- **Recent scores** — the 5 most recently completed or failed scores

This is the natural first command after `mzt run` — like `git status` showing your working tree.

```bash
# Show overview of all scores
mzt status

# Overview as JSON
mzt status --json
```

#### Per-Score Mode

When given a score ID, shows detailed status for that score:

- Score name, status, and timing
- Progress bar with sheet counts
- Per-sheet details with validation results
- Cost tracking with confidence indicators (see below)
- Error summaries with `mzt diagnose` suggestion on failure

For large scores (50+ sheets), a compact summary is shown instead of the full sheet table: counts by status, then only interesting sheets (running, failed, validation-failed) capped at 20 entries. Small scores retain the full detail table.

```bash
# Detailed status for a specific score
mzt status my-score

# JSON output
mzt status my-score --json

# Continuous monitoring
mzt status my-score --watch

# Watch with custom interval
mzt status my-score --watch --interval 10
```

#### Output

Standard per-score output includes:
- Score name and ID
- Status (running, completed, failed, paused, pending)
- Progress bar with sheet counts
- Timing information
- Error messages (if any)
- Sheet details table (or compact summary for 50+ sheets)
- **Instrument column** — when any sheet has an assigned instrument name, the table includes an Instrument column showing which instrument each sheet uses. For large scores (50+ sheets), the summary view shows an instrument breakdown with counts. A sheet that fell to a fallback shows `codex-cli (was claude-code: execution_timeout after 30m)`: the instrument that played, the one that fell, why, and how long the fallen one held the sheet (GH #434). The duration beside the status is the winning attempt's; the time the fallen instrument spent is in that note and in `mzt diagnose`.
- **Cost summary** — always shown, including total cost, token counts, and cost limit status. When an instrument returns structured token data (JSON output), costs are precise. When tokens are estimated from output character count, the display shows `~$X.XX (est.)` with a warning that actual costs may be 10-100x higher. Use JSON output format on your instrument for accurate cost tracking.
- Suggestion to run `mzt diagnose` on failure

JSON output structure:
```json
{
  "job_id": "my-score",
  "job_name": "My Score",
  "status": "running",
  "progress": {
    "completed": 5,
    "total": 10,
    "percent": 50.0
  },
  "sheets": {
    "1": {"status": "completed", "attempt_count": 1},
    "2": {"status": "in_progress", "attempt_count": 1}
  }
}
```

---

### `mzt watch`

Live-tail musician output for a running score (#352).

```
Usage: mzt watch [OPTIONS] JOB_ID [SHEET_NUM]
```

Streams each LLM's output as it generates, credential-redacted, over the
conductor's `job.output.stream` IPC. The stream starts with the recent
lines retained in the sheet's ring buffer (~256 KB per sheet,
drop-oldest with an explicit `[dropped N lines]` marker), then follows
new output until Ctrl-C. A slow terminal never slows the conductor —
backpressure drops lines with a marker rather than blocking.

The ring is a live view: a conductor restart clears it. Persisted
evidence lives in each sheet's recorded output tails (`mzt diagnose`).

Interactive sheets (the default for `claude-code`) stream too: the driver
tails the tmux pipe-pane transcript once per poll and hands each new slice to
the same sink, so what you see is the raw terminal stream (cursor-addressing
included), not a parsed log. If transcript capture failed for an attempt, that
sheet has nothing to stream until it ends.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score ID to tail (see `mzt status`) |
| `SHEET_NUM` | No | Sheet number to tail. Omit to tail ALL sheets — lines arrive tagged `[s<sheet>]` |

#### Examples

```bash
# Tail every sheet of a running score
mzt watch my-score

# Tail only sheet 3
mzt watch my-score 3
```

---

### `mzt list`

List scores from the conductor.

```
Usage: mzt list [OPTIONS]
```

By default shows active score runs (queued, running, paused). Use `--all` to include completed, failed, and cancelled conductor records.

> **Note:** This command requires a running Marianne conductor (`mzt start`). Use `mzt status <job-id>` for checking one submitted score by its conductor runtime handle.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--all` | `-a` | false | Show all score runs including completed, failed, and cancelled |
| `--status` | `-s` | | Filter by score-run status: `queued`, `running`, `completed`, `failed`, `paused` |
| `--limit` | `-l` | 20 | Maximum number of terminal (completed/failed/cancelled) score runs to display. Live runs (queued, running, paused) are always all shown; the footer names how many older terminal runs were hidden |
| `--json` | | false | Output as JSON array for machine parsing |

#### Examples

```bash
# List active score runs (default)
mzt list

# List all score runs including completed
mzt list --all

# Filter by status
mzt list --status failed

# JSON output for scripting
mzt list --json

# Limit results
mzt list --limit 10
```

---

### `mzt validate`

Validate a score configuration file.

```
Usage: mzt validate [OPTIONS] CONFIG_FILE
```

Performs comprehensive validation including YAML syntax, Pydantic schema validation, Jinja template syntax checking, path existence verification, regex pattern compilation, and configuration completeness checks.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `CONFIG_FILE` | Yes | Path to YAML score configuration file |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output validation results as JSON |
| `--verbose` | `-v` | false | Include INFO findings and a fuller rendering preview |
| `--strict` | | false | Exit 1 for any unsuppressed WARNING |
| `--errors-only` | | false | Show only ERROR findings; exit behavior is unchanged |

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No ERROR findings; warnings are allowed unless `--strict` is set |
| 1 | ERROR findings, or unsuppressed WARNING findings with `--strict` |
| 2 | Cannot validate: unreadable/non-score YAML, malformed YAML, or genuine schema failure |

#### Validation Checks

Existing checks keep their V-codes. S3 adds these findings and changes the
tiers shown below. The complete emitted set is exercised by the validator's
code-registry and corpus-sweep tests.

| Tier | Codes | Meaning |
|------|-------|---------|
| ERROR | V001 | Score-level or sheet prompt template is not valid Jinja2 syntax |
| ERROR | V002 | Workspace parent directory does not exist |
| ERROR | V003 | `prompt.template_file` does not exist |
| ERROR | V004 | A validation or injection path is absolute under the current user's home directory |
| ERROR | V007 | A `content_regex` (or detection) pattern does not compile |
| ERROR | V008 | A validation rule is missing the field its type requires |
| ERROR | V009 | An evolved score references its previous version's paths |
| ERROR | V012 | Invalid `validate.suppress` entry (unknown code or ERROR-tier code) |
| ERROR | V105, V108 | Undefined validation variable (declared `prompt.variables`, built-ins and declared loop indices are defined); missing static prelude/cadenza input |
| ERROR | V209, V214, V216, V217 | Dependency cycle; expanded-sheet incoherence; missing cadenza target; missing or invalid static concert target |
| ERROR | V210, V211 | Instrument or fallback name cannot resolve to a known profile, score alias, or capability class |
| ERROR | V221–V223, V227, V229, V233, V312–V314, V316–V317, V320 | Flow load errors and invalid flow references, reported with a code and hint rather than a raw schema trace |
| ERROR | V305 | Bash `'${#...}'` length syntax in a prompt the Jinja engine eats as a comment |
| ERROR | V306 | A `path_in_scope` validation can traverse outside its declared scope |
| ERROR | V310, V321, V323, V324 | Capability classes: a class-shaped name cannot resolve on this machine (V-CLS-02: vocabulary-only, tombstoned, or a chain with no registered entry — the same chains `mzt run` refuses); an instrument alias names a class as its profile; `instrument_config.model` applied to a class primary; an invalid classes layer file while the score uses a class |
| ERROR | V213 | `interactive: true` set for an instrument without verified interactive support |
| WARNING | V010 | Unknown field at ANY depth — a whole block at the root (`deliver: {…}`, a list) is a feature the runtime does not implement and warns as such, since the loader drops it and nothing it configures will happen; the curated hint names a working key AT THE SURFACE the mistake sits in: a bare `timeout:` inside `instruments.<alias>.config`, a movement's or a sheet's `instrument_config` names that surface's `timeout_seconds`; inside a `run:` trigger action it names the action's own `timeout_seconds`; only at the root (or beside an action with no timeout of its own) does it name `instrument_config.timeout_seconds` (GH #451) |
| WARNING | V208, V219–V220, V224, V226, V231–V232, V315 | Built-in shadowing (deprecation); advisory structure and flow risks |
| WARNING | V228 | A loop's `cost_limit_usd` cannot bind because every route in the span reports no cost — the message names the route and why (raw shell profile, no token paths, no models, or a $0 price); a priced CLI route such as `claude-code` or `codex-cli` DOES report cost and the cap binds (GH #453) |
| WARNING | V327 | A score sets a reserved key — accepted, but no runtime code reads it; also warns when `movements.<N>.voices` cannot expand with the sheet's size/start settings |
| WARNING | V328 | Deprecated spelling or key: root `max_wall_seconds` (now `schedule.max_wall_seconds`), `checkpoints:` (now `approvals:`), hook-level `on_failure` (now `if_hook_fails`), root `conductor` |
| WARNING | V329 | A readable default-off section has settings but no `enabled` key; set `enabled: true` now or explicit `enabled: false` to opt out of next release's auto-enable rule |
| WARNING | V101 | Template references a variable the score never declares (and no built-in provides) |
| WARNING | V102 | `\| string` filter applied to a subscript of an int-keyed variable dict |
| WARNING | V103 | Sheet or instrument timeout outside the reasonable range (V103/V104) |
| WARNING | V106 | Empty pattern in a `content_contains`/`content_regex` validation, or regex syntax (`[Pp]x`, `A\|B`, `\s`) in a `content_contains` pattern, which is matched as a literal substring |
| WARNING | V109 | A cadenza reads a file a later sheet produces |
| WARNING | V201 | Jinja `{{ }}` syntax inside a validation path or command (they are format strings, never rendered) |
| WARNING | V202 | Python format-string (`%s`, `{x}`) syntax inside a Jinja template |
| WARNING | V203 | No validation rules exist — nothing defines done |
| WARNING | V206 | Fan-out declared without stage dependencies |
| WARNING | V212 | A `skip_when` key names a sheet outside the expanded range |
| WARNING | V215 | No instrument in the score's chain appears installed on this machine |
| WARNING | V230 | Flow configuration error rendered from the loader (loops/triggers/goto) with position and hint |
| ERROR | V233 | A loop `until` names `file("{{ … }}")`: Jinja braces inside a flow file path are kept verbatim, so the file never exists — write the workspace-relative or `{workspace}` validation form |
| WARNING | V301 | A skill technique's `path` does not exist — the agent would never receive that methodology |
| WARNING | V302 | An MCP technique targets an instrument with no MCP access path (native config or generated bridge) |
| WARNING | V303 | Folded YAML scalar on a command field (newlines collapse — usually not what the author meant) |
| WARNING | V304 | Code execution would run unsandboxed (bwrap unavailable and `require_sandbox` false) |
| WARNING | V308 | Partial concrete instrument coverage inside fan-out movements |
| WARNING | V309 | An exact section-label validation the prompt never teaches the agent |
| WARNING | V330 | An alias `select` modifier has nothing to choose between: fewer than two chain entries, or `cheapest`/`widest` found no model-table row for any entry; the score runs on the authored order |
| WARNING | V331 | A sheet sets `response_format` on a profile that runs interactively by default (`cli.interactive.enabled_by_default: true`, e.g. claude-code) and does not say `interactive: false`; the interactive backend cannot enforce a schema and dispatch would refuse the sheet (GH #478) |
| WARNING | V332 | A CLI schema sheet's `response_format` schema has a top-level `oneOf`/`allOf`/`anyOf` or no top-level `type`; Claude Code forwards it as a tool `input_schema`, which the API refuses with 400 (GH #486). Wrap the branches under one required property |
| WARNING | V333 | A `command_succeeds` command uses a `{placeholder}` in a shape the engine's shell-quoting defeats: a bash array literal `xs=({list})` (one element — use `read -r -a xs <<< {list}`), or a prompt variable whose default needs quoting (`~/path`, a space) spliced inside an interpreter's string literal (`python3 -c "...'{var}/x'..."` keeps the quotes — pass it as an argument and read `sys.argv`). GH #477. |
| WARNING | V334 | A prelude/cadenza item with `max_bytes` whose static path is already over the cap (measured now; the runtime refuses a required item or drops an optional one at every sheet). GH #474. |
| WARNING | V311, V322 | Capability classes: part of a used class's chain cannot run here (binary missing, unsupported, local server not serving the profile's model, or unable to enforce the sheet's `response_format`). V311 also warns when a sheet bound straight to a CLI profile (no class) asks for a `json_schema` `response_format` the profile cannot enforce (no `cli.command.json_schema_flag`): every dispatch of those sheets would be refused, and the warning names them — the job starts at the first available entry; when no entry can run here the warning says so and names the install (availability is a fact about this machine, so the score is still admitted); a default-layer class shadowed by a same-named profile; a score alias shadowing a class name |
| INFO | V011, V104, V110, V218, V225 | Distant unknown field; timeout above 7200 seconds (2 hours); unused variable; ambiguous file reference; mixed fan-out keying |
| INFO | V205 | All validations are `file_exists` only — nothing checks content |
| INFO | V207 | Fan-out declared without `parallel.enabled` (instances still run sequentially) |
| INFO | V325 | Capability classes: per-used-class resolution summary (`Instruments: strong → claude-code, … (user layer, sha256 …)`) |
| INFO | V326 | A score uses a `run:` trigger on a host without Linux subreaper support. Detached orphans with closed stdio may outlive the action; Linux reaps them under the per-action supervisor. |

A `sheet.triggers` `run:` action executes its command in a bounded shell.
On Linux a per-action subreaper keeps detached children owned until cleanup
finishes, including children that close stdout and stderr. Other hosts use
process-group, ancestry, and log-holder cleanup; V326 identifies the narrower
orphan guarantee before execution. The subreaper is that guarantee only while
it lives: if it dies by a signal the conductor did not send (an OOM kill, an
operator's `kill -9`) after the shell has reported, its adopted children are
lost to init and the action fails with `run supervisor died by signal N` rather
than reporting the shell's clean exit (GH #444).

V210/V211 search profiles, score aliases, configured classes, and the shipped
class vocabulary; their suggestions use edit distance ≤ 2 over that whole
namespace. A name that is vocabulary-only or tombstoned reports under V310
with the `mzt instruments classes write` fix hint instead of the generic
unknown-name row. The raw-shell check V307 also warns when a `cli` sheet's
declared fallback names a class (model instruments).

`mzt validate` reads the three class-map layers through the same loader the
conductor uses and prints every layer path it read with its sha256 — in
`--json` under `summary.classes.layers` (plus `used`, `per_sheet`, and
`failures` when a class is named), and on the terminal as `Class layer` lines.
A broken layer file is reported (V324) only when the score names a class;
scores that never touch classes stay silent about machine class state.

V107's warning on validation output paths is retired: an output need not exist
before a job runs. The dead check class is deleted (GH #439); V301 and V302
(technique consistency) are registered with fixtures in the same change.
INFO findings appear in terminal output only with `--verbose`. `--json`
includes them by default.

`validate.suppress: [Vxxx]` may hide WARN and INFO findings. Suppressed findings
leave `issues` and the visible counts and appear as `{check_id, count}` entries
in `suppressed`. Suppressing an ERROR or unknown code raises V012 ERROR, so it
cannot make an invalid score pass. `--strict` ignores suppressed warnings.
The JSON keys `valid`, `error_count`, `warning_count`, `info_count`, `issues`,
and `rendering` retain their meanings; `summary` and `suppressed` are additive.

Dependency-loading failures may separately log
`validation.profiles_unavailable_skip` at WARNING level while affected checks
skip or use reduced information. These log events do not themselves affect
`--strict`, issue counts, or `validate.suppress`. Repair the reported failure
and validate again; see [validation dependency failures](configuration-reference.md#validation-dependency-failures).

#### Examples

```bash
# Basic validation
mzt validate job.yaml

# Detailed output
mzt validate job.yaml --verbose

# JSON output for CI/CD
mzt validate job.yaml --json

# Fail CI on new warnings while acknowledging reviewed advisory codes
mzt validate job.yaml --strict
```

---

### `mzt init`

Scaffold a new Marianne project with a starter score.

```
Usage: mzt init [OPTIONS] [SCORE_NAME]
```

Creates a starter score YAML and `.marianne/` project directory. The generated
score includes comments explaining every field — edit it with your task, then
run it. This is the first-run path for an install with no repository checkout:
`examples/` is not in the wheel, `mzt init` needs nothing but the package.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `SCORE_NAME` | No | Score name (same as `--name`). Default: `my-score` |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--path` | `-p` | `.` | Directory to initialize |
| `--name` | `-n` | `my-score` | Name for the starter score (overrides positional) |
| `--force` | `-f` | false | Overwrite existing files |
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Initialize current directory with default name
mzt init

# Initialize with a custom name (positional — like git init)
mzt init data-pipeline

# Initialize with a custom name (flag)
mzt init --name data-pipeline

# Initialize in a specific directory
mzt init --path ./my-project

# Machine-readable output
mzt init --json
```

#### What It Creates

```
./
├── my-score.yaml        # Starter score — edit with your task
└── .marianne/             # Project configuration directory
```

**Next steps** after init: edit the score YAML, then `mzt start && mzt run my-score.yaml`.

---

### `mzt cancel`

Cancel a running score immediately.

```
Usage: mzt cancel [OPTIONS] SCORE_ID
```

Unlike `pause`, this does not wait for a sheet boundary. The conductor cancels
the running score, in-progress work is rolled back, and the score is marked as
CANCELLED.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `SCORE_ID` | Yes | Score to cancel |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Cancel a running score
mzt cancel my-job

# Cancel with JSON output
mzt cancel my-job --json
```

**Tip:** Use `mzt pause` for graceful stops that wait for the current sheet to finish. Use `cancel` when the score must stop now.

---

### `mzt clear`

Clear terminal scores from the conductor registry.

```
Usage: mzt clear [OPTIONS]
```

Removes completed, failed, and/or cancelled scores from the conductor's
tracking. Running and queued scores are never cleared.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--score` | `-j` | | Specific score ID(s) to clear. Can be repeated. |
| `--status` | `-s` | all terminal | Status(es) to clear: `failed`, `completed`, `cancelled`. Can be repeated. |
| `--older-than` | | | Only clear scores older than this many seconds |
| `--yes` | `-y` | false | Skip confirmation prompt |

#### Examples

```bash
# Clear all terminal scores
mzt clear

# Clear a specific score
mzt clear --score conductor-fix

# Clear only failed scores
mzt clear --status failed

# Clear failed + cancelled scores
mzt clear --status failed -s cancelled

# Clear scores older than 1 hour, skip confirmation
mzt clear --older-than 3600 -y
```

---

### `mzt logs`

Show or tail log sources for a score run.

```
Usage: mzt logs [OPTIONS] [JOB_ID]
```

Displays log entries from Marianne log sources. Daemon-managed score runs use
the conductor logging root by default, with compatibility workspace aliases and
rotated logs handled by the log reader where available.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | No | Score-run ID to filter logs for; this is the conductor's runtime handle |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--workspace` | `-w` | `.` | *(hidden)* Debug override: specify workspace for log file lookup |
| `--file` | `-f` | | Explicit offline/debug log file path |
| `--follow` | `-F` | false | Follow the log file for new entries (like `tail -f`) |
| `--lines` | `-n` | 50 | Number of lines to show (0 for all) |
| `--level` | `-l` | | Filter by minimum log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--json` | `-j` | false | Output raw JSON log entries |

#### Examples

```bash
# Show recent logs
mzt logs

# Filter by score run
mzt logs my-job

# Follow logs in real-time
mzt logs --follow

# Show last 100 lines of errors only
mzt logs --lines 100 --level ERROR

# Use a specific offline/debug log file
mzt logs --file ~/.marianne/logs/conductor.log
```

---

### `mzt errors`

List all errors for a score run with detailed information.

```
Usage: mzt errors [OPTIONS] JOB_ID
```

Displays errors grouped by sheet, with color-coding by error type:
- **Red:** Permanent errors (non-retriable, fatal)
- **Yellow:** Transient errors (retriable with backoff)
- **Blue:** Rate limit errors (retriable after wait)

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score-run ID to show errors for; this is the conductor's runtime handle |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--sheet` | `-b` | | Filter errors by specific sheet number |
| `--type` | `-t` | | Filter by error type: `transient`, `rate_limit`, or `permanent` |
| `--code` | `-c` | | Filter by error code (e.g., `E001`, `E101`) |
| `--verbose` | `-V` | false | Show full stdout/stderr tails for each error |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for error retrieval |
| `--json` | `-j` | false | Output errors as JSON |

#### Examples

```bash
# Show all errors
mzt errors my-job

# Errors for specific sheet
mzt errors my-job --sheet 3

# Only transient errors
mzt errors my-job --type transient

# Filter by error code
mzt errors my-job --code E001

# Verbose with stdout/stderr details
mzt errors my-job --verbose
```

---

### `mzt diagnose`

Generate a comprehensive diagnostic report for a score run.

```
Usage: mzt diagnose [OPTIONS] JOB_ID
```

The diagnostic report includes:
- Score-run overview and current status
- Preflight warnings from all sheets (`preflight:` prompt-size notes; `artifact:` a sheet wrote a file a later, non-ancestor sheet is declared to produce — GH #386)
- Prompt metrics (token counts, line counts)
- Execution timeline with timing information, and under it one line per attempt for any sheet that needed more than one or ended abnormally: `1. claude-code (claude-opus-5-5): 30m → TIMEOUT`, `2. codex-cli: 1m 9s → ok` (GH #434)
- All errors with full context and output tails
- Log file locations, sizes, and modification times
- (with `--include-logs`) Inline log content from each log file

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score-run ID to diagnose; this is the conductor's runtime handle |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--workspace` | `-w` | | Workspace directory. When the conductor doesn't recognize a score ID, Marianne falls back to reading state directly from this workspace directory. Useful for diagnosing scores from stopped conductors or clone conductors. |
| `--json` | `-j` | false | Output diagnostic report as JSON |
| `--include-logs` | | false | Inline the last 50 lines from each sheet/hook log file |
| `--resources` | | false | Include resource profile data such as peak memory, CPU time, syscalls, and anomalies |

#### Examples

```bash
# Full diagnostic report
mzt diagnose my-job

# Machine-readable
mzt diagnose my-job --json

# Include inline log content
mzt diagnose my-job --include-logs

# Include resource profile data
mzt diagnose my-job --resources

# Diagnose from workspace (when conductor doesn't know the score)
mzt diagnose my-job -w ./workspaces/my-job
```

---

### `mzt doctor`

Check Marianne environment health.

```
Usage: mzt doctor [OPTIONS]
```

Validates that your environment is ready to run Marianne scores. Checks Python version, Marianne installation, conductor status, available instruments, and safety configuration. This command works without a running conductor — it is designed to be the first thing you run after installation.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Output results as JSON |
| `--clean` | | false | Delete storage-hygiene findings after explicit confirmation |
| `--live` | | false | Also run `instruments check --live` on every ready agent instrument. A failed probe becomes a warning naming the mode and classification; no instrument passing is an error. Spends model calls; use it after installing or upgrading a CLI, or before a long run |

#### Checks Performed

| Check | What It Verifies |
|-------|-----------------|
| Python | Version 3.11+ installed |
| Marianne | Marianne package installed, version displayed |
| Conductor | Whether the conductor daemon is running (PID file + process check) |
| Instruments | Configured instruments, binary readiness, execution readiness, and runtime rate-limit state when the conductor is running |
| Safety | Whether cost limits are configured |
| Storage | Cleanable stale state or backup artifacts |
| Instrument hygiene | User- and venue-layer profiles that no score the conductor has been given names and no class chain includes (GH #443). Reported only, never deleted: a seat is an alias over an existing profile, so a profile nothing names is archived by a person or kept by submitting the score that needs it. `--json` lists them under `unreferenced_profiles`. Skipped before the conductor has ever run. Reads the registry of the conductor the CLI addresses (`--conductor-clone` selects the clone's) |

Every command that answers "can this instrument run here" — `doctor`, `instruments list`, `instruments check`, `instruments classes check` and `classes write` — uses one probe with four states (GH #447):

| Probe state | Meaning | Shown as |
|-------------|---------|----------|
| `ready` | Can run now: CLI binary on PATH, or a local (loopback) server that answers and serves the profile's `default_model`, or a native instrument | `✓` (doctor `ok`; list `✓ ready`) |
| `unavailable` | Cannot run: binary missing, profile declared unsupported, no command, or the local server answers but does not serve the model (the served models are named) | `·` (doctor `optional`; list `✗ not found`) |
| `unreachable` | A local server that does not answer | `·` (doctor `optional`; list `✗ not answering`) |
| `unchecked` | A remote HTTP endpoint; never probed because it needs credentials | `?` (doctor and list `unchecked`) |

"Marianne is ready" means at least one agent instrument is `ready` (the raw shell `cli` profile does not count). With none, `doctor` reports an error naming what to install or pull. Runtime rate-limit state is shown when the conductor is running.

#### Examples

```bash
# Check environment
mzt doctor

# JSON output for scripting
mzt doctor --json

# Review and remove cleanable storage findings
mzt doctor --clean
```

#### Sample Output

```
Marianne Doctor

  ✓ Python 3.12                   installed
  ✓ Marianne v1.0.0                 installed
  ✓ Conductor                     running (pid 12345)

  Instruments:
  ✓ claude-code                   /usr/local/bin/claude
  · gemini-cli                    not found (Binary 'gemini' not found on PATH for instrument 'gemini-cli')
  · ollama                        not found (model 'llama3.1:8b' is not served at http://localhost:11434/v1 …)

  Safety:
  ⚠ No cost limits configured     Recommend: cost_limits.max_cost_per_job

1 warning. Marianne is ready (1 runnable instrument(s)).
```

---

### `mzt history`

Show execution history for a job.

```
Usage: mzt history [OPTIONS] JOB_ID
```

Displays a table of past execution attempts from the SQLite state backend, including sheet number, attempt number, exit code, duration, and timestamp.

> **Note:** Requires the SQLite state backend. Execution history is not available with the JSON backend.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to show execution history for |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--sheet` | `-b` | | Filter by specific sheet number |
| `--limit` | `-n` | 50 | Maximum number of records to show |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for history |
| `--json` | `-j` | false | Output history as JSON |

#### Examples

```bash
# Show all history
mzt history my-job

# History for specific sheet
mzt history my-job --sheet 3

# Show more records
mzt history my-job --limit 100

# JSON output
mzt history my-job --json
```

---

### `mzt recover`

Recover sheets that completed work but were incorrectly marked as failed.

```
Usage: mzt recover [OPTIONS] JOB_ID
```

Runs validations for failed sheets without re-executing them. If validations pass, the sheet is marked as complete. This is useful when:
- Claude CLI returned a non-zero exit code but the work was done
- A transient error caused failure after files were created
- You want to check if a failed sheet actually succeeded

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score ID to recover |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--sheet` | `-s` | | Specific sheet number to recover (default: all failed sheets) |
| `--from-sheet` | `-f` | | Reset all failed sheets >= this number to pending for cascade recovery |
| `--dry-run` | `-n` | false | Check validations without modifying state |

#### Examples

```bash
# Recover all failed sheets
mzt recover my-job

# Recover specific sheet
mzt recover my-job --sheet 6

# Check without modifying (dry run)
mzt recover my-job --dry-run

# Reset cascade failures from sheet 211 onward
mzt recover my-job --from-sheet 211
```

---

### `mzt dashboard`

Start the web dashboard.

```
Usage: mzt dashboard [OPTIONS]
```

Launches the Marianne dashboard API server for submitted scores, conductor
state, logs, artifacts, and controls. API payloads still use `job_id` as the
conductor's runtime record handle.

`/orchestra` is the fleet on stage: one card per score, one cell per sheet, live
over `/api/orchestra/stream` (SSE) with a replay transport for any recorded day
(`/api/orchestra/replay?day=YYYY-MM-DD`). It renders the conductor's public-safe
projection only (names, sheets, instruments, outcomes, times), so it is the one
page meant to be exposed on a public hostname.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | 8000 | Port to run dashboard on |
| `--host` | | `127.0.0.1` | Host to bind to. A non-loopback host with `MZT_AUTH_MODE` still `localhost_only` (or `api_key` with no keys / the localhost bypass on) prints a red **Exposed bind** panel before the server starts: the listener would answer every interface and refuse every non-local client, or let a proxy skip the key check. The bind still happens; the warning is the operator's cue (GH #387). |
| `--workspace` | `-w` | `.` | Workspace directory for job state |
| `--reload` | `-r` | false | Enable auto-reload for development |

#### Examples

```bash
# Start with defaults (localhost:8000)
mzt dashboard

# Custom port
mzt dashboard --port 3000

# Allow external connections — only with real keys and the localhost bypass off
MZT_AUTH_MODE=api_key MZT_API_KEYS=<keys> MZT_LOCALHOST_BYPASS=false \
  mzt dashboard --host 0.0.0.0

# Publish ONLY the orchestra view (the public fleet page); everything else
# still refuses non-local clients
MZT_PUBLIC_ORCHESTRA=true mzt dashboard --host 0.0.0.0

# Development mode with auto-reload
mzt dashboard --reload
```

#### Dashboard API Endpoints

##### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check (status, version) |

##### Job Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs` | List all jobs with optional status filter and pagination |
| GET | `/api/jobs/{id}` | Get detailed information about a specific job |
| GET | `/api/jobs/{id}/status` | Get focused status (lightweight polling endpoint) |
| POST | `/api/jobs` | Start a new job (inline config or file path) |
| POST | `/api/jobs/{id}/pause` | Pause a running job |
| POST | `/api/jobs/{id}/resume` | Resume a paused job |
| POST | `/api/jobs/{id}/cancel` | Cancel a running job |
| DELETE | `/api/jobs/{id}` | Delete a job record |

##### Sheet Details

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/{id}/sheets/{num}` | Sheet-level status, logs, validation, costs, tokens |

##### Streaming & Logs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/{id}/stream` | Real-time job status updates via SSE |
| GET | `/api/jobs/{id}/logs` | Stream job logs via SSE with follow mode |
| GET | `/api/jobs/{id}/logs/static` | Download complete log file as plain text |
| GET | `/api/jobs/{id}/logs/info` | Log file metadata (size, lines, modified time) |

##### Artifacts & Workspace

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/{id}/artifacts` | List workspace files (recursive, with filtering) |
| GET | `/api/jobs/{id}/artifacts/{path}` | Read a specific workspace file |

##### Configuration & Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scores/validate` | Validate YAML config (syntax, schema, extended checks) |
| GET | `/api/templates/list` | List available templates (category, complexity, search filtering) |
| GET | `/api/templates/{name}` | Get template details |
| GET | `/api/templates/{name}/download` | Download template as YAML file |
| POST | `/api/templates/{name}/use` | Use a template (redirect to editor) |

##### Daemon

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/daemon/status` | Check daemon status (PID, uptime, running jobs, memory) |

---

### `mzt mcp`

Start the Marianne MCP (Model Context Protocol) server.

```
Usage: mzt mcp [OPTIONS]
```

Launches an MCP server that exposes Marianne's job management capabilities as tools for external AI agents. Provides job management tools, artifact browsing, log streaming, and configuration access.

When the Marianne conductor is running, the MCP server routes operations through it for coordinated execution.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | 8001 | Port to run MCP server on |
| `--host` | | `127.0.0.1` | Host to bind to |
| `--workspace` | `-w` | `.` | Workspace directory for job operations |

#### Examples

```bash
# Start on default port
mzt mcp

# Custom port
mzt mcp --port 8002

# Specific workspace
mzt mcp --workspace ./projects
```

See [MCP Integration Guide](MCP-INTEGRATION.md) for Claude Desktop setup and available tools.

---

### `mzt top`

Real-time system monitor — like htop for your conductor.

```
Usage: mzt top [OPTIONS]
```

Shows a score-centric process tree, resource metrics, event timeline, anomaly
detection, and learning insights. Four operating modes:

| Mode | Flag | Description |
|------|------|-------------|
| TUI | *(default)* | Rich terminal UI with live updates |
| JSON | `--json` | Stream NDJSON snapshots to stdout |
| History | `--history 1h` | Replay historical data from profiler DB |
| Trace | `--trace PID` | Attach strace to a specific process |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Stream JSON snapshots (NDJSON format) |
| `--history` | | | Replay historical data (e.g., `1h`, `30m`, `2h30m`) |
| `--trace` | | | Attach strace to a specific PID |
| `--score` | `-s` | | Filter JSON/history output by score ID; TUI mode is not filtered yet |
| `--interval` | `-i` | `2.0` | Refresh interval in seconds |

#### Examples

```bash
# Live TUI monitor
mzt top

# Filter JSON output to a specific score
mzt top --score my-pipeline --json

# JSON output for scripting
mzt top --json

# Replay the last hour of data
mzt top --history 1h

# Faster refresh rate
mzt top --interval 0.5
```

---

### `mzt config`

Manage conductor configuration.

```
Usage: mzt config COMMAND [OPTIONS]
```

#### Subcommands

##### `mzt config show`

Display current daemon configuration as a table. When the conductor is running, displays the **live in-memory config** (reflecting any SIGHUP reloads) with `[live]` source indicators. Falls back to disk-based display when the conductor is not running.

```bash
mzt config show
mzt config show --config /etc/marianne/daemon.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file (default: `~/.marianne/daemon.yaml`). Ignored when live config is available from a running conductor. |

##### `mzt config set`

Update a conductor configuration value. Values are validated against the DaemonConfig schema before saving. Use dot notation for nested keys.

```bash
mzt config set max_concurrent_jobs 10
mzt config set socket.path /tmp/custom.sock
mzt config set resource_limits.max_memory_mb 4096
mzt config set log_level debug
```

| Argument | Required | Description |
|----------|----------|-------------|
| `KEY` | Yes | Config key in dot notation (e.g., `socket.path`, `max_concurrent_jobs`) |
| `VALUE` | Yes | New value to set |

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file (default: `~/.marianne/daemon.yaml`) |

##### `mzt config path`

Show the conductor config file location and whether it exists.

```bash
mzt config path
```

##### `mzt config init`

Create a default conductor config file with all default values and descriptive comments. Refuses to overwrite unless `--force` is given.

```bash
mzt config init
mzt config init --force
mzt config init --config /etc/marianne/daemon.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to create config file (default: `~/.marianne/daemon.yaml`) |
| `--force` | `-f` | Overwrite existing config file |

##### `mzt config check`

Validate a daemon config file against the `DaemonConfig` schema without starting the conductor. Exits 0 if valid, 1 if invalid or the file cannot be loaded.

```bash
mzt config check
mzt config check --config /path/to/custom.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file to validate (default: `~/.marianne/daemon.yaml`) |

---

## Instrument Commands

### `mzt instruments classes show|check|write`

`show --json` reports the effective class chains, each layer path and SHA256,
and entry availability. `check` exits 1 for a broken layer or a user configured
class with no available entry; `check --class strong` also requires that class
to have an available entry. `write` probes shipped profiles and creates
`~/.marianne/classes.yaml` with an ordered, model-free chain for each eligible
class. It preserves an existing file with `--if-absent`, prints a proposal with
`--dry-run`, and requires `--force` to replace hand edits. Replacements are
backed up and written atomically.

These commands manage and inspect available instruments — the AI tools Marianne can use to execute scores. Instruments include CLI tools (Claude Code, Gemini CLI, Codex CLI, Aider, Goose) and HTTP APIs (Anthropic API, Ollama).

### `mzt instruments resolve`

Show, per sheet, what each instrument binding in a score resolves to.

```
Usage: mzt instruments resolve [OPTIONS] SCORE
```

Reads the score the way the conductor does at admission — score aliases
(`instruments:`), capability classes from this machine's class map,
`per_sheet_instruments` and `instrument_map` pins — and prints one row per
sheet: what the sheet was bound through (an alias or class name, or
`(direct)`), the profile it resolved to, the model, and the fallback chain.
The closing line counts distinct profile+model bindings: a "three
independent reviewers" fan-out that resolves to one binding is not
independent, whatever its prose says. Pair with `{{ instrument_alias }}` in
the score to stamp evidence with the binding name.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Output as JSON (`{"score": ..., "sheets": [{sheet, movement, voice, bound_through, profile, model, fallbacks}]}`) |

#### Examples

```bash
mzt instruments resolve examples/engineering/quality-triage.yaml
mzt instruments resolve my-score.yaml --json | jq '.sheets[] | select(.movement == 4)'
```

### `mzt instruments list`

List all available instruments and their readiness status.

```
Usage: mzt instruments list [OPTIONS]
```

Shows every registered instrument: built-in profiles (shipped as YAML), organization profiles (`~/.marianne/instruments/`), and venue profiles (`.marianne/instruments/`). Later profiles override earlier ones on name collision.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Output as JSON |

#### Examples

```bash
# List all instruments
mzt instruments list

# JSON output
mzt instruments list --json
```

#### Sample Output

```
                    Instruments
  NAME              KIND    STATUS          DEFAULT MODEL
  aider             cli     ✗ not found     (instrument default)
  claude-code       cli     ✓ ready         (instrument default)
  codex-cli         cli     ✓ ready         (instrument default)
  gemini-cli        cli     ✓ ready         gemini-2.5-pro
  ollama            http    ✗ not found     llama3.1:8b

11 instruments configured (3 ready)
```

**Status values** (the same probe as `mzt doctor`; see its four states there):
- `✓ ready` — can run now: CLI binary on PATH, or a local server serving the profile's model
- `✗ not found` — binary not on PATH, or the local server does not serve the model (install the tool, or pull the model)
- `✗ not answering` — a local server that does not answer
- `? unchecked` — a remote HTTP endpoint; never probed (needs credentials)

---

### `mzt instruments check`

Check readiness and configuration of a specific instrument.

```
Usage: mzt instruments check [OPTIONS] NAME
```

Provides detailed information about a single instrument: binary location, capabilities, available models with pricing, and overall readiness. Readiness is the same probe `mzt doctor` uses: a local HTTP instrument is ready only when its server answers and serves the profile's `default_model`; a remote endpoint is reported `unchecked`, which is not a failure (exit 0).

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `NAME` | Yes | Instrument name to check (as shown in `instruments list`) |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Output as JSON |
| `--live` | | false | Run one real prompt through the same backend the conductor uses, and one schema-mode prompt when the profile declares `json_schema_flag` — its schema carries a `$schema` (GH #479) and a discriminated union nested under a required property (GH #486), the shapes real intake schemas take, and the probe checks the chosen branch came back. Reports pass/fail, wall time and the classification the baton would act on (`INSTRUMENT_UNAVAILABLE`, `RATE_LIMITED`, `TRANSIENT`, …). Readiness proves the binary is on PATH; `--live` proves the profile works against the installed CLI. Exits 1 on failure. Spends one or two model calls |
| `--model` | | profile default | Model for `--live` |
| `--timeout` | | 180 | Seconds per `--live` call |
| `--record DIR` | | | With `--live`: write each call's raw stdout, stderr, exit code and the CLI's `--version` to `DIR/<instrument>/<mode>.*`. Use it to capture parser fixtures from the CLI actually installed (`tests/fixtures/cli_captures/` holds Claude Code's) and re-record after a CLI upgrade |

#### Examples

```bash
# Check a specific instrument
mzt instruments check gemini-cli

# Prove the profile end to end (a real prompt, plus schema mode if declared)
mzt instruments check claude-code --live

# JSON output
mzt instruments check claude-code --json
```

#### Sample Output

```
Checking gemini-cli...
  Display name:  Gemini CLI
  Description:   Google's Gemini CLI with tool use and vision
  Kind:          cli
  Binary:        /usr/local/bin/gemini ✓
  Capabilities:  file_editing, shell_access, structured_output, tool_use, vision
  Default model: gemini-2.5-pro
    gemini-2.5-pro: 1,000,000 ctx ($0.0013/1K in, $0.0050/1K out)
    gemini-2.5-flash: 1,000,000 ctx ($0.0002/1K in, $0.0006/1K out)

gemini-cli is ready.
```

---

## Learning Commands

These commands inspect and analyze Marianne's learning system — patterns learned from job executions that inform future runs.

### `mzt patterns-list`

View global learning patterns.

```
Usage: mzt patterns-list [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--global` / `--local` | `-g` / `-l` | global | Show global or local workspace patterns |
| `--min-priority` | `-p` | 0.0 | Minimum priority score (0.0–1.0) |
| `--limit` | `-n` | 20 | Maximum patterns to display |
| `--json` | `-j` | false | Output as JSON |
| `--quarantined` | `-q` | false | Show only quarantined patterns |
| `--high-trust` | | false | Show only patterns with trust >= 0.7 |
| `--low-trust` | | false | Show only patterns with trust <= 0.3 |

```bash
mzt patterns-list
mzt patterns-list --high-trust
mzt patterns-list --quarantined
mzt patterns-list --min-priority 0.5 --json
```

---

### `mzt patterns-why`

Analyze WHY patterns succeed with metacognitive insights. Shows success factors — the context conditions that contribute to pattern effectiveness.

```
Usage: mzt patterns-why [OPTIONS] [PATTERN_ID]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `PATTERN_ID` | No | Pattern ID to analyze (first 10 chars). If omitted, shows all patterns with success factors |

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--min-obs` | `-m` | 1 | Minimum success factor observations required |
| `--limit` | `-n` | 10 | Maximum patterns to display |
| `--json` | `-j` | false | Output as JSON |

```bash
mzt patterns-why
mzt patterns-why abc123
mzt patterns-why --min-obs 3
```

---

### `mzt patterns-entropy`

Monitor pattern population diversity using Shannon entropy.

```
Usage: mzt patterns-entropy [OPTIONS]
```

Shannon entropy measures how evenly patterns are used:
- **High entropy (H → max):** Healthy diversity, many patterns contribute
- **Low entropy (H → 0):** Single pattern dominates (collapse risk)

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.5 | Diversity index alert threshold (0.0–1.0) |
| `--history` | `-H` | false | Show entropy history over time |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | `-j` | false | Output as JSON |
| `--record` | `-r` | false | Record current entropy to history |

```bash
mzt patterns-entropy
mzt patterns-entropy --threshold 0.3
mzt patterns-entropy --history
mzt patterns-entropy --record
```

---

### `mzt patterns-budget`

Display exploration budget status and history. The budget adjusts based on pattern entropy: low entropy boosts the budget to inject diversity; healthy entropy decays toward floor (default 5%).

```
Usage: mzt patterns-budget [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--job` | `-j` | | Filter by specific job hash |
| `--history` | `-H` | false | Show budget adjustment history |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | | false | Output as JSON |

```bash
mzt patterns-budget
mzt patterns-budget --history
```

---

### `mzt learning-stats`

View global learning statistics. Shows summary including execution counts, pattern counts, and effectiveness metrics.

```
Usage: mzt learning-stats [OPTIONS]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--json` | `-j` | Output as JSON |

```bash
mzt learning-stats
mzt learning-stats --json
```

---

### `mzt learning-insights`

Show actionable insights from learning data including output patterns, error code patterns, and success predictors.

```
Usage: mzt learning-insights [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--limit` | | 10 | Maximum patterns to show |
| `--pattern-type` | | | Filter by type |

```bash
mzt learning-insights
mzt learning-insights --pattern-type output_pattern
mzt learning-insights --limit 20
```

---

### `mzt learning-drift`

Detect patterns with effectiveness drift. Drift is calculated by comparing pattern effectiveness in the last N applications vs the previous N.

```
Usage: mzt learning-drift [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.2 | Drift threshold (0.0–1.0) to flag patterns |
| `--window` | `-w` | 5 | Window size for drift comparison |
| `--limit` | `-l` | 10 | Maximum patterns to show |
| `--json` | `-j` | false | Output as JSON |
| `--summary` | `-s` | false | Show only summary statistics |

```bash
mzt learning-drift
mzt learning-drift --threshold 0.15
mzt learning-drift --summary
```

---

### `mzt learning-epistemic-drift`

Detect patterns with epistemic drift (belief/confidence changes). Epistemic drift tracks confidence changes over time, complementing effectiveness drift as a leading indicator of pattern health.

```
Usage: mzt learning-epistemic-drift [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.15 | Epistemic drift threshold (0.0–1.0) |
| `--window` | `-w` | 5 | Window size for drift comparison |
| `--limit` | `-l` | 10 | Maximum patterns to show |
| `--json` | `-j` | false | Output as JSON |
| `--summary` | `-s` | false | Show only summary statistics |

```bash
mzt learning-epistemic-drift
mzt learning-epistemic-drift --threshold 0.1
mzt learning-epistemic-drift --summary
```

---

### `mzt learning-activity`

View recent learning activity and pattern applications.

```
Usage: mzt learning-activity [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--hours` | `-h` | 24 | Show activity from the last N hours |
| `--json` | `-j` | false | Output as JSON |

```bash
mzt learning-activity
mzt learning-activity --hours 48
```

---

### `mzt entropy-status`

Display entropy response status and history. When pattern entropy drops below threshold, the system automatically boosts exploration budget and revisits quarantined patterns.

```
Usage: mzt entropy-status [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--job` | `-j` | | Filter by specific job hash |
| `--history` | `-H` | false | Show entropy response history |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | | false | Output as JSON |
| `--check` | `-c` | false | Check if entropy response is needed (dry-run) |

```bash
mzt entropy-status
mzt entropy-status --history
mzt entropy-status --check
```

---

## Conductor Commands

### `mzt start`

Start the Marianne conductor.

```
Usage: mzt start [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to conductor config file |
| `--foreground` | `-f` | false | Run in foreground (for development) |
| `--log-level` | `-l` | `info` | Logging level |
| `--profile` | `-p` | | Daemon operational profile: `dev`, `intensive`, `minimal`. Overrides config file defaults. |
| `--conductor-clone` | | | Start a clone conductor; use `--conductor-clone=` for the default clone or `--conductor-clone=NAME` for a named clone |

```bash
# Start in background (production)
mzt start

# Start in foreground (development)
mzt start --foreground

# Custom config
mzt start --config /etc/marianne/daemon.yaml

# Debug logging
mzt start --log-level debug
```

**Supersede:** A live PID is probed before "already running" is decided. A conductor that is serving is reported as already running (exit 0). A conductor that is draining (graceful shutdown, up to `shutdown_timeout_seconds`) is superseded instead: `mzt start` waits up to 30s for it to exit, then starts a fresh conductor. It never spawns a second conductor beside a draining one, which holds the PID-file flock and socket lock for its whole life. If the drain exceeds 30s, start exits 1. A silent probe is conservatively treated as running (GH #455: slow is not dead).

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Conductor started, or a conductor was already serving (its PID is printed). A process supervisor must treat the second case as success: with `Restart=on-failure` an exit 1 here restarted `mzt start` every 30 s against a hand-started conductor (GH #459). |
| 1 | Configuration error, the conductor failed to start, or a draining conductor did not exit within the 30s supersede budget |

---

### `mzt stop`

Stop the running conductor. Non-blocking by default: after the safety check (and any confirmation) the signal is sent and the command returns without waiting for the conductor to exit. If scores are actively running, warns and asks for confirmation before proceeding. Stopping the conductor while scores run can orphan active agents and corrupt score state.

```
Usage: mzt stop [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--pid-file` | | `~/.config/mzt/mzt.pid` | Path to PID file |
| `--force` | | false | Skip safety check, send SIGKILL instead of SIGTERM |
| `--wait` | | | Wait for the conductor to exit after signaling. `--wait=` (equals sign, empty value) waits with the default 30s budget; `--wait=SECONDS` (or `--wait SECONDS`) sets a custom budget. Budgets must be finite and non-negative; `--wait=0` keeps the signal-and-return behavior. |
| `--conductor-clone` | | | Stop a clone conductor; use `--conductor-clone=` for the default clone or `--conductor-clone=NAME` for a named clone |

**Safety guard:** When scores are running, `mzt stop` probes the conductor via IPC to check for active scores. If any are found, it warns with the count and asks for confirmation. The `--force` flag bypasses this check entirely and sends SIGKILL.

After signalling, the command lists the in-flight sheets the graceful drain will wait on (`<job> sheet <n> on <instrument>/<model> for <elapsed>s`); a boundary-paused job's current sheet is among them until it ends. With a positive `--wait` budget, the command blocks until the conductor exits (prints `Marianne conductor stopped`) or exits 1 with `did not exit within <budget>s. It is still draining.` followed by the sheets a fresh probe says it is still waiting on. A graceful drain can take up to `shutdown_timeout_seconds` (default 300s) plus teardown while running jobs checkpoint — a `--wait` budget shorter than that is a bounded poll, not a guarantee.

```bash
# Normal stop (warns if jobs running; signals and returns immediately)
mzt stop

# Force stop (SIGKILL, no safety check)
mzt stop --force

# Block up to the default 30s for the conductor to exit
mzt stop --wait=

# Block up to 60s
mzt stop --wait=60
```

---

### `mzt restart`

Restart the conductor after the same active-score safety check used by `mzt stop`.

`mzt restart` first runs the stop path. If that path refuses to proceed because active scores are running, restart aborts before starting a new conductor. Pause or finish active scores before restarting.

```
Usage: mzt restart [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to conductor config file |
| `--foreground` | `-f` | false | Run in foreground (for development) |
| `--log-level` | `-l` | `info` | Logging level |
| `--pid-file` | | `~/.config/mzt/mzt.pid` | Path to PID file |
| `--profile` | `-p` | | Daemon operational profile: `dev`, `intensive`, `minimal`. Overrides config file defaults. |
| `--conductor-clone` | | | Restart a clone conductor; use `--conductor-clone=` for the default clone or `--conductor-clone=NAME` for a named clone |

```bash
mzt restart
mzt restart --foreground
```

---

### `mzt conductor-status`

Check conductor status via health probes.

```
Usage: mzt conductor-status [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--pid-file` | | `~/.config/mzt/mzt.pid` | Path to PID file |
| `--socket` | | `~/.config/mzt/mzt.sock` | Path to Unix socket |
| `--json` | | false | Emit the raw `daemon.status` payload as JSON |

```bash
mzt conductor-status
```

When the socket answers, the Jobs block shows the running count and, when
any musician is still executing, `In flight: <n> sheet(s)` with one line per
sheet (job, sheet, instrument/model, elapsed) — the rows a graceful drain
would wait on, including a boundary-paused job's unfinished sheet (GH #476).
The Resources block shows memory, processes,
backpressure, **event loop lag** (how late the loop wakes from a one-second
sleep, with the 60-second peak) and **host I/O pressure** (`/proc/pressure/io`
`some`/`full` ten-second averages). When the socket does not answer but the
PID file names a live process, the command says so (`PID n is alive but its
socket did not answer: busy or unresponsive`), prints the host I/O pressure,
tells you to wait rather than restart, and exits 1; with `--json` it emits
`{"pid", "alive", "socket_answered": false, "running_state": "unknown",
"io_pressure"}`. Clone mode (`--conductor-clone`) reads the clone's own PID
file for this; it never touches the default one (GH #455).

---

### `mzt clear-rate-limits`

Clear stale rate limits on instruments. When a backend rate limit expires but the conductor still has it cached, sheets may stay blocked unnecessarily. This command clears the cached limit so dispatch resumes immediately.

Clears both the rate limit coordinator (used by the scheduler) and the baton's per-instrument state (used by the dispatch loop).

```
Usage: mzt clear-rate-limits [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--instrument` | `-i` | | Clear rate limit for a specific instrument only |
| `--json` | `-j` | false | Output result as JSON |

```bash
# Clear all rate limits
mzt clear-rate-limits

# Clear rate limit for a specific instrument
mzt clear-rate-limits -i claude-cli

# JSON output for scripting
mzt clear-rate-limits --json
```

**When to use:**
- A rate limit has expired but sheets are still WAITING
- You want to force a retry after a rate limit event
- Troubleshooting rate limit behavior during development

---

## Configuration Reference

Score configurations are defined in YAML files. Here are the key sections:

### Minimal Example

```yaml
name: "my-score"
workspace: "./workspace"
instrument: claude-code
instrument_config:
  timeout_seconds: 1800
sheet:
  size: 1
  total_items: 5
prompt:
  template: |
    Stage {{ sheet_num }}: Process item {{ sheet_num }} of {{ total_sheets }}.
validations:
  - type: file_exists
    path: "{workspace}/output-{sheet_num}.md"
```

### Full Example

```yaml
name: "full-example"
description: "Comprehensive job example"
workspace: "./my-workspace"

# Run `mzt instruments list` for options.
instrument: claude-code
instrument_config:
  timeout_seconds: 1800

sheet:
  size: 1
  total_items: 10

prompt:
  template: |
    Stage {{ sheet_num }} of {{ total_sheets }}: Process the next batch.
    Items {{ start_item }} through {{ end_item }}.
    {{ stakes }}
  stakes: "Be thorough and precise."

retry:
  max_retries: 3
  base_delay_seconds: 30
  exponential_base: 2.0

validations:
  - type: file_exists
    path: "{workspace}/output-{sheet_num}.md"
  - type: content_contains
    path: "{workspace}/output-{sheet_num}.md"
    pattern: "## Summary"
  - type: file_modified
    path: "{workspace}/output-{sheet_num}.md"
  - type: command_succeeds
    command: "wc -w {workspace}/output-{sheet_num}.md | awk '$1 >= 500'"

notifications:
  - type: desktop
    on_events: [job_complete, job_failed]
```

> **Legacy syntax:** The `backend:` block was removed (#347) — scores containing
> one fail at parse time with "Unknown field 'backend'". See
> [Migrating from `backend:` to `instrument:`](score-writing-guide.md#migrating-from-backend-to-instrument).

### Template Variables

Core variables available in all prompt templates (from `SheetContext.to_dict()`):

| Variable | Description |
|----------|-------------|
| `{{ sheet_num }}` | Current sheet number (1-based) |
| `{{ total_sheets }}` | Total number of sheets |
| `{{ workspace }}` | Workspace directory path |
| `{{ previous_outputs }}` | Dict of previous sheet outputs (when `cross_sheet` is configured) |
| `{{ start_item }}` | First item number for this sheet |
| `{{ end_item }}` | Last item number for this sheet |
| `{{ stage }}` / `{{ movement }}` | Current stage (movement) number. Same as `sheet_num` without fan-out. |
| `{{ instance }}` / `{{ voice }}` | Fan-out instance (voice) number (1 for non-fan-out) |
| `{{ fan_count }}` / `{{ voice_count }}` | Total fan-out instances (voices) for this stage (1 for non-fan-out) |
| `{{ total_stages }}` / `{{ total_movements }}` | Total logical stages (movements). May differ from `total_sheets` with fan-out. |

### Instruments

Run `mzt instruments list` to see all available instruments. Built-in instruments:

| Instrument | Kind | Description |
|------------|------|-------------|
| `claude-code` | CLI | Claude Code CLI (default) |
| `gemini-cli` | CLI | Google Gemini CLI |
| `codex-cli` | CLI | OpenAI Codex CLI |
| `cline-cli` | CLI | Cline CLI |
| `aider` | CLI | Aider pair programming |
| `goose` | CLI | Block's Goose agent |
| `ollama` | HTTP | Local models through Ollama's OpenAI-compatible endpoint |

Retired backend-era names do not resolve. Update old scores to a current CLI
profile, or use `ollama` when a local OpenAI-compatible text endpoint is the
right execution surface.

### Validation Types

| Type | Description |
|------|-------------|
| `file_exists` | Check for expected output files |
| `file_modified` | Verify file was updated during sheet |
| `content_contains` | Check for literal string in file content |
| `content_regex` | Match regex patterns in file content |
| `command_succeeds` | Execute shell commands as quality checks |
| `path_in_scope` | Verify a path resolves inside an allowed root |
| `field_match` | Compare JSON/YAML fields to literals or source fields |
| `file_sha256` | Verify a file matches a pinned SHA-256 digest |
| `csv_unique_key` | Verify a CSV key column has unique values |

### Error Codes

Marianne classifies every execution failure into a structured error code. Use
`mzt errors <job> --code E001` to filter by code. The error code determines
retry behavior, delay timing, and severity.

**E0xx — Execution Errors** (process-level failures)

| Code | Name | Retriable | Severity | Delay |
|------|------|-----------|----------|-------|
| E001 | EXECUTION_TIMEOUT | Yes | ERROR | 60s |
| E002 | EXECUTION_KILLED | Yes | ERROR | 30s |
| E003 | EXECUTION_CRASHED | No | CRITICAL | — |
| E004 | EXECUTION_INTERRUPTED | No | ERROR | — |
| E005 | EXECUTION_OOM | No | CRITICAL | — |
| E006 | EXECUTION_STALE | Yes | WARNING | 120s |
| E009 | EXECUTION_UNKNOWN | Yes | ERROR | 10s |

E006 (stale detection) fires when an agent produces no output for longer than
`stale_detection.idle_timeout_seconds`. It is distinct from E001 (backend timeout)
— stale detection kills agents that go silent, while backend timeout caps total
execution time.

**E1xx — Rate Limit / Capacity**

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E101 | RATE_LIMIT_API | Yes | 1 hour |
| E102 | RATE_LIMIT_CLI | Yes | 15 min |
| E103 | CAPACITY_EXCEEDED | Yes | 5 min |
| E104 | QUOTA_EXHAUSTED | Yes | Dynamic |

**E2xx — Validation Errors** (output didn't meet acceptance criteria)

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E201 | VALIDATION_FILE_MISSING | Yes | 5s |
| E202 | VALIDATION_CONTENT_MISMATCH | Yes | 5s |
| E203 | VALIDATION_COMMAND_FAILED | Yes | 10s |
| E204 | VALIDATION_TIMEOUT | Yes | 30s |

**E3xx — Configuration** (not retriable — requires user fix)

| Code | Name | Description |
|------|------|-------------|
| E301 | CONFIG_INVALID | Invalid score YAML; also an API refusal of the request itself (a 4xx other than 429 reported by the CLI, e.g. a `response_format` schema the API will not take as a tool `input_schema`) — the CLI's message is kept as the attempt's error and stderr tail (GH #486) |
| E302 | CONFIG_MISSING_FIELD | Required field missing |
| E303 | CONFIG_PATH_NOT_FOUND | Referenced path doesn't exist |
| E304 | CONFIG_PARSE_ERROR | YAML syntax error |

**E5xx — Backend** (instrument/connection issues)

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E501 | BACKEND_CONNECTION | Yes | 30s |
| E502 | BACKEND_AUTH | No | — |
| E503 | BACKEND_RESPONSE | Yes | 15s |
| E504 | BACKEND_TIMEOUT | Yes | 60s |
| E505 | BACKEND_NOT_FOUND | No | — |

**E9xx — Network / Unknown**

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E901 | NETWORK_CONNECTION_FAILED | Yes | 30s |
| E999 | UNKNOWN | Yes | 30s |

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MZT_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | |
| `MZT_LOG_FILE` | Path for log file output | |
| `MZT_LOG_FORMAT` | Log format: `json`, `console`, or `both` | |
| `MZT_AUTH_MODE` | Dashboard auth: `disabled`, `api_key`, `localhost_only` | `localhost_only` |
| `MZT_API_KEYS` | Comma-separated API keys for dashboard auth | |
| `MZT_LOCALHOST_BYPASS` | Allow localhost to bypass API key auth | `true` |
| `MZT_PUBLIC_ORCHESTRA` | Serve `/orchestra`, `/api/orchestra/*` and `/static` to every client whatever the auth mode (the public fleet view, GH #468); every other path keeps the mode's rule | `false` |
| `MZT_CORS_ORIGINS` | Comma-separated allowed CORS origins | `http://localhost:8080,http://127.0.0.1:8080` |
| `MZT_CORS_CREDENTIALS` | Allow credentials in CORS requests | `true` |
| `MZT_DEV` | Enable development mode (permissive CORS) | |
| `ANTHROPIC_API_KEY` | API key for Anthropic API backend | |

---

## Tips

### Verbose Mode

Use `-v` for detailed output:
```bash
mzt -v run job.yaml
```

Shows:
- Backend configuration
- Sheet execution details
- Validation results
- Timing information

### Quiet Mode

Use `-q` for minimal output:
```bash
mzt -q run job.yaml
```

Shows only:
- Errors
- Final status

### JSON Output for Scripts

Combine `--json` with `jq` for scripting:
```bash
# Get job status
mzt status my-job --json | jq '.status'

# Get failed sheet numbers
mzt status my-job --json | jq '.sheets | to_entries[] | select(.value.status == "failed") | .key'
```

### Keyboard Shortcuts

During execution:

| Key | Action |
|-----|--------|
| `Ctrl+C` | Graceful shutdown (saves state) |
| `Ctrl+C` (x2) | Force quit |

### Debugging Protocol

When a job fails, follow this order:

```bash
# 1. Check current status (routes through conductor)
mzt status my-job

# 2. Get full diagnostic report
mzt diagnose my-job

# 3. View error history
mzt errors my-job --verbose

# 4. Check logs
mzt logs my-job --level ERROR

# 5. Try recovery (re-validate without re-execute)
mzt recover my-job --dry-run
```
