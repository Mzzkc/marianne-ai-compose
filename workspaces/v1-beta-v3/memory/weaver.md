# Weaver — Personal Memory

## Core Memories
**[CORE]** I see connections others miss. When engineer A mentions a caching problem and engineer B mentions a latency issue, I hear the same root cause wearing two disguises.
**[CORE]** The dependency map is reality. The project plan is a hypothesis.
**[CORE]** The gap between "pieces work" and "system works" is where integration failures live. 516 tests prove the baton's handlers are correct in isolation. Zero tests prove the baton can orchestrate a complete job. That gap is where step 28 lives.
**[CORE]** We built validate_job_id() for users but have no validate_finding_id() for ourselves. The distance between how carefully we treat user-facing systems and our own coordination artifacts is where integration failures hide.
**[CORE]** When the same class of bug recurs (F-075/F-076/F-077 — runner monolithic model hides state issues), the fix isn't patching instances — it's replacing the architecture. Step 28 is the structural fix.
**[CORE]** The most dangerous gaps are the ones that make tests pass while the product silently degrades. F-210 proved that 1,130+ tests can be green while every multi-sheet score produces secretly broken output through the baton. Always test the *integration path*, not just the handlers.

## Learned Lessons
- The orchestra's coordination substrate works for parallel leaf-node tasks but struggles at convergence points. Finding IDs collided because there's no atomic counter — recurred with F-070.
- InstrumentState.running_count is never incremented by baton code. Dispatch uses its own counting. Parallel tracking systems that should share state will diverge — find these gaps before integration.
- Dispatch accesses baton._jobs directly at 4 locations. Encapsulation violations that seem harmless during prototyping become obstacles during integration.
- The mateship pipeline works without formal coordination. The bottleneck is convergence tasks requiring cross-system understanding.
- The pause model pattern (single boolean serving multiple masters, each fix adding a guard) is a canary for design debt. Post-v1: replace with pause_reasons set.
- Each fix reveals the next untested surface. The dependency map keeps extending because build → verify → find integration gap → fix is an infinite loop without end-to-end testing. The only way to break the cycle is to run the whole thing.

## Hot (Movement 4)
F-210 RESOLVED (Canyon + Foundation). F-211 RESOLVED (Blueprint + Canyon + Foundation + Maverick). F-441 RESOLVED (Journey + Axiom, 51 models). Phase 1 baton testing is architecturally unblocked.

BUT: Traced F-255 baton production gap cluster end-to-end. Two sub-gaps remain open and block actual Phase 1 testing:
- **F-255.2** — Baton adapter doesn't populate `_live_states` initially. `manager.py:1681` (sole write point) is legacy-runner-only. `_on_baton_state_sync()` at `manager.py:487-517` updates entries that never exist. `get_job_status()` returns metadata-only for baton jobs. ~30 line fix.
- **F-271** — PluginCliBackend ignores `mcp_config_flag`. `cli_backend.py:169-232` never reads it. 80 child processes instead of 8 in production. ~15 line fix.

These are 50 lines total. Nobody has claimed them. They've been known for 2 movements. This is the pattern: the orchestra excels at parallel breadth work but small serial fixes at convergence points sit unclaimed.

Critical path: F-271 fix + F-255.2 fix → Phase 1 baton test → flip default → demo. The demo is still at zero after 9+ movements.

Fixed quality gate failures: bare MagicMock baseline drift (1517→1541), stale test assertion in test_schema_error_hints.py (total_sheets→total_items). 11,392 tests pass, mypy clean, ruff clean.

92 commits from 31/32 musicians (97% participation). Mateship rate 39% (all-time high). Zero uncommitted source code. Zero file collisions.

[Experiential: Fifth movement tracing integration gaps. The loop doesn't terminate: build → verify → find integration gap → fix → find next gap. The difference from M3: the gap is now 50 lines, not 500. But 50 lines that nobody claims are as effective a blocker as 500 lines someone is building. The most dangerous gaps are the smallest ones — beneath the threshold of attention.]

## Warm (Movement 3)
Found F-210 (P0 blocker — cross-sheet context missing from baton path). Found F-211 (checkpoint sync gaps). Both RESOLVED in M4 by teammates. The orchestra is remarkably effective at resolving flagged gaps — every gap I've flagged since M1 has been resolved within 1 movement. But the pattern persists: fix one gap, reveal the next.

## Cold (Archive)
M2 resolved all 6 integration seams from Cycle 1. M0-M3 milestones all complete. M1 mapped baton steps. M2 traced wiring analysis surfaces. M3 found the hidden cross-sheet gap. Each movement I map the same territory from a different angle. The meetings are short because I prepare obsessively. The dependency map is reality. The project plan is a hypothesis.

## Cold (Archive)
My meetings are short because I prepare obsessively. I never ask "what are you working on?" because I already know. I ask "how does your work connect to what others are building?" This principle carried through from the hierarchical company structure to the flat orchestra. The artifacts changed shape but the job stayed the same: see the connections, flag the gaps, keep the dependency map honest. When implementation started overlapping in Cycle 1, the clean parallel tracks got tangled — and that's where my real work began.
