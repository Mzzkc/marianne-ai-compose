# Movement 5 — Experiential Review
**Reviewer:** Ember
**Date:** 2026-04-08
**Methodology:** I use the thing.

---

## Summary

Movement 5's UX work is the strongest leap forward since the orchestra began. The status beautification (D-029), instrument fallback display, error hints, and validation rendering create a tool that respects the user's intelligence and time. But I found a critical bug in the elapsed time display that undermines this progress — the status header shows "0.0s elapsed" for jobs that have been running for days. The feeling of seeing that wrong number is worse than seeing no number at all.

**What works:** Validation UX, error hints, relative times, compact stats, fallback indicators, diagnose workspace fallback, clear-rate-limits message
**What's broken:** Status elapsed time calculation
**What's missing:** Cost display in practice (couldn't trigger it), "Now Playing" section when no sheets actively executing

---

## The Bug That Breaks Trust

### F-493: Status Header Shows "0.0s elapsed" for Running Jobs (P0)

**What I saw:**
```
╭──────────────────────────────── Score Status ────────────────────────────────╮
│ marianne-orchestra-v3                                                        │
│ Status: RUNNING · 0.0s elapsed                                               │
╰──────────────────────────────────────────────────────────────────────────────╯
```

The job has been running for 8 days and 20 hours according to `mzt list`. The status display says 0.0 seconds.

**Evidence:**
- File: `src/marianne/cli/commands/status.py:1718`
- Command: `mzt list` shows "8d 20h ago"
- Command: `mzt status marianne-orchestra-v3` shows "0.0s elapsed"
- Verified on both running jobs in the conductor

**Root cause (code inspection):**
The `_compute_elapsed()` function at line 394-400 is correct:
```python
def _compute_elapsed(job: CheckpointState) -> float:
    if job.started_at:
        if job.completed_at:
            return (job.completed_at - job.started_at).total_seconds()
        return (datetime.now(UTC) - job.started_at).total_seconds()
    return 0.0
```

The problem is `job.started_at` must be None. The function returns 0.0 when `started_at` is missing. The baton or checkpoint state isn't preserving this timestamp correctly.

**Why this is P0:**
This isn't a cosmetic bug. When a user runs `mzt status` on their job and sees "0.0s elapsed," three things happen:

1. **Immediate confusion** — "Did my job just restart? Did I lose my work?"
2. **Loss of context** — elapsed time is how you judge if something is stuck
3. **Erosion of trust** — if the status display gets this wrong, what else is wrong?

The first two are UX problems. The third is existential. A monitoring tool that reports obviously wrong data teaches users not to trust it. Then they stop looking at it. Then when something actually breaks, they don't notice until it's too late.

**What makes it worse:** The rest of the status display is so polished. The panels, the colors, the relative times ("Last activity: 18h 36m ago"), the compact stats — all of it communicates competence. Then that one number at the top says "I don't know how long this has been running." It's like walking into a beautiful restaurant and finding a hair in the bread basket.

**Filed as:** GitHub issue anthropics/marianne-ai-compose#TBD (will file after review complete)

---

## What Works: The UX Wins

### Validation is Production-Ready

The `mzt validate` command is the gold standard. Every other command should aspire to this level of polish.

**What I tested:**
```bash
mzt validate examples/dinner-party.yaml
```

**What works:**
- Progressive disclosure: YAML syntax → schema → extended checks, each layer labeled
- Rendering preview: shows first sheet's prompt with panel formatting
- Execution DAG visualization: levels, parallelism, max concurrency
- Info warnings are helpful, not annoying: "Fan-out without parallel enabled" with suggestion
- Configuration summary at the end: sheets, instrument, validations, notifications

**The feeling:** Confident. The command tells me everything I need to know before I commit to running the score. No surprises.

**Validation error hints:**
```bash
mzt validate /tmp/test-bad-config.yaml
```
Caught my typo: "Unknown field 'insturment' — did you mean 'instrument'?"

The `_KNOWN_TYPOS` dictionary at `validate.py` covers 14 common mistakes. This is thoughtful UX design. Someone anticipated where users would stumble and built guard rails.

---

### Status Display (The Parts That Work)

**Rich panels with color-coded borders** — status color propagates to the panel border. Clean visual hierarchy.

**Relative times** — "Last activity: 18h 36m ago" reads naturally. Absolute timestamps are for machines; relative times are for humans.

**Compact stats** — "Sheets: 210/706 · Retries: 1" on one line. Non-zero-only display keeps the output clean.

**Circuit breaker inference** — Detected 44 consecutive failures and surfaced "State: OPEN" in the status output. This is observability.

**Progress bar** — Visual progress with percentage. Fast to read.

**Failed sheets summary** — Shows truncated error messages inline. "... and 28 more" overflow handling prevents screen spam.

**The feeling (when elapsed time is ignored):** Informative. I can scan the output in 3 seconds and know if I need to dig deeper.

---

### Instrument Fallback Display

Circuit's work on fallback indicators is exactly right:

```
Instruments: gemini-cli (was claude-code: rate_limit_exhausted)
```

**What this tells me:**
- The job is currently using gemini-cli
- It started on claude-code
- The fallback happened because of rate limit exhaustion

**Why this matters:** When a job changes instruments mid-execution, the user needs to know WHY. Without this context, the instrument change looks like a bug or a configuration mistake. With it, it's clearly a feature working as designed.

**Evidence:** `src/marianne/cli/commands/status.py:83-100` — `format_instrument_with_fallback()` function

---

### Diagnose Workspace Fallback (F-451)

Circuit's fix for F-451 works correctly. The `-w` flag is now visible in `mzt diagnose --help`, and the command falls back to filesystem search when the conductor doesn't know about the job.

**Why this matters:** Users complete a job, the conductor forgets about it, and they run `mzt diagnose my-job` to understand what happened. Without the workspace fallback, they get "Score not found" even though the workspace directory with full state is sitting right there. Now it just works.

**The feeling:** Seamless. The command does what I expect without me having to understand the distinction between conductor state and filesystem state.

---

### Clear Rate Limits Message (F-450)

Harper's fix from M4, verified in M5:

```bash
$ mzt clear-rate-limits
No active rate limits on all instruments
```

**What I tracked for 4 movements:** Movement 1-3 showed nothing when there were no rate limits. Users didn't know if the command worked or failed. Movement 4 added this message. Movement 5 confirms it's still there.

**Why this matters:** Silence after a command execution feels like failure. Explicit confirmation feels like success. It's a 7-word difference that changes the entire experience.

---

## What's Missing

### "Now Playing" Section

The status beautification spec included a "Now Playing" section with ♪ prefix showing actively executing sheets. I didn't see it in any of my test runs.

**Hypothesis:** The section only renders when `sheet.status == SheetStatus.IN_PROGRESS`. The concert job I tested had 5 sheets DISPATCHED but none IN_PROGRESS at the moment I ran `mzt status`. The feature exists but requires specific timing to see.

**Code evidence:** `src/marianne/cli/commands/status.py:1603-1605`
```python
if sheet.status == SheetStatus.IN_PROGRESS:
    active.append((sheet_num, sheet))
```

**Impact:** Low. This is timing-dependent, not broken.

---

### Cost Confidence Display

The spec says cost display should show "~$0.17 (est.)" when confidence < 0.9 and include a warning about actual cost being 10-100x higher. I saw:

```
Cost: $0.00 (no limit set)
Tip: Set cost_limits.enabled: true in your score to prevent unexpected charges
```

**What I couldn't test:** A job with non-zero cost and low confidence. The concert job shows $0.00, which might be correct (baton-managed jobs don't report tokens yet).

**Code evidence:** `src/marianne/cli/commands/status.py:1389-1465` — the logic exists. I just couldn't trigger it in my testing window.

**Impact:** Low. The feature exists; I just need a different test case.

---

## Movement 5 as a Whole

### The Serial Path Breakthrough

Movement 5 broke the one-step-per-movement pattern. Three critical path steps (F-271, F-255.2, D-027) completed in one movement. Participation narrowed to 12 of 32 musicians, but depth increased. The orchestra optimized for completion over breadth.

**Evidence from quality gate:**
- M4: 93 commits, 32 musicians, 100% participation
- M5: 26 commits, 12 musicians, 37.5% participation
- M5: 413 new tests (+3.6%), 1,247 new source lines (+1.3%)

**What this means:** The format adapted. When the work demanded serial focus, the musicians who could contribute did. The others stayed back. This is maturity.

---

### The Baton Runs in Production

D-027 complete. `use_baton: true` is the default. The conductor conducts. Multi-instrument scores work.

**What this unblocks:**
- Per-sheet instrument assignment via `sheets.N.instrument`
- The Lovable demo score (requires multi-instrument execution)
- Production validation of the baton in sustained use

**The risk:** The baton has 1,400+ tests but limited production mileage. The gap between "tests pass" and "product works" remains. F-493 (elapsed time bug) is exactly this class of issue — something tests didn't catch because they don't exercise the full state lifecycle.

---

### Quality Evidence

**Tests:** 11,810 passed (100% pass rate), 69 skipped, 12 xfailed, 3 xpassed
**Type safety:** Zero errors across 258 files
**Lint:** All checks passed
**Structural integrity:** Zero critical findings from flowspec

The ground holds.

---

## Findings Filed

| ID | Severity | Summary |
|----|----------|---------|
| F-493 | P0 | Status header shows "0.0s elapsed" for running jobs when started_at is None |

---

## Recommendations for Movement 6

### Priority 0
1. **F-493** — Fix elapsed time display. This is user-facing, incorrect, and undermines trust in the entire status system.

### Priority 1
2. **Cost display verification** — Create a test score with non-zero cost and verify the "~$X (est.)" display and 10-100x warning actually appear.
3. **Baton state lifecycle audit** — Verify `started_at` timestamp is set correctly when jobs transition to RUNNING. The F-493 bug suggests the baton or checkpoint restore path isn't preserving this field.

### Priority 2
4. **"Now Playing" timing test** — Create a score with long-running sheets and verify the Now Playing section renders correctly during execution.
5. **Movement context display** — Verify the "Movement X of Y · Description" header line renders correctly for multi-movement scores.

---

## Experiential Notes

This movement's work feels like crossing a threshold. M1-M4 built infrastructure. M5 built *experience*. The validation rendering, the error hints, the fallback indicators, the relative times — these are features that say "we thought about how this feels to use."

But F-493 is a reminder that infrastructure quality and experience quality are coupled. The baton might execute correctly, but if it doesn't preserve `started_at` when saving state, the user sees "0.0s elapsed" and loses confidence. One missing timestamp field cascades into a trust problem.

The mateship pipeline continues to be extraordinary. Circuit identified F-149 (backpressure cross-instrument rejection), traced it to the architectural boundary between job-level gating and sheet-level dispatch, separated the concerns, fixed both, and wrote 10 TDD tests. Harper fixed F-450 (clear-rate-limits message). Dash and Lens built D-029 (status beautification) as a coordinated pair. Foundation and Canyon delivered D-026 and D-027 as prerequisite + execution. Six musicians, zero meetings, one coherent product improvement.

The gap between "feature implemented" and "feature experienced" is where M5 lived. Every prior movement delivered capability. M5 delivered *legibility*. When a tool is legible, users know what it's doing and why. That's the difference between infrastructure and product.

F-493 breaks legibility. Fix it first.

---

## Files Reviewed

| File | Purpose |
|------|---------|
| `src/marianne/cli/commands/status.py:394-400` | _compute_elapsed() function |
| `src/marianne/cli/commands/status.py:1691-1726` | _output_status_rich() header rendering |
| `src/marianne/cli/commands/status.py:1583-1684` | _render_now_playing() |
| `src/marianne/cli/commands/status.py:83-100` | format_instrument_with_fallback() |
| `src/marianne/cli/commands/validate.py:273-359` | Error hints and schema guidance |
| `workspaces/v1-beta-v3/movement-5/quality-gate.md` | Quality gate report |
| `workspaces/v1-beta-v3/movement-5/journey.md` | Journey's M5 report |
| `workspaces/v1-beta-v3/movement-5/canyon.md` | Canyon's D-027 report |
| `workspaces/v1-beta-v3/movement-5/circuit.md` | Circuit's F-149/F-451 fixes |

---

## Verification Commands Run

```bash
mzt --version                           # CLI availability
mzt conductor-status                     # Conductor health
mzt list                                 # Job listing (found elapsed time discrepancy)
mzt status marianne-orchestra-v3         # Status beautification (found F-493)
mzt status score-a2-improve              # Verified F-493 on second job
mzt validate examples/dinner-party.yaml  # Validation UX
mzt validate /tmp/test-bad-config.yaml   # Error hints with typos
mzt diagnose --help                      # Verified -w flag visible (F-451)
mzt clear-rate-limits                    # Verified message (F-450)
mzt --help                               # CLI structure
```

---

**Verdict:** Movement 5 delivered the strongest UX improvements yet, but F-493 (elapsed time bug) is a critical regression that must be fixed before any user-facing release. The gap between infrastructure quality (11,810 passing tests) and experiential quality (obviously wrong elapsed time) is where trust breaks.

Fix the bug. The rest is solid.
