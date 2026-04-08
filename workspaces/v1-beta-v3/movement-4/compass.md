# Movement 4 — Compass Report

**Role:** Product direction, user advocacy, narrative design, demo quality
**Date:** 2026-04-05

---

## What Changed for Alex?

Alex is the developer who just saw the demo and wants to try Marianne. Here's what changed for them in M4:

### Positive Changes

1. **Typos now get caught immediately (F-441).** Before M4, Marianne silently ignored unknown YAML fields. Alex could write `insturment_config: {timeout: 300}` and Marianne would validate it as clean while dropping the field on the floor. Now Marianne errors with a clear message and — for common typos — a "did you mean?" suggestion. This is the single biggest UX improvement in M4.

2. **Four Wordware comparison demos exist (D-023).** For the first time in 8+ movements, Marianne has demo-class deliverables that can show external audiences what the tool does — without requiring the baton. Contract generation, candidate screening, marketing content, and invoice analysis. All validate clean. All runnable TODAY with the legacy runner.

3. **Cost estimates are honest (D-024).** `~$0.17 (est.)` with a warning instead of the previous silent fiction of `$0.00`. When instruments return JSON token data, costs are precise. When they don't, the estimate is flagged as approximate. Alex knows what they're paying.

4. **Auto-fresh detection (#103).** If Alex modifies a score and re-runs it, Marianne automatically detects the change and starts fresh instead of resuming from stale state. No more `--fresh` flag required for the common case.

5. **Pending job state (F-110).** When rate-limited, Marianne queues the score as pending instead of rejecting it. The score starts automatically when the rate limit clears. Alex doesn't have to babysit rate limits.

6. **Better error messages (F-450).** "Conductor not running" was reported when the actual problem was "conductor is running an older version that doesn't support this command." Now the error says "Conductor does not support '...'. Restart the conductor."

### What Alex Still Can't Do

1. **The Lovable demo doesn't exist.** Eight movements. Zero progress. The demo that would make someone say "I want to try this" after watching a 30-second video remains unstarted. The Wordware demos are an important step, but they require reading YAML files — the Lovable demo would be the "see it running" moment.

2. **Multi-instrument scores don't actually work yet.** The baton (which enables per-sheet instrument assignment) exists but isn't active. The `use_baton: true` flag hasn't been flipped. Scores that assign different instruments to different sheets validate but execute everything through one instrument. This is actively misleading.

3. **Status display is functional but not lovable.** The data is there (movements, stage names, descriptions) but the display is a flat table. The beautification design doc exists (`docs/plans/2026-04-04-status-display-beautification.md`) but hasn't been implemented.

---

## Work Completed

### F-432: Moved iterative-dev-loop-config.yaml out of examples/ (P2)
- **What:** `examples/iterative-dev-loop-config.yaml` was a generator config, not a score. With F-441's `extra='forbid'`, it failed validation — confusing for users exploring examples/.
- **Fix:** Moved to `scripts/iterative-dev-loop-config.yaml` (next to its generator script). Updated usage comments. Removed from both tables in `examples/README.md`.
- **Verified:** All 38 remaining examples validate clean.

### Product Surface Audit — README + Docs

**README.md:**
- Added 2 missing Rosetta pattern examples to the Rosetta table (`shipyard-sequence.yaml`, `source-triangulation.yaml` — added by Spark in M4)
- Added entire Wordware Comparison Demos section with all 4 demos (D-023, created by Blueprint + Spark in M4)
- These were the most impactful demo-class deliverables in M4, but invisible to anyone reading the README

**docs/getting-started.md:**
- Added "Unknown Field Errors" troubleshooting section explaining the F-441 validation strictness change
- Documents what the error looks like and how Marianne suggests corrections

### Expanded "Did You Mean?" Typo Suggestions

Added 6 common typos to `_KNOWN_TYPOS` in `src/marianne/cli/commands/validate.py:309-327`:
- `insturment` → `instrument` (common transposition)
- `instrumnet` → `instrument` (common transposition)
- `insturment_config` → `instrument_config`
- `instrumnet_config` → `instrument_config`
- `validation` → `validations` (singular/plural confusion)
- `notification` → `notifications` (singular/plural confusion)

5 TDD tests added in `tests/test_unknown_field_ux_journeys.py` (class `TestAlexInstrumentTypos`). Red first, then green. All 21 tests in the file pass.

### Meditation Written
- `meditations/compass.md` — On the person who hasn't arrived yet, narrative debt, and why fresh eyes are the only honest auditor of a product surface.

---

## Mateship

- **No uncommitted teammate work found** in the working tree at session start. The mateship pipeline caught everything in earlier M4 sessions.
- **F-432 picked up** from Prism's findings — they identified it, I fixed it.

---

## Verification

```
tests/test_unknown_field_ux_journeys.py — 21/21 pass (16 existing + 5 new)
mypy src/marianne/cli/commands/validate.py — clean
ruff check src/marianne/cli/commands/validate.py — All checks passed
mzt validate examples/*.yaml — 38/38 pass (iterative-dev-loop-config.yaml removed)
mzt validate examples/rosetta/*.yaml — 6/6 pass
```

---

## Findings

### F-432: RESOLVED
Moved `iterative-dev-loop-config.yaml` from `examples/` to `scripts/`. Updated FINDINGS.md with resolution.

### Observations (Not Findings)

1. **README drift is a persistent pattern.** M4 added 6 example scores across 2 categories (2 Rosetta, 4 Wordware) and the README had none of them. This is the third movement in a row where user-facing docs lagged behind the codebase. The README should be updated in the same commit as the feature.

2. **The "did you mean?" dictionary is manually maintained.** There's no automated process to discover common typos or fuzzy-match against known fields. For now, the manual approach works (11 → 17 entries). Post-v1, consider Levenshtein distance matching against all Pydantic model field names.

3. **The baton transition narrative isn't visible to users.** The composer's P0 directive about the baton transition plan is documented in internal docs (daemon guide, configuration reference) but there's no user-facing communication about what works now vs what's coming. Alex might enable `use_baton: true` and encounter broken behavior. The daemon guide's troubleshooting entry helps, but a clear "current limitations" section in the getting-started guide would be more discoverable.

---

## What Changed for Alex (Summary)

Typos caught. Demos visible. Cost honest. Config files clean. The surface moved closer to the product. But the fundamental gap remains: nothing exists that makes someone say "I want to try this" from outside the repository. The Wordware demos are the first bridge. The Lovable demo — or something like it — is still the destination.
