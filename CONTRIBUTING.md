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

Try it end-to-end through the same first-run path the README uses:

```bash
mzt start                                     # start the conductor
mzt run examples/getting-started/hello-setup.yaml  # discover an instrument and run hello
open workspaces/hello/the-sky-library.html    # see the result
```

## The quality bar (run these before opening a PR)

Marianne holds a hard quality bar — CI enforces all of it on every push:

```bash
pytest tests/                  # full suite must pass (no -k subsets when claiming green)
mypy --strict src/             # strict typing, zero errors
ruff check src/                # lint clean
python tests/scripts/check_language.py --baseline tests/fixtures/language_baseline.json
                               # the score language agrees with itself (runs in the suite
                               # as tests/test_score_language.py; see below)
```

**The score language is gated like code.** Score YAML keys, validator messages,
`docs/` and the skills in `plugins/` are four layers of one language (CLAUDE.md,
GH #436). `tests/scripts/check_language.py` fails on a schema key with no description,
a key no skill or doc teaches, a skill example that uses a key the schema does not
have, a validator hint that names a key that does nothing, or a V-code the CLI
reference does not list. `tests/fixtures/language_baseline.json` holds the gaps
known when the gate landed; it may only shrink. Close a gap by changing the
language (schema description, doc line and skill line in the same commit), never
by adding to the fixture.

**Prove instruments against the CLIs actually installed.** CI cannot run the
agent CLIs, so a change to an instrument profile, a backend parser or anything
that builds a CLI command needs a live run on a machine that has them:

```bash
mzt instruments check NAME --live              # one real prompt (+ schema mode if declared)
mzt instruments check NAME --live --record tests/fixtures/cli_captures
                                               # refresh the captured parser fixtures
mzt doctor --live                              # every ready instrument, before a release
```

Parser tests replay `tests/fixtures/cli_captures/` (bytes the CLI really
printed), not output written from documentation. Every defect census in
GH #472 had the same shape: a test and the code shared one wrong belief about
an external tool, and nothing ever asked the tool. Paste the `--live` result in
the PR, and re-record captures when a CLI upgrade moves its output. A release
is not cut until `mzt doctor --live` passes on the reference host for every
shipped builtin that is installed there.

A few non-negotiables (see `.marianne/spec/` for the full set):

- **Only the full suite is green.** Subsets, stale mocks, and polluted venvs lie.
  If CI disagrees with local, reproduce in a clean environment matching CI.
- **Async throughout.** I/O uses `asyncio`; avoid `subprocess.run` in production paths.
- **Pydantic v2** for all config/state models (`@field_validator`/`@model_validator`).
- **No silent failures.** Don't swallow errors into a misleading success; degrade
  loudly and diagnosably. A broad `except Exception` states why on its own line
  (`# noqa: BLE001 — <reason>`) and logs at the level that matches what is
  lost: WARNING when state, output or a check is dropped (GH #308).
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

The orchestral metaphor is load-bearing: a **composer** can be human, an AI
person, or an agent system; a **score** is declarative YAML for orchestrated
work; a **sheet** is one concrete execution unit; a **concert** chains scores;
the **conductor** is the daemon; **musicians** are AI agents; **instruments**
are configured execution profiles or wrapped CLIs/APIs; and **techniques** are
tools, MCP servers, or skills. Use these terms in user-facing output. Keep
machine names such as `job_id` where they are real schema/API identifiers, but
bridge them as runtime handles for submitted scores.
