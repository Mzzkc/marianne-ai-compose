# Marianne Examples

These examples show how to orchestrate multi-agent AI workflows for any kind
of knowledge work — engineering, research, creative writing, business analysis,
and more. Each score is a declarative YAML config that decomposes work into
parallel and sequential stages, validates outputs, and produces integrated
results no single agent could reach alone.

## Quick Start

```bash
pip install marianne-ai-compose
mzt start
mzt run examples/getting-started/hello.yaml
```

## Categories

| Category | What It Covers | Examples | Start Here |
|----------|---------------|----------|------------|
| [getting-started](getting-started/README.md) | Score structure, parallel execution, context passing, API backends, observability, worktree isolation | 7 | `hello.yaml` |
| [creative](creative/README.md) | Philosophical argumentation, worldbuilding, fiction, dinner planning, skill teaching, literary translation, interactive fiction | 7 | `hello-marianne.yaml` |
| [research](research/README.md) | Literature reviews, strategic planning, research synthesis, training data curation, nonfiction authoring, context architecture, multi-source deep research | 7 | `parallel-research-fanout.yaml` |
| [engineering](engineering/README.md) | Issue resolution, quality improvement, score generation, codebase rewrites, SaaS app building, web app generation | 6 | `score-composer.yaml` |
| [patterns](patterns/README.md) | Named Rosetta orchestration patterns: Immune Cascade, Echelon Repair, Source Triangulation, Dead Letter Quarantine, Prefabrication, Shipyard Sequence, Rashomon Gate | 7 | `shipyard-sequence.yaml` |
| [product](product/README.md) | Candidate screening, contract generation, invoice analysis, marketing content | 4 | `candidate-screening.yaml` |
| [advanced](advanced/README.md) | Explicit dependency DAGs, multi-instrument routing, echelon-tiered analysis, multi-source convergence | 2 | `instrument-showcase.yaml` |
| [finance/24x7-trader](finance/24x7-trader/README.md) | **Flagship family.** Six scores operating an autonomous swing-trading agent: deterministic risk-envelope enforcement, three-frame Source Triangulation pre-market, Red Team / Blue Team adversarial review at the open, two-round Delphi weekly retros, file-based fermata. Vendor-neutral via technique contracts. Paper-safe by default. | 1 family (6 scores) | `finance/24x7-trader/README.md` |

40 examples + 1 flagship family across 8 categories.

## Running Examples

```bash
# Start the conductor (runs in background)
mzt start

# Run any example by path
mzt run examples/getting-started/simple-sheet.yaml

# Watch execution progress in real time
mzt status simple-sheet --watch

# View results in the workspace
ls workspaces/simple-sheet/
```

Every score declares its own `workspace:` path where outputs land. Scores
with multiple stages write intermediate files there too, so you can inspect
each stage's contribution. Use `mzt logs <job>` for structured logging and
`mzt diagnose <job>` when something fails.

## Patterns

Many scores implement named orchestration patterns from the Rosetta corpus
— structural coordination moves with known forces, trade-offs, and composition
rules. Patterns solve recurring problems in multi-agent coordination:

- **Fan-out + Synthesis** — parallelize independent sub-problems, then integrate diverse perspectives
- **Succession Pipeline** — sequential substrate transformations where each stage requires different methods
- **Immune Cascade** — cheap broad sweeps narrow scope before expensive targeted investigation
- **Prefabrication** — parallel tracks coordinate via shared interface contracts
- **Source Triangulation** — independent evidence sources cross-validate claims
- **Echelon Repair** — graduated response routing work to the right tier by complexity
- **Cathedral Construction** — iterative build loops with self-chaining and convergence gates
- **Commissioning Cascade** — multi-scope validation from unit to integration to semantic

The `patterns/` category contains faithful proof-of-concept implementations
of seven patterns. Other categories use patterns where they fit — each score's
header comments name which patterns it applies and why.

Full corpus: `scores/rosetta-corpus/`

## Creating Your Own

All examples use `prompt.variables` for customization. To adapt an existing
score to your own work:

1. Copy the score file
2. Edit `prompt.variables` — look for `[CHANGE THIS: ...]` markers or replace defaults
3. Update the `workspace:` path
4. Run: `mzt run your-score.yaml`

No template changes needed — data lives in variables, logic stays in
templates. To write scores from scratch, see `docs/score-writing-guide.md`
or invoke `/marianne:score-authoring` in your Claude Code session for
interactive guidance.

## Quality

All 40 examples validate clean with `mzt validate`. Every score uses
`instrument_fallbacks` for resilience — if the primary instrument is
unavailable, execution falls back automatically. Validation paths use correct
syntax (`{workspace}` for Python format strings, `{{ workspace }}` for Jinja2
templates). No hardcoded paths, no undefined template variables, no
placeholder prompts.

Scores in `patterns/`, `research/`, and `engineering/` include substantive
validations beyond `file_exists` — content checks, structural regex, and
command-based verification that outputs contain real work.

All examples passed three-stage commissioning validation:

1. **Syntax**: YAML parses, Pydantic schemas validate, no critical errors
2. **Structure**: Required fields present (instrument, movements, dependencies, fallbacks)
3. **Compliance**: Generic/reusable, meaningful validations, real work prompts, professional content
