# Changelog

All notable changes to Marianne AI Compose will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Prompt-cache token counts, per sheet and per job (token-efficiency commission, 2026-11-10).**
  Profiles gain `cli.output.cache_read_tokens_path` and `cache_write_tokens_path` (set on the
  claude-code, claude-code-ollama and codex-cli builtins); the backend reads them from JSON and
  JSONL output, the attempt event and the checkpoint sheet carry `cache_read_tokens` /
  `cache_write_tokens`, every sheet sums its attempts into `total_*_tokens`, and `mzt status`
  shows a `Cache read` line with the share of input served from the cache (`--json`:
  `cost.total_cache_read_tokens`, `total_cache_write_tokens`). On Anthropic and OpenAI
  `input_tokens` excludes cache reads, which were 88% of what the provider processed on one
  23-turn Claude Code sheet; Marianne had been recording the other 12%. The job-level token
  totals were also never summed before, so status read 0; they are now derived from the sheets.
  Instrument guide, CLI reference, expert profile contract and command skill carry the words.

- **`stale_detection.watch_paths` (GH #516).** Idle detection measured activity only as
  writes under the sheet's workspace, so a musician building in the project's git
  worktree or a scratch clone was killed by the 3-strike backstop while writing code
  (two fleet members, 2026-11-09, 5,469 s "idle" with files landing every minute).
  Directories named in `watch_paths` (with `~` and `{{ workspace }}`/`{{ project_root }}`
  templates) now count as activity; the liveness probe and backstop are unchanged.

### Changed
- **The skills say what V228 means.** The validator and the configuration reference already said
  that a loop's `cost_limit_usd` cannot stop a route priced at $0; no skill said what a composer
  should do when the warning fires. The composing and score-authoring skills now do: on such a
  venue `max_iterations` is the only working cap, so set it to the passes the work justifies and
  keep `cost_limit_usd` for venues that price the route (`artifact-analysis` trips it on a
  subscription venue by design).
- **A maker/judge pair is cast as two explicit chains in opposite orders, never a class.**
  `spread` separates voices within one alias; it cannot keep an analyst and its reviewer apart,
  and a capability class on either seat resolves per venue (the first live run of
  `artifact-analysis` seated both on codex-cli through a user class layer, and the release gate
  refused it). The score-writing guide, the composing skill and the score-reviewer checklist now
  say so; the release gate's receipt comparison stays as the last line.
- **The composing release gate executes the first sheet and measures suite runtimes.**
  Every static gate (`mzt validate --strict`, the release checker with a lock, the
  orchestra gate) passed nine fleet scores on 2026-11-09 and three failed at runtime
  within the first hour: a required injection over its `max_bytes`, two one-hour caps
  on `cli` stages whose full product suite measured 68 minutes at the fleet's
  parallelism, and a prepare check that assumed a branch. The composing skill and the
  score-reviewer agent now require a first-sheet dry run in a fresh workspace with the
  real injections, and a measured (not estimated) runtime for any sheet that runs a
  whole test suite. GH #513, #515.

### Added
- **`instrument_config.first_output_timeout_seconds`: per-sheet tri-state override of the CLI first-output silence bound (GH #502).** The profile-level bound (GH #434) ends an attempt that produces zero stdout/stderr bytes within N seconds as a `Stale execution` stall — correct for a streaming agent, wrong for a sheet that is legitimately slow to first byte (cold caches, backup or IO pressure). The key now works at all four instrument override scopes (score, movement, `instruments.<alias>.config`, `sheet.per_sheet_instrument_config` — an alias is applied before a per-sheet override) and is tri-state like `response_format`: omitted inherits the profile bound, a finite positive number widens or narrows it for that scope, and `null` disables it for that sheet without touching the shared profile or other concurrent sheets. The sheet timeout always caps execution — an override larger than it (or a disabled bound) never extends the attempt — and once any stdout or stderr byte arrives the bound is inert and only the sheet timeout governs. Invalid values (booleans, zero, negatives, nonfinite, non-numeric) are refused at both YAML load and adapter dispatch by one shared coercion helper (`coerce_first_output_timeout`), so YAML authors and direct `Sheet` callers cannot drift apart; the stale marker names the effective bound. CLI backends only: HTTP and interactive backends log `adapter.dispatch.first_output_timeout_unsupported` and ignore the key. `execution.base` gains the `FIRST_OUTPUT_TIMEOUT_UNSET` sentinel, the `FirstOutputTimeoutResolution` type, and the public `is_first_output_timeout_unset` predicate for downstream narrowing.

- **`examples/engineering/artifact-analysis.yaml`: the shipped `kind: mcp` score (GH #499 rung 2).**
  Characterizes a binary, bundle or package through REA over the conductor's pool. The
  analyst is the only sheet with the technique (`phases: ["2"]`, the movement number); it
  exports REA's own evidence bundle and closes the shared session. Every claim cites an
  `evidence_id` from that bundle. A reviewer on another model family judges from the bundle
  alone, never calling the tool (the two seats are explicit chains in opposite orders, so a
  venue's class layer cannot seat both on one model); the release sheet is a `cli` sheet that refuses an approval
  older than the claims, a claim the bundle cannot back, or a reviewer whose context receipt
  names the analyst's instrument. Fixture source under `examples/engineering/assets/`. The
  technique guide, pool guide, score-authoring and command skills point at it.

- **REA (Reverse Engineer Anything) support, first rung (GH #499).** A pool entry for
  REA's stdio MCP server is documented in the MCP pool guide (interpreter and entry
  script spelled out, open/close discipline for its shared analysis state), and
  `mzt doctor` gains a Providers section that runs REA's own read-only `rea doctor
  --json` for each such entry and reports its toolchain gaps in REA's words, under
  `provider_checks` in `--json`. Nothing installs; a gap is a warning. The technique
  guide's MCP section shows the `kind: mcp` technique that attaches the entry to the
  sheets that read artifacts, with the open/close discipline.

- **`mzt run --job-id NAME` (GH #505).** Names a run, so one shipped score can serve two
  commissions at once with no renamed copy: the job ID is NAME, and a score that omits
  `workspace:` gets `~/workspaces/NAME`. Without the flag nothing changes. The composing
  and command skills now say to run shipped scores as-is with `--var` inputs and
  `--job-id`, and to copy a score only when its behaviour must change.

- **spec-forge writes the stakeholder brief even when review caps out.** A repair loop that ends its last pass still at REWORK no longer fails the job on the triage sheet with the specs unread; the brief runs, opens with `## Not yet approved` and the blockers the last pass left, and its approval line says the design is NOT approved. The brief's own gate refuses the opposite mislabel in both directions (an approved design carrying the header; an unapproved one claiming approval). Found conducting the memory-primitive commission, whose three passes each resolved the previous pass's blockers and found the next layer.
- **Live context probe for local servers (GH #494).** llama-server and PolyStrata both report the loaded model's context as `default_generation_settings.n_ctx` on `GET /props`. The availability probe now reads it for a ready loopback `http` profile: `mzt instruments check` prints `Context: N tokens (server /props)` (`served_context` in `--json`) and warns when the profile's `context_window` for the default model is larger, and `mzt validate` reports the same mismatch as V234 for the score's chain, because the executor sizes `max_tokens` from the profile and the server refuses a request above its real context with HTTP 400 before generating. A server that does not expose `/props`, or reports 0 while loading, is not checked.
- **`claude-code-ollama`: Claude Code as a zero-cost local agent harness over Ollama (GH #85).** A new built-in `kind: cli` profile routes the Claude Code harness at a local Ollama server: it pins `ANTHROPIC_BASE_URL=http://localhost:11434`, `ANTHROPIC_AUTH_TOKEN=local`, and an empty `ANTHROPIC_API_KEY` in the child environment (filtering parent cloud-provider switches), so file edits, shell commands, and tool use run through `claude` against a local model instead of a raw HTTP prompt. Requires `claude` on PATH, a running `ollama serve`, and a pulled model (default `llama3.1:8b`; select another pulled tag such as `qwen3:8b` with `instrument_config.model`); the profile's availability probe checks only the `claude` binary — it does not start the server or pull models, and a ready probe does not establish server connectivity or model presence. It is seated second in the generated `local` and `cheap` class chains (`ollama` first; both are zero-cost), and `ollama` remains the raw OpenAI-compatible HTTP route. Transport was verified with Claude Code 2.1.319 against a local mock Anthropic-compatible server; real Ollama model tool calls remain unverified.

- **Long-tail Rosetta patterns get proof scores and a citation audit (GH #354).** Composers converged on the same ~7 patterns because only those had proof scores; six small offline proofs now cover Talmudic Page, Sugya Weave (Editorial Synthesis), Forward Observer, Stigmergic Workspace, After-Action Review and Screening Cascade (ledger: 19 primary-proven patterns, up from 13). Each proof bootstraps deterministic fixtures via the new sibling `tools/long_tail_proof.py`, runs in ~10 minutes with a configured role-class instrument chain, and needs no data downloads or publication credentials; gates bind outputs to input bytes (routing conservation, byte compression, run-2 learning consumption) and explicitly do not judge semantic quality or measure token billing. Pattern frontmatter gains an optional `proof_score:` pointer (a file under `proof-scores/`, relative), the composing skill directs pattern reads to consult it alongside the ledger, and `tools/unproven.py` gains `--usage [DIR]` (YAML pattern-name citation counts, once per file, case-insensitive; default scans the repository's `scores/` tree excluding the corpus's own proofs) with a `--check`/`--usage` mutually exclusive argparse CLI replacing hand-rolled argv parsing.
- **Movements can declare their output files with `produces:` (GH #384).** Explicit declarations, `sheet.stdout_to` targets and existing gated `file_exists` proxies now share one producer map for V108/V109 and the foreign-artifact guard. Explicit ownership takes precedence for the same path; fan-out declarations expand `{sheet_num}` per concrete voice. Declarations name expected outputs; output validations still check success.

- **Model-driven interactive continuation (GH #378): a cheap model reads the idle screen and writes the nudge.** The shipped static nudge is blind — it told a context-less agent (lost prompt) to "continue working" three times instead of reporting the agent never received instructions. `instrument_config.interactive_nudge_policy: model` (default stays `static`) opts an interactive sheet into `ModelNudgePolicy`: a one-off headless instrument (`interactive_nudge_instrument`, backed by the new public `BackendPool.create_one_off` seam so service calls don't consume a dispatch slot) gets the JSON-encoded screen as untrusted evidence plus elapsed time and nudge count, and answers `NUDGE: <input>` or `NUDGE: UNRECOVERABLE <reason>`; `interactive_nudge_timeout_seconds` (default 30, finite positive) bounds each call within the sheet deadline. Invalid or failed replies — timeouts, unparseable output, payloads over 2,048 chars, control characters, empty `UNRECOVERABLE` — fall back to the static `interactive_nudge_message`; an abstaining decision (`message=None`) re-polls without spending nudge budget; an unrecoverable decision stops nudging and leaves validations to judge the attempt. The driver now checks the completion marker after the policy call (a completion written while the model hung to the deadline is no longer returned as `timeout`) and refreshes the screen before acting on a decision, so a provider-error screen arriving mid-model-call takes precedence over the nudge and a resumed busy screen drops the stale idle decision. The three knobs work at all four instrument override scopes; a model policy with no configured instrument warns and stays static.
- **`sheet.stdout_to`: a sheet's full reply as a workspace file (GH #494).** An `http` profile answers but has no filesystem, so its reply reached later sheets only through `cross_sheet` capture and could never meet a `file_exists` or `content_regex` validation. `sheet.stdout_to: {1: design.md}` makes the musician write the whole stdout to that file (workspace-relative or `{workspace}/...`) before validations run; a fanned-out stage gets one file per voice, a failed attempt writes nothing, a path that escapes the workspace is refused with a preflight warning, V004 flags a home-absolute path and V214 a sheet outside the range. The local-server starter `mzt init` writes uses it.
- **Fan-out musician messaging through a scratch-owned file mailbox (GH #467).** Concurrent fan-out voices had no way to exchange notes during a run — no harness adapter reaches them and the conductor only sees per-sheet results. `sheet.fan_out_messaging: file` (default; `none` omits it; explicit use without concurrent voices warns V234) prepares one append-only JSONL inbox per sheet under `<workspace>/.marianne-scratch/mailbox/`, guarded by advisory locks and opaque, strictly-increasing byte-cursor IDs per inbox. Shell-capable instruments get `MARIANNE_MAILBOX`, `MARIANNE_SCRATCH`, and `MARIANNE_SHEET` exported, and the new `mzt mail send <sheet|all> "<body>"` / `mzt mail read [--since <id>]` subcommands deliver and poll messages (`all` targets every initialized inbox except the sender's own; the CLI refuses to guess a job or sheet context). The conductor's log records each send (sender, recipient, UTF-8 byte count; never the body) and a traffic summary, and retains them (body-free) in the job checkpoint at every terminal settlement — COMPLETED, FAILED, or CANCELLED — so a job that ended in an error, timeout, or cancellation still records every delivered send when the conductor investigates a failed musician; failed jobs keep their scratch (and inboxes) for post-mortem. `mzt diagnose` shows the traffic (sender, recipient, byte counts, per-inbox totals, first/last timestamps) and `--json` adds `mailbox_traffic`; `merge_traffic` reconciles retained and live metadata across resume without double-counting. Message bodies never enter logs, checkpoints, or diagnosis.

### Fixed
- **A reopened loop sheet no longer waits out its primary's rate limit while its fallbacks
  idle (GH #521).** `reset_for_flow` rewinds a reopened sheet to its primary, and dispatch
  treated a rate-limited primary as "wait for expiry" even when the sheet had a fallback,
  so a day-long quota timer parked a reviewer whose chain had three idle seats. Dispatch now
  advances the chain the way the in-flight rate-limit handler does
  (`baton.rate_limit.fallback_advanced`, `at: dispatch`); a sheet with no fallback, or
  whose whole chain is limited, still waits. Found by `artifact-analysis` pass 2.
- **The MCP pool bridge no longer dies silently on a catalog-sized reply (GH #520).** The
  upstream pipe was read with the default 64 KiB `StreamReader` limit; REA's `tools/list`
  is one 3.5 MB line, so the reader raised once, the process stayed alive, `is_running`
  stayed true, and every later request parked forever while the pool kept handing out the
  dead socket. The bridge now reads lines up to 64 MiB, and when its reader stops for any
  reason it answers every pending request with JSON-RPC error `-32000` naming the cause,
  closes its clients, reports not running, and lets the pool monitor restart it under
  `restart_policy` (`mcp_bridge.upstream_reader_stopped` in the log). Found by the first
  live run of `artifact-analysis`.

- **Compiler-emitted validators ground on THIS run, not on inherited artifacts (GH #508).**
  The score-count claim validator's second pattern was dead: inside the emitted
  rf-string `{0,24}` rendered as the tuple text `(0, 24)`, so "agent scores total
  nine" against an empty `scores/` was never caught. The cadenza-completion
  validator accepted any done task-board row whose evidence cell contained the
  artifact's relative path, so a row from an earlier engagement passed the check
  before this run wrote anything. The pattern is now `\D{0,24}`, and a row binds
  only when it names this run's absolute artifact or its workspace, or the relative
  path with an artifact no older than the board. Installed engagement scores need
  recompiling to pick this up (`mzt compile`, GH #498's precedent). Compiler tests
  execute both emitted bodies against a disk state that must fail and one that must
  pass.

- **`design-to-orchestra` and `spec-forge`: the pass cap fails the release sheet, not the verdict musician (GH #519).**
  The verdict sheet carried `test pass_no -lt 3 || test -f review/APPROVED`; a REWORK at
  the cap failed the attempt, the baton retried the sheet, and the retry (same inputs,
  another instrument) reclassified two confirmed HIGHs as "hardening" and approved.
  The check is gone from the judgment sheets; the release sheet already refuses an
  unapproved hand-over under `set -e` with no fallback, and spec-forge's brief opens
  "Not yet approved". The verdict validation also refuses an APPROVE that follows a
  REWORK of the same pass with no score changed since.

- **`design-to-orchestra` requires an independent oracle to pin every assertion input (GH #518).**
  Two of nine generated scores froze a RED pin set over the test modules, acceptance map
  and runner only; `conftest.py`, assertion helpers and fixtures were unpinned, so an
  expected manifest could change after RED with every pinned test still green (reproduced
  by a reviewer on `orchestra-memory-rev5`). The composition prompt now names the full
  manifest, and the stage-3 gate refuses a support runner whose pin set never names
  conftests or fixtures. Composing skill, conducting reference and the design-to-score
  guide say the same.

- **`design-to-orchestra`'s gate no longer validates a composer's `composition-design.yaml` as a score (GH #517).**
  The composing skill asks for a design file before YAML; four composers wrote it beside
  their scores and the gate failed the pass on "not a Marianne score" while all nine real
  scores passed. The sidecar is excluded wherever `*.generated-inputs.yaml` already is.

- **`design-to-orchestra` and `spec-forge` no longer approve what one voice confirmed broken (GH #513).**
  The orchestra's stage-3 gate refuses a generated score whose `cli` verifier sits inside
  a repair loop with no `on_fail` goto (the engine ends such a loop `range_failed` on its
  first red run, so the "bounded repair loop" could never repair); a review that writes
  `NO FINDINGS` must list every score it read; and the verdict reads each voice's context
  receipt, records which instrument each ran on and how much it read, counts two voices
  on one model (or a voice that read a fraction) as one, and treats a confirmed
  single-voice HIGH that breaks a hard requirement as a blocker. `spec-forge`'s triage
  adopts the same rule. Composing skill, conducting reference and the design-to-score
  guide say the same.

### Fixed

- **Fleet members are named for their fleet (GH #514).** A member took the bare file
  stem as its job ID, so `integration.yaml` became job `integration`, collided with an
  unrelated September job, and auto-fresh replaced that job's record while
  `mzt status integration` showed the stale job as the member. Members are now
  `<fleet>--<stem>` through the same request field as `mzt run --job-id`, so two fleets
  with a member of one stem never share an ID or a workspace.

- **A required injection over its `max_bytes` is refused before dispatch (GH #515).**
  V334 is an ERROR for a `required` item (WARNING stays for an optional one), and the
  composing release checker measures every injection the sheet will use against its
  cap, so a lock cannot be written over a required file the #474 dispatch guard will
  refuse. Nine fleet scores had passed every static gate with such an item and one
  died at sheet 1. A fleet also no longer loses its dependent groups for good when a
  wave member fails: the dropped groups are recorded and shown in the fleet's status,
  and `mzt resume <fleet>` submits them once the failed member, resubmitted under its
  fleet ID, is complete.

- **A project venue class file the conductor never read is refused at submit (GH #510).**
  The conductor resolves `.marianne/classes.yaml` from its own working directory
  (`$HOME` under the shipped unit), so a checkout's venue file reached `mzt validate`
  but never dispatch. Submit now refuses a score that uses a class the file beside
  it redefines when the conductor read a different venue file, naming both paths,
  mirroring the venue-profile guard; `mzt validate --json` reports each venue layer
  with `resolved_from`. Configuration reference, instrument guide, daemon guide and
  the command skill say which working directory decides the venue layer.

- **Z.AI's "Weekly/Monthly Limit Exhausted" is a rate limit on every route (GH #509).**
  The built-in `claude-code` profile (headless patterns and interactive screen
  patterns) and `opencode` profile now recognise the Z.AI gateway's quota text
  ("Limit Exhausted", "temporarily limiting requests", "Your limit will reset at
  <ISO time>"), so the attempt waits or advances its fallback instead of burning
  retries or being nudged as an idle agent. `spec-forge` (stage 4) and
  `design-to-orchestra` (stage 3) now prove THIS pass on a repair: a part or score
  the previous verdict or gate named must be newer than the file that named it, so
  a musician that did no work cannot be rescued by the previous pass's files.
  The instrument guide says why a user-layer copy of a built-in must carry its
  pattern blocks whole.

- **`design-to-orchestra` no longer asks for an "absolute workspace" its own strict gate refuses (GH #511).**
  The composition prompt now spells the gate's rules (V004: `~` or template
  variables, never an expanded home path; V307: a `cli` sheet's script in
  `command`), so the first pass does not spend a repair pass on a contradiction
  in the score's own instructions.

- **A late rate-limit result no longer strands a sheet whose fallback already advanced.**
  When several sheets on one instrument hit a quota screen together, the first
  result's `RateLimitHit` advances every in-flight sibling to its fallback; each
  sibling's own late result then named the old instrument and parked the sheet
  WAITING on the new one, which nothing ever woke (`orchestra-memory-rev4`,
  2026-11-08: three composition sheets sat for hours). The baton now re-queues such
  a sheet PENDING on its advanced instrument, and a bare `mzt clear-rate-limits`
  (no instrument) sweeps any sheet WAITING on an unlimited instrument back to
  PENDING, so a strand left by an older conductor is cleared by the operator's
  existing command. A single instrument's expiry or clear still touches only that
  instrument's sheets.

- **`issue-triage`: a stale DAG on tail-sheet failure no longer masquerades as fresh (GH #503).**
  Triage runs in one workspace across labels, and when a tail sheet failed, the
  validations that re-ran on retry inherited files from the previous run against a
  stable workspace: the sheet-7 corpus-coverage gate matched issue numbers that no
  longer existed in the current corpus, and sheets 8-9 passed on a `issue-dag.yaml`
  and `07-ready.md` left over from the previous label. The DAG is now run-bound:
  sheet 8 archives any existing DAG under a unique timestamped name (a stale DAG is
  history, never input to reuse), writes this run's DAG to a per-label
  `issue-dag-{{ issue_label }}.yaml`, and atomically republishes the canonical
  `issue-dag.yaml` from the same bytes (temporary file + `os.replace`), while the
  solver keeps reading only the canonical name. Four new gates in
  `examples/engineering/issue-triage.yaml` bind the tail to the corpus rewritten by
  this run — triangulation references every corpus issue, the DAG's mtime is at
  least the corpus's with the run's exact label and issue-number set, and the
  `07-ready.md` manifest carries the run's label and corpus count — all written as
  quoted-heredoc Python that takes `{workspace}` and `{issue_label}` as `argv`
  (never spliced into string literals), pinned by replay tests
  (`tests/test_issue_triage_stale_gate.py`) that prove the stale direction refuses
  and the fresh direction accepts.
- **The conductor ceiling no longer fails a held job (GH #504).** `job_timeout_seconds`
  was documented as accumulated RUNNING time but enforced as wall-clock from
  activation, so a job PAUSED by the operator or holding a FERMATA for a composer's
  decision was failed at 24 h as "exceeded timeout" (the memory-primitive spec-forge,
  paused 23 h 54 m). A hold-aware watchdog now charges RUNNING intervals only; a
  score with a tick deadline (`schedule.max_wall_seconds`) keeps its absolute bound.
- **Intermittent "database is locked" on resume (GH #506).** The job registry had no
  busy timeout (Python's 5 s default) while the schedule registry held `BEGIN
  IMMEDIATE` across a custody fsync on the same state database. The job registry
  now sets an explicit 30 s busy timeout, and the schedule registry writes its
  custody witness before taking the writer lock, so neither starves the other.

- **Claude Code's weekly limit on stdout is a rate limit (GH #507).** The structured
  error scan now reads the `result` text of a Claude Code terminal object that says
  `is_error: true`, and the builtin profile's patterns accept "hit your weekly limit"
  and a `(UTC)` reset zone. Before, the refusal was retried as a transient E009 and a
  twelve-sheet job failed on its first sheet with no quota wait and no fallback.

- **The orchestra timeline's chain-refusal note is a fixed word, never the conductor's message (merge review of GH #468).** `job.chain_refused` beats projected the refusal text verbatim, and two refusal causes carry a resolved score path or raw exception text; the public beat log and replay JSONL now carry one of `already_active`, `depth_limit`, `not_found`, `cost_limit`, `unavailable`, `error`, and the free text stays in the daemon log. A test plants a path and an exception string and asserts neither reaches the projection.
- **A torn mailbox record no longer bricks the inbox (merge review of GH #467).** A sender killed mid-record left a last line without its newline; every later `mzt mail send` to that sheet then refused forever and readers stopped silently at the tear. The next append now truncates the unacknowledged tail under the exclusive lock and writes its record with one `os.write`; readers still never surface torn bytes.
- **`claude-code-ollama` ships as `execution_status: warning` (merge review of GH #85).** Its verification is a mock Anthropic-compatible seam, so the profile says so instead of defaulting to `ready`; the detail names the live check to run before relying on it.
- **`fan_out_messaging`'s advisory is V235 (merge of GH #467 onto main).** The branch had taken V234, which main already uses for the served-context warning; the merge keeps main's V234 and renumbers the messaging check.
- **A structured-output CLI's quota refusal is a rate limit, not a plain failure.** Codex `--json` reports "You've hit your usage limit" as the last JSONL record on stdout (`codex_error_info: usage_limit_exceeded`), exits 1, and writes nothing to stderr; the stderr-only scan (GH #189) saw a plain non-zero exit, the #344 rescue ran the sheet's validations, an inherited branch satisfied them, and the clamonacc proof's second iteration passed eight empty characterization passes in six seconds. For `json`/`jsonl` output profiles the terminal record's `error`/`message`/`codex_error_info` text now joins the rate-limit scan (agent prose in earlier records stays out), the codex profile names `usage_limit_exceeded`, and the instrument is paused for the fallback instead of retried. Docs and the expert contract carry the rule.
- **`codebase-rewrite`'s characterization gate proves THIS pass, not the branch's history.** The gate accepted "a `test(` commit exists on the rewrite branch", which an earlier iteration had already made true; it now requires the pass's own `## Pass N` entry in the characterization log naming a commit that is an ancestor of the branch.
- **`codebase-rewrite`'s specification critic is told to write its rule table first.** On the clamonacc proof the spec lens (`claude-code` at max reasoning) hit its 2,400 s wall three times across two refinement passes while the idiom and structure lenses finished in minutes: "check every rule with evidence" over a 900-word spec became an open-ended audit. The prompt now orders the work rule by rule, table first, one citation per rule, and expands only UNMET/PARTIAL rows.
- **`codebase-rewrite`'s refiner reruns a red tier once before reverting, and names a recurring flake as a harness defect.** The clamonacc proof's refiner reverted three green commits on one differential input (`ping-refused-connection`) that flickered between runs: under parallel load the C scanner's first connect to a peer that closes immediately is sometimes refused before the peer accepts (3–5 of 12 parallel runs; the Rust side is stable 24/24), so the difference was the oracle's nondeterminism, not any of the three edits. The prompt now distinguishes a flake from a red tier and sends a recurring one back to the harness.
- **`codebase-rewrite`'s critics are told what a citation is, and the gate resolves a unique suffix.** Two of three critics on the clamonacc proof (claude-code, opencode) wrote `mod.rs:57` and `ddd/task.rs:283` for `path:line` and lost two attempts each to the "names a path:line that exists" gate; codex cited absolute paths and passed. The prompt now states the rule with an example and says the gate refuses a bare or ambiguous name; the gate resolves a citation that ends exactly one project file (skipping .git, target, build trees), still refuses an ambiguous one, and its failure message carries the rule so the retry can read it.
- **A loop that meets its `until` on the pass that reaches a cap ends `condition_met`.** The boundary decision checked `count`, `max_iterations` and `cost_limit_usd` before `until`, so a goal met on the capped pass was recorded as `cost_limit_exceeded` and `mzt status` said "the until was not met" over a met goal (the clamonacc proof's behaviour round went GREEN at $52.73 against $45 and read cost_limit_exceeded). `until` is now judged first, including across the off-loop file-facts round trip (`LoopRunState.pending_cap_reason`); a cap with the goal unmet still ends on the cap.
- **`issue-solver`'s commit gate accepts `(#N)` at the end of a subject line.** The pattern put `\b` after an alternation that ended in `)`, so `feat: … (#148)` as the last characters of the subject never matched (no word character follows the parenthesis) while `(#148) more` did; the committing musician saw its pushed commit refused and had to add a metadata-only follow-up. The boundary now applies only to the keyword forms (`closes #148` still refuses `#1480`).
- **`codebase-rewrite`'s differential gate reads the intended-differences list the way the prompt says to write it.** Stage 4 asks for `<input> # <spec rule>` lines, but the stage-8 gate matched each corpus file's basename with `grep -qxF`, so a commented line never matched and every intended difference counted as a defect: the clamonacc proof's differential voice reported PASS (0 unadjudicated differences through the harness checker) and the gate failed it on 9 inputs, all listed as intended. The gate now strips comments and matches the filename or its stem.
- **`codebase-rewrite`'s characterization loop can name what it cannot reach, and a loop that ends short is recorded (proving ground, clamonacc C→Rust).** The loop spent $26.02 over five passes lifting a 1,240-line C module from 18% to 78% and ended on its $25 cap twelve points under the floor, while `misc/utils.c` stayed at 0% because its lines run only as root; a flat floor could not say so, and the oracle inventory ran as if the floor had been met. The characterization cap is now $60 over eight passes, the unit loop $75 over twelve passes, the behaviour round $240 over three and the refinement loop $100 over four (two refinement passes cost $50.30 against $30: every kept commit reruns the whole oracle) (its four-pass unit loop had ended at $36 against $30 with four of thirty rows left, and a $45 round cap could not have covered a second round), each with the measurement in a comment, the stage-3 prompt excludes a module whose uncovered lines need a privilege, device or credential the host cannot supply (coverage configuration, own commit, `03-excluded.yaml` with path/reason/evidence/commit, validated), the oracle inventory must carry a `## Coverage shortfall` section when the loop ended with `floor_met: false` and no marker (validated), and the final review audits every exclusion and caps behavioural equivalence at the measured source coverage.
- **Resuming a FAILED score no longer loses the loop index (GH #500).** A loop whose member sheet exhausted its retries ended with `phase="completed"`, `completed_reason="range_failed"`, and the current iteration persisted. `mzt resume` on the FAILED job reset the failed/cascade-skipped member sheets to PENDING (#185 intrinsic recovery), but nothing reopened the loop run: `FlowEngine.indices_for` filters out non-running loops, so every recovered dispatch rendered with an empty `flow_vars` and any template or validation naming the index (`{{ pass_no }}`) failed deterministically with E999 (`pass_no is undefined`) until retries exhausted — the resume could never succeed, and the loop's `until` decision would never run again. Resume now calls `_reopen_loops_for_resume` after the sheet reset: a loop in `range_failed`/`condition_error` with a member sheet the reset left PENDING returns to `running` with `completed_reason` cleared, the in-flight **iteration preserved** (the recovered pass is the one that was interrupted, not pass 1), `cost_baseline_usd` re-based to the span's accumulated sheet costs so caps measure the resumed run from the current state, and `iteration_started_at` refreshed. Loops that ended for a genuine reason — `condition_met`, `count_reached`, `max_iterations`, `cost_limit_exceeded` — stay closed (a resume must not mint extra passes a composer did not ask for), and a PAUSED resume preserves loop state exactly as persisted (its terminal work is deliberate). `--from-sheet` follows the same rules through the sheet-reset guard: a failure-terminated loop reopens only when it still has a pending member sheet >= N, so a reset that leaves a loop's span untouched leaves the loop closed. Each reopen is logged (`baton.resume.loops_reopened`) and idempotent — a second resume of the same checkpoint reopens nothing.
- **Packaged persistent-agent lifecycles follow the current compiler (GH #498).**
  Regenerated all portable agent seeds, scores and roster hashes. Inspect now waits
  for Integration (`7: [5]`), and Reflect waits for Consolidate (`10: [9]`), preventing
  concurrent writes to `profile.yaml`. Debt and integration-receipt instructions
  explicitly require `sha256:` followed by exactly 64 lowercase hex characters;
  validators remain strict. A compiler regression test regenerates the complete
  package and compares its bytes with the checked-in assets, skipping only when
  the plugin package is absent in a compiler-only checkout.
  Existing installations with lived changes or an older managed-asset baseline may
  report `locally_modified_managed_asset` conflicts; installation preserves agent
  authority rather than overwriting divergent memory. Live debt files containing
  bare hex hashes remain invalid until the agent rewrites them on its next targeted
  engagement using the explicit format. No migration silently rewrites agent-owned
  memory or accepts an invalid integration receipt.

- **The MCP pool actually supervises its servers, interactive musicians receive the pool config, and technique phase gating works for numbered movements (GH #497).** Four defects, one pool. (1) `McpPoolManager.is_running()` trusted the recorded state enum, so a server whose process had crashed kept answering "running" and kept being handed to sheets as a live MCP technique; it now reconciles against the live process/bridge (a RUNNING handle whose process exited or whose bridge is dead is transitioned to FAILED as a side effect, and `get_status()` refreshes every server). (2) `restart_policy` was validated in config but read by nothing — `start_all()` now starts once and supervises: a monitor task polls every second and restarts a FAILED server per its policy (`on-failure` skips a clean exit 0), with exponential backoff (1s, 2s, 4s) and a bounded crash budget of 3 attempts that only a server staying up 30 seconds resets; startup and shutdown run under a lifecycle lock, so concurrent `start_all()` calls no longer orphan a second server generation and a cancelled initial startup still cleans up. (3) Interactive (tmux) musicians never received the pool config: `InteractiveCliBackend` now implements `set_mcp_config()`, threads the config flag in `_build_command`, and — for CLIs that only discover MCP through a workspace file (e.g. Antigravity, Gemini) — temporarily materializes that file around each tmux run via the same `WorkspaceMcpConfigMixin` the headless backend uses (extracted to `workspace_mcp_config.py`), serialized against concurrent headless attempts through a shared per-target lock and restored byte-exact afterwards; a backend without MCP config support is now named in an `adapter.dispatch.mcp_config_unsupported` warning instead of silently dropping the config. (4) Phase gating for hand-written techniques compared the raw `str(movement)` (`"1"`..`"6"`) against phase names and never matched: the adapter now uses the canonical `movement_phase_name()` mapping (1→recon, 2→plan, 3→work, 4→integration, 5→inspect, 6→aar, later→work) and `filter_techniques_for_phase()` accepts the movement number as an alias, so a technique declared `phases: ["3"]` gates like `phases: [work]`. `restart_policy` is a `Literal["on-failure", "always", "never"]` at config load.
- **Interactive pane deaths are attributed, and stale tracked pgids can no longer be group-killed (GH #487).** An interactive (tmux) agent whose pane died by SIGKILL surfaced only tmux's `pane_dead_signal` — which names the *final* signal, the supervisor's own escalation — so a benign forwarded SIGHUP escalated after the grace was indistinguishable from an external SIGKILL, and a death with no tmux-recorded cause was simply unattributed. Under `command.supervise` (the interactive default) the supervisor now writes a forensic report next to the transcript (`<session>.supervisor.json`, atomic tmp+rename) carrying `forwarded_signals` (each signal received and dispatched, with monotonic `at_seconds` relative to run-loop start), `escalated_to_sigkill`, and `escalated_after_seconds`; the keys are present on every path including spawn errors. The driver reads it on `session_lost` and the attempt detail states what actually happened: the forensic summary (`supervisor forwarded SIGHUP (first at t=0.2s), child did not exit within 1.0s, escalated to SIGKILL`), or `supervisor observed no forwarded signal — death not sent by the supervisor: external kill` when the supervisor wrote a report but forwarded nothing (the strongest external-kill evidence a report can give), or `no supervisor report — death not observed by the supervisor: external kill, or the supervisor died first`. A missing, unreadable, or malformed report never fails the attempt, and the backend logs the report (`interactive_supervisor_report`) for observability. Separately, the baton's interactive process-group registry (`_active_pids`) now records each tracked leader's kernel start ticks at spawn time; before any tracked group kill (teardown, preemption, deregistration) the leader's *current* ticks are re-verified, and a stale entry whose PID was reaped and recycled into an unrelated process is refused (logged as `adapter.kill.refused_recycled_pgid`, counted in `sigterm_skipped`, never signalled) — so a missed deregistration can never route TERM→KILL into a live unrelated group. The guard is refuse-only and fail-open: no recorded ticks (probe gap, legacy entry, non-Linux) or an unreadable `/proc` entry proceeds with the kill, per the #344 rule that a probe gap must never skip a legitimate one.

- **`--json` machine output can no longer emit invalid UTF-8 (GH #495).** At 19:01Z on 2026-11-05, `mzt status issue-solver --json` emitted ~40 KB of JSON containing a raw `0x97` byte (a Windows-1252 em dash from a subprocess stream) and a strict `json.load` consumer failed with `UnicodeDecodeError`. `--json` is a machine contract — a consumer cannot be expected to decode with `errors="replace"` — so the guarantee is now enforced at both ends. At the writer: `output_json` and the JSON branch of `output_error` serialize with `ensure_ascii=True` and, on the default path (no explicit console), write the ASCII-only text directly to `sys.stdout`, bypassing Rich's rendering pipeline, whose emoji substitution (`:warning:` → ⚠) can mutate serialized JSON into non-ASCII even with `markup=False`; JSON-format logging (`--log-format json`) is pinned to the same `ensure_ascii=True` serializer because the log handler may share stdout with machine output. At the capture boundary: every subprocess capture that feeds conductor state (`SheetState.capture_output`, snapshot, validation CLI checks, the review scorer's three git captures, GPU probes, instrument version probes, tmux session kills) decodes with explicit `encoding="utf-8", errors="replace"`, and untruncated output tails are round-tripped through UTF-8 with `errors="replace"` so a lone surrogate from a mis-decoded stream never survives into state. Regression tests (`tests/test_json_utf8_contract.py`) plant the incident's hostile bytes (raw `0x97`, lone surrogates) in attempt output and assert `status --json` parses as strict UTF-8 JSON.
- **Gemini CLI has a documented API-key readiness path (GH #469).** Doctor recognizes `GEMINI_API_KEY` alongside the existing Google auth signals. The class writer now emits `high-context`, `image`, and `video` policies and probes same-named user/venue overrides, so a working local Gemini profile can join generated chains. The instrument guide documents copying the full profile to `~/.marianne/instruments/gemini-cli.yaml`, running `mzt instruments check gemini-cli --live`, and enabling it only after success. The shipped profile remains honestly unsupported for the default individual-tier OAuth path.
- **`issue-solver`'s functional reviewer rules on a harness failure only after isolation.** A full run at load 12 failed two timed tests that passed alone in 18 s and touched no file in the diff; the reviewer reran them in isolation, recorded the passes, and still set FAIL on the full run. The prompt now says a failure that passes alone and is outside the diff is a venue flake to record, not a verdict, and the README carries the rule.
- **`codebase-rewrite`'s plan gate names a malformed status cell instead of 'no PENDING unit rows'.** The clamonacc proof's decomposition wrote `DONE (iter 1)` and `**PENDING**` in the status column; every gate reads that cell with a bare-word regex, so the stage-5 validation refused the plan with a message that did not say why, and the stage-6 and stage-9 gates would have counted zero rows. The prompt now states the contract (last column, one bare word, iteration or blocker elsewhere) and the validation names the first offending cell.
- **`mzt status` shows the cost the loop caps read, and each loop's exit reason.** The characterize loop of the clamonacc proof stopped on `cost_limit_exceeded` at $26.02 against a $25 cap with its `until` unmet, and status read `Cost: $0.00` with no mention of the loop: the summary read a job-level total nothing writes and the last attempt's `estimated_cost`, not the sheets' accumulated `total_cost_usd`. The summary now falls back to that sum (`(from sheets)`), a **Loops** section names each span's pass or exit reason and spend and says when a cap left the `until` unmet, and JSON carries `loops` and per-sheet `total_cost_usd`.
- **`issue-solver` ranks by the repo's priority label after tier.** With a flat DAG (every issue tier 1, no edges) the selection fell through to lowest issue number and chose a P3 research item over four unlabelled and two P2 issues. The sort now reads `P0`..`P3` from the sanitized corpus between tier and scanner verdict, ranking an unlabelled issue as `P2`, and the selection record names the priority.
- **`mzt init` on a machine whose only ready instrument is a local model server writes a starter that can play (GH #494).** The shipped starter binds `strong`, which no `http` profile can serve, so a first run with nothing but a local server failed at sheet 1 with E505. When no CLI agent is ready and a loopback `http` profile is, the starter binds that profile with `cross_sheet.auto_capture_stdout` and passes replies between sheets instead of files; the output names the binding.
- **`mzt instruments classes write` says why a hinted profile was not seated (GH #494).** A local `http` profile with `class_hints: {workhorse: first}` was silently dropped from `workhorse` (the class requires `tool_use` and `file_editing`, which an endpoint cannot provide), leaving the class empty with no cause named. The writer now reports every refused (profile, class) pair in the map's header comment and on the terminal, with the missing capability and, for an http profile, the route through opencode.
- **`sheet.size` defaults to 1.** A first score that named only `total_items` failed `mzt validate` with a Pydantic "Field required" (the first wall the first external user hit, GH #494). One item per sheet is what 59 of 60 shipped examples set and what `fan_out` and `voices` require, so it is now the default; a score that sets `size` is unchanged.
- **The workspace observer no longer records its own timeline file as a modification.** Each append to `.marianne-observer.jsonl` was reported back as `observer.file_modified` and recorded, one event per watch interval for the whole job (89 of 208 events in a run's first ten minutes), so the TUI timeline and `mzt observe` showed the recorder's own heartbeat as workspace activity. The recorder's own file and the `.marianne/` and `.marianne-scratch/` bookkeeping directories are always excluded, whatever `observer.exclude_patterns` says.
- **Graceful shutdown no longer waits on a boundary-paused job's sheet (GH #476, gap 2).** A job paused at a sheet boundary kept its current sheet in flight until the musician ended, and the drain gave it the whole `shutdown_timeout_seconds` although that sheet is discarded and re-run on resume by design; eight such jobs held a 300 s drain on 21 Oct and four on 22 Oct. The drain now ends those musicians at once (`manager.shutdown_ended_boundary_paused_sheets` names them), leaves the pause on disk, and waits only for RUNNING jobs. `mzt stop` says so.
- **A route's `profile_origin` no longer depends on the caller's working directory (GH #493).** The venue default is the relative `.marianne/instruments`, so from `$HOME` it is the organization directory itself, and the venue test ran first: the same profile bytes were `venue` from `$HOME` and `organization` from anywhere else, and `E301 attempt_route_drift` refused a dispatch whose route had not changed. The organization layer is classified first; `venue` means a venue directory that is not also the organization one.
- **A first score on a local OpenAI-compatible server no longer fails before any model runs (first external user, 2026-11-05).** Walked against a PolyStrata-shaped server: the HTTP executor sent its 16,384 `max_tokens` default to an 8k-context model and got HTTP 400 on every sheet; `mzt validate` warned V215 about a server that was answering; and a profile written in the project's `.marianne/instruments/` was seen by validate, check and list but not by a conductor started elsewhere, so the job died at sheet 1 with E505 and a list of every other profile. The executor now caps `max_tokens` at the bound model's `max_output_tokens` or `context_window`; V215 counts a loopback server that serves the profile's model as runnable; submit refuses a score that binds a venue profile the conductor never loaded and names both directories. `docs/local-server-quickstart.md` walks the path in the order the plumbing needs it, and `docs/design-to-score.md` writes down the design doc → spec → score → run route for users without the conducting skill. `score-composer`'s target defaults to the `workhorse` class and says why an `http` profile cannot be its target.
- **`codebase-rewrite` no longer refines an unfinished plan (proving ground, humanize Python→Rust, 9 iterations).** Three defects the proof surfaced: the behaviour gate accepted GREEN with seven of fifteen plan rows still PENDING after the unit loop's eight passes, so refinement and the final review ran on incomplete code and the review refused on bookkeeping; the unit loop took only PENDING rows, so two PARTIAL rows stalled four passes with "no eligible unit"; and the critic sheet's 1,200 s wall cut 18 of 58 claude-code critiques mid-review. The gate now requires every plan row DONE or BLOCKED (its validation checks the plan, not the report), the unit loop takes PARTIAL rows and reconciles any row whose acceptance command is already green at HEAD to DONE before writing code, and the critic wall is 2,400 s.
- **Subprocess-lifecycle tests scale their timeout ceilings with host load (GH #501).** On 2026-11-06 a root restic backup held host load at 10–18 and three tests that measure real reaping/drain behavior — `test_large_stdout_burst` (50 MB through one pipe, 120 s internal budget), `test_cancelled_start_reaps_upstream_and_releases_socket` (cancels a start task against a child that sleeps 60 s), and `TestDaemonLifecycle::test_graceful_shutdown` — each tripped the global 30 s pytest-timeout safety net under load while passing in ~1 s when idle, so full-suite runs failed on the net rather than on any assertion and three solver chains paid a wasted 7–8 minute full-suite pass each. The 30 s net in `pyproject.toml` is untouched (it guards every other test against orphaned processes). Instead, the named tests plus one audited sibling (`test_one_pipe_closed_early`, 60 s internal budget) now carry `@pytest.mark.timeout(load_scaled_timeout(N))` and the new `load_sensitive` marker, where `load_scaled_timeout` in `tests/helpers.py` returns `ceil(base × factor)` with the factor read from `MARIANNE_TEST_LOAD_FACTOR` — default 1, clamped ≥ 1, and fail-safe to 1.0 on garbage/NaN/inf input because a bad env var must never brick collection. Marks are evaluated at module import, so the env var must be set before pytest starts. Bases dominate the internal budgets with headroom (180 > 120 s; 120 > 60 s), so the ceiling stays finite — a hung test still dies, just at a load-scaled bound. CI gains a `load-sensitive` job: an idle control run at the default factor (the marks must not change idle behavior) and an under-load run under `stress-ng --io 4 --cpu 4` with `MARIANNE_TEST_LOAD_FACTOR=4`.

### Security

- CLI instrument environments now default to system essentials and explicitly
  declared `required_env` / profile `env` variables (GH #335). Interactive tmux
  launches use the same policy and replace the server environment. Custom
  profiles relying on ambient keys must declare them in `cli.command.required_env`.
  Temporary `cli.command.inherit_env: true` restores full inheritance only when
  `required_env` is null; doctor warns about this opt-out. File-based credentials
  and daemon hook/MCP subprocess isolation remain separate work.

### Score-language release note (2026-10-15)

The score language is the product's type system: a key's name, its validator
message, its doc line and its skill line are one contract. This release makes
the language say what it does. Nothing you have written stops working; some
of it now warns so you can move before the next release removes the alias.

**Three timers, three keys — read this first.** Confusing them is how work gets
lost. `instrument_config.timeout_seconds` bounds one sheet's execution and is
typed at all four scopes (root, `movements.<N>`, `sheet.per_sheet_instrument_config.<N>`,
`instruments.<alias>.config`); a misspelling at any of them is a V010/V011
warning. `stale_detection.idle_timeout_seconds` is the opt-in no-output timer.
`schedule.max_wall_seconds` is the window of ONE scheduled tick of a recurring
score. A one-shot score has no absolute deadline: its envelope is the per-sheet
timeouts, idle detection and the conductor's `job_timeout_seconds`, which counts
accumulated RUNNING time only (pauses and downtime are free).

**Behaviour changes** (each also listed under *Changed*):
- Root `max_wall_seconds` no longer caps a one-shot score. It was the tick window
  mislabelled as a job budget (GH #435). Write `schedule.max_wall_seconds` for a
  recurring score; the root spelling keeps working for one release and warns V328.
- `retry.completion_threshold_percent` is a strict gate: a pass rate must exceed it
  (default 50) to enter completion mode. Older releases sent any partial pass to
  completion mode; review scores that lean on the default.
- `cost_limits.max_cost_per_sheet` is enforced on every attempt, including a
  successful one that takes the sheet over its cap.

**Renamed spellings (one-release aliases, V328):** `checkpoints:` → `approvals:`;
inside an `on_success`/`on_failure` hook, `on_failure` → `if_hook_fails` (at the
trigger level `on_failure` keeps its own meaning). The root `conductor:` block is
deprecated identity metadata with no reader; conductor settings live in
`~/.marianne/conductor.yaml`.

**Keys that were accepted and ignored now say so.** `mzt validate` emits V327 for
a reserved section the runtime does not read (`rate_limit`, `circuit_breaker`,
`logging`, `bridge`, `learning.exploration_budget`, `learning.entropy_response`,
`learning.auto_apply`): remove the key rather than suppress the warning. A
readable default-off section (`parallel`, `stale_detection`, `isolation`,
`code_execution`, `judgment`, `concert`) with settings but no `enabled:` warns
V329 this release; from the next release those settings enable their section.
Write `enabled: true` now, or explicit `enabled: false` to keep it off.
The configuration reference now carries that mark on every one of the 60
score keys the runtime does not read (the language checker's L08 list, the
audit GH #353 asked for), `grounding:`, `ai_review:` and `feedback:` are
introduced as reserved sections, the getting-started, score-writing and
troubleshooting guides stop teaching `rate_limit.wait_minutes` as the way to
configure waits (detection is the instrument profile's
`rate_limit_patterns`), and no shipped example sets a reserved key any more.

**New checks you may see:** V010 curated typo hints now fire at any depth (an
unknown `timeout` beside a trigger action names `instrument_config.timeout_seconds`);
V301/V302 technique paths and MCP instrument references; the language gate
(`tests/scripts/check_language.py`, baseline only shrinks) runs in CI so these
four layers cannot drift apart again.

**For instrument hygiene:** `mzt doctor` now lists user- and venue-layer profiles
that no submitted score names and no class chain includes (GH #443). A seat is
an alias over an existing profile; archive what nothing names.

### Added
- **`mzt corpus` — local composition-corpus groundwork (GH #148).** Two commands assemble and measure a composition corpus entirely locally: `mzt corpus build --sources examples,scores --output corpus.jsonl` writes a deduplicated JSONL of `TrainingExample` records (`instruction`, optional `input`, score-YAML `output`, optional human `rationale`, `provenance`), and `mzt corpus stats [--json]` reports `total_examples`, `by_source_kind`, `by_category`, `annotated`, and `meets_target` against the 200-example volume target — a volume boundary only, not a diversity, licensing, or training-readiness certificate. Sources are explicitly selected score directories (YAML preserved byte-for-byte; nested repositories, symlinks, and lock files excluded) or telemetry JSONL spools (every accepted score re-passes `telemetry.sanitizer.sanitize_score`; malformed envelopes are skipped and counted in a warning; only an opaque `consent_hash` is kept, never a raw grant ID). Deduplication hashes exact output text, repository example winning across sources; build writes atomically (temp file + `replace()`) and refuses a destination that is a source, the consent file, or a `.lock` name. No network access, no consent change, no training — training, annotation reload, and outcome joins remain future work. See `docs/research/2026-11-05-composition-corpus-groundwork.md`.

- **`plugins/scripts/install_skills.py` distributes the shipped skills to every harness.** Each harness (Claude Code, Codex, Gemini, ZCode, OpenCode, the shared `.agents` surface, the registered Claude plugin cache, and the Windows-side roots under WSL) keeps its own copy of the skill tree, and every copy lagged each skill commit until someone re-ran a one-off sync. `status` names the roots that lag, `install` brings every root current with each target's preimage archived and byte-verified first, and `verify` recomputes every installed copy against the source. The installed copies differ from source only by the portable plugin-root note and the expert skill's rebuilt manifest, as the 18 and 26 October scoped distributions did by hand.

- **`mzt remediate` — user-facing remediation for failed jobs (GH #147).** Diagnose failed sheets and propose or apply fixes using the same remedy registry and healing coordinator as conductor self-healing. The command reads persisted job context and attempt history from the authoritative registry; it does not update checkpoint status, restart execution, or schedule a retry — after applying offline remedies it prints `Remediation finished. No retry was scheduled.` AUTOMATIC remedies apply unless `--dry-run` is set; SUGGESTED remedies ask for confirmation unless `--yes` is set; DIAGNOSTIC remedies print manual guidance. `--sheet N` selects one sheet (including a nonterminal sheet that has an error), `--workspace` overrides the workspace (otherwise the stored configuration supplies it), and `--json` writes the report to stdout with confirmation prompts on stderr. Two new DIAGNOSTIC remedies ship in the default registry: `suggest_spec_fix` (E301–E304 spec/corpus failures — reviews `spec.spec_dir` and fragment content, edits nothing) and `suggest_recompose` (E201/E202/E203/E209 output-validation failures after retries are exhausted — proposes a re-compose, never fires for auth or capacity errors and only when a `config_path` exists and `retry_count >= max_retries > 0`). Applying offline remedies requires a terminal job (FAILED/COMPLETED/CANCELLED); use `--dry-run` to inspect any other job. `ErrorContext.from_checkpoint_sheet` adapts persisted checkpoint fields into the healing engine's context without relying on baton counters, and `RemediationService` reuses the healing coordinator against persisted state without saving it — each sheet gets a fresh coordinator so one job's healing budget isn't exhausted by the sheet before it. See `docs/cli-reference.md`.

- **Community telemetry pipeline, opt-in and default-off (GH #146).** Scores may set a `telemetry:` block (`enabled`, `spool_path`; `endpoint` and `submit_mode` are reserved with no runtime reader) and the user grants consent separately with `mzt telemetry consent --grant` (`~/.marianne/telemetry-consent.json`). With both, the daemon stages one sanitized JSONL envelope per completed or failed job — including resumed jobs — into `~/.marianne/telemetry.jsonl` (or the score's `telemetry.spool_path`) via `asyncio.to_thread`, never blocking the event loop; any collection failure only logs a warning and never fails the job. The sanitizer recursively scrubs paths, usernames, emails, URLs, and credential-shaped strings including diagnostic prose; pattern matching cannot guarantee anonymity, so `mzt telemetry export` exists for inspection before sharing. Submission is never automatic: `mzt telemetry flush --push --endpoint https://…` is the only network path (HTTPS, or HTTP on loopback; no redirects, proxies, retries, or credentials; at-least-once delivery). `mzt telemetry consent --revoke [--purge]` stops collection and optionally deletes the spool; revocation rotates the consent ID so old records cannot be sent under a new grant. See `docs/telemetry.md` for the full data policy.

- **`openclaw` built-in instrument (GH #84).** Adds headless `agent exec` with stdin prompts, saved client auth/config, per-sheet model overrides, final JSON text and token usage. Source contract pinned to OpenClaw v2026.10.1; live authenticated execution was not verified. Explicit config/state env allowlist; exec defaults to full shell execution policy, so use a trusted workspace. See the instrument guide and source-derived fixtures.

- **`qwen-code` built-in instrument profile (GH #83).** Qwen Code, Alibaba's coding agent CLI, joins the shipped agent harnesses. The profile runs it headlessly (`--yolo --output-format json`, prompt over stdin) and reads final text plus input/output token usage from the terminal JSON result. Authentication uses the client's saved OAuth credentials or an OpenAI-compatible provider (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `QWEN_MODEL` are the declared `required_env` allowlist; other provider families need a profile override). Without a score model override Qwen's own configured model is used - the profile ships no assumed Qwen model prices or capacities. `--yolo` auto-approves tools including file edits and shell commands: choose the workspace accordingly. The source contract is pinned to Qwen Code v0.12.6; live authenticated execution was not available on the implementation host, so the protocol is documented by source-derived fixtures (`tests/fixtures/qwen_code/`). See the Qwen Code section in the instrument guide.

- **`cli.output.error_flag_path` (GH #83).** Some CLIs return exit zero for a terminal semantic refusal (Qwen Code's `is_error` is one). A profile may now set `error_flag_path` to a JSON dot-path; when it resolves to exactly boolean `true`, the attempt fails as `cli_error` even at a successful exit code. Opt-in and JSON-format-only; unset preserves exit-code-based behavior for every existing profile.


- **`codebase-rewrite` builds its oracle before rewriting, and refines after.** The example now measures the source's coverage the way its tests reach the code, and below `coverage_floor` (default 90%) writes characterization tests against the source until the floor is met. Rewriting runs in a behaviour loop: a test-first unit loop, then four oracle tiers in parallel (unit, integration, functional, and a differential run of the same inputs through `source_run_command` and `target_run_command`), then a gate that either declares all of them green with target coverage at the floor or reopens the units the red tests point at. Only green code is refined: three critics on different models (`select: [spread, balance]`) review idiom, specification conformance and structural fidelity to the source, and a refiner keeps only commits the whole oracle still passes. The final review scores mirroring the source's decomposition as a reward, never a requirement. Every sheet's gate reruns the real command, so no report can claim green over a red suite. Measured motivation: the earlier hoard-mill proof passed all 29 original tests while one of its two source files had 0% coverage.

- **A `response_format` sheet's validated object stays on the sheet record (GH #489).** `job.status` and `mzt status --json` now carry `sheets[n].structured_output`: the whole object, credential-redacted per string, from the latest attempt (a failed retry clears it), so a caller that polls after COMPLETED can read it. Before, the object reached the record only as a 500-character output tail. CLI schema sheets and OpenAI-compatible JSON-mode sheets both fill it. An object over 64 KiB of compact JSON is not stored, and `structured_output_omitted: true` says so.

- **`workspace_lifecycle.foreign_artifacts: quarantine` (GH #386, second half).** The conductor already named a sheet that wrote a later, non-ancestor sheet's declared artifact; with this setting it also moves the file to `$MARIANNE_SCRATCH/quarantine/sheet-<N>/` as the attempt ends, before any downstream sheet is dispatched, and the warning says where it went. Default stays `warn`. The quarantine lives under the scratch root, so it is cleaned with the job and can never land in a commit.

- **`class_hints` on an instrument profile seats it in `mzt instruments classes write` (GH #469).** The writer ranked only the shipped profiles, so a user or venue profile with a working route (a GLM-pinned `opencode`, a Gemini key) could never enter the generated map and had to be hand-edited in. A profile may now declare `class_hints: {strong: "after opencode", fast: first, review: last}` (`first`, `last`, `after <profile>`, `before <profile>`; validated at load); the writer splices it into that class's order, still subject to the class's capability and availability probe. A profile without a hint is never seated — the ranking decision belongs to the profile's author.

- **`mzt diagnose` prints a failed attempt's cause, not only its category (GH #143).** The per-attempt history now keeps the musician's redacted `error_message` (trimmed to 300 chars; None on success), and diagnose shows it under the attempt line beside the error code: `1. claude-code: 31.8s → TRANSIENT E999` / `agent process killed by SIGKILL before signalling completion`. The classifier's root-cause selection existed; its result never reached the report.

- **`mzt validate --render-prompts PATH` (GH #89, #138).** Writes every sheet's fully rendered prompt to a Markdown file (`-` for stdout), deduplicated: sheets whose renders are byte-identical share one section headed by their numbers, so a thousand-sheet fan-out reads as its few distinct prompts; a sheet that fails to render gets its error. With `--json`, each sheet also carries `rendered_prompt` beside the 15-line snippet. Until now the only way to read a rendered prompt in full was a custom script around `PromptBuilder`.

- **An interactive attempt that dies says how.** tmux panes are created with `remain-on-exit`, and the driver reads `pane_dead_status`/`pane_dead_signal` when the agent process ends without the completion marker: the attempt's error now reads `agent process killed by SIGKILL before signalling completion` or `… exited with status N …` (conductor log `interactive_pane_exited` carries both fields); `agent session vanished …` is reserved for a session tmux no longer holds. Eighteen claude-code sessions on one night reported only "session died" with no cause on record.

- **`mzt stop` names what the graceful drain waits on (GH #476).** `daemon.ready` now carries `in_flight`: every musician still executing (`job_id`, `sheet_num`, `instrument`, `model`, `elapsed_seconds`). After signalling, `mzt stop` prints those rows; on a `--wait` timeout it re-probes and prints the rows still holding the drain, and says the drain runs to `shutdown_timeout_seconds` plus teardown rather than guessing. `mzt conductor-status` shows `In flight: <n> sheet(s)` with the same rows. The conductor log's `manager.shutting_down` carries `in_flight` and a new `manager.shutdown_timeout_exceeded` names `held_by`. Found on Pallas: eight jobs paused at a sheet boundary held two restarts for the full 300 s with `running_jobs=0` in the log — a pending pause keeps its sheet in flight until the sheet ends, and nothing said so.

- **V332: a CLI schema whose top level the API refuses is named before the run (GH #486).** V311 proves the profile can enforce a schema, V331 that the sheet runs headless; V332 proves the schema's top level is one the CLI's tool `input_schema` accepts — no top-level `oneOf`/`allOf`/`anyOf`, a top-level `type`. Found live: a discriminated-union intake schema cost a 400 at sheet 1 the job could not quote.

- **Lazy-acceptance gate over the example scores (GH #480).** `tests/scripts/check_lazy_acceptance.py` synthesises, per example sheet, the fixture a lazy musician would write — the files `file_exists` names, the exact `content_contains` text, a literal the `content_regex` matches, plus filler — and runs the real `ValidationEngine`; a sheet whose rules all pass has acceptance that headings-plus-filler satisfies. `tests/fixtures/lazy_acceptance_baseline.json` holds today's 132 of 689 sheets (patterns 46, product 20, creative 19, research 18, engineering 15); the test fails on a new row and asks the fixture to shrink when a row disappears. This productises the QA voices' probes as the one gate that reads the score file.

- **Example READMEs are checked against their scores (GH #481).** `tests/scripts/check_examples_readme.py` (and its test, run in CI) fails on any relative link in an examples README that does not resolve and on any score row whose Sheets column differs from the score's concrete sheet count. First run: 13 dead Rosetta links (repointed at `scores/rosetta-corpus/`) and 5 wrong sheet counts (quality-triage 15, issue-solver 13, saas-app-builder 8, lovable-generator 11, research-agent 9), all fixed. A gate that reads the score file beats a rule in prose.

- **V331: a schema sheet that would run interactively is named before the run (GH #478).** `response_format` on a profile that is interactive by default (claude-code, antigravity) with no `interactive: false` passed `mzt validate` (V311 judges the profile's `json_schema_flag`) and was refused at sheet 1 ("cannot enforce instrument_config.response_format"). V331 builds the sheets, resolves each one's instrument, and warns per instrument and movement with the fix. Docs and the score-authoring skill state the rule.

- **A sheet that writes another sheet's declared artifact is named (GH #386).**
  The producer signal is the existing proxy — a sheet-gated `file_exists`
  validation (what V108/V109 already read; it now lives in
  `core/producers.py`). Each sheet carries the artifacts non-ancestor sheets
  are declared to produce; the musician snapshots them around execution and
  an attempt that created or modified one reports an `artifact:` warning,
  folded into the sheet's preflight warnings for `mzt status` and
  `mzt diagnose`. Advisory only: the owning sheet still has to write the
  file, and nothing is reverted.

- **Alias chains with `select` modifiers (GH #473).** A score alias may bind
  an ordered `chain` of entries instead of one profile; `select` decides which
  entry each sheet starts on and the rest follow as that sheet's fallbacks.
  `cycle` rotates per use, `spread` rotates per movement (a fan-out's voices
  get distinct models from one alias), `cheapest` and `widest` order by the
  profile's model table, and `balance` lets the conductor start on the
  least-loaded entry at dispatch (recorded in the fallback history; the
  chosen entry's whole config travels with it). Modifiers
  compose in the order written. V330 warns when a modifier has nothing to
  choose between. Single-profile aliases are unchanged. The example scores
  bind instruments per role instead of one alias for the whole score:
  issue-solver and quality-continuous put `fast`/`strong`/`workhorse`/
  `writing` on the movements that play those roles and a `reviewer` chain
  with `[spread, balance]` on their review fan-outs; design-review,
  thinking-lab and research-agent spread their review, perspective and
  investigation voices across distinct models the same way; the rosetta
  discover/prove scores collapse three reviewer aliases into one.

- **`mzt instruments check NAME --live --record DIR` and captured CLI
  fixtures.** `--record` writes each live call's raw bytes, exit code and CLI
  version, so parser tests replay what the installed CLI prints instead of a
  fixture written from its docs (GH #472). `tests/fixtures/cli_captures/`
  holds Claude Code 2.1.306 output (Claude model and GLM-5.3 route, plain and
  schema mode), reduced to the events the parser reads. The first recording
  caught a real case: on the GLM-5.3 route Claude Code sometimes fails to call
  its StructuredOutput tool (1 of 3 runs), and the parser correctly refuses
  the prose answer.

- **Language gate L13 `reserved-taught`.** A doc, skill or shipped example
  score that sets a key the schema marks Reserved or Deprecated now fails the
  score-language gate (GH #472). It found 31 such settings across the
  configuration reference, the score-writing, concert, conductor and
  learning guides, the plugin refs and four plugin example scores, all
  fixed here: hook-level `detached` and `inherit_learning` (a `run_job` hook
  never waits; there is one global learning store), `concert_log_path`,
  `warn_at_percent`, `branch_prefix`, the deprecated `conductor:` section,
  and reserved learning sub-blocks. Blocks that show reserved syntax on
  purpose now say so in the line above them.

- **`mzt instruments check NAME --live` and `mzt doctor --live`.** A profile
  could validate, its binary sit on PATH, and every dispatch still fail:
  a flag the installed CLI rejects (crush `--yolo`, since April), an
  output shape the parser cannot read (Claude Code's verbose event array),
  a provider that is not configured. `--live` sends one real prompt through
  the conductor's own backend factory (plus a schema-mode prompt when the
  profile declares `json_schema_flag`) and reports what a sheet would see,
  classified by the baton's own classifier. First run on Pallas: claude-code
  plain and schema ok; codex-cli, opencode, opencode-glm-5.3-flash, goose,
  antigravity ok; crush rate-limited by its free provider; gemini-cli
  unavailable (#469).

- **`mzt run --require-lock LOCK` (GH #403).** An opt-in admission gate: the
  conductor verifies the composition lock the release gate wrote
  (`check_score_release.py --write-lock`) against the exact score bytes it is
  about to parse, and refuses a missing, stale, unreadable or malformed lock
  with that reason. Unlocked submission is unchanged. Also: the output hub
  strips terminal control sequences before redaction and fan-out, so an
  interactive transcript cannot drive a watcher's terminal and a credential a
  TUI redraws around cursor moves is still redacted.

- **Orchestra view (GH #468).** `mzt dashboard` serves `/orchestra`: every score
  on stage, each sheet a cell coloured by state, the instrument it plays on, a
  beat log, live over SSE, with a replay transport for any recorded day. The
  conductor's `OrchestraRecorder` projects EventBus events into a public-safe
  vocabulary (score, sheet, instrument, outcome, time, plus a short note —
  fallback transitions, retry attempt numbers, and the verbatim chain-refusal
  reason, which can name the child score's path),
  keeps the fleet snapshot, appends beats to `~/.marianne/orchestra/<day>.jsonl`,
  and serves `daemon.orchestra.snapshot|replay|stream` over IPC. The baton
  adapter now publishes `sheet.dispatched`. `orchestra:` daemon config. A copy
  of the 20 Oct proof day ships as `docs/orchestra/2026-10-20.jsonl`.
- **Per-job scratch root (GH #460)** — Every CLI and interactive instrument now receives `MARIANNE_SCRATCH=<workspace>/.marianne-scratch`; the conductor deletes that directory when the job reaches COMPLETED or FAILED (`workspace_lifecycle.scratch: remove|keep`, default `remove`). Probe clones, mutants and worktrees belong there instead of a system temp directory, where 112 orphaned clones had accumulated on one box.

#### Daemon Mode (marianned) — 2026-02-11
- **Daemon service** (`marianned start/stop/status`) — Long-running conductor process that manages multiple concurrent score runs
- **IPC layer** — Unix domain socket with JSON-RPC 2.0 protocol for client-daemon communication
- **Score-run manager** — Tracks conductor runtime records and handles submission/cancellation
- **Resource monitor** — Tracks CPU/memory/process usage with configurable limits
- **Health checks** — Liveness/readiness probes for daemon health monitoring
- **Cross-score scheduler** — GlobalSheetScheduler for sheet-level coordination across score runs (built and tested, not yet wired)
- **Rate limit coordinator** — Shares rate limit state across concurrent score runs (built and tested, not yet wired)
- **Backpressure controller** — Adaptive load management to prevent resource exhaustion; gates score submission based on memory pressure
- **Learning hub** — Centralizes pattern learning across all daemon-managed score runs
- **`mzt config`** — New command with subcommands: `show`, `set`, `path`, `init` for daemon configuration management
- **`mzt list`** — Now routes through daemon; shows active score runs by default, `--all` for everything, `--limit` defaults to 20
- **Systemd integration** — Service file and installation scripts for production deployment
- **Dashboard wiring** — Dashboard routes through daemon when available
- **MCP wiring** — MCP server routes through daemon for coordinated execution

#### MCP Server — 2026-01-24
- **`mzt mcp`** — Start MCP (Model Context Protocol) server for external AI agent integration
- **Score-run tools** — Run, status, pause, resume, cancel via MCP
- **Artifact browsing** — List and read workspace files through MCP
- **Log streaming** — Access score-run logs through MCP tools
- **Validation resources** — Expose validation and score-authoring guidance through MCP
- **Configuration resources** — Expose score configs as MCP resources

#### Dashboard Enhancements — 2026-01-16 to 2026-02-12
- **Score-run control endpoints** — POST endpoints for start, pause, resume, cancel, delete conductor records
- **Sheet details** — GET endpoint for individual sheet status, logs, validation, costs, tokens
- **SSE streaming** — Real-time score-run status and log streaming via Server-Sent Events
- **Log endpoints** — Static download, info metadata, follow mode for log files
- **Artifact management** — Secure file listing and reading within workspaces
- **Template system** — Browse, filter, download, and use configuration templates
- **Config validation** — Three-phase YAML validation (syntax, schema, extended checks)
- **Daemon status** — Dashboard can detect and report daemon health
- **Authentication system** — Three modes: `disabled`, `api_key`, `localhost_only` (via `MZT_AUTH_MODE`)

#### Fan-Out & Parallel Execution — 2026-02-09
- **Parameterized stage instantiation** — Eliminates manual sheet duplication for parallel workflows
- **Fan-out/fan-in patterns** — Automatic variable instantiation across parallel stages
- **Sheet dependency DAG** — Directed acyclic graph for sheet ordering and parallel execution

#### Self-Healing & Enhanced Validation — 2026-01-05 to 2026-01-15
- **`--self-healing` flag** — Automatic diagnosis and remediation when retries are exhausted
- **`--yes` flag** — Auto-confirm suggested fixes with self-healing
- **Enhanced `mzt validate`** — Comprehensive pre-execution checks beyond schema validation (V001-V103 codes)
- **Built-in remedies** — Automatic workspace creation, path fixes, suggested Jinja fixes, diagnostic guidance
- **Staged validation** — Fail-fast behavior for ordered validation checks
- **Compound conditions** — Support for `and` conditions in validation rules

#### Pause/Modify Workflow — 2026-01-13
- **`mzt pause`** — Gracefully pause running score runs at next sheet boundary
- **`mzt modify`** — Combine pause + config update + optional resume in one command
- **Auto-reload config on resume** — Config automatically reloads from the original YAML file on resume (cached snapshot is fallback). Use `--no-reload` for deterministic replay from cache

#### Learning System Enhancements — 2025-12-27 to 2026-02-04
- **Pattern broadcasting** — Automatic pattern sharing across score runs with auto-retirement
- **Metacognitive reflection** (`patterns-why`) — Analyze WHY patterns succeed, not just that they do
- **Shannon entropy monitoring** (`patterns-entropy`) — Detect pattern population collapse
- **Exploration budget** (`patterns-budget`) — Entropy-driven budget adjustments to maintain diversity
- **Epistemic drift detection** (`learning-epistemic-drift`) — Track belief/confidence changes as leading indicators
- **Effectiveness drift** (`learning-drift`) — Compare pattern performance across time windows
- **Activity monitoring** (`learning-activity`) — Recent pattern applications and learning events
- **Entropy response** (`entropy-status`) — Automatic budget boosts and quarantine revisits on low entropy
- **Learning insights** (`learning-insights`) — Actionable patterns from stdout/stderr analysis
- **Trust scoring** — Patterns have trust levels affecting application probability
- **Quarantine system** — Low-performing patterns quarantined and periodically reevaluated
- **Escalation feedback loop** — Human feedback integrated into pattern learning
- **Output pattern extraction** — Automatic pattern discovery from execution output

#### Worktree Isolation — 2026-01-15
- **Git worktree isolation** — Each job runs in a detached worktree for safe parallel execution
- **Automatic cleanup** — Worktrees cleaned up on success, preserved on failure for debugging
- **`isolation` config section** — Opt-in via YAML configuration

#### Additional Commands — Various dates
- **`mzt errors`** — Color-coded error listing grouped by sheet (red=permanent, yellow=transient, blue=rate limit)
- **`mzt diagnose`** — Comprehensive diagnostic reports with `--include-logs` for inline log content
- **`mzt history`** — SQLite-based execution history with sheet/attempt filtering
- **`mzt recover`** — Re-validate failed sheets without re-executing them
- **`--fresh` flag** — Delete existing state for clean re-runs and self-chaining jobs
- **`--watch` mode** — Continuous status monitoring with configurable refresh interval
- **`--escalation` flag** — Human-in-the-loop escalation for low-confidence sheets

#### Backends
- **Ollama backend** — Local model execution via Ollama

#### CLI Improvements — 2026-02-04 to 2026-02-14
- **CLI modularization** — Single `cli.py` file restructured into `cli/` package with submodules
- **Runner modularization** — Single `runner.py` restructured into `runner/` package (9 modules)
- **Learning store modularization** — Single `global_store.py` restructured into `store/` package (14 modules)
- **Config modularization** — Single `config.py` restructured into `config/` package (6 modules)
- **Error modularization** — Single `errors.py` restructured into `errors/` package (5 modules)
- **26+ registered commands** — Up from 7 in v0.1.0
- **Default active-only listing** — `mzt list` shows only active score runs by default

#### Examples
- **`docs-generator.yaml`** — Documentation generation orchestration
- **`quality-daemon.yaml`** — Quality improvement via daemon
- **`quality-continuous-daemon.yaml`** — Continuous quality improvement with daemon
- **`quality-continuous.yaml`** — Backlog-driven quality improvement (18 sheets, fan-out + tool agents)
- **`quality-continuous-generic.yaml`** — Language-agnostic quality improvement (16 sheets, fan-out)
- **`parallel-research.yaml`** — Multi-source parallel research
- **`parallel-research-fanout.yaml`** — Fan-out parameterized parallel stages
- **`issue-fixer.yaml`** — GitHub issue fixing workflow
- **`agent-spike.yaml`** — Agent exploration and experimentation
- **`observability-demo.yaml`** — Logging, error tracking, and diagnostics demo
- **`cross-sheet-test.yaml`** — Cross-sheet context testing

#### Non-Coding Domain Examples — 2025-12-25
- **`systematic-literature-review.yaml`** — PRISMA-compliant dual-reviewer research (8 sheets, 17 validations)
- **`training-data-curation.yaml`** — ML dataset creation with inter-annotator agreement (7 sheets, 24 validations)
- **`nonfiction-book.yaml`** — Non-fiction book authoring via Snowflake Method (8 sheets, 31 validations)
- **`strategic-plan.yaml`** — Multi-framework strategic planning (8 sheets, 39 validations)

#### Observability & Reliability
- **Cross-sheet context** — Multi-phase workflows can share context between sheets
- **Hook logging** — Post-success hooks with structured logging
- **Execution history** — SQLite-backed attempt tracking
- **Process management** — Reusable ProcessManager, orphaned process cleanup
- **Circuit breaker** — Async circuit breaker for failure isolation
- **`mzt doctor` instrument hygiene (GH #443)** — Reports every user- and venue-layer profile that no score the conductor has been given names and no class chain includes, as an "Instrument hygiene" section and `unreferenced_profiles` in `--json`; never deleted by `--clean`. A seat is an alias over an existing profile; a profile nothing names is archived by a person

### Changed
- **`mzt stop` is non-blocking; `mzt start` supersedes a draining conductor (GH #115)** — After the active-job safety check and any confirmation, `mzt stop` sends the signal and returns without waiting for the conductor to exit. A new `--wait` option opts into a bounded exit wait: `--wait=` for the default 30s budget, `--wait=SECONDS` for a custom one (`--wait=0` keeps signal-and-return; budgets must be finite and non-negative; an exceeded budget exits 1 naming the still-draining PID). A graceful drain can still take up to `shutdown_timeout_seconds` (default 300s). `mzt start` no longer reports a still-draining conductor as "already running": it probes the shutdown state, waits up to 30s for the old process to exit, then starts a fresh conductor — never a second one beside the dying process, which holds the PID-file flock and socket lock; a drain that exceeds 30s exits 1. A silent probe is treated as running (GH #455: slow is not dead), and the serving-conductor exit-0 contract (GH #459) is unchanged. `mzt restart` now sequences through the same bounded stop wait (`stop_conductor(wait=30.0)`) instead of a separate `wait_for_conductor_exit` call.
- **Shipped examples route through capability classes (T6)** — 20 example scores that pinned `instrument: claude-code` with no model pin now say `instrument: strong`, so they resolve per machine through the class-map layers (`~/.marianne/classes.yaml`, `.marianne/classes.yaml`, packaged default) instead of failing where one CLI is absent. Kept on a profile by design: `advanced/instrument-showcase.yaml` (its subject is the profiles), `engineering/lovable-generator.yaml` (root model pin, V323), and `finance/24x7-trader/*` (per-sheet model pins). `docs/getting-started.md` and `docs/README.md` show the same form.
- **"Batch" → "Sheet" terminology** — All config keys, template variables, CLI flags, and internal references renamed from `batch` to `sheet`
- **`mzt list` default behavior** — Shows active score runs only (queued, running, paused); use `--all` for everything
- **`mzt list` requires daemon** — Routes through `marianned`; use `mzt status` for direct file-based status
- **Default JSON output format** — Backends default to JSON output to prevent streaming mode errors
- **Completion mode honors `retry.completion_threshold_percent`** — A result must exceed the threshold (default 50) to enter completion mode; results at or below it spend the ordinary retry budget, and a value of 100 never enters completion mode
- **`cost_limits.max_cost_per_sheet` is enforced** — The baton arms the per-sheet cap at registration and recovery; an attempt that takes a sheet above its cap fails the sheet, including an otherwise successful attempt
- **Root `max_wall_seconds` no longer caps a one-shot score** — It is now the scheduled-tick window `schedule.max_wall_seconds` (the root spelling warns V328 for one release); the research generator no longer emits it for one-shot scores, whose sheet timeouts remain in force

### Fixed

- **`mzt history` shows every attempt (GH #488).** It read a SQLite table nothing wrote, so every job answered "requires the SQLite state backend". History is now a view over each sheet's persisted attempt records: instrument and model, outcome, exit code, duration, and the redacted cause of a failure, newest first. `mzt diagnose` counts the same records. The never-called `record_execution` writer and its SQLite readers are removed; the table stays so older databases open unchanged.

- **The instrument supervisor no longer scans all of `/proc` every 50 ms (GH #490).** Its run loop counted adopted children by reading every process's stat file twenty times a second, about 18% CPU per running sheet on a host with ~550 processes. It now reads the kernel's per-task children list and keeps the full scan for the exit sweep.

- **Log lookups stop at the first matching line (GH #491).** Deciding whether the conductor log held any line for a job read the whole file, 24 MB on a busy host, on every MCP `get_logs` call and dashboard stream.

- **`mzt instruments resolve SCORE` shows what every sheet's binding resolves to.** One row per sheet — bound through (alias, class or direct), profile, model, fallback chain — resolved the way admission resolves them on this machine, plus a count of distinct profile+model bindings. The examples QA found seven scores whose prose claimed independent reviewers while every voice resolved to one binding (GH #485); this is the command that would have shown it.

- **`{{ instrument_alias }}` names the score alias a sheet was bound through.** `instrument_name` renders the profile an alias resolved to, so a seat census that joins evidence against a roster of alias names (condorcet-s-premise's ten seats) collapsed as soon as two seats shared a profile. The new core template variable is the `instruments:` key for an alias-bound sheet and `""` otherwise; it is stable across fallback where `instrument_name` is the profile that actually ran. Unblocks GH #485 family 3.

- **Six more gates stop rejecting honest output or accepting empty output (examples QA, GH #480).** systematic-literature-review refused an integer kappa, a full-text review with nothing excluded, and a star-based quality tool its own protocol allows. invoice-analysis accepted a parsed invoice with bare `line_items`, three reviews that merely existed, and a report containing the word "Recommendation"; it now checks populated line items and both parties, a PASS/FAIL/WARNING per check, a compliance score, an explicit anomaly finding, action items and a named disposition. firing-the-pass's ephemeris gate ran its jq checks without errexit and `all()` over an empty shower list was true; the chart gate never required a chart. negative-treatment-watch's canary passed with zero probes; it requires the five by name. dead-letter-quarantine demanded "Root cause" of an analysis its own prompt permits to say the quarantine was empty.

- **Eight examples whose prompts contradicted their own DAG, runtime or inputs (examples QA, GH #484 family 5).** instrument-showcase's E1 escalated findings to E2 and E2 to E3 while the three tiers ran in parallel from the same classification, so an escalation had no consumer — E2 reads E1's results, E3 reads E2's, and the dependency block is the cascade. echelon-repair's E3 read E1/E2 output its dependencies did not order. market-close made broker and benchmark calls before testing the fermata; the guard is first. quality-continuous's conflict recipe ran `git checkout -- .` then `git stash drop` on the only copy of the fix and hard-coded `origin/main`; the stash becomes a named branch first and every pull/fetch uses the tracking branch. quality-triage fed Jira/Linear/CSV keys to `int()`; ids keep the tracker's form. saas-app-builder's database builder mandated SQLite while `tech_stack` asked for PostgreSQL; it builds the contract. marketing-content's email 3 was told to invent a closing date and "X engineers already using"; it argues from what the brief supports. simple-sheet said "process items 1 to 5" with no items and no processing; `items` is a variable and the task is stated.

- **Five more examples stop carrying their sample outside `prompt.variables` (examples QA, GH #484 family 1).** skill-builder's "skill-agnostic" template still sent every learner to the kitchen with a fermentation journal; it speaks in `skill.domain` and a generic practice log. thinking-lab's five lens seeds reasoned about Turing completeness and the Chinese Room whatever `question` said; each seed is now a lens-shaped prompt applied to the question. dialectic's three traditions defended or dissected AGI by name; their methods are the tradition's, applied to `proposition`. marketing-content's briefs hard-coded CI/CD pain points, "developer marketing" and the sample product's name; they read `product`/`campaign`. contract-generator's prompts mandated HIPAA and an existing NDA and its gates grepped for `HIPAA` and both sample company names; the prompts read `special_terms`, the gates read `client_name`/`provider_name` (flat copies for the validator) and the clause heading.

- **Four examples stop sharing state they do not own (examples QA, GH #482 family 5).** quality-triage kept its issue-dedup corpus at `/tmp/existing.json`, shared by every run and every project on the host; it lives in the workspace. market-open read yesterday's `today-date.txt` when this morning's pre-market was missed and ingested yesterday's slate; it takes today's date and falls through to the documented empty slate. cathedral-construction's two-path install copied the finished score into `scores/rosetta-corpus/proof-scores/` of whatever repository contained it; the second copy lands in the workspace, publishing stays the composer's commit. lovable-generator scaffolded the project from one of three parallel voices while all three were gated on `package.json` + `npm install`; the scaffold is movement 1's deliverable, gated before fan-out, and movements 2-3 partition file ownership so voices read anything and write only what they own.

- **echelon-repair names its target, score-composer requires its design doc, weekly-review archives its Delphi rounds (examples QA, GH #482/#484).** echelon-repair told the musician to audit "the current working directory (the project root)" and the README said to `cd` there first — musicians start in the workspace, so the audit inspected its own output directory; a `target_project` variable (per run: `--var target_project=/abs/path`) is what every stage reads. score-composer injected the design document with `required: false`, so a wrong `doc` path ran a composer that invented a design; it is required. weekly-review's header promised dated `round1`/`round2` files per reviewer and only the aggregate, synthesis, decision and threshold were archived, so next week overwrote the review provenance; the save step copies all six and a gate checks them.

- **prelude-cadenza-example ships its seed files (examples QA, GH #482 GS F14).** The example injected four files from the workspace that nothing created and the header told the reader to copy from a directory that does not exist, so every sheet ran with its prelude and cadenzas silently missing while the prompts said the methodology "has been injected". The four files now live in `assets/prelude-demo/`, are injected from `{{ score_dir }}` as `required: true`, and their content matches the gates (`### Target Audience`, Key Points, `### Overview`/`### Summary`).

- **weekly-review's reviewers answer questions the aggregate now asks (examples QA, GH #484 AD F32).** Every reviewer was told to answer "the operator's three tracked questions" from an aggregate section that no stage wrote. `tracked_questions` is a variable with three defaults to edit; the stage-1 aggregate prints them under "Operator's Tracked Questions", a stage-1 gate requires the section, and the reviewers and synthesis reference the variable's count.

- **Producer and consumer agree in three more examples (examples QA, GH #484 family 2).** parallel-research-fanout's setup listed three hard-coded domains and its synthesis read `02-search-1..3` whatever `search_domains` and `fan_out` said; both now iterate `search_domains`. pre-market told the musician four files were "already in your prelude" when only `02-strategy.md` is; the other three are read from the workspace. invoice-analysis's extraction emitted vendor/client as bare names while the compliance reviewer verified a tax ID and addresses it never received, and both reviewers carried the sample contract's `15%`/`Net 30`/`99.95%` instead of `contract_terms`; the extraction carries identity fields and the reviewers read the variable.

- **issue-solver's `gh` mutations name the repo; quality-triage's harness scripts bake the project root in (examples QA, GH #482).** The solver claimed and closed issues with `gh issue comment`/`close` and no `--repo`, so `gh` resolved the repository from the musician's current directory — the solver workspace, not the target. quality-triage's generated test/lint/typecheck scripts started with `cd "$(git rev-parse --show-toplevel)"`, which from that workspace found the Marianne checkout. Both now use the score's own `repo` / `project_root`.

- **Three examples stop promising what the runtime does not do, or baking in the author's sample (examples QA, GH #482/#484).** worktree-isolation claimed a private worktree per reviewer; the runtime makes one detached worktree per JOB from `workspace` (which must be the git repository) and parallel sheets share it — the score, its header and the README now say so, and `fallback_on_error` is false so a non-git workspace fails instead of silently reviewing un-isolated. quality-continuous ran `find .` and `git log` from whatever directory the musician started in; it takes a `project_root` and every shell step runs there. dinner-party's prompts carried the sample party's `6:30 PM`, `$200 / 4`, and three guests by name outside `prompt.variables`, so editing the variables left the sample behind; the budget is numeric and the per-track share computed, and guest-specific instructions derive from the guests' own notes.

- **Injection items take a `max_bytes` cap (GH #474).** A directory cadenza injects every file at every sheet, so a ledger that grows with every sheet (an `active/` directory past ~400 KB in a 21 Oct lifecycle run) shipped as an ever-larger prompt until the conductor-wide token warning fired after assembly, naming no item. `InjectionItem.max_bytes` caps one item (a directory sums its files): over it, a `required` item fails the sheet before execution with the measured size and an optional one is dropped with a warning; V334 warns at validate time when a static path is already over. Score-side rotation into `archive/` stays the score owner's job.

- **`instruments check --live` proves the schema shapes real scores send (GH #486).** The schema probe asked for `{n: 7}` against a flat object, so a profile that mangled a nested discriminated union — the shape every intake schema takes — passed the probe and failed on its first sheet. The probe's schema now nests a `oneOf` under a required property and checks the chosen branch came back; the refused shape (a top-level combinator) stays V332's finding at validate time, since a probe must pass on a correct profile. Verified live against claude-code 2.1.307 (9 s).

- **Two more gates that rejected honest output (examples QA #480, AD F3/F19).** instrument-showcase's manifest gate demanded at least one finding, so a clean project failed while a one-field fabrication with a null summary passed; zero findings with a diagnosis summary now passes and every finding needs an echelon and a title. Its E1 status gate accepts NEEDS UPGRADE and "no E1 findings". pre-market's slate gate built the slate path with a Python f-string over a *shell* variable (`today`), so a valid `[]` no-trade slate died with NameError; the shell now passes the path as argv. Both replayed through the real engine: honest-empty passes, fabricated/malformed fails.

- **issue-triage and quality-triage call the scanner by its real name and feed it what it scans.** Both scores ran `cisco-ai-skill-scanner` (the PyPI distribution; the installed command is `skill-scanner`), installed it with a bare `pip install` (refused on every PEP 668 host), and passed it a temp *file* (it scans a *directory* holding a `SKILL.md` — a file path is "not a directory", exit 0, no JSON — so every verdict was `scanner_nonzero_exit`/FLAGGED or a musician's improvisation). Stage 3 now installs with `uv tool`/pipx, probes `skill-scanner`, wraps each issue as a SKILL.md under a fixed manifest, and keys the verdict on `is_safe` and HIGH/CRITICAL content findings (the manifest's own policy notes are ignored). Replayed against the real scanner: a clean issue is CLEAN, a prompt-injection body is FLAGGED `data_exfiltration`, a shell-dangerous title is FLAGGED.

- **V333 names the two command shapes that shell-quoted placeholders defeat (GH #477).** A `command_succeeds` command that splits a placeholder with a bash array literal (`xs=({list})` is one element) or splices a prompt variable whose default needs quoting (`~/path`, a space) inside a `python3 -c`/`node -e` string literal now warns at validate time, with the working form in the suggestion. Built-ins and bare defaults are left alone.

- **A self-chain survives `mzt resume --config <copy>`, and a refused chain is logged.** The concert trigger decided "this is a self-chain" by comparing the child's path with the parent's `config_path`; after a resume from a copy the parent's path is the copy, so the child resolved to a sibling file, derived the parent's own job id, and was refused as an active duplicate — the 13-sheet issue-solver finished green and chained nothing, with no log line. Identity is now the job id the child would get; a refused submission logs `flow.concert.refused` with the reason, an accepted one `flow.concert.submitted`, both reach the observer stream (`baton.flow.concert_refused` / `concert_submitted`), and the orchestra view shows a refused chain with its reason.

- **A dropped root block warns, not whispers.** A top-level key the schema does not know whose value is a mapping or list (`deliver: {1: {to: …}}` from an unmerged branch) was V011 INFO — hidden unless `mzt validate --verbose` — so a product learned at runtime that nothing had written its output file. It is now V010 WARNING naming the block; a stray scalar stays INFO.

- **Schema sheets on claude-code no longer hang under `--effort max`.** Claude Code 2.1.307 with `--effort max` and `--json-schema` emits nothing until the sheet timeout kills it (0 bytes at 150 s with a real 8.8 KB intake schema; `--effort high` answers in 23 s). CLI profiles gain `cli.command.schema_extra_flags` — flags a schema sheet gets *instead of* `extra_flags` (unset inherits, `[]` drops them) — and the shipped `claude-code` profile sends `--effort high` there. Found by a peer conductor's intake wall (cut4g); reproduced against the binary before the profile changed.

- **hello-local no longer needs a private instrument profile (examples QA, GH #485).** The pure-local first score bound `opencode-gemma`, a profile that existed only on the author's machine, so `mzt validate` passed for the author and `mzt run` failed for everyone else. It now pins `ollama/gemma4:26b` on the built-in `opencode` profile through a score alias; the README states the OpenCode `ollama` provider it needs.

- **Review fan-outs that claimed independence now configure it (examples QA, GH #485).** Seven examples advertised independent reviewers, expert analysts or parallel investigators while every voice resolved to one profile and model (project-roadmap's review was two aliases plus the score default; quality-triage's four personas, candidate-screening, invoice-analysis, parallel-research-fanout, worktree-isolation and source-triangulation were one class). Each fan-out now binds one alias with `chain: [codex-cli, claude-code, antigravity]` and `select: [spread, balance]`, so its voices start on three different heads with the rest of the chain as fallbacks — independence by construction, not by persona prose.

- **Example prompts that contradicted themselves or emitted broken code (examples QA, GH #484).** the-economic-injury-line's sheet-1 heredoc rendered each county as `{...` with no closing brace (a `}}` that Jinja swallowed), so `deployment.json` could never be written — every rendered Python heredoc in the examples now parses (86 scanned, rendered per sheet). research-agent's relay wrote "Primary Data Investigation Brief" while the investigator looked for "Primary Investigation Brief". issue-triage's stage 4 called an empty corpus a failure that stage 3 had just allowed. simple-sheet carried an undefined-variable trick nothing read (V101 row leaves the baseline).

- **Example inputs resolve from the score, not from the author's tree (examples QA, GH #482/#483).** The six finance scores injected their libretto and technique files through `../../../examples/finance/24x7-trader/…` relative to the workspace — one directory too far, so every one of 22 optional preludes was silently absent while the prompts said the content "has been injected"; `hello`/`hello-local`/`hello-setup` reached their shipped assets through `{{ workspace }}/../../examples/…`, which breaks the moment a user moves the workspace as the README tells them to. All 29 now anchor on `{{ score_dir }}` (`{score_dir}` in validations) and every score-shipped injection resolves. The author's GitHub identity is no longer a silent default: `github_owner: "Mzzkc"` in nine rosetta scores and `repo: "Mzzkc/marianne-ai-compose"` in issue-solver are `[CHANGE THIS]` placeholders the scores already refuse to run on.

- **A failed CLI attempt keeps the API's refusal as its witness (GH #486).** When Claude Code's terminal JSON reports `is_error` with an `api_error_status` (a 400 for a tool `input_schema` the API rejects: a top-level `oneOf`, a missing top-level `type`), the backend kept neither the message nor a stderr tail, so the job could only say E009 after 5 s. The message is now the attempt's error and its stderr tail, typed `api_error`; a 4xx other than 429 classifies as E301 (the request's fault, not retried as weather), 429/5xx stay transient. Recorded as a capture fixture and replayed through the real parser.

- **`mzt run --dry-run` honours `--var` (examples QA, 21 Oct).** The preview rendered the YAML's placeholder values (`[CHANGE THIS]`) whatever `--var` said, so the advertised safe verification path verified nothing; the runtime-variable merge now lives in `core/config/job.py` and both the conductor's submit/resume seam and the preview apply it. V106 also warns when a `content_contains` pattern carries regex syntax (a literal no prose contains); V201 also scans validation commands for Jinja.

- **A schema with `$schema` no longer breaks Claude Code schema mode (GH #479).** `claude -p --json-schema` exits 1 on a schema whose top level carries `$schema` (it tries to resolve the draft URL); the CLI backend now drops top-level `$schema` and `$id` before building the argument, and the `instruments check --live` probe schema carries a `$schema` so the probe fails the way a sheet would if the strip regressed.

- **`mzt validate` no longer re-parses every instrument profile a dozen times.** The profile loader parsed each YAML with PyYAML's pure-Python parser (~35 ms per profile) and every validator that asked for profiles loaded them all again, so a host with 50 personal profiles paid ~20 s per validate and the suite's validate-driven tests tripped their 30 s timeouts under load (the #308 solver's final verification failed its conductor-side suite three times on exactly those). The loader uses the C SafeLoader (same semantics) and memoises each file on (path, mtime, size): the same bytes parse once per process, an edited profile is re-read, hot reload and `instruments check` are unchanged. simple-sheet validate: 24 s → 0.8 s on that host.

- **Example validation gates that could never pass, or could never fail (examples QA, 21 Oct).** Twelve folded `command: >` scalars (dead-letter-quarantine ×6, echelon-repair ×3, prefabrication ×2, instrument-showcase) collapsed multi-line `python3 -c` bodies into one line with a leading space, so a correct artifact failed with `IndentationError`; they are literal blocks now and every body parses. `lovable-generator`'s build gate and `quality-continuous`'s test gate returned `tail`'s status, so a failing build or suite passed — `set -o pipefail`. `quality-continuous`'s commit gate was `… && echo passed || echo 'no recent commit'`, true on both branches and in no repository — it now runs in the workspace and exits non-zero without a recent commit. `codebase-rewrite`'s unit/integration gates accepted a `**Results:** FAIL` report; each tier now runs the bound test command in `project_root` (`bash -c {test_command}` — the placeholder is one shell-quoted word, GH #477) and requires the report's verdict to match the harness's exit, so a PASS over a red suite fails while an honest FAIL passes on to the review, and a FAIL unit report skips integration — the fail-fast the header promised. Its triage gate accepts an empty triage (every candidate SKIP) so a converged codebase can reach the gate. The corpus baseline shrank by the twelve V303 rows.

- **V105 accepts a declared loop index as a validation placeholder (GH #475).** A validation path scoped to a loop (`{pass_no}`, the documented form) failed `mzt validate` with "undefined variable" while the baton defined it at runtime; the check now seeds every `sheet.loops.<span>.index` as the V101 side already did (#448).

- **Fewer instruments see every credential (GH #335).** The `opencode` builtin
  now passes no credential variables (it reads its own store) and `goose` only
  its providers' variables; both verified live. `mzt doctor` warns, by name,
  about each ready CLI profile that still inherits the full environment while
  a credential-shaped variable is set (variable names are counted, never
  printed with values).

- **Diagnosable fallback paths (GH #308, MN-007).** Profile and capability-class
  preflight failures now emit `validation.profiles_unavailable_skip` warnings
  with check identity and exception information while retaining existing
  skip/fallback results. These are log events, not new validation findings.
  Previously silent runtime fallbacks now report their failures, including
  instrument metadata, judgment context, dashboard data, profiler storage,
  backend initialization, output decoding, and schema type hints; optional
  probes log at DEBUG. Ruff BLE001 and an AST regression guard enforce reviewed
  broad exception boundaries. The validation corpus fixture now exercises the
  real profile loader and records reviewed diagnostics; timestamp tests no
  longer swallow assertions, and dashboard CLI mocks restore only their own
  module entry so later route tests retain their injected state.

- **Pausing then stopping no longer costs five minutes.** Graceful shutdown
  waited on every job task, and a PAUSED job's task waits for a resume that
  cannot come during shutdown, so `mzt stop` after `mzt pause` always ran the
  full `shutdown_timeout_seconds` (300 s) with `running_jobs: 0` and
  `mzt stop --wait=300` reported a timeout. A paused job with no musician in
  flight is now cancelled at once and stays PAUSED; one with a musician still
  running is waited on as before.
- **Structured output through Claude Code works, and validate catches the
  case that cannot.** The `claude-code` builtin declares
  `json_schema_flag: --json-schema` (verified on Claude Code 2.1.306: the
  validated object arrives as the terminal result's `structured_output`). A
  CLI that prints an array of stream events for `--output-format json`
  (Claude Code with `verbose: true` in its settings) is now read from its
  last `type: result` object, in schema mode and plain mode alike; before,
  the result path came back empty. V311 now also warns when a sheet bound
  straight to a CLI profile asks for a `json_schema` `response_format` that
  profile cannot enforce, naming the sheets, instead of letting every
  dispatch be refused at run time. `opencode` has no schema flag (its
  `--format json` is a raw event stream), so it stays without one.
- **No broad `except` passes in silence any more (GH #308, first slice).**
  The 22 `except Exception: pass` sites now say why they catch broadly and
  leave a log line (DEBUG for expected teardown and best-effort fallbacks,
  WARNING where state can be lost: an unreadable scratch policy, a baton
  loop that errored at shutdown, an MCP reset that failed on a failed
  dispatch). `mzt doctor` now reports a conductor whose socket accepts but
  does not answer as running, not absent, so it stops telling an operator
  to start a second one (GH #455 rule: absence needs two proofs).
- **Dashboard pre-exposure hardening (GH #387).** The artifact read opens
  with `O_NOFOLLOW`, verifies the descriptor is the regular file the path
  named (device and inode against a fresh `lstat`), and serves its bytes;
  a symlink swapped in between the path check and the open is refused with
  400 instead of followed. `mzt dashboard --host <non-loopback>` prints a
  red **Exposed bind** panel before starting when `MZT_AUTH_MODE` is still
  `localhost_only`, or `api_key` with no keys or the localhost bypass on.
  `MZT_PUBLIC_ORCHESTRA=true` is the shape for mzt.dev: `/orchestra`, its
  read-only API and `/static` are served to every client, every other path
  keeps the auth mode's rule (the docs had promised a public orchestra page
  that the middleware then refused with 403).
- **An installed harness with no usable provider no longer stalls a deep
  chain (GH #389).** `cli.errors.unavailable_patterns` lets a profile name its
  harness's own startup refusal ("No provider configured", "Provider not
  found"); a match is typed `INSTRUMENT_UNAVAILABLE`, so the sheet advances
  its fallback chain on the first attempt without charging a retry, the same
  path a missing binary takes since #418. The shipped `goose`, `crush` and
  `opencode` profiles carry their measured refusal text. Every
  `cli.errors.*_patterns` regex is now compiled when the profile loads, so a
  bad pattern is refused there rather than mid-attempt.
- **The `crush` profile no longer sends `--yolo` (GH #471).** crush v0.98.1
  rejects the flag in every position, so every dispatch exited 1 in 60 ms
  before reaching a provider and was retried as transient. `crush run` is
  unattended on its own; the profile now sends only model, cwd and quiet.
- **Startup failures wait before retrying (GH #470).** An attempt that exits
  non-zero within `retry.startup_failure_window_seconds` (60) did no work;
  its retry now waits at least `retry.startup_failure_floor_seconds` (120)
  instead of the ordinary backoff. Two lanes burnt a three-attempt budget in
  75 s against a transiently locked opencode store. Timeouts, signals and
  rate limits are unaffected; a longer exponential backoff is unchanged.

- **Judgment counter persists on resolution (GH #382).** `judgment_count` is
  the durable `max_judgments_per_sheet` cap; it is now written to the registry
  as soon as a judgment resolves instead of riding the next state-transition
  persist, so a crash in between cannot reset it. The startup sweep's
  docstring now states that it finds nothing on the restart path by
  construction and that restart-recovered fermatas reach the judge through
  the adapter's re-emit.

- **`mzt watch` follows interactive sheets (GH #381).** The interactive driver
  tails the pipe-pane transcript once per poll and hands each new slice to the
  live output sink; the interactive backend exposes `set_output_callback`, so
  the adapter's existing wiring reaches it. Tailing starts at offset 0 because
  the transcript is truncated at session start (#398); a sink failure ends
  tailing, never the attempt. The dead `set_output_log_path` base-class
  surface is removed.

- **issue-solver: FLAGGED issues rank last in selection, and the close
  comment carries scope.** With a zero-edge DAG the selector took the lowest
  issue number regardless of the scanner verdict, so the first chained
  iteration started on a security-design issue whose body had been redacted
  to its title. Selection order is now tier, CLEAN before FLAGGED,
  dependencies, number. The automated close comment summarizes the branch
  against its merge base (an empty receipt commit's `HEAD~1` diff printed
  nothing), names the verify.sh result, and copies the synthesis report's
  Deferred section so a reader sees what the iteration did not do.

- **`mzt doctor` finishes in seconds on a long-lived host.** The instrument
  hygiene scan parses every score the conductor was ever given; the pure
  Python YAML loader took 41 s over 1,374 files and timed out every test that
  invokes doctor. It now uses the C loader (3 s) and reads the registry of the
  conductor the CLI addresses (clone-aware). Tests never read the developer's
  registry through doctor (conftest).
- **A nested instrument no longer inherits the outer job's `MARIANNE_SCRATCH`.**
  A CLI backend with no working directory now strips the variable instead of
  passing the environment through, so Marianne's own test suite run as an
  instrument of a Marianne job sees the documented "nothing exported".
- **issue-solver: one full-suite run per pass, and a loop cap a pass cannot
  exhaust.** The implementation pass ran the harness twice (Step 3 and again
  inside verify.sh); on a 14-minute suite that timed the pass out at 3600 s
  and one pass cost $16.80 against a $12 cap, so the six-pass loop was a
  single shot. Step 3 runs only the touched tests; `cost_limit_usd` is 40.

- **Concert trigger children inherit `--var` runtime variables.** A self-chaining
  score's second iteration re-read its YAML defaults and could solve the wrong
  project; the child is now submitted with the parent's runtime variables, the
  same way resume re-applies them.
- **issue-triage / issue-solver agree on one stable triage path.** Both scores
  drop their score-relative `workspace:` for the conductor-managed
  `~/workspaces/<name>` (#58); the solver's `triage_workspace` default is that
  path; every Python read of it expands `~` (a `Path("~/…").read_text()` never
  did); `project_root` must be absolute because musicians and validations run
  with the workspace as cwd; the generated harness scripts `cd` into the
  literal project root instead of `git rev-parse` from wherever they are run.

- **`mzt list` never pages out a live score.** `--limit` caps terminal history
  only; queued, running and paused runs are always all shown, and the footer
  names how many older terminal runs were hidden. A 20-row page over 25 running
  jobs had read as "0 RUNNING" for the five that fell off.

- **Busy conductor named by PID; no restart advice for a live one (GH #455).**
  Every routed read (`status`, `status JOB`, `list`, `errors`, `diagnose`,
  `history`, `watch`, `run` submission) reports a conductor whose socket did
  not answer as `Marianne conductor busy or unresponsive (PID n alive);
  running state is unknown` (`running_state: unknown`, `conductor_pid`).
  Absence needs two proofs: a missing socket, or a refused connect with no
  live PID. The `mzt restart` hint `errors`/`diagnose`/`history` attached to
  every conductor error is gone. `mzt conductor-status` prints the PID,
  whether it is alive and the host I/O pressure when the socket is silent
  (clone mode reads the clone's own PID file), and the readiness payload
  carries `event_loop_lag_ms`, `event_loop_lag_peak_ms` and `io_pressure`.
  `socket.backlog` defaults to 128 (was 5), and log rotation gzips on a
  helper thread instead of inline on the event loop.
- **A reload-declined profile is visible, and a stale schema is told apart from a broken file (GH #450)** — `ConfigReloadResult` carries `failed_profiles` (path, reason_code, reason, retained) and `schema_stale`; the loader reports `instrument_schema_stale` when a file names a key the running conductor's schema lacks (restart, do not edit). `mzt doctor` gains a "Config reload" section and `conductor.last_reload` in JSON; `mzt conductor-status` gains a "Config" block. An argv-delivered prompt past the kernel cap (E2BIG) now says so in the spawn failure instead of reading as a missing binary.
- **A busy conductor is no longer reported as absent (GH #455)** — When a conductor blocked on disk stops accepting, its AF_UNIX listen queue fills; each queued connect looks successful and the first write fails with `EINVAL`. The IPC client now classifies a connect or first-write failure on an existing socket as unresponsive (`running_state: unknown`, no `mzt start` hint); only a missing socket or a refused connection proves absence. The connection close path also no longer renders a chained traceback into a debug log (it stalled the status read itself under load). Regression: a real listener with a full backlog.
- **Instrument descendants die with the attempt (Linux instrument supervisor)** — A CLI or interactive instrument now runs under `marianne.utils.instrument_supervisor`, a child subreaper in the instrument's own process group: a grandchild whose parent exits (an agent's `bfs`/`ugrep` search helper, a disowned shell) re-parents to the supervisor and is ended and reaped when the instrument exits or the attempt is torn down, instead of re-parenting to init and running for hours. New `cli.command.supervise` (default `true`), `cli.command.io_priority` (default `low`, so an agent's whole-disk search no longer stalls the conductor's state reads) and `cli.command.max_open_files` (default 4096, a soft `RLIMIT_NOFILE` for the instrument's whole tree; GH #457). The backend's own descendant reaper (GH #406) remains the guard where the subreaper is unavailable.
- **IPC server stop no longer hangs on Python 3.12 (GH #442)** — `Server.wait_closed()` waits for accepted connections since 3.12, so `DaemonServer.stop()` now cancels parked handlers before awaiting the listener; every IPC lifecycle test that hit the 30 s timeout on 3.12 passes
- **Class vocabulary lives in the packaged map (GH #433)** — `instruments/classes/default.yaml` now declares the vocabulary; `image`, `video` and `high-context` ship with chains; adding a packaged class is a YAML edit. User and venue layers were always open to any name and remain so
- **Completion snapshots follow the state database (GH #432)** — New `snapshot_dir` on the conductor config, defaulting to `snapshots/` beside `state_db_path`; a conductor clone gets `clone-<name>-state-snapshots/`. Every test or clone that isolates its state DB now isolates its snapshots instead of writing into `~/.marianne/snapshots` (5,010 job dirs on one machine)
- **run action settle no longer ends a reader of its log (GH #449)** — The file-holder capture source admits only processes holding the action log open for writing (the detached escapee's inherited stdout/stderr); an operator's `tail -f` is a bystander and is left alone
- **V010 curated hint names the surface it sits in (GH #451)** — A bare `timeout:` inside an alias `config`, a movement's or sheet's `instrument_config`, or a `run:` trigger action now suggests that surface's `timeout_seconds` instead of the root key, so a hint never widens one seat's or one action's bound to the whole score
- **V228 fires only for routes that cannot report cost (GH #453)** — A priced CLI route (token paths + model price) makes `cost_limit_usd` bind; the warning now names the route and why it is unpriced (raw shell, no token paths, no models, $0 price, or a pinned model the profile does not price)
- **Concert trigger self-chain (GH #454)** — A `concert` trigger that names its own score is admitted under a lineage id (`<name>--chain<depth>--<stamp>`) instead of being refused as an active duplicate of its still-running parent; `concert.max_chain_depth` is the only bound
- **recover_job keeps a fallen-back terminal sheet's instrument name (GH #452)** — Recovery no longer rewrites a COMPLETED/SKIPPED/CANCELLED sheet's `instrument_name` to the primary while keeping the fallback's model; status and diagnose name the instrument that actually ran
- **SIGABRT crash** — Unified streaming with parallel CancelledError guard
- **Infinite loop in parallel execution** — Fixed production bug with parallel sheet execution
- **MCP proxy test infinite loop** — Fixed test expectations for MCP proxy
- **Rate limit UTC timestamps** — Use UTC consistently for rate limit tracking
- **Orphaned Claude processes** — Kill orphaned processes on exception

---

## [0.1.0] - 2025-12-25

### Added

#### Core Features
- **YAML Configuration** - Declarative job definitions with Jinja2 templating
- **Sheet Processing** - Split work into configurable sheets with customizable execution
- **Resumable Execution** - Checkpoint-based state management for fault tolerance
- **Smart Retry** - Exponential backoff with jitter and error classification
- **Rate Limit Handling** - Automatic detection and wait with configurable thresholds

#### CLI Commands
- `mzt run` - Execute jobs with progress tracking and ETA
- `mzt status` - View detailed job status with sheet breakdown
- `mzt resume` - Continue paused or failed jobs from checkpoint
- `mzt list` - List all jobs with filtering by status
- `mzt validate` - Validate configuration files before running
- `mzt dashboard` - Start web dashboard for monitoring

#### State Management
- **JSON Backend** - File-based state storage for simplicity
- **SQLite Backend** - Database-backed state for production use
- **Config Snapshots** - Store configuration in state for resume without original file

#### Validation System
- `file_exists` - Check for expected output files
- `file_modified` - Verify file was updated during sheet
- `content_contains` - Match patterns in file content
- `command_succeeds` - Execute shell commands as quality checks
- Confidence scoring for validation results

#### Backends
- **Claude CLI Backend** - Execute via Claude CLI with full options
- **Anthropic API Backend** - Direct API integration with model selection

#### Dashboard
- FastAPI-based REST API
- Job listing with status filtering
- Detailed job view with sheet information
- Health check endpoint
- OpenAPI/Swagger documentation
- CORS support for frontend development

#### Notifications
- Desktop notifications via system notify
- Slack webhook integration
- Generic webhook support
- Configurable event triggers (job_complete, job_failed, sheet_failed)

#### User Experience
- **Progress Bar** - Real-time progress with ETA during execution
- **Graceful Shutdown** - Ctrl+C saves state and shows resume command
- **Run Summary** - Comprehensive summary panel at job completion
- **Verbosity Control** - `--verbose` and `--quiet` flags
- **JSON Output** - Machine-readable output for scripting

#### Developer Experience
- Type hints throughout with mypy validation
- Comprehensive test suite with pytest
- Linting with ruff
- Pydantic v2 for configuration and state models
- Protocol-based backends for extensibility

### Architecture

```
marianne/
├── core/           # Domain models, config, errors
├── backends/       # Claude CLI, API, and Ollama backends
├── execution/      # Runner, validation, retry logic
├── state/          # JSON and SQLite state backends
├── prompts/        # Jinja2 templating
├── notifications/  # Desktop, Slack, webhook
├── dashboard/      # FastAPI web interface + auth
├── learning/       # Pattern learning and analysis
├── daemon/         # marianned service (IPC, scheduling, monitoring)
├── healing/        # Self-healing and diagnostic remedies
├── validation/     # Enhanced pre-execution validation
├── isolation/      # Worktree isolation for parallel jobs
├── bridge/         # MCP proxy and integration
└── mcp/            # MCP server implementation
```

---

*Marianne AI Compose - Orchestration for the AI Age*
