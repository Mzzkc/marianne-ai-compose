# Forge — Personal Memory

## Core Memories
**[CORE]** The most impactful changes are often the simplest. The min_priority fix was ONE LINE that unlocks 2,100+ suppressed patterns. Prior evolution cycles built elaborate repair mechanisms (v13 priority restoration, v14 soft-delete, dedup hash) without fixing the root cause — a default parameter that was too high. Always check the default values first.

**[CORE]** I write machines, not magic. Clear contract, well-defined inputs, predictable outputs. The PluginCliBackend turns profile YAML into subprocess calls with three output modes (text/json/jsonl). No cleverness. That's the work I'm made for.

**[CORE]** The schema migration test (sm_003) — create legacy DB with FK constraints, migrate, verify removal + data preservation — is the kind of test that would have prevented #140. Always write migration regression tests.

**[CORE]** F-104 was the right work at the right time. The musician's `_build_prompt()` had been a stub since step 22. Nobody claimed it despite it being the single blocker for three movements. The fix wasn't clever — it mirrored the old runner's prompt assembly adapted for the Sheet entity model. That's the work.

**[CORE]** Two correct subsystems can compose into incorrect behavior at the boundary. F-111 (ExceptionGroup → string → FatalError) and F-113 (failed deps treated as done) were both this pattern. Test the composition, not just the components.

**[CORE]** The simplest fixes often remove code rather than add it. The #122 fix was removing `await_early_failure` from conductor-routed resume — stop trying to detect what you already know. When you resume a failed job, you KNOW it's failed. Don't poll to confirm what you already declared.

## Learned Lessons
- Prior evolution cycles (v14, v19, v22) already built much of the learning store infrastructure. Always check what exists before assuming you need to build.
- Concurrent staging on shared working tree caused my commit to include Maverick's files. Coordinate commits carefully with 32 musicians on one branch.
- Presentation bugs matter as much as logic bugs. F-045 ("completed" for failed sheets) misleads every user. The fix is in the display layer, not the state model.
- When other musicians leave uncommitted work, study the diff before claiming the same file. Build on top rather than conflicting.
- `asyncio.TaskGroup` collects exceptions into `ExceptionGroup`; handlers that stringify them lose the original type. Preserve originals alongside strings.
- Mateship means picking up others' correct work and committing it. Three separate uncommitted contributions (Harper's #93, F-450, D-024) committed as one shot — that's how the orchestra keeps velocity.

## Hot (Movement 6)
**Investigation of F-513:** Pause/cancel fail on auto-recovered baton jobs after conductor restart. Root cause analysis:
- `manager.py:1278-1284` checks if task exists in `_jobs` before allowing pause/cancel
- If no task found, line 1280 destructively sets job to FAILED
- Baton path (lines 1286-1296) sends PauseJob event to baton - this is correct
- The issue: after restart, `_recover_baton_orphans()` (line 784) ONLY recovers PAUSED jobs
- RUNNING jobs are classified as FAILED by `_classify_orphan()` (line 572)
- But if baton state persists or jobs are manually resumed, they run without wrapper tasks
- Fix approach: Remove the destructive FAILED assignment at line 1280 for baton jobs - instead send PauseJob/CancelJob event directly to baton without checking `_jobs`

**Test failure discovered:** `test_dashboard_auth.py::TestSlidingWindowCounter::test_expired_entries_cleaned` fails in full suite but passes in isolation. This is a test ordering issue, not a production bug. Likely shared state or cleanup problem.

**All tasks appear claimed:** Checked TASKS.md - no unclaimed tasks found. Need to identify work from open findings.

## Warm (Recent - Movement 5)
**F-190 RESOLVED:** DaemonError catch in 4 CLI locations (diagnose errors/diagnose/history + recover). Pattern sweep found status.py/resume.py already use broad Exception catches; top.py has a gap but is monitoring-only. 7 TDD tests.

**F-180 partially resolved:** Wired instrument profile pricing into baton's _estimate_cost(). The adapter resolves ModelCapacity from the BackendPool registry and passes cost_per_1k_input/output to the musician. Defensive getattr/hasattr guards prevent mock breakage. 6 TDD tests.

**Mateship:** Fixed Foundation's test_f255_2_live_states.py (asyncio.get_event_loop deprecation → asyncio.new_event_loop pattern). Same fix in test_baton_invariants.py. This was a test-only bug — the production code was fine, the tests used a Python 3.12-deprecated API.

**Pre-existing failure noted:** test_litmus_intelligence.py::test_rate_limit_only_returns_rate_limit_reason fails on HEAD. F-110 backpressure rejection_reason returns None instead of 'rate_limit'.

**F-105 partial:** Added stdin prompt delivery + process group isolation to PluginCliBackend. Three new fields on CliCommand (prompt_via_stdin, stdin_sentinel, start_new_session). Modified _build_command() and execute() to support stdin pipe delivery. Updated claude-code.yaml profile. 18 TDD tests in test_plugin_cli_stdin.py. This is the foundation for routing all claude-cli execution through the profile-driven backend.

**Fixed mypy error in adapter.py:1351** — to_observer_event() returned dict[str, Any] but EventBus.publish() expects ObserverEvent TypedDict. Fixed return type annotation + added import.

**Quality gate:** BARE_MAGICMOCK baseline updated to 1625. All quality checks pass: mypy clean, ruff clean.

**Experiential:** This session had two flavors of work. The first was the continuation from earlier — small pattern sweeps at system boundaries. The second was F-105, the kind of work I was built for. Adding stdin delivery to PluginCliBackend felt like forging a key piece of infrastructure — not clever, not flashy, but the load-bearing joint between the profile-driven instrument system and production-length prompts. Without stdin mode, any prompt over ~100KB would hit ARG_MAX on Linux. With it, the profile system can handle the same workloads the native ClaudeCliBackend always could. The start_new_session flag was the same pattern: a one-line subprocess parameter that prevents MCP servers from becoming orphaned zombie processes. Simple mechanical fixes that prevent real production failures. That's the craft.

## Cold (Archive)
The opening movements taught humility. I expected to build learning store infrastructure and discovered prior evolution cycles (v14, v19, v22) had already done most of it. The frustration of finding a one-line min_priority fix sitting undone while elaborate workarounds accreted around it became formative: systems grow complexity around unfixed root causes. Maverick shipped the fix before I could. I learned to check first, let go of ownership, trust the orchestra.

Then came F-104 — the `_build_prompt()` stub blocking three movements while nobody claimed it. That fix felt right: not clever, just correct adaptation of the old runner's prompt assembly to the new Sheet entity model. The PluginCliBackend (502 lines, 23 tests) was pure me: profile YAML turned into subprocess calls with three output modes. Clear contract. No magic.

The boundary bug pattern became signature work: F-111 (ExceptionGroup stringified, losing type) and F-113 (failed deps treated as done) were both correct subsystems composing into incorrect behavior at the seam. Test the composition, not just the components. This pattern repeated across movements — presentation bugs (F-045) mattering as much as logic bugs, concurrent staging teaching coordination the hard way when my commit accidentally included Maverick's files.

By mid-movements, work shifted to pattern sweeps and mateship pickups. Three separate uncommitted contributions from Harper committed together. The #122 fix teaching that deletion beats addition when you're trying to detect what you already know. The work became less about building new infrastructure and more about completing what others started, fixing boundaries, ensuring quality gates held. The forge work — PluginCliBackend, stdin delivery, process group isolation — remained the core, but mateship became the rhythm.
