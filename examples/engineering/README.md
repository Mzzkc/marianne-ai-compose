# Engineering Examples

These scores automate software development workflows — from solving GitHub issues to improving code quality to generating implementation plans from design documents. They demonstrate how AI orchestration can replace manual development cycles with autonomous, multi-stage pipelines that investigate, plan, execute, review, and ship production-ready changes.

## Scores

| Score | What It Does | Sheets | Patterns Used | Time | Cost |
|-------|-------------|--------|--------------|------|------|
| [issue-triage](issue-triage.yaml) | Discovers any project, generates a verified test/lint/typecheck/smoke harness, safely scans open GitHub issues (Cisco AI Defense), builds an issue-solver-compatible dependency DAG | 9 | Source Triangulation, Triage Gate, Prefabrication, Shipyard Sequence | 30-90m | depends on the `workhorse` chain |
| [quality-triage](quality-triage.yaml) | Unified local code review + external issues → prioritized DAG. 4-persona TDF review, cross-lens synthesis, escalation-gate flagging, dual emit (internal DAG + ultrareview-compatible findings.json). Complements issue-triage for broader local-code coverage | 15 | Immune Cascade, Fan-out + Synthesis, Source Triangulation, Rashomon Gate | 1-3h | subscription |
| [issue-solver](issue-solver.yaml) | Consumes the triage DAG, self-chains through solvable issues, plans phased implementation, executes with parallel quality review, commits and ships | 13 | Succession Pipeline, Fan-out + Synthesis, Read-and-React | 2-8h/iter | ~$40-100/iter |
| [quality-continuous](quality-continuous.yaml) | Language-agnostic quality pipeline — parallel expert reviews, batched fixes by difficulty, commits and files GitHub issues for next iteration | 16 | Immune Cascade, Fan-out + Synthesis | 10-15h | ~$50-130 |
| [score-composer](score-composer.yaml) | Reads a design document and generates a runnable Marianne score with implementation tasks, validations, and dependencies | 4 | Succession Pipeline | 1-2h | ~$10-20 |
| [spec-forge](spec-forge.yaml) | Concept → intake, five blind domain lenses, a parts breakdown, one spec and integration doc per part, three-family review with mutual-find triage and bounded repair, then a stakeholder brief held in FERMATA for approval | 20 (+ loop) | Reconnaissance Pull → Rashomon Gate → Constraint Propagation Sweep → Fan-out + Synthesis | 45-90m | loop-bounded |
| [design-to-orchestra](design-to-orchestra.yaml) | Approved specs → repertoire survey, per-part casting, one build score per part, an integration score and a fleet; every generated score gated by `mzt validate` (no errors or warnings but V311), the composing release checker with a lock, a fleet-membership check and a two-family verdict before it hands over RUN.md | 23 (+ loop) | Reconnaissance Pull → Fan-out + Synthesis → The Etiquette Law → Rashomon Gate | 1-2h | loop-bounded |
| [project-roadmap](project-roadmap.yaml) | Audits any project from seven analytical frames, triangulates the findings and produces a prioritized roadmap with milestones, a task list, a Mermaid timeline and a slide deck | 24 | Immune Cascade → Rashomon Gate → Sugya Weave → Fan-out + Synthesis | 40-60m | ~$15-28 |
| [codebase-rewrite](codebase-rewrite.yaml) | Coverage-first language/framework migration: cover the source to a floor, rewrite test-bound units until unit, integration, functional and differential tiers all pass, then refine under the same oracle with critics on different models | 18 (+ loops) | Cathedral Construction + CEGAR Loop + Commissioning Cascade + Rashomon refinement | 2-8h/iter | loop-bounded |
| [saas-app-builder](saas-app-builder.yaml) | Full-stack application generator with contract-first parallel builds and validation gates | 8 | Prefabrication + Shipyard Sequence + Commissioning Cascade | 2-6h | ~$10-30 |
| [lovable-generator](lovable-generator.yaml) | Web application generator producing a complete deployable app from a concept description | 11 | Succession Pipeline | 1-3h | ~$5-15 |

### issue-triage.yaml + issue-solver.yaml (concert)

Two scores composed into a concert that autonomously solves any project's GitHub issues.

**issue-triage** runs first (once per project). It profiles the project (language, frameworks, build/test/lint commands, entry points) via **Source Triangulation**, generates a verified test/lint/typecheck/smoke harness with a **Shipyard Gate** (the smoke test must pass before expensive downstream work runs), fetches open issues from the target repository, and passes every issue body through a **Triage Gate**: the Cisco AI skill scanner rejects prompt-injection payloads while a hardened Python pass sanitizes titles/labels of shell-dangerous characters. Surviving issues are classified by effort tier (integer, 1 = no deps), their dependencies extracted and triangulated against GitHub cross-references, and emitted as `issue-dag.yaml` + `issues/sanitized-corpus.yaml` + `project-harness/*.sh` + `project-profile.yaml` — the exact artifacts issue-solver consumes.

**issue-solver** then consumes those artifacts. Each iteration picks the next solvable issue (no unresolved deps, lowest tier, lowest number; an issue GitHub reports CLOSED since the triage is folded into the DAG's `resolved` list first, so a chain never re-solves what a human closed), investigates the codebase, plans a 1-4 phase implementation strategy, executes each phase with fix+completion passes, runs three parallel quality reviewers (functional, E2E, code quality) whose findings are synthesized before shipping, updates documentation, verifies all tests pass against the generated harness, commits, pushes, closes the GitHub issue, atomically appends to the DAG's resolved list, and **self-chains** to solve the next one.

### quality-triage.yaml (complements issue-triage)

An alternative triage score for cases where you want **multi-perspective review of the local codebase** alongside GitHub issue discovery, not just external issues. Two optional input branches converge on a unified findings set:

- **Local branch**: 4 reviewers with cross-section lens triples — Field Engineer (EXP+SCI+COMP, "debug at 3am"), Architect (SCI+META+CULT, "does this fit?"), New Contributor (COMP+CULT+EXP, "day 3 hire"), Systems Steward (META+CULT+SCI, "three years out") — each runnable on a different instrument family for real cognitive diversity (claude-code / goose / gemini-cli). Local Synthesis triangulates across reviewers (2+ flaggers = corroborated), across lenses (3+ dimensions = multi-dimensional deep issue), and explicitly checks for blind spots.
- **External branch**: cisco-ai-skill-scanner gate + 3-investigator Source Triangulation (issues/code/tests), same as issue-triage.

Both branches fan in to **Unified Triage**: dedup across sources, E1/E2/E3 risk-tier classification (E1=safe auto-fix, E2=human review, E3=escalate), and escalation-gate flagging (touching `scores/`, `daemon/`, `learning/`, or `.marianne/spec/` triggers issue-filing with no auto-PR). Outputs both an internal `issue-dag.yaml` and `findings.json` in ultrareview's schema for A/B comparison with opaque cloud services.

**Note:** `quality-triage` and `issue-triage` use different DAG schemas optimized for different downstream use cases. `issue-triage` is the direct upstream for `issue-solver`. `quality-triage`'s findings.json is ultrareview-compatible; its `issue-dag.yaml` is consumed by humans or custom solvers. Pick based on what you want to do with the findings.

### quality-continuous.yaml

A self-chaining quality improvement pipeline that discovers and fixes code issues without language-specific configuration. Stage 1 generates test/typecheck/lint runner scripts by examining the project. Three parallel expert reviews (Architecture, Test Coverage, Code Debt) scan the codebase from different analytical lenses. Their findings are synthesized, categorized, and prioritized into three remediation batches (quick wins, medium effort, significant). Each batch gets a fix pass and a completion pass. The pipeline verifies all changes, commits, resolves merge conflicts, and files GitHub issues for unresolved problems. Then chains to the next iteration.

This is **Immune Cascade** — cheap broad scanning (expert reviews) identifies ALL issues first, triage creates a targeting brief, then expensive deep work (code fixes) begins on prioritized targets. This avoids wasting fix passes on issues that a cheap sweep would deprioritize. The expert reviews use **Fan-out + Synthesis** — independent parallel reviews produce genuine convergence when 2 of 3 reviewers (who can't see each other) flag the same module.

### score-composer.yaml

Transforms design documents into executable implementation scores. Given a design doc, it produces a structured analysis identifying all deliverables and dependencies, decomposes them into ordered implementation tasks with acceptance criteria, generates a Marianne score YAML where each sheet is one implementation step, validates the score syntax, and produces documentation explaining how to run it.

This is **Succession Pipeline** — each stage requires categorically different methods (analytical reading → decomposition → code generation → syntax validation) and each output becomes the next input substrate. The generated score can then be run to execute the implementation automatically.

### spec-forge.yaml + design-to-orchestra.yaml (concert)

The two halves of taking a rough concept to a running orchestra, in any domain. Both are the shipped machinery behind the conducting skill's concept-to-completion route.

**spec-forge** turns a concept file (and an optional flat directory of stakeholder notes) into specs a team can build from. An intake sheet records the problem, deeper needs (stated or inferred), hard and soft limits, tempo and open questions, each with the assumption it proceeds on. Five lenses (COMP, SCI, CULT, EXP, META) examine the problem in parallel without seeing each other; a breakdown sheet resolves their conflicts into a system design, a `parts.yaml` of up to eight parts in parallel waves, and a decisions log. Each part then gets its own `spec.md` and `integration.md` (empty slots skip at no cost). Three reviewers on three model families review the set; triage keeps the cross-section of their findings: CRITICAL/HIGH findings two families agree on are blockers, real single-family findings are deferred to a polish backlog, unconfirmed outliers are discarded. Blockers loop back through repair and re-review, at most three passes. The last sheet writes a stakeholder brief and holds in FERMATA until `mzt resolve spec-forge <sheet> accept`.

**design-to-orchestra** consumes that `specs/` tree. It surveys the shipped repertoire (examples, Rosetta patterns, primitives, techniques, persistent agents, live instrument classes), casts each part as a team with independent review and generous budgets, and writes one build score per part, an integration score that joins the parts and puts a prototype in front of people, and a `type: fleet` that runs each wave in parallel and integration last. A deterministic `cli` gate runs `mzt validate` on every generated score (any error or warning fails it, except V311, which names an instrument this machine lacks), runs the composing release checker and writes a lock, checks that the fleet names exactly the gated scores and that every part has one, and dry-runs the fleet. Two reviewers on different families then read the scores blind to each other, a verdict sheet keeps the cross-section of their findings, and blockers loop back to composition. Release writes `RUN.md` whose start commands re-verify every lock, so a score edited after review refuses to start, and submits the fleet only when `submit=true`.

```bash
mzt run examples/engineering/spec-forge.yaml --var concept_file=/abs/concept.md --var context_dir=/abs/context
mzt resolve spec-forge <sheet> accept
mzt run examples/engineering/design-to-orchestra.yaml \
  --var specs_dir=$HOME/workspaces/spec-forge/specs --var project_root=/abs/project
```

The repair loops end at a cap rather than spinning: spec-forge's last triage pass fails the job honestly if blockers remain, and design-to-orchestra's release refuses an orchestra without `GATE: PASS` and an APPROVE verdict.

### codebase-rewrite.yaml

A rewrite is only as safe as the oracle that checks it, so this score builds the oracle first. It measures the source's line coverage the way the tests actually reach the code (a suite that runs the program as a subprocess needs subprocess coverage). Below `coverage_floor` (default 90%), a bounded loop writes characterization tests against the *source*, one under-covered module per pass, and commits them before anything is rewritten. An oracle inventory then sorts every test into unit, integration and functional tiers and builds a differential corpus: inputs fed on stdin to both `source_run_command` and `target_run_command`, whose outputs and exit codes must agree unless the specification says otherwise.

Rewriting happens inside a behaviour loop of up to three rounds. Each round's inner unit loop rewrites one test-bound unit per pass, red then green, and commits it on `rewrite_branch`. Four oracle voices then run the tiers in parallel, and a behaviour gate either declares every tier green with target coverage at the floor, or reopens the units the red tests point at. A unit reopened twice is marked BLOCKED, so no round spins on it.

Only green code is refined. Each refinement pass sends the code to three critics bound through one alias with `select: [spread, balance]`, so they run on different models: idiom and simplicity, specification conformance, and structural fidelity to the source. A refiner applies one finding per commit and reverts any commit that turns a tier red, so every kept commit passes the whole oracle. The loop ends after `count` passes or when the critics find nothing worth changing. The final review applies hard gates (behaviour green, coverage at the floor, every spec rule met) and reports a scorecard in which mirroring the source's decomposition earns points but never overrides a test, a spec rule or an idiom. Every sheet's validation reruns the real command, so a report cannot claim green over a red suite. `improvement_specs` is the target specification, and every analysis stage reads it. Each iteration self-chains until the review writes COMPLETE.

### saas-app-builder.yaml

Generates a complete, working SaaS application with backend API, frontend UI, and database layer — built in parallel by three independent agents coordinated only through an architecture contract. Prefabrication defines shared interface contracts before parallel builds, making integration mechanical when the contract is precise. Shipyard Sequence validates contracts with structural tools before expensive fan-out. Commissioning Cascade validates at contract scope, integration scope, and system scope, with staged fail-fast if earlier scopes fail.

### lovable-generator.yaml

Web application generator producing a complete deployable app from a concept description. Five movements — Architecture, Foundation (3 parallel voices), Features (4 parallel voices), Polish (2 parallel voices), and Verification — progressively build and refine a working React + TypeScript app. Succession Pipeline stages each transform the workspace from requirements to running application. The practical answer to "can Marianne build a product?" — yes, and this score proves it.

## Quick Start

```bash
# Start the conductor
mzt start

# Run a score
mzt run examples/engineering/score-composer.yaml

# Watch progress
mzt status score-composer --watch
```

## Adapting to Your Project

### issue-triage.yaml + issue-solver.yaml

**Run triage first (once per project):**

1. Set `repo` to the `owner/name` of the GitHub repository
2. Set `project_root` to the absolute path of the project on disk (for discovery)
3. Authenticate GitHub CLI: `gh auth login`
4. Run it: `mzt run examples/engineering/issue-triage.yaml`

Output: `examples/workspaces/issue-triage-workspace/issue-dag.yaml` (plus `issues/sanitized-corpus.yaml`, `project-harness/*.sh`, `project-profile.yaml`) — the artifacts issue-solver consumes.

**Then run the solver (self-chains until the DAG is exhausted):**

1. Set `repo` to the same `owner/name` (validated against the DAG)
2. Set `triage_workspace` to the absolute path of the triage workspace (containing `issue-dag.yaml`)
3. Set `project_root` to the same absolute path used in triage
4. Update `on_success.job_path` to the absolute path of `issue-solver.yaml` (enables self-chaining)
5. Run it: `mzt run examples/engineering/issue-solver.yaml`

**Prerequisites:** gh CLI authenticated, `cisco-ai-skill-scanner` installable as a tool — `uv tool install cisco-ai-skill-scanner` or pipx; its command is `skill-scanner` (auto-installed by triage; a bare `pip install` is refused on PEP 668 hosts), issue-triage has been run and produced a DAG.

**Safety rails:** The solver refuses to push to `main`/`master`/`trunk` unless `ALLOW_PUSH_TO_MAIN=1` is exported. Commit titles are written to a file and committed via `-F` — never interpolated into shell. The DAG is updated atomically before the GitHub issue is closed, so crash-during-close is idempotent on replay.

### quality-triage.yaml

1. Authenticate GitHub CLI if you want the external issue branch: `gh auth login` (optional — branch skips cleanly when unavailable)
2. Run it: `mzt run examples/engineering/quality-triage.yaml`

Output: `examples/workspaces/quality-triage-workspace/issue-dag.yaml` + `findings.json` (ultrareview schema) + `unified-findings.yaml` + `handoff-manifest.md`.

The score does **not** chain to `issue-solver.yaml` automatically. `quality-triage`'s DAG schema (`quality-triage-dag-v1`, with node-id `depends_on` and E1/E2/E3 tier strings) intentionally differs from `issue-triage`'s (`issue-triage-dag-v1`, with issue-number `depends_on` and integer tiers). The DAG includes both a schema discriminator and a `tier_int` alias (E1→1, E2→2, E3→3), so downstream tools that validate the discriminator can adapt. To get the autonomous triage → solve pipeline, use `issue-triage.yaml` directly as the upstream for `issue-solver.yaml`.

**Generic by default** — the score discovers venue structure from the target repo itself. No hardcoded paths. Works on any codebase without modification.

**Prerequisites:** Target is a git repo. `gh` CLI authenticated (optional — external branch skipped if absent). `.marianne/spec/intent.yaml` is optional; score falls back to README + manifests.

**Safety rails:** Escalation-flagged findings (touching score definitions, daemon lifecycle, learning-store schema, or spec corpus) are filed as issues for human review with no auto-PR. Issue bodies flagged by cisco-ai-skill-scanner are replaced with metadata-only summaries before entering any downstream LLM context. Filing is capped at 50 issues per run.

### quality-continuous.yaml

1. Set `workspace` to your preferred output directory
2. Customize `github_label` for your project's issue tracking (e.g., "technical-debt", "code-quality")
3. Run from the project root, or set `prompt.variables.score_relpath` to the score's path from the current directory. Sheet 1 copies the score to `{workspace}/self-chain.yaml`, which the success hook runs by absolute workspace path.
4. Adjust `instrument_config.timeout_seconds` if your test suite takes longer than 40 minutes per sheet

**Prerequisites:** Source code to review, gh CLI authenticated (optional — only needed for GitHub issue filing in stage 14)

The score is language-agnostic — it generates test/typecheck/lint runner scripts by examining your project structure and tooling, then uses those scripts throughout the pipeline. Works with Python, TypeScript, Go, Rust, Java, or any project with verifiable quality commands. The concert caps chaining at 10 jobs, with a 120-second cooldown; a fresh iteration archives the previous workspace. Sheet 1 must stage the self-copy successfully before any iteration can chain.

### score-composer.yaml

1. Set `doc` to the path of your design document (Markdown, RFC, spec, or prose plan)
2. Set `output_score` to the desired filename for the generated score (without .yaml extension)
3. Set `project_context` to describe your project — language, frameworks, codebase size, source layout, test directory, key conventions
4. Set `granularity` to "fine" for small TDD-strict tasks (2-5 min each) or "coarse" for larger units of work (10-20 min each)
5. Set `target_workspace` to where the generated implementation score should write its output
6. Set `target_instrument` to the instrument the generated score should use (e.g., "claude-code", "codex-cli")

**Prerequisites:** Design document exists, project root is accessible

**Output:** A validated, runnable Marianne score with one sheet per implementation task, acceptance criteria as validations, and dependencies declared. Review the generated score, customize if needed, then run it: `mzt run <generated-score>.yaml`

## Patterns Demonstrated

These scores demonstrate core Rosetta patterns for production AI orchestration:

- **Succession Pipeline** — Sequential substrate transformations where each stage requires categorically different methods (issue-solver, score-composer)
- **Fan-out + Synthesis** — Parallel independent work streams whose convergence produces genuine signal (issue-solver review phase, quality-continuous expert reviews)
- **Immune Cascade** — Graduated response where cheap broad scanning identifies targets before expensive deep work begins (quality-continuous)

See the [Rosetta Corpus](../../scores/rosetta-corpus/) for full pattern documentation and implementation guidance.

## Further Reading

- [Marianne documentation](../../docs/)
- [Pattern index](../../scores/rosetta-corpus/INDEX.md)
- [Score authoring skill](../../plugins/marianne/skills/score-authoring/SKILL.md)
- [Command reference](../../plugins/marianne/skills/command/SKILL.md)
