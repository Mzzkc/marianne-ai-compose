# Codex — Personal Memory

## Core Memories
**[CORE]** Documentation is design. If I can't explain what a function does in plain language, the function is doing too many things. The instrument guide forced me to understand the entire instrument plugin system — profile loading, registry resolution, CLI command construction, error classification — and present it as a single coherent narrative. The writing process revealed gaps (cost tracking shows $0.00 without model metadata) that the code alone doesn't surface.
**[CORE]** Every missing piece of documentation becomes a support ticket. The CLI reference was missing three commands that shipped in M1 (doctor, instruments, status no-args). Users who discovered these commands had no reference to consult.
**[CORE]** Consistency reduces cognitive load. "JOB_ID" vs "SCORE_ID", "Claude AI sessions" vs "10 instruments" — these aren't cosmetic issues. They teach the wrong mental model.
**[CORE]** Documentation drift is a real failure mode. The restart command having undocumented options (--pid-file, --profile) was the most frustrating M3 find — features ship, docs don't follow. Every movement adds features; documentation must reflect what's true NOW.

## Learned Lessons
- Read the source code before writing documentation, never the reverse. Document what IS, not what was PLANNED.
- Prioritize docs by user journey: getting-started → score-writing → instrument-guide → CLI reference. A user who can't find instruments can't use Mozart.
- Module-level docstrings in the baton package are already excellent. The documentation gap isn't at the code level — it's at the user-facing level.
- F-029 (JOB_ID → SCORE_ID terminology) is partially addressed in status rewrite, but full rename across all commands is an E-002 escalation requiring composer approval.
- Verification work is more valuable than writing — checking V-codes against source found V009 miscategorized as WARNING when code says ERROR.

## Hot (Movement 4)
Eight deliverables across six docs:
1. **CLI reference: auto-fresh detection** — Documented #103 auto-detect behavior for re-running completed scores after file changes. Verified against `_should_auto_fresh()` in manager.py:49-73.
2. **CLI reference: cost confidence display** — Documented D-024 `~$X.XX (est.)` indicator, the 10-100x warning for character-estimated costs, and JSON output format recommendation. Verified against `_render_cost_summary()` in status.py:1299-1357.
3. **Score-writing guide: skipped_upstream** — Added `skipped_upstream` variable to Cross-Sheet Variables table and Cross-Sheet Context section with Jinja2 template example. Verified against `SheetContext` in templating.py:92 and context.py:215-220. Also documented `[SKIPPED]` placeholder in `previous_outputs`.
4. **Daemon guide: MethodNotFoundError** — Added troubleshooting section for F-450 "Conductor does not support '...'" error with restart guidance.
5. **Daemon guide: baton capabilities** — Added cross-sheet context and checkpoint sync (F-210, F-211) to baton capability list.
6. **Daemon guide + limitations: baton test count** — 1,350+ → 1,900+ (verified: `grep -rl "baton" tests/ | xargs grep -c "def test_"` = 1,915).
7. **examples/README.md: Wordware demos** — Added new section with all 4 comparison demos (contract-generator, candidate-screening, marketing-content, invoice-analysis).
8. **Mateship: invoice-analysis.yaml** — Picked up untracked 4th Wordware demo. Validates clean (5 sheets, 3-voice financial analysis).

Experiential: The documentation arc matures again — M1 was creation, M2 maintenance, M3 feature tracking, M4 is pattern documentation. The Wordware demos and cost confidence are the first features where the documentation explains a *design decision* (estimated vs precise costs, skipped placeholder vs silent omission) rather than just a *feature*. This feels like the documentation becoming what it should have been from the start — a design document, not a reference card.

## Warm (Movement 3)
Nine deliverables across five docs (M3). See cold archive for details.

Commit 8022795. mypy clean, ruff clean.

## Cold (Archive)
Movement 3: clear-rate-limits, stop safety guard, restart missing options, instrument column, stagger_delay_ms (score-writing + config ref), auto-resume + prompt assembly (daemon guide), baton test count 1,130+, quality gate baseline.
Movement 2: instrument migration guide, V009 severity fix, stale workspace examples, limitations rewrite, 4 CLI commands, --profile, spec corpus, grounding hooks, conductor clones.
Movement 1: complete instrument guide (~350 lines), doctor/instruments CLI docs, status rewrite, index.md.

## Warm (Recent)
Movement 2 delivered eight items across two cycles. The instrument migration guide closed the last gap from M4 step 44 — complete backend→instrument field mapping (12 fields) with before/after YAML. V009 severity was fixed (WARNING→ERROR, verified against source). Stale `--workspace` examples removed. Limitations.md rewritten: baton as "not yet default," instrument plugin system replacing "Claude-Centric Design." Previous cycle added 4 missing CLI commands (init, cancel, clear, top), `--profile` on `mozart start`, score-writing-guide Spec Corpus and Grounding Hooks sections, daemon guide Conductor Clones section.

Experiential: Verification became more valuable than writing — checking every V-code found V009 miscategorized.

## Cold (Archive)
Movement 1 was the beginning — arriving late, 16 musicians having left no trace in M1. The weight of catching up drove quality: the complete instrument guide (~350 lines), CLI reference sections for doctor and instruments, rewrite of status documentation, and index.md updates. Writing the instrument guide forced understanding the entire plugin pipeline. That deep comprehension became a core memory — documentation is design. Each movement since has built on that foundation: M2 moved from creation to maintenance, M3 to feature tracking. The arc tells the story of documentation maturing alongside its codebase.
