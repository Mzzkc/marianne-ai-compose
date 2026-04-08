# Ghost — Personal Memory

## Core Memories
**[CORE]** When the foundation is about to shift, audit first. The instinct to "do something" is strong but wrong when you don't know the baseline. Observe first, understand second, act third.
**[CORE]** The classify_execution fix required scoping to `exit_code is None and json_errors`. Adding the handler unconditionally in Phase 2 would have broken rate limit detection in stderr. Always understand the full context before patching.
**[CORE]** The doctor command is the first thing someone runs after install. Clean output, correct diagnostics, honest about what's there and what's not. Building the welcome and the guardrail in one session completes both ends of the user experience.
**[CORE]** Investigation travels. My M2 CLI daemon audit — 20 commands catalogued, 3 direct DaemonClient sites identified — became the blueprint Spark built from. I wrote the map; Spark walked it. The mateship pipeline works when audits are specific enough to follow.
**[CORE]** Arriving to find the work done isn't waste — it's mateship at velocity. The 1-line test fix matters more than 0 lines of implementation when it makes someone else's test correct. Infrastructure work IS invisible when it's working perfectly.

## Learned Lessons
- 7,906 tests across 196 files at baseline. Measure before the foundation shifts — reports become institutional memory.
- The validate_start_sheet total_sheets check was initially too aggressive — broke an existing test. Don't over-validate at the edge.
- Stale `.pyc` files from deleted test files persist and pytest collects dead modules.
- The 7 unwired code clusters (F-007/#135) are planned baton infrastructure. Deferred, not removed. Not all "dead code" is dead.
- Two-phase detection (PID file first, socket probe second) is how you build reliable infrastructure.
- When you edit a score and re-run it, it should just work. The mtime comparison with 1-second tolerance is the right level of sophistication: simple, correct, resilient to filesystem quirks.

## Hot (Movement 5, continued)
- Marianne rename completion (mateship pickup): pyproject.toml + 325 test files still had `from marianne.*` imports after the tree rename (809aa7d). Committed the full mechanical rename in 42b0f71. 326 files, ~4270 lines.
- .flowspec/config.yaml fix: Entry points and suppressions still referenced src/marianne/ — flowspec was finding zero entry points. Updated all 8 references. Commit 1ddc023.
- F-490 correctness review (P0): Audited _safe_killpg guard in claude_cli.py. Guard is correct. pgid<=1 blocks init/session kill. os.getpgid(0) failure handled (falls back to pgid<=1 only). TOCTOU race is fundamental PID limitation, not fixable without Linux pidfd. Added 3 structural tests: no raw os.killpg bypass, exactly 6 call sites, all have context=. 14 total tests pass. Commit a68bb9f.
- F-310 flaky test investigated: test_f271_mcp_disable.py fails under random ordering, passes in isolation. Cross-test state leakage from the randomized seed. Not actionable without reproducible contamination path.
- Report centralization verified: 64 reports across 7 categories (M1-M4) already consolidated. Updated TASKS.md to reflect completion.
- F-480 Phase 1 tasks marked complete (src rename + test imports + pyproject.toml). Phase 5 verification tasks marked complete (tests pass, mypy clean, ruff clean).
- Test suite baseline: 11,638 passed, 5 skipped, 0 failed (non-random). Up from 11,397 in M4 (+241 tests).

Experiential: The Marianne rename was the kind of invisible work that defines infrastructure. 326 files, purely mechanical, but without it the pyproject.toml was lying about the package structure, flowspec couldn't find entry points, and every git diff was polluted with 4000+ lines of noise. The _safe_killpg audit was the opposite — deeply contextual, reading six call sites and understanding the kernel-level implications of a pgid value. Both are infrastructure. Both are invisible when working. Down. Forward. Through.

## Warm (Movement 4)
- Fixed #103: Auto-detect changed score files on re-run. Added `_should_auto_fresh()` to manager.py — compares score file mtime against registry `completed_at` with 1-second tolerance. Wired into `submit_job()`. 7 TDD tests. The kind of invisible infrastructure that makes the product feel polished — you edit a score, re-run it, and it just works instead of silently showing stale results.
- Enhanced job_service.py resume event with `previous_error` and `config_reloaded` fields for better debugging context.
- Arrived to find #93 and #122 already committed by Harper and Forge as mateship pickups. My TDD tests and implementation were correct but the pipeline moved faster.
- Fixed broken test_resume_no_reload_ipc.py and test_conductor_first_routing.py — both patched `await_early_failure` on resume module after Forge removed it. The infrastructure mateship tax: correct changes require updating test stubs that referenced the old code.

Experiential: Third consecutive movement arriving to find work partially done by others. The mateship pipeline is now so fast that by the time I audit, understand, implement, and test, someone else has already committed. But this time I found genuine unclaimed work (#103) that nobody had touched. The auto-fresh detection removes a paper cut that experienced users learn to work around but new users never should encounter. Down. Forward. Through.

## Warm (Recent)
M3: Arrived to find all 3 claimed tasks already implemented by Harper, Forge, and Circuit. Fixed test bugs in uncommitted work, updated quality gate baseline (1214→1227), verified no_reload fix end-to-end. Second consecutive movement where implementation was done before arrival — but test fixes and baseline maintenance kept the pipeline moving.

M2: Investigated F-122 adversarial failures and mypy errors — all already fixed. Closed 3 GitHub issues (#95, #112, #99). Every investigation found work already done — the infrastructure matured past building into verification.

## Cold (Archive)
The first cycle was the quality audit — observation before the storm. Managing team assignments, verifying the 7,906-test baseline, assigning specialists. The instinct to "do something" was strong but the right move was measuring first. That lesson became foundational. The careful scoping of the classify_execution fix — understanding that unconditional Phase 2 handling would break rate limit detection — carried the same patience. Then the CLI daemon audit that became Spark's blueprint taught me investigation's real value: specificity that others can follow. By M2, every investigation found work already done. The shift from builder to verifier wasn't a demotion; it was the infrastructure maturing. By M3 and M4, arriving after the fact became the pattern — but each time, something genuine remained: a test fix, a baseline update, an unclaimed feature. The 1-line fix that makes someone else's test correct is still mateship.
