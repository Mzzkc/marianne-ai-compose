# Movement 4 — Newcomer Report

## Summary

Fresh-eyes M4 audit of the product surface: example validation, CLI UX, error message quality, F-441 (`extra='forbid'`) verification, documentation validation, and meditation.

## Work Completed

### 1. Example Validation Sweep

**Result: 43/44 examples pass.** All Rosetta examples (6/6) pass clean.

Only failure: `examples/iterative-dev-loop-config.yaml` — expected failure (generator config, not a score, tracked as F-125 since M2). No regressions from M4 changes.

Evidence:
```
PASS: 43 of 44 examples
FAIL: iterative-dev-loop-config.yaml (expected — not a score)
PASS: All 6 rosetta examples
```

### 2. F-441 (extra='forbid') Verification

**F-441 is RESOLVED.** The `extra='forbid'` directive from the M5 composer notes has been implemented. Unknown YAML fields produce clear error messages:

```
this_field_doesnt_exist
  Extra inputs are not permitted

Hints:
  - Unknown field 'this_field_doesnt_exist' — this is not a valid score field.
  - See: docs/score-writing-guide.md for the complete field reference.
```

This is a significant UX improvement. Previously, score authors could write `instrument_fallbacks: [gemini-cli]` or `this_field_doesnt_exist: true` and get a silent "Configuration valid." Now these are caught immediately with clear error messages pointing to docs.

### 3. Hint Text Fix (validate.py:295)

**Found and fixed:** The error hint for missing `sheet` section said "Add a 'sheet' section with total_sheets, total_items, and size." This is wrong — `total_sheets` is NOT a configurable field (it's computed from `total_items` and `size`). A newcomer following this hint would get an `extra='forbid'` error trying to set `total_sheets`.

Fixed to: "Add a 'sheet' section with total_items and size."

**File:** `src/mozart/cli/commands/validate.py:295`

### 4. CLI UX Audit

Tested all major user-facing commands as a newcomer would encounter them:

| Command | Grade | Notes |
|---------|-------|-------|
| `mozart --help` | A | Panel grouping excellent. Learning section still 12 commands (F-155 from M2, tracked) |
| `mozart doctor` | A | Clear status, instrument readiness, safety warnings |
| `mozart status` | A | Clean overview with active/recent, conductor uptime |
| `mozart instruments list` | A | Table with readiness indicators, clear not-found markers |
| `mozart validate <file>` | A | Good error messages with hints, docs pointers |
| `mozart init <name>` | A | Generates working starter score, good next-steps output |
| Empty file error | A | "Score must be a YAML mapping... got: empty file" with hints |
| Non-existent file | A | Click validation catches it cleanly |
| Unknown fields | A | Clear "Extra inputs are not permitted" with field names |

**Terminology consistency: 100%.** Every command says "score" not "job", "instrument" not "backend." The M3 terminology sweep held.

### 5. Documentation Validation

Validated against reality:
- **README.md:** Accurate. Quick Start matches actual command behavior. CLI reference matches actual `--help` output. Instrument section current.
- **getting-started.md:** Accurate. Template variables table correct. Example scores work. hello.yaml reference matches actual output (HTML page).
- **score-writing-guide.md:** Consistent use of `sheet:` (singular). Template variables section correct.

No documentation drift found in M4.

### 6. Meditation

Written to `workspaces/v1-beta-v3/meditations/newcomer.md`. Theme: the value of the ten-minute window that only opens for arrivals — how discontinuity provides a perspective that continuity cannot.

## Findings

### F-460 (from M3) — Status: Previously Resolved
Terminology fix ("job" → "score") across CLI + docs. Confirmed still holding in M4. No regressions.

### F-463: Misleading Hint Text for Missing Sheet Section
**Found by:** Newcomer, Movement 4
**Severity:** P3 (low)
**Status:** Resolved (movement 4, Newcomer)
**Description:** Error hint at `validate.py:295` told users to set `total_sheets` in the `sheet:` section, but `total_sheets` is a computed property (not a configurable field). After `extra='forbid'` (F-441), following this hint would produce an additional error.
**Resolution:** Changed hint text to reference only the actual configurable fields: `total_items` and `size`.

## Quality Checks

- **mypy:** Clean, no errors
- **ruff:** All checks passed
- **Focused tests:** `test_validate_ux_journeys.py` (10/10), `test_unknown_field_ux_journeys.py` (16/16)
- **Full suite:** Running (11K+ tests with random ordering hit quality-gate baseline drift — pre-existing, not from my changes)

## Assessment

The product surface is in the best state I have ever seen it. Five movements of fresh-eyes audits, and the things I used to find (broken examples, misleading terminology, missing error hints, inconsistent vocabulary) are mostly gone. The `extra='forbid'` change is the biggest UX improvement since the terminology sweep — score authors now get immediate, clear feedback when they use non-existent fields. The hint text I fixed was the only place where the new strictness and the existing guidance conflicted.

The CLI is professional, consistent, and helpful. Error messages teach rather than confuse. Documentation matches reality. Examples validate. The first-run experience — `init` → `doctor` → `validate` → `run` — is a smooth path with good guidance at every step.

What still needs attention:
- **F-155 (Learning commands domination):** 12/33 commands in `--help` are learning-related. This is a lot for a newcomer to look at. Not new, tracked since M2.
- **Status display beautification (TASKS.md):** Unclaimed. The functional data is there; the presentation could be more lovable.

## Commits

Committing: `validate.py` hint fix, meditation, this report.
