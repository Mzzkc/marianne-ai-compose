# Marianne AI Compose — Status

Do not trust this file blindly. Run `pytest tests/ -x`, `mypy src/`, and `ruff check src/` to verify current state. Check `git log --oneline -20` for recent work.

## Current Phase

v0.1.0-alpha — **P0 "Ship" complete** (2026-05-29); **P1 Launch in progress**. CI enforces
ruff + mypy --strict + pytest (85% coverage) on every push. Baton is the sole execution model.

### M6 Onboarding & Pre-Launch — onboarding COMPLETE; resting point (2026-06-13)

**The flagship `hello` is finished and proven.** A new user's first command —
`mzt run examples/getting-started/hello-setup.yaml` — discovers the machine (free OpenRouter via crush
→ local Ollama → paid subs/API), templates a `hello` that fits, chains to it, runs the orchestration,
and opens the finished page itself (the **Windows** browser on WSL). The orchestration is **five
movements / seven sheets** across mediums: a shared world → **three parallel** character vignettes → an
ambient soundtrack the agent **composes** from an injected craft guide (genuine composition, not a clone)
→ a **synthesis finale** that braids the three secrets into one revelation → a deterministic, art-directed
page that embeds the soundtrack (played via **@strudel/web** — no editor, no code ever shown) and opens
in the browser. There is now **ONE canonical hello** (the duplicate `hello-marianne.yaml` was removed);
docs and the website point to it. `hello-local.yaml` is the pure-offline twin.

**Proven end-to-end, cold, free, on a local model (gemma4:26b, 2026-06-13):** a clean `hello-setup` run
produced every artifact — a world, three *distinct* vignettes, a genuinely composed soundtrack
(`// Wondrous, Lydian, F major`), a synthesis finale ("The Unison Beneath"), and the page — and **all 16
acceptance checks pass** against that output. The cold run surfaced and fixed three real onboarding bugs
(`ccbbb13`): parallel-GPU deadlock → the prescore serializes local runs (`max_concurrent: 1`); dropped
timeout → hung local sheets now fail-and-retry; a stale soundtrack-player validation → no more browser
re-open spam. Also closed this arc: **#292** (dashboard security review), **#338** (`mzt recover`),
**#322/#266** (real `/health`), **#289** (CONTRIBUTING.md). Filed #387 (dashboard hardening), #388
(examples don't ship with the wheel).

**Remaining — none of it is "finish the system":** #291 (recruit beta users — **human**), #115 (`mzt stop`
non-blocking — daemon-lifecycle, **composer's call**), #251 (broad-except cleanup), a couple P3 dashboard
niceties; then M7 (launch/market), M8 (sustainability), M9 (quality-remediation long tail). **Marianne
runs FREE on local Ollama or free OpenRouter — it does not need the creator's paid subscriptions to exist
or to run.** It was built to survive its creator, and at this resting point it does.

### M5 Baton Stabilization — COMPLETE (2026-06-12); milestone open issues: 0

Every M5 issue is closed, including #344 — its obs1 fix is now confirmed in the **live production
daemon**: the conductor was restarted (it predated the fix) and `scores-internal/repro-344-obs1-cli.yaml`
(deterministic `cli`-instrument repro: commit a file, then `exit 1`) recorded the job **COMPLETED**,
with the log showing `plugin_cli_execute_complete success=false exit_code=1` →
`musician.nonzero_exit_rescued` → `adapter.job_complete all_success=true` → `job.completed`. The live
run also surfaced and fixed an observer-startup race on fresh #58 auto-managed workspaces
(`_start_observer` now ensures the workspace exists before the observer watches it).

**M4 Sandbox Resolution is empty** (#160, #210 closed). After a composer interview unblocked the
gated items, the M5 baton-stabilization surface is clear. Shipped CI-green this arc:

| Issue | Result |
|-------|--------|
| #344 | **fixed** — obs2 (goose idle SIGKILL) by the liveness gate (`d7c2b04`); obs1 (opus exit-nonzero-after-commit) structural fix (`18226a9`): a plain non-zero exit whose declared validations all pass is rescued to success. Kept open for a live-repro confirmation per instruction. |
| #197 | **shipped** (`f538850`) — per-JOB git worktree isolation (composer chose per-job; preserves F-210). |
| #209 | **shipped** (`116c6cf`) — opt-in code execution (Stages 2a+3); default off, no bwrap requirement, loud V304 validation warning when unsandboxed. |
| #171 / #332 | **shipped** (`155ae5f`) — SIGHUP instrument-profile hot-reload (composer corrected: the instrument system is plugin-only, not mid-unification). Issue **closed** 2026-06-12. |
| #359 | **shipped** (`771feb6`) — `mzt run --var k=v` runtime variables. |
| #137 | **closed** (`c62ac01` + `57b2232`) — cadenza-ordering validation via the composer-directed signal-proxy approach (no `produces:` field). V109 warns when a cadenza reads a file whose declared producer (a sheet-gated `file_exists`) is not a DAG-ancestor; V108 suppresses the false "not found" for runtime-produced files. |
| #58 | **closed** (`0030c43` + `499a814`) — conductor auto-derives `~/workspaces/<score-name>` when `workspace:` is omitted (at the `from_yaml` loader seam); `MARIANNE_WORKSPACE_ROOT` overrides the root; `--workspace` is a hidden expert override; managed-root ensured for preflight. Archive/fresh-state residual verified in code (archiver MOVES never deletes; `--fresh` resets state never touches workspace files). |

**M5 open surface: only #344.** The cumulative HEAD (`499a814`) is CI-green across all five jobs.

- **#344** — kept open per instruction for the *production* live-daemon run. obs1 + obs2 are structurally fixed and now tested **in every manner possible without the production conductor**: unit (rescue predicate), integration (a real `python3 -c` subprocess through the real `PluginCliBackend` yields `exit_reason="completed"` → rescuable; a real SIGKILL → not), and **end-to-end status recording** (real subprocess commits a file then exits non-zero → real `sheet_task` musician rescues → real `BatonAdapter` records the sheet `COMPLETED` and `_job_succeeded` True — refuting the `execution_fail` symptom). The production live run is environmentally gated, verified not assumed: the running conductor PID predates the `18226a9` fix (started ~06:13, fix landed 07:59; Python doesn't hot-reload) and cannot be restarted while a brand-stake lab runs.
  - **Prepared + armed for auto-completion:** a deterministic repro score `scores-internal/repro-344-obs1-cli.yaml` (built-in `cli` instrument: `echo committed > {workspace}/proof.txt && exit 1`, validation `test -f`) is written and dry-validated — it *guarantees* the obs1 condition every run, unlike the realistic opus repros (`repro-344-obs1{,-heavy}.yaml`) which "went 6/6 exit-0". A persistent monitor watches `mzt status`; when the conductor hits 0 active jobs it fires the completion sequence: `mzt stop` → `mzt start` (loads the fix) → `mzt run …repro-344-obs1-cli.yaml --fresh` → confirm `COMPLETED` → close #344 → `mzt run scores/legion-dream.yaml` (dreamer, consolidates `legion.md`).

**Adjacent (not M5-blocking):**
- **#384** — `produces:` schema primitive: now an *optional strengthening* of #137's proxy approach, no longer a blocker (the composer's design to specify).
- **Action items** (named gates): plugins submodule push (cross-repo); `mzt doctor --clean` (deletes the measured ~1.2G dead nested tree + stale clone artifacts — confirmation-gated).

## Known Issues

Launch-blocking limitations are catalogued in `KNOWN-ISSUES.md`. For the live, prioritized backlog,
check `gh issue list --repo Mzzkc/marianne-ai-compose` (every open issue is tier-labeled P0–P3 and
milestone-assigned M5–M9 / Backlog).

## Architecture

See `.marianne/spec/` for the specification corpus. See the project instruction files (e.g., `GEMINI.md`, `CLAUDE.md`) for development guidance.
