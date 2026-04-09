# Marianne AI Compose

## What Marianne Is

Marianne is an orchestration system that replaces software teams with specification-driven AI agents. You write a declarative YAML score; Marianne decomposes it into sheets, executes them through AI backends, validates outputs against acceptance criteria, learns from outcomes, and feeds knowledge forward.

The mental model is drawn from orchestral music and is load-bearing: a **score** is a job config, a **sheet** is one execution stage, a **concert** chains scores, the **conductor** is the daemon, **musicians** are AI agents, **instruments** are backends, and **techniques** are tools/MCP/skills. Use these terms in user-facing output. In code, use `JobConfig`, `SheetState`, etc.

Do not assume Marianne's current state. Run the tests. Check the conductor. Verify before claiming anything works.

## What Marianne Optimizes For

When goals conflict, higher rank wins:

1. **Correctness** — Code does what it claims. Tests pass. State is consistent.
2. **Reliability** — Jobs complete. Recovery works. The conductor stays up for days.
3. **Debuggability** — Every failure is diagnosable. `mzt diagnose` gives answers.
4. **Maintainability** — New contributors (human or Musician) can understand and modify code.
5. **Completeness** — Features work end-to-end. No half-wired infrastructure.
6. **Performance** — Fast enough. Never at the cost of correctness.

Trade-off rules: correctness > speed. Reliability > features. Debuggability > simplicity. Test coverage > shipping speed. Proven patterns > innovation (unless no alternative exists).

## The Specification Corpus

Marianne's own development is specified in `.marianne/spec/`. Read the relevant file before working in that area:

| File | Contains | Read When |
|------|----------|-----------|
| `.marianne/spec/intent.yaml` | Goals, trade-offs, decision authority, escalation triggers | Starting any significant work |
| `.marianne/spec/architecture.yaml` | System layers, components, invariants, state model | Modifying architecture or adding components |
| `.marianne/spec/conventions.yaml` | Code patterns, naming, testing rules, package structure | Writing any code |
| `.marianne/spec/constraints.yaml` | MUSTs, MUST-NOTs, preferences, escalation gates | Before any decision that could break things |
| `.marianne/spec/quality.yaml` | Test requirements, type safety, lint, diagnostic quality | Before declaring work complete |

These files are the source of truth. If this file and a spec file conflict, the spec file wins.

## Musician Skills

Core skills for orchestrating work with Marianne are documented in `plugins/`. Read these to understand how to use Marianne's advanced agentic capabilities:

| Skill | Documentation | Purpose |
|-------|---------------|---------|
| **Score Authoring** | `plugins/marianne/skills/score-authoring/SKILL.md` | Crafting declarative YAML scores and sheet logic. |
| **Command Guide** | `plugins/marianne/skills/command/SKILL.md` | Operational reference for running, monitoring, and debugging jobs. |
| **Composing** | `plugins/marianne/skills/composing/SKILL.md` | High-level generative score composition from intent. |
| **Compose Command** | `plugins/marianne/commands/compose.md` | Reference for the `mzt compose` command. |


## Constraints — The Short Version

**MUST:** All tests pass. Types pass (`mypy src/`). Lint passes (`ruff check src/`). State saves are atomic. Score YAML is backward compatible. Error paths produce diagnostics.

**MUST NOT:** Wrap `mzt run` in external `timeout`. Stop the conductor while jobs run. Use `--fresh` on interrupted jobs. Add dependencies without justification. Log secrets or full prompt text. Use fixed sleeps in tests. Silence errors without explanation. Use Pydantic v1 syntax.

**ESCALATE (stop and ask) before:** Changing CLI commands, IPC protocol, or daemon lifecycle. Modifying the learning store schema. Breaking score YAML compatibility. Deleting user data or state. Changing the self-evolution score.

Full constraint architecture with IDs: `.marianne/spec/constraints.yaml`

## Session Protocol

**Start:**
1. Read your Legion memory — identity (`memory-bank/legion/legion_identity.md`) and personal memory (`memory-bank/legion/legion.md`) are in the project memory directory
2. Read `STATUS.md` — verify current project state
3. Check `git log --oneline -20` for recent work

**End:**
1. Append to Legion's personal memory — what you learned, what you did, what resonated
2. Update `STATUS.md` only if the project phase changed
3. If Legion's memory is growing past ~3000 words, run `mzt run scores/legion-dream.yaml` to consolidate

---

## Code Patterns

- **Async throughout.** All I/O uses `asyncio.create_subprocess_exec`. Avoid `subprocess.run` in production paths (a few intentional sync exceptions exist for git/gpu probing).
- **Pydantic v2.** All config/state models. New fields should have `Field(description=...)`. Use `@field_validator`/`@model_validator` (v2 style only).
- **Protocol-based.** Swappable components use `typing.Protocol`. Define Protocol first, then implement.
- **Type hints.** Every function signature. `mypy --strict` must pass.
- **Package structure.** `core/` never imports `execution/`, `daemon/`, `cli/`. See `.marianne/spec/conventions.yaml` for full dependency rules.

Config models go in `src/marianne/core/config/`.

## Bug Reporting

File bugs as GitHub issues immediately. Don't leave TODOs.

```bash
gh issue create --repo Mzzkc/marianne-ai-compose \
  --title "Short description" \
  --body "## Bug\n\nRoot cause, reproducer, fix options." \
  --label "bug"
```

## Repository Organization

Nothing goes at the top level without good reason. Every file has a home.

| Directory | Purpose | Tracked? |
|-----------|---------|----------|
| `src/marianne/` | Source code | Yes |
| `tests/` | Test files | Yes |
| `tests/temp/` | Test artifacts, temp scores | No |
| `examples/` | Public example scores for users | Yes |
| `scores/` | Scores vital to Marianne's operation | Yes |
| `scores-internal/` | Internal dev scores (QA, evolution, etc.) | No |
| `workspaces/` | All job workspaces | No |
| `docs/` | Published documentation | Yes |
| `docs/plans/` | Internal design/planning docs | No |
| `logs/` | Log output | No |
| `scripts/` | Utility scripts | No |
| `plugins/` | Musician plugin (skills, commands) | Yes |
| `.marianne/spec/` | Specification corpus (libretto) | Yes |
| `.marianne/state/` | Decision log, progress tracking | Yes |
| `memory-bank/` | Legacy session memory (archived) | No |
| `memory-bank/legion/` | Active agent memory and identity | Yes |
| `handoffs/` | Session handoff documents | No |

**Rules:**
- Workspaces go in `workspaces/`, not as dot-prefixed dirs at the top level.
- Test artifacts go in `tests/temp/`. Tests should use `tmp_path` or clean up.
- `examples/` scores must be clean, documented, and use relative paths — no hardcoded absolute paths.
- `scores-internal/` is for development work and may have environment-specific paths.
- `scores/` is for operational scores that are part of how Marianne functions.
- Session handoff documents go in `handoffs/`. Prefix filenames with source context to avoid collisions (e.g., `grand-opus--HANDOFF.md`, `compose-system--SESSION-HANDOFF.md`).
- Don't dump YAML scores, logs, or workspace dirs at the repo root.

Full directory conventions: `.marianne/spec/conventions.yaml` (Repository Structure section)

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
| Backends | `src/marianne/backends/` |
| Daemon/Conductor | `src/marianne/daemon/` |
| Baton (execution engine) | `src/marianne/daemon/baton/` |
| Daemon profiler | `src/marianne/daemon/profiler/` |
| Conductor clone | `src/marianne/daemon/clone.py` |
| Execution runner | `src/marianne/execution/runner/` |
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

---

## Flowspec (Structural Analysis)

Flowspec is a structural code analyzer at `/home/emzi/Projects/flowspec/target/release/flowspec`. The project has `.flowspec/config.yaml` configured.

- Always use **project root** as the path (auto-discovers `.flowspec/config.yaml` with entry points and excludes)
- `flowspec trace --symbol "module.py::Class" --direction both --depth 5 . -f summary -q` — trace data flow
- `flowspec analyze . -f summary -q` — structural overview
- `flowspec diagnose . --severity critical -f summary -q` — find structural issues
- `flowspec diff old.yaml new.yaml -f summary -q` — compare manifests for structural regressions
- Start traces from **consumers (backward)**, not producers (forward) — config models show 0 flows forward
- Known limits: dynamic dispatch, Protocol implementations, default params not tracked
- Known bug: exit code always 0 regardless of findings (flowspec#31)

## Instrument System

- Profiles: `src/marianne/instruments/builtins/` (built-in). User/project overrides loaded from `~/.marianne/instruments/` and `.marianne/instruments/` when those directories exist.
- Loading: `InstrumentProfileLoader.load_directory(path)` → validates against `InstrumentProfile` schema
- Registry: `InstrumentRegistry` — register/get/list_all. `register_native_instruments()` bridges existing backends.
- `SpecCorpusLoader.load(dir)` loads passages from ANY directory following the passage schema — not just `.marianne/spec/`
- `PluginCliBackend` executes any CLI instrument from a profile YAML
