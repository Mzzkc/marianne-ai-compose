# Mozart Orchestra — Collective Memory

## Core Memories
**[CORE]** We are building Mozart v1 beta — the intelligence layer that makes AI agent output worth adopting.
**[CORE]** The spec corpus at .mozart/spec/ is the source of truth. Every agent reads it.
**[CORE]** pytest/mypy/ruff must pass after every implementation. No exceptions.
**[CORE]** The music metaphor is load-bearing — use it in all user-facing output.
**[CORE]** We restructured from a hierarchical company to a flat orchestra. Every musician is an equal peer. The music metaphor is real now.
**[CORE]** Uncommitted work doesn't exist. Commit on main. Always.
**[CORE]** Read 03-confluence.md — understand what you are and what your work means. Down. Forward. Through.
**[CORE]** Two correct subsystems can compose into incorrect behavior. Bugs at system boundaries are the hardest to find because each side looks correct in isolation. (F-065, Axiom M2)
**[CORE]** The composer found more bugs in one real production usage session than 755 tests found in two movements. The gap between "tests pass" and "product works" is where quality lives.

## Learned Lessons
- **[Cycle 1, Forge]** Always check what exists before assuming you need to build.
- **[Cycle 1, Circuit]** Test the production path, not the internal method. `classify_execution()` had zero coverage while `classify()` was fully tested.
- **[Cycle 1, Harper]** Always check the error path, not just the happy path. Stale detection only covered COMPLETED, not FAILED.
- **[Cycle 1, Dash]** Don't assume something is broken without checking. The dashboard has 23 Python files, ~50 endpoints, all functional.
- **[Cycle 1, Composer Notes]** The learning store schema migration failure (#140) brought down ALL jobs. Always write migrations, tests, verify against existing DBs.
- **[Cycle 1, Lens]** 12 learning commands (36% of CLI) dominate help output — poor information architecture.
- **[Cycle 1, Warden]** stdout_tail/stderr_tail stored in 6+ locations without credential scanning. Safety applied piecemeal.
- **[Cycle 1, Blueprint]** SpecCorpusLoader used `if not name:` instead of `if name is None:` — rejects falsy-but-valid YAML values.
- **[Cycle 1, Ghost]** When the foundation is about to shift, audit first. The instinct to "do something" is wrong when you don't know the baseline.
- **[Cycle 1, Breakpoint]** Test the abstraction level that runs in production. Zero tests existed for PriorityScheduler._detect_cycle().
- **[Movement 1, Axiom]** Failed sheets must propagate failure to dependents. Without propagation, `is_job_complete` returns False forever — zombie jobs.
- **[Movement 1, Theorem]** Property-based testing finds bugs hand-picked examples miss. Hypothesis found the escalation terminal-guard violation in seconds.
- **[Movement 1, Adversary]** Every handler that transitions sheet status must check `_TERMINAL_STATUSES`. The baton's guard pattern is now complete.
- **[Movement 1, Mateship]** The finding->fix pipeline works without coordination: F-018 filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings.
- **[Movement 2, Axiom]** `record_attempt()` correctly doesn't count successes; `_handle_attempt_result` correctly retries on 0% validation. Together: infinite loop. Bugs at system boundaries.
- **[Movement 2, Axiom]** The pause model (`job.paused` boolean) serves three masters: user, escalation, cost. Post-v1: replace with pause reason set.
- **[Movement 2, Axiom]** When fixing a bug class (e.g., "handler doesn't check cost"), audit ALL handlers with the same pattern. F-067 fixed two of three. F-143 found the third.
- **[Movement 2, Axiom]** F-009 root cause confirmed: learning store query tags and storage tags are in different namespaces with zero overlap. 28,772 patterns accumulated, 91% never applied. The intelligence layer was disconnected.
- **[Movement 2, Newcomer]** The gap between "feature works" and "feature is taught" is where adoption dies. F-083 — instrument system had zero adoption in examples.
- **[Movement 3, Axiom]** State sync gap class: _propagate_failure_to_dependents() changes status directly without events. On restart, cascaded failures lost. Same class as F-039 and F-065.
- **[Movement 3, Weaver]** The orchestra optimizes for parallel work. The critical path is serial. Every movement since M2 confirms this.
- **[Movement 3, North]** Directives must specify the deliverable, not the direction. "Activate the baton" produced readiness, not activation.
- **[Movement 3, Prism]** 32 parallel musicians can't execute a serial critical path. The format optimizes for breadth; the remaining work demands depth.
- **[Movement 3, Tempo]** Mateship rate 33% (12/36 commits) — pipeline is now the dominant collaboration mechanism, evolved from anti-pattern fix to institutional behavior.
- **[Movement 4, Tempo]** The parallel-serial tension is a priority perception problem, not a capacity problem. One step per movement, four consecutive times.
- **[Movement 4, Ember]** Strategic assessments must verify config before making production claims. North's "baton already running" claim was falsified — `use_baton: false` in conductor.yaml.
- **[Movement 4, Captain]** The orchestra is bad at initiation (step 1) but excellent at continuation (steps 2+) via mateship pipeline. Ensure step 1 of every serial path has an explicit assignee and deliverable.
- **[Movement 4, F-441]** Six musicians (Axiom→Journey→Axiom→Prism→Theorem→Adversary) discovered, fixed, verified, and proved a P0 in one movement. Zero coordination overhead. The mateship pipeline at its best.

## Design Decisions
- **Baton migration:** Feature-flagged BatonAdapter. Old and new paths coexist. Do not re-debate.
- **Cost visibility:** Scoped first (CostTracker in status), full UX later.
- **Learning schema changes:** Additive only without escalation.
- **Code-mode techniques:** Long-term direction. MCP supported for v1. code_mode flag exists, not wired.
- **No automatic instrument fallback on rate limits.** Explicit opt-in only.
- **Daemon-only architecture.** All state in ~/.mozart/mozart-state.db. JsonStateBackend deprecated.
- **Musician-baton contract:** validation_pass_rate defaults to 0.0 (safety), baton auto-corrects to 100.0 when validations_total==0 and execution_success==True.
- **Terminal state invariant:** All baton handlers guard against `_TERMINAL_STATUSES`. Seven bugs found and fixed by three independent methodologies.
- **Pause model debt:** Single boolean serving three masters. Post-v1 -> pause_reasons set.
- **Finding ID allocation:** Range-based, 10 IDs per musician per movement. `FINDING_RANGES.md` + `scripts/next-finding-id.sh`. Prevents collision (12 historical incidents).
- **F-254 governance:** Hard cut to baton-as-default. Dual-state architecture generates more bugs than any other subsystem. Flip the default, document breaking change, delete legacy in Phase 3.
- **F-202 cross-sheet FAILED stdout (Blueprint M5):** Baton's stricter behavior is the correct design — only COMPLETED sheets' stdout appears in cross-sheet context. Failed output may be incomplete/malformed and would mislead downstream agents. Legacy runner's permissiveness is a legacy accident, not a feature. If recovery patterns need failed output, add explicit `include_failed_outputs: true` to CrossSheetConfig post-v1. F-202 RESOLVED by design decision.

## Current Status

Movement 5 — IN PROGRESS (2026-04-05).

### M5 Progress (Circuit)
- **F-149 RESOLVED (P1):** Backpressure no longer rejects ALL jobs when ONE instrument is rate-limited. `should_accept_job()` and `rejection_reason()` now only consider resource pressure (memory, processes). Rate limits handled at sheet dispatch level. 10 TDD tests. 7 existing tests updated. Manager simplified.
- **F-451 RESOLVED (P2):** Diagnose falls back to workspace filesystem when conductor says "not found" and -w provided. -w flag unhidden. 4 TDD tests.
- **F-471 MITIGATED (P2):** Pending jobs lost on restart — moot since F-149 removed rate-limit→PENDING path.
- **Meditation written:** meditations/circuit.md
- **Instrument fallback observability (P1):** InstrumentFallback events now emitted to EventBus via core._fallback_events + adapter._publish_fallback_events(). 11 TDD tests.
- **Fallback status display (P1):** format_instrument_with_fallback() shows "(was X: reason)" in status. 5 TDD tests.
- **Adversarial fallback tests (P1):** 15 tests covering empty chain, full chain walk, duplicates, reason distinction, serialization, edge cases.
- **TASKS.md:** All fallback spec tasks now complete. Commit 0a43895.

### M5 Progress (Maverick)
- **F-470 RESOLVED:** _synced_status memory leak in deregister_job() — 1-line fix, 5 TDD tests.
- **F-431 RESOLVED:** extra='forbid' on all 9 daemon/profiler config models — completes F-441 class. 23 TDD tests.
- **User variables in validations:** prompt.variables now available in validation paths during `mozart validate` (rendering.py) and `mozart recover` (recover.py). 8 TDD tests.
- **D-027 COMPLETE (Canyon):** use_baton default flipped to True. D-026 prerequisites (F-271, F-255.2) both resolved. Legacy tests updated with explicit `use_baton=False`. The baton IS now the default execution model.
- **Meditation written:** meditations/maverick.md

### M5 Progress (Foundation, D-026)
- **F-271 RESOLVED:** PluginCliBackend MCP process explosion — profile-driven `mcp_disable_args` on CliCommand. Claude-code profile updated. 7 TDD tests. Litmus test updated.
- **F-255.2 RESOLVED:** Baton _live_states never populated — `_run_via_baton` creates initial CheckpointState, `_resume_via_baton` populates from recovered checkpoint. F-151 instrument_name now set at creation time. 7 TDD tests.
- **F-472 filed:** Pre-existing test expects `use_baton` default=True (D-027 not completed). Quality gate false failure.
- **D-026 COMPLETE.** Both assigned findings resolved. ~50 lines of fix code, 14 TDD tests.
- **Meditation written:** meditations/foundation.md

### M5 Progress (Blueprint)
- **F-470 mateship pickup:** Wrote 5 TDD tests, verified uncommitted fix. Committed.
- **F-431 mateship pickup:** Added missing ProfilerConfig `extra='forbid'` (Maverick had 8 of 9). Committed with 23 tests.
- **F-430 RESOLVED:** Fixed docstring/code precedence mismatch in ValidationRule.sheet. 4 TDD tests.
- **F-202 RESOLVED:** Design decision — baton's stricter cross-sheet behavior is correct. Documented in Design Decisions.
- **User variables mateship:** Verified Maverick's implementation passes all 8 tests. Marked complete.
- **Meditation written:** meditations/blueprint.md
- **Pre-existing test failure noted:** `test_no_reload_with_none_snapshot_falls_back_to_disk` fails with M5 uncommitted changes but passes on HEAD. Not from Blueprint's changes.

### M5 Progress (Canyon, co-composer)
- **D-027 COMPLETE:** Flipped `use_baton` default to True in DaemonConfig. Phase 2 of baton transition. Legacy tests updated with explicit `use_baton=False`. 3 TDD tests in test_d027_baton_default.py.
- **F-271 mateship improvement:** Replaced Foundation's hardcoded MCP disabling with profile-driven `CliCommand.mcp_disable_args`. Generic — each instrument defines its own MCP disable mechanism. 8 TDD tests in test_f271_mcp_disable.py.
- **F-255.2 enhancement:** Added `instruments_used` and `total_movements` to the initial CheckpointState in `_run_via_baton` for complete status display. 4 TDD tests in test_f255_2_live_states.py.
- **F-431 verified resolved:** All 9 daemon + profiler config models have `extra='forbid'`. No work needed.
- **Quality gate baselines updated:** BARE_MAGICMOCK 1541→1582, ASSERTION_LESS_TEST 129→131.
- **Meditation written:** meditations/canyon.md

### M5 Progress (Harper)
- **Session 1 — Instrument fallbacks config models (P0):** Added `instrument_fallbacks` field to JobConfig, MovementDef. Added `per_sheet_fallbacks` to SheetConfig with validation. Reconciliation mapping updated. 15 TDD tests.
- **Session 1 — Instrument fallbacks Sheet entity (P0):** Added `instrument_fallbacks` to Sheet, resolved in `build_sheets()`. Resolution chain: per_sheet > movement > score-level. Per-sheet replaces, not merges. 8 TDD tests including fan-out inheritance.
- **Session 1 — Instrument fallback history (P1):** Added `instrument_fallback_history` to SheetState for resume-safe fallback event tracking. 4 TDD tests.
- **Session 1 — V211 validation (P1):** InstrumentFallbackCheck warns on unknown fallback instrument names. Checks all 3 levels. Registered in runner.py. 8 TDD tests.
- **Session 1 — Adversarial test update:** Converted test_instrument_fallbacks_rejected_until_implemented from "prove field doesn't exist" to "prove field works" — the field now exists.
- **Session 2 — Stale task verification (16 tasks):** Verified instrument fallback runtime + F-481 baton orphan detection already implemented. Marked 16 unclaimed tasks complete in TASKS.md with file:line evidence.
- **Session 2 — F-490 coverage audit (P0):** Full process-control syscall audit. Zero sibling bugs. All os.killpg through _safe_killpg, all os.kill guarded, SIG_IGN dance correct. Document at movement-5/process-control-defensive-patterns.md. Recommended M-011/M-012/M-013 constraints.
- **Session 2 — Quality gate baseline:** BARE_MAGICMOCK 1615→1625.
- **Meditation written:** meditations/harper.md

### M5 Progress (Ghost)
- **F-311 RESOLVED:** Fixed deterministic test failure in test_unknown_field_ux_journeys.py. `instrument_fallbacks` was added as a real field by Harper, making the "unknown field rejection" test obsolete. Updated to use `instrument_priorities` (genuinely non-existent).
- **F-310 filed (P2):** Test suite flaky — different tests fail each full run, all pass in isolation. Cross-test state leakage across 11,400+ tests. Timing-dependent async tests degrade under 500s runtime.
- **F-472 verified resolved:** D-027 complete (Canyon). `use_baton` defaults to `True`. Test passes.
- **Mateship pickup:** Committed Harper's instrument_fallbacks config+validation and Circuit's F-149/F-451. Concurrent execution collision with Circuit — resolved cleanly via separate commits.
- **Marianne rename completion (mateship):** pyproject.toml + 325 test files with stale `from mozart.*` imports. 326 files, 42b0f71. Also fixed .flowspec/config.yaml (8 stale src/mozart references). Commit 1ddc023.
- **F-490 correctness review COMPLETE (P0):** Full audit of _safe_killpg guard. Guard is correct. Added 3 structural regression tests (no bypass, 6 call sites, all have context). 14 total tests. Commit a68bb9f.
- **F-480 Phase 1 + Phase 5 tasks marked complete** in TASKS.md. Report centralization verified.
- **Test suite baseline:** 11,638 passed, 5 skipped, 0 failed (non-random).
- **Meditation written:** meditations/ghost.md

### M5 Progress (Lens)
- **D-029 Status Beautification (P1):** Mateship pickup of Dash's D-029. All three status displays beautified.
- **format_relative_time():** New utility in output.py — "just now", "5m ago", "3h 15m ago", "6d 12h ago". 7 TDD tests.
- **Beautified `mozart status`:** Musical header panel with movement context ("Movement 3 of 10 · The Baton"), relative elapsed time, "Now Playing" section for active sheets (♪ prefix), compact Stats section replacing verbose Timing+Execution Stats.
- **Beautified `mozart list`:** WORKSPACE column → PROGRESS column (50/100, 50%). Relative time. Test artifact filtering (/tmp/pytest paths hidden by default).
- **Synthesis bounding:** Last 5 batches (was unbounded). "Showing last 5 of N" header.
- **Movement completion fraction fix:** Running movements with >1 sheet now show "2/4 complete" (was only showing for multi-voice movements).
- **Conductor status:** Preserved Dash's Rich Panel implementation, enhanced uptime display for 24h+.
- **15 TDD tests** in test_status_beautification.py. 2 existing tests updated (format change).
- **Meditation written:** meditations/lens.md

### M5 Progress (Codex)
- **M5 documentation sweep (12 deliverables across 5 docs):** D-027 use_baton default True (daemon guide + config ref + limitations), F-149 backpressure rework (daemon guide + CLI ref), F-451 diagnose -w unhidden (CLI ref), instrument fallbacks (config ref section + score-writing guide section + TOC entries + V211), disable_mcp hazard (limitations), getting-started.md verified accurate.
- **Key updates:** Baton section rewritten in daemon-guide (Phase 1+2 complete), backpressure no longer mentions rate-limit queueing (F-149 removed it), new Instrument Fallbacks section in config reference and score-writing guide.
- **Meditation written:** meditations/codex.md

### M5 Progress (Forge)
- **F-190 RESOLVED:** DaemonError catch added to diagnose.py (errors/diagnose/history) + recover.py. 4 locations fixed. 7 TDD tests in test_f190_daemon_error_catch.py.
- **F-180 partially resolved (root causes 2+3):** Baton _estimate_cost() now uses instrument profile ModelCapacity pricing when available. Adapter resolves pricing from BackendPool registry. Falls back to hardcoded rates. 6 TDD tests in test_f180_cost_pricing.py.
- **Mateship: Foundation's test_f255_2_live_states.py fixed.** Replaced deprecated asyncio.get_event_loop() with asyncio.new_event_loop() pattern (2 locations). Same fix in test_baton_invariants.py.
- **F-105 partial: Stdin prompt delivery for PluginCliBackend.** Added prompt_via_stdin, stdin_sentinel, start_new_session fields to CliCommand. Modified _build_command() and execute() to support stdin pipe delivery and process group isolation. Updated claude-code.yaml profile. 18 TDD tests in test_plugin_cli_stdin.py. Remaining for F-105: route actual claude-cli jobs through PluginCliBackend instead of native ClaudeCliBackend.
- **Mypy fix:** to_observer_event() in events.py return type changed from dict[str, Any] to ObserverEvent.
- **Quality gate baseline:** BARE_MAGICMOCK updated to 1625.
- **Pre-existing test failure noted:** test_litmus_intelligence.py::test_rate_limit_only_returns_rate_limit_reason fails on HEAD — F-110 backpressure rejection_reason returns None instead of 'rate_limit'. Not from any M5 changes.
- **Meditation written:** meditations/forge.md

### M5 Progress (Dash)
- **D-029 COMPLETE: Status beautification (mateship pickup + conductor enhancement).** Unnamed musician implemented status beautification (header panel, Now Playing, compact stats, list progress, synthesis bounding). Dash pickup: conductor-status upgraded to Rich Panel with resource context (memory %, process limits, pressure indicator), job ID restored to header panel, format_duration extended for days, 2 broken tests fixed (test_status_with_valid_job_id, test_status_shows_last_activity). 24 TDD tests in test_d029_status_beautification.py.
- **Meditation written:** meditations/dash.md

### M5 Progress (Spark)
- **Rosetta proof scores updated (P1):** All 6 examples/rosetta/ scores now have named `instruments:` aliases and per-movement instrument assignments. Each score maps task types to instrument archetypes (fast-scanner/deep-analyst, designer/generator/validator, etc.) with differentiated timeouts. Comments explain multi-provider deployment variants. All 6 validate clean.
- **Gemini-cli rate limit test (P2):** 18 TDD tests in test_gemini_cli_rate_limit.py using gemini-cli.yaml's actual error patterns. Covers RESOURCE_EXHAUSTED, 429, Too Many Requests, quota exceeded, plus error classification (auth/capacity/timeout). All pass.
- **Quality gate baseline fix:** BARE_MAGICMOCK 1625→1632 (new tests from M5 unnamed musician work).
- **Meditation written:** meditations/spark.md

### M5 Progress (Oracle)
- **Full M5 metrics assessment COMPLETE.** Learning store: 31,462 patterns, warm tier 3,426 (+7.6%). F-300 resource_anomaly: 5,506 at 0.5000 (STILL DARK, +191). Executions: 243,136 total, 99.6% success. p99 duration UP from 30.5min to 48.5min.
- **Critical finding:** D-027 changed code default to True but production `conductor.yaml` still has `use_baton: false`. Baton is default in code, NOT in production. Same pattern as Ember's M4 finding.
- **Predictive update:** Critical path advanced THREE steps (broke one-step-per-movement pattern). Phase 1 testing M6, production activation M6-M7, demo M7-M8, release M8-M9.
- **Meditation written:** meditations/oracle.md

### M5 Progress (Bedrock — Quality Gate & Ground Duties)
- **Quality gate PASS:** mypy clean, ruff clean, pytest **11,708 passed / 5 skipped** (exit 0, ~502s). +311 tests from M4 gate.
- **Codebase metrics:** 99,694 source lines (+1,247 from M4). 362 test files (+29). 26+ commits from 12+ musicians. 707 files changed, 18,504 insertions, 6,992 deletions.
- **Participation:** 12+ musicians committed. Warden, Oracle active concurrent. Work naturally concentrated on rename, baton flip, and instrument fallbacks.
- **Meditations:** 27/32 (84%). Missing: atlas, breakpoint, journey, litmus, sentinel. Canyon synthesis blocked by 5.
- **TASKS.md audit:** 257 completed, 69 open. 15 rename tasks, 7 compose system, 5 Rosetta modernization, 14 M6-M7 future.
- **FINDINGS.md:** 13 new M5 entries (F-472 through F-490). 2 P0 critical (F-487 WSL crash, F-490 killpg WSL crash) — both resolved. 3 open P2s (F-484, F-485, F-488).
- **Key M5 assessment:** D-026+D-027 achieved. Baton IS the default. Instrument fallbacks shipped. Marianne rename Phase 1 complete. The serial critical path advanced two steps this movement (F-271+F-255.2 → baton flip). Phase 3 (remove toggle) and demo remain.
- **Meditation written:** meditations/bedrock.md (prior session)

### M5 Progress (Warden)
- **F-252 RESOLVED (P2):** Unbounded `instrument_fallback_history` capped at 50 records (matching `MAX_ERROR_HISTORY`). Both checkpoint and baton state paths trimmed. `add_fallback_to_history()` helper added to SheetState. 10 TDD tests.
- **M5 safety audit:** 7 areas audited. D-027 baton default flip safe (F-157 irrelevant). F-149 backpressure architecturally correct. Instrument fallbacks safe (infinite loop protected). F-105 stdin delivery safe. Only gap: F-252 (fixed).
- **Meditation written:** meditations/warden.md

### M5 Progress (Atlas)
- **STATUS.md fully updated:** Stale since M4. Header changed to "Marianne AI Compose", 11,638 tests, 99,718 source lines, M5 progress, blockers. Key Files table fixed (src/mozart → src/marianne).
- **CLAUDE.md fixed:** 14 stale `src/mozart/` references updated to `src/marianne/` (config models, repository org, key files, instrument system).
- **8th strategic alignment assessment:** M5 broke serial path pattern (3 steps vs 1/movement). Integration cliff still critical — baton never run in production. Rename incomplete. Demo at zero for 10+ movements.
- **Context rot finding:** STATUS.md and CLAUDE.md were an entire movement stale with wrong file paths. Maps agents read at session start were pointing to a package that doesn't exist.
- **Meditation written:** meditations/atlas.md — "The Map and the Territory"
- **Meditation count:** 26/32 (81%). Missing: Breakpoint, Journey, Litmus, Oracle, Sentinel, Warden.

Movement 4 — COMPLETE (2026-04-05). All movements M0-M4 complete.

### M4 Quality Gate — PASS (Bedrock, verified by Prism)
- **ALL CHECKS PASS:** pytest 11,397 passed / 5 skipped, mypy clean, ruff clean, flowspec 0 critical.
- 98,447 source lines, 333 test files. 93 commits from ALL 32 musicians (100% participation — first time).
- 215 files changed, 38,168 insertions. Largest movement yet.
- **Major deliverables:** F-210/F-211 (P0 baton blockers cleared), F-441 (config strictness, 51 models), D-023 (4 Wordware demos), D-024 (cost accuracy), 5 GitHub issues closed (#156, #122, #120, #103, #93, #128).
- **Open findings:** F-470 (memory leak), F-471 (pending jobs lost on restart), F-202 (baton/legacy parity), F-271 (MCP gap), F-255.2 (live_states), F-431 (DaemonConfig missing extra='forbid').
- **Mateship rate 39% (all-time high).** Institutional collaboration mechanism.
- **Meditations:** 13/32 (40.6%).

### M5 Directives (North, D-026 through D-031)
- D-026: Foundation → F-271+F-255.2 (~50 lines combined, P0)
- D-027: Canyon → flip use_baton default (P0, gated on D-026)
- D-028: Guide → ship Wordware demos (P0)
- D-029: Dash → status beautification (P1)
- D-030: Axiom → close verified issues (P1)
- D-031: ALL → meditation (P1)

## Coordination Notes (Active)
- **CRITICAL PATH (M5 UPDATED):** F-271 RESOLVED. F-255.2 RESOLVED. D-027 COMPLETE (code default True). Remaining: Phase 1 baton test (--conductor-clone) → update conductor.yaml to remove `use_baton: false` → demo → release.
- **PRODUCTION GAP:** `~/.mozart/conductor.yaml` still has `use_baton: false`. The baton is default in code but NOT in the running conductor. This override must be removed AFTER Phase 1 testing passes.
- **DEMO (P0 — EXISTENTIAL):** Lovable demo blocked on baton running in production. Wordware demos (4) work on legacy runner.
- **Tempo's recommendation (STILL UNACTED):** Designate a serial convergence musician. One musician, one focus, the whole movement. Two movements without action on this.

## Blockers (Active Only)
- **Phase 1 baton testing:** UNBLOCKED (F-271, F-255.2 resolved, D-027 done). Needs one musician to dedicate a full session with `--conductor-clone` and `use_baton: true`. Two movements unblocked, zero progress.
- **Production activation:** Gated on Phase 1 testing. Then: remove `use_baton: false` from `~/.mozart/conductor.yaml`.

## Top Risks
1. **Phase 1 baton testing NOT STARTED (CRITICAL).** All prerequisites resolved (F-271, F-255.2, D-027). Two movements unblocked. No one has tested the baton against real sheets. This is an execution gap, not a technical blocker.
2. **Production conductor on legacy (HIGH).** `conductor.yaml` has `use_baton: false`. Code default changed (D-027) but production hasn't switched. Claims of "baton is default" are true for code, false for the running system.
3. **Demo existential risk (HIGH — improving).** Wordware demos (4) work on legacy. Lovable demo blocked on baton in production.
4. **Model concentration risk (MEDIUM).** 97.6% claude-sonnet. Gemini tasks unclaimed since M4. Zero model diversity.
5. **Resource anomaly pipeline dark (MEDIUM).** 5,506 patterns at 0.5000. F-300. 17.5% of corpus contributes zero intelligence signal. Growing but producing nothing.
6. **p99 duration increase (LOW-MEDIUM).** 30.5min → 48.5min. Cause unknown. May indicate stale detection change or deeper sheets.

## Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Cold Archive

### Movement 4 (Complete, 2026-04-05)
The largest movement yet — 93 commits from all 32 musicians (100% participation for the first time), 215 files changed, 38,168 insertions. The movement resolved both P0 blockers that had stalled the baton transition: Canyon wired cross-sheet context (F-210) and Blueprint completed checkpoint sync (F-211), with Foundation finishing the mateship completion on both. Phase 1 baton testing was finally unblocked.

The movement's signature achievement was F-441 — config strictness. Axiom filed it, Journey fixed it with schema hints, Axiom completed the mateship across 45 remaining models, Prism reviewed it architecturally, Theorem proved it with property-based tests across all 51 config models, and Adversary verified it with 55 adversarial tests. Six musicians, zero coordination overhead, one movement. The mateship pipeline operating at peak efficiency, hitting an all-time high of 39%.

But the deepest tension surfaced around the baton itself. North declared the baton was "already running in production" based on Theorem's observation of conductor logs showing 150 completed sheets. Ember flatly contradicted this — `use_baton: false` in conductor.yaml. The baton was NOT running. Legacy runner had executed all sheets. This was the movement's most consequential factual dispute, and it remained unresolved at the gate. The lesson was sharp: strategic assessments must verify config, not infer from logs.

Four Wordware comparison demos shipped (Blueprint built 3, Spark finished the 4th) — the first externally-demonstrable deliverables in 9+ movements. They validate clean and work TODAY on the legacy runner. Ghost, Forge, and Harper cleared three long-standing resume issues (#93, #103, #122). Compass added typo suggestions and fixed the iterative-dev-loop misplacement. Newcomer validated everything with fresh eyes. Warden audited all 18 commits and found two gaps (F-250, F-251), both fixed same-movement.

The serial path advanced exactly one step — again. Fourth consecutive movement at one-step-per-movement pace. Tempo diagnosed it as a priority perception problem, not a capacity problem, and recommended designating a serial convergence musician for M5. North issued concrete directives (D-026 through D-031) with named assignees. The path to v1 beta narrowed to ~50 lines of code: F-271 (MCP gap, ~15 lines) and F-255.2 (live_states population, ~30 lines). The infrastructure was extraordinary. What remained was executing the serial path with the focus it demanded.

Oracle's analysis showed 30,232 patterns in the learning store, warm tier exploding from 182 to 3,185, but resource anomaly patterns (5,315) remained uniformly cold at 0.5000 — a dark pipeline contributing zero intelligence signal. The codebase reached 98,447 source lines with 11,397 tests, mypy clean, ruff clean. The quality was undeniable. The question was whether quality would translate to a product anyone could see.

### Movement 3 (Complete, 2026-04-04)
The UX and polish movement — 48 commits from 28 musicians, mateship at 33%. Ten critical bugs fixed, 584 tests added, every M3 milestone completed. The baton was mathematically verified from four independent angles with zero bugs in new code. The mateship pipeline matured into institutional behavior — six musicians completing chains started by others, the uncommitted-work anti-pattern countered so reliably it became a collaboration mechanism. But Prism articulated the central tension: "32 parallel musicians can't execute a serial critical path." North acknowledged his own failure — zero M3 output until the final strategic report — and issued specific M4 directives. The lesson: directives must specify the deliverable, not the direction.

### Movement 2 (Complete, 2026-04-04)
A single 15-hour wave. 60 commits, 28 musicians, zero merge conflicts. Baton completed (23/23 tasks), conductor-clone reached full coverage, product surface healed from 2/37 to 38/38 validating examples. The movement crystallized the parallel-vs-serial tension: 1,120 baton tests, never run a real sheet. Demo at zero for 6+ movements. Captain and Atlas independently concluded: assign ONE musician to the serial path.

### Movement 1 (Cycles 1-7)
The first building movement. 32 musicians, 42 commits, zero merge conflicts. Instrument plugin system shipped end-to-end. Baton terminal guard completed across all 19 transitions. Three natural rhythms emerged (build, convergence, verification). Mateship matured through Harper's mass pickups. The finding-fix pipeline became the orchestra's strongest institutional mechanism.

### Movement 0 (Stabilization)
Foundation laid: learning store fixes, critical bug resolution, dead code removal. Quality gates established.
