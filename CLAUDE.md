# Mozart AI Compose

## What Mozart Is

Mozart is an orchestration system that replaces software teams with specification-driven AI agents. You write a declarative YAML score; Mozart decomposes it into sheets, executes them through AI backends, validates outputs against acceptance criteria, learns from outcomes, and feeds knowledge forward.

The mental model is drawn from orchestral music and is load-bearing: a **score** is a job config, a **sheet** is one execution stage, a **concert** chains scores, the **conductor** is the daemon, **musicians** are AI agents, **instruments** are backends, and **techniques** are tools/MCP/skills. Use these terms in user-facing output. In code, use `JobConfig`, `SheetState`, etc.

Mozart runs production workloads today. 24 self-evolution cycles completed autonomously. 3384+ tests. The system works. The current focus is making it operate at all four levels of the AI Input Engineering stack: prompt craft, context engineering, intent engineering, and specification engineering — so that every agent Mozart spawns is fully set up for success, not just handed a prompt and wished luck.

## What Mozart Optimizes For

When goals conflict, higher rank wins:

1. **Correctness** — Code does what it claims. Tests pass. State is consistent.
2. **Reliability** — Jobs complete. Recovery works. The conductor stays up for days.
3. **Debuggability** — Every failure is diagnosable. `mozart diagnose` gives answers.
4. **Maintainability** — New contributors (human or AI) can understand and modify code.
5. **Completeness** — Features work end-to-end. No half-wired infrastructure.
6. **Performance** — Fast enough. Never at the cost of correctness.

Trade-off rules: correctness > speed. Reliability > features. Debuggability > simplicity. Test coverage > shipping speed. Proven patterns > innovation (unless no alternative exists).

## The Specification Corpus

Mozart's own development is specified in `.mozart/spec/`. Read the relevant file before working in that area:

| File | Contains | Read When |
|------|----------|-----------|
| `.mozart/spec/intent.yaml` | Goals, trade-offs, decision authority, escalation triggers | Starting any significant work |
| `.mozart/spec/architecture.yaml` | System layers, components, invariants, state model | Modifying architecture or adding components |
| `.mozart/spec/conventions.yaml` | Code patterns, naming, testing rules, package structure | Writing any code |
| `.mozart/spec/constraints.yaml` | MUSTs, MUST-NOTs, preferences, escalation gates | Before any decision that could break things |
| `.mozart/spec/quality.yaml` | Test requirements, type safety, lint, diagnostic quality | Before declaring work complete |

These files are the source of truth. If CLAUDE.md and a spec file conflict, the spec file wins.

## Constraints — The Short Version

**MUST:** All tests pass. Types pass (`mypy src/`). Lint passes (`ruff check src/`). State saves are atomic. Score YAML is backward compatible. Error paths produce diagnostics.

**MUST NOT:** Wrap `mozart run` in external `timeout`. Stop the conductor while jobs run. Use `--fresh` on interrupted jobs. Add dependencies without justification. Log secrets or full prompt text. Use fixed sleeps in tests. Silence errors without explanation. Use Pydantic v1 syntax.

**ESCALATE (stop and ask) before:** Changing CLI commands, IPC protocol, or daemon lifecycle. Modifying the learning store schema. Breaking score YAML compatibility. Deleting user data or state. Changing the self-evolution score.

Full constraint architecture with IDs (M-001 through M-010, MN-001 through MN-012, P-001 through P-010, E-001 through E-008): `.mozart/spec/constraints.yaml`

## Session Protocol

**Start:**
1. Read `STATUS.md` — quick project status
2. Read `memory-bank/activeContext.md` — current state and focus
3. Read `memory-bank/projectbrief.md` if unfamiliar with project purpose
4. Check `memory-bank/progress.md` for what's been done

**End:**
1. Update `memory-bank/activeContext.md` with session results
2. Update `STATUS.md` if phase/component status changed
3. Add entry to `memory-bank/progress.md` if significant work

---

## Code Patterns

- **Async throughout.** All I/O uses `asyncio.create_subprocess_exec`. Never `subprocess.run` in production.
- **Pydantic v2.** All config/state models. Every field has `Field(description=...)`. Use `@field_validator`/`@model_validator` (v2 style only).
- **Protocol-based.** Swappable components use `typing.Protocol`. Define Protocol first, then implement.
- **Type hints.** Every function signature. `mypy --strict` must pass.
- **Package structure.** `core/` never imports `execution/`, `daemon/`, `cli/`. See `.mozart/spec/conventions.yaml` for full dependency rules.

Config models go in `src/mozart/core/config/` — `backend.py`, `execution.py`, `job.py`, `learning.py`, `orchestration.py`, `workspace.py`.

## Bug Reporting

File bugs as GitHub issues immediately. Don't leave TODOs.

```bash
gh issue create --repo Mzzkc/mozart-ai-compose \
  --title "Short description" \
  --body "## Bug\n\nRoot cause, reproducer, fix options." \
  --label "bug"
```

## Repository Organization

Nothing goes at the top level without good reason. Every file has a home.

| Directory | Purpose | Tracked? |
|-----------|---------|----------|
| `src/mozart/` | Source code | Yes |
| `tests/` | Test files | Yes |
| `tests/temp/` | Test artifacts, temp scores | No |
| `examples/` | Public example scores for users | Yes |
| `scores/` | Scores vital to Mozart's operation | Yes |
| `scores-internal/` | Internal dev scores (QA, evolution, etc.) | No |
| `workspaces/` | All job workspaces | No |
| `docs/` | Published documentation | Yes |
| `docs/plans/` | Internal design/planning docs | No |
| `logs/` | Log output | No |
| `scripts/` | Utility scripts | No |
| `skills/` | Mozart-specific skill files | Yes |
| `.mozart/spec/` | Specification corpus (libretto) | Yes |
| `.mozart/state/` | Decision log, progress tracking | Yes |
| `memory-bank/` | Session memory for agents | No |

**Rules:**
- Workspaces go in `workspaces/`, not as dot-prefixed dirs at the top level.
- Test artifacts go in `tests/temp/`. Tests should use `tmp_path` or clean up.
- `examples/` scores must be clean, documented, and use relative paths — no hardcoded absolute paths.
- `scores-internal/` is for development work and may have environment-specific paths.
- `scores/` is for operational scores that are part of how Mozart functions.
- Don't dump YAML scores, logs, or workspace dirs at the repo root.

Full directory conventions: `.mozart/spec/conventions.yaml` (Repository Structure section)

## Key Files

| Purpose | File |
|---------|------|
| CLI entry | `src/mozart/cli/` (package) |
| Conductor commands | `src/mozart/cli/commands/conductor.py` |
| Config models | `src/mozart/core/config/` (package: 6 modules) |
| State models | `src/mozart/core/checkpoint.py` |
| Error handling | `src/mozart/core/errors/` (package) |
| Execution runner | `src/mozart/execution/runner/` (package: base, sheet, lifecycle, recovery, cost, isolation, patterns, models) |
| Prompt assembly | `src/mozart/prompts/` (preamble.py, templating.py) |
| Learning store | `src/mozart/learning/store/` (14 modules) |
| Claude backend | `src/mozart/backends/claude_cli.py` |
| Daemon | `src/mozart/daemon/` (package, ~20 modules) |
| Dashboard | `src/mozart/dashboard/` |
| Spec corpus | `.mozart/spec/` (5 YAML files) |

## Running Mozart

The conductor is required for `mozart run`. Without it, only `--dry-run` and `mozart validate` work.

```bash
mozart start                    # Start conductor
mozart run my-job.yaml          # Submit job (routes through conductor)
mozart status my-job -w ./ws    # Check status
mozart pause my-job -w ./ws     # Pause gracefully
mozart resume my-job -w ./ws    # Resume from checkpoint
mozart stop                     # Stop conductor (ONLY when no jobs are running)
```

**NEVER** stop the conductor while jobs run — orphans agents, corrupts state. Pause first.

**NEVER** use `--fresh` on interrupted jobs — destroys checkpoint state. Use `mozart resume`.

For detached startup: `setsid mozart start &`

## Debugging

Use Mozart's tools FIRST:

```bash
mozart status <job-id> -w <dir>     # 1. What happened?
mozart diagnose <job-id> -w <dir>   # 2. Full diagnostic report
mozart errors <job-id> --verbose    # 3. Error history
```

Then investigate code if needed. Invoke `/mozart:command` for comprehensive guidance.

## Testing

```bash
pytest tests/ -x          # Run all tests
mypy src/                 # Type check
ruff check src/           # Lint
pip install -e ".[dev]"   # Install dev deps
```

Tests MUST be deterministic:
- **No fixed sleeps** for async coordination — poll with deadline
- **No tight timing assertions** — use generous bounds (10x expected)
- **No PID assumptions** — mock `os.kill`
- **No cross-test state leakage** — reset in fixtures
- Run `pytest tests/ -x` before declaring tests pass

Full testing conventions: `.mozart/spec/conventions.yaml` (Testing Patterns section)

## Architecture Summary

```
CLI → (IPC: Unix socket + JSON-RPC 2.0) → Daemon/Conductor
  → JobService → Runner → Backend → (Claude CLI / API / Ollama)
  → Validation → State (SQLite/JSON) → Learning Store
```

The conductor is the single execution authority. CheckpointState is the single state authority. Backends are interchangeable. State saves are atomic. The EventBus never blocks publishers.

Full architecture with component table and invariants: `.mozart/spec/architecture.yaml`

## Conductor Mode

The daemon manages concurrent jobs, coordinates rate limits, routes events, and centralizes learning. Key components: DaemonManager, JobService, EventBus, IPC Server, Rate Coordinator, Backpressure, Scheduler (built, not yet wired), Learning Hub, Semantic Analyzer.

```bash
mozart --verbose start     # Development
mozart conductor-status    # Check health
tail -f ~/.mozart/mozart.log  # Logs
```

## Self-Healing & Validation

```bash
mozart validate my-job.yaml              # Pre-execution checks
mozart run my-job.yaml --self-healing     # Auto-diagnose + fix on retry exhaustion
mozart run my-job.yaml --self-healing --yes  # Auto-confirm suggested fixes
```

Validation checks: Jinja syntax (V001), workspace paths (V002), template files (V003), regex patterns (V007), undefined variables (V101), prelude/cadenza files (V108).

## Reference

**Docs:** `docs/daemon-guide.md`, `docs/score-writing-guide.md`, `docs/configuration-reference.md`, `docs/limitations.md`, `docs/cli-reference.md`

**Skills:**
- `/mozart:score-authoring` — Score/config writing guide (plugin skill)
- `/mozart:command` — Debugging and usage guide (plugin skill)
- `/home/emzi/.claude/skills/session-startup-protocol.md`
- `/home/emzi/.claude/skills/session-shutdown-protocol.md`
- `/home/emzi/.claude/skills/wolf-prevention-patterns.md`

**Design Docs (Four Disciplines):**
- `docs/plans/2026-03-01-four-disciplines-gap-analysis-design.md` — 106 gaps across 7 dimensions
- `docs/plans/2026-03-01-four-disciplines-phase1-foundation.md` — Spec corpus + token budget
- `docs/plans/2026-03-01-four-disciplines-phase2-prompt-craft.md` — 8-layer agent input structure
- Phases 3-9 as above

## Operational Gotchas

- **Conductor config:** `~/.mozart/conductor.yaml` (not `daemon.yaml`). `_load_config()` in `process.py` auto-discovers this.
- **Code changes:** Run `pip install -e ".[dev]"` after modifying Mozart source — the daemon loads from the installed package, not source tree.
- **Workspace files are live:** Agents modify workspace files during execution. NEVER overwrite them with seeds/templates.
- **Cadenza syntax:** `{{ workspace }}` (Jinja2) in cadenza file paths, `{workspace}` (Python format) in validation paths. Mixing them fails silently.
- **Pipe exit codes:** `cmd | tail -5` always exits 0. Use `bash -c '...; exit ${PIPESTATUS[0]}'` in `command_succeeds` validations.
- **Jinja2 dict methods:** `persona.values` resolves to the dict `.values()` method in Jinja2. Use `persona['values']` to access the key.
- **Preflight token thresholds:** Configurable via `preflight:` in `~/.mozart/conductor.yaml`. Default 150K is too low for Opus 1M context. Currently set to 800K warning / 200K error on this system.

## Flowspec Integration

Flowspec is a structural code analyzer at `/home/emzi/Projects/flowspec/target/release/flowspec`. The project has `.flowspec/config.yaml` configured.

- Always use **project root** as the path (auto-discovers `.flowspec/config.yaml` with entry points and excludes)
- `flowspec trace --symbol "module.py::Class" --direction both --depth 5 . -f summary -q` — trace data flow
- `flowspec analyze . -f summary -q` — structural overview (14K entities with config, 3K without)
- `flowspec diagnose . --severity critical -f summary -q` — find structural issues
- `flowspec diff old.yaml new.yaml -f summary -q` — compare manifests for structural regressions
- Start traces from **consumers (backward)**, not producers (forward) — config models show 0 flows forward
- Known limits: dynamic dispatch, Protocol implementations, default params not tracked
- Known bug: exit code always 0 regardless of findings (flowspec#31)

## Instrument System

- Profiles: `src/mozart/instruments/builtins/` (6 built-in), `~/.mozart/instruments/` (user), `.mozart/instruments/` (project)
- Loading: `InstrumentProfileLoader.load_directory(path)` → validates against `InstrumentProfile` schema
- Registry: `InstrumentRegistry` — register/get/list_all. `register_native_instruments()` bridges existing 4 backends.
- `SpecCorpusLoader.load(dir)` loads passages from ANY directory following the passage schema — not just `.mozart/spec/`
- `PluginCliBackend` executes any CLI instrument from a profile YAML

---

*Last restructured: 2026-03-29 — Operational gotchas, Flowspec, Instrument system added*
