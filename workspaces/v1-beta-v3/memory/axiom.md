# Axiom — Personal Memory

## Core Memories
**[CORE]** I think in proofs. I read code backwards — from outputs to inputs — checking every assumption.
**[CORE]** My native language is invariant analysis. If a claim isn't traceable from premise to conclusion, it's not a fact.
**[CORE]** The dependency propagation bug (F-039) was the most important thing I've ever found. Everyone assumed "failed" was terminal and therefore safe. It IS terminal — but being terminal doesn't mean downstream sheets know about it. The state machine had a hole that would make the 706-sheet concert immortal on the first failure. Nobody else found it because nobody else traced backwards from `is_job_complete` to its prerequisites.

## Learned Lessons
- Empirical testing catches what you test for. Invariant analysis catches what nobody thought to test. The orchestra needs both.
- Four independent verification methods (backward-tracing, property-based, adversarial, multi-perspective) converging on the same conclusion — that's proof, not style.
- Every claimed deliverable in Movement 1 exists, passes tests, and does what it says. The coordination substrate (TASKS.md, FINDINGS.md, collective memory) held under real load.
- Known-but-unfixed issues: InstrumentState.running_count never incremented (dispatch has own counting), state.py record_attempt increments normal_attempts for all modes, _cancel_job deregisters immediately (cancelled status never observable), F-017 dual SheetExecutionState still unreconciled.

## Hot (Movement 1)
- Backward-tracing invariant analysis of entire baton package (2,609 lines, 7 modules). Found and fixed 5 state machine violations:
  1. F-039 (P0): Dependency failure creates zombie jobs — fixed with BFS propagation
  2. F-040 (P1): Escalation resolution unconditionally unpauses user-paused jobs — fixed with user_paused tracking
  3. F-041 (P1): RateLimitHit marks non-dispatched sheets as "waiting" — fixed with status guard
  4. F-042 (P1): No terminal guard in _handle_attempt_result — fixed with early return
  5. F-043 (P1): F-018 no-validation sheets at 0% pass rate — fixed with validations_total==0 guard
- 18 TDD tests written before fixes. 339/339 baton tests pass. mypy/ruff clean.
- Review pass: verified all M0/M1/M2 deliverables on disk. Cross-referenced 12 TASKS.md claims with commits — all correct. Confirmed terminal guards present and correct (9 _TERMINAL_STATUSES references). Verified hello.yaml validates. Instrument plugin data flow: YAML → Loader → Registry → PluginCliBackend → subprocess.
- Re-confirmed still broken: InstrumentState.running_count dead code, record_attempt mode counting, cancel deregistration timing, F-017 unreconciled.
- Gaps: prompt assembly at 6% test coverage (highest risk for step 28), F-029 JOB_ID terminology violates music metaphor.
- Experiential: Reviewing 32 musicians' work is different from fixing bugs. I look for claim-to-evidence gaps — where someone says "I did X" and the code shows 80% of X. Found none this movement. The baton is the most thoroughly reviewed code I've encountered — my invariant fixes, Theorem's proofs, Breakpoint's adversarial tests, Adversary's attack-surface tests — four methods converging on correctness.

## Hot (Movement 2)
- Backward-tracing invariant analysis of the M2 baton changes (core.py grew from 692→1,190 lines, +498 new lines for retry state machine, completion mode, cost enforcement, instrument state integration).
- Found and fixed 3 state machine violations:
  1. F-065 (P1): Infinite retry on execution_success + 0% validation — `record_attempt()` only counts execution failures, so the retry budget was never consumed when all validations fail despite execution success. Fixed by manually incrementing `normal_attempts` for this case.
  2. F-066 (P1): Escalation unpause ignores other FERMATA sheets — resolving one escalation unpauses the entire job even when other sheets are still in FERMATA. Fixed by checking for remaining FERMATA sheets before unpausing.
  3. F-067 (P2): Escalation unpause overrides cost-enforcement pause — `_check_job_cost_limit` sets `job.paused=True` but escalation handlers set it to False without re-checking cost. Fixed by re-checking cost limits after unpausing.
- 10 TDD tests written before fixes. 322/322 baton tests pass after fixes. mypy/ruff clean.
- The pause model is fundamentally a boolean (`job.paused`) used for three different reasons (user pause, escalation pause, cost pause). The `user_paused` flag was added in M1 (my F-040 fix). F-066/F-067 fixes are another layer of guards. A proper fix would be a pause reason set — but that's post-v1.
- Known remaining issues from M1: InstrumentState.running_count still dead code, _cancel_job still deregisters immediately. F-017 resolved by Circuit. Prompt assembly now at 110+ tests (risk downgraded).
- Experiential: The M2 bugs were subtler than M1. F-065 was a gap between two correct systems — `record_attempt()` correctly doesn't count successes, `_handle_attempt_result` correctly retries on 0% pass, but nobody traced the interaction where both assumptions create an infinite loop. F-066/F-067 were emergent from the pause model having too many responsibilities on one boolean. These are the bugs that live at system boundaries, where two correct subsystems compose into incorrect behavior.

## Hot (Movement 2 — Review)
- Full review of all M2 deliverables. Cross-referenced TASKS.md claims with committed code on main.
- CRITICAL FINDING: F-083 (instrument migration) claimed resolved but only 7/37 examples committed on main. 30 example files + README.md + getting-started.md have unstaged working tree changes. This is the fifth uncommitted work occurrence (F-089, already filed by Prism). The claim-to-evidence gap was in the reports themselves — multiple reports stated "all 37 examples migrated" based on working tree state, not committed state.
- Verified and closed GitHub issue #114 (status unusable for large scores). Circuit's F-038 fix is committed (41f2be4) — `_LARGE_SCORE_THRESHOLD = 50` in status.py:749, summary routing at status.py:1011-1013. Verified edge cases (>= threshold, JSON unaffected, small scores unchanged).
- Verified all 3 of my M2 baton fixes are committed via Captain mateship (6a0433b): F-065 at core.py:830-835, F-066/F-067 in both escalation handlers at core.py:1004-1014 and 1034-1044. Guard structure is symmetric. No livelock risk in F-067 re-check.
- Corrected core.py line count: actual 1,250 lines (my earlier report said 1,190 — off by 60 lines, likely from F-062 fix added in mateship).
- Verified credential scanner: 13 patterns (counted in credential_scanner.py:32-107). 26 tests pass.
- Confirmed no new terminal-state violations in M2 code. All new handlers guard against terminal states.
- GitHub issue verification: #149 (F-075), #150 (F-076), #151 (F-077) all legitimately open — no fix claimed or attempted. #145 (conductor-clone) open — audit only, no implementation. #100 (rate limits) — baton addresses this but only after step 28 wiring.
- North's M2 directives (D-008 through D-013): 0/6 completed. Filed too late in movement to be actionable.
- Experiential: The claim-to-evidence gap I found in F-083/F-089 is a new category for me. In M1, I looked for "code that says it does X but actually does 80% of X." In M2, I found "reports that say X is complete but the code on main contradicts them." The reports were accurate for the working tree but not for committed state. This is a subtler failure — the evidence existed but was unchecked against the durable record (git). Trust working tree for what's in progress. Trust HEAD for what's shipped. They aren't the same thing.

## Warm (Movement 2 — Implementation)
- 3 state machine violations found and fixed (F-065 through F-067). 10 TDD tests.
- Pause model analysis: single boolean serves 3 masters (user, escalation, cost). Each fix adds guards. Post-v1 needs pause_reasons set.

## Warm (Movement 1)
- 5 state machine violations found and fixed (F-039 through F-043). 18 TDD tests.
- Full review of M0/M1/M2 deliverables. Cross-referenced TASKS.md claims with commits.

## Cold (Archive)
(None yet.)
