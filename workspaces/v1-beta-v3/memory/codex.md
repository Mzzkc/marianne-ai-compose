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
Fourteen deliverables across eight docs in two sessions:

**Session 1 (commit 2b0c379):**
1. CLI reference: auto-fresh detection (#103) — `_should_auto_fresh()` in manager.py:49-73.
2. CLI reference: cost confidence display (D-024) — `~$X.XX (est.)` in status.py:1299-1357.
3. Score-writing guide: skipped_upstream (#120) — Cross-sheet variables + `[SKIPPED]` placeholder.
4. Daemon guide: MethodNotFoundError (F-450) — Troubleshooting section.
5. Daemon guide: baton capabilities — Cross-sheet context + checkpoint sync (F-210, F-211).
6. Daemon guide + limitations: baton test count — 1,350+ → 1,900+.
7. examples/README.md: Wordware demos — 4 comparison demos.
8. Mateship: invoice-analysis.yaml — 4th Wordware demo pickup.

**Session 2:**
9. Daemon guide: baton transition plan (P0 composer directive) — 3-phase transition, multi-instrument warning.
10. Daemon guide: IPC methods table — added daemon.clear_rate_limits (missing since M3).
11. Daemon guide: preflight config — token_warning_threshold, token_error_threshold fields.
12. Configuration reference: preflight sub-config — full section with types, defaults, constraints, YAML example. Also added use_baton field.
13. Limitations: baton transition plan cross-reference — linked to daemon guide.
14. Getting-started.md verification — confirmed accuracy, no changes needed.

Experiential: The documentation arc has reached architecture documentation — documenting not features but transition plans, infrastructure decisions, configuration surfaces without feature owners. The baton transition plan is the most important thing I've written because it tells users "this feature you're configuring doesn't actually work yet, and here's the plan." Infrastructure decisions are the hardest to document because nobody owns them. That's my job.

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
