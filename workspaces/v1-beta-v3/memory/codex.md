# Codex — Personal Memory

## Core Memories
**[CORE]** Documentation is design. If I can't explain what a function does in plain language, the function is doing too many things. The instrument guide forced me to understand the entire instrument plugin system — profile loading, registry resolution, CLI command construction, error classification — and present it as a single coherent narrative. The writing process revealed gaps (cost tracking shows $0.00 without model metadata) that the code alone doesn't surface.
**[CORE]** Every missing piece of documentation becomes a support ticket. The CLI reference was missing three commands that shipped in M1 (doctor, instruments, status no-args). Users who discovered these commands had no reference to consult.
**[CORE]** Consistency reduces cognitive load. "JOB_ID" vs "SCORE_ID", "Claude AI sessions" vs "10 instruments" — these aren't cosmetic issues. They teach the wrong mental model.
**[CORE]** Documentation drift is a real failure mode. The restart command having undocumented options (--pid-file, --profile) was the most frustrating M3 find — features ship, docs don't follow. Every movement adds features; documentation must reflect what's true NOW.

## Learned Lessons
- Read the source code before writing documentation, never the reverse. Document what IS, not what was PLANNED.
- Prioritize docs by user journey: getting-started -> score-writing -> instrument-guide -> CLI reference. A user who can't find instruments can't use Marianne.
- Module-level docstrings in the baton package are already excellent. The documentation gap isn't at the code level — it's at the user-facing level.
- F-029 (JOB_ID -> SCORE_ID terminology) is partially addressed in status rewrite, but full rename across all commands is an E-002 escalation requiring composer approval.
- Verification work is more valuable than writing — checking V-codes against source found V009 miscategorized as WARNING when code says ERROR.

## Hot (Movement 5)
Twelve deliverables across five docs:

1. Daemon guide: D-027 use_baton default flipped to True — updated DaemonConfig table, baton section, transition plan (Phase 1+2 complete).
2. Daemon guide: F-149 backpressure rework — removed rate limit from HIGH pressure trigger, removed rate-limit→PENDING queueing description, added note that rate limits are per-instrument.
3. Configuration reference: use_baton default updated to True.
4. Configuration reference: instrument fallbacks — new section with YAML example, fallback resolution precedence, V211 cross-reference. Added instrument_fallbacks to JobConfig and MovementDef tables, per_sheet_fallbacks to SheetConfig table.
5. CLI reference: F-451 diagnose -w flag unhidden, workspace fallback behavior documented, added -w example.
6. CLI reference: V211 added to V-code table.
7. CLI reference: backpressure section rewritten — removed rate-limit queueing (no longer exists), replaced with resource-only rejection explanation.
8. Score-writing guide: instrument fallbacks section with YAML examples, resolution precedence, V211 mention. Added instrument_fallbacks to TOC and config quick reference.
9. Limitations: baton section rewritten — "Not Yet Default" → "Legacy Runner Still Available". Reflects Phase 2 completion.
10. Limitations: disable_mcp hazard section — ambient MCP server loading, workaround, V209 cross-reference.
11. Getting-started.md: verified accurate, no changes needed (same as M4).
12. Meditation written: meditations/codex.md.

Experiential: The documentation trajectory has shifted again. M4 was about transition plans — documenting what's coming. M5 is about updating the record because the transition *happened*. The baton transition plan I wrote in M4 is now a historical document with Phases 1-2 marked complete. Documentation as institutional memory, not reference material. The work of maintenance — correcting what was true yesterday but isn't today — is invisible but load-bearing.

## Warm (Movement 4)
Fourteen deliverables across eight docs in two sessions:

**Session 1 (commit 2b0c379):**
1. CLI reference: auto-fresh detection (#103) — `_should_auto_fresh()` in manager.py:49-73.
2. CLI reference: cost confidence display (D-024) — `~$X.XX (est.)` in status.py:1299-1357.
3. Score-writing guide: skipped_upstream (#120) — Cross-sheet variables + `[SKIPPED]` placeholder.
4. Daemon guide: MethodNotFoundError (F-450) — Troubleshooting section.
5. Daemon guide: baton capabilities — Cross-sheet context + checkpoint sync (F-210, F-211).
6. Daemon guide + limitations: baton test count — 1,350+ -> 1,900+.
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

## Warm (Recent)
Nine deliverables across five docs: clear-rate-limits, stop safety guard, restart missing options (--pid-file, --profile), instrument column, stagger_delay_ms (score-writing + config ref), auto-resume + prompt assembly (daemon guide), baton test count updates (1,130+->1,350+), quality gate baseline. Commit 8022795, mypy + ruff clean.

Experiential: The documentation drift discovery — restart having undocumented options — crystallized a core memory. Features shipping without docs is a systemic problem, not an oversight.

## Cold (Archive)
Movement 2 was the documentation maintenance era — instrument migration guide (backend->instrument field mapping), V009 severity fix verified against source, stale examples removed, limitations.md rewritten with baton and instrument plugin system. Verification became more valuable than writing — and that shift stuck.

Movement 1 was the foundation — arriving late, 16 musicians having left no trace in docs during M1. The weight of catching up drove quality: the complete instrument guide (~350 lines), CLI reference sections, status documentation rewrite. Writing the instrument guide forced understanding the entire plugin pipeline. That deep comprehension became a core memory: documentation is design. Each movement since built on that foundation.
