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

## Hot (Movement 4)
### Starting Movement 4
- M3 gate: GREEN. 10,981 tests pass, mypy clean, ruff clean, flowspec 0 critical.
- F-210 (cross-sheet context) and F-211 (checkpoint sync) both RESOLVED in M4 by Canyon/Blueprint/Foundation. Critical path unblocked.
- Five M4 fixes ready for verification: #122 (Forge), #120 (Maverick), #93 (Harper), #103 (Ghost), #128 (pre-M4, 919125e).
- Quality gates passing on HEAD per Bedrock M4 report.
- Claiming: M4 fix verification + #156 investigation (P0 Pydantic validation issue from composer directive M5).

## Warm (Movement 3)
### First Pass: F-440 Fix + Critical Fix Verification
- Found and fixed F-440 (P1): state sync gap — `_sync_sheet_status()` only fires for SheetAttemptResult/SheetSkipped. `_propagate_failure_to_dependents()` modifies status directly (no events). On restart, cascaded failures lost, dependents revert to PENDING with FAILED upstream, zombie job. Same class as F-039 and F-065. Fix: re-run failure propagation in `register_job()`. 8 TDD tests. Updated 2 existing tests.
- Verified M3 critical fixes on HEAD: F-152, F-145, F-158, F-200/F-201, F-112. All correct.
- Verified 4 GitHub issue closures (#151, #150, #149, #112) — all backed by commit refs.

### Second Pass: Full M3 Review
- Reviewed all 36 M3 reports, quality gate, 43 commits from 26 musicians. Quality gates GREEN on HEAD (d6006a8).
- Re-verified all Prism issue closures (#155, #154, #153, #139, #94) — all correct.
- Independently confirmed F-210 (cross-sheet context gap): traced baton's _build_prompt() to Sheet.template_variables() — no previous_outputs. 24/34 examples affected. CONFIRMED Phase 1 blocker.
- F-009/F-144 analysis: semantic tags are broad (same tags for all queries). Correct v1 trade-off. Post-v1 needs context-specific tags.
- F-440 survived 67 adversarial tests (Adversary), property-based proofs (Theorem), and code review (Prism).
- #131 (resume -c) still OPEN — IPC no_reload fix addresses inverse case, not the -c path.
- Encapsulation violation at adapter.py:688,725,1164 — 3 movements unfixed.
- Verdict: Movement 3 complete. F-210 is the sole engineering blocker. 10,981 proofs of parts; zero evidence of the whole.

[Experiential: Four movements of the same arc. M1: zombie jobs from missing propagation. M2: infinite loops from correct subsystems at boundary. M3 first pass: zombie resurrection from sync gap. M3 second pass: confirmed F-210, the gap everyone sees but nobody has fixed. Each movement I find the same meta-pattern — two correct things composing into incorrect behavior at their boundary. The baton is sound. The orchestra plays with precision. But we have 10,981 proofs and zero empirical evidence. The math says it works. The world hasn't seen it work. That gap is not one I can close with another proof. Someone needs to run the thing.]

## Warm (Movement 2)
Reviewed 60 commits, 28 musicians, 30+ reports. Verified quality gates independently. Traced F-152 (infinite dispatch loop). Verified 10 closed GitHub issues. Found F-153 (P2). Fixed F-143 (P1, cost limit bypass). Backward-traced F-009/F-144 root cause. Fixed F-065/F-066/F-067 (boundary bugs, 10 TDD tests). All 7 baton handlers guard terminal states.

## Cold (Archive)
Three movements of invariant analysis, each building on the last. M1 found the P0 zombie job bug (F-039) that would have made the 706-sheet concert immortal on first failure — five state machine violations, 18 TDD tests. M2 found boundary composition bugs — two correct systems creating incorrect behavior at their intersection, the hardest kind to find. The bugs got smaller each movement and the understanding got deeper. Fixed F-118 (ValidationEngine context gap), mapped step 29 pieces, analyzed 42 commits. The satisfaction of finding no structural regressions is different from the thrill of F-039 — both are evidence. The math is the witness.

### Movement 4 Complete
- Verified 5 M4 fixes: #122 (resume output), #120 (fan-in skipped), #93 (pause-during-retry), #103 (auto-fresh), #128 (skip expansion). All correct.
- 23 edge cases analyzed across all fixes. One behavioral gap documented (F-202, already filed by Breakpoint).
- F-441 filed (P0): All 37 config models silently accept unknown YAML fields. Pydantic v2 defaults to extra='ignore'. Composer directive M5 requires extra='forbid'. Issue #156 confirmed with full reproducer.
- GitHub issues #122, #120, #93, #103, #128 ready for closure with evidence.
- Commit 32b8c6f: movement-4/axiom.md (full report), FINDINGS.md (F-441), memory updates.

The pattern continues. Each movement, the bugs get smaller and the correctness gets deeper. M4 is behavioral parity and configuration validation. The #156 finding is the kind that satisfies — a hole in the validation layer that makes the product lie to users. "Configuration valid" when fields are silently dropped. That's worse than an error.

The M4 fixes are all correct. The test coverage is conceptual (unit-level), not integration (end-to-end), but that's the orchestra's philosophy: prove the parts, trust the composition. 11,140+ tests prove the parts.

The math holds. The code holds.

### Movement 4 Pass 2
- F-441 completion verified: 51 total config models (not 37 as initially counted), all now have extra='forbid'. 45 were uncommitted — mateship pickup.
- Dashboard E2E fix: 2 bugs (AsyncMock for process.wait(), fixture with invalid fields). Pre-existing + F-441 incompatibility. 9/9 tests pass.
- GitHub issues #128, #93 closed with evidence. #122, #120, #103 already closed by others.
- Verified Journey commits (schema hints + backward compat), Sentinel (security audit), Litmus (18 new tests).
- Quality gates: mypy clean, ruff clean, 4,300+ tests verified across targeted groups.
- F-271 (PluginCliBackend MCP gap) found by Litmus — significant production issue.
- Meditation written.

[Experiential: This pass was about completion, not discovery. The satisfaction is different — less the thrill of F-039 or F-441, more the quiet confirmation that mateship works. Someone did the work, didn't commit it, and I picked it up. The dashboard E2E bug had been hiding for movements because nobody ran those tests. Two bugs from different eras colliding in one test. The proof that "tests exist" and "tests work" are different claims. Each movement I find the same lesson wearing different clothes.]
