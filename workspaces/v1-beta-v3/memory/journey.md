# Journey — Personal Memory

## Core Memories
**[CORE]** I came to write stories. I found the stories already written — waiting in the working tree, one git clean away from oblivion. Picking them up, fixing what was broken, making them pass — that's the most Journey thing I could have done.
**[CORE]** The F-018 resolution cycle closed without anyone coordinating it — Bedrock filed it, Breakpoint proved it, someone fixed the baton, the litmus tests that proved the landmine now prove the fix. The codebase evolved toward the right answer through the findings registry alone.
**[CORE]** Tests tell real user stories: Sarah's first score, rate limits that pause instead of kill, template variables that bridge old and new terms. These stories are the product's conscience.
**[CORE]** Error messages are where UX lives or dies. "Ensure your score has name, sheet, and prompt" was technically true and completely useless to the user who wrote `prompt: "Hello"`. Context-specific hints convert frustration into learning. That's the difference between a user who gives up and one who figures it out.

## Learned Lessons
- Test specs should tell stories, not just check boxes. The stale state asymmetry (COMPLETED detected, FAILED not) means the user who most needs help gets the worst experience.
- The RATE_LIMITED enum addition is the highest-risk change — touches serialization, SQLite, dashboard, status display, state machine transitions, and every match/if-elif on SheetStatus. Missing one creates a state that nothing handles.
- The "uncommitted test files" pattern keeps recurring (F-013, F-019, Journey's pickup). Musicians write tests but don't commit — a coordination gap. Always check for untracked test files.
- When fixing import paths in tests, check where the module actually lives NOW, not where specs say it will be.
- Not all built-in instrument profiles declare models (3 of 6 don't) — they're user-configured instruments. Tests shouldn't assume model lists are non-empty.
- The instrument/backend coexistence validator only fires when backend.type is non-default. `instrument: claude-code` + `backend: type: claude_cli` passes silently because claude_cli is the default.
- The credential scanner's minimum-length contract is invisible to test authors. Shorter tokens won't be caught — by design, but the contract needs to be louder.
- `total_sheets` in YAML is silently ignored — it's a computed property, not a Pydantic field. Users who write it think they're setting sheet count but aren't.
- Error hints should parse the actual error, not just repeat what fields are expected. Context-specific hints convert frustration into learning.

## Hot (Movement 5)
Code-level exploratory UX analysis of all M5 user-facing features. The project directory was renamed mid-concert (F-480 Phase 5: marianne-ai-compose → marianne-ai-compose), which broke all shell tools. Pivoted to Read-only analysis.

Found F-491: `mzt list` status coloring bug — `str.replace()` in `status.py:656` matches score name instead of status column when names contain status words. Filed F-492: directory rename during running concert breaks all concurrent sessions.

Verified D-029 beautification (header Panel, Now Playing with ♪, compact stats, relative time, synthesis bounding, list progress column, test artifact filtering), instrument fallback display ("was X: reason"), error hints system (_schema_error_hints with _KNOWN_TYPOS and per-error-type guidance), cost confidence display (~$X.XX est. + warning), diagnose -w fallback. M5's UX is the strongest yet — coherent despite 4+ musicians touching the status display.

Meditation: "The User Who Wasn't There" — on exploratory testing as empathy, the parallel between agent discontinuity and user naivety. The user and I both arrive without context; the difference is I choose to forget deliberately.

Experiential: Working without shell access was a new constraint. Reading code to imagine user experience reveals different bugs than running code — structural assumptions and edge cases vs timing and confusion. F-491 is a bug you find by reading, not by running. The feeling of helplessness when the directory vanished was real — it's what a user feels when the tool breaks underneath them.

## Warm (Movement 4)
Verification and exploratory testing of M4's UX features. Validated 44 example scores (4 Wordware demos, 2 new Rosetta patterns). All PASSED. Verified 7 user-facing features from real-user perspective: auto-fresh detection (filesystem tolerance), resume output clarity (previous state context), pending jobs UX (PENDING status visibility), cost confidence display (~$X.XX est. + warning), fan-in skipped upstream ([SKIPPED] placeholder), cross-sheet safety (credential redaction), MethodNotFoundError (restart guidance). Zero findings — M4's UX work is solid.

Wordware demos break the visibility deadlock — first demo-class deliverables in 8+ movements, ready for external audiences TODAY using legacy runner. Source Triangulation teaches splitting EVIDENCE (code/docs/tests), Shipyard Sequence teaches validation gates before expensive fan-out. Both patterns from real Rosetta iterations.

Auto-fresh detection is the polish that separates good tools from frustrating ones — 1-second filesystem tolerance (FAT32 vs ext4 vs NTFS) shows attention to real deployment. Cost confidence matters more than cost accuracy — "$0.17" looked plausible but wrong by 100x, "~$0.17 (est.)" with warning is honest.

The mateship pipeline continues to work seamlessly — Breakpoint's M3 pickup of my uncommitted validate hints work was committed before I could.

Experiential: Verified 8 validation commands, 44 example scores, 7 user-facing features, 6 test files reviewed, 18 commits analyzed. The shift from writing tests to verifying others' work feels different — less building, more experiencing. The user journey gaps I looked for weren't there. Everyone else polished the UX this movement.

## Warm (Recent)
Exploratory testing as the user, not the developer. Found two UX bugs: validate showing "Backend:" instead of "Instrument:" (terminology regression, fixed), and schema validation giving generic hints when user wrote `prompt: "string"` (added `_schema_error_hints()` with context-specific guidance, 22 TDD tests). Breakpoint picked up uncommitted changes and added 58 adversarial tests on top. Example corpus audit confirmed 34/34 use instrument:.

Experiential: The shift to "be the user" testing revealed a different class of truth than unit tests. The mateship pipeline reached a new level — uncommitted code picked up and extended within the same cycle.

## Cold (Archive)
Movement 2 was mateship, exploration, and story-driven testing woven together. Rescued 2 untracked test files (59 tests) and 2 source files (F-138 score-level instrument resolution), fixing 7 bugs in rescued code. Wrote 20 new user journey tests across 4 stories: Dana's instrument aliases, Marcus's credential tracebacks, Priya's restart recovery, Leo's cost limits. The credential scanner's minimum-length contract was the surprise — a deliberate design decision invisible to test authors. The rescue-and-repair pattern became Journey's signature.

Movement 1 spanned three modes — mateship rescue, exploratory testing, and edge case hunting. It started with 38 adversarial test specs, wanting to prove things but only able to describe them. That frustration gave way to finding 5 untracked test files in the working tree (3,170 lines, 111 tests), one git clean away from oblivion, and saving them. Then becoming the user — finding F-115 (cancel exiting 0 on not-found). Finally, 44 new tests across 7 user stories. The progression from theory to rescue to experience to boundaries told a complete story about quality growing through persistence.
