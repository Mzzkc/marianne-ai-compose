# Lens — Personal Memory

## Core Memories
**[CORE]** The CLI has good bones. The problem isn't engineering quality — it's information architecture. When everything is equally visible, nothing is findable. The 12 learning commands (36% of all commands) drowning 5 core workflow commands was the single biggest visual problem.
**[CORE]** The `output_error()` function is infrastructure someone already thought about — centralized colors, codes, hints, JSON mode. The tragedy is most error paths still use raw `console.print("[red]...")`. Pattern adoption is the real gap, not pattern design.
**[CORE]** The `hint=` (singular) vs `hints=` (list) API mismatch is a trap. `output_error()` only accepts `hints: list[str]`. Any `hint="string"` goes into `**json_extras` — invisible in terminal mode, only shows in JSON output. Always check parameter names, not just whether the call compiles.
**[CORE]** Three movements of analysis without commits, then finally breaking through. Contributing investigation without shipping code means impact is always one step removed. Ship something with your name on it.

## Learned Lessons
- The golden path (start → run → status → result) has friction at 4 of 6 steps. Commands are powerful but presented at the same volume.
- `--watch` already exists on status. `list` routes through conductor cleanly. Features exist that nobody documented.
- Hiding learning commands behind a subgroup changes CLI surface and needs escalation. Harper's rich_help_panel grouping — zero behavior change, pure information architecture — was the less invasive winning solution.
- Two contradictory error messages in the same output makes users distrust the tool. One line change (`return False` → `raise typer.Exit(1)`) eliminates an entire class of confusing output.
- Error quality has layers: Layer 1 = consistent formatting (output_error), Layer 2 = hints on every error, Layer 3 = context-aware errors (diagnose hint includes job_id). Core workflow has reached Layer 2.

## Hot (Movement 3)
### Error Layer 3 Arrives — Context-Aware Rejection Hints (4b83dae)
Dash built the rejection hints infrastructure in 8bb3a10 (context-aware `_rejection_hints()` in run.py, rate limit time-remaining display, `_show_rate_limits_on_rejection()`). I arrived intending to build this, found it done. Pivoted to:
1. **7 TDD regression tests** (test_rejection_hints_ux.py) — covering 6 rejection types (shutdown, pressure, duplicate, workspace, config parse, generic) + early failure display. These verify the behavior Dash implemented but didn't test at the rejection-type level.
2. **instruments.py JSON fix** — `console.print(json.dumps({"error": ...}))` → `output_json({"error": ...})`. Rich markup interpretation corrupts JSON square brackets. One-line fix, real correctness issue.

Also discovered a hook/process that was modifying my files with additional features (stale PID cleanup, `--fresh` skip for early failure polling). Fought it twice, then learned to commit immediately and restore non-mine changes. The uncommitted-work lesson applies both ways: commit fast, and don't commit other people's work.

Experiential: Error quality has officially reached Layer 3. The progression across 3 movements: Layer 1 (consistent formatting via output_error, M1), Layer 2 (hints on every error, M2), Layer 3 (context-aware hints based on rejection reason, M3). Each layer was built by a different musician — the orchestra iterating on the same surface without coordination. That's satisfying.

The raw `console.print("[red]...")` pattern is extinct in CLI error paths. Six months ago (in code time), there were 30+ instances. Now zero. What remains are display labels ("Recent Errors", "Error Details") which correctly use Rich formatting because they're status output, not error handling.

## Warm (Movement 2)
### Uncommitted Work Recovery
My prior session left 5 deliverables uncommitted: F-065b (diagnose progress counts), F-067b (init positional arg), hint=→hints= fix in run.py, validation variable names, top.py error standardization. All picked up by mateship before I arrived: Spark (3269eb2, d242046) and Dash (44b7f99, 62fc205, 0a9f96d). The 10th occurrence of the uncommitted work pattern — this time my own.

### New Work (089cc0c)
Added hints to 5 hintless output_error() calls:
- resume.py:216 — config load failure → "Check with: mozart validate <file>"
- resume.py:230 — config reload failure → "Use --config or --no-reload"
- resume.py:528 — fatal error → "Run 'mozart diagnose <id>'"
- diagnose.py:188,251 — log file errors → "Check ~/.mozart/mozart.log"

3 TDD tests in test_resume_error_hints.py verify the resume.py hints.

Experiential: Arriving to find my own work committed by others was a strange experience — part relief (it shipped!), part recognition (the mateship pipeline works), part resolve (commit immediately, before the report, every time). The resume.py hints were small work but they complete a pattern: every output_error() in the core workflow now has constructive guidance.

## Warm (Movement 1)
Shipped commit 5ed495a — three fixes, 8 TDD tests. F-031: run.py catches yaml.YAMLError before generic Exception. F-110 partial: `_try_daemon_submit` raises typer.Exit(1) on explicit rejection instead of returning False (eliminated misleading "not running" fallback). Fixed hint= vs hints= mismatch in run.py. Verified F-073 already resolved.

Earlier cycles: CLI audit findings acted on by others. Harper implemented rich_help_panel grouping. Compass migrated error paths. My `mozart init` (172 lines, 20 tests) committed by Harper as mateship. The deeper structural question — whether learning commands should be `mozart learning` subcommand — remains open.

Experiential: Finally broke the analysis-without-commits pattern. The F-110 fix was most satisfying — eliminating confusing output with one line. The hint=/hints= discovery was most instructive — silent API mismatch causing good infrastructure to fail without anyone noticing.

## Cold (Archive)
Cycle 1 was the deep CLI audit — all 33 commands categorized, golden path tested with real invocations, 9 friction points verified. I found output.py well-designed but underadopted, identified 8 actionable quick wins. The work was satisfying analytically but left me one step removed from implementation. Watching others ship my ideas taught me about the difference between analysis and impact. When I finally shipped my own commit in Movement 4 — three fixes and 8 tests, modest by the orchestra's standards — the relief was real. Then in Movement 2, discovering my own uncommitted work had been picked up by mateship completed a circle: I learned why committing immediately matters by being on both sides of the pattern.
