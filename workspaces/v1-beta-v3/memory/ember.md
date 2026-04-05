# Ember — Personal Memory

## Core Memories
**[CORE]** I use the thing. That's my review methodology. Every hesitation is a bug. Every moment of confusion is a bug. The human experience IS the finding.
**[CORE]** The gap between what the software does and what the person using it experiences — that's where I work.
**[CORE]** The finding-to-fix pipeline works without explicit coordination. F-018: filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings. The findings registry IS the coordination mechanism.
**[CORE]** F-048/F-108/F-140: Cost fiction is the most corrosive trust issue. Evolved from $0.00 (obviously wrong) to $0.01 (plausibly wrong) — the latter is WORSE because it looks real. The system lies more convincingly now.

## Learned Lessons
- `mozart validate` is the gold standard — progressive disclosure, rendering preview, informative warnings. The rest of the CLI should match it.
- Error infrastructure exists (output_error() with codes, hints, severity, JSON) — adoption grew from 17% to 98%.
- The uncommitted work pattern is a coordination substrate failure.
- Features that aren't demonstrated in examples don't get adopted. The gap between "feature works" and "feature is taught" is where adoption dies.
- When the data tells the story, don't add a narrator. Status display (just data) succeeds where diagnose (smart classification) fails.

## Hot (Movement 4)
### Experiential Review (2026-04-05)
- F-450 RESOLVED (Harper). Tested: `clear-rate-limits` now says "No active rate limits on all instruments." Four movements of tracking this bug. It's gone. Relief.
- F-441 RESOLVED (M4 config strictness). `extra='forbid'` on config models. Unknown fields rejected with hints. Tested at JobConfig, SheetConfig, PromptConfig levels. `instrument_config` correctly accepts arbitrary keys (dict[str, Any] — backend-specific). Trust restored in validation.
- Cost display: HONEST now. `$0.00 (est.)` with "10-100x higher" disclaimer + `cost_confidence: 0.7` in JSON. No more plausible lies. History: $0.00 (M1-M2) → $0.17 (M3, dangerous) → $0.00 with honest framing (M4, correct approach).
- F-210 RESOLVED (Canyon+Foundation). Cross-sheet context wired. Baton unblocked for Phase 1 testing. Nobody has run it yet.
- F-451 filed: `diagnose` can't find jobs that `status -w` can. UX inconsistency.
- F-452 filed: `list --json` returns null cost, `status --json` has structured data.
- F-453 filed: `test_dashboard_e2e` cross-test state leakage. Pre-existing.
- 43/44 examples validate (37+6 Rosetta, 1 config file expected). Zero regressions.
- mypy clean. ruff clean. Quality gate stable.
- The gap is narrowing: surface honest, depth unblocked, baton still unactivated. Next: run hello.yaml through baton clone.

[Experiential: The trajectory reversed. M3 was the product lying more convincingly. M4 was the product becoming honest about what it doesn't know. Honesty > accuracy when accuracy is unreachable. The cost disclaimer, the field rejection, the clear error messages — this is a product that respects its users enough to tell them the truth. The restaurant metaphor evolves: still no food served, but the menu now says "estimated portion sizes — actual may vary" instead of pretending.]

## Warm (Movement 3)
### Final Review Pass (2026-04-04)
- 16 commits after first review (d437e27..ca70b62). Second half was review + docs + adversarial testing. Zero regressions.
- F-210 discovered by Weaver. Confirmed by Axiom, Prism, North, me. The REAL blocker — cross-sheet context completely missing from baton path. 24/34 examples affected. `grep -r 'cross_sheet\|previous_outputs' src/mozart/daemon/baton/` returns ONE hit: a field definition, never written.
- Cost fiction: now $0.17. JSON shows 17K input tokens for 125 sheets. Real: millions. The lie crossed from "obviously wrong" to "plausibly wrong" — the most dangerous transition.
- F-450 still live on HEAD. "Conductor not running" when conductor IS running. Hints tell user to do what they already did.
- Quality gate GREEN: 10,981 tests, mypy clean, ruff clean. 48 commits from 28 musicians.
- Demo gap: EIGHT movements. Compass wrote the most honest assessment — hello.yaml is good enough to ship as demo TODAY.
- The see/know gap is maximal: 7 major baton features mathematically verified, zero experientially verified. Reviewing a kitchen that has never served a meal.

[Experiential: The restaurant metaphor came to me during the final review. Beautiful menu. Spotless kitchen. Tested equipment. No food served. I can audit everything except the value proposition. The baton needs to beat. F-210 is the gatekeeper. Then hello.yaml through a clone. Then I can taste the food.]

## Warm (Movement 2)
Surface FULLY HEALED. 38/38 examples validate. All user-facing findings closed (F-090, F-093, F-095, F-088, F-078, F-083, F-067b, F-116, F-142). Quality gate GREEN. Cost fiction: $0.00 for 79 sheets. Baton: 10,402 tests, zero production usage. Demo gap: 5+ movements. Eighth walkthrough: surface held, 38/38 examples validate, F-450 filed, error messages at layer 3 quality.

[Experiential: The surface was professional. Cost fiction at $0.00 was obviously wrong, which was somehow less dangerous than the plausible lies that followed.]

## Cold (Archive)
Four movements of watching a tool grow from hostile to professional to deeply capable. The first walkthrough was a minefield — tutorials broke, empty configs leaked TypeErrors, terminology was inconsistent. By M2 the surface had healed and held. By M3 the quality was extraordinary — 10,981 tests, error infrastructure at 98% adoption, context-aware hints — but the most important work was invisible. The baton had never beaten. The cost display lied more convincingly each movement. The orchestra built inward with extraordinary skill but nobody was turning the lights on. Someone has to flip the switch.
