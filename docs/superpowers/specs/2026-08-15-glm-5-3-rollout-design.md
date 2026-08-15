# GLM 5.3 Default and Marianne Rollout Design

**Date:** 2026-08-15
**Status:** Approved in conversation; written review pending
**Compatibility:** Intentional replacement of GLM 5.2 defaults and current routing picks

## Goal

Make the one-million-token GLM 5.3 release the default GLM route for Claude
Code and every active Marianne profile or built-in fleet preset that currently
selects GLM 5.2. Update current instrument-selection guidance with the
composer's operational calibration for reasoning and authorized defensive
security work.

## Evidence and Claim Boundaries

Live `opencode models zai-coding-plan --verbose` output on 2026-08-15 reports
`zai-coding-plan/glm-5.3` as active with a 1,000,000-token context limit, a
131,072-token output limit, reasoning support, and tool-call support. Claude
Code's Z.AI gateway names the extended-context route `glm-5.3[1m]` by the same
convention as the currently working `glm-5.2[1m]` route.

The following capability guidance is composer-supplied operational
calibration, not a claim copied from an older model's benchmarks:

- GLM 5.3 at `high` or `max` reasoning has Fable 5-level performance on tasks
  that are well bounded, clearly specified, and equipped with observable proof
  obligations. The improvement is attributed to post-training.
- This calibration does not imply equivalent performance on ambiguous,
  open-ended, underspecified, or unvalidated work.
- GLM 5.3 is particularly suitable for authorized defensive cybersecurity
  analysis and vulnerability discovery.

Historical documents and changelog entries keep their original model names.
Current routing authorities and live profiles move to GLM 5.3.

## Configuration and Profile Changes

### Claude Code

Update the global Claude Code settings so the selected `opus` alias resolves to
`glm-5.3[1m]`. Set the session effort default to `max`; retain the existing
one-million-token auto-compaction window and Z.AI Anthropic-compatible gateway.
Do not expose or rewrite authentication material.

### Marianne built-in profile

Replace the GLM 5.2 gateway model entry in
`src/marianne/instruments/builtins/claude-code.yaml` with `glm-5.3[1m]` and
declare:

- `context_window: 1000000`
- `max_output_tokens: 131072`
- zero marginal token costs under the configured subscription route
- the existing deep-reasoning concurrency tier

Claude Code executions use `--effort max` so GLM receives the reasoning mode
on which the capability calibration is based.

### Marianne private profiles

Update these organization profiles:

- `~/.marianne/instruments/claude-code.yaml`: make `glm-5.3[1m]` the default,
  add matching one-million-token metadata, and use `--effort max`.
- `~/.marianne/instruments/opencode.yaml`: make
  `zai-coding-plan/glm-5.3` the default, declare the live 1,000,000-token and
  131,072-output limits, and request OpenCode's `max` reasoning variant.
- `~/.marianne/instruments/flowspec-q5c2-sandbox.yaml`: replace its GLM 5.2
  route with `zai-coding-plan/glm-5.3`, use the same capacity metadata, and
  request the `max` variant without changing its sandbox wrapper or
  single-concurrency constraint.

The private Claude Code profile is a full replacement, not a merge overlay, so
it must retain the built-in command, output, MCP, interactive, and error
contracts while changing only the intended model and effort behavior.

Older GLM 4.x and GLM 5 Turbo specialist profiles remain available. They are
not silently renamed into GLM 5.3.

## Fleet and Instrument Guidance

The generic fleet preset currently routes substantial work through
`glm-5.2[1m]`. Replace those current picks with `glm-5.3[1m]`; leave explicit
GLM 5 Turbo fast-tier routes intact. Update compiler and root contract tests so
generated aliases and per-sheet instrument configuration prove the new model
name.

The authoritative instrument catalog gains a GLM 5.3 entry and repoints all
current GLM 5.2 use-case chains to it. The entry records:

- the live one-million-token context and 131,072-token output limits;
- reasoning and tool-call support;
- the composer-supplied high/max reasoning calibration and its bounded-task
  preconditions;
- defensive-security suitability;
- verified routes through Z.AI Coding Plan/OpenCode and the Claude Code gateway;
- the distinction between current live facts, composer calibration, and
  unverified benchmark or licensing claims.

The composing skill continues to treat the instrument catalog and live doctor
output as routing authority. It adds a concise rule to select GLM 5.3 with
high/max reasoning for well-bounded tasks when that calibration fits, and to
prefer a specialized score for vulnerability discovery rather than burying
security work inside a general fleet prompt.

## Conducting Boundary

The conducting skill intentionally does not preserve volatile model names.
That rule remains intact. Its provider-neutral runtime doctrine will say:

- A conductor whose own harness cannot perform an authorized task may
  commission it to a capable configured specialist; inability at the podium is
  not evidence that the task is invalid or should be abandoned.
- Delegation is not a guardrail bypass. The composer must authorize the target
  and scope, the selected instrument must permit the work, and the conductor
  retains evidence, review, and stop authority.
- Trust is operational, not blind: bound the commission, isolate side effects,
  require reproducible findings, and independently validate severity before
  remediation or disclosure.

This keeps durable conducting doctrine provider-neutral while allowing the
current instrument catalog to identify GLM 5.3 as the preferred specialist.

## Defensive-Security Score Pattern

Guidance for GLM 5.3 vulnerability-search scores requires the following staged
shape:

1. **Authorization and scope gate:** name the owned or explicitly authorized
   target, allowed techniques, excluded systems, data-handling rules, and stop
   conditions.
2. **Attack-surface inventory:** inspect the admitted source, dependencies,
   configuration, and reachable boundaries without modifying the target.
3. **Specialist search:** use GLM 5.3 at high/max reasoning on small, explicit
   vulnerability classes or components rather than one unbounded request.
4. **Reproduction:** require a minimal, non-destructive reproducer or static
   proof tied to exact candidate bytes.
5. **Independent triage:** separate existence, reachability, severity, and
   exploitability; reject unsupported findings.
6. **Remediation and regression:** commission a fix only after acceptance, add
   a failing regression test first, and prove the exact cause-level test turns
   green.
7. **Disclosure gate:** keep sensitive findings private and follow the target's
   coordinated-disclosure policy.

The pattern is for authorized defensive work. It must not weaken instrument
guardrails, broaden target authority, or turn a security finding into
permission for destructive exploitation.

## Verification

Verification will bind the candidate checkout explicitly and cover:

- a failing built-in-profile contract test before the profile change;
- failing generic-fleet compiler/root contract tests before preset changes;
- parsing every built-in and private profile with `InstrumentProfile`;
- `mzt instruments check` for `claude-code`, `opencode`, and
  `flowspec-q5c2-sandbox` after the private updates;
- live model discovery confirming `zai-coding-plan/glm-5.3` and its limits;
- a bounded Claude Code probe using `glm-5.3[1m] --effort max`;
- a bounded OpenCode probe using `zai-coding-plan/glm-5.3 --variant max`;
- targeted profile, compiler, composing, conducting, and catalog tests;
- the repository's full applicable test, lint, and type gates;
- a final census proving no current routing authority still selects GLM 5.2,
  while historical evidence remains unmodified.

The conductor currently has no active scores. A restart is unnecessary for
profile-file validation; any later daemon reload or restart must still follow
the repository's lifecycle constraints.

## Non-Goals

- Rewriting historical score snapshots, run evidence, completed plans, or
  changelog history.
- Replacing GLM 5 Turbo or GLM 4.x specialist routes without explicit evidence.
- Claiming public benchmark, licensing, or architecture facts not established
  by current primary evidence.
- Circumventing provider safeguards or authorizing offensive activity beyond
  an explicitly permitted defensive scope.
