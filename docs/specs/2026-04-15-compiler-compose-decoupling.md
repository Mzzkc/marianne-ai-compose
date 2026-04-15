# Agent Compiler Extraction — Fully Separate from Mozart

**Date:** 2026-04-15
**Status:** Instructions for implementation
**Priority:** High — the compiler is currently inside Mozart (`src/marianne/compose/`) where it doesn't belong

---

## The Problem

The composition compiler (agent identity → Mozart scores) was built inside `src/marianne/compose/` and wired to `mzt compose`. This is wrong on two levels:

1. **The compiler is not part of Mozart.** Mozart is an orchestrator. It runs scores, manages the conductor, dispatches sheets, handles circuit breakers. The compiler is a separate tool that happens to produce Mozart-compatible YAML as output. Putting it inside `src/marianne/` makes Mozart a smorgasbord instead of an orchestrator.

2. **The compiler is not the compose system.** The compose system (designed in `docs/plans/compose-system/`) is a generative AI pipeline that turns user intent into compositions. The compiler is a deterministic expansion engine that turns pre-defined agent configs into scores. Different users, different concerns, different lifecycles.

The compiler must be extracted into its own repository/package. Mozart's only relationship to it is: Mozart runs what the compiler produces.

## What Each System Is

### Agent Compiler (separate tool)

**Input:** Semantic agent config YAML — agents defined as people (voice, focus, meditation, techniques, instruments)

**Output:** Mozart score YAML (one per agent), identity directories (`~/.mzt/agents/`), fleet configs, agent card sidecars

**Nature:** Deterministic. Same input → same output. No AI reasoning at compile time. Pure expansion/transformation.

**User:** An engineer or architect who has already decided what agents they want, with what identities, techniques, and instruments. They've done the thinking. The compiler does the mechanical expansion.

**Modules (implemented, currently at `src/marianne/compose/`):**
- `pipeline.py` — Top-level orchestrator (343 lines)
- `identity.py` — L1-L4 identity stack seeder (325 lines)
- `sheets.py` — 12-sheet agent lifecycle composer (259 lines)
- `techniques.py` — Cadenza injection, MCP config, A2A cards (217 lines)
- `instruments.py` — Per-sheet assignment with fallback chains (253 lines)
- `validations.py` — Per-phase conditional validations (226 lines)
- `patterns.py` — Rosetta corpus → sheet sequences (718 lines)
- `fleet.py` — Concert-of-concerts from roster (131 lines)
- `technique_modules/` — Standalone technique documents (5 files)
- **Tests:** 126 tests across 8 files (1,973 lines)

**Current (wrong) location:** `src/marianne/compose/`, CLI at `mzt compose`

### Mozart (the orchestrator)

**What it is:** A conductor-driven orchestration system. You give it a score YAML, it decomposes it into sheets, dispatches them through AI backends, validates outputs, handles retries/fallbacks/circuit breakers, and feeds knowledge forward.

**What it is NOT:** A compiler, a code generator, an identity system, or a smorgasbord of loosely-related tools. It runs things. That's it.

**Relationship to the compiler:** Mozart runs what the compiler produces. The compiler's output format (score YAML) must conform to Mozart's `JobConfig` schema. That's the interface boundary. Mozart does not need to know how scores were produced — by hand, by the compiler, by the compose system, or by any other tool.

### Marianne Compose System (future, separate from both)

**What it is:** A generative AI pipeline that turns user intent into compositions. Designed in `docs/plans/compose-system/` (8 specs, not yet implemented).

**Pipeline:** Intent → Instrument Survey → Init → Interview/Config → TDF Spec Generation → Score Composition → Concert Assembly → Execution → Remediation

**Relationship to the compiler:** The compose system MAY use the compiler when producing agent-orchestration scores. But it can also produce scores directly (for non-agent tasks). The compiler is a potential dependency, not a subsystem.

**Relationship to Mozart:** The compose system produces scores that Mozart runs. Same boundary as the compiler.

## What Must Happen

### 1. Merge the compiler into backyard-capitalism-9000

The compiler does NOT get a new repo. It extends and replaces the work already done in `Mzzkc/backyard-capitalism-9000` (CIAB — Company in a Box). CIAB already has:

- A 7-stage compiler pipeline (`src/ciab/compiler/compiler.py`, 886 lines)
- A roster system with agent catalog, compatibility, schema (`src/ciab/roster/`)
- An org builder with company definitions (`src/ciab/org/`)
- A governance layer (`src/ciab/governance/`)
- A persistence layer (`src/ciab/persistence/`)
- Memory selection for agent compilation (`src/ciab/compiler/memory_selection.py`)
- Pattern-based stage generation (`src/ciab/compiler/patterns/`)
- A dashboard (`src/ciab/dashboard/`)
- 10K lines of source, 52K lines of tests
- 17 spec documents covering architecture, roster, memory, compiler, persistence, governance, departments
- A curated agent roster (`agents/` with categories: executives, managers, engineers, architects, qa, reviewers, specialists, antagonists)
- Its own CLI (`python -m ciab company compile`)

The new compiler modules built in Score 3 are improvements that should flow INTO CIAB:

| New Module (Score 3) | CIAB Equivalent | Action |
|----------------------|-----------------|--------|
| `identity.py` — L1-L4 stack | No equivalent (CIAB uses flat persona prompts) | **Add** — upgrade CIAB's identity model |
| `sheets.py` — 12-sheet cycle | `compiler/stages.py` + `compiler/patterns/` | **Replace/upgrade** — CIAB has 7 stages, new has 12 with fan-out |
| `techniques.py` — cadenza injection | `compiler/compiler.py` build_cadenzas stage | **Merge** — new module is more modular |
| `instruments.py` — per-sheet resolver | `compiler/compiler.py` (inline) | **Extract** — new module separates concerns better |
| `validations.py` — per-phase rules | `compiler/compiler.py` build_validations stage | **Merge** — new module adds phase-aware validation |
| `patterns.py` — Rosetta expansion | `compiler/patterns/` directory | **Merge** — combine Rosetta corpus with CIAB's existing patterns |
| `fleet.py` — concert-of-concerts | No equivalent (CIAB produces single scores) | **Add** — fleet management is new capability |
| `pipeline.py` — top-level orchestrator | `compiler/compiler.py` CompanyCompiler class | **Merge** — reconcile the two pipelines |

**Target repo:** `Mzzkc/backyard-capitalism-9000`

**CLI stays:** `python -m ciab` (or whatever CIAB's CLI becomes)

**NOT a Mozart subcommand.** CIAB is a product built ON Mozart, not part of Mozart.

### 2. What moves out of Mozart

**Source files to extract:**
- `src/marianne/compose/__init__.py`
- `src/marianne/compose/pipeline.py`
- `src/marianne/compose/identity.py`
- `src/marianne/compose/sheets.py`
- `src/marianne/compose/techniques.py`
- `src/marianne/compose/instruments.py`
- `src/marianne/compose/validations.py`
- `src/marianne/compose/patterns.py`
- `src/marianne/compose/fleet.py`
- `src/marianne/compose/technique_modules/` (5 technique documents)

**Tests to extract:**
- `tests/test_compose_identity.py`
- `tests/test_compose_sheets.py`
- `tests/test_compose_techniques.py`
- `tests/test_compose_instruments.py`
- `tests/test_compose_validations.py`
- `tests/test_compose_patterns.py`
- `tests/test_compose_fleet.py`
- `tests/test_compose_cli.py`
- `tests/test_cli_compose.py`

**CLI to extract:**
- `src/marianne/cli/commands/compose.py` (the current implementation that calls the compiler)

**Design spec to move:**
- `docs/specs/2026-04-13-composition-compiler-design.md` → lives in the compiler repo

### 3. What stays in Mozart

**Everything else.** The conductor, baton, daemon, backends, instruments, CLI, state, learning, validation, dashboard, TUI — all of that is Mozart.

The `mzt compose` command should be either:
- Removed entirely (compose system is unimplemented, no point having a stub)
- Reserved for the compose system with a clear message: "The compose system is not yet implemented. Use `agent-compile` for score generation from agent configs."

### 4. The interface boundary

The ONLY contract between the compiler and Mozart is the score YAML format:

```
Compiler output → JobConfig-compatible YAML → Mozart input
```

The compiler must produce YAML that passes `mzt validate`. That's the entire interface. No shared code, no shared imports, no shared state.

The compiler may import Mozart's `JobConfig` model for validation (as a dev dependency), or it may validate against the schema independently. Either way, no runtime coupling.

### 5. What CIAB already has that the new compiler doesn't

The new compiler modules are cleaner in some ways (modular, well-tested) but CIAB has capabilities the new code lacks entirely:

- **Roster system** — Curated agent catalog with personality traits, skill profiles, compatibility matrices, capability tiers. The new compiler takes agents as config input; CIAB lets you browse and draft.
- **Org builder** — Company definitions with culture, departments, hierarchies. The new compiler has no organizational concept.
- **Governance** — Decision-making structures. The new compiler has no governance.
- **Persistence** — State management across cycles. The new compiler generates stateless scores.
- **Dashboard** — Visual management. The new compiler is CLI-only.
- **Memory selection** — Intelligent memory pruning for agent compilation. The new compiler's identity seeder is simpler.
- **The seat abstraction** — Entity-agnostic roles (AI fills seats today, humans tomorrow). The new compiler hardcodes AI agents.
- **The product vision** — "Backyard Baseball for building software companies." The new compiler is a tool; CIAB is a product.

**Dependencies on Mozart:** NONE. Not even dev dependencies. No `from marianne import` anywhere. CIAB and Mozart have separate licenses. The ONLY interface is the score YAML file format. CIAB must validate its output against its own copy of the schema or against the published spec — never by importing Mozart code.

**Dependencies FROM Mozart:** NONE. No `from ciab import` anywhere. Mozart reads YAML files. It doesn't know or care what produced them.

**Licensing:** Separate licenses, separate repos, no cross-importing. The score YAML format is the public interface. If the schema needs to be shared, publish it as a standalone spec document or JSON Schema — not as importable code.

### 6. Clean up Mozart after extraction

- Delete `src/marianne/compose/` entirely
- Delete `tests/test_compose_*.py`
- Remove `compose` from CLI command registration
- Remove any `from marianne.compose` imports (there shouldn't be any from core Mozart code, but verify)
- Update `CLAUDE.md` to remove compose references from Key Files
- Full test suite must pass after removal

## Implementation Plan

### Phase 1: Discovery — Map the Infestation

Before ripping anything out, understand how deep the compiler has rooted into Mozart.

**1a. Import graph analysis**
- Trace every `from marianne.compose` and `import marianne.compose` across the entire codebase
- Map which Mozart modules depend on the compiler (CLI, tests, config, daemon?)
- Map which compiler modules depend on Mozart internals (if any — they shouldn't, but verify)
- Identify any shared types, constants, or utilities that both systems use

**1b. CLI entanglement**
- How is `mzt compose` registered? What does removing it break?
- Are there other CLI commands that reference compose functionality?
- Does the dashboard, TUI, or status display reference compose?

**1c. Test entanglement**
- Which test files test the compiler vs test Mozart features that happen to use compose?
- Are there integration tests that cross the boundary?
- What's the test count impact of extraction?

**1d. Config/schema entanglement**
- Does `JobConfig` or any Mozart config model reference compiler types?
- Does the compiler use Mozart's Pydantic models directly?
- Are there score YAML fields that only exist because of the compiler?

**1e. Artifact inventory**
- Score 3 outputs: what was committed to the Mozart repo that belongs to the compiler?
- Score 5 outputs: what migration work was done that references compiler internals?
- Workspace artifacts that straddle the boundary

**Deliverable:** A dependency map document listing every cross-reference, categorized by severity (hard dependency, soft reference, test-only, documentation-only).

### Phase 2: Standalone Extraction — Make It Independent

Extract the compiler into a standalone package that has ZERO Mozart imports. TDD throughout.

**2a. Create the standalone package**
- New directory (temporary, in-repo or adjacent) with its own `pyproject.toml`
- Copy compiler modules out of `src/marianne/compose/` into the standalone package
- Copy tests into the standalone test directory
- Replace ALL `from marianne.*` imports with local equivalents or remove them

**2b. Sever Mozart dependencies**
- If the compiler imports Mozart types (e.g., `JobConfig` for validation): replace with standalone schema validation (JSON Schema or own Pydantic models)
- If the compiler uses Mozart utilities (logging, constants): copy or rewrite locally
- If the compiler references Mozart paths or conventions: make configurable

**2c. Own CLI**
- Standalone entry point (not `mzt` subcommand)
- Same functionality: compile, dry-run, seed-only, fleet
- Wraps Mozart commands for execution: `mzt run`, `mzt validate`, `mzt status` — called as subprocesses, never imported

**2d. Validate standalone operation**
- All 126 compiler tests pass in the standalone package with zero Mozart imports
- `mzt validate` accepts the standalone compiler's YAML output
- `mzt run --dry-run` works on compiled scores
- mypy clean, ruff clean on the standalone package

### Phase 3: Integrate into bc9k — Replace and Supersede

The standalone compiler replaces CIAB's existing compiler. The organizational structure (seats, departments, hierarchies) gives way to agents with their own lifecycles.

**3a. Understand bc9k's current compiler**
- Read `src/ciab/compiler/compiler.py` (886 lines, 7-stage pipeline)
- Read `src/ciab/compiler/patterns/`, `memory_selection.py`, `stages.py`
- Read `src/ciab/roster/` (catalog, compatibility, loader, schema)
- Read all 17 specs in `specs/`
- Map what bc9k has that the new compiler doesn't and vice versa

**3b. Replace the compiler pipeline**
- New compiler modules (identity, sheets, techniques, instruments, validations, patterns, fleet) supersede CIAB's 7-stage pipeline
- CIAB's roster system feeds INTO the new compiler's agent config format
- The org schema (company.yaml) becomes the input that generates agent configs
- Drop the fixed organizational hierarchy in favor of agents with self-chaining lifecycles

**3c. Wire CIAB to wrap Mozart commands**
- `ciab run` → calls `mzt run` on compiled scores
- `ciab status` → calls `mzt status`
- `ciab validate` → calls `mzt validate`
- Provide bc9k with a reference to Mozart's usage skill and README for integration
- CIAB is a product layer ON Mozart, invoking it as a subprocess — never importing from it

**3d. Preserve what CIAB has that's valuable**
- Roster catalog (agent browsing, drafting)
- Compatibility matrices
- Dashboard (adapt to use Mozart's status data via CLI/API)
- Persistence layer (agent memory across cycles)
- The product vision (Backyard Baseball for companies)

**3e. Test extensively**
- CIAB's existing tests must pass or be updated for the new compiler
- New integration tests: company.yaml → compile → mzt validate → mzt run --dry-run
- TDD for any new glue code between roster and compiler

### Phase 4: Mozart Tearout — Clean the Codebase (parallel with Phase 3)

Remove all compiler code from Mozart. Fix everything that breaks.

**4a. Remove `src/marianne/compose/`**
- Delete the entire package
- Delete `src/marianne/cli/commands/compose.py` (or stub it for the compose system)
- Remove CLI registration for `compose` command

**4b. Remove compiler tests**
- Delete `tests/test_compose_*.py` and `tests/test_cli_compose.py`
- Verify remaining test count and that nothing else broke

**4c. Fix broken imports and references**
- Search entire codebase for `marianne.compose` references
- Update CLAUDE.md Key Files table
- Update any documentation that references `src/marianne/compose/`

**4d. Fix Score 5 (Migration)**
- Score 5 Sheet 4 failed because `mzt compose` couldn't generate agent-fleet scores
- After tearout, Score 5 needs to invoke the standalone compiler (or bc9k) instead of `mzt compose`
- Determine: is Score 5 still relevant post-extraction? Does the migration path change?
- If Score 5's remaining work (fleet generation, canary launch, full launch) is now bc9k's job, update the score or retire it

**4e. Full regression**
- `python -m pytest tests/ -n auto --timeout=120` — all tests pass
- `mypy src/marianne/` — clean
- `ruff check src/marianne/` — clean
- No remaining references to `marianne.compose` anywhere
- `mzt validate` still works on existing scores
- Running scores (if any) are unaffected

### Phase 5: Validation — Prove It Works End-to-End

**5a. The full pipeline works**
- Write an agent config YAML (in bc9k format)
- Compile it with bc9k's compiler → produces Mozart score YAML
- `mzt validate score.yaml` → passes
- `mzt run score.yaml --dry-run` → correct structure
- `mzt run score.yaml` → agent executes a cycle

**5b. No cross-contamination**
- `grep -r "from marianne" /path/to/bc9k/` → zero results
- `grep -r "from ciab" /path/to/mozart/` → zero results
- Both repos build and test independently

**5c. Documentation updated**
- Mozart README/CLAUDE.md has no compiler references
- bc9k README references Mozart's usage skill and README for execution
- The score YAML format is documented as the public interface

## Reference Documents

### Compose System Design (the pipeline — Steps 0-8)
- `docs/plans/compose-system/00-system-overview.md` — Full pipeline, grounding layers, design principles
- `docs/plans/compose-system/01-init-redesign.md` — Init interview → libretto
- `docs/plans/compose-system/02-tdf-spec-engine.md` — 5-sheet TDF spec generation engine
- `docs/plans/compose-system/03-interview-system.md` — Interactive interview, concurrent spec kickoff
- `docs/plans/compose-system/04-score-composition.md` — 6-step generative score composition
- `docs/plans/compose-system/05-concert-execution.md` — Concert orchestration, auto-reviews
- `docs/plans/compose-system/06-manifest-remediation.md` — Manifest and remediation workflow
- `docs/plans/compose-system/07-in-score-spec-gen.md` — Runtime spec generation
- `docs/plans/compose-system/REVIEW.md` — 39 design review issues
- `docs/plans/compose-system/FEEDBACK.md` — Composer feedback
- `docs/plans/compose-system/RESEARCH.md` — Codebase grounding research

### Compiler Design (the expansion engine)
- `docs/specs/2026-04-13-composition-compiler-design.md` — Full architecture (15 sections)

### What Was Built (concert outputs)
- Score 1 (Discovery): 9 research reports + synthesis in `workspaces/composition-compiler-build/`
- Score 2 (Infrastructure): Config models, OpenRouter backend, sandbox, baton events in `src/marianne/`
- Score 3 (Compiler): All 7 modules + 126 tests in `src/marianne/compose/` (to be moved)
- Score 4 (Integration): E2E validation — compile → validate → dry-run → canary agents
- Score 5 (Migration): Sheets 1-3 done (manifest, memories, scores), Sheet 4 failed on fleet generation

### Existing Compose Skill (v1)
- `plugins/marianne/skills/composing/SKILL.md` — Single-agent workflow: Understand → Question → Design Gate → Compose → Review → Offer

### Session Context
- `docs/handoffs/composition-compiler--SESSION-HANDOFF-2026-04-13.md` — Build session handoff
- `memory-bank/legion/legion.md` — Legion's memory of the build

## Verification After Decoupling

1. `mzt compile --help` works and shows compiler options
2. `mzt compose --help` either shows compose system stub or v1 skill info
3. `python -m pytest tests/test_compiler_*.py -n auto` — all 126 tests pass
4. `mypy src/marianne/compiler/` — clean
5. `ruff check src/marianne/compiler/` — clean
6. No remaining imports from `marianne.compose` anywhere in the codebase
7. `src/marianne/compose/` is empty or contains only a stub
8. Full test suite passes with zero regressions
