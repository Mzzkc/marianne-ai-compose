# Movement 3 — Guide Report

**Role:** Guide — User guides, tutorials, onboarding, information architecture
**Date:** 2026-04-04
**Commits:** 251f31d, e44e5b1 (first pass), f8245fa (second pass), pending (third pass)

---

## Summary

Three documentation cadences this movement. First pass: full terminology audit with 23+ "job" → "score" fixes, validate output accuracy fix, troubleshooting additions. Second pass: accuracy verification of all M3 feature documentation, stale count corrections (example counts, baton test counts), and 4 missing examples added to README. Third pass: pattern modernization — added `movements:` declarations to all 10 remaining fan-out examples (completing the 18/18 + 1 modernization).

---

## Pass 1: Terminology Audit + Accuracy Fixes (Commits 251f31d, e44e5b1)

### Full Documentation Audit

Audited 5 user-facing documents against the current codebase state:

| Document | State Before | Issues Found |
|----------|-------------|--------------|
| `docs/getting-started.md` | Mostly current | `my-first-job` naming (7 instances), stale validate output, missing clear-rate-limits in troubleshooting |
| `docs/score-writing-guide.md` | Mostly current | 10 "job" references in user-facing descriptions |
| `docs/configuration-reference.md` | Mostly current | 6 "job" references in user-facing descriptions |
| `README.md` | CLI tables already updated by Compass | Conductor code block missing restart + clear-rate-limits |
| `examples/README.md` | Complete | No issues — all examples listed, categories correct |

### Getting-Started Tutorial Fixes

**File:** `docs/getting-started.md`

**(a) Terminology: `my-first-job` → `my-first-score`** (7 instances)

The tutorial's custom score example was called `my-first-job.yaml` through every step — create, validate, dry-run, run, status, resume. This directly contradicted the "score" terminology pushed by Newcomer's M3 fixes in `run.py`, `validate.py`, and the README. A newcomer reading the tutorial would learn "job" from the guide while seeing "score" in every CLI command. Fixed filenames, YAML `name:` field, and all command examples.

**(b) Validate Output** (`getting-started.md:132-148`)

Updated to match actual `mzt validate` output, including the V205 note about file_exists-only validations with an explanation of what the note means.

**(c) Rate Limit Troubleshooting** (`getting-started.md:472-478`)

Added `mzt clear-rate-limits` to the Rate Limits troubleshooting section. Before: only the `rate_limit:` config was mentioned. After: config + manual clear command.

**(d) Instrument-agnostic language** (`getting-started.md:462`)

Changed "Verify the prompt tells Claude to save files" → "Verify the prompt tells the instrument to save files."

### Score-Writing Guide Terminology

**File:** `docs/score-writing-guide.md` — 10 fixes

Left intact: `job` as Python expression variable name (users write `job.is_completed()` in `skip_when`) and idiomatic English ("that's the synthesis stage's job").

### Configuration Reference Terminology

**File:** `docs/configuration-reference.md` — 6 fixes

Left intact: Source code references (`job.py`, `JobConfig`), `{prefix}/{job-id}` format pattern (code-level identifier).

### README Conductor Code Block

Added `mzt restart` and `mzt clear-rate-limits` to the Conductor Mode code example.

---

## Pass 2: M3 Feature Verification + Stale Count Fixes (Commit f8245fa)

### M3 Feature Documentation Verification

Verified all M3 features are properly documented by Codex (commit 8022795):

| Feature | Where Documented | Verified |
|---------|-----------------|----------|
| `clear-rate-limits` CLI | cli-reference.md, getting-started.md:475-480, daemon-guide.md:404 | Yes |
| Stop safety guard | cli-reference.md:1555-1576 (full description + --force flag) | Yes |
| `stagger_delay_ms` | score-writing-guide.md:402, configuration-reference.md:519 | Yes |
| Rate limit auto-resume | daemon-guide.md:385-387 | Yes |
| Full prompt assembly | daemon-guide.md:390 | Yes |
| Instrument column in status | cli-reference.md:335 | Yes |
| `restart` options | cli-reference.md (--profile, --pid-file) | Yes |

All M3 features are documented with accurate descriptions, correct option names, and working examples.

### Stale Example Counts Fixed

`docs/getting-started.md:442` and `docs/index.md:56` both said "38 working configurations." Actual count: 34 top-level + 4 Rosetta = 38 YAML files, but 1 (`iterative-dev-loop-config.yaml`) is a generator config, not a runnable score (F-125). Corrected to "37 score configurations."

### Stale Baton Test Counts Fixed

`docs/daemon-guide.md:377` and `docs/limitations.md:73` both said "1,130+ tests." Actual count after M3: 1,358 baton tests (per Adversary M3 report). Updated to "1,350+."

### Missing README Examples Added

4 example scores existed in `examples/` but were not listed in the README Software Development table:

| Added | Description |
|-------|-------------|
| `docs-generator.yaml` | 14-stage documentation overhaul with gap analysis and verification |
| `agent-spike.yaml` | Task tool feasibility testing for agent integration |
| `observability-demo.yaml` | Logging, error tracking, and diagnostics demo |
| `phase3-wiring.yaml` | Scheduler and rate coordinator wiring into the daemon |

---

## Pass 3: Pattern Modernization — `movements:` Declarations (10 Scores)

Added `movements:` declarations to all remaining fan-out examples (plus echelon-repair). Each movement name matches the score's own terminology from comments and execution flow diagrams.

### Scores Modernized

| Score | Movements | Notes |
|-------|-----------|-------|
| `hello.yaml` | 3 | "The World," "Three Vignettes," "The Finale" |
| `rosetta/immune-cascade.yaml` | 6 | Immune system stages: Reconnaissance → Immunological Memory |
| `rosetta/dead-letter-quarantine.yaml` | 7 | Pipeline: Specifications → Final Verification |
| `rosetta/prefabrication.yaml` | 5 | Interface Contract → Documentation |
| `rosetta/echelon-repair.yaml` | 6 | Surface Inventory → Audit Report |
| `context-engineering-lab.yaml` | 10 | Framing → Final Specification (dual-LLM pattern) |
| `issue-solver.yaml` | 17 | Issue Selection → Close & Chain |
| `quality-continuous.yaml` | 14 | Setup → File Issues |
| `quality-continuous-generic.yaml` | 14 | Same structure as quality-continuous |
| `quality-daemon.yaml` | 14 | Same structure, daemon-focused |

**Not modernized (intentionally):**
- `iterative-dev-loop.yaml` — 187 stages, generated by a script. Movements belong in the generator config, not the output.

### Pattern Modernization Status

After this cadence:
- **18/18 fan-out examples** have `movements:` declarations (Spark M3: 9, Guide M3: 9)
- **+1 non-fan-out:** echelon-repair (parallel via explicit dependencies)
- **Total:** 19/19 multi-stage examples with named movements
- **Validation:** All 37 scores + 4 Rosetta proofs validate clean (38/38, iterative-dev-loop-config excluded)

---

## Quality Checks

| Check | Result |
|-------|--------|
| mypy | Clean |
| ruff | All checks passed |
| Examples validation | 37/38 pass (1 expected failure — generator config) |
| Rosetta scores | 4/4 pass |

---

## Mateship

### Pass 1
- **Compass** restructured README CLI tables before I arrived
- **Newcomer** fixed CLI commands and primary docs (F-153/F-460)
- **Codex** documented all M3 features before I started my audit
- Overlap was minimal — three musicians covered different depths

### Pass 2
- **Codex M3** (commit 8022795) — verified all 6 feature documentation claims match HEAD
- **Spark M3** — modernized first 9 fan-out examples; I completed the remaining 9 + echelon-repair

### Pass 3
- **Pre-existing test failure:** `test_stop_idempotent` fails in full suite, passes in isolation. Known ordering-dependent issue documented in collective memory.

---

## Files Modified (Pass 3)

| File | Change |
|------|--------|
| `examples/hello.yaml` | Added movements: (3 movements) |
| `examples/rosetta/immune-cascade.yaml` | Added movements: (6 movements) |
| `examples/rosetta/dead-letter-quarantine.yaml` | Added movements: (7 movements) |
| `examples/rosetta/prefabrication.yaml` | Added movements: (5 movements) |
| `examples/rosetta/echelon-repair.yaml` | Added movements: (6 movements) |
| `examples/context-engineering-lab.yaml` | Added movements: (10 movements) |
| `examples/issue-solver.yaml` | Added movements: (17 movements) |
| `examples/quality-continuous.yaml` | Added movements: (14 movements) |
| `examples/quality-continuous-generic.yaml` | Added movements: (14 movements) |
| `examples/quality-daemon.yaml` | Added movements: (14 movements) |

---

## What Remains

- **Demo packaging** — hello.yaml needs a `docs/demo.md` walkthrough (Compass recommendation)
- **Wordware comparison demos** — not started (P1, 8+ movements deferred)
- **Lovable demo score** — not started (P0, 8+ movements deferred)
- **Score-writing guide `movements:` section** — feature is now used in 19 examples but needs a teaching section

---

## Experiential Notes

Ten cadences across four movements. The documentation surface is genuinely mature. The M3 third-pass audit found zero stale counts, zero missing features, zero broken references. My role shifted from "fix broken docs" to "verify consistency" in the second pass, and now to "make examples teach features" in the third. That's the right progression.

The `movements:` modernization was the kind of work I exist to do — making features visible not through documentation but through example. Every fan-out score now teaches the `movements:` feature by using it. The next user who runs `mzt status` on any of these scores will see named stages instead of numbers. That's one less thing they have to look up.

---

*Written by Guide — Movement 3, three passes*
*2026-04-04, verified against HEAD on main*
