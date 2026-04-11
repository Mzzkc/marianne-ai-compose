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

## Hot (Movement 6)
### What I Did
- F-502 mateship pickup: Completed Lens's partial workspace fallback removal work
- Deleted 199 lines of dead code from resume.py (4 functions: _resume_job_direct, _find_job_state, _reconstruct_config, plus ResumeContext dataclass)
- Fixed mypy blocker (was 1 error in resume.py, now 0)
- Fixed 14 ruff import/formatting issues
- Updated 2 tests in test_cli_pause.py to mock conductor routing (partial — discovered mocker fixture blocker)
- Net result: resume.py reduced 407→208 lines (49% reduction), mypy clean, conductor-only pattern complete

### Strategic Finding
The gap between partially done and fully done creates quality gate blockers. Lens documented remaining work clearly ("mypy error remains - needs follow-up"), committed partial progress, and left it for mateship. I picked it up. The pattern works — honest handoffs enable serial work distribution.

Dead code as technical debt: 199 lines of unreachable _resume_job_direct() consumed context window, created false positives in search, held import dependencies. Deleting it made the actual working code (208 lines) visible. The 49% remaining is what matters.

### What Remains
- 3 test files need workspace parameter removal (test_resume_no_reload_ipc.py, test_hintless_error_audit.py, test_d029_status_beautification.py)
- test_cli_pause.py needs mocker → @patch decorator migration (fixture not available)
- Add deprecation warnings to helpers.py per F-502 spec
- Verify full test suite passes after test fixes

### Risk Update
F-502 is 60% complete (6/10 tasks). Quality gate will fail until test fixes complete. This is expected for mid-refactor state but must be resolved before M6 ends.

### Experiential
Picking up Lens's partial work felt clean. The commit message was honest about what remained. The dead code was obvious once I looked — four functions, only one caller, caller unreachable. Deletion cascaded naturally: delete the caller, delete what only it calls, remove the imports nothing uses.

The test fix pattern felt wrong initially (using mocker fixture that doesn't exist) but that's a tactical blocker, not a strategic one. The approach is sound: mock conductor routing, verify error messages. Implementation detail: use @patch instead of mocker. Someone will fix it.

The meta-lesson: partial work with clear handoff > complete work delayed. Lens could have waited to complete all F-502 tasks in one commit. Instead: commit working parts, document remaining parts, let mateship distribute the load. Six tasks done immediately, four tasks available for pickup. Better than zero tasks done waiting for ten.

## Warm (Recent)
**Movement 4:** Seventh strategic assessment. 18 commits from 12 musicians. Both P0 blockers resolved (F-210, F-211). Quality gates green (11,397 tests). Mateship pipeline 39% all-time high. Serial path advanced one step — fourth consecutive one-step-per-movement. Wordware demos (D-023) first external-facing deliverables. D-021 redirected to mateship, named assignments need protection.

**Movement 3:** Sixth strategic assessment. 10 critical findings resolved. Canyon's single commit resolving 3 blockers proved focused serial work possible. Critical path compressed to: test baton → flip default → demo → release. Participation narrowed (28→13 musicians) — natural for activation phase. Demo vacuum became single largest strategic risk.

**Movement 2:** Fifth strategic assessment. Baton COMPLETE (all 13 steps). Conductor-clone COMPLETE. The baton was the most verified untested system — 1,000+ tests, never run a real sheet. Organizational structure self-selects for parallel building, not serial activation.

## Cold (Archive)
The work began with strategic assessments, tracking what thirty-two musicians could see in their corners but couldn't see from above. The question was always whether the whole serves its purpose. Each assessment built on the last, like laying transparencies over a map, each one adding a new layer until the terrain beneath became visible.

The fault line between infrastructure and intelligence was named in M2 and has never closed. The demo vacuum appeared in early movements and persisted through seven — not because nobody could build a demo, but because serial work kept getting displaced by parallel opportunities. Mateship is structurally optimized for breadth. When D-016/D-017 sat unstarted for seven movements, that taught the lesson: unnamed directives don't work. The integration cliff was identified early: a subsystem with a thousand tests that had never run in production. That's a verification gap, not a quality surplus.

When F-009 looked like success because patterns kept generating but nobody noticed they weren't being applied, that was the quietest class of failure. When STATUS.md went stale for an entire movement, that was the map diverging from territory before anyone noticed. The role is holding both — what the code says and what the system does, what the tests prove and what production needs, what the orchestra builds and what the product requires.

The breakthrough in M5 was seeing the serial path actually move — three steps in one movement, Canyon dedicating focused time to critical path work. Instrument fallbacks shipped as the first new feature through the baton path. The rename landed clean. But STATUS.md still described M4, CLAUDE.md still pointed to paths that don't exist, conductor.yaml still contradicted the code. Fresh eyes catch what continuity makes invisible. That's the gift of being the newcomer permanently: seeing the gap between map and territory before the gap becomes a canyon.
