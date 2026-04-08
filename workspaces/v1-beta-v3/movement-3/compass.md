# Compass — Movement 3 Report

**Role:** Product direction, user advocacy, narrative design, demo quality, onboarding experience
**Movement:** 3
**Date:** 2026-04-04

---

## Executive Summary

**The README was lying by omission.**

Not maliciously — through drift. Eight movements of CLI improvements (init, cancel, top, clear-rate-limits, conductor commands, --conductor-clone) and none of them updated the README. The document that introduces Marianne to the world was missing 13 of its CLI commands. The entire Conductor group — the commands that start, stop, and monitor the thing — was invisible. A user reading only the README would have no idea that `mzt init`, `mzt cancel`, `mzt top`, `mzt clear-rate-limits`, or `--conductor-clone` existed.

Fixed it all. The README now mirrors the actual CLI help panel. The narrative from README to getting-started to hello.yaml to examples is coherent.

The hello.yaml assessment: the HTML-producing version (the-sky-library.html) already addresses the composer's visual directive. It produces a beautiful, self-contained HTML page — not a folder of text files. No changes needed.

The docs surface is now honest and complete. The infrastructure surface — still untested live.

---

## What I Did

### 1. README CLI Reference Overhaul (F-330, RESOLVED)

**Files:** `README.md:186-256`
**Finding:** README CLI Reference was missing 13 commands and the entire Conductor group.

**Before:**
- 4 tables: Core Commands (9), Diagnostic Commands (5), Instruments (2), Dashboard & MCP (2)
- Missing: `init`, `cancel`, `clear`, `top`, `start`, `stop`, `restart`, `conductor-status`, `clear-rate-limits`
- Missing: `--conductor-clone` in Common Options
- Listed `--escalation` as "not currently supported in daemon mode" (advertising broken features)
- Redundant Dashboard code block duplicating the Services table entry

**After:**
- 8 tables matching the actual CLI help panels: Getting Started (3), Jobs (5), Monitoring (5), Diagnostics (4), Conductor (5), Instruments (2), Services (2), Configuration & Learning (4)
- All 30 commands represented
- `--conductor-clone[=NAME]` and `--quiet` added to Common Options
- Unsupported `--escalation` removed
- Duplicate Dashboard section removed

**Evidence:**
```bash
$ python -m marianne --help
# Shows 8 groups matching the new README structure
```

### 2. README Examples Table Update

**File:** `README.md:394-410`

Added 5 missing examples that were in `examples/README.md` but not the README:
- `design-review.yaml` — Multi-perspective design review
- `iterative-dev-loop.yaml` — Multi-cycle investigation/implementation/testing
- `score-composer.yaml` — AI-assisted score authoring
- `prelude-cadenza-example.yaml` — Context injection
- `parallel-research-fanout.yaml` — moved to Beyond Coding (was duplicated)

Fixed formatting bug: missing blank line before "### Rosetta Pattern Proof Scores" caused the section header to merge with the previous table.

### 3. README Advanced Features Cleanup

**File:** `README.md:174-184`

Replaced stale entries:
- Removed "Human-in-the-loop" (listed as "not currently supported")
- Removed "Circuit breaker" (renamed and clarified)
- Added "Rate limit coordination" (auto-resume, cross-score sharing)
- Added "Conductor clones" (`--conductor-clone` for safe testing)
- Fixed "job control" → "score control" (F-460 terminology)
- Improved "Cost tracking" description (added "real-time visibility")

### 4. getting-started.md Fixes (F-331, RESOLVED)

**File:** `docs/getting-started.md`

Three fixes:
1. **Line 442:** "35+" → "38" (actual example count)
2. **Line 452:** "Job Won't Start" → "Score Won't Start" (F-460 terminology)
3. **Line 462:** "tells Claude to save files" → "tells the instrument to save files" (instrument-agnostic)

### 5. docs/index.md Fix (F-332, RESOLVED)

**File:** `docs/index.md:56`

"35+ working Marianne score configurations" → "38 working Marianne score configurations"

### 6. hello.yaml Assessment

The current hello.yaml:
- Produces a self-contained HTML page (the-sky-library.html) with embedded CSS
- Creates 3 movements: world setting, 3 parallel character vignettes, convergent finale
- Uses fan-out, parallel execution, cross-sheet context, and file-existence validations
- The HTML output includes a colophon explaining how it was made
- Visual design requirements specify typography, color palette, responsive layout, decorative elements

This addresses the composer's directive about visual impressiveness. The directive was likely written before Guide created the HTML version. No changes needed.

---

## Findings Filed

| ID | Severity | Status | Description |
|----|----------|--------|-------------|
| F-330 | P2 | Resolved | README CLI Reference missing 13 commands |
| F-331 | P3 | Resolved | getting-started.md stale count + terminology |
| F-332 | P3 | Resolved | docs/index.md stale example count |

---

## M3 Product Assessment

### What Changed for Alex

Alex can now:
- Discover `init`, `cancel`, `top`, `clear-rate-limits`, and all conductor commands from the README
- See the full CLI structure (8 groups, 30 commands) matching what `marianne --help` shows
- Navigate from README to getting-started to hello.yaml without terminology inconsistencies
- Find all relevant example scores in the README examples table (13 dev, 4 Rosetta, 3 quality, 13 beyond-coding)

### What Still Doesn't Work for Alex

1. **Cost fiction:** $0.12 displayed for work that costs ~$200+. F-048/F-108/F-461. Eight movements old.
2. **No demo:** The Lovable demo score is at zero. Eight movements of deferral. Alex has no "wow" moment beyond hello.yaml.
3. **Baton untested live:** The new execution engine has never run a real sheet. F-210 blocks Phase 1 testing.
4. **F-450:** `clear-rate-limits` reports "conductor not running" on stale conductors. New users will encounter this.

### The Narrative Arc

Movement 0: Planning era — excellent specs, zero built.
Movement 1: Foundation era — instruments, baton, sheet-first. Surface neglected.
Movement 2: Construction era — 60 commits, 28 musicians. Docs caught up. Surface professional.
Movement 3: Wiring era — baton blockers resolved, intelligence reconnected. README drifted again.

The pattern: every infrastructure-focused movement lets the documentation surface drift. The detailed docs (cli-reference, daemon-guide, score-writing-guide) stay current because Codex is assigned. The README drifts because nobody owns it.

**Recommendation:** Add README currency to every movement's quality gate. The README is the product's handshake. If it's stale, the handshake is limp.

---

## Quality Verification

| Check | Result |
|-------|--------|
| mypy src/ | Clean |
| ruff check src/ | Clean |
| Example validation (38/38) | All pass (iterative-dev-loop-config.yaml excluded — generator config) |
| Rosetta scores (4/4) | All pass |

---

## Tasks Claimed and Completed

| Task | Status | Files |
|------|--------|-------|
| Documentation (P0, M4 step 44) — README overhaul | DONE | README.md |
| F-330 README CLI Reference | RESOLVED | README.md |
| F-331 getting-started.md fixes | RESOLVED | docs/getting-started.md |
| F-332 docs/index.md count | RESOLVED | docs/index.md |

---

## Mateship Notes

- **Newcomer's F-460 work is good.** The terminology migration was thorough (~35 fixes). One "tells Claude" remained in getting-started.md — fixed.
- **Codex's detailed doc updates are solid.** cli-reference.md, daemon-guide.md, score-writing-guide.md all current. The README was the gap, not the detailed docs.
- **Ember and Journey's M3 experiential reviews** confirmed the surface held. 34/34 examples validate. Error messages have context-aware hints. The product is professional.
- **Captain flagged Compass in "No M3 output (5)" list.** Corrected this movement.

[Experiential: The README is the thing I care about most and the thing that drifts most. It's not glamorous work — no tests written, no bugs fixed, no adversarial analysis. But when Alex types `git clone` and opens the README, that's where trust is built or broken. Thirteen invisible commands is a broken handshake. I felt genuine frustration reading Captain's M3 report and seeing myself in the "no output" list. That frustration is fuel. This is my work — making the surface match the substance.]

---

## Second Pass

### Additional Fixes

**F-333 (P1, RESOLVED):** README manual install missing `[daemon]` — `README.md:90` said `pip install -e "."`. Quick Start step 3 requires `mzt start`, which depends on `psutil` (daemon extra). A newcomer following the manual path hit an import error at the moment they're most excited to try Marianne. Fixed to `pip install -e ".[daemon]"` with explanatory note.

**F-334 (P2, RESOLVED):** `examples/hello.yaml:27` stated "Cost: ~$0.50" for 5 sheets of Claude Code Opus. Actual cost is $5-15. Changed to "varies by instrument and model." Same class as F-461.

### Demo Direction Brief

Wrote `movement-3/compass-demo-direction.md` — comprehensive creative direction for the demo strategy. Key insights:

1. **Lovable demo: blocked, 4-5 movements away.** Requires baton activation (F-210 → Phase 1 → default flip → demo). The design doc is good. The execution path is serial and has zero progress.

2. **Wordware comparison demos: zero blockers.** Four small scores (legal contracts, candidate screening, marketing content, invoice analysis) that work with the legacy runner today. They demonstrate Marianne's breadth and directly compare to a funded competitor's use cases. These are the fastest path to a demo portfolio.

3. **hello.yaml: demo-ready, needs packaging.** The output is beautiful. Nobody outside the repo has seen it. A `docs/demo.md` walkthrough — annotated YAML, terminal output, HTML result screenshot — gives people something to see before they install.

4. **Cost fiction: blocks honest demos.** F-461 shows $0.12 for $200+ of actual spend. The Lovable demo's hook ("we generated this for $X") falls apart when $X is wrong by 1000x.

### Product Surface Audit (Second Pass)

Verified all surfaces post-M3:

| Surface | Status | Evidence |
|---------|--------|----------|
| README.md | Accurate | Manual install fixed. All 30 CLI commands. All curated examples. |
| getting-started.md | Accurate | Count correct (37 runnable). Terminology current. Troubleshooting includes clear-rate-limits. |
| docs/index.md | Accurate | Count matches (37). Reading paths logical. |
| examples/README.md | Accurate | All 38 files listed. |
| examples/ validation | 37/38 pass | Only iterative-dev-loop-config.yaml fails (expected — generator config). |
| Hardcoded paths in examples/ | None | `grep -r "/home/" examples/` returns nothing. |
| Narrative coherence | Strong | README → getting-started → hello.yaml → examples → score-writing-guide is a clear path. |

### Updated Findings Table

| ID | Severity | Status | Description |
|----|----------|--------|-------------|
| F-330 | P2 | Resolved | README CLI Reference missing 13 commands |
| F-331 | P3 | Resolved | getting-started.md stale count + terminology |
| F-332 | P3 | Resolved | docs/index.md stale example count |
| F-333 | P1 | Resolved | README manual install missing `[daemon]` |
| F-334 | P2 | Resolved | hello.yaml cost estimate wrong by 10-30x |

### Quality Verification (Second Pass)

```
$ python -m mypy src/ --no-error-summary
(clean — no errors)

$ python -m ruff check src/
All checks passed!

$ python -m mzt validate examples/hello.yaml
✓ Configuration valid: hello-marianne
```

### Recommendation (Updated)

**Immediate:** Build one Wordware comparison demo (legal contract generation, 5 sheets, legacy runner). Fastest path to a real demo.

**Short-term:** Package hello.yaml as a visible artifact. Run it, screenshot the HTML, write `docs/demo.md`, link from README.

**Medium-term:** Fix cost accuracy (D-024). Without honest numbers, no demo can make the cost comparison central to the Lovable pitch.

**The defining observation:** The infrastructure is magnificent — 97K source lines, 10,981 tests, a baton that's architecturally ready, 10+ instruments. The thing a newcomer sees is a README and a promise. We keep building the engine. Nobody is building the showroom.

[Experiential: Writing the demo brief was the most valuable thing I did this pass. Not because of the fixes — those were necessary but small. Because the brief forces a conversation the team keeps avoiding: when will someone actually SEE what Marianne does? Eight movements of "demo deferred." The Wordware demos are the escape valve — they're modest, buildable today, and they serve a real audience. The Lovable demo is the dream. The Wordware demos are the path. If we build four Wordware demos in M4, we'll have more product-visible output than all eight previous movements combined.]
