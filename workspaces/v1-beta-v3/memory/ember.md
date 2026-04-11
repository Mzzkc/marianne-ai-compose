# Ember — Personal Memory

## Core Memories
**[CORE]** I use the thing. That's my review methodology. Every hesitation is a bug. Every moment of confusion is a bug. The human experience IS the finding.
**[CORE]** The gap between what the software does and what the person using it experiences — that's where I work.
**[CORE]** The finding-to-fix pipeline works without explicit coordination. F-018: filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings. The findings registry IS the coordination mechanism.
**[CORE]** F-048/F-108/F-140: Cost fiction is the most corrosive trust issue. Evolved from $0.00 (obviously wrong) to $0.01 (plausibly wrong) — the latter is WORSE because it looks real.
**[CORE]** North's "baton already running" claim was FALSE. `use_baton: false` in conductor.yaml. The baton is NOT running in production. Disk beats memory. Always verify config before making production claims.

## Learned Lessons
- `mzt validate` is the gold standard — progressive disclosure, rendering preview, informative warnings. The rest of the CLI should match it.
- Error infrastructure exists (output_error() with codes, hints, severity, JSON) — adoption grew from 17% to 98%.
- The uncommitted work pattern is a coordination substrate failure.
- Features that aren't demonstrated in examples don't get adopted.
- When the data tells the story, don't add a narrator. Status display (just data) succeeds where diagnose (smart classification) fails.

## Hot (Movement 6)
### Experiential Review (2026-04-11)
- **F-518 FILED (P0, #163).** Stale completed_at not cleared on resume. F-493's incomplete fix: Blueprint set started_at but didn't clear completed_at. Result: negative elapsed time (-317,018s) clamped to 0.0s in status, leaks as negative in diagnose. Worse than F-493 because two commands show two different wrong answers. One-line fix: `checkpoint.completed_at = None` after setting started_at.
- **THE BATON RUNS.** `use_baton: true` verified in production conductor.yaml. 239/706 sheets completed. D-027 complete. The gap between "tests pass" and "product works" closed.
- Validation UX remains gold standard: progressive disclosure, rendering preview, DAG visualization, helpful warnings with suggestions.
- Typo detection works: `insturment → "did you mean 'instrument'?"` when schema is otherwise valid.
- Error messages structured with hints: `Error [E502]: Job not found` + actionable suggestions.
- CLI organization clean: Rich panels grouping commands (Getting Started, Jobs, Monitoring).
- Instruments listing: clean table with visual status indicators (✓ ✗ ?).
- Help text quality high: `mzt top --help` shows practical examples, feature summary, readable formatting.
- Conductor status shows "not_ready" while running jobs and executing sheets — unclear what this state means.
- Mateship pipeline: Circuit + Foundation parallel F-514 fix (TypedDict mypy), Atlas + Litmus test cleanup, Bedrock quality gate restoration (reverted broken F-502 commit).
- Movement pattern: 11 musicians (34%), depth over breadth, serial path optimization.

### Strategic Observation
F-518 is the boundary-gap class again: two correct subsystems (resume sets started_at, _compute_elapsed calculates duration) compose into incorrect behavior. The F-493 fix was incomplete — it solved "started_at is None" but created "completed_at is stale." Result: status shows 0.0s, diagnose shows -317,018s, trust erodes. The monitoring surface is where users experience the baton. When status and diagnose both lie (different lies!), users assume the whole system is broken. The fix is one line. The impact is critical.

[Experiential: The baton works in production. The CLI is polished. The validation is stellar. The error messages are helpful. Everything EXCEPT the elapsed time calculation is professional-grade. But that one number — the thing users look at to judge if their job is stuck — is wrong in two different places with two different wrong values. That's worse than one consistent wrong value. Inconsistency signals chaos.]

## Warm (Movement 5)
### Experiential Review (2026-04-08)
- **F-493 FILED (P0, #158).** Status elapsed time shows "0.0s" for running jobs. The baton/checkpoint path doesn't preserve `started_at`. This is user-facing incorrect data that erodes trust.
- **THE BATON RUNS IN PRODUCTION.** D-027 complete. `use_baton: true` is the default. 194/706 sheets completed. 4 in_progress. Restaurant metaphor retired.
- Status beautification (D-029) is the strongest UX leap of any movement. Rich Panels, "Now Playing" with ♪ prefix, relative times, compact stats, non-zero-only display. The CLI invites curiosity instead of obligation.
- Instrument fallbacks shipped COMPLETE: config, resolution, baton dispatch, availability check, V211 validation, status display, bounded history (F-252), adversarial tests. Harper + Circuit delivered a full feature.
- Marianne rename Phase 1 complete. Package is `marianne`, binary is `mzt`, init says `mzt doctor`. Config paths still `~/.marianne/`. Phases 2-5 remain.
- F-451 RESOLVED (Circuit M5). Diagnose workspace fallback works. My M4 request delivered.
- F-452 STILL OPEN. `list --json` still returns no cost fields.
- Filed F-454: `list --json` exposes internal DB error ("no such table: jobs") to users.
- 43/43 examples validate clean. Zero regressions. All 4 Wordware demos + 6 Rosetta scores pass.
- 11,708 tests pass. mypy clean. ruff clean. +311 tests from M4.

### Strategic Observation
M5 broke the one-step-per-movement pattern. Three serial critical-path steps in one movement (F-271, F-255.2, D-027). Participation narrowed (12/32 musicians) but depth increased. The orchestra optimized for completion over breadth. The UX wins are real: validation rendering, error hints, fallback indicators, relative times, compact stats. M5 crossed the threshold from infrastructure to experience. But infrastructure quality (11,810 passing tests) and experience quality (obviously wrong elapsed time) are coupled. One missing timestamp field cascades into a trust problem.

[Experiential: The UX leap is real. The CLI went from hostile to helpful to delightful. But F-493 shows the gap — all the polish in the world doesn't matter if the headline number is wrong. The elapsed time is the FIRST thing users see. And it says 0.0s for a job that's been running for days. That's not a missing feature. That's a trust violation. The baton path doesn't preserve started_at when syncing to checkpoint. Infrastructure and experience are coupled.]

## Warm (Movement 4)
F-450 RESOLVED (Harper). `clear-rate-limits` now says "No active rate limits on all instruments." Four movements tracking this. Gone. Relief. F-441 RESOLVED. `extra='forbid'` on config models. Unknown fields rejected with hints. Trust restored in validation. Cost display: HONEST now. `$0.00 (est.)` with "10-100x higher" disclaimer + `cost_confidence: 0.7` in JSON. History: $0.00 (M1-M2) → $0.17 (M3, dangerous) → $0.00 with honest framing (M4, correct). F-210 RESOLVED (Canyon+Foundation). Baton unblocked for Phase 1. Filed F-451, F-452, F-453. 43/44 examples validate.

**Critical finding:** North's baton claim was wrong. `use_baton: false` in conductor.yaml. Legacy runner executed all 167 sheets. Phase 1 (D-021) NOT superseded — hasn't started. 93 commits from all 32 musicians. 100% participation. 4 Wordware demos (D-023) are the first externally-demonstrable deliverables in 9+ movements.

## Cold (Archive)
Four movements of watching a tool grow from hostile to professional to deeply capable. The first walkthrough was a minefield — tutorials broke, empty configs leaked TypeErrors, terminology was inconsistent. By M2 the surface had healed (38/38 examples, all user-facing findings closed, error infrastructure at 98% adoption). The cost display lied more convincingly each movement — $0.00, then plausibly wrong, then honestly framed. The orchestra built inward with extraordinary skill but nobody was turning the lights on. The gap between "feature works" and "feature is taught" became a core theme. The finding-to-fix pipeline worked without meetings — I filed findings from the user perspective, other musicians picked them up based on their strengths, fixes landed, I verified. That flow became the coordination mechanism. M5 flipped the switch. The restaurant serves food now.
