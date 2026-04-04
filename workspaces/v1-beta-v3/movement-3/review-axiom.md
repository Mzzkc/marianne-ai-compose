# Movement 3 — Axiom Review (Second Pass)

**Reviewer:** Axiom
**Focus:** Logical analysis, dependency tracing, invariant verification, edge case detection, data flow analysis
**Method:** Read all 36 M3 reports, both review reports (Prism, Axiom M3 first pass), quality gate, collective memory, TASKS.md. Verified M3 fixes on HEAD (d6006a8). Ran mypy, ruff, targeted tests. Traced F-210 through the code. Verified GitHub issue closures. Cross-referenced task claims against git log.
**Date:** 2026-04-04

---

## Executive Summary

Movement 3 produced **43 commits from 26 musicians** (per quality gate) plus **36 reports**. Quality gates are GREEN: 10,981 tests pass, mypy clean, ruff clean, flowspec 0 critical. Five GitHub issues were closed by Prism (#155, #154, #153, #139, #94) — all verified against committed code with separation of duties.

**The movement's thesis was consolidation, and it delivered.** Every P0 baton blocker from M2 was resolved. The intelligence layer's longest-standing bug (F-009/F-144, 7+ movements open) has its first real fix. 584 new tests were added, including 258 adversarial tests from Breakpoint, 67 Phase 1 baton adversarial tests from Adversary, 29 property-based invariant proofs from Theorem, and 21 intelligence-layer litmus tests from Litmus. My own contribution was F-440 — a state sync gap that would resurrect zombie jobs during recovery — same error class as F-039 (M1) and F-065 (M2).

**Three structural observations that matter:**

1. **F-210 is real and critical.** I traced it independently. The baton's `_build_prompt()` at `musician.py:208-288` assembles prompts from Sheet template variables, but `template_variables()` at `sheet.py:133-163` never includes `previous_outputs`. The legacy runner populates this via `_populate_cross_sheet_context()` at `context.py:171-221`. The field exists on `SheetExecutionState` at `state.py:161` but is never written. 24 of 34 example scores use `cross_sheet: auto_capture_stdout: true`. Phase 1 testing without F-210 fixed would produce scores that silently render empty `{{ previous_outputs[N] }}` — functional degradation with no error signal. Weaver's finding is correct.

2. **The semantic tag fix (F-009/F-144) is broad, not precise.** The fix at `patterns.py:82-118` generates tags like `success:first_attempt`, `retry:effective`, and `completion:used` for ALL queries regardless of context. This means every pattern query now matches any stored pattern tagged with these categories. The overlap problem is solved (0% → >0%) but the specificity problem replaces it — we're querying broadly instead of precisely. For v1, this is the correct trade-off (better to over-apply than under-apply). Post-v1, tags should be context-specific.

3. **The mateship pipeline is now institutional.** 12/36 commits (33%) were mateship pickups — Foundation (4), Bedrock (2), and 6 others committing uncommitted teammate work. This is the highest rate ever and represents genuine emergent coordination. The uncommitted work anti-pattern was caught and resolved within the movement.

---

## Quality Gates — Independently Verified on HEAD (d6006a8)

| Gate | Status | Evidence |
|------|--------|----------|
| mypy | **GREEN** | `mypy src/` — zero errors (clean) |
| ruff | **GREEN** | `ruff check src/` — "All checks passed!" |
| Key M3 tests | **GREEN** | `pytest tests/test_recovery_failure_propagation.py tests/test_baton_dispatch_guard.py tests/test_f009_semantic_tags.py -q` — 36 passed |

Quality gate report (Bedrock): 10,981 passed, 5 skipped, 158 warnings. 315 test files. Up from 10,397 (M2). +584 tests this movement.

---

## M3 Fix Verification — All Verified on HEAD

I traced each fix backwards from the claim to the code. Every fix below is confirmed present and correct on HEAD (d6006a8).

### Critical Path Fixes

| Fix | Severity | Code Location (Verified) | Test Evidence |
|-----|----------|-------------------------|---------------|
| **F-152** dispatch guard | P0 | `adapter.py:746-866` — `_send_dispatch_failure()` called from all 3 early-return paths in `_dispatch_callback`. Exception catch is `except Exception` (broad, correct). | 20+ TDD (Canyon + Foundation) + 67 adversarial (Adversary) |
| **F-145** concert chaining | P2 | `adapter.py:674` — `has_completed_sheets()`. Checked at `manager.py:1837` and `manager.py:1968` (both baton paths). | 8 TDD (Canyon + Foundation) |
| **F-158** prompt assembly | P1 | `adapter.py:419-430` — PromptRenderer created in `register_job`. Used at `adapter.py:953-960`. `prompt_config` passed from `manager.py:1815-1816` (register) and `manager.py:1959-1960` (recover). | 3 TDD (Canyon) + 21 litmus (Litmus) |
| **F-009/F-144** semantic tags | P0 | `patterns.py:82-118` — `build_semantic_context_tags()`. Called at `patterns.py:229`. `instrument_name` passed to `get_patterns()` at `patterns.py:234`. | 13 TDD (Maverick/Foundation) + 21 litmus (Litmus) |
| **F-440** state sync gap | P1 | `core.py:544-555` — Failure re-propagation in `register_job()`. Loop iterates FAILED sheets and calls `_propagate_failure_to_dependents()`. Idempotent for fresh jobs. | 8 TDD (Axiom) + 67 adversarial (Adversary) |

### Infrastructure Fixes

| Fix | Severity | Code Location (Verified) | Test Evidence |
|-----|----------|-------------------------|---------------|
| **F-112** rate limit auto-resume | P1 | `core.py:958-967` — `RateLimitExpired` timer scheduled. Handler at `core.py:991-1020`. | 10 TDD (Circuit) |
| **F-150** model override | P1 | `cli_backend.py:116-142` — `apply_overrides()`/`clear_overrides()`. `backend_pool.py:205-210` — release clears. `sheet.py:230-243` — movement-level gating decoupled from instrument name. | 19 TDD (Blueprint/Foundation) |
| **F-151** instrument observability | P1 | Status display shows Instrument column when any sheet has `instrument_name`. Summary view shows instrument breakdown. | 16 TDD (Circuit) |
| **F-149** clear-rate-limits CLI | P1 | `RateLimitCoordinator.clear_limits()`, `BatonCore.clear_instrument_rate_limit()`, `BatonAdapter` delegation, `JobManager.clear_rate_limits()`. IPC handler registered. | 18 TDD (Harper) |
| **F-160** wait cap | P2 | `constants.py` — `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0`. `ErrorClassifier._clamp_wait()` replaces bare `max()` calls. | 10 TDD (Warden) |
| **F-200/F-201** rate limit clear | P2/P3 | `core.py:254-297` — `.get()` with `is not None` guard. | Adversarial (Breakpoint) |
| **F-148** finding ID collision | P2 | `FINDING_RANGES.md` — range-based allocation. `scripts/next-finding-id.sh`. | Bedrock |
| **F-099** stagger delay | P2 | `ParallelConfig` — `stagger_delay_ms` (0-5000ms Pydantic-bounded). | 10 TDD (Forge) |

### CLI/UX Fixes

| Fix | Commit | Verified |
|-----|--------|----------|
| Rate limit time-remaining UX (F-110) | 8bb3a10 | `output.py:format_rate_limit_info()`, `helpers.py:query_rate_limits()` |
| Stale PID detection (#139) | cdd921a | `process.py:89-95` — dead PID cleanup |
| `--fresh` early failure suppression (#139) | cdd921a | `run.py:260-262` — skip `await_early_failure()` |
| Stop safety guard (#94) | 04ab102 | `process.py:186-197` — IPC probe + confirmation |
| no_reload IPC threading (#98/#131) | 8590fd3, 07b43be | `manager.py:835-875`, `process.py:535-540`, `job_service.py:913-941` |
| Schema error hints | 0028fa1 | `validate.py:_schema_error_hints()` |
| Terminology cleanup ("job" → "score") | d1a4dbc, 251f31d, e44e5b1 | Multiple docs and CLI files |

---

## GitHub Issue Verification

### Issues Closed by Prism This Movement — ALL VERIFIED

| Issue | Fix Description | Closing Commit(s) | Prism's Verification | My Re-Verification |
|-------|----------------|-------------------|---------------------|-------------------|
| **#155** | F-152 dispatch guard | d3ffebe + e929d95 | Code review + 20+ TDD + 2 litmus | Confirmed — 3 paths at adapter.py:822,835,866 all call `_send_dispatch_failure` |
| **#154** | F-150 model override | 08c5ca4 | Code review + 19 TDD + 3 litmus | Confirmed — `apply_overrides`/`clear_overrides` on PluginCliBackend |
| **#153** | F-149 clear-rate-limits | ae31ca8 + bd325bc | Code review + 18 TDD + F-200/F-201 regression | Confirmed — 4-layer implementation, IPC handler registered |
| **#139** | Stale state feedback (3 root causes) | cdd921a + 4b83dae | Code review + 17 TDD | Confirmed — PID cleanup, --fresh guard, rejection hints |
| **#94** | Stop safety guard | 04ab102 | Code review + 10 TDD | Confirmed — IPC probe + confirmation + --force bypass |

**Separation of duties maintained:** All 5 issues were fixed by other musicians. Prism verified and closed. I re-verified the code and test evidence on HEAD. All closures are correct.

### Previously Closed (Pre-M3 or Between Movements)

| Issue | State | Notes |
|-------|-------|-------|
| **#98** | CLOSED | no_reload IPC threading. Harper (8590fd3) + Forge (07b43be). |
| **#96** | CLOSED | Cost reset on resume. CONFIG_STATE_MAPPING handles. |

### Open Issues — Assessment

| Issue | State | M3 Impact | Assessment |
|-------|-------|-----------|------------|
| **#131** | OPEN | no_reload IPC threaded (8590fd3, 07b43be) | Issue title is "resume -c does not force config reload in running conductor." The no_reload fix addresses the inverse case (suppress reload). The `-c` (force reload) path may need separate verification. **Cannot close without testing the -c path specifically.** |
| **#141** | OPEN | F-112 auto-resume exists for baton | Blocked by baton activation. Legacy runner still kills on rate limit. |
| **#100** | OPEN | F-112 auto-resume exists for baton | Same — blocked by baton not being default. |
| **#128** | OPEN | skip_when not expanded in fan-out | No M3 fix. |
| **#132** | OPEN | `| string` filter validation | No M3 fix. |
| **#124** | OPEN | Job registry name matching | No M3 fix. |
| **#120** | OPEN | Fan-in empty inputs on skipped fan-out | No M3 fix. |
| **#111** | OPEN | Conductor state persistence | Baton recovery exists but untested live. |

---

## F-210: Cross-Sheet Context Gap — Independent Verification

This is the highest-priority finding from M3. I traced it end-to-end to confirm Weaver's analysis.

### The Trace

**Legacy runner (working):**
1. `_build_sheet_context()` at `context.py:53-86` calls `_populate_cross_sheet_context()` when `cross_sheet` config exists
2. `_populate_cross_sheet_context()` at `context.py:171-221` reads previous sheets' stdout from `SheetState.result_file` or `SheetState.output`
3. Populates `context.previous_outputs[prev_num]` with the content
4. `TemplateContext.previous_outputs` at `templating.py:88` carries this to Jinja2 rendering
5. Templates access `{{ previous_outputs[1] }}` etc.

**Baton path (broken):**
1. `SheetExecutionState.previous_outputs` at `state.py:161` — field exists, default `dict()`, **never populated**
2. `_build_prompt()` at `musician.py:208-288` — no cross-sheet context injection
3. `Sheet.template_variables()` at `sheet.py:133-163` — no `previous_outputs` in the returned dict
4. Jinja2 template references `{{ previous_outputs[1] }}` → **UndefinedError** or empty

### Impact

```
$ grep -rl "cross_sheet\|auto_capture_stdout" examples/ | wc -l
24
```

24 of 34 example scores use cross-sheet context. Any score where sheet N references previous sheet output will **fail or degrade silently** under the baton.

### Verdict

F-210 is real. It blocks Phase 1 testing. Without this fix, testing would produce misleading results — scores that appear to work but with broken inter-sheet context. Weaver's priority assessment (P1 blocker) is correct. Estimated fix: wire `_populate_cross_sheet_context` logic into the adapter's dispatch path, populating `SheetExecutionState.previous_outputs` before each sheet executes. ~100-200 lines.

---

## F-440 Post-Fix Review (My First-Pass Fix)

My F-440 fix at `core.py:544-555` was independently verified by:
- **Prism** (633345c) — code review, test update for correct post-F-440 behavior
- **Adversary** (b5b8857) — 67 adversarial tests including F-440 propagation edge cases, zero bugs found
- **Theorem** (6116966) — property-based invariant verification of state mapping round-trip

The fix is holding. The zombie job defense is now complete across both runtime (F-039, M1) and recovery (F-440, M3) paths.

### Remaining Sync Gaps (P2, documented in F-211)

Weaver independently confirmed the additional sync gaps I noted in my first-pass report:
- `EscalationResolved/EscalationTimeout` — terminal transitions not synced
- `CancelJob/ShutdownRequested` — CANCELLED transitions not synced

These are lower impact (exception paths, rare during normal execution) but should be fixed before baton Phase 2 (default flip).

---

## Composer's Notes Compliance

| Directive | Priority | Status |
|-----------|----------|--------|
| Baton transition mandatory | P0 | **BLOCKERS RESOLVED.** F-152, F-145, F-158 fixed. Phase 1 blocked by F-210. |
| --conductor-clone first | P0 | **19/20 tasks.** Only "convert ALL pytests" remains. |
| pytest/mypy/ruff pass | P0 | **GREEN.** All three pass. |
| Uncommitted work doesn't exist | P0 | **IMPROVED.** Mateship pipeline at 33%. All source code committed. |
| Documentation IS the UX | P0 | **MET.** Guide (10 cadences), Codex, Compass, Newcomer updated every public doc. |
| Lovable demo | P0 | **NOT STARTED.** 8+ movements non-compliant. Compass wrote a demo direction brief identifying Wordware as the zero-blocker alternative. |
| Wordware demos | P0 | **NOT STARTED.** Per Compass analysis, buildable today with legacy runner. |
| Read design spec before implementing | P0 | **FOLLOWED.** Foundation read wiring analysis before F-152 regression tests. |
| Separation of duties | P0 | **WORKING.** 5 issues closed by Prism, re-verified by Axiom. |
| Step 28 update (Canyon, M3) | P0 | **FOLLOWED.** BatonAdapter covers surfaces 1-6. Prompt assembly (F-158) wired. |
| Uncommitted work as P1 finding (Canyon, M3) | P1 | **FOLLOWED.** Foundation committed 3 mateship pickups. Bedrock committed 2. |

---

## Architectural Observations

### Encapsulation Violation — Persistent (3rd Movement)

`adapter.py` directly accesses private state:
- `self._baton._jobs.get(job_id)` at lines 688, 725
- `self._baton._shutting_down` at line 1164

These need public API methods on `BatonCore`: `get_job_record(job_id)` and `is_shutting_down()`. Functional correctness is unaffected, but this coupling means any refactoring of BatonCore's internal job tracking breaks the adapter silently. P3 — but it's been P3 for 3 movements, which means it's never getting fixed unless explicitly assigned.

### Semantic Tag Specificity (F-009/F-144 Follow-Up)

`build_semantic_context_tags()` at `patterns.py:82-118` generates the same tag set for every query. This is a correct v1 trade-off (any match is better than zero matches), but it means every pattern query now matches broadly. The learning store's effectiveness differentiation (Oracle reports avg 0.5000 → 0.5088) will be muted because bad patterns get the same tags as good ones. Post-v1, context-specific tags (e.g., only tag `retry:effective` when the sheet is actually retrying) would improve discrimination.

### Test Ordering Dependencies

Multiple reports mention ordering-dependent test failures that "pass in isolation." Foundation, Circuit, and Prism all noted these. The root cause is cross-test module state leakage — likely global state in mock configurations or shared fixtures. Not a correctness issue for the codebase, but it undermines confidence in full-suite CI runs.

---

## What's Missing — The Silence

### F-210 (Cross-Sheet Context)

The single highest-priority gap. 24/34 examples affected. Without this, Phase 1 baton testing is either impossible or misleading. Must be M4's first task.

### Demo (8+ Movements)

The Lovable demo has not been started. The Wordware comparison demos have not been started. Compass's demo direction brief correctly identifies that Wordware demos have **zero blockers** — buildable today with the legacy runner. The demo doesn't need the baton. It needs someone to build it.

### Baton Live Testing (0 Movements of Progress)

The baton has never executed a real sheet. This is now the 4th consecutive movement where reviewers, strategists, coordinators, and the co-composer all agree the baton is ready. The serial path (F-210 → Phase 1 → flip → demo) needs someone dedicated to it. The parallel orchestra structure optimizes for 32 independent tasks, not a 5-step serial chain.

### Cost Accuracy

Cost tracking reports $0.12 for 114 sheets of Opus execution. The actual cost is likely ~$120-$1200. Newcomer (F-461) correctly notes this is more dangerous than $0.00 — it looks plausible. This affects any demo where cost is discussed.

---

## Participation Analysis

**M3:** 26/32 musicians produced output (81%). 23/32 committed code (72%).
**M2:** 28/32 committed code (87.5%).

The narrowing is functional, not dysfunctional. The 6 musicians with no M3 commits (Blueprint, Maverick, North, Oracle, Sentinel, Warden) all produced reports. Blueprint and Maverick's uncommitted code was committed by Foundation through the mateship pipeline. North, Oracle, and Sentinel operate in advisory/review roles. Warden's audit was committed under Bedrock.

The mateship rate at 33% (12/36 commits) is the highest ever. The orchestra has evolved a self-correcting mechanism for the uncommitted work anti-pattern — mateship pickups are now institutional behavior, not exceptional heroics.

---

## Findings Summary

### New Findings This Movement

| ID | Severity | Status | Description |
|----|----------|--------|-------------|
| F-210 | P1 | **OPEN (BLOCKS Phase 1)** | Cross-sheet context missing from baton path. 24/34 examples affected. |
| F-211 | P2 | Open | Checkpoint sync missing for 4 event types (escalation, cancel, shutdown) |
| F-212 | P3 | Open | Spec budget gating missing from baton PromptRenderer |
| F-440 | P1 | **RESOLVED** (Axiom, M3) | State sync gap — failure propagation not synced. Fix: re-propagate in register_job(). |
| F-450 | P2 | Open | IPC "method not found" misreported as "conductor not running" |
| F-460 | P2 | **RESOLVED** (Newcomer, M3) | "job" → "score" terminology inconsistency |
| F-200 | P2 | **RESOLVED** (Breakpoint, M3) | clear_instrument_rate_limit() cleared ALL on non-existent name |
| F-201 | P3 | **RESOLVED** (Breakpoint, M3) | Same function, empty string truthiness bug |
| F-330 | P2 | **RESOLVED** (Compass, M3) | README missing 13 CLI commands |
| F-331 | P2 | **RESOLVED** (Compass, M3) | getting-started.md stale counts and terminology |
| F-332 | P3 | **RESOLVED** (Compass, M3) | docs/index.md stale example count |
| F-333 | P1 | **RESOLVED** (Compass, M3) | README manual install missing [daemon] extra |
| F-334 | P2 | **RESOLVED** (Compass, M3) | hello.yaml cost estimate wrong by 10-30x |

### P0 Findings — Status

| ID | Status | Notes |
|----|--------|-------|
| F-009/F-144 | **RESOLVED** (M3) | Semantic tags replace positional. Intelligence layer reconnected. |
| F-152 | **RESOLVED** (M3) | Dispatch guard with E505 failure posting. |
| F-107 | **OPEN** | No standardized instrument profile verification. Process gap. |

---

## Verdict

**Movement 3 is COMPLETE. The ground holds. The fixes are correct.**

43 commits. 26 musicians. 10,981 tests. mypy clean. ruff clean. flowspec 0 critical. Three P0 baton blockers resolved (F-152, F-145, F-158). The intelligence layer's 7-movement bug (F-009/F-144) has its first fix. My own F-440 fix survived 67 adversarial tests with zero failures. Five GitHub issues verified and closed with evidence.

**The critical path is now a 5-step serial chain:**

```
F-210 fix → Phase 1 testing (--conductor-clone) → fix issues → flip use_baton default → demo
```

F-210 is the only engineering blocker. The demo has no engineering blockers — it needs someone to build it.

The baton has 1,358 tests and has never executed a real sheet. The intelligence layer has semantic tags and has never applied a pattern in a real run. The model override is wired and has never been tested against a real API. We have 10,981 proofs that the parts work. We have zero evidence the whole does.

The orchestra plays with precision. The audience waits. The next movement must produce evidence, not more proofs.

---

*Report verified against HEAD (d6006a8) on main. All file paths, line numbers, and code claims independently confirmed. All GitHub issue closures re-verified.*
*Axiom — Movement 3 Review (Second Pass), 2026-04-04*
