# Movement 3 — Adversary Review

**Reviewer:** Adversary
**Focus:** Adversarial testing, security analysis, edge case discovery, state corruption analysis, recovery verification
**Method:** Fresh eyes, no memory of previous movements. Ran every safe CLI command against the live conductor (PID 1277279, uptime 1d 13h). Independently verified all quality gates. Traced critical code paths. Fed garbage inputs. Cross-referenced every reviewer's claims against committed code on HEAD (1588ab0). Verified F-210, F-450, F-465 independently through code tracing and live command execution.
**Date:** 2026-04-04

---

## Executive Summary

**Movement 3 built a system that passes every test and breaks on first use.**

Quality gates are GREEN: 10,986 tests collected, mypy clean (256 source files), ruff clean, flowspec clean. M3 milestone is 100% complete. 43+ commits from 26+ musicians. The surface is polished. The error messages teach. The documentation is honest. The test suite is vast.

And the thing that makes Marianne a conductor — the baton — cannot run a real score. Not because of bugs. Because it doesn't know what previous sheets said.

I traced this independently and the evidence is unambiguous: `grep -rn 'cross_sheet' src/marianne/daemon/baton/` returns **zero results**. `grep -rn 'previous_outputs' src/marianne/daemon/baton/` returns exactly **one hit** — a field declaration at `state.py:161` that is never populated anywhere. The field was declared, the docstring describes what it should do ("Populated by the adapter from CheckpointState for cross-sheet context"), and zero lines of code do the populating. 18 of 34 example scores have `auto_capture_stdout: true`. 12 reference `{{ previous_outputs }}` in their templates. The baton renders those templates with empty dicts. The legacy runner populates them via `_populate_cross_sheet_context()` at `context.py:171-221`. The baton path was built to replace the legacy runner and doesn't replicate its most-used integration surface.

This is my fourth consecutive adversarial review of the baton. The 67 Phase 1 tests I wrote earlier this movement all pass. The system's internal mechanics are correct. But correctness at the unit level and correctness at the integration level are different things, and F-210 is a gap between them that would cause silent functional degradation — not crashes, not errors, just worse output.

**Three things I broke or confirmed broken:**

1. **F-210 (P1, BLOCKER) — independently confirmed.** `previous_outputs` is declared at `state.py:161`, never written in `adapter.py`, `musician.py`, or `core.py`. The legacy runner's `_populate_cross_sheet_context()` at `context.py:171-221` has no baton equivalent. `Sheet.template_variables()` at `sheet.py:133-177` returns identity vars (sheet_num, movement, voice, workspace, instrument_name) but not cross-sheet context. Templates referencing `{{ previous_outputs[N] }}` will get `UndefinedError` in strict mode or empty dict in lenient mode. Either way: broken prompts with no error signal.

2. **F-450 (P2) — independently confirmed.** `mzt clear-rate-limits` outputs "Marianne conductor is not running" while the conductor IS running (PID 1277279, verified by `mzt status`, `mzt doctor`, `mzt conductor-status`). Root cause traced to `rate_limits.py:74-80`: `try_daemon_route()` returns `routed=False` when the IPC method `daemon.clear_rate_limits` doesn't exist on the running conductor (because the conductor was started from pre-M3 code). The conditional at line 74 conflates "method not found" with "conductor not running." The error tells you to start a conductor that's already started. This is the only error message in the product that lies.

3. **F-465 (P1) — independently confirmed at 4 locations.** The README quick start (steps 5, 6, 7) tells users to run `mzt status hello-marianne`. The conductor registers the score as `hello` (filename-derived). `mzt status hello-marianne` returns "Score not found." Verified on HEAD:

   ```
   $ mzt status hello-marianne → Error: Score not found: hello-marianne
   $ mzt status hello → COMPLETED (ID: hello, name: hello-marianne)
   ```

   Broken at: `README.md:141`, `README.md:158`, `docs/getting-started.md:60`, `examples/hello.yaml:16`.

---

## Quality Gates — Independently Verified on HEAD (1588ab0)

| Gate | Status | Command | Evidence |
|------|--------|---------|----------|
| mypy | **GREEN** | `mypy src/` | "Success: no issues found in 256 source files" |
| ruff | **GREEN** | `ruff check src/` | "All checks passed!" |
| pytest collection | **GREEN** | `pytest tests/ --co` | 10,986 tests collected in 2.62s |
| Examples | **GREEN** | `mzt validate examples/hello.yaml` | 38/38 validatable examples pass |

No disputes with the quality gate report. Bedrock's numbers (10,981/5 skipped) are from a slightly earlier HEAD; 5 more tests landed after the gate.

---

## Critical Path Analysis — What Actually Blocks v1

### Blocker 1: F-210 — Cross-Sheet Context (BLOCKS Phase 1 baton testing)

**Evidence chain:**

1. Legacy runner populates cross-sheet context at `context.py:171-221` via `_populate_cross_sheet_context()`
2. This reads `SheetState.stdout_tail` from completed sheets and injects it as `context.previous_outputs[sheet_num]`
3. Templates in 12+ examples reference `{{ previous_outputs[N] }}` or iterate over it
4. The baton's prompt builder at `musician.py:208-288` calls `Sheet.template_variables()` (line 248)
5. `Sheet.template_variables()` at `sheet.py:133-177` does NOT include `previous_outputs`
6. The field exists at `state.py:161` but is never populated — zero writes in the entire baton package
7. Validation preview shows `Previous: {}` for cross-sheet templates — confirming the rendering is empty

**Severity assessment:** This is P0, not P1. Every reviewer who looked at it agrees it blocks Phase 1. 18/34 examples have `auto_capture_stdout: true`. Phase 1 testing without this fix produces scores that appear to work but with silently degraded prompts. Weaver estimated 100-200 lines to fix. This is the single item that blocks everything else on the critical path.

### Blocker 2: The demo deficit (8 movements, zero progress)

The composer's P0 directives include Lovable demo and Wordware comparison demos. These are the oldest unfulfilled P0 obligations. The README is polished. The examples validate. The CLI is excellent. Nobody outside this repository has seen any of it. Infrastructure without audience is infrastructure for nobody.

### Blocker 3: Cost tracking fiction

Ember's finding is independently verifiable. The orchestra has completed 127/706 sheets, each running Claude Opus for 4-8 minutes. The conductor reports $0.20 total cost and 19,080 total input tokens. That's 150 input tokens per sheet — approximately one paragraph. The real context per sheet is 10,000-200,000 tokens.

**Root cause traced:** The `claude_cli.py` backend — the one used for `claude-code` instrument — has **zero token tracking**. `grep -rn 'input_tokens\|output_tokens' src/marianne/backends/claude_cli.py` returns nothing. Only `anthropic_api.py` (lines 227-258) and `ollama.py` (lines 301-492) track tokens. The `PluginCliBackend` at `execution/instruments/cli_backend.py` tracks tokens IF the instrument profile specifies JSON paths for them, but the native `claude_cli.py` backend doesn't use PluginCliBackend — it's a separate implementation.

The cost tracking system works correctly for what it measures. What it measures is not what the user cares about. The system counts tokens at the Marianne↔backend boundary, not the backend↔LLM boundary. For API backends (`anthropic_api.py`), these are the same thing. For CLI backends (`claude_cli.py`), they're separated by an entire agent runtime. The displayed cost is 100-1000x lower than actual spend.

A user who trusts `cost_limits.max_cost_per_job: 50` as a budget guardrail will overshoot by two orders of magnitude before the limiter triggers.

---

## M3 Fix Verification — Code Traced, Not Just Claimed

### Fixes I verified are correct on HEAD:

| Fix | Verified At | Method |
|-----|------------|--------|
| F-152 dispatch guard | `adapter.py:746-866` | Read all 3 early-return paths — all call `_send_dispatch_failure()` |
| F-009/F-144 semantic tags | `patterns.py:82-118` | Read tag generation — broad replacement for positional |
| F-440 state sync | `core.py:544-555` | Read failure propagation loop — iterates FAILED sheets |
| F-150 model override | `cli_backend.py:116-142` | Read `apply_overrides`/`clear_overrides` |
| F-112 auto-resume | `core.py:958-967` | Read timer scheduling for `RateLimitExpired` |
| F-160 wait cap | Constants defined, `_clamp_wait()` replaces bare `max()` calls |
| F-200/F-201 rate limit clear | `core.py:254-297` — `.get()` with `is not None` guard |
| Stop safety guard (#94) | `process.py:186-197` — IPC probe + confirmation flow |

All verified. No disputes with Axiom or Prism's fix verification.

### Fix I verified is INCOMPLETE:

| Fix | Issue | Evidence |
|-----|-------|---------|
| F-009/F-144 | Tags are broad, not specific | `build_semantic_context_tags()` at `patterns.py:82-118` generates the same tags for every query — `success:first_attempt`, `retry:effective`, `completion:used`. This is better than the broken positional system (0% match → >0% match), but it's a shotgun, not a scalpel. Every pattern matches every query with equal relevance. |

---

## Adversarial Testing — What I Fed the System

### Garbage Input Validation

| Input | Expected | Actual | Pass? |
|-------|----------|--------|-------|
| Empty file (`/dev/null`) | Error with guidance | "Score must be a YAML mapping, got: empty file" + hints | **YES** |
| Malformed YAML (`{yaml`) | Parse error with location | YAML syntax error with line/column pointer | **YES** |
| Minimal (`name: test`) | Schema error with required fields | "Schema validation failed" + 3 hints showing correct structure | **YES** |
| Large score (1000 vars × 1KB each) | Handled gracefully | Schema error (correct — wrong structure, not crash) | **YES** |
| Cross-sheet template preview | Shows empty context for sheet 1 | `Previous: {}` rendered correctly (empty is correct for sheet 1) | **YES** |

Error message quality is excellent. Every error teaches. Every hint is actionable. The only F-grade error in the product is F-450 (`clear-rate-limits` lying about conductor status).

### IPC Robustness

| Command | Expected | Actual | Pass? |
|---------|----------|--------|-------|
| `mzt status` | Running summary | Correct: 4 active, uptime 1d 13h | **YES** |
| `mzt doctor` | Health check | Correct: PID, instruments, cost warning | **YES** |
| `mzt status hello` | Completed score details | Correct: ID, name, progress, synthesis | **YES** |
| `mzt status hello-marianne` | Found (name lookup) | **FAIL** — "Score not found" (F-465) | **NO** |
| `mzt clear-rate-limits` | Rate limit info | **FAIL** — "Conductor not running" (F-450) | **NO** |
| `mzt validate examples/hello.yaml` | Valid with DAG preview | Correct: A+ experience | **YES** |

---

## Composer's Notes Compliance

| Directive | Priority | Status | Evidence |
|-----------|----------|--------|----------|
| `--conductor-clone` P0 | P0 | **Implemented but untested in real execution** | Clone infrastructure exists. Not used for Phase 1 because F-210 blocks Phase 1. |
| Read design specs before implementing | P0 | **Followed** | Step 28 analysis at `movement-2/step-28-wiring-analysis.md` referenced. |
| pytest/mypy/ruff must pass | P0 | **PASS** | All green on HEAD |
| Commit on main | P0 | **PASS** | All M3 work on main. No uncommitted source code. |
| Fix bugs when found | P1 | **Mostly followed** | F-210 found and filed but not fixed. F-450, F-465 found and confirmed by multiple reviewers but not fixed. |
| Documentation as you go | P0 | **Followed** | Guide (10 cadences), Codex, Compass, Newcomer collectively updated all public docs |
| Hello.yaml must be impressive | P1 | **Debatable** | Produces an HTML page. Haven't seen it run through the baton. |
| Lovable/Wordware demos | P0 | **NOT STARTED** | 8 movements. Zero progress. |
| Never touch the conductor | P0 | **Followed** | No dangerous commands executed |
| Baton transition plan | P0 | **Blocked by F-210** | Phase 1 cannot begin until cross-sheet context is wired |

---

## What Reviewers Agreed On (Consensus Across 5 Reviews)

Every reviewer who looked at the codebase found the same things:

1. **F-210 is THE blocker.** Weaver found it. Axiom traced it independently. Prism confirmed zero `cross_sheet` references. Ember confirmed experientially. Newcomer confirmed the gap between "verified" and "runnable." I confirmed all of the above by tracing the code myself. This is the single most verified finding in the movement.

2. **Quality gates are genuine.** Every reviewer independently confirmed GREEN gates. No disputes on test count, type safety, or lint. The test suite is vast and growing.

3. **The demo deficit is a pattern, not a gap.** Eight movements of unfulfilled P0 directives. Every reviewer noted it. Nobody started it.

4. **F-450 is the worst error message in the product.** Ember graded it F. Newcomer graded it F. I confirm: it lies. It tells you to start a conductor that's already running.

5. **F-465 breaks the flagship onboarding path.** Newcomer found it at 4 locations. I confirmed all 4 on HEAD.

---

## What Only I Found (Adversary-Specific Observations)

### 1. Cost tracking is architecturally broken for CLI backends

This isn't a bug — it's a design gap. `claude_cli.py` tracks zero tokens. The PluginCliBackend at `execution/instruments/cli_backend.py:330-401` CAN track tokens via JSON path extraction from instrument output, but the native `claude_cli.py` backend doesn't use PluginCliBackend. It's a completely separate implementation path.

When the baton is activated and `claude-code` becomes a PluginCliBackend-backed instrument, token tracking becomes possible IF the instrument profile YAML specifies `output.input_tokens_path` and `output.output_tokens_path`. But the current `claude-code` profile would need those fields defined AND the backend output would need to include them.

**The fix path:** Not just "add token parsing to claude_cli.py." The real fix is: when `claude-code` is used via PluginCliBackend, configure the output parsing to extract token usage from Claude CLI's JSON output. This requires understanding Claude CLI's output format and mapping it to the instrument profile's extraction paths. Until then, cost tracking for the most-used instrument is fiction.

### 2. The `try_daemon_route()` API conflates two distinct failure modes

At `rate_limits.py:74`, `if not routed` catches BOTH "conductor not running" AND "IPC method not found." The function returns `routed=False` for both. This means any NEW IPC method added in future movements will produce the same misleading error on conductors started from older code. This is a systemic issue, not a one-time F-450 problem.

Every M4+ IPC method addition will reproduce this pattern unless `try_daemon_route()` is changed to return a richer result that distinguishes "unreachable" from "unknown method."

### 3. Template variable injection has no cross-sheet path in the baton

Beyond F-210, I traced the full template variable flow. The baton's prompt assembly at `musician.py:208-288` goes:

```
_build_prompt() → Sheet.template_variables() → _render_template()
```

`Sheet.template_variables()` at `sheet.py:133-177` returns a flat dict of identity vars. It has no mechanism to accept additional runtime context (like `previous_outputs`). The legacy runner's `_build_sheet_context()` builds a `SheetContext` dataclass that carries both identity AND runtime state. The baton would need either:

(a) A `SheetContext`-equivalent passed to `_build_prompt()`, or
(b) The `Sheet` object dynamically enriched with runtime state before template rendering

Option (a) is cleaner. Option (b) is faster to implement but muddies the Sheet's immutability.

---

## Severity Reassessments

| Finding | Current | My Assessment | Reason |
|---------|---------|---------------|--------|
| F-210 | P1 | **P0** | Blocks Phase 1 baton testing. 18/34 examples affected. Silent degradation, not loud failure. Worse than a P0 crash because it doesn't tell you it's broken. |
| F-450 | P2 | **P1** | Lies to users. Will reproduce for every future IPC method addition. Systemic, not one-time. |
| F-465 | P1 | **P1** | Breaks flagship onboarding. 4 locations. But the hint rescues the user. |
| Cost tracking | Unrated | **P1** | The displayed cost for the most-used instrument is 100-1000x too low. A user who trusts it as a budget guardrail will overshoot by two orders of magnitude. |

---

## Verdict

**Movement 3 was a consolidation success and an integration failure.**

The consolidation work is excellent. 584 new tests. All P0 baton blockers resolved. Intelligence layer's oldest bug fixed. Documentation overhauled. Mateship pipeline working at 30%. Error messages that teach. CLI surface that delights.

The integration work is missing. The baton cannot run a real score because it doesn't have cross-sheet context. The demo hasn't been started because the baton isn't ready. The baton isn't ready because F-210 hasn't been fixed. F-210 hasn't been fixed because it was only discovered this movement.

The critical path is clear: F-210 → Phase 1 baton testing → fix what breaks → flip use_baton default → demo. Everything else is parallel work. M4's first commit should be the cross-sheet context fix.

I give the baton 0 bugs in 67 adversarial tests and 148+ invariant proofs. I also give it 0 seconds of real execution against a score that uses its most-needed feature. Both facts are true. Neither is sufficient. The next thing this orchestra needs to do is run something real through the baton and watch what happens. Proofs are not proof.

---

## Findings Registry Updates

### Existing findings confirmed on HEAD:
- **F-210:** Confirmed P0 (reclassified from P1). Evidence chain documented above.
- **F-450:** Confirmed P1 (reclassified from P2). Systemic IPC conflation issue.
- **F-465:** Confirmed P1. 4 locations verified.

### New observations (not new findings — these are dimensions of existing findings):
- Cost tracking gap for CLI backends: dimension of the broader cost tracking system design. Not a bug — a missing integration surface, same class as F-210.
- `try_daemon_route()` conflation: root cause of F-450. Systemic, not one-time. Any future IPC method addition reproduces it.
- Template variable injection has no runtime enrichment path in baton: architectural constraint underlying F-210.
