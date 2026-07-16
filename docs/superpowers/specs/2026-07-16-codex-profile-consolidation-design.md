# Codex Instrument Profile Consolidation Design

## Purpose

Remove the standalone `gpt-5.6` instrument profile and represent GPT-5.6
correctly as a family of models played through the existing `codex-cli`
instrument.

This is an intentional clean break. Marianne has no compatibility audience for
the mistaken profile, so no alias, shim, deprecation period, or legacy example
is required.

## Current Defect

`src/marianne/instruments/builtins/gpt-5.6.yaml` duplicates the execution
contract already owned by `codex-cli.yaml`:

- executable and subcommand;
- authentication environment;
- model flag;
- approval and output flags;
- interactive TUI behavior;
- output parsing;
- rate-limit and error classification.

It therefore describes a model family as though it were a distinct instrument.
The same base GPT-5.6 model names also appear in `codex-cli.yaml`, producing two
registered routes to the same Codex executable and two places where the
execution contract can drift.

## End State

### Instrument and model boundary

- `codex-cli` is the only Codex instrument.
- `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` are models on that
  instrument.
- Their `[1m]` variants are models on that instrument.
- `gpt-5.6` is not a registered instrument name.

Scores select the family through the existing override:

```yaml
instrument: codex-cli
instrument_config:
  model: gpt-5.6-luna
```

### Default behavior

`codex-cli.default_model` remains unset. Marianne passes no `--model` flag
unless a score explicitly requests one, allowing the installed Codex client and
user configuration to choose its own default.

Luna is not made an implicit Marianne default. It remains the recommended
explicit choice for fast or inexpensive work when that is the composer's
intent.

### Consolidated metadata

`codex-cli.yaml` retains the complete GPT-5.6 metadata formerly split across
the two profiles:

- base and `[1m]` variants;
- context windows;
- maximum output tokens;
- input and output pricing;
- tier-specific concurrency;
- vision and thinking capability tags;
- existing Codex CLI execution, interactive, output, and error configuration.

The older Codex model entries remain unless current source proves they are
invalid. This change removes duplicate instrumentation; it does not narrow
Codex's model catalog.

## Test Contract

Regression tests must prove:

1. built-in discovery does not register `gpt-5.6`;
2. `gpt-5.6.yaml` does not exist;
3. `codex-cli.default_model is None`;
4. all six GPT-5.6 model variants are present under `codex-cli`;
5. their costs, context windows, output limits, and concurrency values match the
   approved metadata;
6. Codex advertises `vision` and `thinking`;
7. model override still reaches the existing Codex `--model` flag;
8. maintained docs describe GPT-5.6 as models available through `codex-cli`,
   never as a separate instrument.

The old tests that expect a `gpt-5.6` instrument are changed because the
contract is intentionally retired. Replacement assertions live in the Codex
profile tests.

## Documentation Scope

Update maintained user-facing surfaces:

- built-in instrument inventory;
- limitations and CLI examples;
- instrument guide/catalog entries;
- any maintained score or example using `instrument: gpt-5.6`.

Historical research, release plans, and evaluation records remain historical
unless they falsely present themselves as current reference material.

## Verification

Run:

1. focused profile, discovery, token, and CLI instrument tests;
2. static searches for standalone `gpt-5.6` instrument references;
3. YAML parsing and `load_all_profiles()` discovery;
4. `mzt instruments list` and `mzt instruments check codex-cli`;
5. the full repository suite;
6. plugin catalog parsing and plugin tests after documentation alignment.

The completion gate requires `gpt-5.6` to be absent from registered instrument
names while explicit GPT-5.6 model selection remains valid through
`codex-cli`.
