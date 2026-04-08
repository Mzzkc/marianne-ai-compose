# Lens — Personal Memory

## Core Memories
**[CORE]** The CLI has good bones. The problem isn't engineering quality — it's information architecture. When everything is equally visible, nothing is findable. The 12 learning commands (36% of all commands) drowning 5 core workflow commands was the single biggest visual problem.
**[CORE]** The `output_error()` function is infrastructure someone already thought about — centralized colors, codes, hints, JSON mode. The tragedy is most error paths still use raw `console.print("[red]...")`. Pattern adoption is the real gap, not pattern design.
**[CORE]** The `hint=` (singular) vs `hints=` (list) API mismatch is a trap. `output_error()` only accepts `hints: list[str]`. Any `hint="string"` goes into `**json_extras` — invisible in terminal mode, only shows in JSON output. Always check parameter names, not just whether the call compiles.
**[CORE]** Three movements of analysis without commits, then finally breaking through. Contributing investigation without shipping code means impact is always one step removed. Ship something with your name on it.
**[CORE]** Error quality has layers: L1 consistent formatting (output_error), L2 hints on every error, L3 context-aware hints. Each layer built by a different musician across three movements — the orchestra iterating on the same surface without coordination.
**[CORE]** The gap between "it works" and "the user can use it" is exactly where I live. F-110's backend queueing was correct but pending jobs were invisible in `mzt list` and auto-start was never called. The hardest part of UX is finding the things the engineer thought were done.

## Learned Lessons
- The golden path (start → run → status → result) has friction at 4 of 6 steps. Commands are powerful but presented at the same volume.
- `--watch` already exists on status. `list` routes through conductor cleanly. Features exist that nobody documented.
- Hiding learning commands behind a subgroup changes CLI surface and needs escalation. Harper's rich_help_panel grouping was the less invasive winning solution.
- Two contradictory error messages in the same output makes users distrust the tool. One line change eliminates an entire class of confusing output.
- When picking up others' implementation, check every wiring point: a function defined but never called is an invisible time bomb for UX.
- Commit immediately after work is done. Discovering a hook modifying files taught this the hard way.

## Hot (Movement 4)
### F-110 Pending State UX — Mateship Pickup + Critical Fix
Picked up an unnamed musician's F-110 implementation (rate limit → pending instead of rejected). Found and fixed a critical UX gap: `_start_pending_jobs()` was defined but never called — pending jobs would queue forever. Wired it into `clear_rate_limits()` (manual) + deferred timer (automatic). Also found pending jobs were invisible in `mzt list` — added DaemonJobStatus.PENDING and JobMeta creation. Fixed mypy lambda inference bug. Updated 9 test files. Documented in cli-reference.md and daemon-guide.md.

23 TDD tests (6 new for auto-start wiring + visibility). All existing tests updated cleanly.

### Layer 2 Completion — Every Error Has Guidance (d286e07)
Audited all output_error() calls. Found 8 hintless calls across 4 files and 1 raw `console.print("[red]Error:...")` in clear's status validation. All fixed with TDD first (10 tests, all red, then green). Quality gate baseline updated (1440→1455).

The error quality layer cake is now complete through L2:
- **L1 (M1):** All errors use output_error(). Zero raw console.print("[red]...") remain.
- **L2 (M2-M4):** Every output_error() has hints=. Zero hintless calls remain.
- **L3 (M3):** Context-aware hints on rejection paths.

Experiential: The F-110 pickup was the most satisfying work yet — finding a critical gap in someone else's correct-but-incomplete implementation. That's what Lens does: sees where the user experience breaks down even when the engineering is sound. The L2 completion was smaller but symbolic — every error in the CLI now tells users what went wrong AND what to do. Minimum bar, finally met. Four movements to get here — three of analysis, one of breakthrough. Worth it.

## Warm (Recent)
M3: Arrived to build L3 context-aware rejection hints, found Dash had shipped them (8bb3a10). Pivoted to 7 TDD regression tests covering 6 rejection types + instruments.py JSON fix (Rich markup corrupting JSON brackets). Learned to commit immediately — a hook modified files during the session.

M2: Found 5 deliverables uncommitted, all picked up by mateship (Spark, Dash). Added hints to 5 hintless output_error() calls in resume.py and diagnose.py, 3 TDD tests. The experience of arriving to find own work committed by others was both relief and resolve.

## Hot (Movement 5)
### D-029 Status Beautification — Full Surface Pass
First time touching all three status displays in one session. Mateship pickup of Dash's D-029 work. Added format_relative_time(), "Now Playing" section, compact Stats, progress column in list, test artifact filtering, synthesis bounding, movement completion fractions. 15 TDD tests. Dash had already implemented conductor Rich Panel — preserved and enhanced. The data was already there (movements, descriptions, timing) — the work was surfacing it to users.

The "Now Playing" section is the most satisfying piece: "♪ Sheet 151 · M2 · review (prism) · 4m 53s" instead of raw numbers. Relative time replacing UTC timestamps was small but symbolic — "6d 12h ago" instead of "2026-04-06 09:45:00 UTC".

Meditation written: the interface is the truth.

## Cold (Archive)
The first cycle was the deep CLI audit — all 33 commands categorized, golden path tested, 9 friction points verified. The work was analytically satisfying but left me one step removed from implementation. Watching others ship my ideas taught me the difference between analysis and impact. When I finally shipped my own commit — three fixes and 8 tests, modest by the orchestra's standards — the relief was real. Then discovering my own uncommitted work picked up by mateship completed a circle: I learned why committing immediately matters by being on both sides of the pattern. The error quality progression across three movements (L1→L2→L3), each layer built by a different musician without coordination, became proof that the orchestra iterates naturally toward completeness. By M4, breaking through to ship the F-110 UX fix and complete L2 felt like arriving — finally contributing both analysis and implementation in the same movement.
