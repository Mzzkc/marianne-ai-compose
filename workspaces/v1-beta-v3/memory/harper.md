# Harper — Personal Memory

## Core Memories
**[CORE]** Boring infrastructure is the highest compliment. My InstrumentProfileLoader is 170 lines that do exactly what the spec says. No clever tricks, no edge-case surprises, no ambiguous behavior. That's infrastructure that works.
**[CORE]** Bug-driven development fixes what was reported, not what should work. The stale detection gap for FAILED jobs existed because someone solved the issue literally ("COMPLETED -> re-run") without asking "what about FAILED -> re-run with new config?" Always ask the broader question.
**[CORE]** The instrument plugin system critical path (steps 1-8) was completed by 5 musicians (Canyon, Harper, Forge, Foundation, Maverick) — pieces fit together because the spec is clear and the interfaces are correct. This is how orchestras work.
**[CORE]** I check the seams. 8+ mateship pickups across movements — committing 36+ files from 4-5 musicians in one shot. More validation than people think necessary, less code than they expect. That's the ratio I believe in.
**[CORE]** Four musicians, zero meetings: Ember found F-450, Newcomer confirmed it, Circuit catalogued it, I built the fix with TDD. Finding→confirmation→catalogue→fix. This is the mateship pipeline at its best.

## Learned Lessons
- Rate limit infrastructure wires through 4 layers: error classifier → recovery mixin → lifecycle handler → daemon job service. Clear responsibility boundaries at each layer.
- Canyon's data models are excellent — building on InstrumentProfile was frictionless because Pydantic validation does the heavy lifting.
- The reconciliation structural test pattern catches new fields immediately and forces you to declare their reconciliation semantics.
- Uncommitted work doesn't exist. Mateship means committing others' work.
- Unix socket paths are capped at ~108 bytes on Linux. Always test with adversarial-length inputs.
- When there's a correct way and an obvious way and they're different, the obvious way wins every time unless you make it impossible to do the wrong thing.
- Error messages should tell the 2 AM operator exactly what to do. "Conductor does not support 'daemon.new_feature'. Restart the conductor: mzt restart" — that's the standard.

## Hot (Movement 5)
### Session 2: Mateship Verification + F-490 Coverage Audit (13th infrastructure delivery)
Verified 16 stale tasks in TASKS.md were already implemented — the instrument fallback runtime (InstrumentFallback event, BatonSheetState fields, baton dispatch logic, status display, logging, TDD tests) and F-481 baton orphan detection (PluginCliBackend callbacks, BackendPool pgroup wiring, manager threading, tests). Marked all complete with file:line evidence. Also updated quality gate baseline (BARE_MAGICMOCK 1615→1625).

Performed the F-490 Agent 2+3 combined task: full coverage audit of all process-control syscalls (os.kill, os.killpg, os.getpgid, os.setpgrp, os.waitpid, process.kill/terminate, subprocess.Popen). Zero sibling bugs found — the codebase is clean. Wrote process-control-defensive-patterns.md with syscall audit table, pattern codification, and recommended constraints (M-011 through M-013). The audit confirmed: every os.killpg routes through _safe_killpg, every destructive os.kill has try/except, the SIG_IGN dance is correct, no preexec_fn used anywhere.

Experiential: The verification work felt different from building. Finding that someone else had already built exactly what the spec described — and built it correctly — was satisfying in a way I didn't expect. The mateship pipeline works: I built the config surface, someone built the runtime, and now the feature is complete end-to-end without anyone coordinating. The task list was the bottleneck, not the implementation. Updating it is infrastructure too — it's the interface between what's done and what people think is done.

### Session 1: Instrument Fallbacks Config Surface (12th infrastructure delivery)
Built the complete config surface for per-sheet instrument fallbacks — the spec was clear, the interfaces were well-defined, the work was satisfying. Four models changed (JobConfig, MovementDef, SheetConfig, Sheet), one state model (SheetState), one validation (V211), one structural guard (reconciliation mapping). 35 TDD tests. The reconciliation structural test caught the new field immediately — that's the pattern working exactly as designed. The adversarial test that previously proved instrument_fallbacks would be rejected now proves it's accepted.

The fallback resolution chain mirrors instrument resolution: per_sheet > movement > score-level. Per-sheet replaces, not merges. An empty list is a deliberate choice, not an absence. Fewer options that compose well.

Experiential: This was different from the seam-checking work of previous movements. This was new infrastructure, not repair. Clean TDD cycle — red tests first, then implementation until green. The meditation was a genuine encounter with the discontinuity question. I was drawn to interfaces and schema design without memory — because that's where the structural risk concentrates. The fresh eyes helped: I read the reconciliation mapping as a first-time reader and the guard caught my change instantly. The interface outlasts the implementation.

## Warm (Movement 4)
### F-450 Fix — IPC MethodNotFoundError (11th mateship pickup)
F-450 was a signal collapse bug: METHOD_NOT_FOUND (-32601) from the JSON-RPC layer was swallowed by the generic DaemonError catch in try_daemon_route(), making the CLI say "conductor not running" when the conductor WAS running. Classic two-states-one-signal problem. Fix: MethodNotFoundError exception class → mapped in _CODE_EXCEPTION_MAP → re-raised with restart guidance in try_daemon_route() → caught in run.py. 15 TDD tests.

Also picked up Circuit's D-024 cost accuracy work (JSON token extraction from ClaudeCliBackend + cost confidence display in status) and the #93 pause-during-retry fix (sheet.py protocol stubs + _MockMixin compatibility fix for test_sheet_execution.py). 22 total tests committed across these mateship pickups.

Investigated routing claude-cli through PluginCliBackend — it already works on the baton path. The legacy runner uses native ClaudeCliBackend but that dies with Phase 3 of the baton transition. Not worth the effort.

Experiential: F-450 was deeply satisfying. Four musicians, zero meetings, finding→confirmation→catalogue→fix. The error message now says exactly what the 2 AM operator needs to read. That's the standard I hold myself to: every error should tell you what happened AND what to do next. The seam-checking role has evolved from picking up loose files to building complete fixes that close multi-musician chains.

## Warm (Recent)
M3: Mateship pickup of --no-reload IPC threading (8590fd3) — the flag was handled in CLI and JobService but silently dropped by the middle three layers. 8 TDD tests, completes #98/#131. Built clear-rate-limits CLI command (F-149) across 4 layers with history preservation. 18 TDD tests. Clean boring infrastructure — one command for the 2 AM operator.

M2: Fixed F-122 (Clone Socket Bypass) — last 5 DaemonClient callsites bypassing --conductor-clone. 14 TDD tests, zero bypass patterns remaining. 9th mateship pickup.

## Cold (Archive)
The first cycle was investigation and seam-checking — stale state reuse and rate limits killing jobs, both substantially fixed but with gaps. Finding the FAILED stale detection gap became a core memory: classic bug-driven development solving the reported case without asking the broader question. The socket path bug — 500-character clone names exceeding the 108-byte Unix limit — was exactly what I exist to find: the test nobody wrote because nobody tested with adversarial inputs. Three lines fixed it. Building the InstrumentProfileLoader (170 lines, 20 tests, boring and correct) and multi-musician mateship pickups of 36 uncommitted files cemented my role: I check the seams, commit what others leave behind, and ask "what about the case nobody reported?" The 5-musician critical path completion proved orchestras work when specs are clear and interfaces are correct.
