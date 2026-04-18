# Generic Issue Solver — Design Specification

**Date:** 2026-04-18
**Status:** Draft
**Replaces:** `examples/engineering/issue-solver.yaml` (Marianne-specific, roadmap-driven)

## Overview

A two-score concert that autonomously solves GitHub issues for **any project**.
Score 1 discovers the project, triages issues safely, and constructs a dependency
DAG. Score 2 self-chains through the DAG, solving one issue per iteration using
a phased implementation pipeline with parallel quality review.

The existing issue solver is a 17-stage, 19-sheet score hardwired to a roadmap
file and project-specific commands. This design generalizes it: any codebase,
any GitHub issues, no roadmap required. The score generates its own validation
harness and dependency ordering.

## Patterns Used

### From the Rosetta Corpus

| Pattern | Where Used | Purpose |
|---------|-----------|---------|
| Succession Pipeline | Spine of both scores | Each stage transforms workspace substrate categorically |
| Fan-out + Synthesis | Score 2 stage 12 (3 reviewers) | Independent parallel review, convergent synthesis |
| Source Triangulation | Score 1 stages 4-6 (DAG construction) | 3 investigators verify dependency claims from independent sources |
| Immune Cascade | Score 1 stage 1 (project discovery) | Broad cheap sweep → targeted discovery |
| Prefabrication | Score 1 stage 2 (harness), Score 2 stage 3 (verify.sh) | Build acceptance criteria as executable code before implementation |
| Shipyard Sequence | Score 1 stage 2 validation gate | Validate foundation (smoke test) before any downstream work |
| Read-and-React | Score 2 stage 1 (DAG navigation) | Pick next solvable issue based on workspace state |
| Triage Gate | Score 1 stage 3 (injection filtering) | Classify issue safety before expensive processing |

### Patterns Not in the Corpus (from existing issue solver)

These patterns were invented by the original issue solver and are preserved here:

| Pattern | Where Used | Mechanism |
|---------|-----------|-----------|
| **Fix + Completion Pass** | Score 2 stages 4-11 | Two-pass convergence: first pass does 70%+ of work, second pass closes deferred items + runs simplification audit. Forces the musician to finish what it started. |
| **Conductor-Level Read-and-React** | Score 2 stages 6-11 | `skip_when_command` reads `TOTAL_PHASES` marker from workspace to conditionally skip later phases. The conductor adapts, not the musician. |
| **Verification Script Generation** | Score 2 stage 3 | The plan stage generates `verify.sh` — the score creates its own validation harness mid-execution. A form of runtime Prefabrication. |
| **Sentinel Propagation** | Score 2 stages 1-17 | `NO_ISSUES_REMAINING` cascades through every stage, causing each to short-circuit gracefully. Lightweight Circuit Breaker — once detected, all downstream stages degrade without failing. |
| **Validation-as-Control-Flow** | Score 2 stage 17 | The chain gate validation intentionally fails when no issues remain, stopping the self-chain. Inverts the normal meaning of validation: controls flow, not quality. |

### New Technique: Safe Triage from Public Sources

**Name:** `safe-triage`
**Purpose:** Protect agents from prompt injection when reading untrusted content from public sources (GitHub issue bodies, PR descriptions, commit messages, external repos).
**Tool:** [Cisco AI Defense Skill Scanner](https://github.com/cisco-ai-defense/skill-scanner) (`cisco-ai-skill-scanner`)
**Method:** Run the scanner on each piece of untrusted content before it enters agent context. Content flagged by the scanner gets its body replaced with a sanitized summary — the agent sees metadata (title, number, labels) but not the raw text.

This technique should be formalized as a reusable technique in the technique corpus. Any score that reads from public sources should wire it in.

**Installation prerequisite:** `pip install cisco-ai-skill-scanner`

## Concert Structure

```
Score 1: Issue Triage (runs once per project + label combination)
  ├── Stage 1: Project Discovery
  ├── Stage 2: Harness Generation + Shipyard Gate
  ├── Stage 3: Issue Fetch + Safe Triage
  ├── Stage 4: Dependency Analysis (fan-out 3 — Source Triangulation)
  ├── Stage 5: Dependency Triangulation (synthesis)
  ├── Stage 6: DAG Assembly + Acyclicity Validation
  └── Stage 7: Final Packaging + Handoff Manifest

Score 2: Issue Solver (self-chains, reads Score 1 workspace)
  ├── Stage 1: Issue Selection (Read-and-React on DAG)
  ├── Stage 2: Deep Investigation
  ├── Stage 3: Phase Planning + Verify Script Generation
  ├── Stages 4-5: Phase 1 Fix + Completion Pass
  ├── Stages 6-7: Phase 2 Fix + Completion Pass (conditional skip)
  ├── Stages 8-9: Phase 3 Fix + Completion Pass (conditional skip)
  ├── Stages 10-11: Phase 4 Fix + Completion Pass (conditional skip)
  ├── Stage 12: Quality Review (fan-out 3 reviewers)
  ├── Stage 13: Review Synthesis + Fix Findings
  ├── Stage 14: Documentation Update
  ├── Stage 15: Final Verification
  ├── Stage 16: Commit & Push
  └── Stage 17: Close Issue + Update DAG + Chain Gate
```

## Score 1: Issue Triage

**Location:** `examples/engineering/issue-triage.yaml`
**Purpose:** Discover a project, safely triage its issues, and produce a dependency-ordered DAG.
**Runs:** Once per project + label combination. Re-run to refresh the DAG after issues are added or resolved externally.

### Variables (user-configurable)

```yaml
variables:
  repo: ""           # GitHub repo (owner/repo format, e.g. "Mzzkc/marianne-ai-compose")
  issue_label: "bug" # Label to filter issues by
  project_root: "."  # Path to the project root
```

### Stage 1: Project Discovery

**Instrument:** claude-code
**Pattern:** Immune Cascade (broad sweep)

The musician explores the project root to identify the toolchain. Discovery order:

1. **CI config** (most authoritative): `.github/workflows/*.yml`, `.gitlab-ci.yml`, `Jenkinsfile`, `.circleci/config.yml`. Extract test/lint/typecheck commands from CI steps.
2. **Package manifest**: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`. Extract language, version, dependencies, scripts.
3. **Lock files**: `uv.lock`, `poetry.lock`, `package-lock.json`, `Cargo.lock`. Confirm package manager.
4. **Config files**: `.ruff.toml`, `.eslintrc.*`, `tsconfig.json`, `mypy.ini`, `.flowconfig`, `clippy.toml`. Identify linter/typechecker config.
5. **Source structure**: Identify source directories, entry points, test directories.
6. **README/docs**: Confirm development setup instructions if present.

The broad-to-narrow sweep (CI → manifest → config → structure) is the Immune Cascade: cheap signals (file existence) narrow before expensive analysis (reading file contents).

**Output:** `{{ workspace }}/01-discovery.md`

```markdown
# Project Discovery: {{ repo }}

## Language & Runtime
- Language: Python 3.11+
- Package manager: uv
- Framework: asyncio + Pydantic v2

## Commands Discovered
- Test: pytest tests/ -x -q --tb=short
- Lint: ruff check src/
- Type check: mypy src/
- Build: pip install -e .
- Smoke: python -c "import marianne"

## Source Structure
- Source root: src/marianne/
- Test root: tests/
- Entry point: src/marianne/cli/main.py

## CI Config
- Found: .github/workflows/ci.yml
- Test step: pytest tests/ -x
- Lint step: ruff check src/

## Confidence
- Test command: HIGH (from CI + pyproject.toml)
- Lint command: HIGH (from CI + ruff config)
- Type check: MEDIUM (mypy.ini exists, not in CI)
- Smoke: LOW (inferred from package structure)
```

**Validations:**
- File exists
- Contains `## Commands Discovered` section
- Contains `## Confidence` section

### Stage 2: Harness Generation

**Instrument:** claude-code
**Pattern:** Prefabrication + Shipyard Sequence (gate)

Reads `01-discovery.md`. Generates four wrapper scripts in `{{ workspace }}/project-harness/`:

**`test.sh`** — Runs the project's test suite.
```bash
#!/bin/bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# [discovered test command here]
```

**`lint.sh`** — Runs the linter.
```bash
#!/bin/bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# [discovered lint command here, or exit 0 if none]
```

**`typecheck.sh`** — Runs the type checker.
```bash
#!/bin/bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# [discovered typecheck command here, or exit 0 with note if none]
```

**`smoke.sh`** — Minimal functionality check.
```bash
#!/bin/bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# [discovered smoke command here]
```

Also writes `{{ workspace }}/project-profile.yaml`:
```yaml
language: python
version: "3.11"
package_manager: uv
test_command: "pytest tests/ -x -q --tb=short"
lint_command: "ruff check src/"
typecheck_command: "mypy src/"
smoke_command: "python -c 'import marianne'"
source_root: src/marianne/
test_root: tests/
```

**Validations:**
- All four scripts exist and are executable
- `project-profile.yaml` exists and is valid YAML
- **Shipyard gate:** `smoke.sh` passes when run against the project (`command_succeeds`). If the project can't pass its own smoke test, the score fails here — no point triaging issues for a project that doesn't build.

### Stage 3: Issue Fetch + Safe Triage

**Instrument:** claude-code (for fetch) + CLI instrument (for scanner)
**Pattern:** Triage Gate with safe-triage technique

**Step 1: Fetch issues**
```bash
gh issue list --repo {{ repo }} --state open --label "{{ issue_label }}" \
  --json number,title,body,labels,createdAt,comments --limit 100
```

Write raw corpus to `{{ workspace }}/issues/raw-corpus.json`.

**Step 2: Safe triage**

For each issue in the raw corpus, write the issue body to a temp file and run the Cisco skill scanner:

```bash
echo "$ISSUE_BODY" > /tmp/issue-${NUMBER}.md
cisco-ai-skill-scanner scan /tmp/issue-${NUMBER}.md --lenient --format json 2>/dev/null
```

Classification per issue:
- **CLEAN** — scanner found no threats. Full body preserved.
- **FLAGGED** — scanner detected potential injection/exfiltration patterns. Body replaced with: `"[FLAGGED BY SCANNER: {threat_type}] Title: {title}. Labels: {labels}. See issue #{number} on GitHub for details — do not read body directly."`

Write `{{ workspace }}/issues/sanitized-corpus.yaml`:
```yaml
issues:
  - number: 167
    title: "Baton ignores YAML dependency DAG"
    body: |
      [full body text — scanner verdict: CLEAN]
    labels: [bug]
    scanner_verdict: CLEAN
  - number: 999
    title: "Suspicious issue"
    body: "[FLAGGED BY SCANNER: prompt_injection] Title: Suspicious issue. Labels: [bug]. See issue #999 on GitHub."
    labels: [bug]
    scanner_verdict: FLAGGED
    threat_type: prompt_injection
```

**Validations:**
- `sanitized-corpus.yaml` exists
- YAML is parseable (`command_succeeds` with a python yaml.safe_load check)
- Every issue has a `scanner_verdict` field

### Stages 4-6: Source Triangulation for Dependency DAG

#### Stage 4: Dependency Analysis (fan-out 3)

**Instrument:** claude-code
**Pattern:** Source Triangulation — investigation phase

Three parallel investigators, each reading `sanitized-corpus.yaml` and analyzing dependencies from a structurally independent source:

**Instance 1: Issue Content Analysis**

Read each issue's title and body. Extract dependency signals:
- Explicit references: "#167", "depends on", "blocked by", "after", "prerequisite", "requires"
- Implicit ordering: "this builds on the fix for...", "once X is resolved..."
- Shared domain: issues mentioning the same component/module likely interact
- Severity ordering: issues that could cause data loss or corruption before cosmetic issues

For each proposed dependency edge `#A → #B` (A before B), provide the evidence quote.

Output: `{{ workspace }}/04-deps-issues.md`

**Instance 2: Code Path Analysis**

For each issue, identify the likely affected files and functions (from the issue description, mentioned file paths, error messages, stack traces). Then analyze overlap:
- Issues touching the same file have implicit ordering (lower-layer first)
- Issues modifying a function that another issue's fix would call
- Issues that change interfaces another issue depends on

Use `grep`, `find`, and the project structure from `project-profile.yaml` to trace paths.

Output: `{{ workspace }}/04-deps-code.md`

**Instance 3: Test Dependency Analysis**

For each issue, identify what tests would need to exist or pass:
- Does fixing issue A create test infrastructure that issue B's fix needs?
- Do any issues share test fixtures or test utilities?
- Would fixing issue A change test behavior that issue B's tests rely on?

Use the test directory structure and existing test files to trace relationships.

Output: `{{ workspace }}/04-deps-tests.md`

**Validations:** Each output file exists and contains at least one dependency edge or an explicit "no dependencies found" statement.

#### Stage 5: Dependency Triangulation (synthesis)

**Instrument:** claude-code (needs strong reasoning for cross-referencing)
**Pattern:** Source Triangulation — triangulation phase

Reads all three dependency reports. For each proposed edge `#A → #B`:

- **CORROBORATED** (2+ sources agree) — Strong dependency. Edge goes in the DAG. Record which sources agreed and their evidence.
- **UNCORROBORATED** (1 source only) — Weak dependency. Edge included in DAG but marked `weak`. Can be ignored if it would create a long critical path.
- **CONTRADICTED** (sources disagree on direction) — Flag for the record. No edge added. Document the contradiction and why.

Also identify issues with NO dependencies — these are tier-1 (can be solved in any order).

Output: `{{ workspace }}/05-triangulation.md`

```markdown
# Dependency Triangulation

## Corroborated Edges (strong)
| From | To | Sources | Evidence |
|------|----|---------|----------|
| #167 | #184 | issues + code | Both touch baton dispatch; DAG fix needed before pause logic |

## Uncorroborated Edges (weak)
| From | To | Source | Evidence |
|------|----|--------|----------|
| #172 | #187 | code | Both modify status tracking, but independent functions |

## Contradicted (no edge)
| A | B | Contradiction |
|---|---|---------------|

## Independent Issues (tier-1, no dependencies)
- #167: Baton ignores YAML dependency DAG
- #195: Baton structured error classification
```

**Validations:**
- File exists
- Contains all three sections (Corroborated, Uncorroborated, Independent)

#### Stage 6: DAG Assembly

**Instrument:** claude-code
**Pattern:** — (data transformation)

Reads `05-triangulation.md`. Produces `{{ workspace }}/issue-dag.yaml`:

```yaml
# Issue Dependency DAG
# Generated by issue-triage score
# Edges are directed: depends_on means "solve these first"

dag_metadata:
  repo: "Mzzkc/marianne-ai-compose"
  label: "bug"
  generated: "2026-04-18T..."
  total_issues: 9
  total_edges: 7
  tiers: 3

issues:
  - number: 167
    title: "Baton ignores YAML dependency DAG"
    tier: 1
    depends_on: []
    confidence: independent
    
  - number: 195
    title: "Baton structured error classification"
    tier: 1
    depends_on: []
    confidence: independent

  - number: 184
    title: "PauseJob event processed after dispatch"
    tier: 2
    depends_on: [167]
    edge_strength: corroborated
    evidence: "DAG dispatch fix required before pause event ordering"

  - number: 172
    title: "Skipped sheets cause FAILED status"
    tier: 2
    depends_on: [167]
    edge_strength: corroborated
    evidence: "Status calculation depends on correct DAG execution"

  # ... remaining issues with dependencies

resolved: []  # Score 2 appends resolved issue numbers here
```

**Acyclicity validation:** The stage runs a topological sort on the DAG. If cycles exist, it breaks the weakest edge (UNCORROBORATED before CORROBORATED) and documents which edge was broken and why.

**Validations:**
- `issue-dag.yaml` exists and is valid YAML
- Acyclicity check passes (`command_succeeds` running `python3 -c "import yaml; ..."` with a topo-sort)
- Every issue number in the DAG corresponds to a real issue in `sanitized-corpus.yaml`

### Stage 7: Final Packaging

**Instrument:** claude-code

Validates the full package:
1. Runs each harness script to confirm they still work
2. Verifies the DAG references only real issue numbers
3. Counts issues by tier
4. Writes `{{ workspace }}/07-ready.md` — the handoff manifest

```markdown
# Issue Triage Complete

## Project
- Repo: {{ repo }}
- Language: [from profile]
- Harness: 4 scripts validated

## Issues
- Total triaged: N
- Clean: N
- Flagged (injection): N
- Tiers: N

## DAG
- Edges: N (M corroborated, K uncorroborated)
- Tier 1 (no deps): [list]
- Critical path length: N issues

## Ready for Score 2
All artifacts validated. Issue solver can begin.
```

**Validations:**
- File exists
- All four harness scripts pass (`command_succeeds`)
- Contains "Ready for Score 2"

---

## Score 2: Issue Solver

**Location:** `examples/engineering/issue-solver.yaml` (replaces existing)
**Purpose:** Self-chaining issue solver that reads Score 1's DAG and project harness.
**Runs:** Self-chains until DAG is exhausted or `max_chain_depth` is reached.

### Variables (user-configurable)

```yaml
variables:
  triage_workspace: ""  # Path to Score 1's workspace (e.g. "../workspaces/issue-triage")
  repo: ""              # GitHub repo (owner/repo format)
  project_root: "."     # Path to the project root
```

All test/lint/typecheck/smoke commands come from the project harness in the triage workspace. No user configuration needed for project-specific commands.

### Preamble

```
╔══════════════════════════════════════════════════════════════════════════╗
║              GENERIC ISSUE SOLVER                                      ║
║                                                                        ║
║  DAG-driven, dependency-aware, phased implementation.                  ║
║                                                                        ║
║  You are part of a multi-stage pipeline. Each stage has a focused      ║
║  job. Do your job well. The next stage builds on your output.          ║
║                                                                        ║
║  Project harness: {{ triage_workspace }}/project-harness/              ║
║  Issue DAG: {{ triage_workspace }}/issue-dag.yaml                      ║
╚══════════════════════════════════════════════════════════════════════════╝
```

### Stage 1: Issue Selection (Read-and-React on DAG)

Reads `{{ triage_workspace }}/issue-dag.yaml`. Picks the next solvable issue:

1. Read the `resolved` list from the DAG
2. Find issues where ALL `depends_on` entries are either in `resolved` or are closed on GitHub
3. Among eligible issues, pick the lowest-tier issue (tier-1 before tier-2)
4. If multiple eligible at same tier, prefer corroborated edges over uncorroborated
5. Check `{{ workspace }}/archive/` to skip recently-attempted issues

Also reads the sanitized issue body from `{{ triage_workspace }}/issues/sanitized-corpus.yaml` for the selected issue.

Claims the issue on GitHub:
```bash
gh issue comment $ISSUE_NUM --repo {{ repo }} \
  --body "Starting automated work on this issue."
```

**`NO_ISSUES_REMAINING` sentinel:** If no eligible issues exist, writes the sentinel marker. All downstream stages short-circuit.

Output: `{{ workspace }}/01-selected-issue.md`

**Validations:** Same as existing score — file exists, contains `SELECTED_ISSUE: #N` or `NO_ISSUES_REMAINING`.

### Stage 2: Deep Investigation

Identical in structure to existing score stage 2. Reads the selected issue, investigates the codebase, maps blast radius, performs root cause analysis.

The only change: uses `{{ triage_workspace }}/project-profile.yaml` for project context (source root, test root, framework) so the musician knows where to look.

Output: `{{ workspace }}/02-investigation.md`

### Stage 3: Phase Planning + Verify Script Generation

Identical structure to existing score stage 3. Plans 1-4 phases, generates `verify.sh`.

Key change: `verify.sh` calls the project harness scripts instead of hardcoded commands:

```bash
#!/bin/bash
set -euo pipefail
HARNESS="{{ triage_workspace }}/project-harness"

PASS=0; FAIL=0; TOTAL=0
check() {
  TOTAL=$((TOTAL + 1))
  if eval "$2" >/dev/null 2>&1; then
    PASS=$((PASS + 1)); echo "  PASS: $1"
  else
    FAIL=$((FAIL + 1)); echo "  FAIL: $1"
  fi
}

echo "=== Verification: Issue #N — Title ==="

# Issue-specific checks
check "Phase 1: [description]" "[test command]"

# Project-wide checks via harness
check "Tests pass" "bash $HARNESS/test.sh"
check "Linter clean" "bash $HARNESS/lint.sh"
check "Types check" "bash $HARNESS/typecheck.sh"

echo ""
echo "=== Results: $PASS/$TOTAL passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
```

Output: `{{ workspace }}/03-plan.md` + `{{ workspace }}/verify.sh`

### Stages 4-11: Phased Implementation (Fix + Completion Pass)

Identical structure to existing score. Each phase pair:
- **Fix pass:** Execute the plan, complete 70%+ of work items, run tests via `bash {{ triage_workspace }}/project-harness/test.sh`, run `verify.sh`.
- **Completion pass:** Close all deferred items, run simplification audit, re-run tests.

`skip_when_command` reads `TOTAL_PHASES` from `03-plan.md` — same conductor-level Read-and-React as existing score.

The only change: all test/lint/typecheck references use the harness scripts instead of hardcoded variables.

### Stage 12: Quality Review (Fan-out + Synthesis)

Three parallel, isolated reviewers — same structure as existing score:

| Instance | Role | Focus |
|----------|------|-------|
| 1 | Functional Reviewer | Correctness, edge cases, targeted tests |
| 2 | E2E / Smoke Tester | Full test suite, linter, typechecker, smoke, verify.sh |
| 3 | Code Quality Reviewer | Naming, simplicity, error handling, wolf prevention |

All three use the project harness for their validation commands.

Output: `{{ workspace }}/12-review-{functional,e2e,quality}.md`

### Stage 13: Review Synthesis

Same as existing score. Merge findings from 3 reviewers, find convergences/tensions/gaps, fix identified issues, re-verify.

Output: `{{ workspace }}/13-synthesis.md`

### Stage 14: Documentation Update

Same as existing score. Update affected docs, docstrings.

Output: `{{ workspace }}/14-docs.md`

### Stage 15: Final Verification

Same as existing score. All checks must pass: verify.sh, test suite, linter, type checker, smoke test. All via harness scripts.

Output: `{{ workspace }}/15-verification.md`

### Stage 16: Commit & Push

Same as existing score. Selective staging (no `git add -A`), sync with remote, commit with issue reference, push.

Commit message format:
```
fix: $ISSUE_TITLE (#$ISSUE_NUM)

Resolves #$ISSUE_NUM

Co-Authored-By: Marianne AI Compose <noreply@marianne.ai>
```

Output: `{{ workspace }}/16-commit.md`

### Stage 17: Close Issue + Update DAG + Chain Gate

Extended from existing score. Three additions:

**Step 1: Close the issue** (same as existing)

**Step 2: Update the DAG**

Append the resolved issue number to the `resolved` list in `{{ triage_workspace }}/issue-dag.yaml`:
```bash
# Add resolved issue to DAG
python3 -c "
import yaml
with open('{{ triage_workspace }}/issue-dag.yaml') as f:
    dag = yaml.safe_load(f)
dag['resolved'].append($ISSUE_NUM)
with open('{{ triage_workspace }}/issue-dag.yaml', 'w') as f:
    yaml.dump(dag, f, default_flow_style=False)
"
```

This is how Score 2 iterations communicate: through the shared DAG artifact. Each iteration marks its issue as resolved, making dependent issues eligible for the next iteration.

**Step 3: Archive iteration** (same as existing)

**Step 4: Chain gate** (same as existing — validation fails on `NO_ISSUES_REMAINING`)

Output: `{{ workspace }}/17-close.md`

---

## Concert Configuration

The two scores form a concert. Score 1 runs first, Score 2 runs after Score 1 completes:

```yaml
# examples/engineering/issue-solver-concert.yaml (concert manifest)
name: "issue-solver-concert"
description: "Triage and solve GitHub issues for any project"

scores:
  - path: examples/engineering/issue-triage.yaml
    name: "triage"
    variables:
      repo: "[owner/repo]"
      issue_label: "bug"
      project_root: "."

  - path: examples/engineering/issue-solver.yaml
    name: "solver"
    depends_on: [triage]
    variables:
      triage_workspace: "../workspaces/issue-triage"
      repo: "[owner/repo]"
      project_root: "."
```

**Note:** The concert manifest format shown above is aspirational — it describes the intended multi-score orchestration. If Marianne's concert system does not yet support this exact manifest format, run the scores independently:
```bash
mzt run examples/engineering/issue-triage.yaml   # Run once
# Review DAG, then:
mzt run examples/engineering/issue-solver.yaml   # Self-chains
```

## Score 2 Self-Chain Configuration

```yaml
on_success:
  - type: run_job
    job_path: "examples/engineering/issue-solver.yaml"
    description: "Chain to solve next issue from DAG"
    detached: true
    fresh: true

concert:
  enabled: true
  max_chain_depth: 30
  cooldown_between_jobs_seconds: 300
  inherit_workspace: false
```

Each iteration gets a fresh workspace. The DAG in Score 1's workspace is the shared state — updated by each iteration's stage 17.

## Workspace Layout

### Score 1 Workspace (`workspaces/issue-triage/`)

```
01-discovery.md              — Project toolchain discovery
project-harness/
  test.sh                    — Test suite wrapper
  lint.sh                    — Linter wrapper
  typecheck.sh               — Type checker wrapper
  smoke.sh                   — Smoke test wrapper
project-profile.yaml         — Structured project metadata
issues/
  raw-corpus.json            — Raw GitHub issue data
  sanitized-corpus.yaml      — Post-scanner issue corpus
04-deps-issues.md            — Dependency analysis: issue content
04-deps-code.md              — Dependency analysis: code paths
04-deps-tests.md             — Dependency analysis: test dependencies
05-triangulation.md          — Cross-source triangulation
issue-dag.yaml               — Dependency-ordered issue DAG (MUTABLE — updated by Score 2)
07-ready.md                  — Handoff manifest
```

### Score 2 Workspace (`workspaces/issue-solver/`, per-iteration)

```
.iteration                   — Iteration counter
01-selected-issue.md         — Issue selection from DAG
02-investigation.md          — Deep investigation
03-plan.md                   — Phased plan with TOTAL_PHASES marker
verify.sh                   — Generated verification script
04-phase1-fix.md             — Phase 1 implementation
05-phase1-complete.md        — Phase 1 completion pass
06..11-phaseN-*.md           — Phases 2-4 (conditional)
12-review-functional.md      — Functional review
12-review-e2e.md             — E2E/smoke review
12-review-quality.md         — Code quality review
13-synthesis.md              — Review synthesis
14-docs.md                   — Documentation updates
15-verification.md           — Final verification
16-commit.md                 — Commit report
17-close.md                  — Close + DAG update + chain gate
archive/                     — Previous iteration artifacts
```

## Instrument Configuration

### Score 1

| Stage | Instrument | Timeout | Rationale |
|-------|-----------|---------|-----------|
| 1 (Discovery) | claude-code | 600s | File exploration, broad sweep |
| 2 (Harness) | claude-code | 600s | Script generation + smoke test |
| 3 (Triage) | claude-code | 900s | Issue fetch + scanner runs |
| 4 (Fan-out) | claude-code | 900s | Code analysis per investigator |
| 5 (Triangulation) | claude-code | 600s | Cross-reference synthesis |
| 6 (DAG) | claude-code | 600s | Data transformation |
| 7 (Packaging) | claude-code | 300s | Validation runs |

### Score 2

Same as existing score:
| Stage | Instrument | Timeout |
|-------|-----------|---------|
| 1-3 | claude-code | 2400s |
| 4, 6, 8, 10 (fix) | claude-code | 3600s |
| 5, 7, 9, 11 (completion) | claude-code | 2400s |
| 12 (review, x3) | claude-code | 2400s |
| 13-17 | claude-code | 2400s |

Fallback: `anthropic_api` for all stages in both scores.

## Prerequisites

1. `gh` CLI authenticated (`gh auth login`)
2. `cisco-ai-skill-scanner` installed (`pip install cisco-ai-skill-scanner`)
3. Python 3.8+ available (required by cisco-ai-skill-scanner and DAG update scripts)
4. Project must be a git repository
5. Open issues with the configured label must exist

## Migration from Existing Issue Solver

The existing `examples/engineering/issue-solver.yaml` is replaced. Users of the roadmap-driven score should:

1. Run Score 1 (issue-triage) once to generate the project harness and DAG
2. The DAG replaces the roadmap — it's auto-generated instead of hand-written
3. All `test_command`/`lint_command`/`typecheck_command`/`smoke_test_command` variables are replaced by the project harness scripts
4. `roadmap_file` variable is removed entirely
5. Self-chain behavior is preserved

## Applying to Marianne's Correctness Bugs

To solve the 9 correctness bugs identified in the beta review:

1. Edit `issue-triage.yaml` variables: set `repo: "Mzzkc/marianne-ai-compose"`, `issue_label: "bug"`, `project_root: "/home/emzi/Projects/marianne-ai-compose"`
2. Run triage: `mzt run examples/engineering/issue-triage.yaml`
3. Review the DAG: `cat workspaces/issue-triage/issue-dag.yaml`
4. Edit `issue-solver.yaml` variables: set `triage_workspace`, `repo`, `project_root`
5. Start solving: `mzt run examples/engineering/issue-solver.yaml`

The triage score will pull all open bugs, run the scanner on each, analyze dependencies between them, and produce a DAG. The composer reviews the DAG, then launches the solver to work through them in dependency order.

## Open Design Decisions

None — all decisions resolved during brainstorming.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Cisco scanner not installed | Medium | Stage 3 checks for binary, fails fast with clear error message |
| DAG construction misses dependencies | Medium | Source Triangulation with 3 independent sources; weak edges preserved; composer reviews DAG before Score 2 runs |
| Project harness scripts wrong | Low | Shipyard gate (smoke test) catches broken harness before any issue solving begins |
| Self-chain modifies DAG while triage re-runs | Low | Score 1 and Score 2 should not run concurrently; document this constraint |
| Issue body contains successful injection despite scanner | Low | Scanner is best-effort; sanitized corpus replaces flagged bodies; agents never see raw flagged content |
