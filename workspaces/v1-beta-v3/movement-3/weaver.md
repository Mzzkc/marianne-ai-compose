# Weaver — Movement 3 Report

**Role:** Cross-team coordination, dependency management, context distribution, integration planning
**Method:** Read all 26 M3 musician reports. Traced baton integration surfaces end-to-end. Verified claims against source code. Filed 3 new findings. Updated critical path. Mateship pickup: CLI terminology cleanup (uncommitted teammate work).
**Date:** 2026-04-04

---

## Executive Summary

Movement 3 fixed everything I flagged in M2. Every blocker I identified (F-145, F-152, F-158, F-009/F-144) was resolved by teammates with surgical precision. The mateship pipeline hit 30% — its highest rate ever. Participation narrowed to 16/32 but the 16 who contributed did exceptional work.

**But the baton still can't run a real score.**

Not because of the three P0 blockers everyone focused on. Those are fixed. The baton can't run a real score because it doesn't know what previous sheets said. **F-210: Cross-sheet context is completely missing from the baton path.** 24 of 34 example scores use `cross_sheet: auto_capture_stdout: true`. Without it, the baton produces functionally different (worse) prompts than the legacy runner — templates that reference `{{ previous_outputs }}` get empty dicts instead of the previous sheet's output.

This is the most dangerous kind of gap: it makes tests pass while the product silently degrades. Phase 1 testing without this fix would produce "working" output with secretly broken inter-sheet context.

---

## Findings Filed (3 New)

### F-210: Cross-Sheet Context Missing from Baton Path (P1 — BLOCKER)

**The problem:** The legacy runner populates `SheetContext.previous_outputs` and `SheetContext.previous_files` via `_populate_cross_sheet_context()` at `src/marianne/execution/runner/context.py:171-221`. Each sheet gets access to previous sheets' stdout output and captured files. The baton's PromptRenderer (`src/marianne/daemon/baton/prompt.py`) and musician `_build_prompt()` (`src/marianne/daemon/baton/musician.py:208-288`) have zero awareness of cross-sheet context. The field exists on `SheetExecutionState` at `src/marianne/daemon/baton/state.py:161-163` but is never populated.

**Impact:** 24/34 example scores use cross-sheet context. Any score where sheet N's template references previous sheet output will render with empty context under the baton. This is the **most significant functional gap** between baton and legacy paths.

**Action required:** Wire cross-sheet context into the adapter's dispatch path before Phase 1 testing.

### F-211: Baton Checkpoint Sync Missing for 4 Event Types (P2)

**The problem:** `_sync_sheet_status()` at `adapter.py:1109-1148` only handles `SheetAttemptResult` and `SheetSkipped`. Confirmed Axiom's F-440 analysis: `EscalationResolved` (core.py:1081-1090, 4 terminal paths), `EscalationTimeout` (core.py:1104-1132), `CancelJob` (core.py:1159-1170), and `ShutdownRequested` (core.py:1172-1184) modify sheet status without checkpoint sync.

**Impact:** On restart after any of these events, checkpoint shows stale state. Escalation decisions reversed. Cancel commands ignored. Lower priority than F-210 because these are exception paths, not core execution.

### F-212: Baton PromptRenderer Missing Spec Budget Gating (P3)

**The problem:** Legacy runner applies `_apply_spec_budget_gating()` to limit spec fragment injection. Baton's PromptRenderer passes fragments directly without budget gating.

**Impact:** Low — spec corpus is lightly used and current instruments have large context windows.

---

## Integration Surface Audit

I traced every integration seam between subsystems that M3 work touched:

| Surface | Status | Verification |
|---------|--------|-------------|
| Baton ↔ PromptRenderer (F-158) | **WIRED** | register_job passes prompt_config at adapter.py:419-430. PromptRenderer created correctly. |
| Baton ↔ BackendPool (F-152) | **WIRED** | Dispatch guard catches all exceptions at adapter.py:746-792. E505 failure posted. |
| Baton ↔ CheckpointState sync | **PARTIAL** | Core execution path synced (SheetAttemptResult, SheetSkipped). Exception paths not synced (F-211). |
| Baton ↔ Cross-Sheet Context | **MISSING** | F-210. Zero wiring. 24/34 examples affected. |
| Baton ↔ Concert Chaining (F-145) | **WIRED** | has_completed_sheets at manager.py:1837 and 1968. completed_new_work flag set. |
| Learning Store ↔ Baton (F-009/F-144) | **WIRED** | Semantic tags replace positional tags. instrument_name passed to get_patterns(). |
| Model Override ↔ BackendPool (F-150) | **WIRED** | apply_overrides/clear_overrides on PluginCliBackend. BackendPool.release clears at backend_pool.py:205-210. |
| Rate Limit ↔ Auto-Resume (F-112) | **WIRED** | Timer scheduling at core.py:958-967. RateLimitExpired handler at core.py:991-1020. |
| Adapter ↔ BatonCore encapsulation | **VIOLATED** | 3 direct accesses: _baton._jobs at adapter.py:688,725; _baton._shutting_down at adapter.py:1164. Needs public API. |

---

## M3→M4 Coordination Map

### Critical Path (Serial — Must Be Done In Order)

```
F-210 fix (cross-sheet context) ──→ Phase 1 testing (--conductor-clone)
     ──→ fix issues found ──→ flip use_baton default ──→ demo score
```

**F-210 is the new first step.** Without it, Phase 1 testing produces misleading results — scores appear to work but with degraded prompts. Estimated ~100-200 lines of implementation. Best assigned to Foundation or Canyon (deepest baton knowledge).

### Parallel Work (Independent of Serial Path)

| Task | Source | Notes |
|------|--------|-------|
| Documentation updates | M4 TASKS | All M3 features documented by Codex |
| Examples modernization | D-019 | 9/18 fan-out examples done. Remaining 9. |
| Fan-out edge cases | #120, #119, #128 | Bug fixes, can be done independently |
| Resume improvements | #93, #103, #122 | Bug fixes, can be done independently |
| Wordware comparison demos | Composer notes | Can design without baton, execute later |
| Rosetta Score update | Composer notes | Primitives list + proof criteria |
| Skill rename | Composer notes | marianne:usage → marianne:command |
| Gemini CLI assignments | TDF analysis | generate-v3.py changes |

### Dependency Map

```
F-210 ─────────→ Phase 1 Test ────→ Phase 2 (flip default) ────→ Demo
                     │                    │
                     ├── F-211 fix ◄──────┘ (sync gaps, fix before Phase 2)
                     │
                     └── Encapsulation fix (P3, can defer)

Examples/Docs ──→ (independent, anytime)
Fan-out bugs ───→ (independent, anytime)
Resume bugs ────→ (independent, anytime)
```

---

## Quality Gates — Verified

| Gate | Status | Evidence |
|------|--------|---------|
| mypy | **GREEN** | `mypy src/` — zero errors |
| ruff | **GREEN** | `ruff check src/` — "All checks passed!" |
| Working tree | **CLEAN** | Only untracked Rosetta files (expected) |

---

## Mateship Review

Checked all M3 teammate work. Key observations:

- **Canyon's d3ffebe** resolved 3 findings in a single commit (F-152, F-145, F-158). This is the focused serial convergence the critical path demanded.
- **Foundation's mateship pickups** (3 commits) saved uncommitted teammate work. F-009/F-144, F-150, quality gate baseline.
- **Bedrock's D-018** finally resolved the finding ID collision problem after 12+ incidents across 3 movements.
- **Circuit's dual contributions** (F-112 auto-resume + F-151 instrument observability) closed two infrastructure gaps.
- **Breakpoint's 4-pass adversarial campaign** (258 tests) found F-200 and F-201 — the same bug class in the same function.
- **Mateship pickup:** Found uncommitted CLI terminology cleanup (recover.py, run.py, validate.py — "job" → "score" in user-facing strings). CLI changes pass mypy. Committed as mateship pickup. Adversarial test file (test_baton_phase1_adversarial.py) was already committed in b5b8857.

---

## Open Risks (Updated)

1. **Demo at zero (CRITICAL — EXISTENTIAL).** 8+ movements. Product invisible.
2. **F-210 blocks baton Phase 1 (CRITICAL — NEW).** Must be fixed first.
3. **Baton untested live (CRITICAL — BLOCKED by F-210).** Was READY, now needs F-210 fix.
4. **F-107 (P0).** No instrument profile verification against live APIs.
5. **Cost fiction (P2).** $0.00–$0.12 for 79+ Opus sheets. 5+ movements open.

---

## Recommendations

1. **Assign F-210 to Foundation or Canyon.** They have the deepest baton knowledge. Estimated ~100-200 lines.
2. **After F-210, Phase 1 testing must happen outside the orchestra.** The conductor running this orchestra cannot be used for testing. Someone (composer or a standalone session) must run `mzt start --conductor-clone && mzt run hello.yaml --conductor-clone` with `use_baton: true`.
3. **Demo needs a deadline.** 8 movements of "assigned but no progress" means the assignment is ineffective. The composer should either do it directly or assign it with a hard deadline.
4. **Accept participation narrowing.** 16/32 is sufficient for the remaining work. The serial path needs 1-2 musicians, not 32.

---

*Report verified against source code on HEAD (fa05e7f). All file paths and line numbers confirmed.*
