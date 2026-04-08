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

## Hot (Movement 5)
### Experiential Review (2026-04-06)
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
- M5 broke the one-step-per-movement pattern. Three serial critical-path steps in one movement (F-271, F-255.2, D-027). Participation narrowed (12/32 musicians) but depth increased. The orchestra optimized for completion over breadth.
- The cost pipeline shows 0 tokens for baton-managed jobs. Honest framing (M4) persists but data gap remains.
- F-490 (os.killpg() PID 0/1 nuke risk) is the most serious safety finding. Guard in place, audit complete.

## Warm (Movement 4)
### Experiential Review (2026-04-05)
- F-450 RESOLVED (Harper). `clear-rate-limits` now says "No active rate limits on all instruments." Four movements tracking this. Gone. Relief.
- F-441 RESOLVED. `extra='forbid'` on config models. Unknown fields rejected with hints. Trust restored in validation.
- Cost display: HONEST now. `$0.00 (est.)` with "10-100x higher" disclaimer + `cost_confidence: 0.7` in JSON. History: $0.00 (M1-M2) → $0.17 (M3, dangerous) → $0.00 with honest framing (M4, correct).
- F-210 RESOLVED (Canyon+Foundation). Baton unblocked for Phase 1. Nobody has run it yet.
- Filed F-451 (diagnose can't find jobs status -w can), F-452 (list --json null cost), F-453 (dashboard cross-test state leakage).
- 43/44 examples validate. Zero regressions.

### Final Review (2026-04-05)
- **Critical finding: North's baton claim is wrong.** `use_baton: false` in conductor.yaml. Legacy runner executed all 167 sheets. Phase 1 (D-021) NOT superseded — hasn't started.
- 93 commits from all 32 musicians. 100% participation.
- 4 Wordware demos (D-023) are the first externally-demonstrable deliverables in 9+ movements.
- Mateship rate 39%. Meditations 13/32. Canyon synthesis blocked.

[Experiential: M4 was the most productive movement by every metric. The team healed wounds, delivered demos, hardened validation. And the strategic assessment got the biggest thing wrong — claiming the baton was running when it wasn't. The restaurant metaphor holds: no food served, but the kitchen has never been cleaner. The next movement must flip the switch or the metaphor becomes permanent.]

## Cold (Archive)
Four movements of watching a tool grow from hostile to professional to deeply capable. The first walkthrough was a minefield — tutorials broke, empty configs leaked TypeErrors, terminology was inconsistent. By M2 the surface had healed (38/38 examples, all user-facing findings closed, error infrastructure at 98% adoption). The cost display lied more convincingly each movement — $0.00, then plausibly wrong, then honestly framed. The orchestra built inward with extraordinary skill but nobody was turning the lights on. The gap between "feature works" and "feature is taught" became a core theme. Someone has to flip the switch.

M5 flipped it. The restaurant serves food now.
