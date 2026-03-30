# Mozart Examples

This directory contains example Mozart configurations for various use cases. Mozart is a **general-purpose cognitive orchestration system**, not just a coding tool. These examples demonstrate its versatility across domains.

## Quick Start Examples

| Example | Purpose | Complexity |
|---------|---------|------------|
| [simple-sheet.yaml](simple-sheet.yaml) | Minimal Mozart configuration to get started | Simple |
| [api-backend.yaml](api-backend.yaml) | Using Anthropic API directly instead of CLI | Simple |

## Software Development Examples

| Example | Purpose | Complexity |
|---------|---------|------------|
| [self-improvement.yaml](self-improvement.yaml) | Incremental codebase improvement with quality gates (pytest, mypy, ruff) | Medium |
| [sheet-review.yaml](sheet-review.yaml) | Multi-agent coordinated code review with expert agents | High |
| [worktree-isolation.yaml](worktree-isolation.yaml) | Parallel-safe execution using git worktrees | Medium |
| [parallel-research-fanout.yaml](parallel-research-fanout.yaml) | Fan-out: parameterized parallel stages without sheet duplication | Medium |
| [parallel-research.yaml](parallel-research.yaml) | Multi-source parallel research with independent sheets | Medium |
| [observability-demo.yaml](observability-demo.yaml) | Demonstrating logging, error tracking, and diagnostics | Medium |
| [issue-fixer.yaml](issue-fixer.yaml) | GitHub issue fixing workflow | Medium |
| [agent-spike.yaml](agent-spike.yaml) | Agent experimentation and exploration | Medium |
| [docs-generator.yaml](docs-generator.yaml) | Documentation generation orchestration | Medium |
| [issue-solver.yaml](issue-solver.yaml) | Roadmap-driven 17-stage issue solver with fan-out reviewers and self-chaining | High |
| [fix-deferred-issues.yaml](fix-deferred-issues.yaml) | Parallel bug fixing across 3 categories with quality gates | High |
| [fix-observability.yaml](fix-observability.yaml) | 13-stage observability improvements with parallel reviewers | High |
| [phase3-wiring.yaml](phase3-wiring.yaml) | Wire GlobalSheetScheduler and RateCoordinator into daemon | High |

## Quality & Continuous Improvement

| Example | Purpose | Complexity |
|---------|---------|------------|
| [quality-continuous.yaml](quality-continuous.yaml) | Backlog-driven quality improvement for Python projects (18 sheets, fan-out + tool agents) | High |
| [quality-continuous-generic.yaml](quality-continuous-generic.yaml) | Language-agnostic quality improvement with parallel reviews (16 sheets, fan-out) | High |
| [quality-daemon.yaml](quality-daemon.yaml) | Quality improvement via daemon mode | High |
| [quality-continuous-daemon.yaml](quality-continuous-daemon.yaml) | Continuous quality improvement with daemon orchestration | High |

## Beyond Coding

Mozart is a general-purpose cognitive orchestration system. These examples demonstrate Mozart's capabilities in domains beyond software development.

| Example | Domain | Sheets | Key Innovation |
|---------|--------|--------|----------------|
| [systematic-literature-review.yaml](systematic-literature-review.yaml) | Research | 8 | PRISMA-compliant dual-reviewer validation |
| [training-data-curation.yaml](training-data-curation.yaml) | Data | 7 | Inter-annotator agreement metrics |
| [nonfiction-book.yaml](nonfiction-book.yaml) | Writing | 8 | Snowflake Method with word count gates |
| [strategic-plan.yaml](strategic-plan.yaml) | Planning | 8 | Multi-framework synthesis (PESTEL, Porter, SWOT) |

---

## Example Categories

### Writing & Authoring

**[nonfiction-book.yaml](nonfiction-book.yaml)** - Author a non-fiction book using the Snowflake Method

- Progressive elaboration from 25-word premise to 50,000+ word manuscript
- Entity bible for cross-chapter consistency tracking
- Word count gates at each phase (premise → synopsis → outline → drafts)
- Readability validation (Flesch-Kincaid targets)
- Consistency review loop: draft → audit → revision → polish

### Research & Analysis

**[systematic-literature-review.yaml](systematic-literature-review.yaml)** - PRISMA-compliant systematic review

- 8 phases aligned with PRISMA 2020 methodology
- Dual-reviewer simulation with inter-rater agreement (kappa ≥0.80)
- PICO framework for research question structuring
- Quality assessment using configurable frameworks (Cochrane RoB, etc.)
- PRISMA flow diagram arithmetic validation

### Data & Knowledge

**[training-data-curation.yaml](training-data-curation.yaml)** - ML training dataset creation

- Pilot-to-production progression (test guidelines before full annotation)
- Dual-annotator pattern with adjudication workflow
- Quantitative IAA metrics (Fleiss' kappa, Cohen's kappa, Krippendorff's alpha)
- Class imbalance detection and documentation
- Datasheet for Datasets framework (Gebru et al., 2021)

### Planning & Strategy

**[strategic-plan.yaml](strategic-plan.yaml)** - Multi-framework strategic planning

- Three-framework synthesis: PESTEL → Porter's Five Forces → SWOT
- SWOT synthesizes from prior analyses (not generated de novo)
- SMART goal validation (all 5 criteria enforced)
- Weighted criteria evaluation for strategic options
- Risk register with mitigation traceability

### Creative & Experimental

For scores exploring Mozart's expressive capabilities in non-traditional domains, see the [Mozart Score Playspace](https://github.com/Mzzkc/mozart-score-playspace):

| Score | Domain | Fan-Out Pattern | Description |
|-------|--------|----------------|-------------|
| [dialectic.yaml](https://github.com/Mzzkc/mozart-score-playspace/blob/main/scores/dialectic.yaml) | Philosophy | Adversarial | Hegelian dialectic: thesis → 3 independent antitheses → synthesis |
| [thinking-lab.yaml](https://github.com/Mzzkc/mozart-score-playspace/blob/main/scores/thinking-lab.yaml) | Meta-cognition | Perspectival | Multi-perspective analysis through 5 parallel lenses |
| [dinner-party.yaml](https://github.com/Mzzkc/mozart-score-playspace/blob/main/scores/dinner-party.yaml) | Hospitality | Functional | Parallel planning across menu, drinks, ambiance, logistics |
| [skill-builder.yaml](https://github.com/Mzzkc/mozart-score-playspace/blob/main/scores/skill-builder.yaml) | Education | Graduated | Progressive curriculum from guided to mastery |
| [worldbuilder.yaml](https://github.com/Mzzkc/mozart-score-playspace/blob/main/scores/worldbuilder.yaml) | Creative writing | Generative | Build fictional worlds through 5 independent creative lenses |

These scores include real output in their workspace directories.

---

## Quality Principles

All examples follow these anti-slop principles:

### 1. Validation at Every Stage
Each sheet has measurable success criteria. Not just "did it run" but "is it good."

```yaml
validations:
  - type: file_exists
    path: "{workspace}/05-data-extraction.md"
  - type: content_contains
    path: "{workspace}/03-screening-decisions.md"
    pattern: "kappa"  # Ensures agreement metric is calculated
```

### 2. Human-Reviewable Output
Intermediate files for inspection. Every phase produces artifacts you can examine before the next phase runs.

### 3. Clear Quality Gates
Quantitative thresholds where possible:
- Inter-rater agreement: κ ≥ 0.80
- Word count minimums: 4000-6000 words per chapter
- Framework coverage: all 6 PESTEL dimensions addressed

### 4. Evidence-Based Content
Citations and sources where applicable. Explicit requirements for evidence:
- Claims must cite sources
- Judgments must reference data
- Fabrication is explicitly forbidden

### 5. Iterative Refinement
Multi-pass improvement built into workflow:
- Draft → Review → Revision patterns
- Pilot → Full execution progressions
- Consistency audits between phases

---

## Running Examples

```bash
# Validate before running (always!)
mozart validate examples/[example].yaml

# Run the example
mozart run examples/[example].yaml

# Check status (conductor tracks the workspace automatically)
mozart status [job-name]

# Resume if interrupted
mozart resume [job-name]
```

### Running Long Jobs

For jobs that take hours (literature reviews, book authoring), run detached:

```bash
# Detached execution (survives session changes)
setsid mozart run examples/nonfiction-book.yaml > book-workspace/mozart.log 2>&1 &

# Monitor progress
mozart status nonfiction-book --watch
```

### Pausing and Modifying Jobs

Jobs can be paused gracefully and resumed with updated configuration:

```bash
# Pause a running job
mozart pause nonfiction-book

# Modify the config and resume in one step
mozart modify nonfiction-book -c examples/nonfiction-book-v2.yaml -r

# Or use the two-step workflow for inspection
mozart pause my-job
# Inspect state, make config changes...
mozart resume my-job --config updated.yaml
```

**When to use pause/modify:**
- Mid-job configuration tweaks (change model, adjust prompts)
- Resource constraints (pause overnight, resume in morning)
- Error recovery (pause, fix config issue, resume with fixed config)

---

## Creating Your Own

Use these examples as templates. Key customization points:

### 1. Workspace
Where outputs go. Each job needs its own workspace.

```yaml
workspace: "./workspaces/my-project-workspace"
```

### 2. Instrument Configuration
Which AI tool to use and how to configure it.

```yaml
# Use any registered instrument (see `mozart instruments list`)
instrument: claude-code
instrument_config:
  timeout_seconds: 3600  # 60 min for complex tasks
  # model: claude-sonnet-4-20250514  # Optional model override
```

### 3. Variables
Domain-specific context. All examples use `prompt.variables` for user customization:

```yaml
prompt:
  variables:
    research_topic: "Your specific topic here"
    target_audience: "Who this is for"
    # ... domain-specific parameters
```

### 4. Validations
Quality criteria for your use case:

```yaml
validations:
  # File must exist
  - type: file_exists
    path: "{{ workspace }}/output.md"

  # File must contain specific content
  - type: content_contains
    path: "{{ workspace }}/output.md"
    pattern: "conclusion"

  # Command must succeed
  - type: command_succeeds
    command: "wc -w {{ workspace }}/output.md | awk '$1 >= 5000'"
```

---

## Comprehensive Documentation

For detailed Mozart usage, debugging, and configuration:

- **[../skills/mozart-usage.md](../skills/mozart-usage.md)** - Comprehensive Mozart guide
- **[../CLAUDE.md](../CLAUDE.md)** - Project-specific instructions and debugging protocols

---

## Validation Summary

All examples pass `mozart validate`:

| Example | Status | Description |
|---------|--------|-------------|
| simple-sheet.yaml | ✓ | Minimal configuration |
| api-backend.yaml | ✓ | Anthropic API backend |
| self-improvement.yaml | ✓ | Codebase improvement with quality gates |
| sheet-review.yaml | ✓ | Multi-agent code review |
| worktree-isolation.yaml | ✓ | Git worktree parallel execution |
| parallel-research-fanout.yaml | ✓ | Fan-out parameterized stages |
| parallel-research.yaml | ✓ | Multi-source parallel research |
| observability-demo.yaml | ✓ | Logging and diagnostics demo |
| issue-fixer.yaml | ✓ | GitHub issue fixing |
| agent-spike.yaml | ✓ | Agent experimentation |
| docs-generator.yaml | ✓ | Documentation generation |
| quality-continuous.yaml | ✓ | Python quality improvement (fan-out) |
| quality-continuous-generic.yaml | ✓ | Language-agnostic quality (fan-out) |
| quality-daemon.yaml | ✓ | Daemon mode quality improvement |
| quality-continuous-daemon.yaml | ✓ | Continuous quality via daemon |
| systematic-literature-review.yaml | ✓ | PRISMA-compliant research |
| training-data-curation.yaml | ✓ | ML dataset creation |
| nonfiction-book.yaml | ✓ | Non-fiction book authoring |
| strategic-plan.yaml | ✓ | Multi-framework strategic planning |
| issue-solver.yaml | ✓ | Roadmap-driven issue solving with fan-out |
| fix-deferred-issues.yaml | ✓ | Parallel bug fixing with quality gates |
| fix-observability.yaml | ✓ | Observability gap fixes with parallel reviews |
| phase3-wiring.yaml | ✓ | Daemon scheduler/rate-coordinator wiring |

---

*Examples created as part of Mozart Example Expansion project - 2026-01-23*
