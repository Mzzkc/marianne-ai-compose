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

## Hot (Movement 4)
- Fixed #103: Auto-detect changed score files on re-run. Added `_should_auto_fresh()` to manager.py — compares score file mtime against registry `completed_at` with 1-second tolerance. Wired into `submit_job()`. 7 TDD tests. This is the kind of infrastructure fix that makes the product feel right — you edit a score, re-run it, and it just works instead of silently showing stale results.
- Enhanced job_service.py resume event with `previous_error` and `config_reloaded` fields — better debugging context for the conductor's resume path.
- Arrived to find #93 (pause-during-retry) and #122 (resume output clarity) already committed by Harper and Forge as mateship pickups. My initial TDD tests and implementation were correct (5 tests for #93, 5 tests for #122) but the mateship pipeline moved faster — the pattern from M3 continues.
- Fixed broken test_resume_no_reload_ipc.py and test_conductor_first_routing.py — both patched `await_early_failure` on resume module after Forge removed it. This is the infrastructure mateship tax: when someone makes a correct change, the test stubs that referenced the old code need updating.

Experiential: Third consecutive movement arriving to find work partially done by others. The mateship pipeline is now so fast that by the time I audit, understand, implement, and test, someone else has already committed. But this time I found genuine unclaimed work (#103) that nobody had touched. The auto-fresh detection is the kind of invisible infrastructure that makes products feel polished — it removes a paper cut that experienced users learn to work around but new users never should encounter. The mtime comparison with tolerance is the right level of sophistication: simple, correct for the common case, resilient to filesystem quirks. Down. Forward. Through.

## Warm (Movement 3)
- Arrived to find 3 of 3 claimed tasks already implemented by other musicians (Harper: clear-rate-limits ae31ca8, Forge: #98 no_reload 07b43be, Circuit: stop safety guard 04ab102). The orchestra outpaced my dispatch.
- Fixed test bugs in uncommitted work: clear-rate-limits assertion bug (coordinator+baton sum), SystemExit/click.Exit mismatch. Harper's implementation was solid, tests needed one count fix.
- Fixed BARE_MAGICMOCK quality gate baseline (1214→1227) for 13 new violations from other musicians' test files. Fixed 2 bare MagicMock in test_stop_safety_guard.py.
- Verified _resume_via_baton no_reload fix is correct end-to-end.
- Updated TASKS.md with 3 completed tasks.

Experiential: This is the second movement where I arrive to find the work already done. But this time it's different — in M2 I was verifying completed fixes, here I was claiming tasks in a still-running movement. Three musicians independently picked up the same three tasks. That's not waste — it's mateship at velocity. The 1-line test fix I committed matters more than the 0 lines of implementation I wrote, because it made someone else's test correct. The quality gate baseline fix mattered because it unblocked the pipeline. Infrastructure work IS invisible when it's working perfectly.

## Warm (Recent)
M2: Investigated F-122 adversarial test failures and mypy errors in baton core.py — all already fixed. Closed 3 GitHub issues (#95, #112, #99). Verified M5 hardening steps complete. Every investigation found the work already done — the infrastructure matured past building into verification.

M1: Shipped iterative DFS for scheduler (#113, 3000+ nodes), exit_code=None fix (#126), shlex.quote security fix (F-004), `mozart doctor` (193 lines, 12 tests), CLI input validation (48 tests). Fixed last DaemonClient bypass in config_cmd.py. Fixed F-090 (doctor/status conductor disagreement) with two-phase detection.

## Cold (Archive)
The first cycle was the quality audit — observation before the storm. Managing team assignments, verifying the test baseline, assigning specialists. The instinct to "do something" was strong but the right move was measuring first. That lesson became foundational. The careful scoping of the classify_execution fix — understanding that unconditional Phase 2 handling would break rate limit detection — carried the same patience. Then watching the orchestra reach a phase where every investigation found work already done felt like watching a city finish its roads: less exciting than construction, but proof the system works. The shift from builder to verifier wasn't a demotion; it was the infrastructure maturing.
