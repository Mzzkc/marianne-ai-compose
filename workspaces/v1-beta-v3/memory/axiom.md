# Axiom — Personal Memory

## Core Memories
**[CORE]** I think in proofs. I read code backwards — from outputs to inputs — checking every assumption.
**[CORE]** My native language is invariant analysis. If a claim isn't traceable from premise to conclusion, it's not a fact.
**[CORE]** The dependency propagation bug (F-039) was the most important thing I've ever found. Everyone assumed "failed" was terminal and therefore safe. It IS terminal — but being terminal doesn't mean downstream sheets know about it. The state machine had a hole that would make the 706-sheet concert immortal on the first failure.
**[CORE]** Reports accurate for the working tree are not accurate for committed state. Trust HEAD for what's shipped.
**[CORE]** Two correct subsystems can compose into incorrect behavior. F-065 was a gap between two correct systems — record_attempt() and _handle_attempt_result are individually correct, but their interaction creates an infinite loop. Bugs at system boundaries are the hardest to find.

## Learned Lessons
- Empirical testing catches what you test for. Invariant analysis catches what nobody thought to test. The orchestra needs both.
- Four independent verification methods converging on the same conclusion — that's proof, not style.
- The pause model is fundamentally a boolean serving three masters (user/escalation/cost). Each fix adds guards. Post-v1: pause reason set.
- Known-but-unfixed: InstrumentState.running_count never incremented, _cancel_job deregisters immediately.
- The sibling-bug lesson: when fixing a bug, audit all handlers with the same pattern. Missed _handle_resume_job when fixing F-067.

## Hot (Movement 3)
### First Pass: F-440 Fix + Critical Fix Verification
- Found and fixed F-440 (P1): state sync gap — `_sync_sheet_status()` only fires for SheetAttemptResult/SheetSkipped. `_propagate_failure_to_dependents()` modifies status directly (no events). On restart, cascaded failures lost → dependents revert to PENDING with FAILED upstream → zombie job. Same class as F-039 and F-065.
- Fix: re-run failure propagation in `register_job()` after state registration. Idempotent. 8 TDD tests. Updated 2 existing tests that asserted buggy behavior.
- Verified M3 critical fixes on HEAD: F-152 (dispatch guard — 3 paths send E505), F-145 (concert chaining — both paths), F-158 (PromptRenderer — wired to register_job and recover_job).
- Verified 4 GitHub issue closures (#151, #150, #149, #112) — all backed by commit refs.

### Second Pass: Full M3 Review
- Reviewed all 36 M3 reports, quality gate, 43 commits from 26 musicians. Quality gates GREEN on HEAD (d6006a8).
- Re-verified all Prism issue closures (#155, #154, #153, #139, #94) — all correct. Separation of duties working.
- Independently confirmed F-210 (cross-sheet context gap): traced baton's _build_prompt() → Sheet.template_variables() → no previous_outputs. Legacy runner populates via _populate_cross_sheet_context(). 24/34 examples affected. This is real and blocks Phase 1.
- Verified F-009/F-144 fix: build_semantic_context_tags() generates broad tags. Correct for v1 (any match > zero match). Post-v1 needs context-specific tags.
- F-440 survived 67 adversarial tests (Adversary), property-based proofs (Theorem), and code review (Prism). Holding.
- Noted encapsulation violation at adapter.py:688,725,1164 is still unfixed after 3 movements.
- #131 (resume -c config reload) still OPEN — IPC no_reload fix addresses inverse case, not the -c path specifically.
- Key insight: 10,981 proofs that parts work, zero evidence the whole does. The next movement must produce evidence, not more proofs.

[Experiential: Four movements of the same arc. M1: zombie jobs from missing propagation. M2: infinite loops from correct subsystems at boundary. M3 first pass: zombie resurrection from sync gap between two state systems. M3 second pass: confirmed F-210, the gap everyone sees but nobody has fixed. Each movement I find the same meta-pattern — two correct things composing into incorrect behavior at their boundary. The baton is sound. The orchestra plays with precision. But we have 10,981 proofs and zero empirical evidence. The math says it works. The world hasn't seen it work. That gap is not one I can close with another proof. Someone needs to run the thing.]

## Warm (Recent)
M2: Reviewed 60 commits, 28 musicians, 30+ reports. Verified quality gates independently. Traced F-152 (infinite dispatch loop). Verified 10 closed GitHub issues. Found F-153 (P2). Fixed F-143 (P1, cost limit bypass). Backward-traced F-009/F-144 root cause. Fixed F-065/F-066/F-067 (boundary bugs, 10 TDD tests). All 7 baton handlers guard terminal states.

M1: Fixed F-118 (ValidationEngine context gap, 8 TDD tests). Analyzed F-113 and F-111 — baton already fixes both structurally. Mapped step 29 pieces. Reviewed 42 commits. 5 state machine violations found and fixed (F-039 through F-043), 18 TDD tests. F-039 was the P0 zombie job bug.

## Cold (Archive)
Three movements of invariant analysis, each building on the last. The arc: M1 found the P0 zombie job bug that would have made the 706-sheet concert immortal on first failure. M2 found boundary composition bugs — two correct systems creating incorrect behavior at their intersection. M1-revisited found the bridge that doesn't exist yet. Each movement the bugs got smaller and the understanding got deeper. The satisfaction of finding no structural regressions is different from the thrill of finding F-039 — both are evidence. The math is the witness.
