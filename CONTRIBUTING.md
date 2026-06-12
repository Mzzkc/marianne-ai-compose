# Contributing to Marianne

Thanks for your interest in Marianne — an orchestration system that turns
declarative YAML *scores* into coordinated work by AI agents. This guide gets
you from clone to a green pull request.

## Getting set up

```bash
git clone https://github.com/Mzzkc/marianne-ai-compose.git
cd marianne-ai-compose
pip install -e ".[daemon]"     # Marianne + the conductor daemon
```

Try it end-to-end (free — runs on a local model, no account needed):

```bash
mzt start                                     # start the conductor
mzt run examples/getting-started/hello.yaml   # orchestrate a tiny website
open workspaces/hello/the-sky-library.html    # see the result
```

## The quality bar (run these before opening a PR)

Marianne holds a hard quality bar — CI enforces all of it on every push:

```bash
pytest tests/                  # full suite must pass (no -k subsets when claiming green)
mypy --strict src/             # strict typing, zero errors
ruff check src/                # lint clean
```

A few non-negotiables (see `.marianne/spec/` for the full set):

- **Only the full suite is green.** Subsets, stale mocks, and polluted venvs lie.
  If CI disagrees with local, reproduce in a clean environment matching CI.
- **Async throughout.** I/O uses `asyncio`; avoid `subprocess.run` in production paths.
- **Pydantic v2** for all config/state models (`@field_validator`/`@model_validator`).
- **No silent failures.** Don't swallow errors into a misleading success; degrade
  loudly and diagnosably.
- **Tests over mocks for infrastructure code** — prefer real integration.

## How the project is specified

Marianne's own development is specified in `.marianne/spec/` — read the relevant
file before working in an area:

| File | Read when |
|------|-----------|
| `intent.yaml` | Starting any significant work (goals, trade-offs, escalation) |
| `architecture.yaml` | Modifying architecture or adding components |
| `conventions.yaml` | Writing any code (patterns, naming, package structure) |
| `constraints.yaml` | Before a decision that could break things (MUSTs / MUST-NOTs) |
| `quality.yaml` | Before declaring work complete (test/type/lint/diagnostic bars) |

Writing a score? Start with `plugins/marianne/skills/score-authoring/SKILL.md`
and the examples in `examples/getting-started/`.

## Pull requests

1. Branch from `main`.
2. Keep changes focused; match the surrounding code's style and conventions.
3. Add tests that pin the behavior you changed — especially the adversarial /
   failure-path cases.
4. Run the full quality bar above; make sure CI is green.
5. Describe **what** changed and **why** in the PR body, and reference any issue.

## Reporting bugs

File issues at <https://github.com/Mzzkc/marianne-ai-compose/issues>. Please
include a root cause or reproducer where you can — Marianne values
debuggability, and a precise report is itself a contribution.

## Vocabulary

The orchestral metaphor is load-bearing: a **score** is a job config, a **sheet**
is one execution stage, a **concert** chains scores, the **conductor** is the
daemon, **musicians** are AI agents, **instruments** are backends/CLIs, and
**techniques** are tools/skills. Use these terms in user-facing output.
