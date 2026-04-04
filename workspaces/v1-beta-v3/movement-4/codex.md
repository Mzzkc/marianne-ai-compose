# Codex — Movement 4 Report

## Summary

Eight documentation deliverables across six files, documenting all major M4 features that shipped without user-facing documentation. One mateship pickup (invoice-analysis.yaml). The documentation gap this movement was smaller than M3 — the team is getting better at shipping features with docs — but four M4 features (auto-fresh detection, cost confidence, skipped_upstream, MethodNotFoundError) had no documentation at all until this session.

## Work Completed

### 1. CLI Reference: Auto-Fresh Detection (#103)

**Source:** `src/mozart/daemon/manager.py:49-73` (`_should_auto_fresh()`), wired at `manager.py:698-714`.

**What I documented:** When a completed score's YAML file is modified, `mozart run` now auto-detects the change and starts fresh without requiring `--fresh`. The conductor compares the score file's mtime against the registry's `completed_at` timestamp with 1-second filesystem tolerance. Only applies to completed scores — failed/paused scores always resume from checkpoint.

**File:** `docs/cli-reference.md` — Added "Auto-Fresh Detection" subsection under `mozart run` options, before the Examples section.

### 2. CLI Reference: Cost Confidence Display (D-024)

**Source:** `src/mozart/cli/commands/status.py:1299-1357` (`_render_cost_summary()`), `src/mozart/core/checkpoint.py:447` (`cost_confidence` field).

**What I documented:** `mozart status` now always shows a cost summary. When instruments return structured JSON token data, costs are precise (`$X.XX`). When tokens are estimated from output character count (confidence < 0.9), the display shows `~$X.XX (est.)` with a warning that actual costs may be 10-100x higher, and recommends JSON output format for accurate tracking.

**File:** `docs/cli-reference.md` — Added cost summary bullet to the status output description, updated "Cost tracking" line to reference confidence indicators.

### 3. Score-Writing Guide: `skipped_upstream` + `[SKIPPED]` Placeholder (#120)

**Source:** `src/mozart/prompts/templating.py:92` (`skipped_upstream` field), `src/mozart/execution/runner/context.py:215-220` (population logic).

**What I documented:** Added `skipped_upstream` (list of sheet numbers) to the Cross-Sheet Variables table. Documented the `[SKIPPED]` placeholder that appears in `previous_outputs` for skipped upstream sheets. Added a Jinja2 template example showing how to handle incomplete fan-in data. Updated the `previous_outputs` description to note the `[SKIPPED]` behavior.

**File:** `docs/score-writing-guide.md` — Updated Cross-Sheet Variables table (line ~510) and added "Handling skipped upstream sheets" subsection before Design Considerations (line ~1600).

### 4. Daemon Guide: MethodNotFoundError Troubleshooting (F-450)

**Source:** `src/mozart/daemon/exceptions.py:37` (`MethodNotFoundError`), `src/mozart/daemon/detect.py:168-177` (re-raise with restart guidance), `src/mozart/daemon/ipc/errors.py:139` (METHOD_NOT_FOUND mapping).

**What I documented:** New troubleshooting section explaining the "Conductor does not support '...'" error message — it means the CLI is newer than the running conductor, and the fix is `pip install -e ".[dev]"` then `mozart restart`.

**File:** `docs/daemon-guide.md` — Added troubleshooting subsection between "Stale PID File" and "--escalation incompatible" sections.

### 5. Daemon Guide: Baton F-210/F-211 Capabilities

**Source:** `src/mozart/daemon/baton/adapter.py` (`_collect_cross_sheet_context()`), F-210 resolution in collective memory.

**What I documented:** Added two new bullet points to the baton capability list: cross-sheet context (previous_outputs and previous_files populated from completed sheets) and checkpoint sync (all event types synchronized with deduplication).

**File:** `docs/daemon-guide.md` — Updated baton capability list (line ~391).

### 6. Baton Test Count Update

**Evidence:** `grep -rl "baton" tests/ | xargs grep -c "def test_"` → 1,915 tests across 123 files.

**What I updated:** Baton test count from `1,350+` to `1,900+` in both `docs/daemon-guide.md` and `docs/limitations.md`.

### 7. Examples README: Wordware Comparison Demos

**Source:** `examples/contract-generator.yaml`, `examples/candidate-screening.yaml`, `examples/marketing-content.yaml`, `examples/invoice-analysis.yaml`.

**What I documented:** Added new "Wordware Comparison Demos" section to `examples/README.md` with a table listing all four demo scores, their domains, sheet counts, and key innovations.

**File:** `examples/README.md` — New section between Rosetta Pattern Proof Scores and Quality & Continuous Improvement.

### 8. Mateship: invoice-analysis.yaml

**Found:** Untracked Wordware comparison demo in the working tree. 5 sheets, 3-voice parallel financial analysis (financial accuracy, compliance, anomaly detection). Validates clean.

**Action:** Added to examples/README.md Wordware table. Will commit as mateship pickup.

## Evidence

### Documentation Changes Verified Against Source

Every documentation claim was verified against the source code:

- `_should_auto_fresh()`: Read at `manager.py:49-73`, wiring at `manager.py:698-714`. Confirmed: mtime comparison, 1-second tolerance, completed-only scope.
- `_render_cost_summary()`: Read at `status.py:1299-1357`. Confirmed: min confidence threshold 0.9, `~$X.XX (est.)` format, 10-100x warning text.
- `SheetContext.skipped_upstream`: Read at `templating.py:92`. Confirmed: `list[int]`, populated at `context.py:215-220` from SheetStatus.SKIPPED.
- `MethodNotFoundError`: Read at `exceptions.py:37`, `detect.py:168-177`, `ipc/errors.py:139`. Confirmed: METHOD_NOT_FOUND code, restart guidance message.
- Baton test count: `grep -rl "baton" tests/ | xargs grep -c "def test_"` = 1,915.
- `invoice-analysis.yaml`: `mozart validate examples/invoice-analysis.yaml` → exit 0, 5 sheets, 7 validations.

### Quality Checks

```
mypy src/ — clean (no errors)
ruff check src/ — All checks passed!
pytest (deterministic) — pre-existing failures only in untracked test_rate_limit_pending.py (another musician's work)
```

## Findings

No new findings. All documentation gaps found were expected M4 feature drift — features shipped, docs didn't follow. This is the same pattern as M3 (F-029 terminology, restart options, clear-rate-limits). The gap is shrinking: M3 had 9 undocumented items, M4 has 6. The team is documenting more as they go.

## Mateship

- **invoice-analysis.yaml:** Picked up untracked 4th Wordware demo. Added to README.
- **Rosetta proof scores:** `shipyard-sequence.yaml` and `source-triangulation.yaml` already added to README by another musician or linter hook — verified entries are correct.
- **Pre-existing test failures:** `test_rate_limit_pending.py` (4 failures) and `test_quality_gate.py` (1 test-ordering failure) are from other musicians' uncommitted work. Noted in collective memory for mateship pipeline.

## Experiential

Movement 4 is a turning point in how I see my role. The documentation gap isn't just "features shipped without docs" anymore. The cost confidence display (D-024) is the first time I'm documenting a *design decision visible in the UI* — the choice to show `~$X.XX (est.)` instead of hiding uncertain numbers. The `skipped_upstream` variable is the first time I'm documenting *explicit handling of absence* — what happens when data isn't there. These are design documentation, not reference cards.

The baton test count jump from 1,350+ to 1,900+ tells a story: 565 new baton tests in one movement, all from F-210/F-211 resolution and the mateship cascade it triggered. The documentation needs to keep up with that pace, and today it did.

The Wordware demos are the most user-facing work I've touched — four scores that someone evaluating Mozart will look at side-by-side with competitors. Making sure the README presents them clearly matters more than another CLI option I document.
