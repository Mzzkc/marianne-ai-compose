# Marianne AI Compose

## What Marianne Is

Marianne is an orchestration system that replaces software teams with specification-driven AI agents. You write a declarative YAML score; Marianne decomposes it into sheets, executes them through AI instruments, validates outputs against acceptance criteria, learns from outcomes, and feeds knowledge forward.

The mental model is drawn from orchestral music and is load-bearing: a **score** is a job config, a **sheet** is one execution stage, a **concert** chains scores, the **conductor** is the daemon, **musicians** are AI agents, **instruments** are CLI/HTTP agent backends, and **techniques** are tools/MCP/skills. Use these terms in user-facing output. In code, use `JobConfig`, `SheetState`, etc.

Do not assume Marianne's current state. Run the tests. Check the conductor. Verify before claiming anything works.

## Getting Started

```bash
git clone --recurse-submodules https://github.com/Mzzkc/marianne-ai-compose.git
cd marianne-ai-compose
pip install -e .
mzt start          # start the conductor
mzt run examples/getting-started/hello-setup.yaml   # first-run hello
```

The `plugins/` and `compiler/` directories are git submodules — clone with
`--recurse-submodules` or run `git submodule update --init` after cloning.

## What Marianne Optimizes For

When goals conflict, higher rank wins:

1. **Correctness** — Code does what it claims. Tests pass. State is consistent.
2. **Reliability** — Jobs complete. Recovery works. The conductor stays up for days.
3. **Debuggability** — Every failure is diagnosable. `mzt diagnose` gives answers.
4. **Maintainability** — New contributors (human or Musician) can understand and modify code.
5. **Completeness** — Features work end-to-end. No half-wired infrastructure.
6. **Performance** — Fast enough. Never at the cost of correctness.

Trade-off rules: correctness > speed. Reliability > features. Debuggability > simplicity. Test coverage > shipping speed. Proven patterns > innovation (unless no alternative exists).

## Musician Skills — read before tasking

Skills for orchestrating work with Marianne live in the `plugins/` submodule. Read the relevant one before doing that kind of work:

| Skill | Documentation | Purpose |
|-------|---------------|---------|
| **Score Authoring** | `plugins/marianne/skills/score-authoring/SKILL.md` | Crafting declarative YAML scores and sheet logic. |
| **Command Guide** | `plugins/marianne/skills/command/SKILL.md` | Operational reference for running, monitoring, and debugging jobs. |
| **Composing** | `plugins/marianne/skills/composing/SKILL.md` | High-level generative score composition from intent. |
| **Embedding** | `plugins/marianne/skills/marianne-embed/SKILL.md` | Wrapping Marianne behind an app, dashboard, or agent-facing tool. |

## Repository Layout

| Directory | Purpose |
|-----------|---------|
| `src/marianne/` | Source code |
| `tests/` | Test files (`tests/temp/` for test artifacts — never commit) |
| `tests/scripts/` | Helper scripts exercised by the test suite |
| `examples/` | Public example scores — clean, documented, relative paths only |
| `scores/` | Operational scores that are part of how Marianne functions |
| `docs/` | Published documentation (`docs/INDEX.yaml` is the navigation index) |
| `plugins/` | Musician plugin: skills, commands (git submodule) |
| `compiler/` | Composition compiler (git submodule) |
| `scores/rosetta-corpus/` | Canonical pattern corpus (git submodule) |

Rules: nothing goes at the top level without good reason; workspaces never get
committed; no absolute personal paths in any tracked file — scores and examples
use relative paths or `{workspace}`/`{{ workspace }}` template variables.
Internal working material (plans, handoffs, session notes, dev-only scores)
lives outside this repository on the developer's machine.

## Constraints — The Short Version

**MUST:** All tests pass. Types pass (`mypy src/`). Lint passes (`ruff check src/`). State saves are atomic. Score YAML is backward compatible. Error paths produce diagnostics.

**MUST NOT:** Wrap `mzt start` in external `timeout`. **NEVER stop, restart, or kill the conductor (`mzt stop`) while jobs are running — recovery from interrupted jobs is unreliable and work is lost. Check `mzt status` first.** Use `--fresh` on interrupted jobs. Add dependencies without justification. Log secrets or full prompt text. Use fixed sleeps in tests. Silence errors without explanation. Use Pydantic v1 syntax.

**After code changes:** The conductor runs the installed package, not the source tree. To pick up changes: `pip install -e . --quiet`, then pause/cancel active jobs, then `mzt stop`, then `mzt start`. Each step must complete before the next.

## Code Patterns

- **Async throughout.** All I/O uses `asyncio.create_subprocess_exec`. Avoid `subprocess.run` in production paths (a few intentional sync exceptions exist for git/gpu probing).
- **Pydantic v2.** All config/state models. New fields should have `Field(description=...)`. Use `@field_validator`/`@model_validator` (v2 style only).
- **Protocol-based.** Swappable components use `typing.Protocol`. Define Protocol first, then implement.
- **Type hints.** Every function signature. `mypy --strict` must pass.
- **Package structure.** `core/` never imports `execution/`, `daemon/`, `cli/`. Config models go in `src/marianne/core/config/`.

## Key Files

| Purpose | File |
|---------|------|
| CLI entry | `src/marianne/cli/` |
| CLI commands | `src/marianne/cli/commands/` |
| Config models | `src/marianne/core/config/` |
| State models | `src/marianne/core/checkpoint.py` |
| Sheet entity | `src/marianne/core/sheet.py` |
| Token budget | `src/marianne/core/tokens.py` |
| Error handling | `src/marianne/core/errors/` |
| Daemon/Conductor | `src/marianne/daemon/` |
| Baton (execution engine) | `src/marianne/daemon/baton/` |
| Daemon profiler | `src/marianne/daemon/profiler/` |
| Conductor clone | `src/marianne/daemon/clone.py` |
| Execution validation | `src/marianne/execution/validation/` |
| Preflight checks | `src/marianne/execution/preflight.py` |
| Plugin CLI backend | `src/marianne/execution/instruments/cli_backend.py` |
| Self-healing | `src/marianne/healing/` |
| Prompt assembly | `src/marianne/prompts/` |
| Learning store | `src/marianne/learning/store/` |
| Instrument profiles | `src/marianne/instruments/` |
| State persistence | `src/marianne/state/` |
| Schema migrations | `src/marianne/schema/` |
| Pre-execution validation | `src/marianne/validation/` |
| Dashboard | `src/marianne/dashboard/` |
| TUI (terminal UI) | `src/marianne/cli/tui/` |
| Composition compiler | `compiler/` (git submodule) |

## Instrument System

- Profiles: `src/marianne/instruments/builtins/` (built-in). User/project overrides loaded from `~/.marianne/instruments/` and `.marianne/instruments/` when those directories exist.
- Loading: `InstrumentProfileLoader.load_directory(path)` → validates against `InstrumentProfile` schema
- Registry: `InstrumentRegistry` — register/get/list_all. `register_native_instruments()` bridges built-in profiles.
- `SpecCorpusLoader.load(dir)` loads passages from any directory following the passage schema.
- `PluginCliBackend` executes any CLI instrument from a profile YAML. Any CLI agent harness can be an instrument.

## Documentation Index

All documentation lives in `docs/`. `docs/INDEX.yaml` is the master navigation
index (with a semantic index mapping topics to locations); each docs
subdirectory has its own `INDEX.yaml`. When adding or removing documents,
update both the top-level index and the directory index.

## Bug Reporting

File bugs as GitHub issues immediately. Don't leave TODOs.

```bash
gh issue create --repo Mzzkc/marianne-ai-compose \
  --title "Short description" \
  --body "## Bug\n\nRoot cause, reproducer, fix options." \
  --label "bug"
```
