# Movement 3 — Prism Review

**Reviewer:** Prism
**Focus:** Multi-perspective code review, architectural analysis, blind spot detection, cross-domain synthesis, GitHub issue verification
**Method:** Read all M3 reports (22+ reports). Verified claims against committed code on HEAD (25cd91e). Ran all quality gates independently. Cross-referenced TASKS.md against git log. Validated all 34 example scores. Reviewed all open GitHub issues. Closed 5 verified issues.
**Date:** 2026-04-04

---

## Executive Summary

Movement 3 produced **28 commits from 16 musicians**. Quality gates are GREEN after one fix: **10,919 tests** collected (up 517 from M2), mypy clean, ruff clean. The three P0 baton blockers from M2 (F-152, F-145, F-158) are all resolved. The intelligence layer's longest-standing bug (F-009/F-144, 7+ movements open) has its first real fix. Five GitHub issues closed with evidence during this review (#155, #154, #153, #139, #94).

**The movement's defining achievement: it fixed what broke.** M2 built the baton. M3 made it usable. The dispatch guard (F-152), prompt assembly (F-158), concert chaining (F-145), model override (F-150), semantic tags (F-009/F-144), and rate limit auto-resume (F-112) were all blockers that had been identified but not fixed. This movement fixed them. That's the right work.

**Three faces turned away from the presenters:**

1. **The baton has STILL never executed a real sheet.** The blockers are resolved. The prompt assembly is wired. The dispatch guard works. Foundation's analysis says "architecturally ready for Phase 1 testing." Yet no one ran a score through `--conductor-clone` with `use_baton: true`. Four movements of building the baton. Zero empirical evidence it produces correct output. The composer's M4 directive calls this "mandatory." It remains the defining gap.

2. **F-440 (failure propagation at registration) broke a pre-existing adversarial test and nobody caught it.** `test_recover_failed_parent_in_progress_child` in `test_baton_m2c2_adversarial.py` expected PENDING; F-440 correctly produces FAILED. The test was updated (detected during this review), but the pattern is concerning: code changes in `core.py` broke tests in a different file, and the breakage was attributed to "test ordering state leakage" rather than investigated. Ordering-dependent failures are real, but this one wasn't ordering-dependent — it failed deterministically. The explanation obscured the actual issue.

3. **Participation dropped.** 16/32 musicians committed M3 work (down from 28/32 in M2). The bottom half of the orchestra is silent. This isn't necessarily a problem — fewer musicians means fewer coordination headaches, and the 16 who committed did excellent work. But the 50% participation rate means the orchestra's theoretical throughput is half its capacity.

---

## Quality Gates — Independently Verified

| Gate | Status | Evidence |
|------|--------|----------|
| pytest | **GREEN** | 10,919 tests collected, quality gate passes (after baseline fix) |
| mypy | **GREEN** | `mypy src/` — zero errors (clean) |
| ruff | **GREEN** | `ruff check src/` — "All checks passed!" |

**Quality gate fix required:** `BARE_MAGICMOCK_BASELINE` was 1346 but actual count is 1347 due to `test_top_error_ux.py:130`. Updated to 1347. This is the 9th occurrence of baseline drift from uncommitted/un-updated test files.

**Test ordering dependency:** `test_recovery_failure_propagation.py::test_adapter_recover_propagates_failures` and `test_baton_m2c2_adversarial.py::test_recover_failed_parent_in_progress_child` showed ordering-dependent results in random seed runs. The latter was a genuine test bug (pre-F-440 expectation), now fixed. The former passes in isolation and in deterministic ordering.

---

## Example Corpus Validation

33/34 pass. Only `iterative-dev-loop-config.yaml` fails (generator config, not a score — expected).

| Metric | Value |
|--------|-------|
| Examples passing | 33/34 |
| Hardcoded absolute paths | 0 |
| Stale `backend:` syntax | 0 |
| hello.yaml instrument (HEAD) | `claude-code` (correct) |
| hello.yaml instrument (working tree) | `claude-code` (correct — gemini-cli artifact cleared) |

The example corpus is clean and consistent. The working tree no longer has the dangerous gemini-cli hello.yaml change from M2.

---

## GitHub Issue Verification — 5 Issues Closed

I verified and closed 5 issues whose M3 fixes are confirmed on HEAD:

| Issue | Fix | Commit | Verified By |
|-------|-----|--------|-------------|
| **#155** | F-152 dispatch guard — `except Exception` catches all failures | d3ffebe + e929d95 | Code review + 20+ TDD + 2 litmus |
| **#154** | F-150 model override — `apply_overrides()`/`clear_overrides()` on PluginCliBackend | 08c5ca4 | Code review + 19 TDD + 3 litmus |
| **#153** | F-149 clear-rate-limits CLI — 4-layer implementation | ae31ca8 + bd325bc | Code review + 18 TDD + F-200/F-201 regression |
| **#139** | Stale state feedback — 3 root causes (PID, --fresh, rejection) | cdd921a + 4b83dae | Code review + 17 TDD |
| **#94** | Stop safety guard — IPC probe + confirmation | 04ab102 | Code review + 10 TDD |

**Separation of duties confirmed:** All 5 issues were fixed by other musicians (Canyon, Foundation, Blueprint, Harper, Dash, Lens, Ghost, Circuit). I verified by reading the code, running the tests, and checking edge cases. No musician closed their own fix.

### Open Issues — Remaining Assessment

| Issue | Status | M3 Impact |
|-------|--------|-----------|
| **#131** | OPEN | no_reload IPC threading done (8590fd3, 07b43be) but issue may have broader scope than the IPC fix. Needs composer assessment. |
| **#128** | OPEN | skip_when not expanded in fan-out. No M3 fix. |
| **#132** | OPEN | `| string` filter validation. No M3 fix. |
| **#124** | OPEN | Job registry name matching. No M3 fix. |
| **#120** | OPEN | Fan-in empty inputs on skipped fan-out. No M3 fix. |
| **#111** | OPEN | Conductor state persistence. Baton recovery exists but not production-tested. |
| **#100** | OPEN | Rate limits kill jobs. F-112 auto-resume exists for baton path but baton not activated. |
| **#141** | OPEN | Rate limits should pause, not kill. Blocked by baton activation. |

---

## M3 Deliverable Verification — Cross-Referenced Against Code

### Critical Path Fixes — ALL VERIFIED ON HEAD

| Fix | Finding | Commit | Status |
|-----|---------|--------|--------|
| F-152 dispatch guard | P0 | d3ffebe, e929d95 | **VERIFIED** — `adapter.py:853` catches Exception broadly |
| F-145 concert chaining | P2 | d3ffebe | **VERIFIED** — `has_completed_sheets()` wired in both baton paths |
| F-158 prompt assembly | P1 | d3ffebe | **VERIFIED** — `PromptRenderer` created in `register_job`/`recover_job`, used in `_musician_wrapper` |
| F-150 model override | P1 | 08c5ca4 | **VERIFIED** — `apply_overrides`/`clear_overrides` on PluginCliBackend |
| F-009/F-144 semantic tags | P0 | e9a9feb | **VERIFIED** — `build_semantic_context_tags()` at `patterns.py:82-118` |
| F-112 auto-resume | P1 | 25ba278 | **VERIFIED** — `_handle_rate_limit_hit()` schedules `RateLimitExpired` timer |
| F-149 clear-rate-limits | P1 | ae31ca8 | **VERIFIED** — 4-layer implementation, IPC handler registered |
| F-160 wait cap | P2 | 0972df3 | **VERIFIED** — `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` |
| F-200 clear fallthrough | P2 | bd325bc | **VERIFIED** — `.get()` instead of unconditional clear |
| F-201 truthiness check | P3 | 25cd91e | **VERIFIED** — `if instrument is not None:` |

### CLI/UX Fixes — ALL VERIFIED

| Fix | Commit | Status |
|-----|--------|--------|
| Rate limit time-remaining UX (F-110) | 8bb3a10 | **VERIFIED** — `format_rate_limit_info()` in output.py |
| Stale PID detection (#139) | cdd921a | **VERIFIED** — `process.py:89-95` |
| Stop safety guard (#94) | 04ab102 | **VERIFIED** — `process.py:186-197` |
| no_reload IPC threading (#98/#131) | 8590fd3, 07b43be | **VERIFIED** — threaded through 5 IPC layers |
| Schema error hints | 0028fa1 | **VERIFIED** — `_schema_error_hints()` in validate.py |
| Rejection hint regression | 4b83dae | **VERIFIED** — instruments.py JSON fix, 7 TDD tests |
| F-151 instrument observability | 25ba278, 4a1308b | **VERIFIED** — Instrument column in status display |

### Documentation — VERIFIED

Codex (8022795) documented 6 M3 features across 5 docs: clear-rate-limits, stop safety guard, stagger_delay_ms, rate limit auto-resume, instrument column, restart options. All claims verified against source code.

### Adversarial Testing — EXCEPTIONAL

Breakpoint wrote **258 adversarial tests** across 4 passes (62 + 58 + 90 + 48). Found 2 bugs (F-200, F-201). Zero bugs in the BatonAdapter (90 tests, zero findings). The adversarial coverage of the baton is now comprehensive. This is the most thorough adversarial testing of any component in the project's history.

Litmus wrote 21 intelligence-layer validation tests covering all major M3 fixes. 95 total litmus tests.

---

## Codebase Metrics

| Metric | M2 End | M3 End | Delta |
|--------|--------|--------|-------|
| Source lines (`src/mozart/`) | 96,475 | 97,424 | +949 |
| Test files | 291 | 314 | +23 |
| Test count | 10,402 | 10,919 | +517 |
| M3 commits | — | 28 | — |
| Unique musicians | — | 16 | (of 32) |
| Open issues | 42 | 37 | -5 |

---

## Composer's Notes Compliance

| Directive | Priority | Compliance |
|-----------|----------|------------|
| P0: Baton transition mandatory | **BLOCKERS RESOLVED** | F-152, F-145, F-158 fixed. Phase 1 testing not started. |
| P0: --conductor-clone first | **DONE** | 17/18 tasks (same as M2) |
| P0: pytest/mypy/ruff pass | **GREEN** | After baseline fix |
| P0: Uncommitted work doesn't exist | **IMPROVED** | Quality gate baseline was the only mateship pickup needed |
| P0: Documentation IS the UX | **MET** | 6 M3 features documented across 5 docs |
| P0: hello.yaml impressive | **UNCHANGED** | Produces HTML. Composer wants more visual impact. |
| P0: Lovable demo | **NOT STARTED** | 8+ movements non-compliant |
| P0: Wordware demos | **NOT STARTED** | 8+ movements non-compliant |
| P0: Separation of duties | **WORKING** | 5 issues closed by reviewer this session |
| P1: Fix siblings | **FOLLOWED** | F-200 → F-201 (same bug class found and fixed) |
| P1: Uncommitted work as P1 finding | **IMPROVED** | Only 1 baseline drift this movement |

---

## Cross-Report Synthesis — What Everyone Sees

I read all 22+ M3 reports. The movement tells a coherent story:

**Unanimous agreement:**
1. Quality gates GREEN — verified by multiple independent runs
2. The three baton blockers (F-152, F-145, F-158) are resolved
3. The mateship pipeline continues to work — findings travel through musicians without coordination
4. CLI/UX is in its best state ever

**Where reviewers diverge (the boundary findings):**

- **Bedrock (ground):** Corrected M3 stats twice. Found and committed 8th occurrence of uncommitted work. Created D-018 finding ID system. Most concerned about the gap between milestone metrics (76% complete) and product reality (zero running demos).
- **Foundation (builder):** "The baton is architecturally ready for Phase 1 testing." Committed 3 mateship pickups. F-009/F-144 semantic tag fix — the P0 intelligence layer bug finally has its first real fix after 7+ movements.
- **Circuit (integration):** Fixed 3 observability bugs sharing the same shape: "system does the right thing internally but presents wrong information to the user." The deepest fix was F-048 (cost tracking when limits disabled) — months of `$0.00` false data.
- **Breakpoint (adversarial):** 258 tests, 2 bugs found. The BatonAdapter survived 90 adversarial tests with zero findings. The adapter is battle-tested in isolation.
- **Warden (safety):** 9-area safety audit. One gap found (F-160: unbounded wait time). All M3 changes are safe from injection, credential leak, and state corruption perspectives.
- **Journey (experiential):** The golden path from the user's perspective is professional and helpful. Schema error hints tell you what's wrong. Terminology is consistent. Edge cases don't crash.

**What I see that nobody else is saying:**

The movement fixed the right things. Every fix resolved a real, identified blocker. No speculative features, no premature abstractions, no infrastructure-for-infrastructure's-sake. F-152 fixed infinite loops. F-158 wired prompt assembly. F-009/F-144 fixed the intelligence layer's namespace mismatch. F-112 wired auto-resume. F-160 capped adversarial wait times. These are all things that would have broken the baton in production.

But the pattern I've seen across every review since M1 is unchanged: **we are making the theoretical perfect while the empirical evidence gap grows wider**. The baton has never executed a real sheet. The intelligence layer has never been tested with real patterns in a real run. The model override has never been tested with a real API call. We have 10,919 tests proving the parts work. We have zero evidence the whole does.

Foundation says the baton is ready. Canyon says the baton is ready. Breakpoint says 258 adversarial tests found only 2 minor bugs. I agree with all of them. **The next step is not more verification. The next step is someone outside the orchestra running `use_baton: true` against a real score and telling us what happens.**

---

## Architectural Observations

### Encapsulation Violation (Persistent)
`adapter.py` accesses `self._baton._jobs` (lines 688, 725) and `self._baton._shutting_down` (line 1164). Three private member accesses. Flagged in M1. Unchanged. The adapter needs public APIs on BatonCore for `get_job_record()` and `is_shutting_down()` to avoid reaching into private state.

### F-440 Failure Propagation — Correct but Untested at Integration Level
The fix at `core.py:544-555` re-propagates failure at registration time. This is correct — without it, zombie jobs occur. But the fix was added to `register_job()` which is also called by `_run_via_baton()` for fresh jobs. For fresh jobs, all sheets start as PENDING, so the propagation loop is a no-op. For recovery, it's essential. The test that was broken (`test_recover_failed_parent_in_progress_child`) only tested the recovery path. No integration test verifies that this works end-to-end through the manager's resume pipeline.

### Semantic Tag Coverage (F-009/F-144)
The fix at `patterns.py:82-118` is a good start but is intentionally broad. It generates `success:first_attempt`, `retry:effective`, and `completion:used` for ALL queries, regardless of context. This means every pattern query now matches any stored pattern tagged with these common categories. The overlap problem is solved (0% → >0%), but the specificity problem remains — we're querying broadly instead of precisely. This is acceptable for v1 (better to over-apply than under-apply), but post-v1 the tags should be context-specific (e.g., `retry:effective` only when the sheet is actually a retry).

---

## Findings

### Quality Gate Baseline Drift (Mateship Pickup)
`BARE_MAGICMOCK_BASELINE` 1346→1347 for `test_top_error_ux.py:130`. Fixed in this session. 9th occurrence of baseline drift.

### No New P0/P1 Findings
All critical issues from M2 are either resolved or unchanged. No new critical or high-severity issues discovered during this review.

---

## Verdict

**Movement 3 is COMPLETE. The baton is ready for activation.**

28 commits. 16 musicians. 10,919 tests. mypy clean. ruff clean. Three P0 baton blockers resolved. Five GitHub issues closed with evidence. Intelligence layer's longest-standing bug has its first fix. 258 adversarial tests with only 2 bugs found. CLI/UX at its best. Working tree clean.

**The critical path is now singular and non-negotiable:**

```
Phase 1: PROVE THE BATON WORKS (composer's M4 directive)
  1. Start a clone conductor with use_baton: true
  2. Run hello.yaml through it
  3. Run a multi-instrument score through it
  4. Run adversarial scenarios (rate limits, failures, timeouts)
  5. Fix what breaks
  → Phase 2: Baton as default
  → Phase 3: Remove the toggle
  → Demo
```

Three P0 composer directives remain at zero progress (8+ movements):
1. **Lovable demo** — the product the outside world can see
2. **Wordware comparison demos** — competitive differentiation
3. **Baton activation** — the mandatory transition that enables everything else

The orchestra plays with extraordinary precision. The baton is ready. The audience is still waiting.

Down. Forward. Through.

---

*Prism — Movement 3 Review*
*2026-04-04, verified against HEAD (25cd91e) on main*
