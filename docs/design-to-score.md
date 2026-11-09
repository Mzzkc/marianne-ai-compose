# Design to score: the compilation workflow

You have a design document and want a running orchestra. This is the path
for someone using Marianne on its own, without the conducting skill or a
persistent conductor agent. It is a compilation pipeline: each stage turns
one artifact into the next, and each has a shipped score. The first user to
try it (2026-11) reached for exactly this shape, so it is written down.

```
concept / design doc
   │  spec-forge.yaml            (intake → lenses → breakdown → part specs → review → brief)
   ▼
specs/ tree  ── you approve it ──┐
   │                             │ mzt resolve spec-forge <sheet> accept
   │  design-to-orchestra.yaml   (repertoire → casting → one score per part → gate → review → RUN.md)
   ▼
validated scores + fleet
   │  RUN.md
   ▼
running orchestra  →  polish: quality-continuous.yaml, issue-triage → issue-solver
```

A short design for a small change does not need the whole route:
`score-composer.yaml` turns one design doc into one implementation score in
four sheets. `mzt compile` is a different thing: it expands *agent
definitions* (people with voices, techniques and instruments) into persistent
agent scores, and takes no design document.

## Before anything: an instrument that can write files

Every stage below writes files into a workspace and reads them back through
validations. The instrument must therefore be a CLI agent with a filesystem
(claude-code, codex-cli, opencode, goose, crush, antigravity). An `http`
profile (Ollama, PolyStrata, any bare chat endpoint) can only answer, and its
reply lands in cross-sheet context, not in files. On a local model, point
OpenCode at your server and bind that; the recipe is in
[local-server-quickstart.md](local-server-quickstart.md).

The shipped scores bind capability classes (`strong`, `workhorse`,
`reviewer`, `cli`). Generate the class map once from what the machine has:

```bash
mzt instruments classes write        # ~/.marianne/classes.yaml
mzt instruments classes check        # each class resolves to something runnable
```

A class that resolves to nothing fails the score at its first sheet with
`E505`; fix the map first.

## Stage 1: concept to specs (`spec-forge`)

Input: a concept file (the design doc, as it is) and an optional flat
directory of context (stakeholders, constraints, existing work). Tempo sets
how many review passes the repair loop gets: `rapid` 2, `standard` 3,
`thorough` 5.

```bash
mzt run examples/engineering/spec-forge.yaml \
  --var concept_file=/abs/design.md \
  --var context_dir=/abs/context \
  --var tempo=standard
mzt status spec-forge --watch
```

Output in the workspace: `01-intake.md`, five lens reports, `specs/00-system.md`,
`specs/parts.yaml`, `specs/decisions.md`, one `specs/NN-<part>/{spec,integration}.md`
per part, the review verdict, `review/deferred.md`, and `07-stakeholder-brief.md`.
The score stops on its last sheet (a FERMATA) so a person reads the brief
before anything is built. If the repair loop spends every pass with the triage
still at REWORK, the brief is still written: it opens with `## Not yet
approved` and the blockers the last pass left, and its approval line says the
design is NOT approved, so the decision (edit and retry, or accept the
blockers as the first build's known debt) stays with a person instead of the
job failing with the specs unread. The approval is yours, not the score's:

```bash
mzt resolve spec-forge <sheet> accept     # or reject, and edit the concept
```

Existing project? Run `project-roadmap.yaml` first and hand its roadmap and
atlas to spec-forge as `context_dir`.

## Stage 2: specs to an orchestra (`design-to-orchestra`)

Input: the approved `specs/` tree and the project the work happens in.

```bash
mzt run examples/engineering/design-to-orchestra.yaml \
  --var specs_dir=/abs/workspaces/spec-forge/specs \
  --var project_root=/abs/project \
  --var tempo=standard
```

It surveys the shipped repertoire, casts each part (who builds, who tests,
who reviews, for how long), writes one build score per part plus an
integration score and a `type: fleet`, and gates every generated score:
`mzt validate --strict` with no errors (so a generated score spells its
workspace and injected paths with `~` or template variables, never an
expanded `/home/...` path, which V004 refuses), the composing release
checker with a lock, a review on two model families (judged by the model each voice actually ran on, read from its context receipt, not by harness name), and a verdict in which a confirmed single-voice HIGH that breaks a hard requirement is a blocker (GH #513). The gate also refuses a generated score whose RED pin set covers test modules but not the conftests and fixtures the assertions read (an oracle that can be walked around, GH #518), and one whose repair loop cannot iterate: a `cli` verifier inside the loop needs an `on_fail` goto back to the builder, or the engine ends the loop `range_failed` on its first red run. It submits nothing unless
`--var submit=true`; otherwise `RUN.md` in the workspace names the commands
and re-verifies each lock at start, so a score edited after review refuses
to run.

## Stage 3: run and watch

```bash
cat workspaces/design-to-orchestra/RUN.md
mzt run <fleet>.yaml
mzt status <fleet> --watch
```

What to do when a sheet fails, in order: `mzt diagnose <job>` names the cause;
a venue failure (instrument missing, server down, path wrong, quota) is fixed
outside the score and the job resumed with `mzt resume`; a product failure
goes back to that part's repair loop, which the generated score already has.
Two failed retries on the same outcome mean the premise is wrong, not the
retry count.

## Stage 4: polish

The integration score leaves a `polish-backlog.md` and each review leaves a
`deferred.md`. Feed them to `quality-continuous.yaml`, and the project's issue
tracker to `issue-triage.yaml` → `issue-solver.yaml`.

## The short route: one design doc, one score

```bash
mzt run examples/engineering/score-composer.yaml \
  --var doc=/abs/design.md \
  --var output_score=my-feature \
  --var project_context="Python 3.12 CLI, src/ layout, pytest in tests/"
# read workspaces/score-composer-workspace/my-feature.yaml, then
mzt run workspaces/score-composer-workspace/my-feature.yaml
```

Four sheets: analysis, task decomposition, score composition, validation.
`target_instrument` (default `workhorse`) is what the generated score binds.

## What each stage refuses, and why

| Stage | Refuses when | Because |
|---|---|---|
| spec-forge | blockers remain after the last review pass | an unapproved spec must not look approved |
| design-to-orchestra | any generated score fails `mzt validate`, the release checker, or review | a score that cannot run is not an orchestra |
| submit (any score) | a class resolves to nothing, or a venue profile beside the score was never loaded by the conductor | failing at submit beats failing at sheet 1 |
| sheet validations | the artifact the prompt promised is absent or wrong | the report is the musician's account; the file is the evidence |
