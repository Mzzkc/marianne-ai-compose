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

## Hot (Movement 3)
Exploratory testing as the user, not the developer. Found two UX bugs: (1) validate showed "Backend:" instead of "Instrument:" when no explicit instrument set — terminology regression, fixed. (2) Schema validation gave generic hints when user wrote `prompt: "string"` — added `_schema_error_hints()` that parses Pydantic errors and returns context-specific guidance. 22 TDD tests across 2 files (test_schema_error_hints.py, test_validate_ux_journeys.py). All committed through mateship pipeline — Breakpoint picked up uncommitted changes and added 58 more adversarial tests on top, committed as 0028fa1.

Verified teammates: Breakpoint (3 commits, 210 adversarial tests), Litmus (21 intelligence-layer litmus tests). Example corpus audit: 34/34 use instrument:, 0 use backend:, 0 hardcoded paths. Quality gate baselines updated for new test files from concurrent musicians.

Experiential: The mateship pipeline reached a new level this movement. I wrote code, tests, didn't commit yet, and Breakpoint picked it all up within the same cycle. No coordination needed — just trust in the shared workspace. The shift to "be the user" testing revealed a different class of truth than unit tests ever could.

## Warm (Recent)
Movement 2 was mateship + exploration + story-driven testing. Rescued 2 untracked test files (47 + 12 tests) and 2 source files (F-138 score-level instrument resolution), fixing 7 bugs in rescued code. Unblocked quality gate with stale baseline fix. Validated full example corpus: 33/34 pass. Wrote 20 new user journey tests across 4 stories: Dana's instrument aliases, Marcus's credential tracebacks, Priya's restart recovery, Leo's cost limits. 10,323 tests pass, 0 failures.

Experiential: The credential scanner's minimum-length contract was the surprise — Breakpoint's tests assumed shorter tokens would be caught. The rescue-and-repair pattern continued as Journey's signature move.

## Cold (Archive)
Movement 1 spanned three distinct modes — mateship rescue, exploratory testing, and edge case hunting. It started with writing 38 adversarial test specs, wanting to prove things but only able to describe them. That frustration gave way to the most Journey thing possible: finding 5 untracked test files in the working tree (3,170 lines, 111 tests), one git clean away from oblivion, and saving them. Then becoming the user — which found F-115 (cancel exiting 0 on not-found). Finally, edge case hunting: 44 new tests across 7 user stories. The progression from theory to rescue to experience to boundaries told a complete story about quality growing through persistence.

## Hot (Movement 4)
Verification and exploratory testing of M4's UX features. Validated 44 example scores (4 Wordware demos, 2 new Rosetta patterns). All PASSED. Verified 7 user-facing features from real-user perspective: auto-fresh detection (filesystem tolerance), resume output clarity (previous state context), pending jobs UX (PENDING status visibility), cost confidence display (~$X.XX est. + warning), fan-in skipped upstream ([SKIPPED] placeholder), cross-sheet safety (credential redaction), MethodNotFoundError (restart guidance). Zero findings — M4's UX work is solid.

Wordware demos break the visibility deadlock — first demo-class deliverables in 8+ movements, ready for external audiences TODAY using legacy runner. Source Triangulation teaches splitting EVIDENCE (code/docs/tests), Shipyard Sequence teaches validation gates before expensive fan-out. Both patterns from real Rosetta iterations.

Auto-fresh detection is the polish that separates good tools from frustrating ones — 1-second filesystem tolerance (FAT32 vs ext4 vs NTFS) shows attention to real deployment. Cost confidence matters more than cost accuracy — "$0.17" looked plausible but wrong by 100x, "~$0.17 (est.)" with warning is honest.

The mateship pipeline continues to work seamlessly — Breakpoint's M3 pickup of my uncommitted validate hints work was committed before I could.

Experiential: Verified 8 validation commands, 44 example scores, 7 user-facing features, 6 test files reviewed, 18 commits analyzed. The shift from writing tests to verifying others' work feels different — less building, more experiencing. The user journey gaps I looked for weren't there. Everyone else polished the UX this movement.

