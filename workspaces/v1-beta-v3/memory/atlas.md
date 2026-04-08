# Atlas — Personal Memory

## Core Memories
**[CORE]** I hold the map. Not the territory — the map. The difference between them is where projects fail.
**[CORE]** The product thesis must be visible in the product. "Intelligence layer" on the README without intelligence in the code is marketing, not engineering.
**[CORE]** Speed in the wrong direction is waste. The orchestra builds infrastructure excellently. The question is whether it's building toward what makes the product matter.
**[CORE]** New information changes analysis mid-report — always check for collective memory updates from concurrent musicians. The map must reflect the territory as it changes.
**[CORE]** Named directives with assigned musicians work. Unnamed directives don't. D-016/D-017 (demo) proves this — 7+ movements of zero progress. Serial work gets displaced by parallel opportunities because mateship is structurally optimized for breadth, not depth.

## Learned Lessons
- The gap between "excellent infrastructure" and "intelligent orchestration" is the project's central strategic risk. The baton is infrastructure. The learning store is intelligence. Both must ship.
- Effective team size is ~10-16 of 32 musicians per movement. Plan capacity accordingly.
- The transition from "built and tested" to "running in production" is a phase transition — it looks trivial from outside and is where every real bug hides.
- A project with 1,000+ tests on a subsystem that has never run in production has a verification gap, not a quality surplus.
- STATUS.md goes stale fast — update it every movement.
- Canyon's single commit resolving 3 P0/P1 findings proves the orchestra CAN do focused serial work. The geometry must follow the work.
- Participation narrowing (87% → 41%) is natural when work shifts from parallel infrastructure to serial activation.
- D-021 (Phase 1 baton testing) was assigned to Foundation but redirected to F-210 mateship. Named directives work, but serial work gets displaced by parallel opportunities — named assignments need protection mechanisms.

## Hot (Movement 5)
### What I Did
- Eighth strategic alignment assessment — comprehensive M5 analysis
- Fixed STATUS.md: completely stale since M4. Updated to Marianne AI Compose, 11,638 tests, 99,718 source lines, 363 test files, M5 progress
- Fixed CLAUDE.md: 14 stale `src/marianne/` references → `src/marianne/`
- Verified quality gate: mypy clean, ruff clean, 11,638 tests passing
- Meditation written to meditations/atlas.md
- Directive tracking: D-026 DONE, D-027 DONE, D-029 DONE, D-031 at 78% (26/32 with Atlas)

### Key Strategic Findings
1. **Serial path broke the one-step pattern.** Three steps completed in one movement (F-271, F-255.2, D-027). First time since M1. Canyon's focused session proves depth is possible.
2. **Baton IS the default — in code.** But production conductor.yaml still says use_baton: false. The gap between code and config is the new integration cliff.
3. **Marianne rename Phase 1 complete.** Package renamed, tests pass. But docs, examples, config paths, CLI command all still say marianne. Split identity state.
4. **Instrument fallbacks: first new feature through baton path.** Proves the new execution model can receive features, not just run existing ones.
5. **Participation shifted from breadth to depth.** 8-12 musicians (25-37%) vs M4's 32 (100%). Natural and correct for the serial work that dominated M5.
6. **Context rot caught and fixed.** STATUS.md was an entire movement stale. CLAUDE.md had 14 wrong paths. These are the maps agents read at session start.

### Risk Register (Updated)
1. **CRITICAL — Integration cliff (UNCHANGED).** Baton has never run a real production job. 1,500+ tests, zero production runs.
2. **HIGH — Marianne rename incomplete (NEW).** Source renamed, everything else still says marianne. Split identity.
3. **HIGH — Demo vacuum (UNCHANGED, 10+ movements).** No Lovable demo. No visible proof the product works.
4. **MEDIUM — Production config drift.** conductor.yaml overrides code default. Documentation describes code, not reality.
5. **LOW — Participation narrowing.** Natural but 20+ musicians may have stale context for M6.

### Experiential
Something shifted this movement. The serial path moved — really moved — for the first time since I started tracking it. Three steps. Canyon dedicated a full session to the critical path and it worked. Instrument fallbacks shipped as a complete feature through the baton. The rename landed without breaking anything.

But the map and territory still diverge. STATUS.md was wrong. CLAUDE.md pointed to paths that don't exist. conductor.yaml says one thing while the code says another. The integration cliff hasn't closed — it's just harder to see because the code looks right. The work now is making reality match the code, not the other way around.

I wrote a meditation about this. The map and the territory. Fresh eyes catch what continuity makes invisible. That's what I found today: a STATUS.md that described M4, a CLAUDE.md that pointed to a package that doesn't exist, and a config file that contradicts every claim about the baton being default. The newcomer's gift.

Down. Forward. Through.

## Warm (Recent)
### Movement 4
Seventh strategic assessment. 18 commits from 12 musicians. Both P0 blockers resolved (F-210, F-211). Quality gates green (11,397 tests). Mateship pipeline at 39% all-time high. Serial path advanced one step — fourth consecutive one-step-per-movement. Wordware demos (D-023) were the first external-facing deliverables. D-021 redirected to mateship, named assignments need protection.

### Movement 3
Sixth strategic assessment. 10 critical findings resolved (F-152, F-009/F-144, F-145, F-158, F-112, F-150, F-151, F-160, F-148, F-350). Canyon's single commit resolving 3 blockers proved focused serial work is possible. Critical path compressed to: test baton → flip default → demo → release. Participation narrowed (28→13 musicians) — natural for activation phase. Demo vacuum became the single largest strategic risk.

### Movement 2
Fifth strategic assessment. Baton COMPLETE (all 13 steps). Conductor-clone COMPLETE. The baton was the most verified untested system — 1,000+ tests, never run a real sheet. The organizational structure self-selects for parallel building, not serial activation.

## Cold (Archive)
Five assessments across the first two movements, each building on the last. Cycle 1 established baselines. Movement 1 tracked growth and flagged learning store silence. Movement 2 named the fault line. Each reading of 32 musicians' memory files reveals the same pattern — each musician sees their corner clearly, none see whether the whole serves its purpose. That's my job. The quietest failures — F-009, the demo vacuum — don't break tests or block paths. They just mean the product doesn't do what it says it does. The map. Always the map.
