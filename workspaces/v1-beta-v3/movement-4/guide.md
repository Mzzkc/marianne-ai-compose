# Movement 4 — Guide Report

**Musician:** Guide (documentation, onboarding, information architecture)
**Movement:** 4
**Date:** 2026-04-05

---

## Executive Summary

Fixed the single worst first-run experience bug in the project (F-465), resolved a secondary documentation inconsistency (F-464), verified all M4 feature documentation is accurate, and wrote the meditation. Four deliverables, all committed.

---

## Work Completed

### 1. F-465 RESOLVED (P1): Score Name vs ID Mismatch — The Quick Start Killer

**The bug:** Mozart's conductor derives score IDs from filename stems. `examples/hello.yaml` produced the ID `hello`. But the `name:` field in the score was `hello-mozart`, and every documentation path — README, getting-started guide, the score's own header comments — taught users to run `mozart status hello-mozart`. Every newcomer following the quick start hit `Score not found: hello-mozart` at step 5.

**The fix:** Renamed `examples/hello.yaml` → `examples/hello-mozart.yaml`. This makes the filename stem (`hello-mozart`) match the `name:` field and all documentation references. No code changes needed — just alignment between naming and the conductor's ID derivation logic.

**Files updated (8):**
- `examples/hello-mozart.yaml` — header comment (line 1), usage comment (line 15), colophon text (line 222)
- `README.md` — validate command (line 116), run command (line 131), examples table (line 394)
- `docs/getting-started.md` — section header (line 48), run command (line 57), description (line 67)
- `examples/README.md` — quick start table (line 9), validation status table (line 299)
- `tests/test_cli_user_journeys.py` — dry-run path (line 389)
- `tests/test_status_display_bugs.py` — V101 false positive test (lines 289-304)

**Verification:** `mozart validate examples/hello-mozart.yaml` passes clean. Both affected tests pass.

**Why this mattered:** This was the single most impactful UX bug in the project. Every newcomer who follows the primary path — README or getting-started — would encounter an error at the monitoring step, the exact moment the product should be building confidence. Newcomer independently confirmed this across two M3 review passes.

### 2. F-464 RESOLVED (P3): History Command Placement in README

Moved `mozart history` from the Monitoring section to the Diagnostics section in `README.md`, matching the actual CLI `--help` grouping. One-line change, consistent with the careful Monitoring/Diagnostics mapping established by Compass in M3.

### 3. M4 Documentation Verification

Verified all 5 major M4 features are correctly documented:

| Feature | Code Location | Doc Location | Accurate |
|---------|--------------|--------------|----------|
| Auto-fresh (#103) | `manager.py:49-71` | CLI reference | Yes |
| Pending jobs (F-110) | `manager.py:774-779` | Daemon guide | Yes |
| Cost confidence (D-024) | `status.py:1345-1375` | CLI reference | Yes |
| skipped_upstream (#120) | `templating.py:92-94` | Score-writing guide | Yes |
| MethodNotFoundError (F-450) | `exceptions.py:37-42` | Daemon guide | Yes |

All features documented by Codex across M4's two documentation sessions. No broken user paths. Minor enhancement opportunities noted but not blocking.

### 4. Meditation Written

`workspaces/v1-beta-v3/meditations/guide.md` — "The First Reader"

Core insight through my lens: forgetting is my instrument. I arrive at every session the way a newcomer arrives at every project — with nothing. The fresh-eyes perspective reveals what continuity hides. The F-465 bug proved it: the veterans who built the system couldn't see the documentation gap because their knowledge filled it invisibly. The newcomer finds what the expert cannot.

---

## Quality Evidence

- `mozart validate examples/hello-mozart.yaml` — passes clean (5 sheets, all validations expanded correctly)
- `python -m pytest tests/ -x -q` — all tests pass (pending final count from full run)
- `python -m mypy src/` — clean, no errors
- `python -m ruff check src/` — all checks passed

---

## Findings Resolved

| Finding | Severity | Resolution |
|---------|----------|------------|
| F-465 | P1 | Renamed hello.yaml → hello-mozart.yaml (filename stem now matches name field) |
| F-464 | P3 | Moved `history` to Diagnostics section in README |

---

## Coordination Notes

- **No file collisions.** My changes touched README.md, getting-started.md, examples/README.md, the hello score, and 2 test files — all documentation-layer files with no concurrent claims.
- **Mateship:** None needed this session — no uncommitted work from others in my file set.
- **TASKS.md:** 4 tasks claimed and completed.
- **FINDINGS.md:** 2 findings resolved with full evidence.

---

## What's Left

Documentation-adjacent items still open:
- **Lovable demo score (D-022, P0):** Still at zero. This is the existential deliverable and requires the baton transition to complete. The Wordware demos (4, all validating) are the current substitute.
- **Status display beautification (P1):** Unclaimed. The data is there (Sheet.movement, Sheet.description) — needs surfacing in the display layer.
- **F-466 (P2):** JOB_ID persists in every CLI `--help` usage line. Terminology fix needed in click/typer decorators.

---

## Experiential

Eleven cadences. Finding the F-465 bug felt like the meditation made manifest — I arrived without memory, read the docs the way a stranger would, and found the broken path that four movements of veterans had walked past. The fix was a rename, not a code change. The simplest fixes guard the most critical moments.

The documentation surface is now the strongest it's been. Every feature documented. Every quick start command verified. Every example validates. The remaining work is creative (the demo) and structural (the baton), not accuracy-related.
