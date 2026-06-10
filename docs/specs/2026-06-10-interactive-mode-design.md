# Interactive Mode — tmux-driven instrument sessions

Status: FINAL (lab-validated). 4-model thinking lab 2026-06-10, 4-0 on the
tmux substrate; review archive: `~/lab-archives/2026-06-10-interactive-mode/`.

## Problem

Marianne executes every sheet through single-pass headless mode (`claude -p`,
`opencode run`, …). That leaves capability on the table:

- Some agent CLIs are richer interactively (hooks, plan mode, session
  continuity); some have no usable headless mode at all.
- One-shot execution has no mid-flight steering. When an agent stops early
  ("I'll continue in the next message…"), the attempt is dead and retry
  restarts from zero context. Interactive mode pushes the *same session*
  forward.
- The spec'd conductor-agent future needs a substrate: a live agent session
  another intelligence can observe and steer.

V1 ships a deterministic driver (busy/idle detection + nudges + completion
protocol). V2 reserves a seam for model-driven steering; V3 is the conductor
agent as a continuation policy.

## Mechanism: tmux (lab verdict 4-0)

An isolated tmux server (`tmux -L marianne`) hosts one detached session per
interactive sheet attempt. Why tmux over raw PTY or per-instrument
programmatic modes:

- Agent CLIs are alternate-screen TUIs; state detection needs a *rendered
  screen*. `capture-pane -p` provides it; a raw PTY would require a terminal
  emulator dependency (pyte) for a worse result.
- The composer can `tmux -L marianne attach -t <session>` and watch a
  musician work live (debuggability, goal rank 3).
- Spike-verified (2026-06-10, Claude Code v2.1.172, tmux 3.4): the pane
  process IS the agent (pid == pgid == session leader), group-kill reaps the
  whole tree including MCP children, `kill-session` teardown is reliable,
  buffer-paste delivers multiline prompts cleanly, and `capture-pane`
  returns plain rendered text (no ANSI in non `-e` mode).
- No new Python dependencies; tmux is an external binary, health-checked
  (minimum version 3.2, parsed from `tmux -V`).

Per-instrument programmatic transports (e.g. Claude's stream-json) are a
future per-profile optimization behind the same driver-facing interface, not
a V1 alternative.

## Architecture

New package `src/marianne/execution/instruments/interactive/`:

| Module | Contents |
|---|---|
| `tmux.py` | `TmuxControl` — async wrapper over the tmux binary. **Every command runs with a short per-command timeout (10s)**; timeout/failure raises a structured `TmuxError` the driver treats as session-lost. `kill_server` exists for tests only — never called in production. |
| `driver.py` | `InteractiveSessionDriver` state machine + `ContinuationPolicy` Protocol + `StaticNudgePolicy`. All per-attempt state is local to `run()` — never instance attributes (free-list reuse safety). |
| `backend.py` | `InteractiveCliBackend(Backend)` — maps driver outcomes to `ExecutionResult`. |

### Launch correctness (lab P0)

`new_session` passes the agent command as an **explicit argv tail**
(`tmux new-session -d -s <name> ... -- <exe> <arg> <arg>`), never a joined
shell string. The integration suite asserts the spike property
(pane_pid == its own pgid, children share the pgid) on the *implementation's*
launch path, with a guard refusing to proceed if the pane pgid equals the
daemon's.

Session naming: `mzt-{job}-s{sheet}-a{attempt}` (sanitized, length-capped).
Deterministic names enable: kill-same-name before launch, kill `mzt-{job}-*`
at deregister, and an orphan sweep at daemon startup (any `mzt-*` session on
the marianne socket without a live job is killed, with a warning log).

**Socket isolation (live-smoke finding)**: the default socket name honours
the `MARIANNE_TMUX_SOCKET` env var (`tests/conftest.py` sets a per-run test
socket). Without this, any test exercising daemon startup runs the orphan
sweep against the production `marianne` socket and kills a live conductor's
interactive sessions mid-flight — observed when the test suite ran
concurrently with a live interactive job.

### Config

**Profile side** (`CliProfile.interactive: InteractiveCliConfig | None`,
default None — additive; `extra="forbid"` requires the explicit field):

```yaml
cli:
  interactive:
    extra_args: []                  # appended after auto_approve/model flags
    startup_gates:                  # ordered; each fires at most once;
      - pattern: "Do you trust the files"   # skipped once ready_pattern
        keys: ["Enter"]                     # has matched (misfire guard)
    ready_pattern: '(?m)^\s*❯'
    busy_patterns: ['esc to interrupt', '…\s*\(\d+m?\s?\d*s']
    quiet_seconds: 15.0
    poll_interval_seconds: 2.0
    startup_timeout_seconds: 90.0
    terminal_width: 200
    terminal_height: 50
    volatile_tail_lines: 2          # status lines excluded from change hash
```

Interactive is **opt-in per verified instrument**: `interactive: true` on a
profile with no `cli.interactive` block is a structured config error at
backend creation (and a pre-execution validation failure where wired).
Builtins gain the block only with spike-verified patterns (claude-code in
V1); no speculative gates.

**Job side** (flat keys in `instrument_config`, matching the existing flat
style and the merge chain in `core/sheet.py`):

```yaml
instrument_config:
  interactive: true
  interactive_max_nudges: 5          # consecutive-idle budget
  interactive_nudge_message: "..."   # optional override
```

### Driver state machine

```
LAUNCH → GATES → READY → SUBMIT → DRIVE ⟲ → HARVEST (exactly once) → CLEANUP
```

- **LAUNCH**: kill same-name session; create session (cwd=workspace,
  fixed terminal size); start `pipe-pane -o` raw log (debug artifact,
  outside the workspace, size-capped — pipe is toggled off if the log
  exceeds the cap); resolve pane_pid; register via
  `_on_process_group_spawned(pane_pid, pane_pid)`.
- **GATES**: poll screen; fire matching gates in order, each at most once;
  gates are skipped once `ready_pattern` has matched (misdirected-Enter
  guard). Bounded by `startup_timeout_seconds` → structured failure with
  final-screen tail.
- **SUBMIT (verified — live-smoke finding)**: delete this attempt's
  completion marker if present; paste the prompt via a **per-session named
  buffer** (`load-buffer -b` a UTF-8 temp file → `paste-buffer -p -d`).
  Enter can be LOST when it arrives while the TUI is still ingesting a
  large bracketed paste (observed live: the full prompt sat unsubmitted in
  the input box while the agent received only the later nudge). So: settle
  ~1s after the paste, send Enter, then **verify the submission took** (a
  busy pattern appears, or the input prompt returns bare) and re-send
  Enter when unverified — an extra Enter on an empty input box is a no-op,
  bounded at 3 attempts. Nudges use the same verified-submit path. The
  prompt carries a completion protocol suffix naming the exact marker path.
- **Completion marker (lab P0 — per-attempt, not workspace-global)**:
  `{workspace}/.marianne/interactive/{job}/s{sheet}-a{attempt}.complete`.
  Marker existence = "agent claims done" — a protocol signal, NOT semantic
  success. **Validations remain the authoritative arbiter**; interactive
  sheets without validations draw a config warning.
- **DRIVE** (poll each `poll_interval_seconds`, deadline = sheet timeout):
  - *busy* = any `busy_patterns` match (primary signal). A change in the
    work-area hash (screen minus the last `volatile_tail_lines` lines)
    resets the quiet clock but never alone means busy.
  - *done* = marker exists → HARVEST.
  - *dead* = session/pane gone → HARVEST (classify).
  - *idle* = no busy match + work area stable ≥ `quiet_seconds` → consult
    the continuation policy → nudge. Consecutive-idle counter resets when
    the agent transitions back to busy (busy-pattern-verified); a separate
    total-nudge hard cap (5× the consecutive budget) prevents infinite
    nudge-work-nudge loops. Budgets exhausted → HARVEST (plain failure).
- **HARVEST** (exactly once per execute): final `capture-pane` (plain
  rendered text; resilient to failure on a dead session — pipe-pane log
  remains as the debug artifact), nudge/timing stats.
- **CLEANUP** (`finally`, idempotent): kill-session, delete temp prompt
  file, fire `_on_process_exited`.

### ExecutionResult mapping

| Outcome | Mapping |
|---|---|
| Marker observed | `success=True, exit_code=None`; stdout = final screen text |
| Nudge budget exhausted | `success=False, exit_code=None, error_type=None`, message describes nudges (plain failed attempt — normal retry/validation flow; lab 3-1) |
| Sheet deadline | `success=False, exit_reason="timeout"` |
| Session lost / startup failure | `success=False`, narrow classification (below), screen tail in message |
| Tokens | `input_tokens=output_tokens=None` → existing `cost_uncertain` machinery (#346); never fabricated |

**Error/rate-limit classification (lab 3-1)**: the transcript is
agent-contaminated and there is no stderr — GH#189 applies with extra force.
Only the **final rendered screen** is scanned, only on non-completion, only
with the profile's vendor-owned patterns. Prefer unknown-failure over false
rate-limit (a false positive burns the fallback chain; a miss just retries).

### Lifecycle integration

- pane_pid registered through the existing `_on_process_group_spawned` →
  `_active_pids`, liveness probes, and `_kill_active_pgroups` work
  unchanged. Pgroup-kill and driver `kill-session` race benignly (both
  idempotent; `_on_process_exited` may fire after the `_active_pids` entry
  is gone — tolerated today).
- **Stale detection (lab unanimous correction)**: interactive sheets are
  recorded in a per-job `_interactive_sheets` collection at dispatch; the
  stale check's **workspace-mtime idle-kill path is skipped** for them (the
  driver owns idle handling; two independent kill-deciders would race).
  The dead-task detection path stays active. The collection is cleaned at
  deregister and added to `test_all_collections_cleaned`.
- **BackendPool**: `acquire(..., interactive=...)`; interactive instances
  live in a separate free-list key (`{name}:interactive`). The adapter
  passes attempt identity (`set_attempt_identity(job_id, sheet_num,
  attempt)`) after acquire — mirrors the existing post-acquire callback
  wiring. Release-time `clear_overrides()` plus locals-only per-attempt
  state make reuse inherently clean; `close()` kills any leftover session.
- Daemon crash mid-sheet: the tmux session survives (server isn't our
  child) — V1 residue, reaped by the startup orphan sweep + the
  kill-same-name guard on re-dispatch.

### Out of scope for V1

Model-driven continuation (V2: `ModelNudgePolicy` via the reserved
`ContinuationPolicy` Protocol — context carries only nudge_count/elapsed
until a real consumer exists), the conductor agent (V3), session adoption
across daemon restarts, stream-json transport, TUI token scraping, HTTP
instruments.

## Test plan (lab-amended)

Unit: TmuxControl argv construction + per-command timeout (hung-subprocess
mock); driver state machine against a scripted FakeTmux (gates, busy→idle,
done-marker, dead-pane, startup-timeout, nudge-reset-on-progress, total-cap,
volatile-idle screen — clock churn in the excluded tail must not hang idle
detection); ExecutionResult mapping; config models; marker isolation
(concurrent sheets, pre-existing markers, premature marker → validations
arbitrate); pool free-list separation; `test_all_collections_cleaned`
update.

Integration (skipped without tmux ≥ 3.2): real tmux + scripted fake agent
(bash REPL printing a `❯` prompt, simulating busy, responding to a nudge,
writing the marker): full happy path; **launch-path process identity**
(pane_pid == pgid, children in group); cancel during each phase (LAUNCH /
GATES / SUBMIT / DRIVE / CLEANUP) → session gone, `_active_pids` clean,
result reported; concurrent sheets on the shared server (session + buffer
isolation); unicode prompt delivery.

Live smoke (operator-run, not CI): one real claude-code interactive sheet
via a dev score.
