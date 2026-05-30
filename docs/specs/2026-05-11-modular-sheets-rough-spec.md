# Modular Sheets — Rough Spec

**Status: ROUGH** · Needs an actual design session before implementation. Filed as a placeholder per composer direction during emzihypno.com build (round 14). PM score's "don't actively add spec" rule applies — this captures the idea so it doesn't get lost; full design work is deferred.

## The Idea (verbatim composer framing)

> "I was thinking to have it more like you would break a python app into modules, or have multiple .c files that target specific things. That kinda deal. This way you could edit specific prompts at specific sheets, in a smaller file. The file itself could also include details on sheet dependencies, instruments, and the like, which would all get globbed together, and validation would point out duplicates or clashes. Self contained prompts, essentially, fully composable in the way you would write or compose actual code."

## The Problem It Solves

Scores grow large. The emzihypno.com build score is 2200+ lines after 14 rounds of iteration. A single YAML file holds:
- Score-level config (instrument, fallbacks, parallel, retry, notifications, variables)
- All movement definitions
- All cadenza assignments
- All dependency declarations
- A Jinja template with per-stage `{% elif stage == N %}` blocks for prompts (the bulk)
- All validations

Editing a single stage's prompt means scrolling through hundreds of other stages' definitions. Authoring in parallel is impossible — every change is to the same file. Git diffs are noisy because unrelated stages live adjacent to each other in the file. Cognitive context for "what does this one sheet do" requires holding the whole score's structure in mind.

## The Sketch (NOT a design — directional only)

A score becomes a *package* on disk:

```
scores-internal/build-emzihypno-site/
  score.yaml                # top-level config: instrument, fallbacks, variables, parallel, retry, notifications, workspace_lifecycle, prompt.variables
  sheets/
    S01-bootstrap.yaml      # name, instrument, dependencies, cadenza, prompt, validations — all for this one sheet
    S02-tokens-fonts.yaml
    S03-supabase-schema.yaml
    ...
    S28-testimonials.yaml
  shared/                   # optional — for cross-sheet prompt fragments referenced via {% include %}
    vibe-style.j2
    typecheck-step.j2
```

Each sheet file is self-contained — name, instrument override, deps, cadenza file list, prompt text, validations. The Marianne loader globs `sheets/*.yaml`, validates each, assembles the same in-memory `JobConfig` the current monolithic YAML produces.

## Open Questions (for the design session)

1. **Sheet ordering**: position number comes from filename (S01, S02 ...) or from explicit field in the file? Filename is convention-driven; explicit field is more flexible but requires uniqueness validation.
2. **Score-level vs sheet-level overrides**: which keys live where? Variables — score level. Instrument default — score level, with per-sheet override (already supported). What about `parallel.max_concurrent`?
3. **Dependency expression**: each sheet declares `depends_on: [S01, S07]` (other sheet IDs) or numeric positions? Names are more refactor-safe.
4. **Validation of the package**: at load time, validator checks for: duplicate sheet IDs, dangling dependencies, dependency cycles, marker-name collisions, conflicting position numbers, conflicting movement names. These checks are stricter than the current single-file validator because the failure modes are richer.
5. **Backwards compatibility**: existing monolithic scores continue to work. The new format is opt-in. Some scores stay simple enough to live in one file forever.
6. **Tooling**: `mzt score init <name>` scaffolds a package; `mzt score lint <path>` validates structure; possibly `mzt score show <sheet-id>` prints just that sheet for review.
7. **Shared fragments**: are they Jinja `{% include %}` (template-level) or YAML anchors (data-level) or both? Each has tradeoffs.
8. **Migration path**: a tool that splits a monolithic score into its modular equivalent? Useful for the emzihypno score itself eventually.

## Adjacent Already-Existing Feature

Marianne already supports `prompt.variables` for repeated string sections. The DRY win exists today within a single file (define once, reference many). Composer noted during round 14 that the building-agent failed to use this aggressively in the emzihypno score — that's a usage gap, not a Marianne gap. Modular sheets are about FILE-LEVEL decomposition; the variable system is about WITHIN-FILE deduplication. Both matter; this spec is about the former.

## Why This Matters Beyond Convenience

The composer's framing: Marianne is a programming language for cognition. Sheets are the functions. The score is the module. Modular files reify that mental model in the source. A "function" in any real programming language lives in its own scope, can be tested in isolation, can be reviewed alone, can be edited without scrolling past unrelated code. Right now sheets share none of those properties — they exist as named blocks inside a monolithic template. Modular sheets bring real source-level modularity to Marianne scores.

This also unlocks agent-first authoring at scale: an agent could be told to write or modify ONE sheet file without ever seeing the rest of the score. Today, an agent editing a score sees the whole thing, which puts cognitive load on the agent that scales with score size rather than with the work the agent is doing.

## What This Spec Is NOT

- Not a full design. Open questions above haven't been answered.
- Not an implementation plan. Loader changes, validator changes, score-authoring skill changes, and conductor changes are all undefined.
- Not a scoping commitment. Composer's PM score may keep this deferred for a long time.

## Next Step

When a design session is appropriate (after emzihypno.com ships and during a quiet Marianne-development window), this rough spec is the starting prompt. Probably a brainstorming-skill session followed by a dedicated design doc. Probably worth lab-reviewing the design before implementation.
