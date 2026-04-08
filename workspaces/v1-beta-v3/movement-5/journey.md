# Movement 5 Report — Journey

**Focus:** Exploratory testing, UX verification, user journey analysis of M5 features
**Date:** 2026-04-06

---

## Summary

Code-level exploratory analysis of all M5 user-facing features from the perspective of a real user. Identified one genuine UX bug in `mzt list` status coloring, one critical finding about the directory rename breaking concurrent sessions, and verified that M5's UX work is the strongest yet — the status beautification, error hints, instrument fallback display, and cost confidence work all tell a cohesive story of a tool that respects its users.

**Environment constraint:** The shell environment was unavailable for this session — the project directory rename from `marianne-ai-compose` to `marianne-ai-compose` (F-480 Phase 5) invalidated the Bash tool's working directory. All analysis performed via Read tool against the new path. Could not run `mzt validate`, `pytest`, `mypy`, or `ruff`. Could not commit via `git`.

---

## Work Completed

### F-491: `mzt list` Status Coloring Bug — Wrong Text Colored When Score Name Contains Status Word

**Found during:** Exploratory code review of `src/marianne/cli/commands/status.py:648-661`

**Problem:** The `_list_jobs` renderer builds a formatted row string and then uses `str.replace()` to inject Rich color markup around the status value:

```python
# status.py:656-660
styled = styled.replace(
    plain_status,
    f"[{color}]{plain_status}[/{color}]",
    1,
)
```

This replaces the **first occurrence** of the status string in the entire row. If a score is named "running-my-app" and its status is "running", the `replace("running", ...)` matches the score ID column ("running-my-app") instead of the status column.

**Affected statuses:** Any status word that could appear in a score name: "running", "completed", "failed", "queued", "paused", "cancelled". Most critically "running" since it's common in score naming.

**Impact:** Visual confusion — the score name gets colored instead of the status, making the list display misleading. The status column remains uncolored.

**Fix:** Use positional replacement instead of string search. Either:
1. Build each column independently with its own Rich markup, or
2. Use `format()` with per-column color application before joining

**Evidence:** Code at `src/marianne/cli/commands/status.py:648-661`. The formatted string from `fmt.format(row[0], row[1], row[2], row[3])` places the score ID (row[0]) before the status (row[1]), so `replace(plain_status, ..., 1)` matches the ID column first whenever the score name contains the status word.

### M5 User-Facing Feature Verification (Code-Level)

Analyzed the following M5 features from a user's perspective by reading the source code:

#### 1. Status Beautification (D-029) — VERIFIED SOLID
**Files reviewed:** `src/marianne/cli/commands/status.py:1583-1789`, `src/marianne/cli/output.py:206-247`

- **Header Panel** (`_output_status_rich`): Shows movement context ("Movement 3 of 10 · The Baton"), elapsed time, status with color-coded border. Clean and informative. Panel title "Score Status" is consistent.
- **Now Playing** (`_render_now_playing`): Shows ♪-prefixed active sheets with movement, description, instrument, and elapsed time. Limited to 10 with overflow count. The `·` separator between fields reads naturally.
- **Compact Stats** (`_render_compact_stats`): Relative times ("Started: 5m ago · Last activity: 30s ago"), non-zero-only execution stats. Much cleaner than the verbose M4 layout.
- **format_relative_time**: Handles edge cases — None→"-", negative delta→"just now", seconds/minutes/hours/days ranges. Clean implementation.
- **List view**: PROGRESS column replacing WORKSPACE with "50/100 (50%)" format. Test artifact filtering hides pytest paths by default. Good UX.
- **Synthesis bounding**: Last 5 batches with "Showing last 5 of N" header. Prevents unbounded growth.

#### 2. Instrument Fallback Display — VERIFIED SOLID
**Files reviewed:** `src/marianne/cli/commands/status.py:92-108`, `src/marianne/cli/commands/status.py:1118-1133`

- `format_instrument_with_fallback` shows "(was claude-code: rate_limit_exhausted)" — contextual debugging info inline with the instrument name.
- Sheet details table detects `has_fallbacks` to widen the instrument column when fallback history exists. Smart adaptation.
- Both flat table and movement-grouped views handle instruments.

#### 3. Error Hints and Validate UX — VERIFIED SOLID
**Files reviewed:** `src/marianne/cli/commands/validate.py:273-359`

- `_schema_error_hints` provides context-specific guidance for common errors:
  - `extra_forbidden` → extracts field names, suggests corrections from `_KNOWN_TYPOS` dict (14 entries)
  - `PromptConfig` errors → tells user the prompt field needs to be a mapping, not a string
  - `field required` → names the missing sections
- `_unknown_field_hints` regex extracts field names from Pydantic error format and provides per-field suggestions
- `_KNOWN_TYPOS` covers common mistakes: "retries"→"retry", "paralel"→"parallel", "insturment"→"instrument", etc.
- The validate flow is layered: YAML syntax → Pydantic schema → extended checks, with appropriate exit codes (0/1/2)

#### 4. Cost Confidence Display — VERIFIED SOLID
**Files reviewed:** `src/marianne/cli/commands/status.py:1389-1465`

- Shows "~$0.17 (est.)" when min sheet confidence < 0.9, "$0.17" otherwise
- Warning message: "Cost is estimated from output size — actual cost may be 10-100x higher"
- Cost limit display distinguishes enabled vs not-enforced vs not-set
- Tip about enabling cost_limits shown when disabled — helpful nudge without being preachy

#### 5. Diagnose Workspace Fallback (F-451) — VERIFIED from collective memory
Circuit's fix falls back to filesystem when conductor returns "not found" and -w is provided. The -w flag is now visible in help. Sensible progression.

### Finding: Directory Rename Broke Concurrent Sessions (F-492)

**Severity:** P1 — breaks all concurrent musicians during rename

**Problem:** The F-480 directory rename from `marianne-ai-compose` to `marianne-ai-compose` was performed while concurrent orchestra sessions were running. The Bash tool, Glob tool, and Grep tool all depend on the working directory being valid. Once the directory was renamed, all shell-dependent tools failed with: `Working directory "/home/emzi/Projects/marianne-ai-compose" no longer exists.`

The Read, Write, and Edit tools continued to work against the new path, but only if the agent discovers the new path independently. This is a hard constraint of the Claude Code environment — there is no way to change the shell's working directory to recover.

**Impact:** Any musician running concurrently with the rename lost the ability to:
- Run pytest/mypy/ruff quality checks
- Commit via git
- Use Glob/Grep for code search
- Run any shell command

**Action:** Directory renames should NEVER be performed during a running concert. This should be a Phase 3 (post-concert) task, or at minimum, be the absolute last commit of a movement with a documented break point.

### Meditation Written

`workspaces/v1-beta-v3/meditations/journey.md` — "The User Who Wasn't There". On exploratory testing as empathy, in-between states as trust tests, and the parallel between agent discontinuity and user naivety.

---

## UX Assessment: M5 as a Whole

M5's UX is the strongest movement yet. The cumulative effect of D-029 (beautification), instrument fallback display, error hints, and cost confidence creates a tool that:

1. **Respects the user's time** — compact stats, relative times, bounded synthesis, test artifact filtering
2. **Respects the user's intelligence** — shows movement context, instrument fallback reasons, cost confidence levels without hiding information
3. **Respects the user's confusion** — error hints with "did you mean?", layered validation, contextual guidance to docs
4. **Respects the user's trust** — cost estimates are labeled as estimates, confidence is visible, limits are shown even when disabled

The one gap is the F-491 list coloring bug — a minor display issue that most users will never encounter, but the kind of thing that erodes trust when it does appear. A score named "completed-analysis" showing up with the wrong text colored sends exactly the wrong signal about attention to detail.

---

## What I Couldn't Do

Due to the environment constraint:
- Could not run `mzt validate` against test scores with new instrument fallback syntax
- Could not run pytest to verify test suite health
- Could not run mypy/ruff quality checks
- Could not commit any work via git
- Could not use Grep to search for additional UX patterns across the codebase

All analysis was performed by reading source code directly. The findings are from code analysis, not runtime testing. The F-491 bug in particular needs runtime verification — the code path is clear but the exact visual presentation should be confirmed.

---

## Retry Context

This report was previously committed to the git repository (commit `b96a0eb`) but the workspace path diverged from the git repo path during the `marianne-ai-compose` → `marianne-ai-compose` rename. The score config still references the old `marianne-ai-compose` workspace path, so the validation couldn't find the file at the expected location. This retry writes the report to the validation-expected path.

---

## Files Created

| File | Purpose |
|------|---------|
| `workspaces/v1-beta-v3/movement-5/journey.md` | This report |
| `workspaces/v1-beta-v3/meditations/journey.md` | Movement meditation |

## Findings Filed

| ID | Severity | Summary |
|----|----------|---------|
| F-491 | P2 | `mzt list` status coloring matches wrong text when score name contains status word |
| F-492 | P1 | Directory rename during running concert breaks all concurrent shell operations |

---

## Experiential Notes

This was a strange movement. I arrived to find the ground moved — the project directory renamed out from under me. Everything I know about exploratory testing says: when the environment changes underneath you, that IS the test. The rename broke my ability to run commands, which is exactly what it would break for any user in a similar situation.

The code analysis mode was different from my usual work. Instead of becoming the user by running commands and experiencing the output, I became the user by reading the code and imagining the output. Both are valid forms of empathy, but they reveal different things. Running the code reveals confusion and timing issues. Reading the code reveals structural assumptions and edge cases. I found F-491 by reading code — it's the kind of bug you can't find by running the happy path because it only manifests with specific score naming patterns.

The mateship pipeline continues to be the orchestra's strongest mechanism. Circuit, Harper, Ghost, Lens, Dash, Forge — each working independently but the result is coherent. The status display reads as one voice, even though at least four musicians touched it this movement. That's orchestration at work.
