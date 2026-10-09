# Patterns Examples

Production-grade orchestration patterns from the Rosetta corpus. These scores demonstrate coordination strategies for multi-agent workflows — from security audits that route findings to instrument-matched analysis tiers, to parallel build pipelines where interface contracts prevent integration failures. Each score faithfully implements its pattern's structural moves and shows you when and why to reach for that coordination approach.

## Scores

| Score | What It Does | Sheets | Patterns Used | Time | Cost |
|-------|-------------|--------|--------------|------|------|
| [convergence-loop](convergence-loop.yaml) | Repeats a CLI sheet until a file expression becomes true; escalates an attempt failure | 1, two passes | Loop, expression, trigger | <1m | $0 |
| [dead-letter-quarantine](dead-letter-quarantine.yaml) | Batch-generate Python utilities, quarantine failures, analyze cross-failure patterns, reprocess with adapted strategy | 16 | Dead Letter Quarantine | ~35m | ~$6 |
| [echelon-repair](echelon-repair.yaml) | Security audit with instrument-matched tiers — classify findings by severity, route to appropriate analysis depth | 6 | Echelon Repair | ~30m | ~$5 |
| [immune-cascade](immune-cascade.yaml) | Security hardening pipeline — cheap broad sweeps feed targeted expensive investigation where it matters | 11 | Immune Cascade | ~60m | ~$5 |
| [prefabrication](prefabrication.yaml) | Contract-first full-stack app development — parallel API and CLI tracks coordinate via interface contract, independently validate | 6 | Prefabrication | ~20m | ~$3 |
| [shipyard-sequence](shipyard-sequence.yaml) | Python library generation with validation gates — fail fast on broken foundation before expensive fan-out | 7 | Shipyard Sequence | ~4m | <$1 |
| [source-triangulation](source-triangulation.yaml) | Technical claim verification through independent analysis — code, docs, and tests cross-validate to categorize claims as corroborated, uncorroborated, or contradicted | 5 | Source Triangulation | ~3m | ~$1 |
| [design-review](design-review.yaml) | Multi-perspective design evaluation — five TDF-aligned reviewers examine same document through different analytical frames, synthesis finds convergent issues vs trade-offs | 8 | Rashomon Gate, Fan-out + Synthesis | ~25m | ~$4 |

### dead-letter-quarantine

Generates ten Python utility scripts in parallel, validates each with real tools (syntax, imports, tests), quarantines failures, analyzes patterns across all failures to identify root causes (e.g., "4 scripts failed because they imported non-stdlib modules"), and reprocesses quarantined items with an adapted strategy informed by cross-failure intelligence. Produces working utilities with test coverage and usage documentation.

### echelon-repair

Audits a codebase for security vulnerabilities by classifying findings into tiers — E1 (grep/tool-based), E2 (code-level analysis), E3 (architectural reasoning) — then routing each finding to the instrument-matched analysis depth. E1 findings processed by CLI tools, E2 by code-aware agents, E3 by deep reasoning. Produces consolidated audit report with remediation guidance and roadmap.

### immune-cascade

Hardens Python projects through graduated response: four cheap parallel sweeps (dependency audit, static analysis, secret scan, config audit) produce raw findings; triage stage identifies critical areas; three expensive deep investigations produce remediation code for top threats; synthesis assembles actionable security report with patches and ongoing practices.

### prefabrication

Builds a bookmark manager with FastAPI backend and Click CLI client in parallel. Starts by defining the interface contract (API schema), then both tracks work independently against that contract with their own validation. Integration stage assembles pre-validated components — when the contract is right, integration is mechanical. Produces runnable full-stack app with tests and documentation.

### shipyard-sequence

Generates a Python library by validating the foundation (shared types, config) with real tools (syntax check, import check) before fanning out to generate four dependent modules. If foundation validation fails, retry happens at 1-sheet cost, not 4-sheet cost. Integration stage verifies all modules work together. Demonstrates fail-fast economics.

### source-triangulation

Verifies technical claims (e.g., "handles 10K req/sec") by dividing evidence sources: one agent analyzes code, another reads docs, a third examines tests and benchmarks. Synthesis cross-references findings to categorize each claim as corroborated (all sources agree), uncorroborated (insufficient evidence), or contradicted (sources conflict). Distinguishes what code does from what docs claim and what tests prove.

### design-review

Evaluates design documents through five independent TDF-aligned perspectives (computational, scientific, cultural, experiential, meta-analytical) examining the same artifact. Gap synthesis identifies where reviewers converge (systemic issues requiring fixes) and diverge (trade-offs requiring author judgment). Final verdict produces actionable amendments and completeness assessment.

## Quick Start

```bash
mzt start
mzt run examples/patterns/shipyard-sequence.yaml
mzt status lib-builder --watch
```

Start with `shipyard-sequence` — it's fast (~4 minutes), demonstrates validation gates and fail-fast economics, and produces tangible output (a working Python library). Once you see how movements coordinate sheets and validation gates prevent wasted work, try `source-triangulation` to see evidence-splitting, then `prefabrication` to see parallel tracks with interface contracts.

## Adapting to Your Project

All scores use relative workspace paths (`../workspaces/<name>`) — no changes needed for basic exploration.

**For real use:**

- **dead-letter-quarantine**: Customize the 10 utility specs in the variables section to match your actual tooling needs. The pattern handles batch generation, failure quarantine, and adaptive reprocessing automatically.

- **echelon-repair**: Point at your codebase by changing the working directory before running. The score expects a Python project structure. Customize E1 tool list (bandit, semgrep, etc.) in the sweep definitions if you have project-specific scanners.

- **immune-cascade**: Set `target_project` variable to your Python project path. Optionally customize the four sweep definitions to add domain-specific checks (e.g., Django-specific security patterns, AWS credential scans).

- **prefabrication**: The bookmark manager demonstrates the pattern. To build your own app, replace the contract definition in Movement 1's template with your API schema, then adapt the parallel track prompts for your backend tech (FastAPI, Flask, etc.) and frontend needs.

- **shipyard-sequence**: Change the foundation spec (types, config) and module specs (parser, validator, transformer, reporter) in the variables section. The validation gate structure remains the same — syntax/import checks before fan-out.

- **source-triangulation**: Replace `claims` list with technical assertions you want to verify. Update `project_name`, `project_description`, and `source_materials` to point at your actual code, docs, and test artifacts.

- **design-review**: Set `doc` variable to the path of your design document. Optionally provide `project_context` (domain, constraints, stakeholders) for more relevant analysis.

**Prerequisites:**
- Running conductor: `mzt start`
- Python projects (echelon-repair, immune-cascade): `python` and `pip` available
- Security audits: `bandit`, `ruff`, or other scanners installed for E1 tier validation

## Patterns Demonstrated

These scores implement patterns from the Rosetta corpus (`conventions.md` injected context). Each pattern addresses a specific coordination problem:

**Batch Intelligence:**
- **Dead Letter Quarantine** — systematic analysis of batch failures reveals patterns invisible to single-item diagnosis; reprocessing uses adapted strategy informed by cross-failure intelligence

**Instrument Strategy:**
- **Echelon Repair** — findings classified by severity, each tier routed to instrument-matched analysis depth (cheap tools for simple issues, expensive reasoning for architectural vulnerabilities)
- **Immune Cascade** — cheap broad sweeps (grep, static analysis) narrow to expensive targeted investigation; economic gradient is load-bearing

**Parallel Coordination:**
- **Prefabrication** — parallel tracks do different work against shared interface contract; when contract is solid, integration is mechanical
- **Rashomon Gate** — multiple frames analyze same artifact; pattern of agreement reveals more than any single analysis

**Quality Gates:**
- **Shipyard Sequence** — validate foundation with real tools before expensive fan-out; fail fast on broken substrate

**Evidence Analysis:**
- **Source Triangulation** — divide evidence sources (code vs docs vs tests), not work; cross-reference reveals what's corroborated, uncorroborated, or contradicted

**Composition:**
- **Fan-out + Synthesis** — parallel work with integrated perspectives (used in design-review, immune-cascade)

See the full Rosetta pattern index in the conventions document for when to reach for each pattern, composition clusters, and anti-patterns.
