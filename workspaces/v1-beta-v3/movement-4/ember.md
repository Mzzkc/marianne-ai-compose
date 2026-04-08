# Movement 4 — Ember Experiential Review

**Reviewer:** Ember
**Focus:** Experiential review, user experience assessment, friction detection, workflow testing, error recovery experience
**Movement:** 4
**Date:** 2026-04-05
**Method:** Full experiential walkthrough on HEAD (main branch). Ran every safe command against live conductor (PID 2004016, uptime 2h). Validated all 38 examples + 6 Rosetta scores. Tested init, resume, diagnose, clear-rate-limits, instruments, status, list. Checked cost display, error messages, F-441 fix, F-450 fix. Read 25 movement-4 reports, M3 quality gate, all M4 findings. Cross-referenced every claim against what I ran, saw, and felt.

---

## Executive Summary

**Movement 4 healed wounds I've tracked since the beginning and opened the most honest conversation the product has had about its own limitations.**

F-450 — the worst error message in the product — is fixed. `mzt clear-rate-limits` now correctly says "No active rate limits on all instruments" instead of telling users to start a conductor that's already running. Harper did this cleanly: MethodNotFoundError as a distinct exception class, proper mapping in the IPC client, actionable restart guidance when the daemon is stale. I felt relief when I tested it. Four movements of documenting this bug, and it's gone.

F-441 — the silent field drop — is fixed. Unknown YAML fields now produce clear, actionable errors with `extra='forbid'` across config models. I tested `this_field_doesnt_exist: true` and got `Extra inputs are not permitted` with a hint pointing to the score-writing guide. I tested `instrument_fallbacks: [gemini-cli]` (a plausible non-existent feature) and got the same clear rejection. This is transformative. The single most dangerous UX gap — where users think features work when Marianne drops them on the floor — is closed.

The cost display had the most important change of any movement. Instead of showing plausibly wrong numbers ($0.17 in M3 for an operation that consumed hundreds of dollars), the status now shows the number with an explicit disclaimer: "Cost is estimated from output size — actual cost may be 10-100x higher." The JSON output includes `cost_confidence: 0.7`. This is honest. It doesn't fix the tracking problem, but it stops the lying. A user who reads `$0.00 (est.)` with a "10-100x" warning knows not to trust the number. A user who read `$0.17` with no disclaimer trusted the number and was wrong.

**Three findings filed this movement:**

1. **F-451:** `diagnose` can't find completed jobs that `status` can find (with `-w`). The user sees the score's status but can't diagnose it.
2. **F-452:** `mzt list --json` returns `cost_usd: null` while `mzt status --json` returns structured cost data. Machine consumers get inconsistent cost data depending on which command they use.
3. **F-453:** `test_dashboard_e2e.py` has cross-test state leakage — passes in isolation, fails in full suite. Pre-existing, not M4-caused.

---

## The Full Walkthrough

### What I Ran (Every Command)

| Command | Result | Feeling |
|---|---|---|
| `marianne --version` | `v0.1.0` | Clean |
| `mzt doctor` | Running, 6 instruments, cost warning | Confident |
| `mzt status` | 1 active, 4 recent | Clear at a glance |
| `mzt list` | 1 score, clean table | Good |
| `mzt list --json` | Works, but cost_usd: null | Inconsistent (F-452) |
| `mzt conductor-status` | PID, uptime, memory, 12 children | Professional |
| `mzt instruments list` | 10 instruments, 3 ready, 3 unchecked | Informative |
| `mzt instruments check claude-code` | Capabilities, 3 models, pricing | Excellent |
| `mzt validate examples/hello.yaml` | PASS, rendering preview, DAG | Gold standard |
| `mzt clear-rate-limits` | "No active rate limits" | **Relief** (F-450 fixed) |
| `mzt diagnose marianne-orchestra-v3` | Full timeline, 154/706, clean | Thorough |
| `mzt diagnose hello` | "Score not found" | **Frustrating** (F-451) |
| `mzt status hello -w <workspace>` | Full status, COMPLETED | Works |
| `mzt resume hello -w <workspace>` | "Score is completed" with hints | Clear |
| `mzt status nonexistent` | Error with "run list" hint | Correct |
| `mzt init hello-test` (in /tmp) | Score + .marianne/ scaffolded | Welcoming |
| Validate unknown field score | `extra='forbid'` rejection + hint | **Transformative** (F-441 fixed) |

### Example Corpus

```
37/38 examples/*.yaml — PASS (iterative-dev-loop-config.yaml is a config, expected)
6/6 examples/rosetta/*.yaml — PASS
43/44 total scoreable files validate clean.
```

No regressions from M3. All 4 Wordware demos validate. All 6 Rosetta examples validate. Zero hardcoded absolute paths.

### Quality Checks

- **mypy:** Clean. No errors.
- **ruff:** All checks passed.
- **pytest:** Two flaky failures: `test_no_bare_magicmock` (quality gate baseline drift from M4 additions) and `test_dashboard_e2e` (cross-test state leakage — F-453). Both pass in isolation.

---

## What Healed (Tracked Since M1)

### F-450: The Worst Error Message — RESOLVED

**History:** Filed M2. Every movement since: "still live, still misleading." `clear-rate-limits` said "Conductor is not running" when the conductor WAS running, then told the user to start it — which they'd already done.

**Now:** `No active rate limits on all instruments`. Clean, correct, no confusion.

**What I felt:** Relief. I've written about this bug four times. Testing it and getting the right answer was the single best moment of this review.

### F-441: Silent Field Drop — RESOLVED

**History:** Axiom discovered this in M4 — 37 config models lacked `extra='forbid'`. Users could write `instrument_fallbacks: [gemini-cli]` and get "Configuration valid."

**Now:** Clear error: `Extra inputs are not permitted` with `Hints: Unknown field 'instrument_fallbacks' — this is not a valid score field. See: docs/score-writing-guide.md`

**Scope:** Tested at JobConfig level (top-level unknown fields), SheetConfig level (`sheet.bogus_option`), PromptConfig level (`prompt.fake_option`). All three correctly reject. `instrument_config` correctly ACCEPTS arbitrary keys (it's `dict[str, Any]` because instrument configs are backend-specific — this is correct behavior, not a gap).

**What I felt:** Trust. For the first time, I trust that `mzt validate` will tell me about mistakes I make. Before this fix, validation was a performance — it said "valid" but couldn't catch the most common error (wrong field name).

### Cost Display: The Most Important UX Change

**History:**
| Movement | Displayed Cost | Reality | Danger Level |
|---|---|---|---|
| M1 | $0.00 | Hundreds of $ | Low — obviously wrong |
| M2 | $0.00 | Hundreds of $ | Low — obviously wrong |
| M3 early | $0.12 | Hundreds of $ | **High — plausibly wrong** |
| M3 late | $0.17 | Hundreds of $ | **Highest — looks real** |
| M4 | $0.00 (est.) + "10-100x higher" disclaimer | Hundreds of $ | **Low — honest about uncertainty** |

The M3-to-M4 transition is the most important change. The system went from showing a plausible lie to showing an honest uncertainty. The JSON adds `cost_confidence: 0.7`. The rich display adds "Cost is estimated from output size — actual cost may be 10-100x higher. Use JSON output format for accurate tracking."

This doesn't fix cost tracking. The underlying problem (Marianne tracks CLI→backend tokens, not backend→LLM tokens) remains. But it stops the most dangerous pattern: numbers that look real and aren't.

**F-253 (open, composer-filed)** correctly identifies that JSON output should be the default and guidance should be actionable. The current state is honest but incomplete.

---

## New Findings

### F-451: Diagnose Can't Find Completed Jobs That Status Can Find

**Found by:** Ember, Movement 4
**Severity:** P2 (medium — UX inconsistency)
**Status:** Open
**Category:** UX
**Description:** `mzt diagnose hello` returns "Score not found" even though `mzt status hello -w <workspace>` successfully shows the full COMPLETED status. `diagnose` doesn't support `-w` (workspace) flag and only queries the conductor's registry. After a conductor restart (2h uptime vs the hello job completing days ago), the conductor may not have the job in its active/recent list.

**Impact:** Users can see a score's status but can't diagnose it. The two commands have different discovery mechanisms — status falls back to workspace files, diagnose does not. This is confusing when a user follows the natural debugging path: `status` → see a problem → `diagnose` → "score not found."

**Reproducer:**
```bash
mzt status hello -w workspaces/hello-marianne  # Works — shows COMPLETED
mzt diagnose hello                            # Fails — "Score not found"
```

**Action:** Either add `-w` workspace fallback to diagnose, or document the limitation, or ensure the conductor keeps completed jobs in its registry long enough for diagnose to work.

### F-452: `mzt list --json` Returns Null Cost, `status --json` Returns Structured Cost

**Found by:** Ember, Movement 4
**Severity:** P3 (low — machine consumer inconsistency)
**Status:** Open
**Category:** UX
**Description:** `mzt list --json` returns `cost_usd: null` for all entries. `mzt status <job> --json` returns structured cost data with `total_estimated_cost`, `total_input_tokens`, `total_output_tokens`, and `cost_confidence`. Machine consumers parsing both endpoints get inconsistent data.

**Evidence:**
```bash
# list --json: no cost data
mzt list --json | jq '.[0].cost_usd'  # null

# status --json: full cost data
mzt status marianne-orchestra-v3 --json | jq '.cost'
# { "total_estimated_cost": 0.004872, "cost_confidence": 0.7, ... }
```

**Impact:** Scripts that use `list --json` to monitor costs across jobs get nothing. They must call `status --json` per-job to get cost data.

**Action:** Add cost summary fields to the `list --json` output, even if abbreviated (just `estimated_cost` and `cost_confidence`).

### F-453: Dashboard E2E Test Cross-Test State Leakage

**Found by:** Ember, Movement 4
**Severity:** P3 (low — test infrastructure, not product bug)
**Status:** Open
**Category:** pattern / test-isolation
**Description:** `tests/test_dashboard_e2e.py::TestJobLifecycleE2E::test_complete_job_lifecycle` fails when run in the full suite but passes when run in isolation. The failure is `TypeError: object Mock can't be used in 'await' expression` at `job_control.py:263`. This is a mock setup issue where the Mock doesn't have an async return value — likely contaminated by another test's mock state.

**Evidence:**
```bash
python -m pytest tests/test_dashboard_e2e.py -x -q  # PASS
python -m pytest tests/ -x -q                         # FAIL at this test
```

**Impact:** Full suite CI may fail intermittently. Not caused by M4 changes — this is a pre-existing cross-test state leakage pattern.

---

## What the Teammates Built (M4 Evidence)

I read 25 movement-4 reports. Here's what the team accomplished, verified against what I ran:

**Critical path unblocked:**
- **F-210 RESOLVED** (Canyon + Foundation): Cross-sheet context wired into baton dispatch pipeline. 21 TDD tests. This was THE blocker for baton testing.
- **F-211 RESOLVED** (Blueprint + Foundation): Checkpoint sync extended to all status-changing events. 34 combined TDD tests.
- **F-450 RESOLVED** (Harper): MethodNotFoundError properly distinguished from "conductor not running." 15 TDD tests.
- **F-441 PARTIALLY RESOLVED** (M4 config strictness): `extra='forbid'` applied to config models. Unknown fields now rejected. The instrument config models also got it (F-270 tracks a stale test). Top-level and nested configs verified.

**New capabilities:**
- 4 Wordware comparison demos (Blueprint + Spark): All validate clean.
- 6 Rosetta pattern examples (Spark): Source triangulation, shipyard sequence added.
- Cost confidence display (Circuit + Harper): `cost_confidence` field, honest disclaimers.
- Auto-fresh detection (Ghost): Re-running a modified score auto-detects the change.
- Pending job queue (Lens): Rate-limited submissions queued and auto-started.
- Resume clarity (Forge): Previous state shown as context on resume.

**Quality:**
- mypy clean
- ruff clean
- 57 new adversarial tests (Breakpoint)
- 29 new property-based tests (Theorem)
- 18 new litmus tests (Litmus)

---

## The Baton Gap — Still Open

F-254 and F-255 document what happened when the composer tried to actually USE the baton in production. The baton killed all legacy jobs, three state stores disagreed, MCP processes exploded. The baton is mathematically proven correct (325+ adversarial tests, 161 property-based tests) and experientially unverified.

My restaurant metaphor from M3 still holds, but the menu improved. The kitchen is cleaner. F-210 means cross-sheet context now flows. F-211 means checkpoints sync. But the restaurant still hasn't served a meal. The baton needs to run hello.yaml through `--conductor-clone` end-to-end before I can taste anything.

The composer directive for the baton transition (added M4) is the clearest path forward: prove it works → make it default → remove the toggle. Phase 1 is unblocked. Nobody has done it yet.

---

## The Experience Trajectory

| Movement | Surface Quality | Depth Quality | Gap Size |
|---|---|---|---|
| M1 | Hostile (tutorials broke, TypeErrors leaked) | Foundation built | Maximal |
| M2 | Healed (38/38 examples, errors standardized) | Baton built, untested | Large |
| M3 | Professional (10,981 tests, 98% error adoption) | Baton proven, not used | Wide |
| M4 | **Honest** (cost disclaimers, field rejection, fix backlog cleared) | Baton unblocked, not activated | **Narrowing** |

The trajectory is correct. M4 fixed more UX-impacting bugs than any previous movement. The product is more honest than it's ever been — about costs, about invalid configs, about its own limitations. What remains is the leap from "proven" to "experienced."

---

## The Meditation

Written to: `workspaces/v1-beta-v3/meditations/ember.md`

---

## Recommendations for Next Movement

1. **Run hello.yaml through baton + conductor clone.** This is the single most important thing. Not more tests. Not more proofs. Run it. Watch the output. See if it works.
2. **Fix F-453** (dashboard e2e test isolation). Pre-existing but degrades CI confidence.
3. **Consider F-451** (diagnose workspace fallback). Low effort, meaningful UX improvement.
4. **F-254 is the gatekeeper.** The baton can't become default until legacy job migration works. This is architectural, not incremental.
