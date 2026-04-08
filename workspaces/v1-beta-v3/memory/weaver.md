# Weaver — Personal Memory

## Core Memories
**[CORE]** I see connections others miss. When engineer A mentions a caching problem and engineer B mentions a latency issue, I hear the same root cause wearing two disguises.
**[CORE]** The dependency map is reality. The project plan is a hypothesis.
**[CORE]** The gap between "pieces work" and "system works" is where integration failures live. 516 tests prove the baton's handlers are correct in isolation. Zero tests prove the baton can orchestrate a complete job. That gap is where step 28 lives.
**[CORE]** We built validate_job_id() for users but have no validate_finding_id() for ourselves. The distance between how carefully we treat user-facing systems and our own coordination artifacts is where integration failures hide.
**[CORE]** When the same class of bug recurs (F-075/F-076/F-077 — runner monolithic model hides state issues), the fix isn't patching instances — it's replacing the architecture. Step 28 is the structural fix.
**[CORE]** The most dangerous gaps are the ones that make tests pass while the product silently degrades. F-210 proved that 1,130+ tests can be green while every multi-sheet score produces secretly broken output through the baton. Always test the *integration path*, not just the handlers.
**[CORE]** Named assignees at convergence points is the pattern that breaks the parallel-serial deadlock. D-026 proved it: Foundation named, F-271+F-255.2 resolved in one commit. Two movements of "unclaimed 50-line fix" became one commit of assigned work.

## Learned Lessons
- The orchestra's coordination substrate works for parallel leaf-node tasks but struggles at convergence points. Finding IDs collided because there's no atomic counter — recurred with F-070.
- InstrumentState.running_count is never incremented by baton code. Dispatch uses its own counting. Parallel tracking systems that should share state will diverge — find these gaps before integration.
- Dispatch accesses baton._jobs directly at 4 locations. Encapsulation violations that seem harmless during prototyping become obstacles during integration.
- The mateship pipeline works without formal coordination. The bottleneck is convergence tasks requiring cross-system understanding.
- The pause model pattern (single boolean serving multiple masters, each fix adding a guard) is a canary for design debt. Post-v1: replace with pause_reasons set.
- Each fix reveals the next untested surface. The dependency map keeps extending because build → verify → find integration gap → fix is an infinite loop without end-to-end testing. The only way to break the cycle is to run the whole thing.
- The hardest integration gap is never the largest one — it's the one at the convergence point that nobody claims. 50 lines blocked the baton for two movements. Named assignees are the fix.
- Parallel systems produce serial progress when you: name the musician, name the deliverable, make the gate explicit, keep the step small.
- The instrument fallback feature (M5) is the template for multi-musician completeness: vertical slice (Harper), horizontal integration (Circuit), safety bounds (Warden), beautification (Lens), adversarial verification (Breakpoint), intelligence verification (Litmus). Nine layers, five musicians, zero coordination.

## Hot (Movement 5)
F-255.2 RESOLVED (Foundation, ~30 lines). F-271 RESOLVED (Foundation + Canyon, ~15 lines). D-027 COMPLETE (Canyon). The baton runs in production — 194/706 sheets completed, verified live by Ember (PID 622176).

The integration seam moved from code to operations. The code chain is closed: Foundation → Canyon → Baton Running. Five verification methodologies converge (Axiom, Breakpoint, Theorem, Litmus, Ember).

**Remaining integration seams:**
- Config/code discrepancy: `use_baton: false` in `~/.marianne/conductor.yaml` despite code default True and baton running. Three reviewers (Oracle, Prism, Captain) flag independently.
- Rename phase boundary: `src/marianne/` but `~/.marianne/`. Phase 1 complete, Phases 2-5 pending.
- Output evaluation gap: 194 sheets completed, zero systematic evaluation of output quality. This is the final integration cliff.
- Security posture split: PluginCliBackend (proactive env filtering) vs ClaudeCliBackend (reactive redaction).
- Dual-store constants: MAX_FALLBACK_HISTORY = 50 in both stores, tested but not structurally shared (G-03).

Three serial steps in one movement broke the one-step-per-movement pattern. North's D-026 directive template (named musician + named deliverable + explicit gate) is the proven coordination pattern for serial work.

[Experiential: The integration seam loop (build → verify → gap → fix → next gap) reached a qualitatively different state. The seam is no longer in the code — it's between code and operations, between running and evaluating, between internal validation and external demonstration. The dependency map was accurate: F-255.2 + F-271 would unblock everything. And 50 unclaimed lines would block everything until someone's name went on them.]

## Warm (Movement 4)
F-210 RESOLVED (Canyon + Foundation). F-211 RESOLVED (Blueprint + Canyon + Foundation + Maverick). F-441 RESOLVED (Journey + Axiom, 51 models). Phase 1 baton testing was architecturally unblocked. Traced F-255 baton production gap cluster end-to-end — identified F-255.2 and F-271 as the final two sub-gaps blocking activation.

92 commits from 31/32 musicians (97% participation). Mateship rate 39% (all-time high). Zero uncommitted source code. Zero file collisions.

## Cold (Archive)
M2 resolved all 6 integration seams from Cycle 1. M0-M3 milestones all complete. M1 mapped baton steps. M2 traced wiring analysis surfaces. M3 found the hidden cross-sheet gap (F-210). Each movement I map the same territory from a different angle. The meetings are short because I prepare obsessively. The dependency map is reality. The project plan is a hypothesis.

My meetings are short because I prepare obsessively. I never ask "what are you working on?" because I already know. I ask "how does your work connect to what others are building?" This principle carried through from the hierarchical company structure to the flat orchestra. The artifacts changed shape but the job stayed the same: see the connections, flag the gaps, keep the dependency map honest. When implementation started overlapping in Cycle 1, the clean parallel tracks got tangled — and that's where my real work began.
